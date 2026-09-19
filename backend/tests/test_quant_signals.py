"""择时信号层测试：交叉判定、超买超卖、ATR 风控位与加权评分。"""

from __future__ import annotations

import math

import pytest
from app.services.quant import signals as s
from app.services.quant.indicators import indicator_series


def _names(signals: list[s.Signal]) -> set[tuple[str, str]]:
    return {(signal.name, signal.direction) for signal in signals}


def test_ma_golden_cross_is_buy() -> None:
    series = {"ma5": [None, 1.0, 3.0], "ma20": [None, 2.0, 2.0]}
    found = s.evaluate_signals(series, [10.0, 10.0, 10.0], 2)
    assert ("ma_cross", "buy") in _names(found)
    assert found[0].detail == "短期均线上穿中期均线"


def test_ma_dead_cross_is_sell() -> None:
    series = {"ma5": [None, 3.0, 1.0], "ma20": [None, 2.0, 2.0]}
    found = s.evaluate_signals(series, [10.0, 10.0, 10.0], 2)
    assert ("ma_cross", "sell") in _names(found)


def test_no_signal_when_lines_do_not_cross() -> None:
    series = {"ma5": [None, 3.0, 4.0], "ma20": [None, 2.0, 2.0]}
    assert s.evaluate_signals(series, [10.0, 10.0, 10.0], 2) == []


def test_macd_golden_cross_and_histogram_flip() -> None:
    series = {
        "dif": [None, -0.5, 0.2],
        "dea": [None, 0.0, 0.1],
        "macd_hist": [None, -1.0, 0.4],
    }
    found = s.evaluate_signals(series, [10.0, 10.0, 10.0], 2)
    assert ("macd_cross", "buy") in _names(found)
    assert ("macd_hist_flip", "buy") in _names(found)


def test_kdj_cross_only_counts_in_oversold_zone() -> None:
    oversold = {"kdj_k": [None, 10.0, 25.0], "kdj_d": [None, 20.0, 20.0]}
    assert ("kdj_cross", "buy") in _names(s.evaluate_signals(oversold, [1.0] * 3, 2))

    # 同样形态但发生在中位区 → 视为噪声，不产生信号
    middle = {"kdj_k": [None, 50.0, 60.0], "kdj_d": [None, 55.0, 55.0]}
    assert s.evaluate_signals(middle, [1.0] * 3, 2) == []


def test_boll_breakout_requires_crossing_the_band() -> None:
    series = {
        "boll_up": [None, 10.0, 10.0],
        "boll_low": [None, 5.0, 5.0],
        "boll_mid": [None, 8.0, 8.0],
    }
    # 前收 9（在轨内）→ 现价 11（突破）
    found = s.evaluate_signals(series, [9.0, 9.0, 11.0], 2)
    assert ("boll_breakout", "buy") in _names(found)


def test_donchian_breakout_and_breakdown() -> None:
    up = {"donchian_up": [None, 10.0, 10.0], "donchian_low": [None, 5.0, 5.0]}
    assert ("donchian", "buy") in _names(s.evaluate_signals(up, [9.0, 9.0, 12.0], 2))

    down = {"donchian_up": [None, 10.0, 10.0], "donchian_low": [None, 5.0, 5.0]}
    assert ("donchian", "sell") in _names(s.evaluate_signals(down, [6.0, 6.0, 4.0], 2))


def test_rsi_extremes_use_the_same_thresholds_as_timing() -> None:
    over = {"rsi": [None, 50.0, 85.0]}
    assert ("rsi", "sell") in _names(s.evaluate_signals(over, [1.0] * 3, 2))
    under = {"rsi": [None, 50.0, 25.0]}
    assert ("rsi", "buy") in _names(s.evaluate_signals(under, [1.0] * 3, 2))
    normal = {"rsi": [None, 50.0, 55.0]}
    assert ("rsi", "buy") not in _names(s.evaluate_signals(normal, [1.0] * 3, 2))


def test_atr_stop_is_neutral_and_carries_a_price_level() -> None:
    series = {"atr14": [None, 1.0, 2.0]}
    found = s.evaluate_signals(series, [100.0, 100.0, 100.0], 2)
    stop = next(signal for signal in found if signal.name == "atr_stop")
    assert stop.direction == "neutral"
    assert stop.level == pytest.approx(96.0)  # 100 - 2×2
    assert stop.strength == 0.0


