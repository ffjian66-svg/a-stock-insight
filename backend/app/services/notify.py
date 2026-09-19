"""微信推送（企业微信群机器人 webhook）：收盘日报 / 止损触发 / 同步失败。

## 为什么是"纯出网 webhook"

线上只有裸 IP + 8000 端口、没有公网 HTTPS 域名，微信**无法回调**进来，所以公众号模板消息
/ 订阅消息这类需要主动调用的方案直接出局；群机器人 webhook 是唯一"只出网、零注册、
不占 80/443"的路子（也就不会碰到同机其他服务已占用的 nginx）。

## 三条纪律（改这个文件时最容易踩的）

1. **结论必须是页面那一份。** 渲染器只接收已经算好的对象（`Board` / `decision.PositionAdvice`），
   **本模块永远不碰数据库、不跑回测、不读行情**。`render_daily` 里任何一个"从 verdict 推
   action"的简化都会让负期望重新看起来像买入——那是本项目唯一不可接受的失败。
   所以这里的测试能逐字节断言文案。
2. **截断必须被明说。** 企业微信 markdown 上限 4096 **字节**（中文 3 字节/字），而候选有
   200 多行。装不下的部分要在消息里写出「列了 N / 共 M」，不许静默丢。
3. **`errcode` 必须校验。** 企业微信失败时返回 **HTTP 200** + `{"errcode":93000}`
   （webhook 失效 / 机器人被移出群）。只看 HTTP 状态码会把"密钥已失效"永远记成「已送达」，
   于是推送坏了几个月都没人知道——这正是本项目所有 docstring 都在防的那种静默谎言。

## 一次推送的完整生命周期

`push()` 先插账本再发送：`dedup_key` 非空 + UNIQUE 就是幂等机制本身，重启/重跑都不会重复发。
**不重试、不排队**——市场消息迟到比不到更糟（一条昨晚的候选今早到达是误导）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import NotifyEvent
from app.services.board import Board
from app.services.quant import decision
from app.services.quant.decision import _VERDICT_LABELS as VERDICT_LABELS
from app.services.sync import classify_error

logger = logging.getLogger(__name__)

# 推送**成功**时企业微信回的 errcode。之所以要显式跟它比而不是"HTTP 200 就算成功"，
# 见模块 docstring 第 3 条。
_OK_CODE = 0

# 出网超时。取 5s 而不是 providers/llm.py 那种几十秒：本函数跑在调度线程里，
# 卡住的是整条傍晚任务链。
_TIMEOUT = 5.0

# 同步**失败**告警白名单。**刻意排除 `quotes` 与 `news`**：它们每 30 秒 / 每 5 分钟跑一次，
# provider 一抖动就是告警和恢复每半分钟一轮，直接撞上企业微信 20 条/分钟的限流
# （45009），而被挤掉的那条恰好是"已恢复"——最该看到的一条。
SYNC_ALERT_JOBS = ("market", "fundamentals", "scores", "calendar", "stocks", "pool")

# 「任务被禁用」告警的名单，**恰好是上面被排除的那两个**。这不是巧合，是互补：
#
# - 高频任务（`quotes`/`news`）失败时不单独告警（会刷屏）。但它们一旦因账号权限被禁用，
#   `scheduler._execute` 在 30 秒后的下一次 tick 就会短路掉——于是这个分支能立刻抓住
#   "它已经不在跑了"，而按天去重的键保证 12 小时里只发一条。没有这条告警，行情刷新
#   悄悄停掉而页面上什么都看不出来。
# - 白名单里那些一天一次的任务**不该挂这条**：`sync._DISABLE_WINDOW` 是 12 小时，
#   在它们的下一次调度之前就过期了，`job_disabled` 在它们的调度时刻永远返回 False，
#   给它们挂就是死代码。它们的失败信号由 `syncfail`（每失败一次发一条）覆盖。
SYNC_DISABLED_JOBS = ("quotes", "news")

# 任务名的中文说法。本仓此前没有这张表（前端也没印过 job_type 的中文名）。
# **覆盖全部已排期的任务**（= 上面两个集合的并集），不是只覆盖白名单：被禁用告警讲的是
# `quotes`/`news`，若这张表里没有它们，那条消息会印出 `**quotes**（\`quotes\`）`。
JOB_LABELS = {
    "market": "日线行情",
    "fundamentals": "财务快照",
    "scores": "综合评分",
    "calendar": "交易日历",
    "stocks": "股票列表",
    "pool": "全市场池化",
    "quotes": "实时行情",
    "news": "个股新闻",
}

# 账本状态的**人话**。放在这里而不是前端：这四个值的含义就写在 `NotifyEvent` 的类注释里，
# 把翻译搬到界面上，等于让"pending 到底算不算送达"这件事在两处各有一份答案——
# 而 `pending` 恰恰是最容易被读成"已送达"的那一个（它没有送达，进程在发送前断了）。
STATUS_LABELS = {
    "pending": "未送达（进程中断）",
    "sent": "已送达",
    "failed": "失败",
    "skipped": "未配置·未发送",
}


# 「明日操作」在前端的路由。只作为**文字指引**出现（"见页面 /path/…"），不拼成可点链接：
# 线上是裸 IP + 8000 端口的 http，聊天客户端对这种链接的识别与打开都不可靠
# （见计划里的"非目标"）。
_PAGE_PATH = "/tomorrow"


def _byte_len(text: str) -> int:
    """**字节**数，不是字符数。企业微信的 4096 是字节上限，中文 3 字节/字。

    用 `len(text)` 量长度会在纯中文文案上低估三倍，于是"预算内"的消息被整条丢弃。
    """
    return len(text.encode("utf-8"))


def _truncate(text: str, max_bytes: int) -> str:
    """按字节截断到 `max_bytes` 以内，且不切碎多字节字符。兜底用，正常模板到不了。"""
    if _byte_len(text) <= max_bytes:
        return text
    body = text.encode("utf-8")[: max(0, max_bytes - 3)]
    # `errors="ignore"` 会丢掉尾部被切断的那半个字符，剩下的仍是合法 UTF-8。
    return body.decode("utf-8", errors="ignore") + "…"


def _render(
    head: Sequence[str],
    blocks: Sequence[Sequence[str]],
    tail: Sequence[str],
    *,
    max_bytes: int,
    disclose: Callable[[int, int], str],
) -> str:
    """`head` + 尽量多的 `blocks` + `tail`，总字节数不超过 `max_bytes`。

    **先给披露句留预算，再装正文**——顺序反过来就会出现"为了说明装不下而写的那句话
    自己把预算撑爆"，于是企业微信整条丢弃，用户收到的不是截断版而是什么都没有。
    装第 `i` 块时预留的是 `disclose(i + 1, 总数)` 的字节数（即"装到这儿就到此为止"
    那个最坏形态），所以披露句永远塞得下。
    """
    head_text = "\n".join(head)
    budget = max_bytes - _byte_len(head_text) - _byte_len("\n".join(tail))

    kept: list[str] = []
    used = 0
    for index, block in enumerate(blocks):
        text = "\n".join(block)
        # 这一块之后还有块要丢时，披露句必须仍在预算内
        reserve = 0
        if index < len(blocks) - 1:
            reserve = _byte_len("\n") + _byte_len(disclose(index + 1, len(blocks)))
        cost = _byte_len(text) + (_byte_len("\n") if kept else 0)
        if used + cost > budget - reserve:
            break
        kept.append(text)
        used += cost

    parts = list(head) + kept
    if len(kept) < len(blocks):
        parts.append(disclose(len(kept), len(blocks)))
    parts.extend(tail)
    return _truncate("\n".join(parts), max_bytes)


# ======================================================================================
# 出网（全模块唯一一处）
# ======================================================================================
def _post_json(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST 一个 JSON 并返回 `(状态码, 解析后的 body)`。**调用方必须读 body 里的 errcode。**

    这是测试的 monkeypatch 缝（照 `tests/test_sync_integration.py` 对 `get_provider` 的
    既有手法）：所有单测都替换本函数，因此测试永远不出网。
    """
    response = httpx.post(url, json=payload, timeout=_TIMEOUT)
    return response.status_code, response.json()


