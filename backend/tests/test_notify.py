"""微信推送：渲染器（纯函数）+ `push` 的账本/幂等/失败语义。

**渲染器测试不碰数据库、不碰网络**：`Board` 与 `decision.Plan` 都是裸 dataclass，
`notify` 只消费它们。所以这一半测的是"文案是不是那一份"，而不是"数字算得对不对"
（后者由 `test_quant_*` 与 `test_strategy_simple.py` 钉着）。

`push` 那一半全部替换 `_post_json`，因此测试**永不出网**。
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta

import pytest
from app import scheduler as scheduler_mod
from app.api import notify_routes
from app.core.config import Settings
from app.db.models import NotifyEvent, SyncRun
from app.providers.rate_limiter import SlidingWindowRateLimiter
from app.services import board as board_service
from app.services import notify
from app.services import positions as position_service
from app.services.board import Board, BoardSector, SkippedCandidate
from app.services.quant import backtest as backtest_engine
from app.services.quant import decision
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker


# ======================================================================================
# 造数据：全是内存里的 dataclass，一行数据库都不用
# ======================================================================================
def _expectancy(**overrides) -> backtest_engine.Expectancy:
    """一个显式写死数字的实测记录。

    刻意不用真的跑回测：本文件的断言要逐字比 `42 笔 / 每笔 +1.80%`，那些数字只有在这里
    写死才说明"文案是从 `honesty` 抄的"——如果渲染器自己算了什么，它算不出这两个数。
    """
    base: dict = {
        "strategy": "fusion",
        "strategy_label": "多策略融合",
        "window_days": 500,
        "bars": 98,
        "trade_count": 42,
        "win_rate": 55.0,
        "avg_win_pct": 4.0,
        "avg_loss_pct": -2.5,
        "profit_factor": 1.6,
        "expectancy_pct": 1.8,
        "expectancy_per_bar_pct": 0.15,
        "avg_hold_bars": 12.0,
        "forced_end_trades": 3,
        "cumulative_return": 20.0,
        "max_drawdown": -8.0,
        "benchmark_return": 5.0,
        "excess_return": 15.0,
        "verdict": "positive",
        "verdict_text": "该规则在本标的实测为正期望。",
        "caveat": "实测只描述这段历史，不预示未来。",
    }
    base.update(overrides)
    return backtest_engine.Expectancy(**base)


def _bars(count: int = 160, *, start: float = 20.0) -> list[dict]:
    """一段有涨有跌的合成日线，够 MA20/ATR14 预热，也让均线真的交叉过。"""
    rows = []
    day = date(2024, 1, 2)
    price = start
    for index in range(count):
        price *= 1.02 if (index // 20) % 2 == 0 else 0.985
        rows.append(
            {
                "trade_date": day,
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1_000_000.0,
                "amount": 20_000_000.0,
            }
        )
        day += timedelta(days=1)
    return rows


def _plan(**overrides) -> decision.Plan:
    """一个**真** `Plan`（走内核 `build_plan`），再按需替换字段——测的是搬运，不是内核。"""
    bars = _bars()
    plan = decision.build_plan(
        bars,
        ts_code="600900.SH",
        name="长江电力",
        industry="电力",
        strategy="fusion",
        expectancy=_expectancy(),
    )
    return dataclasses.replace(plan, **overrides)


def _board(*sectors: BoardSector, **overrides) -> Board:
    base: dict = {
        "basis_date": date(2026, 9, 18),
        "strategy": "fusion",
        "strategy_label": "多策略融合",
        "window_days": 500,
        "bars_median": 98,
        "note": "候选口径：综合评分与时机两步都过，每板块最多列总市值最大的 10 只。",
        "caveat": "实测只描述这段历史，不预示未来。",
        "market_edge": None,
        "sectors": tuple(sectors),
        "skipped": (),
    }
    base.update(overrides)
    return Board(**base)


def _sector(industry: str, *plans: decision.Plan, candidates: int | None = None) -> BoardSector:
    return BoardSector(
        industry=industry,
        candidates=candidates if candidates is not None else len(plans),
        selected=len(plans),
        plans=tuple(plans),
    )


def _render_daily(board: Board, *, max_bytes: int = 4000, positions=()) -> str:
    return notify.render_daily(board, positions, max_bytes=max_bytes, equity=1_000_000.0)


# ======================================================================================
# 1：日报必须带全四件事
# ======================================================================================
def test_no_candidate_digest_carries_the_four_facts() -> None:
    """无候选的日子 → **照推**，且四件事一件不少。

    这几句就是"沉默是歧义的"的解药：候选多少 / 判定分布 / 样本有多薄 / 持仓查没查。
    少任何一条，"今日无买入候选"都会退化成一句无法证伪的话（推送坏了？没同步？真没候选？）。
    """
    plan = _plan(
        action="watch",
        action_label="观望：样本不足",
        honesty=_expectancy(verdict="insufficient", trade_count=3, expectancy_pct=0.92),
    )
    board = _board(_sector("电力", plan, candidates=210))

    text = _render_daily(board)

    assert "2026-09-18" in text  # 数据日期：没有它，读者不知道这是哪天的结论
    assert "多策略融合" in text  # 口径：哪条规则
    assert "**98 根**" in text and "500 根" in text  # 样本深度 vs 窗口
    assert "样本不足 1" in text  # 判定直方图
    assert "今日无买入候选" in text
    # `market_edge=None`（从没算过池化 / 内核版本不符）**必须说出来**：不说的话
    # "0 个候选"会被读成"规则跑完了确实没有"，而事实可能是否决闸没开
    assert "未执行" in text and "买入否决闸" in text

    # 无候选时**不该**出现买入标题，否则自相矛盾
    assert "可分批买入" not in text


def test_buy_row_copies_the_measured_numbers_verbatim() -> None:
    """有候选时，行里的笔数与期望**逐字**来自 `honesty`。

    `42 笔 / 每笔 +1.80%` 这两个数只在 `_expectancy()` 里出现过。渲染器若自己重算，
    它算不出这两个数——这正是"结论必须是页面那一份，不许重算"的可执行版本。
    """
    plan = _plan(action="buy", action_label="信号与实测均支持买入")
    board = _board(_sector("电力", plan))

    text = _render_daily(board)

    assert "可分批买入 1 只" in text
    assert "长江电力" in text and "600900.SH" in text
    assert "实测 42 笔 / 每笔 +1.80% / 正期望" in text
    assert "信号与实测均支持买入" in text  # action_label 逐字照抄
    # 入场区与止损来自 `Plan`，不是另算的
    assert f"{plan.entry_zone.low:.2f}–{plan.entry_zone.high:.2f} 入场" in text
    assert f"止损 {plan.stop_loss.recommended:.2f}" in text


def test_empty_slots_are_never_printed() -> None:
    """`strategy_label`/`caveat` 在空名单时是空串 → 那一行整条不印。

    `> ` 后面吊一个空行会被读成"这里本来有句话"，比不印更糟。
    """
    board = _board(_sector("电力"), strategy_label="", caveat="", sectors=())
    text = _render_daily(board)

    assert "> \n" not in text and not text.rstrip().endswith(">")
    assert "**：" not in text  # 空的加粗标签


# ======================================================================================
# 2：负期望绝不能读成买入
# ======================================================================================
def test_a_negative_row_can_never_read_as_a_buy() -> None:
    """负期望的行必须**紧挨着**警告色与"负期望"三个字。

    内核保证了 `verdict="negative"` 时 `action` 不会是 `buy`（见
    `test_negative_expectancy_can_never_render_as_buy`）。这里把那个不可能的状态**故意
    造出来**，钉住"即便上游哪天把 action 推错，手机上也不会出现一行干净的买入"。
    色号与企业微信支持的一致；`warning` 在客户端渲染成橙红。
    """
    plan = _plan(
        action="buy",
        action_label="信号与实测均支持买入",
        honesty=_expectancy(verdict="negative", trade_count=30, expectancy_pct=-1.2),
    )
    text = _render_daily(_board(_sector("电力", plan)))

    assert "负期望" in text
    assert '<font color="warning">' in text
    # 色标必须包住那一整行，只标个号码等于没标
    assert '<font color="warning">实测 30 笔 / 每笔 -1.20% / 负期望</font>' in text
    # 「买入」这两个字与警告必须同处一行，中间不能隔着段落
    row = next(line for line in text.splitlines() if "长江电力" in line)
    assert "买入" in row
    assert "负期望" in text.split("**电力")[1]


def test_insufficient_rows_are_grey_not_warning() -> None:
    """样本不足是灰（`comment`），不是橙红——两者是不同的事，颜色不能通用。"""
    plan = _plan(
        action="buy",
        action_label="买入",
        honesty=_expectancy(verdict="insufficient", trade_count=3, expectancy_pct=0.92),
    )
    text = _render_daily(_board(_sector("电力", plan)))

    assert '<font color="comment">实测 3 笔 / 每笔 +0.92% / 样本不足</font>' in text
    assert "warning" not in text


# ======================================================================================
# 3：截断必须被明说，且披露句自己不能撑爆预算
# ======================================================================================
def test_truncation_is_disclosed_with_counts() -> None:
    """装不下的部分要写出「列了 N / 共 M」，不许静默丢。"""
    plans = tuple(
        _plan(ts_code=f"60090{i}.SH", name=f"测试股{i}", action="buy", action_label="买入")
        for i in range(60)
    )
    board = _board(_sector("电力", *plans))

    text = _render_daily(board, max_bytes=2000)

    assert notify._byte_len(text) <= 2000
    assert "只列了" in text and "未列出不是「已排除」" in text
    # 真的截了（否则这条测试是空转）
    kept = text.count("入场 · 止损")
    assert kept < len(plans)
    # 披露句里的数字必须与**读者能数出来的只数**一致。板块标题如果单独占一块，
    # 这里就会多出板块数——读的人会以为自己漏看了几只票。
    assert f"只列了 {kept} / {len(plans)} 只" in text
    assert f"其余 {len(plans) - kept} 只未列出" in text


@pytest.mark.parametrize("max_bytes", [4000, 1200, 600, 300])
def test_the_budget_holds_even_when_it_is_absurd(max_bytes: int) -> None:
    """`_render` 的契约：**任何预算下都不超**。

    这是"宁可长"与"4096 字节上限"之间唯一的仲裁者。越是极端的预算越要测：企业微信收到
    超限消息不是截断显示，而是**整条丢弃**——用户收到的会是一片空白。
    """
    plans = tuple(
        _plan(ts_code=f"60090{i}.SH", name=f"测试股{i}", action="buy", action_label="买入")
        for i in range(60)
    )
    text = _render_daily(_board(_sector("电力", *plans)), max_bytes=max_bytes)

    assert notify._byte_len(text) <= max_bytes


def test_stop_alert_truncates_with_a_pointer_not_a_silent_drop() -> None:
    """止损告警被截断时，指向 `/strategy/positions`——名单短不下来的那一头要能自己去看。"""
    items = [_triggered(f"60051{i}.SH", f"持仓{i}") for i in range(40)]
    text = notify.render_stop_alert(items, as_of=date(2026, 9, 18), max_bytes=1200)

    assert notify._byte_len(text) <= 1200
    assert "只列了前" in text and "/strategy/positions" in text


# ======================================================================================
# 止损告警：形态与"没检查"的披露
# ======================================================================================
def _position(ts_code: str, name: str, **overrides) -> decision.PositionAdvice:
    """一个**自洽的未触发**持仓：收盘 ¥18.00 在止损位 ¥17.50 之上。

    自洽是要紧的——`stop_triggered=False` 配一个高于收盘的止损位是造不出来的状态，
    拿它当基准会让后面"加上 `stop_triggered=True`"的测试变成在测一个假对象。
    """
    base: dict = {
        "ts_code": ts_code,
        "name": name,
        "industry": "电力",
        "status": "holding",
        "shares": 1000.0,
        "avg_cost": 20.0,
        "cost": 20_000.0,
        "price": 18.0,
        "price_source": "close",
        "is_stale": False,
        "pnl": -2_000.0,
        "pnl_pct": -10.0,
        "realized_pnl": 0.0,
        "hold_bars": 30,
        "first_entry_date": date(2026, 8, 1),
        "trade_count": 1,
        "stop_price": 17.5,
        "stop_source": "rule",
        "stop_distance_pct": -2.78,
        "stop_triggered": False,
        "trail_level": None,
        "trail_triggered": False,
        "scores": None,
        "action": "hold",
        "action_label": "继续持有",
        "reasons": ("融合分 62.0 仍在多头区间",),
        "data_warning": "",
    }
    base.update(overrides)
    return decision.PositionAdvice(**base)


def _triggered(ts_code: str = "600519.SH", name: str = "贵州茅台", **overrides):
    """一个**自洽的已触发**持仓：收盘 ¥17.00 跌破了 ¥17.50 的止损位。"""
    base: dict = {
        "price": 17.0,
        "stop_price": 17.5,
        "stop_distance_pct": 2.94,
        "stop_triggered": True,
        "action": "exit",
        "action_label": "清仓：收盘跌破止损位",
        "reasons": ("收盘价 17.00 跌破止损位 17.50",),
    }
    base.update(overrides)
    return _position(ts_code, name, **base)


def test_stop_alert_reuses_the_kernel_wording_and_the_close_basis() -> None:
    text = notify.render_stop_alert(
        [_triggered()], as_of=date(2026, 9, 18), max_bytes=4000
    )

    assert "收盘口径 2026-09-18" in text
    assert "贵州茅台" in text and "¥17.00" in text
    assert "跌破止损位 ¥17.50（规则止损）" in text
    assert "清仓：收盘跌破止损位" in text  # action_label 逐字
    assert "未按实时价重算" in text  # 口径声明不能省


def test_a_users_own_stop_is_labelled_as_such() -> None:
    """用户存过止损 → 必须说"你的止损"。

    两者的区别是有意义的：用了用户的止损位，屏上的实测期望就不再描述他自己的风险
    （见 `decision.advise_position` 的 `stop_source`）。
    """
    text = notify.render_stop_alert(
        [_triggered(stop_source="user")], as_of=date(2026, 9, 18), max_bytes=4000
    )
    assert "（你的止损）" in text


def test_the_atr_fallback_trail_never_invents_a_level() -> None:
    """`trail_level is None` 那一支（ATR 兜底）**不报价位**。

    `decision._exit_rules` 的移动止盈有两支：有 level 就比 level，没 level 就比
    `high_water - 2×ATR`。后者的 `trail_triggered` 为真而 `trail_level` 为 None——
    渲染器若在这里漏判就会印出一个 `None`，或者更糟：编一个价位出来。
    """
    text = notify.render_stop_alert(
        [_triggered(trail_triggered=True, trail_level=None)],
        as_of=date(2026, 9, 18),
        max_bytes=4000,
    )
    assert "2×ATR" in text
    assert "None" not in text and "移动止盈位 ¥" not in text


def test_an_unchecked_holding_is_not_dressed_up_as_checked() -> None:
    """「没检查」与「检查了没触发」必须分得开。

    `decision.advise_position` 在库内不足 `_MIN_BARS` 时提前返回，`stop_triggered=False`
    且带 `data_warning`（`decision.py:1173`）——与"检查过没事"长得一模一样。不把它挑出来，
    日报会写「已检查 4 只，今日无触发」，而实际上一只都没查。
    """
    checked = _position("600519.SH", "贵州茅台")
    # 这句 `data_warning` 逐字来自 `decision.py:1173` 那一支
    unchecked = _position(
        "000001.SZ",
        "平安银行",
        data_warning="本地暂无该标的日线，请先同步数据；持仓数字本身不受影响",
    )
    board = _board()

    tally = notify.tally_positions([checked, unchecked])
    assert (tally.total, tally.checked, tally.triggered) == (2, 1, 0)
    assert tally.unchecked == (("000001.SZ", "平安银行"),)

    text = _render_daily(board, positions=[checked, unchecked])
    assert "持仓 2 只：已检查 1 只，今日无触发" in text
    assert "另有 1 只**未做检查**（库内日线不足）" in text

    # 反过来：全都检查过就不该出现"未做检查"这句话
    clean = _render_daily(board, positions=[checked])
    assert "已检查 1 只" in clean
    assert "未做检查" not in clean


def test_a_triggered_holding_is_counted_in_the_digest() -> None:
    """触发时日报要指路到下一条消息，别让告警孤零零地来。"""
    text = _render_daily(_board(), positions=[_triggered()])
    assert "**1 只触发**" in text and "详见下一条消息" in text


def test_a_trigger_is_never_counted_as_checked() -> None:
    """没检查的行即便 `stop_triggered` 为真也不算触发（它的 False 是"没算过"）。"""
    tally = notify.tally_positions([_triggered(data_warning="无日线")])
    assert tally.triggered == 0 and tally.checked == 0


# ======================================================================================
# 幂等键
# ======================================================================================
def test_the_dedup_key_never_drops_the_date() -> None:
    """`basis is None` 时退到 `today()`，**绝不产出没有日期的键**。

    少了日期，键会退化成一个常量，于是这一辈子只会推一条日报——而且完全静默。
    """
    assert notify.dedup_key("daily", date(2026, 9, 18)) == "daily:2026-09-18"
    assert notify.dedup_key("daily", None) == f"daily:{date.today().isoformat()}"
    assert notify.dedup_key("syncfail", date(2026, 9, 18), "fundamentals") == (
        "syncfail:2026-09-18:fundamentals"
    )
    assert notify.dedup_key("daily", date(2026, 9, 18)) != notify.dedup_key(
        "daily", date(2026, 9, 19)
    )


# ======================================================================================
# push：账本 / 幂等 / 失败语义（全部替换 `_post_json`，永不出网）
# ======================================================================================
class _Stub:
    """替换 `_post_json`：记录调用、回放预设的 (状态码, body) 或抛异常。"""

    def __init__(self, *, status: int = 200, body: dict | None = None, exc=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = status
        self.body = {"errcode": 0, "errmsg": "ok"} if body is None else body
        self.exc = exc

    def __call__(self, url: str, payload: dict):
        self.calls.append((url, payload))
        if self.exc is not None:
            raise self.exc
        return self.status, self.body


@pytest.fixture()
def webhook(monkeypatch):
    """配好 webhook 的设置 + 一个 stub 出网。返回 (stub, monkeypatch)。"""
    stub = _Stub()
    monkeypatch.setattr(notify, "_post_json", stub)
    monkeypatch.setattr(
        notify,
        "get_settings",
        lambda: Settings(_env_file=None, wecom_webhook_url="https://qyapi.example/send?key=SECRET"),
    )
    return stub


@pytest.fixture()
def scheduler_db(monkeypatch, engine):
    """把 `scheduler.SessionLocal` 钉到**本用例**的临时库。

    `conftest._isolate_session_db` 的会话级重定向指向另一个临时库，而
    `_alert_disabled` / `_evening_notify` 都是自己开 `SessionLocal()`。不钉这一处，
    用例写进去的行它们看不见——失败形态是"告警数为 0"，看上去像功能没写。
    （同 `conftest.client` 对 main/pool 的做法。）
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(scheduler_mod, "SessionLocal", factory)
    return factory


