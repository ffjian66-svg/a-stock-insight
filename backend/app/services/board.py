"""「明日操作」榜单的计算。从 `api/strategy_routes.py` 搬下来，为的是让**微信推送与网页
共用同一次计算**。

## 为什么必须共用

`schemas.SimplePlanRow` 与 `api.strategy_routes._simple_row` 都写死了同一条纪律：
「`action`/`action_label` 是 `Plan` 的同一份输出，**逐字照抄，不在这里重算**」——任何
"从 verdict 推 action"的简化都会静默删掉四条信号条件里的几项，让负期望重新变成买入。
推送如果自己再算一遍，两条路径迟早漂移成「推送说买入、网页说观望」。那是本项目唯一
不可接受的失败，所以两者共用 `build_board`。

## 为什么是裸 dataclass

`services/picks.py` 有一条成文禁令：**服务层不 import `app.api.schemas`**（同款理由见
`services/timing.py`——它返回 dict，由路由层包成 `TimingAdvice`）。所以这里交裸
dataclass，由路由层组装响应模型，响应契约因此没有被搬动。

同理这里**刻意不 import `api.routes.stock_view`**：本模块只需要候选行的身份三字段，而
`PickRow` 的第 0 项就是 `Stock`，三字段都在上面；视图映射（含实时报价兜底）是 API 层的事。
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.db.models import StrategyPoolStats
from app.services import picks
from app.services.history import history_window, stored_bar_rows
from app.services.quant import backtest as backtest_engine
from app.services.quant import decision
from app.services.quant.indicators import MIN_BARS
from app.services.scoring import RULE_VERSION

logger = logging.getLogger(__name__)

# 单标的端点的默认窗口。抽成常量是因为「明日操作」要拿它当 `window_days` 传给 build_plan，
# 两个屏各写一个 500 的话，`verdict_text` 里的「最近 N 个交易日」会无理由地对不上。
PLAN_DAYS_DEFAULT = 500

# 「明日操作」固定用融合策略，且端点**不接受 query 参数**：没有 strategy 参数，就不可能有人
# 传 `buy_hold` 拿到一整页「买入」——它每只股票恰好成交 1 笔，结构性「样本不足」，而且
# `market_edge.is_baseline` 让全市场否决对它失效。fusion 同时是 /strategy/plan 的默认值，
# 所以两页的数字可比。
SIMPLE_STRATEGY = "fusion"


@dataclass(frozen=True)
class SkippedCandidate:
    """因库内日线不足而没进表的候选。`reason` 是要上屏/上推送的那句话本身。"""

    ts_code: str
    name: str
    industry: str
    bars: int
    reason: str


@dataclass(frozen=True)
class BoardSector:
    """一个板块的候选行。三个数字必须能分开读：候选 → 入选 → 实际列出。

    `selected` 是截断后**入选**的只数，包含随后因库内日线不足被跳过的那些，所以
    `selected >= len(plans)`。两个差值说的是两件事：`candidates - selected` 是「我们按市值
    截断了」，`selected - len(plans)` 是「有候选因库内日线不足没进表」。混成一句就是把
    "数据没拉到"说成"我们挑过了"。
    """

    industry: str
    candidates: int  # 通过门槛与时机两步的候选数，截断**之前**的事实
    selected: int
    plans: tuple[decision.Plan, ...]


@dataclass(frozen=True)
class Board:
    """`/strategy/simple` 的全部输入。

    `market_edge` **只进这里、不进响应模型**（`SimplePlanBoard` 没有这个字段，加进去就是
    改契约）：它给推送用——`market_edge is None` 时买入否决闸根本没跑，此时"0 个买入
    候选"与"规则跑了没找到"是两件事，推送必须区分。见 `market_edge` 的 docstring。
    """

    basis_date: Optional[date]
    strategy: str
    strategy_label: str
    window_days: int
    bars_median: int
    note: str
    caveat: str
    market_edge: Optional[decision.MarketEdge]
    sectors: tuple[BoardSector, ...]
    skipped: tuple[SkippedCandidate, ...]


def market_edge(db: Session, strategy: str) -> Optional[decision.MarketEdge]:
    """读该规则的**全市场**汇总，供买入结论的否决闸使用。

    没算过（表为空）时返回 `None` —— 不否决，行为与加本功能之前逐位一致：一个还没
    算过汇总的库，不该因为"缺证据"而改变结论。

    `rule_version` 对不上时也返回 `None`：那条数字是上一个内核版本算的，拿它否决买入
    比不否决更糟。注意这是**粗粒度**的守卫（本仓只有"整版升级"这一个版本标记，
    挡不住"顺手调了一个 signals 阈值"），而它失效的方向恰好是"不否决"——保守的那一侧。
    """
    record = db.get(StrategyPoolStats, strategy)
    if record is None or record.computed_at is None:
        return None
    if record.rule_version != RULE_VERSION:
        logger.warning(
            "全市场汇总的 rule_version=%s 与当前 %s 不符，本次不做否决",
            record.rule_version,
            RULE_VERSION,
        )
        return None
    return decision.MarketEdge(
        strategy=record.strategy,
        verdict=record.verdict,
        trade_count=record.trade_count,
        expectancy_pct=record.expectancy_pct,
        expectancy_per_bar_pct=record.expectancy_per_bar_pct,
        excess_per_bar_pct=record.excess_per_bar_pct,
        stocks_with_trades=record.stocks_with_trades,
        bars_median=record.bars_median,
        computed_at=record.computed_at,
        rule_version=record.rule_version,
        is_baseline=record.strategy == backtest_engine.BASELINE_STRATEGY,
    )


def run_expectancy(
    rows: list[dict[str, Any]], strategy: str, days: int, *, params: Any = None
) -> tuple[backtest_engine.BacktestResult, backtest_engine.Expectancy]:
    """跑一次回测并折出实测期望。

    `benchmark_return` 取同期 `buy_hold` 的累计收益——没有基准，页面会反向撒谎：
    一只跌 15% 而股票跌 30% 的策略其实在创造价值。
    """
    live_params = params or backtest_engine.LIVE_PARAMS
    result = backtest_engine.backtest(rows, strategy, live_params)
    baseline = backtest_engine.backtest(rows, backtest_engine.BASELINE_STRATEGY, live_params)
    expectancy = backtest_engine.trade_expectancy(
        result,
        strategy=strategy,
        window_days=days,
        benchmark_return=backtest_engine._metric(baseline.metrics, "cumulative_return"),
    )
    return result, expectancy


def windowed_stored_rows(db: Session, code: str, days: int) -> list[dict[str, Any]]:
    """`stored_bar_rows` 再裁到**与 `/plan/{ts_code}` 完全相同的那两年窗口**。

    两个取数口径差在窗口起点，差出来的不是小数：`stored_bar_rows` 取"库内最近 `days` 根"，
    而单股页走 `ensure_bar_history`，它取的是"两年窗口内的根数"。库内只要有几根早于窗口
    起点的日线（2026-09-17 线上实测：603323.SH/600908.SH/000906.SZ/601107.SH 各有 3 根
    2024-09-11~13），同一个函数就会在 489 根与 486 根上各跑一遍，于是同一只股票在两个屏
    上印出「实测 8 笔 +1.61%」与「实测 7 笔 +2.10%」——四个价位一模一样，数字却对不上，
    而用户看不出来是 3 根 bar 造成的。

    裁到同一个窗口，同库内数据下两条端点逐位相同（`test_the_board_matches_...` 就是钉这个，
    它种的日线跨过窗口起点）。**只减不加**：窗口外的陈旧日线本来就不该参与"最近两年"的
    实测，单股页也是这么裁的。
    """
    window_start, _ = history_window()
    return [row for row in stored_bar_rows(db, code, days) if row["trade_date"] >= window_start]


def build_board(db: Session) -> Board:
    """全市场候体现算计划，按行业分板块。

    成本（2026-09-17 线上实测 250 行星 ≈ 14s，其中门槛 SQL 0.15s、全池现算时机 4.1s、
    250 次计划 9.98s）：每一行都要跑两次回测（融合策略 + 买入持有）来喂 `honesty`，
    所以这个成本不是"没优化"，是诚实契约的一部分。不做缓存。

    板块内的排序键是 `FundamentalSnapshot.total_mv`（最新一期快照），**由
    `picks.select_board_picks` 决定**。它只用来排序：不进 `entry_conditions`，不影响
    `action`/`action_label`/`honesty`/四个价位里的任何一个字节。让市值参与放行 = 与
    「样本外数字只能否决、不能放行」的诚实政策冲突。

    ## 与 `/plan/{ts_code}` 的一处刻意差异（不是漏掉）

    本函数 `days=PLAN_DAYS_DEFAULT` 但 **`backfill=False`**，单股页 `backfill=True`。库内
    根数少 → 笔数少 → 更容易 `insufficient` → 更容易落到「观望」。方向上是**更保守**，但
    不是「逐位一致」——所以 `bars_median`、行级 `bars`、`note` 三处都把这层差异说出来，
    调用方也绝不写「与单股页一致」。

    价格锚点则**与单股页完全一致**：同样不传 `price=`，四个数字都基于库内最后一根收盘。
    见下面 `build_plan` 调用处的注释——候选行上有实时报价，但把实时价喂进去会让入场区/
    止损/移动止盈位随 tick 漂移，而同一行的实测期望是按收盘序列回测出来的。
    """
    board = picks.select_board_picks(db)
    edge = market_edge(db, SIMPLE_STRATEGY)

    sectors: list[BoardSector] = []
    skipped: list[SkippedCandidate] = []
    shown: list[decision.Plan] = []
    strategy_label = ""
    caveat = ""
    for sector in board:
        plans: list[decision.Plan] = []
        for row, _advice in sector.rows:
            # 只用候选行的身份三字段（`PickRow` 第 0 项就是 `Stock`）。**刻意不走
            # `stock_view`**：那会把实时报价一起带进来，而本页四个价位全部锚定在库内最后
            # 一根收盘上——两个价格基准混在一行里，页面还会与单股页对不上且无从解释。
            stock = row[0]
            bars = windowed_stored_rows(db, stock.ts_code, PLAN_DAYS_DEFAULT)
            if len(bars) < MIN_BARS:
                skipped.append(
                    SkippedCandidate(
                        ts_code=stock.ts_code,
                        name=stock.name,
                        industry=stock.industry,
                        bars=len(bars),
                        reason=(
                            f"库内只有 {len(bars)} 根日线（不足 {MIN_BARS} 根），"
                            "MA20 与 ATR14 无定义"
                        ),
                    )
                )
                continue
            _, expectancy = run_expectancy(bars, SIMPLE_STRATEGY, PLAN_DAYS_DEFAULT)
            plan = decision.build_plan(
                bars,
                ts_code=stock.ts_code,
                name=stock.name,
                industry=stock.industry,
                strategy=SIMPLE_STRATEGY,
                expectancy=expectancy,
                market_edge=edge,
            )
            # 结论逐字照抄，绝不在这里重算（见 `_simple_row`）。
            strategy_label = plan.strategy_label
            caveat = plan.honesty.caveat
            plans.append(plan)
        sectors.append(
            BoardSector(
                industry=sector.industry,
                candidates=sector.candidates,
                # 截断后**入选**的只数，包含随后因库内日线不足被跳过的那些，所以它 ≥
                # len(plans)。两个差值说的是两件事，见 `BoardSector` 的 docstring。
                selected=len(sector.rows),
                plans=tuple(plans),
            )
        )
        shown.extend(plans)

    return Board(
        basis_date=picks.picks_basis_date(db),
        strategy=SIMPLE_STRATEGY,
        strategy_label=strategy_label,
        window_days=PLAN_DAYS_DEFAULT,
        # 中位数取**所有板块**的行：这是「库内样本有多薄」的披露，不是某个板块的统计量。
        bars_median=int(statistics.median([plan.bars for plan in shown])) if shown else 0,
        note=(
            f"{picks.board_note()}本页只用库内日线，绝不向上游回补；"
            f"实测样本因此可能比单股页少，结论只会更保守。"
        ),
        caveat=caveat,
        market_edge=edge,
        sectors=tuple(sectors),
        skipped=tuple(skipped),
    )
