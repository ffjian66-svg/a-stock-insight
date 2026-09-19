"""回测引擎测试：成交时点（无未来函数）、成本、整手、止损与确定性。"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest
from app.services.quant import backtest as bt
from app.services.quant import signals as signal_rules
from app.services.quant.indicators import indicator_series

START = date(2025, 1, 1)


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


def _v_shape() -> list[float]:
    """先跌 40 天再涨 40 天，制造一次确定的 MA5 上穿 MA20。"""
    down = [100.0 - index for index in range(40)]
    up = [down[-1] + index * 1.5 for index in range(1, 41)]
    return down + up


def _free() -> bt.BacktestParams:
    return bt.BacktestParams(slippage=0.0, commission=0.0, min_commission=0.0, stamp_tax=0.0)


def _with_trail(multiple: float) -> bt.BacktestParams:
    return dataclasses.replace(_free(), trail_atr=multiple)


def _spike_then_crash() -> list[float]:
    """先跌再涨（制造金叉建仓与足够浮盈），最后急跌触发移动止盈。"""
    down = [100.0 - index for index in range(40)]
    up = [down[-1] + index * 1.5 for index in range(1, 61)]
    crash = [up[-1] - index * 4.0 for index in range(1, 25)]
    return down + up + crash


# --------------------------------------------------------------------------------------
# 成交时点：信号在 bar i 收盘产生，成交必须在 bar i+1 开盘
# --------------------------------------------------------------------------------------
def test_entry_fills_on_the_next_bar_open() -> None:
    bars = _bars(_v_shape())
    closes = [float(bar["close"]) for bar in bars]
    series = indicator_series(
        [{"close": bar["close"], "high": bar["high"], "low": bar["low"]} for bar in bars]
    )
    index = next(
        i
        for i in range(len(bars))
        if any(
            signal.name == "ma_cross" and signal.direction == "buy"
            for signal in signal_rules.evaluate_signals(series, closes, i)
        )
    )
    result = bt.backtest(bars, "ma_cross", _free())
    assert result.trades, "构造的序列必然产生至少一次金叉交易"
    trade = result.trades[0]
    assert trade.entry_date == bars[index + 1]["trade_date"]
    assert trade.entry_price == pytest.approx(float(bars[index + 1]["open"]))


def test_first_fill_happens_strictly_after_the_first_signal_bar() -> None:
    """信号 bar 本身绝不能有成交——那正是未来函数的典型症状。"""
    bars = _bars(_v_shape())
    closes = [float(bar["close"]) for bar in bars]
    series = indicator_series(
        [{"close": bar["close"], "high": bar["high"], "low": bar["low"]} for bar in bars]
    )
    signal_bars = [
        index
        for index in range(len(bars))
        if any(
            signal.name == "ma_cross" and signal.direction == "buy"
            for signal in signal_rules.evaluate_signals(series, closes, index)
        )
    ]
    assert signal_bars, "构造的序列必然产生金叉"
    trade = bt.backtest(bars, "ma_cross", _free()).trades[0]
    entry_index = next(
        index for index, bar in enumerate(bars) if bar["trade_date"] == trade.entry_date
    )
    assert entry_index == signal_bars[0] + 1


# --------------------------------------------------------------------------------------
# 成本 / 整手 / 止损
# --------------------------------------------------------------------------------------
def test_costs_reduce_final_equity() -> None:
    bars = _bars(_v_shape())
    free = bt.backtest(bars, "ma_cross", _free())
    charged = bt.backtest(bars, "ma_cross", bt.BacktestParams())
    assert free.trades and charged.trades
    assert charged.trades[0].pnl < free.trades[0].pnl
    assert charged.equity[-1].equity < free.equity[-1].equity


def test_positions_are_whole_lots() -> None:
    bars = _bars(_v_shape())
    result = bt.backtest(bars, "ma_cross", bt.BacktestParams())
    for trade in result.trades:
        assert trade.shares > 0
        assert trade.shares % 100 == 0


def test_commission_has_a_floor() -> None:
    # 小资金下按比例算出的佣金低于 5 元，必须按 5 元收取
    bars = _bars(_v_shape())
    params = bt.BacktestParams(initial_cash=10_000.0, slippage=0.0, stamp_tax=0.0)
    trade = bt.backtest(bars, "ma_cross", params).trades[0]
    assert trade.shares == 100  # 万元资金在 70 元价位只够一手
    assert 100 * trade.entry_price * params.commission < params.min_commission  # 比例佣金不足 5 元


def test_atr_stop_loss_exits_with_stop_reason() -> None:
    bars = _bars(_v_shape())
    entry_date = bt.backtest(bars, "ma_cross", _free()).trades[0].entry_date
    entry_index = next(index for index, bar in enumerate(bars) if bar["trade_date"] == entry_date)
    # 入场次日的深插针必然击穿 入场价 - 2×ATR（止损位在入场那根就已算好）
    bars[entry_index + 1]["low"] = 10.0
    params = bt.BacktestParams(
        slippage=0.0, commission=0.0, min_commission=0.0, stamp_tax=0.0, stop_loss_atr=2.0
    )
    without_stop = bt.backtest(bars, "ma_cross", _free()).trades[0]
    stopped = bt.backtest(bars, "ma_cross", params).trades[0]
    assert without_stop.exit_reason == "end"
    assert stopped.exit_reason == "stop"
    assert stopped.exit_date == bars[entry_index + 1]["trade_date"]
    assert stopped.exit_price < stopped.entry_price


# --------------------------------------------------------------------------------------
# buy_hold 基准与确定性
# --------------------------------------------------------------------------------------
def test_buy_hold_tracks_a_single_long_position() -> None:
    closes = [100.0 + index for index in range(50)]
    bars = _bars(closes)
    result = bt.backtest(bars, "buy_hold", _free())
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_date == bars[0]["trade_date"]
    assert trade.entry_price == pytest.approx(closes[0])
    # 从返回的 params 取初始资金，别写死——默认值调整过一次（10 万 → 100 万）
    cash = float(result.params["initial_cash"])  # type: ignore[arg-type]
    expected_shares = int(cash // (closes[0] * 100)) * 100
    assert trade.shares == expected_shares
    assert result.equity[-1].equity == pytest.approx(
        cash - expected_shares * closes[0] + expected_shares * closes[-1]
    )


def test_buy_hold_beats_crossing_strategy_in_a_pure_uptrend() -> None:
    """单边上涨里频繁进出的策略不该跑赢持有——顺带验证策略确实在交易。"""
    bars = _bars([100.0 + index * 1.2 for index in range(90)])
    hold = bt.backtest(bars, "buy_hold", _free())
    cross = bt.backtest(bars, "ma_cross", _free())
    assert hold.equity[-1].equity > cross.equity[-1].equity


def test_backtest_is_deterministic() -> None:
    bars = _bars(_v_shape())
    first = bt.backtest(bars, "macd_cross", bt.BacktestParams())
    second = bt.backtest(bars, "macd_cross", bt.BacktestParams())
    assert first.equity == second.equity
    assert first.trades == second.trades
    assert first.metrics == second.metrics


def test_timing_strategy_runs_off_the_shared_rule_table() -> None:
    bars = _bars(_v_shape())
    result = bt.backtest(bars, "timing", _free())
    assert len(result.equity) == len(bars)
    for point in result.equity:
        assert point.position in (0.0, 1.0)


# --------------------------------------------------------------------------------------
# 边界
# --------------------------------------------------------------------------------------
def test_unknown_strategy_is_rejected() -> None:
    with pytest.raises(ValueError):
        bt.backtest(_bars([1.0, 2.0]), "不存在的策略")


def test_too_few_bars_returns_empty_result() -> None:
    result = bt.backtest(_bars([100.0]), "ma_cross")
    assert result.equity == [] and result.trades == []
    assert result.metrics["bars"] == 0


def test_max_bars_caps_the_input() -> None:
    bars = _bars([100.0 + index for index in range(60)])
    result = bt.backtest(bars, "buy_hold", bt.BacktestParams(max_bars=20))
    assert len(result.equity) == 20


def test_default_cash_affords_one_lot_of_an_expensive_stock() -> None:
    """回归：默认资金买不起高价股一手时，全部策略都会变成一条直线。

    贵州茅台 ~1500 元/股，一手就是 15 万；默认 10 万的年代 `_enter` 算出 0 手直接返回，
    于是「策略没触发信号」和「资金不足」在结果里长得一模一样，页面默认标的直接空跑。
    """
    bars = _bars([value * 20 for value in _v_shape()])  # 1200~2400 元
    result = bt.backtest(bars, "ma_cross")
    assert result.trades, "默认资金必须买得起一手高价股"
    assert result.equity[-1].equity != result.params["initial_cash"]


def test_insufficient_cash_is_reported_instead_of_silently_flat() -> None:
    bars = _bars([value * 20 for value in _v_shape()])
    result = bt.backtest(bars, "ma_cross", bt.BacktestParams(initial_cash=10_000.0))
    assert result.trades == []
    assert result.equity[-1].equity == 10_000.0
    # 关键：空跑必须自曝，不能伪装成「没有信号」
    assert "买不起一手" in str(result.params["caveat"])


def test_affordable_run_has_no_cash_warning() -> None:
    result = bt.backtest(_bars(_v_shape()), "ma_cross")
    assert "买不起" not in str(result.params["caveat"])


def test_params_payload_documents_the_caveat() -> None:
    result = bt.backtest(_bars(_v_shape()), "buy_hold")
    assert "未复权" in str(result.params["caveat"])
    assert bt.strategy_names() == [
        "ma_cross",
        "macd_cross",
        "kdj_cross",
        "boll_reversion",
        "donchian",
        "trend_combo",
        "timing",
        "buy_hold",
    ]


# --------------------------------------------------------------------------------------
# 融合策略注册表：与既有 8 个平行，绝不改动 /quant/backtest 的契约
# --------------------------------------------------------------------------------------
def test_fused_strategies_extend_rather_than_replace_the_registry() -> None:
    assert bt.fused_strategy_names() == ["trend_follow", "mean_reversion", "fusion"]
    assert bt.all_strategy_names() == [*bt.strategy_names(), *bt.fused_strategy_names()]
    assert len(bt.all_strategy_names()) == 11
    for name in bt.all_strategy_names():
        assert bt.is_known_strategy(name)
        assert bt.STRATEGY_LABELS[name], name
    assert not bt.is_known_strategy("nope")


def test_unknown_strategy_still_raises() -> None:
    with pytest.raises(ValueError, match="未知策略"):
        bt.backtest(_bars(_v_shape()), "definitely_not_a_strategy")


def test_fused_strategies_are_backtestable() -> None:
    bars = _bars(_v_shape())
    for name in bt.fused_strategy_names():
        result = bt.backtest(bars, name, _free())
        assert len(result.equity) == len(bars), name


def test_plan_and_engine_use_the_same_rules() -> None:
    """页面上看到的信号 = 回测里成交的信号：每笔成交的前一根 bar 必须真的产 buy。

    这是两套代码共用一个 `signals` 模块的机械证明——若哪天计划改走"另一套更聪明的规则"，
    这条会失败。
    """
    bars = _bars(_spike_then_crash())
    result = bt.backtest(bars, "fusion", _free())
    assert result.trades, "构造的序列必须产生融合策略成交"
    closes = [float(bar["close"]) for bar in bars]
    series = indicator_series(
        [{"close": bar["close"], "high": bar["high"], "low": bar["low"]} for bar in bars]
    )
    index_by_date = {bar["trade_date"]: index for index, bar in enumerate(bars)}
    checked = 0
    for trade in result.trades:
        signal_index = index_by_date[trade.entry_date] - 1
        assert signal_index >= 0
        action = signal_rules.fused_action(
            "fusion", signal_rules.evaluate_signals(series, closes, signal_index)
        )
        assert action == "buy", f"{trade.entry_date} 建仓，但前一根 bar 的融合动作是 {action}"
        checked += 1
    assert checked >= 1


# --------------------------------------------------------------------------------------
# 移动止盈
# --------------------------------------------------------------------------------------
def test_trailing_stop_produces_trail_exits_only_when_enabled() -> None:
    bars = _bars(_spike_then_crash())
    with_trail = bt.backtest(bars, "ma_cross", _with_trail(2.0))
    assert any(trade.exit_reason == "trail" for trade in with_trail.trades), (
        "冲高后崩塌必须触发移动止盈"
    )
    without = bt.backtest(bars, "ma_cross", _free())
    assert not any(trade.exit_reason == "trail" for trade in without.trades)


def test_trailing_stop_cannot_see_the_current_bar_high() -> None:
    """同根 bar 未来函数：先抬水位再拿当根最低价去测，会在建仓那根自己身上出场。

    `_bars` 的 open == close，所以建仓那根的 high 被抬到 3 倍后，若实现写反了顺序，
    移动止盈位会高于当根最低价，`hold_bars == 0` 立刻出场——一个静默虚高所有趋势结果的 bug。
    """
    bars = _bars(_v_shape())
    plain = bt.backtest(bars, "ma_cross", _free())
    entry_index = next(
        index for index, bar in enumerate(bars) if bar["trade_date"] == plain.trades[0].entry_date
    )
    close = float(bars[entry_index]["close"])
    bars[entry_index]["high"] = close * 3  # 收盘价不变 → ma_cross 信号不变
    bars[entry_index]["low"] = close - 0.5  # 但最低价低于 3×close - 2×ATR

    result = bt.backtest(bars, "ma_cross", _with_trail(2.0))
    first = result.trades[0]
    assert first.entry_date == bars[entry_index]["trade_date"]
    # 建仓那根的 high_water 只能等于成交价，所以移动止盈位 = 成交价 - 2×ATR，
    # 绝不可能被当根最低价触及。写成 hold_bars == 0 的版本会让这笔交易以 0 盈亏立刻出场：
    # 一个把每笔趋势交易的持有期和收益都改掉的静默错误。
    assert first.hold_bars > 0, "建仓那根就被自己的最高价触发止损 = 用了当根 bar 的未来信息"
    assert first.exit_date != bars[entry_index]["trade_date"]


# --------------------------------------------------------------------------------------
# 期望值：诚实政策的内核
# --------------------------------------------------------------------------------------
def _trade(pnl_pct: float, *, hold: int = 10, reason: str = "signal") -> bt.Trade:
    return bt.Trade(
        entry_date=START,
        entry_price=100.0,
        shares=100.0,
        exit_date=START + timedelta(days=hold),
        exit_price=100.0 * (1 + pnl_pct / 100),
        pnl=pnl_pct * 100,
        pnl_pct=pnl_pct,
        hold_bars=hold,
        exit_reason=reason,
    )


def _result(trades: list[bt.Trade], **metrics: float) -> bt.BacktestResult:
    return bt.BacktestResult(
        equity=[],
        trades=trades,
        metrics={"bars": 500, **metrics},
        stats={},
        params={},
    )


def test_expectancy_matches_the_mean_of_trade_returns() -> None:
    exp = bt.trade_expectancy(
        _result([_trade(10.0), _trade(-5.0), _trade(3.0, reason="end")], cumulative_return=0.1),
        strategy="fusion",
        window_days=500,
        benchmark_return=0.05,
    )
    assert exp.expectancy_pct == pytest.approx((10.0 - 5.0 + 3.0) / 3)
    assert exp.win_rate == pytest.approx(2 / 3)
    assert exp.avg_win_pct == pytest.approx(6.5)
    assert exp.avg_loss_pct == pytest.approx(-5.0)
    assert exp.avg_hold_bars == pytest.approx(10.0)
    assert exp.expectancy_per_bar_pct == pytest.approx(exp.expectancy_pct / 10)
    assert exp.forced_end_trades == 1
    assert exp.benchmark_return == pytest.approx(0.05)
    assert exp.excess_return == pytest.approx(0.05)
    assert exp.strategy_label == bt.STRATEGY_LABELS["fusion"]
    assert "未复权" in exp.caveat
    assert "不可比" in exp.caveat  # 满仓口径与缩仓口径的差异必须写明


def test_a_handful_of_winning_trades_is_never_called_positive() -> None:
    """本功能要防的核心失败模式：6 笔成交的正期望被当成"策略有效"。"""
    exp = bt.trade_expectancy(_result([_trade(4.0)] * 6), strategy="fusion")
    assert exp.expectancy_pct is not None and exp.expectancy_pct > 0
    assert exp.verdict == "insufficient"
    assert "样本" in exp.verdict_text
    assert "勿据此下单" in exp.verdict_text


def test_a_handful_of_losing_trades_is_also_insufficient_not_negative() -> None:
    exp = bt.trade_expectancy(_result([_trade(-4.0)] * 6), strategy="fusion")
    assert exp.verdict == "insufficient"


def test_enough_trades_earns_a_real_verdict() -> None:
    winning = bt.trade_expectancy(_result([_trade(4.0), _trade(-1.0)] * 12), strategy="fusion")
    assert winning.trade_count == 24
    assert winning.verdict == "positive"
    losing = bt.trade_expectancy(_result([_trade(-4.0), _trade(1.0)] * 12), strategy="fusion")
    assert losing.trade_count == 24
    assert losing.verdict == "negative"
    assert "请勿据此下单" in losing.verdict_text


def test_expectancy_without_trades_is_insufficient_not_flattering() -> None:
    exp = bt.trade_expectancy(_result([]), strategy="fusion")
    assert exp.trade_count == 0
    assert exp.expectancy_pct is None
    assert exp.verdict == "insufficient"
    assert "一次都没触发" in exp.verdict_text
    assert "样本为空" in exp.verdict_text


def test_expectancy_of_a_real_backtest_matches_its_own_trades() -> None:
    bars = _bars(_spike_then_crash())
    result = bt.backtest(bars, "ma_cross", _free())
    exp = bt.trade_expectancy(result, strategy="ma_cross", window_days=500)
    assert exp.trade_count == len(result.trades)
    assert exp.expectancy_pct == pytest.approx(
        sum(trade.pnl_pct for trade in result.trades) / len(result.trades)
    )
    assert exp.bars == result.metrics["bars"]