@pytest.fixture()
def evening(monkeypatch):
    """傍晚任务：**同一个** `Settings` 同时钉给 notify 与 scheduler，出网换成 stub。

    `webhook` fixture 只钉 `notify.get_settings`。调度器读的是它自己 import 的那个名字
    （`scheduler.get_settings`），不钉就会去读真实 `.env`：本机没配 webhook 时任务直接
    早退，断言会以"什么都没发生"的形式通过，看着像绿的。

    `notify_equity` 刻意用**非默认值**，否则"本金取自配置"那条测试证明不了什么。
    """
    settings = Settings(
        _env_file=None,
        wecom_webhook_url="https://qyapi.example/send?key=SECRET",
        notify_equity=2_000_000.0,
        notify_max_bytes=4_000,
    )
    stub = _Stub()
    monkeypatch.setattr(notify, "_post_json", stub)
    monkeypatch.setattr(notify, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler_mod, "get_settings", lambda: settings)
    return settings, stub


def test_a_successful_push_is_recorded_and_sent_once(db_session, webhook) -> None:
    event = notify.push(db_session, kind="test", key="test:1", content="# 推送测试\n正文")

    assert event.status == "sent"
    assert event.errcode == 0
    assert event.http_status == 200
    assert event.summary == "推送测试"  # 摘要剥掉 markdown 记号
    assert event.content == "# 推送测试\n正文"
    assert event.byte_len == notify._byte_len("# 推送测试\n正文")
    assert len(webhook.calls) == 1
    url, payload = webhook.calls[0]
    assert url.endswith("key=SECRET")
    # 必须是 markdown + content，键名写错企业微信用 errcode 拒绝而不是报错
    assert payload["msgtype"] == "markdown"
    assert payload["markdown"]["content"] == "# 推送测试\n正文"


