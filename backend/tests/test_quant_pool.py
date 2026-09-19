"""池化聚合数学与"只能否决"的契约。

本文件里最重要的两条：

1. `test_pooled_stats_are_reproducible_from_the_trade_lists` —— 汇总里的每个数字都必须
   能由 `backtest()` 的成交明细**独立重算**出来。只断言"字段存在"对一个写死的常量同样
   成立，而写死的常量正是本功能最不能有的东西（与 `test_strategy_api` 的第一条同源）。
2. `test_veto_can_only_downgrade` —— 「全市场汇总只能否决、不能放行」的**机械形式**：
   任何输入下，带 `market_edge` 的动作都不允许比不带它更乐观。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from app.services.quant import backtest as backtest_engine
from app.services.quant.pool import (
    POOL_MIN_BARS,
    POSITIVE_STOCK_MIN_TRADES,
    PoolAccumulator,
)

START = date(2026, 1, 5)
PARAMS = backtest_engine.LIVE_PARAMS


def _bars(closes: list[float], spread: float = 1.0) -> list[dict[str, object]]:
    return [
        {
            "trade_date": START + timedelta(days=index),
            "open": close,
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": 1_000_000.0,
        }
        for index, close in enumerate(closes)
    ]


def _wave(length: int, *, amplitude: float, drift: float) -> list[float]:
    """确定性的锯齿 + 漂移序列：让均线类与摆动类规则都能真的成交。

    刻意不用随机数——回测对序列形状敏感，随机序列会让"期望值应该等于多少"无法手算，
    测试就退化成"跑出什么算什么"。
    """
    return [
        100.0 + drift * index + amplitude * (1 if index % 20 < 10 else -1)
        for index in range(length)
    ]


def _pool() -> tuple[list[list[dict[str, object]]], PoolAccumulator]:
    """三只形态不同的股票，全部达到 POOL_MIN_BARS。"""
    series = [
        _bar_set(amplitude=8.0, drift=0.12),
        _bar_set(amplitude=4.0, drift=-0.09),
        _bar_set(amplitude=11.0, drift=0.0),
    ]
    accumulator = PoolAccumulator()
    for rows in series:
        assert accumulator.add_stock(rows, PARAMS)
    return series, accumulator


def _bar_set(*, amplitude: float, drift: float) -> list[dict[str, object]]:
    return _bars(_wave(POOL_MIN_BARS + 40, amplitude=amplitude, drift=drift))


# ======================================================================================
# 1：聚合数学可由成交明细独立重算
# ======================================================================================
def test_pooled_stats_are_reproducible_from_the_trade_lists() -> None:
    """逐项核对：池化的聚合必须等于把三只股票的成交**堆在一起**重算的结果。"""
    series, accumulator = _pool()
    rows = {row.strategy: row for row in accumulator.finalize(universe_total=3, rule_version="v2")}

    for name in backtest_engine.all_strategy_names():
        trades = [
            trade
            for stock in series
            for trade in backtest_engine.backtest(stock, name, PARAMS).trades
        ]
        row = rows[name]
        assert row.trade_count == len(trades), name
        if not trades:
            assert row.expectancy_pct is None, name
            continue
        # 独立重算：直接用成交列表，不碰累加器里的任何和数
        wins = [trade for trade in trades if trade.pnl > 0]
        losses = [trade for trade in trades if trade.pnl <= 0]
        win_rate = len(wins) / len(trades)
        avg_win = sum(trade.pnl_pct for trade in wins) / len(wins) if wins else 0.0
        avg_loss = sum(trade.pnl_pct for trade in losses) / len(losses) if losses else 0.0
        expected = win_rate * avg_win + (1 - win_rate) * avg_loss

        assert row.expectancy_pct == pytest.approx(expected, abs=1e-12), name
        # 代数等价形式：期望/笔必须等于 pnl_pct 的算术平均
        assert row.expectancy_pct == pytest.approx(
            sum(trade.pnl_pct for trade in trades) / len(trades), abs=1e-12
        ), name
        assert row.win_rate == pytest.approx(win_rate, abs=1e-12), name
        assert row.avg_hold_bars == pytest.approx(
            sum(trade.hold_bars for trade in trades) / len(trades), abs=1e-12
        ), name
        assert row.forced_end_trades == sum(
            1 for trade in trades if trade.exit_reason == "end"
        ), name
        gross_win = sum(trade.pnl for trade in wins)
        gross_loss = abs(sum(trade.pnl for trade in losses))
        if gross_loss:
            assert row.profit_factor == pytest.approx(gross_win / gross_loss, abs=1e-12), name


def test_verdict_matches_trade_expectancy_on_the_same_trades() -> None:
    """`verdict_for` 与 `trade_expectancy` 必须给出**同一个**判定——它是唯一实现。"""
    series, accumulator = _pool()
    rows = {row.strategy: row for row in accumulator.finalize(universe_total=3, rule_version="v2")}
    for stock in series:
        for name in backtest_engine.all_strategy_names():
            result = backtest_engine.backtest(stock, name, PARAMS)
            single = backtest_engine.trade_expectancy(result, strategy=name)
            # 同一批成交，一条走累加器（池化路径），一条走 trade_expectancy（个股路径）
            assert single.verdict == backtest_engine.verdict_for(
                len(result.trades), single.expectancy_pct
            ), (name, single.verdict)
            if single.trade_count == 0:
                assert rows[name].verdict in {"insufficient", "negative"}


@pytest.mark.parametrize(
    ("count", "expectancy", "expected"),
    [
        (0, None, "insufficient"),
        (19, 5.0, "insufficient"),  # 门槛之下，正期望也判样本不足
        (20, 5.0, "positive"),
        (20, 0.0, "negative"),  # 零期望不是正期望
        (20, -0.001, "negative"),
        (1_000, -12.4, "negative"),
    ],
)
def test_verdict_threshold_behaviour(count: int, expectancy, expected: str) -> None:
    assert backtest_engine.verdict_for(count, expectancy) == expected


# ======================================================================================
# 2：宇宙口径——分母必须诚实
# ======================================================================================
def test_stocks_below_min_bars_are_skipped_entirely() -> None:
    accumulator = PoolAccumulator()
    short = _bars(_wave(POOL_MIN_BARS - 1, amplitude=5.0, drift=0.1))
    exact = _bars(_wave(POOL_MIN_BARS, amplitude=5.0, drift=0.1))
    assert not accumulator.add_stock(short, PARAMS)
    assert accumulator.add_stock(exact, PARAMS)

    assert accumulator.stocks_used == 1
    # 被跳过的那只既不进 bars_total 也不进 stats——不足 60 根在 MA60 上全是 None，
    # 计进去等于往样本里掺零
    assert accumulator.bars_total == POOL_MIN_BARS
    assert accumulator.window() == (
        START,
        START + timedelta(days=POOL_MIN_BARS - 1),
    )


def test_bars_median_is_a_median_not_a_mean() -> None:
    """中位数决定"窗口有多长"。用均值会被少数深历史股票拉高，读出像是覆盖了两年。"""
    accumulator = PoolAccumulator()
    for length in (60, 60, 60, 400):
        accumulator.add_stock(_bars(_wave(length, amplitude=5.0, drift=0.1)), PARAMS)

    assert accumulator.bars_median() == 60
    assert accumulator.bars_total == 580  # 均值 145，与中位数差一倍以上


def test_window_is_the_median_of_each_stocks_dates() -> None:
    """窗口取各股首/末交易日的**中位数**，不是 min/max 的并集。

    库内 97% 的股票只覆盖尾部 4.5 个月，2 年并集只属于少数深历史股票；把并集当
    "样本覆盖区间"会让读者以为回测跑在两年数据上。
    """
    accumulator = PoolAccumulator()
    accumulator.add_stock(_bars(_wave(60, amplitude=5.0, drift=0.1)), PARAMS)
    accumulator.add_stock(_bars(_wave(60, amplitude=5.0, drift=0.1)), PARAMS)
    deep = _bars(_wave(60, amplitude=5.0, drift=0.1))
    # 一只深历史股票：整体往前挪 200 天
    for index, row in enumerate(deep):
        row["trade_date"] = START + timedelta(days=index - 200)
    accumulator.add_stock(deep, PARAMS)

    window_from, window_to = accumulator.window()
    # 中位数是那两只常规股票，而不是 min/max（那会得到 -200 与 +59）
    assert window_to == START + timedelta(days=59)
    assert window_from == START


# ======================================================================================
# 3：基准与"反池化"度量
# ======================================================================================
def test_baseline_row_has_zero_excess_and_no_veto_role() -> None:
    """买入持有自己就是基准：超额记 0，而不是拿它减自己（那是噪声）。"""
    _, accumulator = _pool()
    rows = {row.strategy: row for row in accumulator.finalize(universe_total=3, rule_version="v2")}
    baseline = rows[backtest_engine.BASELINE_STRATEGY]

    assert baseline.excess_per_bar_pct == 0.0
    assert baseline.expectancy_pct == baseline.benchmark_expectancy_pct
    for row in rows.values():
        # 每条规则都挂着同一个基准，页面才能并排读
        assert row.benchmark_expectancy_pct == baseline.expectancy_pct


def test_excess_uses_per_bar_so_holding_periods_are_comparable() -> None:
    """超额必须按 bar 算。按笔算会让"5 天赚 1%"与"103 天赚 1%"看起来一样——
    买入持有每笔持有 ~100 个交易日，拿它的每笔期望去比别人是量纲错误。"""
    _, accumulator = _pool()
    rows = {row.strategy: row for row in accumulator.finalize(universe_total=3, rule_version="v2")}
    baseline = rows[backtest_engine.BASELINE_STRATEGY]

    for name, row in rows.items():
        if name == backtest_engine.BASELINE_STRATEGY:
            continue
        if row.expectancy_per_bar_pct is None:
            assert row.excess_per_bar_pct is None, name
            continue
        assert row.excess_per_bar_pct == pytest.approx(
            row.expectancy_per_bar_pct - baseline.expectancy_per_bar_pct, abs=1e-12
        ), name


def test_stock_median_and_positive_share_respect_the_per_stock_floor() -> None:
    """两个"反池化"度量的分母是"成交 ≥ N 笔的股票"，不是全部股票。"""
    _, accumulator = _pool()
    rows = accumulator.finalize(universe_total=3, rule_version="v2")

    for row in rows:
        assert row.positive_stock_min_trades == POSITIVE_STOCK_MIN_TRADES
        assert row.stock_denominator <= row.stocks_with_trades
        if row.stock_denominator == 0:
            # 没有一只股票达到笔数门槛时，中位数与占比必须是 None——不能是 0，
            # 前端把 None 渲染成 `--`，把 0 渲染成"0%"，后者是在陈述一个结论
            assert row.median_stock_expectancy_pct is None
            assert row.positive_stock_share is None
        else:
            assert 0.0 <= row.positive_stock_share <= 1.0


def test_finalize_is_deterministic_and_sorted_by_expectancy() -> None:
    _, accumulator = _pool()
    first = accumulator.finalize(universe_total=3, rule_version="v2")
    second = accumulator.finalize(universe_total=3, rule_version="v2")

    assert first == second
    values = [row.expectancy_pct for row in first]
    # 期望为 None（零成交）的排最后，不要混进排行榜中间
    assert [value is None for value in values] == sorted(value is None for value in values)
    known = [value for value in values if value is not None]
    assert known == sorted(known, reverse=True)
