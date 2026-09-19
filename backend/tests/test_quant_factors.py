"""多因子横截面打分测试：因子提取、方向归一、分位标准化、缺失值与覆盖度语义。"""

from __future__ import annotations

import pytest
from app.services.quant import factors as f
from app.services.quant.indicators import indicator_series

ALL_FACTORS = (
    "momentum_20",
    "momentum_60",
    "reversal_5",
    "turnover",
    "value_ep",
    "value_bp",
    "quality_roe",
    "quality_growth",
    "sentiment",
    "low_vol",
)


def _ordered_panel(size: int = 10) -> dict[str, dict[str, float]]:
    """第 i 只股票在每个因子上都排第 i 名，便于断言分位。"""
    return {
        f"60000{index}.SH": {
            **{name: float(index) for name in ALL_FACTORS if name != "sentiment"},
            "sentiment": 50.0 + index,
        }
        for index in range(size)
    }


# --------------------------------------------------------------------------------------
# 因子定义与提取
# --------------------------------------------------------------------------------------
def test_factor_definitions_group_into_the_four_canonical_dimensions() -> None:
    groups = {definition.group for definition in f.FACTOR_DEFS}
    assert groups == set(f.GROUPS)
    assert sum(f.GROUP_WEIGHTS.values()) == pytest.approx(1.0)
    for group in f.GROUPS:
        weights = sum(item.weight for item in f.FACTOR_DEFS if item.group == group)
        assert weights == pytest.approx(1.0), group


def test_raw_factors_extracts_momentum_and_valuation() -> None:
    closes = [100.0 + index for index in range(80)]
    values = f.raw_factors(
        closes, {"pe_ttm": 20.0, "pb": 4.0, "turnover_rate": 3.0, "roe": 15.0}
    )
    # 动量 = (今收 / N 日前收 - 1) × 100
    assert values["momentum_20"] == pytest.approx((closes[-1] / closes[-21] - 1) * 100)
    assert values["momentum_60"] == pytest.approx((closes[-1] / closes[-61] - 1) * 100)
    assert values["value_ep"] == pytest.approx(0.05)
    assert values["value_bp"] == pytest.approx(0.25)
    assert values["quality_roe"] == 15.0
    assert values["turnover"] == 3.0
    assert values["low_vol"] < 0  # 单调上涨的波动极小，取负后仍应为负值之外的有限数
    assert values["sentiment"] is None  # 未传情绪


def test_raw_factors_returns_none_for_unavailable_inputs() -> None:
    values = f.raw_factors([100.0] * 5, {"pe_ttm": -3.0, "pb": 0.0}, None)
    assert values["momentum_20"] is None  # 不足 21 根
    assert values["value_ep"] is None  # 亏损股 PE 为负 → 不可用
    assert values["value_bp"] is None
    assert values["quality_roe"] is None
    assert values["sentiment"] is None


def test_raw_factors_sentiment_maps_absolutely() -> None:
    assert f.raw_factors([100.0] * 30, None, 0.6)["sentiment"] == pytest.approx(80.0)
    assert f.raw_factors([100.0] * 30, None, -1.0)["sentiment"] == pytest.approx(0.0)


def test_raw_factors_accepts_bars_derived_series() -> None:
    rows = [
        {"close": 100.0 + index, "high": 101.0 + index, "low": 99.0 + index}
        for index in range(40)
    ]
    series = indicator_series(rows)
    values = f.raw_factors([row["close"] for row in rows], None, None)
    assert values["low_vol"] is not None
    assert series["ma20"][-1] is not None


# --------------------------------------------------------------------------------------
# 横截面分位标准化
# --------------------------------------------------------------------------------------
def test_percentile_rank_spans_zero_to_hundred() -> None:
    results = f.panel_scores(_ordered_panel(10))
    lowest = results["600000.SH"]
    highest = results["600009.SH"]
    assert lowest.groups["波动风险"] == 0.0
    assert highest.groups["波动风险"] == 100.0
    # 趋势组里 reversal_5 方向为负，排名最低的股票反而拿到 100 分：
    # 0.35×0 + 0.30×0 + 0.20×100 + 0.15×0 = 20.0
    assert lowest.groups["趋势与动量"] == pytest.approx(20.0)
    assert highest.groups["趋势与动量"] == pytest.approx(80.0)