def test_http_200_with_a_nonzero_errcode_is_a_failure(db_session, monkeypatch) -> None:
    """**这条挡住"密钥失效却永远记成已送达"。**

    企业微信 webhook 失效（机器人被移出群）时回的是 HTTP 200 + `errcode=93000`。
    只看状态码的实现在这里会得到 `sent`，于是推送坏掉几个月都没人发现。
    """
    stub = _Stub(status=200, body={"errcode": 93000, "errmsg": "invalid webhook url"})
    monkeypatch.setattr(notify, "_post_json", stub)
    monkeypatch.setattr(
        notify, "get_settings", lambda: Settings(_env_file=None, wecom_webhook_url="https://q/key=x")
    )

    event = notify.push(db_session, kind="daily", key="daily:2026-09-18", content="# 日报")

    assert event.status == "failed"
    assert event.http_status == 200
    assert event.errcode == 93000
    assert "invalid webhook url" in event.error


def test_an_unconfigured_webhook_never_touches_the_network(db_session, monkeypatch) -> None:
    """没配 webhook → 记一行 `skipped` 并返回，**一次出网都没有**。

    `skipped` 不是 `failed`：它是"没开"。`/settings` 必须分开显示，否则用户会去查一个
    并不存在的故障。
    """
    stub = _Stub()
    monkeypatch.setattr(notify, "_post_json", stub)
    monkeypatch.setattr(
        notify, "get_settings", lambda: Settings(_env_file=None, wecom_webhook_url="")
    )

    event = notify.push(db_session, kind="daily", key="daily:2026-09-18", content="# 日报")

    assert event.status == "skipped"
    assert stub.calls == []
    assert "WECOM_WEBHOOK_URL" in event.error
    assert db_session.scalar(select(func.count()).select_from(NotifyEvent)) == 1


