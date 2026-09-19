"""买卖策略：单股买卖计划、策略实测对比、持仓流水与调整建议。

## 单用户、无鉴权

完全沿用 `WatchlistItem` 的既有姿态：没有 `user_id`、没有归属校验、没有登录。本应用跑在
localhost。**不要**在这里加半个鉴权层——半层鉴权比没有鉴权更糟（会让人以为数据被保护了），
要加就整层加。

## 数据源纪律（保护 TuShare 配额）

`/plan` 与 `/backtest` 是**单标的**端点，允许按需回补日线；`/expectancy` 一次要跑 11 个
策略，`/positions*` 可能一次看到 N 个持仓——这两类**只用库内已有数据，绝不回补**。
列表端点逐个回补正是 `_stored_bars` 当初要防的配额烧穿 bug，见 `app/api/bars.py`。

## 降级而非 500

库内没有日线或不足 `MIN_BARS` 时：`scores=None`、`action="watch"`、`data_warning` 给出
可执行的下一步。**绝不编造分数**，也绝不 500——持仓是用户自己录的，缺日线不该让整张表打不开。
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Sequence
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.bars import load_bars, require_stock
from app.api.schemas import (
    ApiMessage,
    BuySellPlan,
    ExpectancyBlock,
    ExpectancyRow,
    HoldEstimate,
    PoolStats,
    PoolStatsRow,
    PositionAdvice,
    PositionTradeCreate,
    PositionTradeRow,
    PositionTradeUpdate,
    PriceZone,
    QuantTrade,
    SimplePlanBoard,
    SimplePlanRow,
    SimpleSector,
    SimpleSkipped,
    StopPlan,
    StrategyBacktest,
    SyncJobView,
    TrailPlan,
)
from app.db.models import PositionTrade, Stock, StrategyPoolStats
from app.db.session import get_db
from app.services import board as board_service
from app.services import pool as pool_service
from app.services import positions as position_service
from app.services.history import stored_bar_rows
from app.services.quant import backtest as backtest_engine
from app.services.quant import decision
from app.services.quant.indicators import MIN_BARS
from app.services.quant.pool import POOL_MIN_BARS, POSITIVE_STOCK_MIN_TRADES

logger = logging.getLogger(__name__)

strategy_router = APIRouter(prefix="/api/v1/strategy", tags=["strategy"])

# 计划、回测、全市场池化必须用**同一组**风控参数与同一个基准，故两者都定义在
# `backtest` 里（单一来源），这里只引用——各写一份，改一处漏一处就会让页面上的
# 「实测」与汇总页说的不是同一件事。
LIVE_PARAMS = backtest_engine.LIVE_PARAMS
BASELINE_STRATEGY = backtest_engine.BASELINE_STRATEGY

# 「明日操作」的窗口与固定策略现在定义在 `services/board.py`——微信推送与网页共用同一份
# 计算，两个屏各写一个 500 的话 `verdict_text` 里的「最近 N 个交易日」会无理由地对不上。
# 这里保留同名再导出：`tests/test_strategy_simple.py` 直接 import 它们，端点 docstring 也
# 指着 `SIMPLE_STRATEGY` 说话。
PLAN_DAYS_DEFAULT = board_service.PLAN_DAYS_DEFAULT
SIMPLE_STRATEGY = board_service.SIMPLE_STRATEGY

# 搬进服务层的函数，按本文件既有的写法（见上面的 `LIVE_PARAMS`）再导出一份：本模块自己的
# 调用点照旧用私有名，`tests/test_strategy_simple.py` 里的 `routes_mod._run(...)` /
# `routes_mod._stored_bar_rows(...)` 也照旧解析得到，不必为一个搬家改测试。
#
# 持仓那一组同理搬进了 `services/positions.py`：傍晚的止损告警必须与 `/strategy/positions`
# 是同一次计算，而它只能从服务层调用。
_run = board_service.run_expectancy
_market_edge = board_service.market_edge
_stored_bar_rows = stored_bar_rows
_stored_ledger_rows = position_service.ledger_rows
_ledger = position_service.ledger_entries


def _resolve_strategy(strategy: str) -> str:
    if not backtest_engine.is_known_strategy(strategy):
        raise HTTPException(400, f"未知策略：{strategy}")
    return strategy


def _rows_or_404(db: Session, stock: Stock, days: int, *, backfill: bool) -> list[dict[str, Any]]:
    rows = load_bars(db, stock.ts_code, days, backfill=backfill)
    if not rows:
        raise HTTPException(404, "该股票暂无日线数据")
    return rows


def _simple_row(plan: decision.Plan) -> SimplePlanRow:
    """从 `Plan` 里挑出「明日操作」页要的四块，**不重算任何结论**。

    `action`/`action_label` 逐字照抄：它们已经是四条信号条件 + 实测期望符号 + 全市场否决的
    合成结果，任何"从 verdict 推 action"的简化都会静默删掉其中几项（负期望会重新变成买入）。
    """
    return SimplePlanRow(
        ts_code=plan.ts_code,
        name=plan.name,
        industry=plan.industry,
        price=plan.price,
        price_source=plan.price_source,
        # 与 stock_view 同一条规则：只有价格真的来自那根收盘 bar 时才给日期，
        # 否则会让人以为现价是收盘价。
        price_date=plan.as_of.isoformat()
        if plan.price_source == "close" and plan.as_of
        else None,
        is_stale=plan.is_stale,
        bars=plan.bars,
        action=plan.action,
        action_label=plan.action_label,
        entry_zone=PriceZone.model_validate(dataclasses.asdict(plan.entry_zone)),
        stop_loss=StopPlan.model_validate(dataclasses.asdict(plan.stop_loss)),
        take_profit=TrailPlan.model_validate(dataclasses.asdict(plan.take_profit)),
        expected_hold=HoldEstimate.model_validate(dataclasses.asdict(plan.expected_hold)),
        honesty=ExpectancyBlock.model_validate(dataclasses.asdict(plan.honesty)),
    )


def _to_quant_trade(trade: backtest_engine.Trade) -> QuantTrade:
    return QuantTrade(
        entry_date=trade.entry_date,
        entry_price=trade.entry_price,
        shares=trade.shares,
        exit_date=trade.exit_date,
        exit_price=trade.exit_price,
        pnl=trade.pnl,
        pnl_pct=trade.pnl_pct,
        hold_bars=trade.hold_bars,
        exit_reason=trade.exit_reason,
    )


def _to_backtest(
    stock: Stock,
    strategy: str,
    result: backtest_engine.BacktestResult,
    expectancy: backtest_engine.Expectancy,
) -> StrategyBacktest:
    return StrategyBacktest(
        ts_code=stock.ts_code,
        name=stock.name,
        strategy=strategy,
        strategies=backtest_engine.all_strategy_names(),
        params=result.params,
        metrics=result.metrics,
        stats=result.stats,
        equity=[
            {
                "trade_date": point.trade_date,
                "equity": point.equity,
                "close": point.close,
                "position": point.position,
                "drawdown": point.drawdown,
            }
            for point in result.equity
        ],
        trades=[_to_quant_trade(trade) for trade in result.trades],
        expectancy=ExpectancyBlock.model_validate(dataclasses.asdict(expectancy)),
        fused=backtest_engine.is_fused_strategy(strategy),
    )


# ======================================================================================
# 1 / 2：计划与回测（单标的，允许回补）
# ======================================================================================
@strategy_router.get("/plan/{ts_code}", response_model=BuySellPlan)
def strategy_plan(
    ts_code: str,
    strategy: str = Query(default="fusion"),
    days: int = Query(default=500, ge=MIN_BARS, le=500),
    equity: float = Query(default=1_000_000.0, gt=0, le=1e10),
    db: Session = Depends(get_db),
) -> BuySellPlan:
    """单股买卖计划：该不该买、在哪买、买多少、什么条件卖。

    `honesty` 是**必填**字段而不是可选装饰：一个没有实测记录的结论不允许出现在本页。

    还会读全市场汇总（`market_edge`）——它只能把买入降级为观望，永远不会把观望变成买入。
    """
    strategy = _resolve_strategy(strategy)
    stock = require_stock(db, ts_code)
    rows = _rows_or_404(db, stock, days, backfill=True)
    _, expectancy = _run(rows, strategy, days)
    plan = decision.build_plan(
        rows,
        ts_code=stock.ts_code,
        name=stock.name,
        industry=stock.industry,
        strategy=strategy,
        equity=equity,
        expectancy=expectancy,
        market_edge=_market_edge(db, strategy),
    )
    return BuySellPlan.model_validate(dataclasses.asdict(plan))


@strategy_router.get("/simple", response_model=SimplePlanBoard)
def simple_plan_board(db: Session = Depends(get_db)) -> SimplePlanBoard:
    """「明日操作」页：按行业分板块的候选名单 + 每只的买入价 / 持有天数 / 卖出价。

    这一页**不新增任何计算**——四个数字全部来自 `decision.build_plan`，候选池来自
    `services.picks.select_board_picks`。计算本身在 `services.board.build_board`（微信
    推送读的是同一个函数：推送必须与网页说同一句话，任何"再算一遍"都会漂移成
    「推送说买入、网页说观望」）。本路由只负责把裸 dataclass 组装成响应模型。

    候选池与 `/picks/daily` 的 `select_daily_picks` 共用第 1、2 步（同一个 SQL 门槛、
    同一次现算时机），**只差第 3 步**：本页每板块至多 `BOARD_PER_INDUSTRY` 只、板块内按
    总市值降序、不封顶；短名单是低风险优先、单行业 2 只、最多 8 只。所以两页对「今天
    哪些标的有资格」是同一个答案，而**名单不必互相包含**——短名单那 8 只不保证都出现在
    本页的板块里。

    成本：本页为全市场候体现算计划（2026-09-17 线上实测 250 行星 ≈ 14s），全站最重的
    端点。见 `build_board` 的 docstring。

    路径刻意是字面量 `/simple` 而不是给 `/plan` 加参数：它不接受任何 query 参数，
    没有 `strategy` 就没有人能传 `buy_hold` 拿到一整页「买入」（见 `SIMPLE_STRATEGY`）。

    顺带：候选池里的 `timing` 建议在这里**不返回**。它只用来复现 `/picks/daily` 的候选集，
    本页的买入理由来自计划（`action_label`/`honesty`）而不是 timing。
    """
    data = board_service.build_board(db)
    return SimplePlanBoard(
        basis_date=data.basis_date,
        strategy=data.strategy,
        strategy_label=data.strategy_label,
        window_days=data.window_days,
        bars_median=data.bars_median,
        note=data.note,
        caveat=data.caveat,
        sectors=[
            SimpleSector(
                industry=sector.industry,
                candidates=sector.candidates,
                # 截断后**入选**的只数，包含随后因库内日线不足被跳过的那些，所以它 ≥
                # len(rows)。两个差值说的是两件事，见 SimpleSector 的 docstring。
                selected=sector.selected,
                rows=[_simple_row(plan) for plan in sector.plans],
            )
            for sector in data.sectors
        ],
        skipped=[
            SimpleSkipped(
                ts_code=item.ts_code,
                name=item.name,
                industry=item.industry,
                bars=item.bars,
                reason=item.reason,
            )
            for item in data.skipped
        ],
    )


@strategy_router.get("/backtest/{ts_code}", response_model=StrategyBacktest)
def strategy_backtest(
    ts_code: str,
    strategy: str = Query(default="fusion"),
    days: int = Query(default=500, ge=MIN_BARS, le=500),
    costs: bool = Query(default=True),
    trail: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> StrategyBacktest:
    """带实测期望的回测。`trail=false` 关掉移动止盈，方便与 `stop_loss_atr` 单独对比。"""
    strategy = _resolve_strategy(strategy)
    stock = require_stock(db, ts_code)
    rows = _rows_or_404(db, stock, days, backfill=True)

    params = LIVE_PARAMS if trail else backtest_engine.BacktestParams(stop_loss_atr=2.0)
    if not costs:
        params = dataclasses.replace(
            params, commission=0.0, min_commission=0.0, stamp_tax=0.0, slippage=0.0
        )
    result, expectancy = _run(rows, strategy, days, params=params)
    return _to_backtest(stock, strategy, result, expectancy)


# ======================================================================================
# 3：11 个策略的实测对比（绝不回补）
# ======================================================================================
@strategy_router.get("/expectancy/{ts_code}", response_model=list[ExpectancyRow])
def strategy_expectancy(
    ts_code: str,
    days: int = Query(default=500, ge=MIN_BARS, le=500),
    costs: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> list[ExpectancyRow]:
    """该股 11 个策略各自的实测期望，按期望值降序。

    这是全页最有说服力的诚实产物：它同时展示经典策略也在亏钱，用户就不会把坏结果
    误读成"新页面坏了"。**但排行榜会诱发选择性偏差**——用户会挑样本里的赢家。
    内建的对冲是每行都带 `trade_count`（<20 笔硬标"样本不足"）和 `excess_return`，
    且 `buy_hold` 永远作为带标签的基准留在榜上。

    **只用库内日线**：这里要跑 11 次回测，一旦允许回补就是 11 次上游调用。
    """
    stock = require_stock(db, ts_code)
    rows = _stored_bar_rows(db, stock.ts_code, days)

    if len(rows) < MIN_BARS:  # 库内无日线：返回空榜而不是 500，前端显示 data_warning
        return []

    params = LIVE_PARAMS if costs else backtest_engine.BacktestParams(
        stop_loss_atr=2.0,
        trail_atr=2.0,
        commission=0.0,
        min_commission=0.0,
        stamp_tax=0.0,
        slippage=0.0,
    )
    # 基准先算：excess_return 必须在 trade_expectancy 里就算出来，事后 replace 补
    # benchmark_return 是补不回 excess_return 的（它已经按 None 算过了）。
    baseline = backtest_engine.backtest(rows, BASELINE_STRATEGY, params)
    baseline_return = backtest_engine._metric(baseline.metrics, "cumulative_return")

    filled: list[backtest_engine.Expectancy] = []
    for name in backtest_engine.all_strategy_names():
        if name == BASELINE_STRATEGY:
            # 基准行自己就是基准：excess 记 0，而不是拿它减自己（那会得到 0 之外的噪声）
            filled.append(
                dataclasses.replace(
                    backtest_engine.trade_expectancy(
                        baseline, strategy=name, window_days=days
                    ),
                    benchmark_return=baseline_return,
                    excess_return=0.0,
                )
            )
            continue
        result = backtest_engine.backtest(rows, name, params)
        filled.append(
            backtest_engine.trade_expectancy(
                result, strategy=name, window_days=days, benchmark_return=baseline_return
            )
        )
    # 期望为 None（零成交）的排到最后：不要把"没有样本"混进排行榜的中间位置
    filled.sort(key=lambda item: (item.expectancy_pct is None, -(item.expectancy_pct or 0.0)))
    return [
        ExpectancyRow.model_validate(
            {**dataclasses.asdict(item), "is_baseline": item.strategy == BASELINE_STRATEGY}
        )
        for item in filled
    ]


# ======================================================================================
# 4：全市场实测汇总（跨股票池化，绝不回补）
# ======================================================================================
def _pool_row(record: Any) -> PoolStatsRow:
    return PoolStatsRow(
        strategy=record.strategy,
        strategy_label=backtest_engine.strategy_label(record.strategy),
        is_baseline=record.strategy == BASELINE_STRATEGY,
        trade_count=record.trade_count,
        stocks_with_trades=record.stocks_with_trades,
        win_rate=record.win_rate,
        avg_win_pct=record.avg_win_pct,
        avg_loss_pct=record.avg_loss_pct,
        profit_factor=record.profit_factor,
        expectancy_pct=record.expectancy_pct,
        expectancy_per_bar_pct=record.expectancy_per_bar_pct,
        avg_hold_bars=record.avg_hold_bars,
        forced_end_trades=record.forced_end_trades,
        forced_end_share=record.forced_end_share,
        benchmark_expectancy_pct=record.benchmark_expectancy_pct,
        benchmark_expectancy_per_bar_pct=record.benchmark_expectancy_per_bar_pct,
        excess_per_bar_pct=record.excess_per_bar_pct,
        median_stock_expectancy_pct=record.median_stock_expectancy_pct,
        positive_stock_share=record.positive_stock_share,
        positive_stock_min_trades=record.positive_stock_min_trades,
        stock_denominator=record.stock_denominator,
        verdict=record.verdict,  # type: ignore[arg-type]
    )


@strategy_router.get("/pool", response_model=PoolStats)
def market_pool_stats(db: Session = Depends(get_db)) -> PoolStats:
    """11 条规则在全市场（跨股票池化）上的实测汇总。

    路径刻意**避开** `/expectancy/market`：`/expectancy/{ts_code}` 会把 `market`
    当成一个股票代码吞掉（`require_stock` 报 404，而不是走到这里）。

    **只读已物化的结果，绝不现场计算**：全市场跑一遍实测约 4 分钟，绝不能挂在请求里。
    表为空 → 200 + `computed_at=None` + `rows=[]`，由前端显示「尚未计算」而不是零值。
    """
    records = list(
        db.scalars(
            select(StrategyPoolStats).order_by(
                StrategyPoolStats.expectancy_pct.is_(None),
                StrategyPoolStats.expectancy_pct.desc(),
            )
        ).all()
    )
    if not records:
        return PoolStats(
            min_bars=POOL_MIN_BARS,
            stop_loss_atr=LIVE_PARAMS.stop_loss_atr,
            trail_atr=LIVE_PARAMS.trail_atr,
            note=_pool_note(),
            caveat=backtest_engine.POOL_CAVEAT,
        )
    head = records[0]
    return PoolStats(
        computed_at=head.computed_at,
        rule_version=head.rule_version,
        universe_total=head.universe_total,
        universe_used=head.universe_used,
        bars_total=head.bars_total,
        bars_median=head.bars_median,
        window_from=head.window_from,
        window_to=head.window_to,
        min_bars=head.min_bars,
        stop_loss_atr=head.stop_loss_atr,
        trail_atr=head.trail_atr,
        note=_pool_note(),
        caveat=backtest_engine.POOL_CAVEAT,
        rows=[_pool_row(record) for record in records],
    )


def _pool_note() -> str:
    """把两个"每家每户都可能不一样"的口径写死在响应里，免得前端自己猜或写死。

    分母是最容易被静默换掉的东西：`stocks_with_trades`（这只股票上真出了成交的股票数）
    与 `stock_denominator`（成交 ≥ `positive_stock_min_trades` 笔、参与中位数与正股占比
    的股票数）不是一回事，页面必须把两者都说出来。
    """
    return (
        f"「股票中位数」与「正股占比」只统计成交 ≥ {POSITIVE_STOCK_MIN_TRADES} 笔的股票"
        f"（分母见每行的 stock_denominator）；每条规则的池化样本见 stocks_with_trades。"
        f"口径：{LIVE_PARAMS.stop_loss_atr:g}×ATR 止损 + {LIVE_PARAMS.trail_atr:g}×ATR 移动止盈，"
        f"与个股页显示的止损/移动止盈是同一组。"
    )


@strategy_router.post("/pool/refresh", response_model=SyncJobView, status_code=202)
def refresh_market_pool_stats(db: Session = Depends(get_db)) -> SyncJobView:
    """触发一次全市场池化汇总（后台线程，约 4 分钟）。

    先建 `SyncRun` 行再起线程，这样能立刻返回 run_id，前端用既有的
    `GET /sync/jobs/{id}` 轮询进度。非阻塞锁保证重复点击不会起第二份，
    被拒的那次会把刚建的 `SyncRun` 行结掉（不会留在 running 让前端一直转圈）。
    """
    run_id = pool_service.start_run()
    # daemon 线程：进程退出时不阻塞（跑到一半被杀掉也无妨——旧快照原样留存，
    # 因为写库集中在最后一步）。
    threading.Thread(
        target=pool_service.run_pool_job, kwargs={"run_id": run_id}, daemon=True
    ).start()
    # `status="running"` 是 `start_run` 刚建那一行的状态（见 pool.start_run），不是这里
    # 现编的：若另一次汇总正持锁，本线程会立刻把它结掉成 failed，前端第一次轮询就会看到。
    return SyncJobView(
        id=run_id,
        job_type=pool_service.POOL_JOB,
        status="running",
        message=f"已开始计算全市场汇总（任务号 {run_id}），约需数分钟；算完后本卡会自动刷新。",
    )
def _oversell_date(trades: Sequence[decision.LedgerEntry]) -> Optional[date]:
    """按时间回放，返回**第一个持仓转负的日期**；不会转负则 None。

    为什么不能只比较"总卖出 > 总买入"：那样会放过"先卖后买"这种在时间上不可能的流水。
    也不能用 `summarize_ledger`——它把超卖夹到 0，是个显示函数而不是校验器。
    """
    ordered = sorted(trades, key=lambda item: (item.trade_date, item.side != "buy"))
    shares = 0.0
    for item in ordered:
        shares += item.shares if item.side == "buy" else -item.shares
        if shares < 0:
            return item.trade_date
    return None


def _advice_for(db: Session, stock: Stock, *, equity: float, days: int) -> PositionAdvice:
    """计算 + **在内核边界上转成响应模型**。

    计算搬进了 `services.positions.advise_for`（傍晚的止损告警必须与这里逐位一致，理由见
    那个模块的 docstring）。留下的这一步转换刻意还在路由层：`extra="forbid"` 于是每次请求
    都在检查"内核字段 ↔ 响应字段"没有漂移，两端都不会静默丢字段。
    """
    advice = position_service.advise_for(db, stock, equity=equity, days=days)
    return PositionAdvice.model_validate(dataclasses.asdict(advice))


def _to_row(db: Session, trade: PositionTrade, names: dict[str, str]) -> PositionTradeRow:
    return PositionTradeRow(
        id=trade.id,
        ts_code=trade.ts_code,
        name=names.get(trade.ts_code, ""),
        side=trade.side,  # type: ignore[arg-type]
        trade_date=trade.trade_date,
        price=trade.price,
        shares=trade.shares,
        fee=trade.fee,
        stop_price=trade.stop_price,
        strategy=trade.strategy,
        note=trade.note,
        created_at=trade.created_at,
    )


@strategy_router.get("/positions", response_model=list[PositionAdvice])
def list_positions(
    status: str = Query(default="holding", pattern="^(holding|closed|all)$"),
    equity: float = Query(default=1_000_000.0, gt=0, le=1e10),
    days: int = Query(default=500, ge=MIN_BARS, le=500),
    db: Session = Depends(get_db),
) -> list[PositionAdvice]:
    """持仓跟踪与调整建议。默认只给未清仓的标的。"""
    codes = position_service.traded_codes(db)
    if not codes:
        return []
    stocks = {
        stock.ts_code: stock
        for stock in db.scalars(select(Stock).where(Stock.ts_code.in_(codes)))
    }
    advice = [
        _advice_for(db, stocks[code], equity=equity, days=days)
        for code in codes
        if code in stocks
    ]
    if status != "all":
        advice = [item for item in advice if item.status == status]
    return advice


@strategy_router.get("/positions/{ts_code}/trades", response_model=list[PositionTradeRow])
def list_position_trades(ts_code: str, db: Session = Depends(get_db)) -> list[PositionTradeRow]:
    stock = require_stock(db, ts_code)
    rows = db.scalars(
        select(PositionTrade)
        .where(PositionTrade.ts_code == stock.ts_code)
        .order_by(PositionTrade.trade_date, PositionTrade.id)
    ).all()
    names = {stock.ts_code: stock.name}
    return [_to_row(db, row, names) for row in rows]


@strategy_router.post("/positions/trades", response_model=PositionAdvice, status_code=201)
def add_position_trade(
    payload: PositionTradeCreate,
    equity: float = Query(default=1_000_000.0, gt=0, le=1e10),
    days: int = Query(default=500, ge=MIN_BARS, le=500),
    db: Session = Depends(get_db),
) -> PositionAdvice:
    """录入一笔流水。返回该标的**更新后**的持仓建议，省掉前端一次往返。"""
    # 先查 Stock：PRAGMA foreign_keys=ON 生效中，漏掉这一步 SQLite 会抛
    # IntegrityError 变成 500 而不是 404。
    stock = require_stock(db, payload.ts_code)
    if payload.side == "sell":
        current = decision.summarize_ledger(_ledger(db, stock.ts_code))
        held = current.shares if current else 0.0
        # 先给最常见的情形一句直白的报错，再让回放兜住"日期上不可能"的流水
        if payload.shares > held:
            raise HTTPException(422, f"卖出股数 {payload.shares:g} 超过当前持仓 {held:g}")
        prospective = [
            *_ledger(db, stock.ts_code),
            decision.LedgerEntry(
                side="sell",
                trade_date=payload.trade_date,
                price=payload.price,
                shares=payload.shares,
                fee=payload.fee,
            ),
        ]
        bad_date = _oversell_date(prospective)
        if bad_date is not None:
            raise HTTPException(
                422, f"按日期回放，{bad_date} 这天的持仓会变成负数——卖出不能早于对应的买入"
            )

    trade = PositionTrade(
        ts_code=stock.ts_code,
        side=payload.side,
        trade_date=payload.trade_date,
        price=payload.price,
        shares=payload.shares,
        fee=payload.fee,
        stop_price=payload.stop_price,
        strategy=payload.strategy,
        note=payload.note,
    )
    db.add(trade)
    db.commit()
    return _advice_for(db, stock, equity=equity, days=days)


def _get_trade(db: Session, trade_id: int) -> PositionTrade:
    trade = db.get(PositionTrade, trade_id)
    if trade is None:
        raise HTTPException(404, "未找到该流水")
    return trade


@strategy_router.patch("/positions/trades/{trade_id}", response_model=PositionAdvice)
def update_position_trade(
    trade_id: int,
    payload: PositionTradeUpdate,
    equity: float = Query(default=1_000_000.0, gt=0, le=1e10),
    days: int = Query(default=500, ge=MIN_BARS, le=500),
    db: Session = Depends(get_db),
) -> PositionAdvice:
    """改一笔流水（改错录的价格/日期，或补上真实止损位）。

    只改显式给出的字段——`exclude_unset`，否则 PATCH 会把没提到的字段全清成 None。
    """
    trade = _get_trade(db, trade_id)
    changes = payload.model_dump(exclude_unset=True)

    # 按主键定位那一行再替换。**不要**用"字段全等"猜是哪一行：两笔同日同价的流水会
    # 互相冒充，改错一行而校验通过。`_ledger` 与这里用同一个排序，index 才是可靠的身份。
    stored = _stored_ledger_rows(db, trade.ts_code)
    index = next((i for i, row in enumerate(stored) if row.id == trade.id), None)
    assert index is not None  # 刚查出来的行必然在列表里
    prospective = [
        decision.LedgerEntry(
            side=row.side, trade_date=row.trade_date, price=row.price,
            shares=row.shares, fee=row.fee,
        )
        for row in stored
    ]
    prospective[index] = decision.LedgerEntry(
        side=changes.get("side", trade.side),
        trade_date=changes.get("trade_date", trade.trade_date),
        price=changes.get("price", trade.price),
        shares=changes.get("shares", trade.shares),
        fee=changes.get("fee", trade.fee),
    )
    bad_date = _oversell_date(prospective)
    if bad_date is not None:
        raise HTTPException(
            422, f"改完之后，{bad_date} 这天的持仓会变成负数——卖出不能超过当时持有的股数"
        )

    for field, value in changes.items():
        setattr(trade, field, value)
    db.commit()
    stock = db.get(Stock, trade.ts_code)
    assert stock is not None  # 有流水就必有 Stock（外键保证）
    return _advice_for(db, stock, equity=equity, days=days)


@strategy_router.delete("/positions/trades/{trade_id}", response_model=ApiMessage)
def delete_position_trade(trade_id: int, db: Session = Depends(get_db)) -> ApiMessage:
    trade = _get_trade(db, trade_id)
    db.delete(trade)
    db.commit()
    return ApiMessage(message="已删除")
