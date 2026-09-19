"""买卖计划内核测试：价格几何、仓位公式、流水账回放，以及诚实门槛。

诚实门槛是本模块存在的理由，所以那几条用例是**确定性**的：注入一个期望值，断言动作
必须跟着变——入场信号全满足时也不允许把负期望渲染成"买入"。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from app.services.quant import backtest as bt
from app.services.quant import decision as decision_mod
from app.services.quant import signals as signal_rules

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


def _weak_signal_series(count: int = 120) -> list[dict[str, object]]:
    """稳步上涨：只有 ma_trend 一条规则触发（趋势 54.8），融合 52.9 够不到 62。

    这里顺带固化一个真实行为，不是 fixture 的巧合：单边上涨时回归族读到超买给出 28.6，
    与趋势族的 64.3 相互抵消，融合分落在 50.0——**涨得最顺的形态反而拿不到买入结论**。
    融合门槛刻意做得难以满足，这是纪律而不是缺陷。
    """
    return _bars([100.0 + index * 0.35 for index in range(count)])


def _accelerating_rally(count: int = 120) -> list[dict[str, object]]:
    """加速上涨：趋势族 64.3（ma_trend + donchian 两条确认，差 0.7 分未过线），
    同时 KDJ 打进超买让回归族读到 28.6 喊卖。两族抵消后融合分**恰好 50.0**。
    """
    return _bars([100.0 + (index**1.7) * 0.06 for index in range(count)])


def _oversold_decline(count: int = 120) -> list[dict[str, object]]:
    """稳步下跌把 KDJ 与 RSI 打进超卖区，回归族因此给出买入（85.7，2 条规则确认），
    融合 64.3 ≥ 62 → **信号侧 4 条条件全部满足**。这是能构造出"信号齐备"的形状。
    """
    return _bars([200.0 - index * 0.55 for index in range(count)])


def _volatile_uptrend(count: int = 120) -> list[dict[str, object]]:
    """同样的上行斜率但日间振幅大得多——波动上限法必须因此减少股数。"""
    closes = [100.0 + index * 0.35 + (12.0 if index % 2 else -12.0) for index in range(count)]
    return _bars(closes, spread=1.0)


def _expectancy(
    *,
    trade_count: int = 24,
    expectancy_pct: float = -2.31,
    avg_hold: float = 12.0,
    verdict: str = "negative",
) -> bt.Expectancy:
    return bt.Expectancy(
        strategy="fusion",
        strategy_label="多策略融合",
        window_days=500,
        bars=500,
        trade_count=trade_count,
        win_rate=0.4,
        avg_win_pct=4.0,
        avg_loss_pct=-4.2,
        profit_factor=0.63,
        expectancy_pct=expectancy_pct,
        expectancy_per_bar_pct=expectancy_pct / avg_hold,
        avg_hold_bars=avg_hold,
        forced_end_trades=1,
        cumulative_return=-0.22,
        max_drawdown=-0.31,
        benchmark_return=-0.05,
        excess_return=-0.17,
        verdict=verdict,
        verdict_text="该规则实测 24 笔，每笔期望 -2.31%；按此规则操作的历史结果是亏钱的。",
        caveat=bt.EXPECTANCY_CAVEAT,
    )


def _plan(bars: list[dict[str, object]], expectancy: bt.Expectancy | None = None, **kwargs):
    return decision_mod.build_plan(
        bars,
        ts_code="600519.SH",
        name="贵州茅台",
        expectancy=expectancy or _expectancy(),
        **kwargs,
    )


# ======================================================================================
# 价格几何
# ======================================================================================
def test_entry_zone_never_starts_below_the_stop() -> None:
    """把入场价定在止损之下 = 一买就触发止损。这是必须结构性堵死的。"""
    for bars in (_weak_signal_series(), _oversold_decline(), _volatile_uptrend()):
        plan = _plan(bars)
        assert plan.entry_zone.low >= plan.stop_loss.recommended
        assert plan.entry_zone.low <= plan.entry_zone.high


def test_stop_is_the_nearer_of_atr_and_structure() -> None:
    plan = _plan(_weak_signal_series())
    stop = plan.stop_loss
    assert stop.source == "atr"  # 单边上涨时 20 日低点更远，ATR 位更近
    assert stop.atr_level is not None and stop.structure_level is not None
    assert stop.recommended == max(stop.atr_level, stop.structure_level)
    assert stop.recommended < plan.price
    assert stop.distance_pct < 0
    # 止损用的必须是同一个 2×ATR 常量，页面数字才能与回测执行的一致
    atr = plan.price - stop.atr_level
    assert atr > 0


def test_stop_falls_back_when_no_level_is_usable() -> None:
    """结构位与 ATR 位都不可用（这里用极短序列逼出 ATR 缺失）时不能返回 None。"""
    plan = _plan(_bars([100.0] * 22))
    assert plan.stop_loss.recommended > 0
    assert plan.stop_loss.recommended < plan.price
    assert plan.stop_loss.note


def test_trailing_take_profit_is_the_only_target() -> None:
    plan = _plan(_weak_signal_series())
    assert plan.take_profit.level is not None
    assert plan.take_profit.level < plan.take_profit.high_water
    assert "建仓后会按实际" in plan.take_profit.note


# ======================================================================================
# 仓位公式
# ======================================================================================
def test_sizing_respects_the_risk_budget_and_lot_size() -> None:
    plan = _plan(_weak_signal_series())
    sizing = plan.sizing
    assert sizing.shares > 0
    assert sizing.shares % decision_mod.LOT == 0
    assert sizing.lots == sizing.shares // decision_mod.LOT
    # 风险预算是硬上限，允许一个整手的取整余量
    budget = 1_000_000.0 * decision_mod.RISK_PCT
    assert sizing.risk_amount <= budget + plan.price * decision_mod.LOT
    assert sizing.weight_pct <= 20.0  # 波动最低档的仓位上限
    assert "①" in sizing.formula and "③" in sizing.formula


def test_higher_volatility_means_a_smaller_position() -> None:
    calm = _plan(_weak_signal_series()).sizing
    wild = _plan(_volatile_uptrend()).sizing
    assert wild.shares <= calm.shares


def test_a_zero_position_from_the_discount_does_not_claim_you_cannot_afford_it() -> None:
    """两种"取整手为 0"必须分开说。

    高分档本金下，把仓位归零的真正原因是**融合分太低**（置信度折扣 0.4 倍），账户其实
    买得起。旧文案在这里会写"这套风险纪律下买不起"——一句可验证的假话，而且刚好把
    "证据不足"这个真正的信息盖掉。
    """
    weak = decision_mod._sizing(1500.0, 1458.0, 1_000_000.0, 45.0, 25.0)
    assert weak.shares == 0
    assert "买不起" not in weak.note
    assert "证据不足" in weak.note
    assert "融合分 45.0" in weak.note  # 说清是哪一条没过
    assert "账户买得起" in weak.note
    # ①②本身是给得出股数的，是折扣把它归零的——公式里能看到 0 出现在第③步
    assert "① 风险预算" in weak.formula
    assert weak.formula.rstrip().endswith("股")

    # 对照：融合分够高时同一组价位应当给得出整手
    strong = decision_mod._sizing(1500.0, 1458.0, 1_000_000.0, 75.0, 25.0)
    assert strong.shares == 100
    assert strong.affordable is True


def test_a_risk_budget_below_one_lot_does_not_claim_you_cannot_afford_it() -> None:
    """第三种归零：一手的风险就超过 1% 预算——账户买得起，是纪律不允许。

    这是茅台的**真实**数字（1258 元 / 止损 1157.36 元 / 100 万本金），线上默认页就是它。
    旧文案在这里写"这套风险纪律下买不起"，而一手只要 12.58 万、账户有 100 万——
    用户一句话就能推翻，然后整页的可信度跟着走。
    """
    sizing = decision_mod._sizing(1258.0, 1157.36, 1_000_000.0, 50.0, 28.0)
    assert sizing.shares == 0
    assert sizing.affordable is False
    assert "买不起" not in sizing.note
    assert "账户买得起这一手" in sizing.note
    assert "10,064" in sizing.note  # 一手的风险，用户可手算
    assert "把止损放宽来凑股数" in sizing.note  # 唯一的"解法"必须被明确否掉
    # ④波动上限那一步是有股数的，公式里要能看出来
    assert "② 波动上限" in sizing.formula
    assert "= 100 股" in sizing.formula


def test_a_stop_at_or_above_the_entry_price_gives_no_position() -> None:
    """退化情形：止损不低于入场价时不能算出"负风险"的股数，也不能编一个理由。"""
    sizing = decision_mod._sizing(100.0, 100.0, 1_000_000.0, 80.0, 25.0)
    assert sizing.shares == 0
    assert "风险预算无从计算" in sizing.note


def test_an_unaffordable_position_says_so_instead_of_showing_zero() -> None:
    """茅台的场景：按 1% 风险预算与波动上限，一手就吃掉大半本金。"""
    bars = _bars([1800.0 + index * 0.5 for index in range(120)])
    plan = _plan(bars, equity=30_000.0)
    sizing = plan.sizing
    assert sizing.shares == 0
    assert sizing.affordable is False
    assert "买不起" in sizing.note
    assert "没有信号" in sizing.note  # 明确区分"没信号"和"买不起"


def test_confidence_discount_shrinks_the_position_when_scores_are_weak() -> None:
    """同样的价格与波动，分数越低仓位越小——证据薄弱时自动少下注。"""
    strong = decision_mod._sizing(100.0, 95.0, 1_000_000.0, 90.0, 25.0)
    weak = decision_mod._sizing(100.0, 95.0, 1_000_000.0, 52.0, 25.0)
    assert weak.shares < strong.shares


# ======================================================================================
# 入场条件与失效条件
# ======================================================================================
def test_plan_always_shows_all_five_entry_conditions() -> None:
    plan = _plan(_weak_signal_series())
    keys = [item.key for item in plan.entry_conditions]
    assert keys == ["score_gate", "family_gate", "not_extended", "above_stop", "expectancy_gate"]
    for item in plan.entry_conditions:
        assert item.label and item.detail


def test_plan_always_shows_all_five_exit_rules() -> None:
    plan = _plan(_weak_signal_series())
    keys = [item.key for item in plan.exit_rules]
    assert keys == ["fusion_exit", "stop_hit", "trail_hit", "trend_break", "time_stop"]


def test_trail_rule_fires_after_a_spike_and_collapse() -> None:
    """冲高后崩塌：移动止盈条件必须为真。"""
    closes = [100.0 + index * 0.9 for index in range(90)]
    closes += [closes[-1] - index * 6.0 for index in range(1, 30)]
    plan = _plan(_bars(closes))
    trail = next(item for item in plan.exit_rules if item.key == "trail_hit")
    assert trail.triggered, plan.take_profit


def test_score_history_is_bounded_and_ordered() -> None:
    plan = _plan(_weak_signal_series(), score_history_bars=30)
    assert len(plan.score_history) == 30
    dates = [point.trade_date for point in plan.score_history]
    assert dates == sorted(dates)
    for point in plan.score_history:
        assert 0 <= point.trend <= 100
        assert 0 <= point.fusion <= 100


def test_expected_hold_is_labelled_heuristic_when_the_sample_is_thin() -> None:
    thin = _plan(
        _weak_signal_series(), expectancy=_expectancy(trade_count=6, verdict="insufficient")
    )
    assert thin.expected_hold.basis == "heuristic"
    assert thin.expected_hold.bars is None
    assert "经验值" in thin.expected_hold.note

    rich = _plan(_weak_signal_series(), expectancy=_expectancy(trade_count=24))
    assert rich.expected_hold.basis == "measured"
    assert rich.expected_hold.bars == 12


# ======================================================================================
# 诚实门槛（内核层，确定性）
# ======================================================================================
def test_negative_expectancy_forbids_a_buy_verdict() -> None:
    """入场条件全满足也不许说买入——负期望是唯一的否决理由，这才是钉子。"""
    plan = _plan(
        _oversold_decline(),
        expectancy=_expectancy(trade_count=24, expectancy_pct=-2.31, verdict="negative"),
    )
    signal_side = [item for item in plan.entry_conditions if item.key != "expectancy_gate"]
    assert all(item.satisfied for item in signal_side), "构造的序列必须真的满足信号侧全部条件"
    gate = next(item for item in plan.entry_conditions if item.key == "expectancy_gate")
    assert gate.satisfied is False
    assert "-2.31" in gate.detail
    assert plan.action == "avoid"
    assert "不建议" in plan.action_label
    assert "-2.31%" in plan.action_label


def test_positive_expectancy_permits_a_buy_when_signals_agree() -> None:
    plan = _plan(
        _oversold_decline(),
        expectancy=_expectancy(trade_count=24, expectancy_pct=1.9, verdict="positive"),
    )
    assert all(item.satisfied for item in plan.entry_conditions)
    assert plan.action == "buy"


def test_insufficient_sample_watches_instead_of_buying() -> None:
    """6 笔成交的正期望也必须只给观望——本功能要防的核心失败模式。"""
    plan = _plan(
        _oversold_decline(),
        expectancy=_expectancy(trade_count=6, expectancy_pct=4.2, verdict="insufficient"),
    )
    assert plan.action == "watch"
    assert "6 笔" in plan.action_label
    assert plan.action != "buy"


def test_weak_signals_watch_regardless_of_a_positive_record() -> None:
    """实测再好，信号没触发也不买——两个门槛是「与」的关系，不是「或」。"""
    plan = _plan(
        _weak_signal_series(),
        expectancy=_expectancy(trade_count=30, expectancy_pct=3.0, verdict="positive"),
    )
    assert plan.action == "watch"
    assert plan.action_label == "未满足入场条件，继续等待"


def test_the_strongest_uptrend_still_gets_no_buy_verdict() -> None:
    """一个值得记住的真实行为：涨得越顺，越拿不到买入结论。

    加速上涨让趋势族拿到 64.3——ma_trend 与 donchian 两条规则都确认了，**却仍差 0.7 分
    过不了 65 的线**；同时 KDJ 超买让回归族读到 28.6 喊卖。两族抵消后融合分恰好 50.0，
    连"信号侧"都过不了。实测期望再漂亮（这里注入 +5.00% / 40 笔）也不会变成买入。

    这不是缺陷：融合门槛刻意做得难以满足，而"两条规则确认的强趋势"被"超买"否掉，
    正是这个页面对追高的态度。
    """
    plan = _plan(
        _accelerating_rally(),
        expectancy=_expectancy(trade_count=40, expectancy_pct=5.0, verdict="positive"),
    )
    assert plan.scores.trend.buy_rules == ("ma_trend", "donchian")
    assert plan.scores.trend.score < signal_rules.BUY_SCORE  # 两条确认仍差一点
    assert plan.scores.reversion.score < signal_rules.SELL_SCORE  # 回归族在喊卖
    assert plan.scores.fusion.score < signal_rules.FUSION_BUY_SCORE
    assert all(item.satisfied for item in plan.entry_conditions if item.key == "expectancy_gate")
    assert plan.action == "watch"


def test_zero_trade_record_is_never_flattering() -> None:
    exp = bt.trade_expectancy(bt.backtest(_weak_signal_series(), "fusion"), strategy="fusion")
    plan = _plan(_weak_signal_series(), expectancy=exp)
    assert plan.action != "buy"
    assert exp.trade_count == 0
    assert "样本为空" in exp.verdict_text
    assert "无成交" in next(
        item for item in plan.entry_conditions if item.key == "expectancy_gate"
    ).detail


# ======================================================================================
# 流水账
# ======================================================================================
def _entry(side: str, day: int, price: float, shares: float, fee: float = 0.0):
    return decision_mod.LedgerEntry(
        side=side,
        trade_date=START + timedelta(days=day),
        price=price,
        shares=shares,
        fee=fee,
    )


def test_empty_ledger_summarizes_to_nothing() -> None:
    assert decision_mod.summarize_ledger([]) is None


def test_weighted_average_cost_across_multiple_buys() -> None:
    holding = decision_mod.summarize_ledger(
        [_entry("buy", 0, 100.0, 100), _entry("buy", 5, 120.0, 300)]
    )
    assert holding is not None
    assert holding.shares == 400
    assert holding.avg_cost == pytest.approx((100 * 100 + 120 * 300) / 400)  # 115
    assert holding.cost == pytest.approx(46_000)
    assert holding.realized_pnl == 0.0
    assert holding.first_entry_date == START


def test_selling_does_not_move_the_average_cost() -> None:
    """券商口径：卖出只减股数，不改均价——否则已实现盈亏会重复计入。"""
    holding = decision_mod.summarize_ledger(
        [_entry("buy", 0, 100.0, 200), _entry("buy", 1, 120.0, 200), _entry("sell", 2, 130.0, 100)]
    )
    assert holding is not None
    assert holding.avg_cost == pytest.approx(110.0)
    assert holding.shares == 300
    assert holding.cost == pytest.approx(33_000)
    assert holding.realized_pnl == pytest.approx((130.0 - 110.0) * 100)


def test_realized_pnl_accumulates_across_sells_and_nets_out_fees() -> None:
    holding = decision_mod.summarize_ledger(
        [
            _entry("buy", 0, 100.0, 200, fee=5.0),
            _entry("sell", 1, 110.0, 100, fee=3.0),
            _entry("sell", 2, 90.0, 100, fee=3.0),
        ]
    )
    assert holding is not None
    average = (200 * 100.0 + 5.0) / 200
    expected = (110.0 - average) * 100 - 3.0 + (90.0 - average) * 100 - 3.0
    assert holding.realized_pnl == pytest.approx(expected)
    assert holding.shares == 0
    assert holding.avg_cost == 0.0


def test_ledger_is_replayed_in_date_order_not_insertion_order() -> None:
    holding = decision_mod.summarize_ledger(
        [_entry("sell", 10, 130.0, 100), _entry("buy", 0, 100.0, 100)]
    )
    assert holding is not None
    assert holding.shares == 0
    assert holding.realized_pnl == pytest.approx(30.0 * 100)


# ======================================================================================
# 持仓建议
# ======================================================================================
def _advise(bars, trades, **kwargs):
    return decision_mod.advise_position(
        bars,
        trades,
        ts_code="600519.SH",
        name="贵州茅台",
        expectancy=_expectancy(),
        **kwargs,
    )


def test_position_below_stop_advises_exiting() -> None:
    bars = _weak_signal_series()
    price = float(bars[-1]["close"])
    advice = _advise(bars, [_entry("buy", 0, price, 100)], user_stop=price + 1.0)
    assert advice.stop_source == "user"
    assert advice.stop_triggered
    assert advice.action == "exit"
    assert "跌破止损" in advice.action_label


def test_open_position_without_a_user_stop_uses_the_rule_stop() -> None:
    bars = _weak_signal_series()
    advice = _advise(bars, [_entry("buy", 0, 100.0, 100)])
    assert advice.stop_source == "rule"
    assert advice.stop_price is not None
    assert advice.stop_price < advice.price


def test_pnl_and_stop_distance_are_reported() -> None:
    bars = _weak_signal_series()
    price = float(bars[-1]["close"])
    advice = _advise(bars, [_entry("buy", 0, price * 0.9, 200)])
    assert advice.pnl_pct == pytest.approx((price / (price * 0.9) - 1) * 100, abs=0.01)
    assert advice.pnl is not None and advice.pnl > 0
    assert advice.stop_distance_pct is not None and advice.stop_distance_pct < 0
    assert advice.hold_bars > 0


def test_closed_position_reports_realized_pnl_instead_of_advice() -> None:
    bars = _weak_signal_series()
    advice = _advise(bars, [_entry("buy", 0, 100.0, 100), _entry("sell", 20, 120.0, 100)])
    assert advice.status == "closed"
    assert advice.shares == 0
    assert advice.action == "watch"
    assert advice.realized_pnl == pytest.approx(2000.0)
    assert "已实现盈亏" in advice.reasons[0]


def test_position_without_local_bars_degrades_instead_of_fabricating() -> None:
    advice = _advise([], [_entry("buy", 0, 100.0, 100)])
    assert advice.action == "watch"
    assert advice.scores is None
    assert advice.data_warning
    assert advice.shares == 100  # 持仓数字本身不受缺数据影响
    assert advice.realized_pnl == 0.0


def test_position_advice_never_returns_a_fabricated_score() -> None:
    """日线不足 MIN_BARS 时宁可没有分数，也不能算一个出来。"""
    advice = _advise(_bars([100.0] * 5), [_entry("buy", 0, 100.0, 100)])
    assert advice.scores is None
    assert advice.action == "watch"
    assert advice.data_warning