def test_notify_enabled_false_is_also_skipped_not_failed(db_session, monkeypatch) -> None:
    """kill switch 关掉 = 没开，不是坏掉。"""
    stub = _Stub()
    monkeypatch.setattr(notify, "_post_json", stub)
    monkeypatch.setattr(
        notify,
        "get_settings",
        lambda: Settings(
            _env_file=None, wecom_webhook_url="https://q/key=x", notify_enabled=False
        ),
    )

    event = notify.push(db_session, kind="daily", key="daily:2026-09-18", content="# 日报")

    assert event.status == "skipped"
    assert stub.calls == []
    assert "NOTIFY_ENABLED" in event.error


def test_a_network_failure_never_raises(db_session, monkeypatch) -> None:
    """出网异常不能穿出去：调用方是调度线程，抛一次就毁掉整条傍晚任务链。"""
    import httpx

    stub = _Stub(exc=httpx.ConnectError("connection refused"))
    monkeypatch.setattr(notify, "_post_json", stub)
    monkeypatch.setattr(
        notify, "get_settings", lambda: Settings(_env_file=None, wecom_webhook_url="https://q/key=x")
    )

    event = notify.push(db_session, kind="daily", key="daily:2026-09-18", content="# 日报")

    assert event.status == "failed"
    assert event.error.startswith("network:")  # 词汇来自 `sync.classify_error`
    assert event.errcode is None