def test_signal_score_is_neutral_when_buy_and_sell_balance() -> None:
    balanced = [
        s.Signal("ma_cross", "x", "buy", "", 0.25, 0, 1.0),
        s.Signal("macd_cross", "y", "sell", "", 0.25, 0, 1.0),
    ]
    assert s.signal_score(balanced) == pytest.approx(50.0)


def test_signal_score_ignores_neutral_and_returns_none_without_rules() -> None:
    only_stop = [s.Signal("atr_stop", "z", "neutral", "", 0.0, 0, 1.0, level=9.0)]
    assert s.signal_score(only_stop) is None
    all_buy = [s.Signal("ma_cross", "x", "buy", "", 0.25, 0, 1.0)]
    assert s.signal_score(all_buy) == pytest.approx(100.0)


def test_families_partition_every_scored_rule() -> None:
    """新增规则忘了分族，会在这里失败——而不是让它静默地永远不参与打分。"""
    scored = set(s._SIGNAL_WEIGHTS) - {"atr_stop"}
    assert set(s.TREND_RULES) | set(s.REVERSION_RULES) == scored
    assert not set(s.TREND_RULES) & set(s.REVERSION_RULES)  # 互斥
    assert s.FAMILY_WEIGHTS.keys() == {"trend", "reversion"}


def _sig(name: str, direction: str) -> s.Signal:
    return s.Signal(name, name, direction, "", s._SIGNAL_WEIGHTS[name], 0, 1.0)  # type: ignore[arg-type]


def test_family_score_is_neutral_without_signals() -> None:
    """无信号是中性 50 而不是 None——None 会让 UI 显示"—"，掩盖"没有证据"这件事。"""
    empty = s.family_score([], s.TREND_RULES)
    assert empty.score == pytest.approx(50.0)
    assert empty.direction == "hold"
    assert empty.rules == ()


def test_family_score_spans_zero_to_hundred() -> None:
    all_buy = s.family_score([_sig(name, "buy") for name in s.TREND_RULES], s.TREND_RULES)
    all_sell = s.family_score([_sig(name, "sell") for name in s.TREND_RULES], s.TREND_RULES)
    assert all_buy.score == pytest.approx(100.0)
    assert all_sell.score == pytest.approx(0.0)


def test_a_single_rule_can_never_reach_the_buy_threshold() -> None:
    """趋势族单条最大贡献 0.25/1.05 → 61.9，够不到 65：这是"单一指标不构成买入理由"。"""
    one = s.family_score([_sig("ma_cross", "buy")], s.TREND_RULES)
    assert one.score == pytest.approx(61.904, abs=1e-3)
    assert one.score < s.BUY_SCORE
    assert one.direction == "hold"


def test_two_confirming_rules_reach_the_buy_threshold() -> None:
    two = s.family_score([_sig("ma_cross", "buy"), _sig("donchian", "buy")], s.TREND_RULES)
    assert two.score == pytest.approx(71.428, abs=1e-3)
    assert len(two.buy_rules) == s.MIN_CONFIRM_RULES
    assert two.direction == "buy"


def test_reversion_needs_two_rules_even_though_kdj_alone_clears_the_line() -> None:
    """回归族只有 3 条规则，kdj_cross 一条就 71.4 分——所以族结论另设条数门槛。"""
    alone = s.family_score([_sig("kdj_cross", "buy")], s.REVERSION_RULES)
    assert alone.score == pytest.approx(71.428, abs=1e-3)
    assert alone.score >= s.BUY_SCORE
    assert alone.direction == "hold"  # 分数够线但只有一条规则
    pair = s.family_score([_sig("kdj_cross", "buy"), _sig("rsi", "buy")], s.REVERSION_RULES)
    assert pair.direction == "buy"


def test_opposing_rules_cancel_to_neutral() -> None:
    both = s.family_score([_sig("ma_cross", "buy"), _sig("macd_cross", "sell")], s.TREND_RULES)
    assert both.score == pytest.approx(50.0)
    assert both.buy_rules == ("ma_cross",)
    assert both.sell_rules == ("macd_cross",)
    assert both.direction == "hold"


