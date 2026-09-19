"""风险/绩效指标的纯函数测试：闭式解 + 边界（样本不足、零方差、基准对齐）。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from app.services.quant import performance as q


def _dates(count: int, start: date = date(2026, 1, 1)) -> list[date]:
    return [start + timedelta(days=index) for index in range(count)]


def _ramp(count: int, step: float = 1.0, base: float = 100.0) -> list[float]:
    return [base + index * step for index in range(count)]


# --------------------------------------------------------------------------------------
# 日期归一化：TuShare 返回 YYYYMMDD、Mock 返回 YYYY-MM-DD，混用会让基准静默为空
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20260831", date(2026, 8, 31)),
        ("2026-08-31", date(2026, 8, 31)),
        (date(2026, 8, 31), date(2026, 8, 31)),
        ("  20260831 ", date(2026, 8, 31)),
    ],
)
def test_parse_date_accepts_both_provider_formats(raw: object, expected: date) -> None:
    assert q.parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", "2026-13-45", "20261345", None, 20260831, "abc"])
def test_parse_date_rejects_garbage(raw: object) -> None:
    assert q.parse_date(raw) is None


def test_benchmark_map_normalizes_mixed_formats() -> None:
    mapping = q.benchmark_map(
        [
            {"date": "20260831", "close": 3200.0},
            {"date": "2026-09-01", "close": 3210.0},
            {"date": "bad", "close": 1.0},
            {"date": "20260902", "close": "不是数字"},
        ]
    )
    assert mapping == {date(2026, 8, 31): 3200.0, date(2026, 9, 1): 3210.0}


# --------------------------------------------------------------------------------------
# 最大回撤
# --------------------------------------------------------------------------------------
def test_max_drawdown_on_v_shape() -> None:
    result = q.max_drawdown([100.0, 120.0, 60.0, 90.0])
    assert result is not None
    drawdown, peak, trough, recovered = result
    assert drawdown == pytest.approx(-0.5)
    assert peak == 1
    assert trough == 2
    assert recovered == 3


def test_max_drawdown_is_zero_when_never_declining() -> None:
    result = q.max_drawdown(_ramp(30))
    assert result is not None
    assert result[0] == 0.0


def test_drawdown_series_tracks_running_peak() -> None:
    series = q.drawdown_series([100.0, 120.0, 60.0, 90.0])
    assert series == pytest.approx([0.0, 0.0, -0.5, -0.25])


# --------------------------------------------------------------------------------------
# 收益与风险
# --------------------------------------------------------------------------------------
def test_annualized_and_cumulative_return_have_closed_form() -> None:
    # 127 根 → 126 个收益，翻倍；年化 = 2^(252/126) - 1 = 300%
    closes = [100.0 * 2 ** (index / 126) for index in range(127)]
    metrics = q.performance_metrics(closes)
    assert metrics["bars"] == 127
    assert metrics["cumulative_return"] == pytest.approx(1.0, abs=1e-9)
    assert metrics["annualized_return"] == pytest.approx(3.0, abs=1e-6)


def test_monotone_rise_has_zero_drawdown_and_no_calmar() -> None:
    metrics = q.performance_metrics(_ramp(60))
    assert metrics["max_drawdown"] == 0.0
    assert metrics["calmar"] is None  # 无回撤时卡玛无定义，返回 None 而不是除零


def test_win_rate_counts_positive_days() -> None:
    closes = [100.0]
    for delta in [1.0, 1.0, -1.0, 1.0]:
        closes.append(closes[-1] + delta)
    metrics = q.performance_metrics(_ramp(21) + closes)
    assert metrics["win_rate"] is not None
    assert 0 <= float(metrics["win_rate"]) <= 1


def test_zero_variance_series_returns_none_ratios_not_crash() -> None:
    metrics = q.performance_metrics([100.0] * 30)
    assert metrics["sharpe"] is None
    assert metrics["sortino"] is None
    assert metrics["annualized_volatility"] == pytest.approx(0.0)


def test_risk_free_rate_lowers_sharpe() -> None:
    closes = [100.0 * (1.002**index) for index in range(60)]
    without = q.performance_metrics(closes, risk_free=0.0)
    with_rf = q.performance_metrics(closes, risk_free=0.05)
    assert without["sharpe"] is not None and with_rf["sharpe"] is not None
    assert float(with_rf["sharpe"]) < float(without["sharpe"])


def test_insufficient_sample_returns_none_metrics() -> None:
    metrics = q.performance_metrics(_ramp(19))
    assert metrics["bars"] == 19
    for key in ("sharpe", "sortino", "max_drawdown", "calmar", "annualized_return"):
        assert metrics[key] is None, key


def test_non_positive_prices_are_rejected() -> None:
    metrics = q.performance_metrics([100.0] * 10 + [0.0] + [100.0] * 10)
    assert metrics["sharpe"] is None


# --------------------------------------------------------------------------------------
# 基准对齐与 Beta/Alpha
# --------------------------------------------------------------------------------------
def test_beta_of_series_against_itself_is_one() -> None:
    closes = [100 + index * 0.7 + (index % 5) for index in range(80)]
    days = _dates(80)
    metrics = q.performance_metrics(
        closes, dates=days, benchmark=dict(zip(days, closes))
    )
    assert metrics["beta"] == pytest.approx(1.0, abs=1e-9)
    assert metrics["correlation"] == pytest.approx(1.0, abs=1e-9)
    assert metrics["alpha"] == pytest.approx(0.0, abs=1e-9)


def test_beta_uses_only_overlapping_dates() -> None:
    closes = _ramp(80)
    days = _dates(80)
    # 基准只在其中 30 天有值 → 重叠样本 29 个，仍 ≥20，可以算
    partial = {day: 3000.0 + index for index, day in enumerate(days) if index >= 50}
    metrics = q.performance_metrics(closes, dates=days, benchmark=partial)
    assert metrics["beta"] is not None

    # 只有 10 天重叠 → 样本不足，Beta 必须为 None 而不是给出噪声值
    tiny = {day: 3000.0 + index for index, day in enumerate(days) if index >= 70}
    metrics = q.performance_metrics(closes, dates=days, benchmark=tiny)
    assert metrics["beta"] is None


def test_no_benchmark_leaves_beta_none() -> None:
    metrics = q.performance_metrics(_ramp(80), dates=_dates(80))
    assert metrics["beta"] is None and metrics["alpha"] is None