def test_a_malformed_response_never_raises_either(db_session, monkeypatch) -> None:
    """body 不是 JSON（网关返回 HTML 之类）也不能抛。"""
    stub = _Stub(exc=ValueError("Expecting value: line 1 column 1"))
    monkeypatch.setattr(notify, "_post_json", stub)
    monkeypatch.setattr(
        notify, "get_settings", lambda: Settings(_env_file=None, wecom_webhook_url="https://q/key=x")
    )

    event = notify.push(db_session, kind="daily", key="daily:2026-09-18", content="# 日报")

    assert event.status == "failed"


def test_the_same_key_is_never_pushed_twice(db_session, webhook) -> None:
    """幂等：同一个键调两次 → **只出网一次**、表里只有一行。

    幂等靠的是 `dedup_key` 的唯一约束（持久化），不是内存里的一个集合——所以进程重启后
    再跑一遍当天任务也不会重复发。19:00 的 job 被手工触发过、又被 cron 触发，就是这条。
    """
    first = notify.push(db_session, kind="daily", key="daily:2026-09-18", content="# 日报")
    second = notify.push(db_session, kind="daily", key="daily:2026-09-18", content="# 日报（改过）")

    assert first.id == second.id
    assert len(webhook.calls) == 1
    assert db_session.scalar(select(func.count()).select_from(NotifyEvent)) == 1
    # 撞键那一支必须 rollback——不回滚，这个会话此后每个查询都会抛 PendingRollbackError
    assert db_session.scalar(select(NotifyEvent.status)) == "sent"


def test_different_keys_both_go_out(db_session, webhook) -> None:
    """幂等是按键的，不是按天的——日报和止损告警同一天各发一条。"""
    notify.push(db_session, kind="daily", key="daily:2026-09-18", content="# 日报")
    notify.push(db_session, kind="stop", key="stop:2026-09-18", content="# 触发")

    assert len(webhook.calls) == 2
    assert db_session.scalar(select(func.count()).select_from(NotifyEvent)) == 2


def test_the_ledger_survives_a_crash_between_insert_and_send(db_session, monkeypatch) -> None:
    """插了行、还没发出去就崩了 → 留下 `pending`，**页面必须读成"未送达"**。

    这是"先插行再发送"的代价，也是它的价值：这一行证明"尝试过但结果未知"，
    比什么都不留要诚实。
    """
    monkeypatch.setattr(notify, "_post_json", _Stub(exc=SystemExit("进程被杀")))
    monkeypatch.setattr(
        notify, "get_settings", lambda: Settings(_env_file=None, wecom_webhook_url="https://q/key=x")
    )
    # SystemExit 不是 Exception 的子类，`except Exception` 抓不住——正是"崩了"的形态
    with pytest.raises(SystemExit):
        notify.push(db_session, kind="daily", key="daily:2026-09-18", content="# 日报")

    row = db_session.scalar(select(NotifyEvent))
    assert row is not None and row.status == "pending"


# ======================================================================================
# 白名单与文案
# ======================================================================================
def test_the_sync_whitelist_excludes_the_noisy_jobs() -> None:
    """`quotes`/`news` 不在白名单里。

    它们每 30 秒 / 每 5 分钟跑一次，provider 一抖动就是告警+恢复每半分钟一轮，
    直接撞上 20 条/分钟的限流（45009）——而被挤掉的恰好是"已恢复"那条。
    """
    assert "quotes" not in notify.SYNC_ALERT_JOBS
    assert "news" not in notify.SYNC_ALERT_JOBS
    for job in ("market", "fundamentals", "scores", "calendar", "stocks", "pool"):
        assert job in notify.SYNC_ALERT_JOBS
        assert job in notify.JOB_LABELS  # 每个可告警的任务都要有中文名


def test_sync_alert_carries_the_error_class_and_the_upstream_words() -> None:
    text = notify.render_sync_alert(
        "fundamentals", "rate_limit", "每分钟调用超限", as_of=date(2026, 9, 18)
    )
    assert "同步失败" in text and "财务快照" in text
    assert "`rate_limit`" in text and "每分钟调用超限" in text
    assert "已恢复" in text  # 告诉人这条坏消息有尽头


def test_sync_recovered_and_disabled_are_distinct_messages() -> None:
    recovered = notify.render_sync_recovered("market", as_of=date(2026, 9, 18))
    assert "同步已恢复" in recovered and "日线行情" in recovered

    # 被禁用的任务**不写 SyncRun**（`job_disabled` 在 run_sync 之前短路），
    # 所以只有这条消息能告诉你它没在跑——文案里必须说清这一点
    disabled = notify.render_sync_disabled("fundamentals", "账号无权限")
    assert "被禁用" in disabled and "账号无权限" in disabled
    assert "不写同步记录" in disabled


def test_the_test_message_takes_no_input() -> None:
    """测试消息是固定文案：这个端点无鉴权，任何可控内容都是被人当喇叭用。"""
    assert notify.render_test() == notify.render_test()
    assert "推送测试" in notify.render_test()


def test_the_market_edge_line_distinguishes_missing_from_not_vetoing() -> None:
    """`market_edge is None` = **闸没跑**，与"跑了没否决"是两回事。"""
    missing = _render_daily(_board())
    assert "未执行" in missing and "买入否决闸" in missing

    edge = decision.MarketEdge(
        strategy="fusion",
        verdict="negative",
        trade_count=1234,
        expectancy_pct=-0.08,
        expectancy_per_bar_pct=-0.01,
        excess_per_bar_pct=-0.05,
        stocks_with_trades=117,
        bars_median=250,
        computed_at=None,
        rule_version="v2",
    )
    present = _render_daily(_board(market_edge=edge))
    assert "买入否决闸" not in present
    assert "1234 笔 / 每笔 -0.08%" in present
    assert "只否决、不放行" in present  # 池化数字的边界必须跟着数字一起出现

    # 全角括号前不留空格。线上真发出去过一条「全市场 （2026-09-18 算）」——
    # 半角空格配全角括号在手机上就像打错了，而这条消息只会在手机上被读。
    dated = dataclasses.replace(edge, computed_at=datetime(2026, 9, 18, 18, 49))
    line = next(
        item for item in _render_daily(_board(market_edge=dated)).splitlines()
        if item.startswith("> 全市场（")
    )
    assert "全市场（2026-09-18 算）" in line
    assert "全市场 （" not in line