def test_direction_is_applied_so_that_lower_is_better_factor_flips() -> None:
    """reversal_5 的 direction=-1：原始值越小（跌得越多）分位越高。"""
    results = f.panel_scores(_ordered_panel(10))
    lowest = results["600000.SH"]
    highest = results["600009.SH"]
    entries = {item["factor"]: item for item in lowest.detail["趋势与动量"]["factors"]}
    top_entries = {item["factor"]: item for item in highest.detail["趋势与动量"]["factors"]}
    assert entries["reversal_5"]["raw"] == 0.0
    assert entries["reversal_5"]["score"] > top_entries["reversal_5"]["score"]
    assert entries["reversal_5"]["direction"] == -1


def test_sentiment_keeps_absolute_mapping_not_percentile() -> None:
    results = f.panel_scores(_ordered_panel(10))
    # 原始情绪 50+x 直接就是分数，不被横截面分位拉伸到 0/100
    assert results["600000.SH"].groups["新闻情绪"] == 50.0
    assert results["600009.SH"].groups["新闻情绪"] == 59.0


def test_single_stock_panel_is_defined() -> None:
    panel = {"600519.SH": {"momentum_20": 12.0, "sentiment": 50.0}}
    result = f.panel_scores(panel)["600519.SH"]
    assert result.groups["趋势与动量"] == 50.0  # 样本只有 1 只 → 中性分，而不是 None/崩溃


def test_ties_share_the_average_rank() -> None:
    panel = {f"60000{index}.SH": {"momentum_20": 5.0} for index in range(3)}
    scores = [f.panel_scores(panel)[code].groups["趋势与动量"] for code in panel]
    assert scores == [50.0, 50.0, 50.0]


def test_empty_panel_does_not_raise() -> None:
    assert f.panel_scores({}) == {}


# --------------------------------------------------------------------------------------
# 缺失值与覆盖度（保持与旧版一致的语义）
# --------------------------------------------------------------------------------------
def test_missing_factors_are_skipped_within_a_group() -> None:
    """组内缺失的因子按权重重归一，而不是当作 0 分把该股拉低。"""
    panel = {
        "A.SH": {"momentum_20": 10.0, "momentum_60": 10.0},
        "B.SH": {"momentum_20": -10.0},
    }
    results = f.panel_scores(panel)
    # B 只有 momentum_20，分位 0；该因子权重 0.35 单独归一后仍是 0
    assert results["B.SH"].groups["趋势与动量"] == 0.0
    # A 的 momentum_20 分位 100，momentum_60 在整个横截面上只有它一个样本 → 中性 50，
    # 于是组内加权 = (100×0.35 + 50×0.30) / 0.65
    assert results["A.SH"].groups["趋势与动量"] == pytest.approx(
        (100 * 0.35 + 50 * 0.30) / 0.65, abs=0.05
    )


def test_single_sample_factor_is_neutral_not_extreme() -> None:
    """只有一只股票有该因子时给中性 50，避免"独苗"直接吃满 100 分。"""
    panel = {"A.SH": {"quality_roe": 20.0}, "B.SH": {"quality_roe": 5.0}}
    results = f.panel_scores(panel)
    assert results["A.SH"].groups["质量与估值"] == 100.0
    lone = f.panel_scores({"A.SH": {"quality_roe": 20.0}, "B.SH": {}})["A.SH"]
    assert lone.groups["质量与估值"] == 50.0


def test_sentiment_only_stock_keeps_the_legacy_coverage_semantics() -> None:
    """只有情绪时 coverage=0.20 且不给综合分——与旧 score_stock 完全一致。"""
    result = f.panel_scores({"600519.SH": {"sentiment": 75.0}})["600519.SH"]
    assert result.coverage == 0.2
    assert result.total is None
    assert result.groups["新闻情绪"] == 75.0
    assert result.groups["趋势与动量"] is None


def test_stock_without_any_factor_has_zero_coverage() -> None:
    result = f.panel_scores({"600519.SH": dict.fromkeys(ALL_FACTORS)})["600519.SH"]
    assert result.coverage == 0.0
    assert result.total is None


def test_full_coverage_yields_total_and_always_four_groups() -> None:
    results = f.panel_scores(_ordered_panel(10))
    for result in results.values():
        assert result.coverage == 1.0
        assert result.total is not None
        assert set(result.groups) == set(f.GROUPS)
        assert 0 <= result.total <= 100


def test_total_is_the_weighted_average_of_group_scores() -> None:
    results = f.panel_scores(_ordered_panel(10))
    lowest = results["600000.SH"]
    # 趋势组 20（反转因子翻了方向）、估值组 0、波动组 0、情绪组 50
    expected = 20 * 0.4 + 0 * 0.3 + 50 * 0.2 + 0 * 0.1
    assert lowest.total == pytest.approx(expected, abs=0.05)