def dedup_key(kind: str, basis: Optional[date], *extra: str) -> str:
    """幂等键的唯一构造处。`:kind:日期[:…]`。

    日期**永不为空**：`basis is None`（今天没能确定交易日）时退到 `date.today()` ——
    "今天定不出交易日"本身就是一件当天的事实，而不是"可以省掉日期"。少了它，
    幂等键会退化成 `daily`，于是**这一辈子只会推一条日报**。
    """
    day = basis or date.today()
    return ":".join([kind, day.isoformat(), *extra])


def push(
    db: Session,
    *,
    kind: str,
    key: str,
    content: str,
    summary: str = "",
) -> NotifyEvent:
    """发出（或判定无需发出）一条消息，**永不抛异常**。返回账本里那一行。

    顺序是刻意的：
    1. **先插行**。`key` 是唯一键，插得进去才说明"今天这条还没发过"；`IntegrityError`
       即已推过（进程重启也不例外），`rollback` 后直接返回旧行——持久化幂等，不靠内存状态。
    2. webhook 没配 → `status="skipped"`，**不出网**。`skipped` 不是失败：它是"没开"，
       `/settings` 必须与 `failed` 分开显示。
    3. 出网失败或 `errcode != 0` → `status="failed"`，把 `errcode`/`errmsg` 存进账本。

    `summary` 是给账本列表看的一句话（默认取首行），与 `content` 分开存：
    列表读摘要，点开读原文。
    """
    settings = get_settings()
    event = NotifyEvent(
        kind=kind,
        dedup_key=key,
        status="pending",
        byte_len=_byte_len(content),
        summary=(summary or _first_line(content))[:200],
        content=content,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        # 唯一键撞了 = 已经推过。**必须 rollback**：不回滚的话这个 Session 此后
        # 每个查询都会抛 PendingRollbackError。
        db.rollback()
        existing = db.scalar(select(NotifyEvent).where(NotifyEvent.dedup_key == key))
        if existing is not None:
            logger.info("推送[%s] %s 已发过，跳过", kind, key)
            return existing
        # 极窄的竞态：另一线程插了同一把钥匙又删了。不重发（重复打扰比漏一条糟），
        # 返回这个未落库的 transient 对象，调用方无从区分也不需要区分。
        return event

    url = settings.wecom_webhook_url.get_secret_value()
    # 门就是 `Settings.notify_configured` 那一条，**不在这里再拼一遍 `enabled and url`**：
    # 拼两遍迟早会让"能不能推"与 `/system/status` 显示的"配没配"对不上。
    if not settings.notify_configured:
        event.status = "skipped"
        event.error = (
            "未配置 WECOM_WEBHOOK_URL" if not url else "NOTIFY_ENABLED=false"
        )
        db.commit()
        logger.info("推送[%s] 未启用（%s），只记账不出网", kind, event.error)
        return event

    try:
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        status_code, body = _post_json(url, payload)
        event.http_status = status_code
        code = body.get("errcode")
        event.errcode = int(code) if code is not None else None
        if event.errcode == _OK_CODE:
            event.status = "sent"
        else:
            event.status = "failed"
            event.error = str(body.get("errmsg", ""))[:300]
            logger.warning(
                "推送[%s] 企业微信拒绝: errcode=%s errmsg=%s", kind, event.errcode, event.error
            )
    except Exception as exc:  # 一次推送失败绝不能让调度器或 API 挂掉
        event.status = "failed"
        event.error = f"{classify_error(exc)}: {exc}"[:300]
        logger.warning("推送[%s] 出网失败: %s", kind, event.error)

    db.commit()
    return event


def _first_line(content: str) -> str:
    """账本摘要取首行并剥掉 markdown 记号，纯中文标题不该在列表里带 `#` 和 `**`。"""
    line = next((item for item in content.splitlines() if item.strip()), "")
    return line.lstrip("#>*- ").replace("**", "").strip()


# ======================================================================================
# 渲染器：全是纯函数（入参 → str），诚实契约因此在测试里可逐字节断言
# ======================================================================================
@dataclass(frozen=True)
class PositionTally:
    """持仓检查的三种结局。**"没检查"与"检查了没触发"必须分得开。**

    `decision.advise_position` 在库内不足 `_MIN_BARS` 时提前返回，那时
    `stop_triggered=False` 且 `trail_triggered=False` —— 与"检查过、没触发"长得一模一样。
    唯一的区别是那一支带了非空 `data_warning`（`decision.py:1173`，另两条返回路径都是 `""`）。
    不靠 `data_warning` 把它分出来，用户就会以为所有持仓都被盯着，而实际上一只都没查。
    """

    total: int
    checked: int
    triggered: int
    unchecked: tuple[tuple[str, str], ...]  # (ts_code, name)

    @property
    def has_unchecked(self) -> bool:
        return bool(self.unchecked)


def is_triggered(item: decision.PositionAdvice) -> bool:
    """要不要为这只持仓叫醒你。**全仓唯一的"触发"判据。**

    傍晚那个 job 用它挑要发的告警，日报的计数也用它——两处各写一遍 `stop_triggered or
    trail_triggered` 就会让"日报说 1 只触发、告警里没有"或者反过来。

    `data_warning` 非空 = 库内日线不足、`advise_position` 提前返回，那时两个 flag 都是
    False。**"没算过"与"算了没触发"在这里必须分开**，否则用户会以为所有持仓都被盯着。
    """
    return not item.data_warning and (item.stop_triggered or item.trail_triggered)


def tally_positions(advices: Sequence[decision.PositionAdvice]) -> PositionTally:
    """`advise_position` 的一批结果 → 三个数字 + 没检查的名单。

    `status == "closed"`（已清仓）不算持仓，不进分母：拿它进"持仓 4 只"会让数字对不上
    `/strategy/positions` 里真正有股的只数。
    """
    held = [item for item in advices if item.status == "holding"]
    unchecked = tuple((item.ts_code, item.name) for item in held if item.data_warning)
    return PositionTally(
        total=len(held),
        checked=len(held) - len(unchecked),
        triggered=sum(1 for item in held if is_triggered(item)),
        unchecked=unchecked,
    )


def _verdict_tally(plans: Sequence[decision.Plan]) -> dict[str, int]:
    """判定直方图。用 `decision._VERDICT_LABELS` 那份词表而不是另写一遍三个词。"""
    counts: dict[str, int] = {}
    for plan in plans:
        counts[plan.honesty.verdict] = counts.get(plan.honesty.verdict, 0) + 1
    return counts


def _verdict_line(plans: Sequence[decision.Plan]) -> str:
    """`样本不足 209、负期望 1、正期望 0`。顺序固定为 正/负/不足，便于两眼对比。

    三个词都来自内核那份 `_VERDICT_LABELS`（"负期望"这三个字必须与页面同源）。
    """
    counts = _verdict_tally(plans)
    order = ("positive", "negative", "insufficient")
    parts = [
        f"{VERDICT_LABELS.get(key, key)} {counts[key]}" for key in order if counts.get(key)
    ]
    return "、".join(parts) if parts else "无"


def _honesty_line(plan: decision.Plan) -> str:
    """一行的实测记录：**笔数先于期望**，且负期望/样本不足照实标、不弱化。

    负期望整行裹 `<font color="warning">`（橙红），样本不足裹 `comment`（灰）：手机上
    必须一眼看出这不是买入。这两个色号与企业微信 markdown 支持的色号一致。
    """
    honesty = plan.honesty
    value = f"{honesty.expectancy_pct:+.2f}%" if honesty.expectancy_pct is not None else "无"
    label = VERDICT_LABELS.get(honesty.verdict, honesty.verdict)
    text = f"实测 {honesty.trade_count} 笔 / 每笔 {value} / {label}"
    if honesty.verdict == "negative":
        return f'<font color="warning">{text}</font>'
    if honesty.verdict == "insufficient":
        return f'<font color="comment">{text}</font>'
    return text


def _plan_block(plan: decision.Plan, index: int) -> list[str]:
    """一只候选的三行：身份+结论 / 四个价位 / 实测记录。

    四个价位全部锚定库内最后一根收盘（与「明日操作」页面同一个锚点，见 `board.build_board`），
    所以这里印 `plan.price` 而**不是**实时报价——印实时价会让同一行的价格与期望分属两个
    基准，而期望是按收盘序列回测出来的。
    """
    zone = plan.entry_zone
    stop = plan.stop_loss
    hold = plan.expected_hold
    lines = [
        f"{index}. **{plan.name}** {plan.ts_code} · {plan.action_label}",
        f"{zone.low:.2f}–{zone.high:.2f} 入场 · 止损 {stop.recommended:.2f} · "
        f"约持 {hold.low}–{hold.high} 根",
        _honesty_line(plan),
    ]
    # `action` 非 buy 的行走不到这个渲染器（调用方只挑 buy 的），但万一行级结论被上游改了，
    # `reasons` 是唯一能解释"为什么不是买入"的东西，宁多一句也不要一个孤零零的"观望"。
    if plan.action != "buy" and plan.reasons:
        lines.append(f"　{plan.reasons[0]}")
    return lines


def _market_edge_line(board: Board, settings_equity: float) -> str:
    """全市场汇总闸的状态。**"闸没跑"与"跑了没否决"是两个截然不同的 0。**

    `board.market_edge is None` 表示从没算过池化、或 `rule_version` 与当前内核不符
    （`board.market_edge` 的 docstring），此时买入否决闸**根本没执行**。不说出来的话，
    "今日无买入候选"会被读成"规则跑完了、确实没有"，而事实可能是"闸没开"。
    """
    edge = board.market_edge
    if edge is None:
        return "> 全市场汇总缺失（未算过或内核版本不符），本次**未执行**买入否决闸"
    value = f"{edge.expectancy_pct:+.2f}%" if edge.expectancy_pct is not None else "无"
    # **`computed_at` 必须跟着数字**：池化是一天一次的任务，它若连着一周失败（最常见是
    # 积分档位不够），这一行会天天印同一组陈旧数字，而读者无从知道那是上周的。
    when = f"（{edge.computed_at:%Y-%m-%d} 算）" if edge.computed_at else "（算过但无时间戳）"
    # 全角括号前**不留空格**（`when` 自带括号）。半角空格配全角括号在手机上就是一处
    # 「全市场 （2026-09-18 算）」——像是打错了，而这条消息是唯一会到手机上的东西。
    body = (
        f"全市场{when}{VERDICT_LABELS.get(edge.verdict, edge.verdict)}："
        f"{edge.trade_count} 笔 / 每笔 {value}"
    )
    if edge.is_baseline:
        return f"> {body}（基准行，不参与否决）"
    return f"> {body}（只否决、不放行，按假设本金 ¥{settings_equity:,.0f} 计算）"


def render_daily(
    board: Board,
    positions: Sequence[decision.PositionAdvice],
    *,
    max_bytes: int,
    equity: float,
) -> str:
    """收盘日报。**有候选与无候选是同一份表头、同一份脚注**，只有中段不同。

    无候选的日子（按线上现状那是常态，见 `board.build_board`）仍然要带全四件事，否则
    "0 个"是歧义的：候选总数 / 判定直方图 / 样本深度 / 持仓检查状态。用户已确认
    「照推，压成最短形态」——短是指不带 200 行名单，不是指省掉这几句。
    """
    plans = [plan for sector in board.sectors for plan in sector.plans]
    buys = [plan for plan in plans if plan.action == "buy"]
    candidates = sum(sector.candidates for sector in board.sectors)
    tally = tally_positions(positions)

    as_of = board.basis_date.isoformat() if board.basis_date else "无库内数据"
    head = [f"# 明日操作 · 数据截至 {as_of}"]
    if board.strategy_label:
        head.append(
            f"> {board.strategy_label} · 库内样本中位 **{board.bars_median} 根**"
            f" / 窗口 {board.window_days} 根"
        )
    head.append(
        f"> 全市场 {len(board.sectors)} 个板块 / 候选 {candidates} 只 / 实算 {len(plans)} 只"
    )

    # 持仓那三行：**触发与未检查都要出现**。没有触发时也印"今日无触发"——
    # 沉默在这里是有歧义的（消息没发出去 / 没持仓 / 检查过没事，三者长得一样）。
    if tally.total or tally.has_unchecked:
        if tally.triggered:
            line = f"> 持仓 {tally.total} 只：**{tally.triggered} 只触发**，详见下一条消息"
        else:
            line = f"> 持仓 {tally.total} 只：已检查 {tally.checked} 只，今日无触发"
        if tally.has_unchecked:
            line += f"；另有 {len(tally.unchecked)} 只**未做检查**（库内日线不足）"
        head.append(line)

    # 候选 → 实算之间有缺口时要说清缺口是**两边**造成的：按市值截断（我们挑过了）
    # 与库内日线不足（数据没拉到）是两件事，混成一句就是把后者说成前者
    # （`BoardSector` 的 docstring 对这条有完整说明）。
    missing = candidates - len(plans)
    if missing:
        short = len(board.skipped)
        reasons = []
        if missing - short > 0:
            reasons.append(f"板块内按市值截断 {missing - short} 只")
        if short > 0:
            reasons.append(f"库内日线不足未实算 {short} 只")
        head.append(f"> 另有 {missing} 只未实算（{'、'.join(reasons)}）")

    # 判定直方图**两个分支都印**。有候选时它尤其不能省：`210 只里只有 3 只过关`是读者
    # 校准"这 3 只值多少信任"的唯一依据，藏起来就等于让 3 只看起来像"找到了 3 只"。
    head.append(f"> 判定：{_verdict_line(plans)}")

    if buys:
        head.append(f"**可分批买入 {len(buys)} 只**（样本充足且正期望）")
    else:
        head.append("**今日无买入候选。**")
        # 样本深度已在表头印过（`库内样本中位 **98 根** / 窗口 500 根`），这里**不再重印
        # 数字**，只说它意味着什么——重复一遍数字多花 60 字节，还让人以为有两组统计量。
        head.append("库内样本远短于窗口，实测笔数太少 —— 按纪律「样本不足不下结论」")

    blocks: list[list[str]] = []
    if buys:
        by_sector: dict[str, list[decision.Plan]] = {}
        for plan in buys:
            by_sector.setdefault(plan.industry or "未知", []).append(plan)
        index = 0
        for industry, group in by_sector.items():
            # 板块标题**并入该板块第一只股票的块**，而不是单独一块。这样"一块 = 一只股票"，
            # 截断披露里的数字就与读者能数出来的只数一致——标题单独占一块时，披露会比
            # 实际列出的股票数多出头几个板块数，读的人会以为漏了票。
            for offset, plan in enumerate(group):
                index += 1
                block = _plan_block(plan, index)
                if offset == 0:
                    block = [f"**{industry}（{len(group)}）**", *block]
                blocks.append(block)

    # 口径正文**只在真有候选行时**才印，空名单换成一句指向页面的指引。
    #
    # 这不是"省掉诚实"，而是让诚实挂在该挂的东西上：`caveat` 限定的是**实测数字**的
    # 可靠性（未复权、满仓口径），而空名单里一个实测数字都没有；`note` 解释的是"这份名单
    # 怎么来的"，而名单是空的。2026-09-19 拿线上真实榜单渲染实测：两者合计 900 字节，
    # 占整条消息 1462 字节的 60%，把"今日无买入候选"这四个字淹掉了——而用户要的正是
    # 「无候选时压成最短形态」。**有候选时两者照旧全文印出**，那时数字在，限定条件必须跟着。
    #
    # 另外 `caveat`/`strategy_label` 是在 `build_board` 的行循环里赋的值，空名单时是 `""`：
    # **不许印空槽**（`> ` 后面吊一个空行会被读成"这里本来有句话"）。
    tail: list[str] = []
    if buys:
        tail.extend(f"> {item}" for item in (board.caveat, board.note) if item)
    else:
        tail.append(f"> 候选口径与完整名单（含未列入者）见 {_PAGE_PATH}")
    tail.append(_market_edge_line(board, equity))

    def disclose(kept: int, total: int) -> str:
        """被截掉的部分要说清两件事：列了几只、没列的那些判定如何。

        只说"已截断"而不给分布，读者会默认被截掉的是"剩下的差票"——而本仓
        「候选 28 只，按市值列出前 10 只」的既有披露正是为了拦住这种默认。
        """
        dropped = total - kept
        return (
            f"> 名单过长，只列了 {kept} / {total} 只；其余 {dropped} 只未列出。"
            "**未列出不是「已排除」，是「证据不够，不下结论」**。"
        )

    return _render(head, blocks, tail, max_bytes=max_bytes, disclose=disclose)


def render_stop_alert(
    triggered: Sequence[decision.PositionAdvice],
    *,
    as_of: Optional[date],
    max_bytes: int,
) -> str:
    """止损/移动止盈触发的告警。**只在真有触发时调用**（无触发不出消息）。

    口径与 `/strategy/positions` 的 `stop_triggered` 逐位一致：收盘价、库里最后一根。
    绝不按实时价重算——那会造出第二个"触发"定义，而页面上的那个仍是收盘口径。
    """
    day = as_of.isoformat() if as_of else "无库内数据"
    head = [
        f"# 持仓触发 · 收盘口径 {day}",
        f"> {len(triggered)} 只持仓收盘触发。口径与 /strategy/positions 逐位一致，未按实时价重算。",
    ]
    blocks = [[*_stop_block(item)] for item in triggered]
    tail = ["> 触发时点为**收盘**：盘中价格穿过这些位置不算触发，请以收盘价为准。"]

    def disclose(kept: int, total: int) -> str:
        return (
            f"> 触发 {total} 只，只列了前 {kept} 只；"
            f"其余 {total - kept} 只请打开 /strategy/positions 查看。"
        )

    return _render(head, blocks, tail, max_bytes=max_bytes, disclose=disclose)


def _stop_block(item: decision.PositionAdvice) -> list[str]:
    """一只触发持仓的正文。

    `trail_level` 可能为 `None`：`decision._exit_rules` 的移动止盈有两支，`trail.level is None`
    时走的是 ATR 兜底（`high_water - price >= 2×ATR`）。那一支没有价位可报，就**不报价位**
    ——编一个出来比留白糟得多。文字复用 `_exit_rules` 自己的标签，不另创词。
    """
    price = f"{item.price:.2f}" if item.price is not None else "无"
    lines = [f"**{item.name}** {item.ts_code} · 收盘 ¥{price}"]
    if item.stop_triggered and item.stop_price is not None:
        source = "你的止损" if item.stop_source == "user" else "规则止损"
        lines.append(f"跌破止损位 ¥{item.stop_price:.2f}（{source}）")
    if item.trail_triggered:
        if item.trail_level is not None:
            lines.append(f"跌破移动止盈位 ¥{item.trail_level:.2f}（自最高价回落 2×ATR）")
        else:
            lines.append("自最高价回落已达 2×ATR（该股无移动止盈价位，按 ATR 兜底判定）")
    facts = [f"建议：{item.action_label}"]
    if item.pnl_pct is not None:
        facts.append(f"浮盈亏 {item.pnl_pct:+.2f}%")
    if item.stop_distance_pct is not None:
        facts.append(f"距止损位 {item.stop_distance_pct:+.2f}%")
    if item.hold_bars:
        facts.append(f"已持 {item.hold_bars} 个交易日")
    lines.append(" · ".join(facts))
    # `reasons` 是内核给出的判定依据，逐条照抄。限 2 条：一条消息里塞 5 只持仓时，
    # 理由全展开会把真正要看的价格挤掉。
    lines.extend(f"　{item.reasons[i]}" for i in range(min(2, len(item.reasons))))
    if item.data_warning:
        lines.append(f"　⚠️ {item.data_warning}")
    return lines


def render_sync_alert(
    job_type: str, error_class: str, message: str, *, as_of: Optional[date]
) -> str:
    """同步任务失败告警。带 `error_class` + 上游原话，让人能直接判断要不要管。"""
    day = as_of.isoformat() if as_of else date.today().isoformat()
    label = JOB_LABELS.get(job_type, job_type)
    lines = [
        f"# 同步失败 · {label}",
        f"**{label}**（`{job_type}`）在 {day} 的同步未成功。",
        f"错误类型：`{error_class or 'unknown'}`",
    ]
    if message:
        lines.append(f"上游返回：{message[:200]}")
    lines.append("> 只对重要任务告警（`quotes`/`news` 每 30 秒一次，不在白名单内）。")
    lines.append("> 本任务下次成功后会自动收到一条「已恢复」。")
    return _truncate("\n".join(lines), 4000)


def render_sync_recovered(job_type: str, *, as_of: Optional[date]) -> str:
    """恢复通知。没有它，失败告警就是一条永远悬着的坏消息。"""
    day = as_of.isoformat() if as_of else date.today().isoformat()
    label = JOB_LABELS.get(job_type, job_type)
    return f"# 同步已恢复 · {label}\n**{label}**（`{job_type}`）在 {day} 的同步已恢复正常。"


def render_sync_disabled(job_type: str, message: str) -> str:
    """任务被临时禁用。

    这条独立存在是因为 `job_disabled` 在 `run_sync` **之前**短路（`scheduler.py:38-40`），
    被禁用的任务根本不写 `SyncRun` —— 状态机看不到它，而"被禁用"恰恰是最需要人处理的状态
    （provider 权限没配好，数据会一直不更新，且页面上什么都看不出来）。
    """
    label = JOB_LABELS.get(job_type, job_type)
    lines = [
        f"# 同步任务被禁用 · {label}",
        f"**{label}**（`{job_type}`）因账号权限不足被临时禁用，已停止执行。",
    ]
    if message:
        lines.append(f"原因：{message[:200]}")
    lines.append("> 这类任务不写同步记录，所以只有这条消息能告诉你它没在跑。")
    return _truncate("\n".join(lines), 4000)


def render_test() -> str:
    """测试消息。**不接受任何入参**——因此不存在攻击者可控的内容。

    站点没有鉴权（只有 `TrustedHostMiddleware` 限制 Host 头），任何拿到 IP 的人都能
    触发这个端点。文案固定 + 限流 1 次/分钟，把能造成的后果压到"往群里发一条固定文本"。
    """
    return "\n".join(
        [
            "# 推送测试",
            "这条消息来自你的 A 股终端，说明企业微信 webhook 配通了。",
            "> 能收到它，日报与止损告警就能送达。内容全由服务器计算，未经第三方。",
        ]
    )