def test_a_skipped_candidate_is_never_rendered() -> None:
    """`skipped`（库内日线不足）不进日报正文，但它在候选总数里已被计入。

    日报是"今天能买什么"，不是"今天为什么有票没算"——后者是页面上的列表。
    """
    board = _board(
        _sector("电力"),
        skipped=(
            SkippedCandidate(
                ts_code="600900.SH",
                name="长江电力",
                industry="电力",
                bars=12,
                reason="库内只有 12 根",
            ),
        ),
        bars_median=0,
    )
    text = _render_daily(board)
    assert "长江电力" not in text
    assert "库内只有 12 根" not in text


# ======================================================================================
# 8：同步告警状态机（`scheduler._alert_*`）
#
# 状态只读 `SyncRun` 自己的历史，**不新增状态列**：`init_db()` 是 `create_all`，
# 给已存在的表加列会在这台有真实数据的线上库上运行时炸，而测试库每次新建所以全绿。
# ======================================================================================
def _record_run(
    factory, job_type: str, status: str, *, error_class: str = "", message: str = ""
) -> SyncRun:
    """写一条同步记录，**提交后**再返回——状态机读的是另一个会话，不提交它看不见。"""
    with factory() as session:
        row = SyncRun(job_type=job_type, status=status, error_class=error_class, message=message)
        session.add(row)
        session.commit()
        return row


def _alert(factory, row: SyncRun) -> None:
    with factory() as session:
        scheduler_mod._alert_sync_result(session, row)


def _events(factory) -> list[NotifyEvent]:
    with factory() as session:
        return list(session.scalars(select(NotifyEvent).order_by(NotifyEvent.id)).all())


def test_a_noisy_job_never_alerts(scheduler_db, webhook) -> None:
    """`quotes`/`news` 每 30 秒一次：不在白名单内。

    放它们进来就是"provider 一抖动 → 告警/恢复每 30 秒一轮"，直接撞 45009，
    而被丢掉的那条恰好是「已恢复」。
    """
    _alert(scheduler_db, _record_run(scheduler_db, "quotes", "failed", error_class="network"))

    assert _events(scheduler_db) == []
    assert webhook.calls == []


def test_the_same_job_only_alerts_once_a_day(scheduler_db, webhook) -> None:
    """一个 job 每天最多一条：一小时后重试再失败，不该再叫醒一次。"""
    _alert(
        scheduler_db,
        _record_run(scheduler_db, "fundamentals", "failed", error_class="network"),
    )
    _alert(
        scheduler_db,
        _record_run(scheduler_db, "fundamentals", "failed", error_class="network"),
    )

    events = _events(scheduler_db)
    assert [event.kind for event in events] == ["sync"]
    assert events[0].dedup_key == f"syncfail:{date.today().isoformat()}:fundamentals"
    assert len(webhook.calls) == 1


def test_a_benign_skip_is_not_a_failure(scheduler_db, webhook) -> None:
    """`status="failed"` 但 `error_class` 为空 = **跳过**（"已有一次在跑"），不是故障。

    池化的 `_finish_run` 在那一支传 `run_id=None` 直接返回、**根本写行**，但手工触发那条
    路径会写出这种行。跳过不需要人处理，告警了就是噪音。
    """
    _alert(scheduler_db, _record_run(scheduler_db, "pool", "failed"))

    assert _events(scheduler_db) == []


def test_a_failure_then_a_success_produces_exactly_one_recovery(scheduler_db, webhook) -> None:
    """恢复通知只看"上一次是失败"，所以第三次成功不再发——否则每天第一个定时任务
    都会报一次「已恢复」。"""
    _alert(scheduler_db, _record_run(scheduler_db, "market", "failed", error_class="network"))
    _alert(scheduler_db, _record_run(scheduler_db, "market", "success"))
    _alert(scheduler_db, _record_run(scheduler_db, "market", "success"))

    assert [event.kind for event in _events(scheduler_db)] == ["sync", "sync_ok"]


def test_a_first_ever_success_never_announces_a_recovery(scheduler_db, webhook) -> None:
    _alert(scheduler_db, _record_run(scheduler_db, "calendar", "success"))

    assert _events(scheduler_db) == []


def test_a_disabled_noisy_job_is_announced_with_the_upstream_words(scheduler_db, webhook) -> None:
    """被禁用的任务**不写 `SyncRun`**，状态机看不到它——所以这条单独存在。

    原因从那次 `error_class="permission"` 的失败里取：`disable_job` 只存一个到期时刻，
    不必为它加参数、更不必动 `SyncRun` 的表结构。
    """
    _record_run(
        scheduler_db,
        "quotes",
        "failed",
        error_class="permission",
        message="抱歉，您没有接口访问权限",
    )
    scheduler_mod._alert_disabled("quotes")
    scheduler_mod._alert_disabled("quotes")  # 每 30 秒的 tick 都会调，一天仍只有一条

    events = _events(scheduler_db)
    assert [event.kind for event in events] == ["sync_off"]
    assert "实时行情" in events[0].content  # 中文名，不是把 job_type 原样印出来
    assert "没有接口访问权限" in events[0].content
    assert len(webhook.calls) == 1


def test_a_disabled_daily_job_can_never_be_announced(scheduler_db, webhook) -> None:
    """`market` 之类撞不上禁用窗口（`sync._DISABLE_WINDOW` 是 12h，而它们的下一次运行
    在更久之后），给它们告警是死代码。两个集合恰好互补。"""
    scheduler_mod._alert_disabled("market")

    assert _events(scheduler_db) == []
    assert set(notify.SYNC_ALERT_JOBS) & set(notify.SYNC_DISABLED_JOBS) == set()
    assert set(notify.SYNC_ALERT_JOBS) | set(notify.SYNC_DISABLED_JOBS) == set(
        notify.JOB_LABELS
    )


