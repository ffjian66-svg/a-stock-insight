"""次日买点候选的选股口径——`/picks/daily` 与 `/strategy/simple` 共用这一份。

这个模块存在的唯一理由：**三步选股只能有一份实现**。它原先长在 `app/api/routes.py` 的
`picks_daily` 里；「明日操作」页要用同一个候选集，于是把它抽出来。留两份副本的后果是
「明天买什么」在两个屏上给出不同答案，而那种分叉不会有任何测试自然地发现。

**两个页面共用的是前三步（= 候选池），不是最终名单。** 第 3 步的参数各页不同：`/picks/daily`
低风险优先、单行业 2 只、最多 8 只；`/strategy/simple` 市值优先、每行业 10 只、不封顶。所以
「哪些标的今天有资格」两页给同一个答案，而「各页列多少、按什么排」不同——**榜单页不是短名单
的超集**：8 只里可能有不在这 250 行里的（两者的取舍标准不同），这是设计，不是漏掉。

三步，顺序即优先级，不许重排：

1. SQL 预筛——综合评分 ≥ `PICKS_MIN_TOTAL`、四维权重覆盖 ≥ `PICKS_MIN_COVERAGE`，按分数降序
   取前 `pool_limit` 只（`None` = 不设上限）。`pool_limit` 只是「参与现算时机的候选池上限」，
   用来控制单次 DB 取数与耗时，**不是**筛选门槛。
2. 现算操作时机（`services.timing`），只留 `tone == "buy"` 的。**两个页面都走这一步**：少了
   它，两页的候选池就不再是同一个（2026-09-17 线上实测 843 只 → 272 只），分享的「一份实现」
   也就名存实亡。
3. 按 `sort_key` 稳定排序后贪心走一遍，每行业至多 `per_industry` 只，封顶 `target` 只
   （`None` = 不封顶）。两个页面只差这一步的参数。

**阈值是模块常量，每个请求只读一次。对候选阈值做参数搜索（遍历若干候选门槛挑一个「更好」的）
属于契约外**——本仓明确拒绝拟合出来的参数，见 `quant/decision.py` 的模块 docstring 与
`quant/backtest.POOL_CAVEAT`。

服务层不 import `app.api.schemas`（`services/timing.py` 的 `compute_timing_for_codes` 也是返回
dict、由路由层包成 `TimingAdvice`），所以这里返回裸行 + timing dict，**不**返回 `StockView`。
`stock_view` 留在 API 层（视图映射是 API 层的事）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyBar,
    FundamentalSnapshot,
    LatestQuote,
    ScoreSnapshot,
    Stock,
)
from app.services.timing import compute_timing_for_codes

# —— 次日买点候选口径（/picks/daily）：低风险优先、按行业分散 ——
PICKS_POOL = 100  # 参与现算时机的候选池上限（高分优先，控制单次 DB 取数与耗时）
PICKS_MIN_TOTAL = 65.0  # 综合评分门槛
# 数据覆盖门槛。四组权重是 趋势动量 0.40 / 质量估值 0.30 / 新闻情绪 0.20 / 波动风险 0.10，
# 所以 0.8 等价于「趋势动量与质量估值两维必须有分，另两维至少有一维」——即"除新闻外都有"。
# **不能写成 0.9**：0.9 等于强制要求新闻情绪分，而新闻只认近 3 天，全市场常年在 0.8
# （2026-09-16 实测 5505/5564 只），有新闻分的只有 2 只 —— 0.9 会让这张清单结构性地
# 永远为空，而空清单看起来像"今天没有好标的"，不是"门槛写错了"。
PICKS_MIN_COVERAGE = 0.8
PICKS_TARGET = 8  # 目标只数
PICKS_PER_INDUSTRY = 2  # 单行业最多只数

# —— 榜单页（/strategy/simple）的口径：按行业分组、每行业取市值最大的 10 只、不设总数上限 ——
# 与 /picks/daily **共用前三步**，只差第三步的排序键与截断。两个常量分开命名（而不是把
# PICKS_* 改成参数、两页共用一份）是为了让两页的口径各自可读、可测，也让「顺手改一个数」
# 改不动另一页。
BOARD_PER_INDUSTRY = 10  # 单行业最多只数
# 参与现算时机的候选池上限。None = 不设上限（榜单页列出全部）；取 None 而不是一个很大的数，
# 是为了让「不截断」这件事在类型上就说得出来。
BOARD_POOL_LIMIT: Optional[int] = None
BOARD_TARGET: Optional[int] = None  # 总数上限；None = 不封顶

_RISK_RANK = {"低": 0, "中": 1, "高": 2}

# 这里用 `Optional[...]` 而不是本仓别处的 `X | None`：类型别名在**运行期**求值，而 mypy 的
# python_version 是 3.9（见 pyproject.toml），`|` 在那条路径上不是合法类型表达式。函数签名里
# 的注解不受影响（`from __future__ import annotations` 让它们不求值），照旧写 `X | None`。
PickRow = tuple[
    Stock,
    Optional[LatestQuote],
    Optional[ScoreSnapshot],
    Optional[FundamentalSnapshot],
    Optional[DailyBar],
]


def stock_query() -> Select[PickRow]:
    """stocks 外接最新报价 / 评分 / 基本面 / 全局最近一根日线。

    搬到这里而不是留在 `app/api/routes.py`，是因为它只依赖 `db.models`、不依赖 API 层，
    而 `select_daily_picks` 需要它。搬的时候一行都没删——尤其那个 `DailyBar` 等值 outerjoin：
    它只为一件事存在（`latest_quotes` 为空时兜底一根收盘价，见 `app/api/routes.stock_view`
    的 docstring），去掉它每个列表的价格列会整片 `--`。
    """
    latest_fundamental = (
        select(
            FundamentalSnapshot.ts_code, func.max(FundamentalSnapshot.trade_date).label("max_date")
        )
        .group_by(FundamentalSnapshot.ts_code)
        .subquery()
    )
    # 全局最近交易日，用于给 latest_quotes 缺失的标的兜底一根收盘价（见 stock_view）。
    # 刻意按**全局**最近交易日做等值 join，而不是每只取自己的 max(trade_date)：
    # 后者要 group by 全表（实测 266ms），而这里走 trade_date 索引只要 15ms。
    # 代价是停牌股取不到（实测 14 只）→ price_source="none"、价格仍为 --，
    # 这恰好是对的：停牌股本来就没有"当前价"。
    latest_bar_date = select(func.max(DailyBar.trade_date)).scalar_subquery()
    return (
        select(Stock, LatestQuote, ScoreSnapshot, FundamentalSnapshot, DailyBar)
        .outerjoin(LatestQuote, LatestQuote.ts_code == Stock.ts_code)
        .outerjoin(ScoreSnapshot, ScoreSnapshot.ts_code == Stock.ts_code)
        .outerjoin(latest_fundamental, latest_fundamental.c.ts_code == Stock.ts_code)
        .outerjoin(
            FundamentalSnapshot,
            (FundamentalSnapshot.ts_code == Stock.ts_code)
            & (FundamentalSnapshot.trade_date == latest_fundamental.c.max_date),
        )
        .outerjoin(
            DailyBar,
            (DailyBar.ts_code == Stock.ts_code) & (DailyBar.trade_date == latest_bar_date),
        )
    )


def picks_basis_date(db: Session) -> date | None:
    """最近一根日线的交易日。盘中/盘后都是「基于哪个收盘」的那个日期。"""
    return db.scalar(select(func.max(DailyBar.trade_date)))


def picks_note() -> str:
    """候选口径文案。与阈值同源——改门槛时文案跟着走，不会忘。"""
    return (
        f"综合评分≥{PICKS_MIN_TOTAL:.0f}、趋势与动量及质量与估值均有分"
        f"（四维权重覆盖≥{PICKS_MIN_COVERAGE:.0%}，新闻情绪不强制）的"
        f'"可分批买入"标的中，低风险优先、单行业至多 {PICKS_PER_INDUSTRY} 只、'
        f"最多 {PICKS_TARGET} 只；以最近收盘日为基准，供下一交易日参考。"
    )


def board_note() -> str:
    """榜单页（`/strategy/simple`）的候选口径文案。与阈值、与截断口径同源。

    **不能复用 `picks_note()`**：那段话写着「低风险优先、单行业至多 2 只、最多 8 只」，
    而这一页是每行业 10 只、不封顶——印上去就是一句用户看一眼页面就能推翻的假话。

    最后一句的「候选内」是这页最容易读错的地方，所以必须写在文案里、而不是只留在前端：
    `total_mv` 排序只在**已过门槛**的候选里进行，真·板块市值龙头多数根本没进候选池
    （2026-09-17 线上实测 70 个板块里有 55 个如此：电气设备的宁德时代 14081 亿 vs 候选里的
    中船科技 133 亿）。让真龙头「因为市值大」而进表 = 让市值**放行**，与诚实政策
    （样本外数字只能否决、不能放行）直接冲突。所以本页只做「候选内」排序，并把这层边界说出来。
    """
    return (
        f"综合评分≥{PICKS_MIN_TOTAL:.0f}、趋势与动量及质量与估值均有分"
        f"（四维权重覆盖≥{PICKS_MIN_COVERAGE:.0%}，新闻情绪不强制）的"
        f'"可分批买入"标的中，按行业分板块，每板块最多列出总市值最大的 {BOARD_PER_INDUSTRY} 只，'
        f"不设总数上限；市值只决定板块内的排列顺序，不参与任何买卖判断，缺失市值的排在板块末位。"
        f"以最近收盘日为基准，供下一交易日参考。"
        f"「龙头」指通过上述门槛的候选里市值最大的那只，**不是该板块市值最大的股票**——"
        f"板块里市值更大但未过门槛的股票不在本名单内。"
    )


def _risk_rank_key(row: PickRow) -> tuple[int, float]:
    """低风险优先，同风险档内高分优先。`risk_level` 缺失时按「高」处理（既有口径，别改）。"""
    score = row[2]
    risk = _RISK_RANK.get((score.risk_level if score else None) or "高", 3)
    total = score.total_score if score and score.total_score is not None else 0.0
    return risk, -total


def _market_cap_key(row: PickRow) -> tuple[bool, float, str]:
    """市值降序（榜单页的「龙头优先」）：最新总市值大的排前面。

    **缺失市值的排最后**，同市值按代码定序。理由：`total_mv` 为 `None` 时我们没有依据说它
    "最大"，排在所有已知市值之后，「本行业市值最大的排前面」这句话才对每一个真有市值的行
    都成立。**但不丢掉这些行**——丢掉等于静默缩小候选集（响应里的 `candidates` 会跟着变小），
    正是本模块 docstring 要防的那类改动；它们照常参与每行业上限的计数。

    第三个键是 `ts_code`：同市值时需要一个**不依赖 `total_score`** 的确定性顺序。否则"同市值"
    的先后会退回 SQL 的 `total_score desc`，评分就悄悄成了这一页的第二排序键，而这一页的排序
    只该由市值决定。

    `total_mv` 取自库内**最近一期**基本面快照（见 `stock_query` 的 `latest_fundamental`），
    各只的快照日期可能不同，排序因此混合了不同时点。它在本页只用来排序、**不上屏显示数值**，
    所以这是已知的近似，不是错误；一旦要把数字印出来，就必须连单位和快照日期一起印。

    **市值只进排序键。** 它不进 `entry_conditions`（`decision._decide_action` 的 docstring
    明确警告过：往入场条件里加全市场性质的条件会被前端渲染成一个绿色的 ✓，等于变相放行），
    也不参与 `entry_zone`/`stop_loss`/`take_profit`/`honesty` 里的任何一个数字。
    """
    fundamental = row[3]
    value = fundamental.total_mv if fundamental is not None else None
    return (value is None, -(value or 0.0), row[0].ts_code)


def _gate_rows(db: Session, *, pool_limit: Optional[int]) -> list[PickRow]:
    """第 1 步：SQL 预筛（见模块 docstring）。`pool_limit=None` = 不设上限。

    `pool_limit` **必须是关键字参数**：`_gate_rows(db, None)` 读不出「不设上限」这层意思。

    排序（`total_score desc, ts_code`）在这里定死：下游的排序都是**稳定**的，同键的行保留的
    就是这个顺序，两个页面因此逐位可复现。
    """
    query = (
        stock_query()
        .where(
            ScoreSnapshot.total_score >= PICKS_MIN_TOTAL,
            ScoreSnapshot.coverage >= PICKS_MIN_COVERAGE,
        )
        .order_by(ScoreSnapshot.total_score.desc(), Stock.ts_code)
    )
    # 显式分支，而不是把 `None` 直接交给 `.limit()`：后者虽然合法（SQLAlchemy 2.0 把
    # `limit(None)` 当无限制），但读的人得先知道这件事才能确认这里没写错。一个分支更便宜。
    if pool_limit is not None:
        query = query.limit(pool_limit)
    return list(db.execute(query).all())


def _buy_rows(db: Session, rows: list[PickRow]) -> list[tuple[PickRow, dict[str, Any]]]:
    """第 2 步：现算操作时机，只留 `tone == "buy"` 的，返回 `(行, timing 建议)` 对。

    `compute_timing_for_codes` **恰好调用一次**——它是批量取数，逐个调会把 DB 往返放大成
    候选池大小倍。空池直接返回，连那一次调用都不发生（调用次数有测试钉着）。

    返回的 timing 建议是复现候选集的副产品：`select_daily_picks` 把它放上屏（`/picks/daily`
    的 `TimingAdvice`），`select_board_picks` 丢掉它（榜单页的买入理由来自计划
    `action_label`/`honesty`，不是 timing）——但**这一步照走**，见模块 docstring 第 2 步。
    """
    if not rows:
        return []
    timings = compute_timing_for_codes(db, [row[0].ts_code for row in rows])
    kept: list[tuple[PickRow, dict[str, Any]]] = []
    for row in rows:
        advice = timings.get(row[0].ts_code)
        if advice is not None and advice["tone"] == "buy":
            kept.append((row, advice))
    return kept


def _diversify(
    kept: list[tuple[PickRow, dict[str, Any]]],
    *,
    per_industry: int,
    target: Optional[int],
    sort_key: Callable[[PickRow], tuple[Any, ...]],
) -> list[tuple[PickRow, dict[str, Any]]]:
    """第 3 步：按 `sort_key` **稳定**排序后贪心走一遍，每行业至多 `per_industry` 只，
    封顶 `target` 只（`None` = 不封顶）。

    一次贪心 == 「每个行业各自取前 `per_industry` 名」：全局序里同一行业的成员一定按同一个
    顺序出现（全局序是该行业上那个序的扩展），所以被取到的正是各行各业自己的前若干名——
    这条等价性是榜单页「板块内 = 市值前 10、按市值降序」的全部依据（2026-09-17 线上实测
    250 行 / 70 行业，与「先分组再各组取前 10」集合逐只相同）。比"先分组再各组取前 N"少一次
    分组，也不会在组间顺序上产生分歧。

    但**贪心的原始输出顺序不是展示顺序**：它是全局序（市值序）在各行业上的交错，不是行业
    连续的。展示顺序由调用方（`select_board_picks`）重新聚拢后决定。

    两种退出必须分开：超配额是 `continue`（继续找别的行业），达标是 `return`（整表截断）。
    `/picks/daily` 就是这么走的——把 `continue` 写成 `break` 会静默缩短清单，而「行数 ≤ 8」
    这类断言照样通过。

    `sorted()` 而不是就地 `list.sort()`：同样的输出，但不会改调用方传进来的列表。
    """
    ordered = sorted(kept, key=lambda pair: sort_key(pair[0]))  # 稳定排序，同键保留 SQL 序
    counts: dict[str, int] = {}
    picks: list[tuple[PickRow, dict[str, Any]]] = []
    for pair in ordered:
        if target is not None and len(picks) >= target:
            return picks
        industry = pair[0][0].industry
        count = counts.get(industry, 0)
        if count >= per_industry:
            continue
        counts[industry] = count + 1
        picks.append(pair)
    return picks


@dataclass(frozen=True)
class SectorPicks:
    """一个行业块（服务层口径，不是响应模型）。

    `rows` 是**已按市值截断入选、但尚未按库内日线过滤**的候选：响应里的 `selected` 就是
    `len(rows)`，而响应里真正列出的行可能更少——不足 `MIN_BARS` 的那些进 `skipped`。
    两个差值（`candidates - len(rows)` 与 `len(rows) - 响应行数`）说的是两件不同的事：
    前者是「我们按市值截断了」，后者是「有候选因库内日线不足没进表」。混成一句就是把
    "数据没拉到"说成"我们挑过了"。见 `schemas.SimpleSector`。
    """

    industry: str
    candidates: int  # 通过第 1、2 步的候选数，**截断之前**的事实
    rows: list[tuple[PickRow, dict[str, Any]]]


def select_daily_picks(db: Session) -> list[tuple[PickRow, dict[str, Any]]]:
    """三步选股（`/picks/daily`）：低风险优先、同风险档内高分优先，单行业至多
    `PICKS_PER_INDUSTRY` 只、最多 `PICKS_TARGET` 只。口径见模块 docstring。

    返回 `(行, timing 建议)` 按最终顺序——建议是本页要上屏的 `TimingAdvice`。
    """
    return _diversify(
        _buy_rows(db, _gate_rows(db, pool_limit=PICKS_POOL)),
        per_industry=PICKS_PER_INDUSTRY,
        target=PICKS_TARGET,
        sort_key=_risk_rank_key,
    )


def select_board_picks(db: Session) -> list[SectorPicks]:
    """三步选股（`/strategy/simple`）：**同一个候选池**，第 3 步换成「按行业分组、每行业取
    市值最大的 `BOARD_PER_INDUSTRY` 只、不封顶」。

    与 `select_daily_picks` 的差异只在第 3 步的排序键与截断——两页对「今天哪些标的有资格」
    给的是同一个答案（同一个 `_gate_rows`/`_buy_rows`），不同的只是各页展示多少、按什么排。
    所以**榜单页不是 `/picks/daily` 那 8 只的超集**，两者按各自的标准取舍。

    「不封顶」不是「无界」：上界是 行业数 × `BOARD_PER_INDUSTRY`（2026-09-17 线上实测
    70 个行业、250 行）。

    返回的块**已按展示顺序排好**：行数多的行业在前，并列按行业名。板块内的行是按市值降序的
    ——这里必须用 `dict.setdefault(...).append(...)` 这种保序聚拢，换成 `sorted(... groupby)`
    会把市值降序悄悄打乱，而页面上「市值最大的排前面」这句话不会有任何测试自然变红。
    """
    kept = _buy_rows(db, _gate_rows(db, pool_limit=BOARD_POOL_LIMIT))

    # 截断**之前**的候选数：与截断无关，所以在这里数，不让 _diversify 同时管两件事。
    candidates: dict[str, int] = {}
    for row, _advice in kept:
        industry = row[0].industry
        candidates[industry] = candidates.get(industry, 0) + 1

    selected = _diversify(
        kept,
        per_industry=BOARD_PER_INDUSTRY,
        target=BOARD_TARGET,
        sort_key=_market_cap_key,
    )

    grouped: dict[str, list[tuple[PickRow, dict[str, Any]]]] = {}
    for pair in selected:
        grouped.setdefault(pair[0][0].industry, []).append(pair)

    # 直接下标取 candidates：两边的行业键集合由同一个 kept 产生，缺键是 bug，不是可降级的情形。
    return sorted(
        (
            SectorPicks(industry=industry, candidates=candidates[industry], rows=rows)
            for industry, rows in grouped.items()
        ),
        key=lambda sector: (-len(sector.rows), sector.industry),
    )
