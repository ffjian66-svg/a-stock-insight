"""量化分析 API。

这一层只做「取数 + 组装」：DB/provider 的访问全部留在这里，`app/services/quant/`
内核保持纯计算、可离线单测。单标的端点（series/performance/backtest）首次访问会经
`ensure_bar_history` 回补两年日线，之后直接读库；扫描端点只读库内已有数据，原因见
`_load_bars` 的说明。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.bars import benchmark, load_bars, require_stock
from app.api.schemas import (
    QuantBacktest,
    QuantFactorDefinition,
    QuantFactorRow,
    QuantFactors,
    QuantIndicatorPoint,
    QuantPerformance,
    QuantScanRow,
    QuantSeries,
    QuantSignal,
    QuantTrade,
)
from app.db.models import FactorSnapshot, LatestQuote, ScoreSnapshot, Stock
from app.db.session import get_db
from app.services.quant import backtest as backtest_engine
from app.services.quant import factors as factor_engine
from app.services.quant import performance as performance_engine
from app.services.quant import signals as signal_engine
from app.services.quant.indicators import MIN_BARS, indicator_series, last_scalars

quant_router = APIRouter(prefix="/api/v1/quant", tags=["quant"])

# 扫描范围：全市场 5,561 只逐个现算指标每次要几秒，故只在综合分靠前的池子里扫。
SCAN_POOL = 300

# QuantIndicatorPoint 上除 trade_date/close/volume 外的指标字段，来自等长序列
_POINT_KEYS = (
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "boll_up",
    "boll_mid",
    "boll_low",
    "dif",
    "dea",
    "macd_hist",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "rsi",
    "atr14",
)

_OVERLAY_KEYS = (
    "boll_up",
    "boll_mid",
    "boll_low",
    "dif",
    "dea",
    "macd_hist",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "atr14",
    "obv",
    "roc20",
    "amplitude",
    "donchian_up",
    "donchian_low",
    "rsi_wilder",
)

def _indicator_point(
    row: dict[str, Any], index: int, series: dict[str, list[Optional[float]]]
) -> QuantIndicatorPoint:
    """把「等长序列」按位移回「逐 bar 对象」——前端图表要的是行而不是列。"""
    values = {name: values[index] for name, values in series.items()}
    return QuantIndicatorPoint(
        trade_date=row["trade_date"],
        close=row["close"],
        volume=row.get("volume"),
        **{name: values.get(name) for name in _POINT_KEYS},
    )


def _to_signal(signal: signal_engine.Signal) -> QuantSignal:
    return QuantSignal(
        name=signal.name,
        label=signal.label,
        direction=signal.direction,
        detail=signal.detail,
        strength=signal.strength,
        level=signal.level,
    )


@quant_router.get("/series/{ts_code}", response_model=QuantSeries)
def quant_series(
    ts_code: str,
    days: int = Query(default=250, ge=MIN_BARS, le=500),
    db: Session = Depends(get_db),
) -> QuantSeries:
    """K 线 + 全部指标序列 + 最新信号，供指标图与信号面板使用。"""
    stock = require_stock(db, ts_code)
    rows = load_bars(db, stock.ts_code, days)
    if not rows:
        raise HTTPException(404, "该股票暂无日线数据")

    series = indicator_series(rows)
    closes = [float(row["close"]) for row in rows]
    signals = signal_engine.latest_signals(series, closes)

    points = [_indicator_point(row, index, series) for index, row in enumerate(rows)]

    latest = last_scalars(rows)
    for key in _OVERLAY_KEYS:
        values = series.get(key)
        if values and values[-1] is not None:
            latest[key] = values[-1]
    return QuantSeries(
        ts_code=stock.ts_code,
        name=stock.name,
        industry=stock.industry,
        window_days=days,
        bars=len(rows),
        indicators=points,
        signals=[_to_signal(signal) for signal in signals],
        signal_score=signal_engine.signal_score(signals),
        latest=latest,
    )


@quant_router.get("/performance/{ts_code}", response_model=QuantPerformance)
def quant_performance(
    ts_code: str,
    days: int = Query(default=250, ge=MIN_BARS, le=500),
    db: Session = Depends(get_db),
) -> QuantPerformance:
    """风险绩效指标（含相对上证指数的 Beta/Alpha），样本不足时各比率为 None。"""
    stock = require_stock(db, ts_code)
    rows = load_bars(db, stock.ts_code, days)
    if not rows:
        raise HTTPException(404, "该股票暂无日线数据")

    closes = [float(row["close"]) for row in rows]
    dates = [row["trade_date"] for row in rows]
    # 局部变量不能叫 benchmark：函数同名会让整个函数体把这个名字当局部，
    # 于是 `benchmark(days)` 直接 UnboundLocalError。
    benchmark_name, benchmark_series = benchmark(days)
    metrics = performance_engine.performance_metrics(
        closes, dates=dates, benchmark=benchmark_series or None
    )
    equity = performance_engine.drawdown_series(closes)
    curve = [
        {
            "trade_date": dates[index],
            "close": closes[index],
            "drawdown": equity[index],
        }
        for index in range(len(closes))
    ]
    return QuantPerformance(
        ts_code=stock.ts_code,
        name=stock.name,
        window_days=days,
        benchmark=benchmark_name if benchmark_series else "",
        metrics=metrics,
        equity=curve,
    )


@quant_router.get("/backtest/{ts_code}", response_model=QuantBacktest)
def quant_backtest(
    ts_code: str,
    strategy: str = Query(default="ma_cross"),
    days: int = Query(default=500, ge=MIN_BARS, le=500),
    costs: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> QuantBacktest:
    """单标的策略回测：信号次日开盘成交，可选交易成本。"""
    if strategy not in backtest_engine.strategy_names():
        raise HTTPException(400, f"未知策略：{strategy}")
    stock = require_stock(db, ts_code)
    rows = load_bars(db, stock.ts_code, days)
    if not rows:
        raise HTTPException(404, "该股票暂无日线数据")

    params = backtest_engine.BacktestParams() if costs else backtest_engine.BacktestParams(
        commission=0.0, min_commission=0.0, stamp_tax=0.0, slippage=0.0
    )
    result = backtest_engine.backtest(rows, strategy, params)
    return QuantBacktest(
        ts_code=stock.ts_code,
        name=stock.name,
        strategy=strategy,
        strategies=backtest_engine.strategy_names(),
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
        trades=[
            QuantTrade(
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
            for trade in result.trades
        ],
    )


@quant_router.get("/factors", response_model=QuantFactors)
def quant_factors(
    limit: int = Query(default=30, ge=1, le=200),
    industry: str = Query(default="", max_length=40),
    db: Session = Depends(get_db),
) -> QuantFactors:
    """因子定义 + 综合分位榜（直接读 recalculate_scores 落下的快照，零重算）。"""
    query = (
        select(FactorSnapshot, Stock)
        .join(Stock, Stock.ts_code == FactorSnapshot.ts_code)
        .where(FactorSnapshot.composite.is_not(None))
        .order_by(FactorSnapshot.composite.desc())
        .limit(limit)
    )
    if industry:
        query = query.where(Stock.industry == industry)
    rows = db.execute(query).all()
    rule_version = rows[0][0].rule_version if rows else ""
    return QuantFactors(
        definitions=[
            QuantFactorDefinition(**item) for item in factor_engine.factor_definitions()
        ],
        groups=list(factor_engine.GROUPS),
        group_weights=dict(factor_engine.GROUP_WEIGHTS),
        rule_version=rule_version,
        rows=[
            QuantFactorRow(
                ts_code=stock.ts_code,
                name=stock.name,
                industry=stock.industry,
                composite=snapshot.composite,
                coverage=snapshot.coverage,
                factors=snapshot.factors or {},
                trade_date=snapshot.trade_date,
                calculated_at=snapshot.calculated_at,
            )
            for snapshot, stock in rows
        ],
    )


@quant_router.get("/scan", response_model=list[QuantScanRow])
def quant_scan(
    signal: str = Query(default="", max_length=40),
    direction: str = Query(default="", pattern=r"^(buy|sell|neutral)?$"),
    limit: int = Query(default=30, ge=1, le=100),
    pool: int = Query(default=SCAN_POOL, ge=10, le=1000),
    db: Session = Depends(get_db),
) -> list[QuantScanRow]:
    """信号扫描：在综合分前 `pool` 只里现算信号，按信号强度排序。

    不做字面意义的「全市场」——5,561 只逐个算指标每次要几秒，而低频信号本来就集中在
    有评分、有流动性的标的上。池子大小可用 `pool` 调整。
    """
    query = (
        select(Stock.ts_code, Stock.name, Stock.industry, ScoreSnapshot.total_score)
        .join(ScoreSnapshot, ScoreSnapshot.ts_code == Stock.ts_code)
        .where(ScoreSnapshot.total_score.is_not(None))
        .order_by(ScoreSnapshot.total_score.desc())
        .limit(pool)
    )
    universe = db.execute(query).all()
    if not universe:
        return []

    codes = [row[0] for row in universe]
    bars: dict[str, list[dict[str, Any]]] = {}
    for code in codes:
        rows = load_bars(db, code, 120, backfill=False)
        if rows:
            bars[code] = rows
    quotes = {
        quote.ts_code: quote
        for quote in db.scalars(
            select(LatestQuote).where(LatestQuote.ts_code.in_(list(bars)))
        ).all()
    }

    found: list[QuantScanRow] = []
    for code, name, industry, total in universe:
        code_bars = bars.get(code)
        if not code_bars:
            continue
        series = indicator_series(code_bars)
        closes = [float(row["close"]) for row in code_bars]
        quote = quotes.get(code)
        price = quote.price if quote and not quote.is_stale else closes[-1]
        for item in signal_engine.latest_signals(series, closes):
            if item.direction == "neutral":
                continue
            if signal and item.name != signal:
                continue
            if direction and item.direction != direction:
                continue
            found.append(
                QuantScanRow(
                    ts_code=code,
                    name=name,
                    industry=industry,
                    total_score=total,
                    price=price,
                    signal=item.name,
                    label=item.label,
                    direction=item.direction,
                    detail=item.detail,
                    strength=item.strength,
                    level=item.level,
                )
            )
    found.sort(key=lambda row: (-row.strength, -(row.total_score or 0)))
    return found[:limit]