def test_the_evening_job_is_registered_as_a_weekday_evening_cron(evening) -> None:
    """19:00 在 18:45 池化之后，是全交易日的收尾；周末没有新收盘数据，跑出来与周五那份
    逐字相同，只会白发一条。"""
    job = scheduler_mod.create_scheduler().get_job("evening-notify")

    assert job is not None
    assert f"hour='{evening[0].notify_daily_hour}'" in str(job.trigger)
    assert "day_of_week='mon-fri'" in str(job.trigger)
    assert job.max_instances == 1


# ======================================================================================
# 9：19:00 的任务端到端跑一次
#
# 榜单本身在 `test_strategy_simple.py` 里钉着，这里钉的是**接线**：幂等键取的是数据日期、
# 先日报后告警、配置项真的被传进了内核、以及它坏掉时绝不外溢。
# ======================================================================================
class _FakeStock:
    """`advise_for` 被替换掉时，`traded_stocks` 的返回值只是个带代码的载体。"""

    def __init__(self, ts_code: str) -> None:
        self.ts_code = ts_code


def _spy_board(monkeypatch, board: Board) -> list[int]:
    calls: list[int] = []

    def fake(db):
        calls.append(1)
        return board

    monkeypatch.setattr(board_service, "build_board", fake)
    return calls


def test_the_evening_job_keys_the_digest_by_the_data_date(
    scheduler_db, evening, monkeypatch
) -> None:
    """幂等键用**数据日期**而不是今天：同一天重跑算出的是同一份东西，不该发第二遍；
    而数据没更新时（收盘同步失败）键也不变——于是不会把昨天的名单当成今天的发出去。"""
    settings, stub = evening
    board = _board(_sector("电力", _plan()))
    _spy_board(monkeypatch, board)
    monkeypatch.setattr(position_service, "traded_stocks", lambda db: [])

    scheduler_mod._evening_notify()

    events = _events(scheduler_db)
    assert [event.kind for event in events] == ["daily"]
    assert events[0].dedup_key == "daily:2026-09-18"
    assert events[0].status == "sent"
    assert len(stub.calls) == 1
    # 逐字等于渲染器的输出 → 配置项（预算 / 本金）确实是从 Settings 传下去的
    assert events[0].content == notify.render_daily(
        board, [], max_bytes=settings.notify_max_bytes, equity=settings.notify_equity
    )


def test_the_evening_job_repeats_neither_message(scheduler_db, evening, monkeypatch) -> None:
    """同一天再跑一次（手工触发 + cron）：一条都不重发，因为键一样。"""
    _, stub = evening
    _spy_board(monkeypatch, _board())
    monkeypatch.setattr(position_service, "traded_stocks", lambda db: [])

    scheduler_mod._evening_notify()
    scheduler_mod._evening_notify()

    assert len(_events(scheduler_db)) == 1
    assert len(stub.calls) == 1


def test_the_evening_job_sends_the_stop_alert_after_the_digest(
    scheduler_db, evening, monkeypatch
) -> None:
    """急的在**后**发 = 最新的那条、最靠近输入框的那条。未触发的持仓不单独发。"""
    _, stub = evening
    _spy_board(monkeypatch, _board(_sector("电力", _plan())))
    monkeypatch.setattr(
        position_service,
        "traded_stocks",
        lambda db: [_FakeStock("600519.SH"), _FakeStock("600900.SH")],
    )
    monkeypatch.setattr(
        position_service,
        "advise_for",
        lambda db, stock, *, equity, days: (
            _triggered() if stock.ts_code == "600519.SH" else _position("600900.SH", "长江电力")
        ),
    )

    scheduler_mod._evening_notify()

    events = _events(scheduler_db)
    assert [event.kind for event in events] == ["daily", "stop"]
    assert [event.dedup_key for event in events] == ["daily:2026-09-18", "stop:2026-09-18"]
    assert "贵州茅台" in events[1].content
    assert "长江电力" not in events[1].content
    # 出网顺序与账本顺序一致：日报先到，告警后到
    assert stub.calls[-1][1]["markdown"]["content"] == events[1].content


def test_the_evening_job_passes_the_configured_equity_to_the_kernel(
    scheduler_db, evening, monkeypatch
) -> None:
    """`equity` 喂 `weight_pct` → 门控 `add` 分支。硬编码 1 000 000 会让同一根收盘、
    同一只票，页面说「加仓」而推送说「继续持有」。所以必须取自配置。"""
    settings, _ = evening
    seen: list[tuple[float, int]] = []
    _spy_board(monkeypatch, _board())
    monkeypatch.setattr(position_service, "traded_stocks", lambda db: [_FakeStock("600519.SH")])

    def spy(db, stock, *, equity: float, days: int):
        seen.append((equity, days))
        return _position(stock.ts_code, "贵州茅台")

    monkeypatch.setattr(position_service, "advise_for", spy)

    scheduler_mod._evening_notify()

    assert settings.notify_equity != 1_000_000.0  # 非默认值，否则这条测试证明不了什么
    assert seen == [(settings.notify_equity, board_service.PLAN_DAYS_DEFAULT)]


def test_a_broken_board_never_escapes_the_evening_job(scheduler_db, evening, monkeypatch) -> None:
    """推送是附加功能：它坏了绝不能让调度器线程挂掉，也绝不能发出半条消息。"""
    _, stub = evening

    def boom(db):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(board_service, "build_board", boom)

    scheduler_mod._evening_notify()  # 不抛

    assert _events(scheduler_db) == []
    assert stub.calls == []


def test_the_evening_job_computes_no_board_without_a_webhook(scheduler_db, monkeypatch) -> None:
    """没配就**连榜单都不算**（`build_board` 是十几秒的活）：为一页没人会收到的日报
    每晚烧一次 CPU 没有意义。"没配"由 `/settings` 与 `/system/status` 明说，
    账本里写不出一行——因为压根没走到 `push`。"""
    settings = Settings(_env_file=None)  # 空 webhook、NOTIFY_ENABLED 默认 true
    stub = _Stub()
    monkeypatch.setattr(notify, "_post_json", stub)
    monkeypatch.setattr(notify, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler_mod, "get_settings", lambda: settings)
    calls = _spy_board(monkeypatch, _board())

    scheduler_mod._evening_notify()

    assert settings.notify_configured is False
    assert calls == []
    assert _events(scheduler_db) == []
    assert stub.calls == []