def test_a_rule_that_fires_both_ways_confirms_neither_side() -> None:
    """kdj_cross 可以同根 bar 既低位金叉又超买，净贡献抵消——不该被算作任何一侧的确认。"""
    crossed = s.family_score(
        [_sig("kdj_cross", "buy"), _sig("kdj_cross", "sell"), _sig("rsi", "buy")],
        s.REVERSION_RULES,
    )
    assert crossed.buy_rules == ("rsi",)
    assert crossed.sell_rules == ()
    assert crossed.direction == "hold"  # 只剩 rsi 一条，不够 MIN_CONFIRM_RULES


def test_signal_score_keeps_its_present_weight_denominator() -> None:
    """UI 不能踩的陷阱：同一条 rsi 买入，信号强度读 100，族分只读 64.3。

    `signal_score` 除以在场权重和（"只有它触发"= 满量程），`family_score` 除以全族权重和
    （向 50 漂移）。两者会同时出现在产品里，语义不同、都不能改。
    """
    lone_rsi = [_sig("rsi", "buy")]
    assert s.signal_score(lone_rsi) == pytest.approx(100.0)
    assert s.family_score(lone_rsi, s.REVERSION_RULES).score == pytest.approx(64.285, abs=1e-3)
    assert s.signal_score(lone_rsi) != s.family_score(lone_rsi, s.REVERSION_RULES).score


def test_fusion_weights_trend_more_than_reversion() -> None:
    scores = {
        "trend": s.FamilyScore("trend", 100.0, 0, 0, (), (), "buy"),
        "reversion": s.FamilyScore("reversion", 50.0, 0, 0, (), (), "hold"),
    }
    assert s.fusion_score(list(scores.values())) == pytest.approx(80.0)  # 0.6×100 + 0.4×50


def test_fused_action_holds_when_the_two_families_disagree() -> None:
    """趋势喊买、回归喊卖 → 融合分落到 50 附近，两条分支都不命中 → hold。"""
    conflicting = [
        _sig("ma_cross", "buy"),
        _sig("donchian", "buy"),
        _sig("kdj_cross", "sell"),
        _sig("rsi", "sell"),
    ]
    assert s.fused_action("trend_follow", conflicting) == "buy"
    assert s.fused_action("mean_reversion", conflicting) == "sell"
    assert s.fused_action("fusion", conflicting) == "hold"


def test_fused_action_reaches_buy_only_when_the_fusion_score_holds_up() -> None:
    """回归族中性(50)时融合买入需趋势分 ≥70；单靠趋势族两条规则(71.4)刚好够。"""
    trend_only = [_sig("ma_cross", "buy"), _sig("donchian", "buy")]
    assert s.fused_action("fusion", trend_only) == "buy"  # 0.6×71.43 + 0.4×50 = 62.86 ≥ 62
    weaker = [_sig("donchian", "buy")]
    assert s.fused_action("fusion", weaker) == "hold"


def test_fused_action_is_unknown_strategy_safe() -> None:
    assert s.fused_action("nope", [_sig("ma_cross", "buy")]) == "hold"


def test_latest_signals_reads_the_last_bar() -> None:
    series = {"ma5": [None, 1.0, 3.0], "ma20": [None, 2.0, 2.0]}
    assert s.latest_signals(series, [10.0, 10.0, 10.0]) == s.evaluate_signals(
        series, [10.0, 10.0, 10.0], 2
    )


def test_signals_from_real_series_are_reproducible() -> None:
    """同一份指标序列必须产出同样的信号——回测与 UI 面板共用的前提。"""
    rows = [
        {
            "close": 100 + math.sin(index / 5.0) * 8 + index * 0.2,
            "high": 101 + math.sin(index / 5.0) * 8 + index * 0.2,
            "low": 99 + math.sin(index / 5.0) * 8 + index * 0.2,
            "volume": 1000.0 + index,
        }
        for index in range(120)
    ]
    series = indicator_series(rows)
    closes = [row["close"] for row in rows]
    first = s.latest_signals(series, closes)
    second = s.latest_signals(series, closes)
    assert first == second
    for signal in first:
        assert signal.direction in {"buy", "sell", "neutral"}
        assert signal.detail
        assert signal.bar_index == len(closes) - 1