# ======================================================================================
# 10：API —— 测试按钮 + 账本
#
# 限流器是**进程级**的（必须如此：它限制的就是"这个进程被刷"），所以每个用例都得换成
# 一个全新的，否则一个用例消耗掉的额度会让同一分钟里后面的用例拿到 429——
# 失败形态是"随机一个用例红"，跟被测代码毫无关系。
# ======================================================================================
@pytest.fixture()
def limiter(monkeypatch):
    """一个干净的限流器（与线上同参数：1 次/分钟）。"""

    def _install(calls_per_minute: int = 1) -> SlidingWindowRateLimiter:
        fresh = SlidingWindowRateLimiter(calls_per_minute=calls_per_minute, daily_budget=60)
        monkeypatch.setattr(notify_routes, "_TEST_LIMITER", fresh)
        return fresh

    return _install


def _configure(monkeypatch, **overrides) -> _Stub:
    """配好 webhook + 替换出网。返回 stub。

    `get_settings` 要在**三个**地方各钉一次：`notify`（发送前的门）、`notify_routes`
    （测试端点的 400）、`routes`（`/system/status` 的 `notify_configured`）。
    它们是三个独立的模块级名字绑定，只钉一处的话，另外两处会去读真实 `.env`——
    而 `get_settings()` 是 `lru_cache`，本机跑过别的测试后连 `.env` 都不再读了。
    """
    from app.api import routes as routes_mod

    stub = _Stub()
    kwargs = {"wecom_webhook_url": "https://qyapi.example/send?key=SECRET"}
    kwargs.update(overrides)
    settings = Settings(_env_file=None, **kwargs)
    monkeypatch.setattr(notify, "_post_json", stub)
    monkeypatch.setattr(notify, "get_settings", lambda: settings)
    monkeypatch.setattr(notify_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_mod, "get_settings", lambda: settings)
    return stub


def test_the_test_button_refuses_when_unconfigured(client, monkeypatch) -> None:
    """没配就明说去哪儿配，而且**不出网、不记账**。"""
    stub = _configure(monkeypatch, wecom_webhook_url="")

    response = client.post("/api/v1/notify/test")

    assert response.status_code == 400
    assert "WECOM_WEBHOOK_URL" in response.json()["detail"]
    assert stub.calls == []
    assert client.get("/api/v1/notify/events").json() == []


def test_the_test_button_sends_one_and_then_rate_limits(client, monkeypatch, limiter) -> None:
    """站点没有鉴权，限流就是这条端点唯一的护栏：1 次/分钟。"""
    stub = _configure(monkeypatch)
    limiter()

    first = client.post("/api/v1/notify/test")
    second = client.post("/api/v1/notify/test")

    assert first.status_code == 200
    assert first.json()["status"] == "sent"
    assert first.json()["status_label"] == "已送达"
    assert first.json()["reused"] is False
    assert second.status_code == 429
    assert len(stub.calls) == 1


def test_a_second_press_in_the_same_minute_never_pretends_to_have_sent(
    client, monkeypatch, limiter
) -> None:
    """换完 webhook 按一下，若被幂等挡住而界面显示上一行的「已送达」，
    看上去就像新密钥通了——其实一条都没发。所以键带分钟，并把 `reused` 明说出来。"""
    stub = _configure(monkeypatch)
    limiter(calls_per_minute=5)  # 放开限流，专门看幂等这一层

    first = client.post("/api/v1/notify/test").json()
    second = client.post("/api/v1/notify/test").json()

    assert second["reused"] is True
    assert "没有重发" in second["detail"]
    assert len(stub.calls) == 1  # 只有第一次真的出网
    assert second["id"] == first["id"]
    assert len(client.get("/api/v1/notify/events").json()) == 1


def test_a_nonzero_errcode_reaches_the_page_as_a_failure(client, monkeypatch, limiter) -> None:
    """HTTP 200 + errcode 93000（机器人被移出群）必须显示成失败，不能显示成已送达。"""
    stub = _configure(monkeypatch)
    stub.body = {"errcode": 93000, "errmsg": "invalid webhook url"}
    limiter()

    body = client.post("/api/v1/notify/test").json()

    assert body["status"] == "failed"
    assert body["status_label"] == "失败"
    assert body["errcode"] == 93000
    assert body["http_status"] == 200  # 传输层是成功的，失败在业务层
    assert "invalid webhook url" in body["detail"]


def test_the_ledger_is_newest_first_and_carries_the_original_text(client, db_session) -> None:
    """账本倒序、带原文。`pending` 显示成「未送达（进程中断）」——它是插了行但进程在
    发送前断了，**没有送达**，读成已送达就会让人以为消息发出去了。"""
    for index, status in enumerate(["sent", "failed", "pending"], start=1):
        db_session.add(
            NotifyEvent(
                kind="daily",
                dedup_key=f"daily:2026-09-1{index}",
                status=status,
                byte_len=10 * index,
                summary=f"第 {index} 条",
                content=f"# 第 {index} 条\n正文",
            )
        )
    db_session.commit()

    rows = client.get("/api/v1/notify/events?limit=2").json()

    assert [row["summary"] for row in rows] == ["第 3 条", "第 2 条"]
    assert rows[0]["status_label"] == "未送达（进程中断）"
    assert rows[0]["content"] == "# 第 3 条\n正文"


def test_the_status_endpoint_says_configured_without_ever_echoing_the_url(
    client, monkeypatch
) -> None:
    """webhook 是凭据：拿到它的人能往那个群里发消息。页面只能说「配没配」。

    断言整段响应文本而不是某个字段——凭据泄漏可以发生在任何一个字段里。
    """
    _configure(monkeypatch)

    response = client.get("/api/v1/system/status")

    assert response.json()["notify_configured"] is True
    assert "SECRET" not in response.text


def test_the_status_endpoint_says_unconfigured_on_a_bare_settings(client, monkeypatch) -> None:
    _configure(monkeypatch, wecom_webhook_url="")

    assert client.get("/api/v1/system/status").json()["notify_configured"] is False
