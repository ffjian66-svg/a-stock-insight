"""指标序列引擎的纯函数测试。

重点：手造序列断言**已知数值**（而不是"跑通就行"），并用手写 pandas 参考实现
对 `last_scalars` 做等价性加固——新引擎必须能原样复现旧 `calculate_indicators`
的 7 个标量，否则全站历史分数会无声漂移。
"""

from __future__ import annotations

import math

import pandas as pd
import pytest
from app.services.quant import indicators as q


# --------------------------------------------------------------------------------------
# 基础指标
# --------------------------------------------------------------------------------------
def test_ma_on_ramp_has_warmup_none() -> None:
    assert q.ma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ma_window_longer_than_series_is_all_none() -> None:
    assert q.ma([1, 2], 5) == [None, None]


def test_ma_skips_windows_containing_missing_values() -> None:
    result = q.ma([1.0, None, 3.0, 4.0, 5.0], 3)
    assert result[:4] == [None, None, None, None]
    assert result[4] == 4.0  # 3,4,5


def test_ema_recursive_matches_hand_computation() -> None:
    # alpha = 2/(2+1) = 0.6667；y0=1，y1=1/3*1+2/3*2=1.6667，y2=1/3*1.6667+2/3*3=2.5556
    result = q.ema([1.0, 2.0, 3.0], 2)
    assert result[0] == pytest.approx(1.0)
    assert result[1] == pytest.approx(1.66667, abs=1e-4)
    assert result[2] == pytest.approx(2.55556, abs=1e-4)


def test_ema_adjust_true_matches_pandas() -> None:
    values = [3.0, 1.5, 7.25, 2.0, 9.5, 4.0]
    expected = pd.Series(values).ewm(span=4).mean().tolist()
    assert q.ema(values, 4, adjust=True) == pytest.approx(expected)


def test_rsi_all_decline_is_zero_and_flat_is_none() -> None:
    declining = [100.0 - index for index in range(20)]
    assert q.rsi(declining, 14)[-1] == pytest.approx(0.0)
    # 一字横盘：涨跌均值都是 0，旧实现产出 NaN，这里统一表达为 None
    assert q.rsi([10.0] * 20, 14)[-1] is None
    # 单调上涨：loss 均值为 0，同样返回 None（旧实现的 NaN）
    assert q.rsi([100.0 + index for index in range(20)], 14)[-1] is None


def test_rsi_simple_matches_hand_computation() -> None:
    # 14 个 delta 中 7 涨 1 跌 6 平：gain=7/14=0.5，loss=1/14=0.0714 → RSI≈87.5
    closes = [100.0]
    pattern = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    for delta in pattern:
        closes.append(closes[-1] + delta)
    value = q.rsi(closes, 14)[-1]
    assert value is not None
    assert value == pytest.approx(100 - 100 / (1 + 0.5 / (1 / 14)), abs=1e-6)


def test_rsi_wilder_differs_from_simple_but_stays_in_range() -> None:
    closes = [100 + math.sin(index / 3) * 5 for index in range(60)]
    simple = q.rsi(closes, 14)[-1]
    wilder = q.rsi(closes, 14, wilder=True)[-1]
    assert simple is not None and wilder is not None
    assert 0 <= simple <= 100 and 0 <= wilder <= 100
    assert simple != wilder


def test_macd_histogram_is_twice_the_gap() -> None:
    values = [100 + index * 0.8 for index in range(80)]
    result = q.macd(values)
    assert result["dif"][-1] is not None and result["dea"][-1] is not None
    assert result["dif"][-1] > 0  # 单调上涨 → DIF 为正
    assert result["hist"][-1] == pytest.approx(
        2 * (result["dif"][-1] - result["dea"][-1]), abs=1e-9
    )


def test_boll_bands_collapse_on_constant_series() -> None:
    result = q.boll([10.0] * 25)
    assert result["mid"][-1] == pytest.approx(10.0)
    assert result["upper"][-1] == pytest.approx(10.0)
    assert result["lower"][-1] == pytest.approx(10.0)


def test_boll_mid_equals_ma_and_band_width_is_two_sigma() -> None:
    values = [float(index) for index in range(1, 31)]
    result = q.boll(values, 20, 2.0)
    assert result["mid"][-1] == pytest.approx(q.ma(values, 20)[-1])
    chunk = values[-20:]
    mean = sum(chunk) / 20
    sigma = math.sqrt(sum((value - mean) ** 2 for value in chunk) / 20)  # ddof=0
    assert result["upper"][-1] == pytest.approx(mean + 2 * sigma)
    assert result["lower"][-1] == pytest.approx(mean - 2 * sigma)


def test_kdj_constant_series_does_not_divide_by_zero() -> None:
    # 一字板/停牌：最高价 == 最低价，RSV 必须走 50 兜底分支
    result = q.kdj([10.0] * 25, [10.0] * 25, [10.0] * 25)
    assert result["k"][-1] == pytest.approx(50.0)
    assert result["d"][-1] == pytest.approx(50.0)
    assert result["j"][-1] == pytest.approx(50.0)


def test_kdj_warms_up_before_window_is_full() -> None:
    highs = [float(index) for index in range(1, 12)]
    result = q.kdj(highs, highs, highs, window=9)
    assert result["k"][7] is None
    assert result["k"][8] is not None


def test_atr_uses_previous_close_on_gap() -> None:
    # 第二根跳空高开：H-L=5 但 |H-C_prev|=10 更大，TR 必须取 10
    tr = q.true_range([104.0, 110.0], [99.0, 105.0], [100.0, 107.0])
    assert tr[0] == pytest.approx(5.0)  # 首根无从比较前收，退化为 H-L
    assert tr[1] == pytest.approx(10.0)


def test_atr_is_zero_on_a_flat_series() -> None:
    flat = [10.0] * 20
    assert q.atr(flat, flat, flat, 14)[-1] == pytest.approx(0.0)


def test_obv_accumulates_on_up_closes() -> None:
    result = q.obv([1.0, 2.0, 3.0, 2.0], [10.0, 10.0, 10.0, 10.0])
    assert result == [0.0, 10.0, 20.0, 10.0]


def test_donchian_window_excludes_current_bar() -> None:
    # 20 根 1..20，第 21 根为 25：窗口是 [1..20]，25 > 20 才算突破
    highs = [float(index) for index in range(1, 21)] + [25.0]
    lows = [float(index) - 1 for index in range(1, 21)] + [24.0]
    result = q.donchian(highs, lows, 20)
    assert result["upper"][19] is None  # 窗口还没满
    assert result["upper"][20] == pytest.approx(20.0)  # 不含当前 bar 的 25
    assert 25.0 > (result["upper"][20] or 0)


def test_roc_and_amplitude() -> None:
    values = [100.0 + index for index in range(25)]
    # 20 日动量：(124-104)/104
    assert q.roc(values, 20)[-1] == pytest.approx(20.0 / 104.0 * 100)
    highs = [value + 2 for value in values]
    lows = [value - 2 for value in values]
    # 振幅 = 窗口内每日 (H-L)/前收 的均值
    expected = sum(4 / values[index - 1] * 100 for index in range(5, 25)) / 20
    assert q.amplitude(highs, lows, values, 20)[-1] == pytest.approx(expected)


# --------------------------------------------------------------------------------------
# 边界：样本不足 / 空输入 / 非有限值
# --------------------------------------------------------------------------------------
def test_short_and_empty_inputs_never_raise() -> None:
    assert q.ma([], 5) == []
    assert q.volatility([], 20) == []
    assert q.rsi([], 14) == []
    assert all(value is None for value in q.rsi([1.0, 2.0], 14))
    # 序列极短时 MACD 仍返回对齐长度的退化值而非报错
    assert len(q.macd([1.0, 2.0])["dif"]) == 2


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "-", True])
def test_non_finite_values_become_none(bad: object) -> None:
    assert q.clean(bad) is None
    result = q.ma([1.0, bad, 3.0], 1)
    assert result[1] is None


def test_indicator_series_is_aligned_to_input_length() -> None:
    rows = [
        {
            "close": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "volume": 1000.0 + index,
        }
        for index in range(30)
    ]
    series = q.indicator_series(rows)
    for name, values in series.items():
        assert len(values) == 30, name
    assert series["ma20"][18] is None
    assert series["ma20"][19] is not None
    assert series["obv"][-1] is not None
    assert series["atr14"][-1] is not None


def test_indicator_series_without_high_low_degrades_gracefully() -> None:
    series = q.indicator_series([{"close": 100.0 + index} for index in range(30)])
    assert series["kdj_k"][-1] is not None  # high/low 回退到 close，不报错
    assert series["obv"][-1] is None  # 没有 volume 就没有 OBV


# --------------------------------------------------------------------------------------
# 等价性：last_scalars 必须复现旧 calculate_indicators 的 7 个标量
# --------------------------------------------------------------------------------------
def _legacy_calculate_indicators(rows: list[dict[str, float]]) -> dict[str, float | None]:
    """改动前的 pandas 实现（原样拷贝），作为等价性测试的参考预言机。"""
    if len(rows) < 20:
        return dict.fromkeys(("ma5", "ma10", "ma20", "ma60", "rsi", "macd", "volatility"))
    frame = pd.DataFrame(rows)
    close = frame["close"].astype(float)
    result: dict[str, float | None] = {}
    for window in (5, 10, 20, 60):
        result[f"ma{window}"] = (
            round(float(close.rolling(window).mean().iloc[-1]), 2) if len(close) >= window else None
        )
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss.replace(0, float("nan"))
    result["rsi"] = round(float((100 - 100 / (1 + rs)).iloc[-1]), 2)
    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    result["macd"] = round(float((ema12 - ema26).iloc[-1]), 3)
    result["volatility"] = round(float(close.pct_change().tail(20).std() * 100), 2)
    return result


def _normalize(value: float | None) -> float | None:
    """旧实现的 NaN 与新的 None 视为同一语义。"""
    if value is None:
        return None
    return None if not math.isfinite(value) else value


def _wave(n: int, seed: int) -> list[float]:
    return [
        100 + seed + math.sin(index / 4.0) * 6 + math.cos(index / 11.0) * 3 + index * 0.15
        for index in range(n)
    ]


@pytest.mark.parametrize(
    "rows",
    [
        # 恰好 20 根（pct_change 首值 NaN 只取到 19 个收益率的边界）
        [{"close": value} for value in _wave(20, 0)],
        [{"close": value} for value in _wave(21, 1)],
        [{"close": value} for value in _wave(59, 2)],
        [{"close": value} for value in _wave(60, 3)],
        [{"close": value} for value in _wave(80, 4)],
        [{"close": value} for value in _wave(200, 5)],
        # 单调上涨：旧实现 rsi 为 NaN → None
        [{"close": 100.0 + index * 0.5} for index in range(80)],
        # 单调下跌
        [{"close": 200.0 - index * 0.7} for index in range(80)],
        # 一字横盘
        [{"close": 50.0} for index in range(80)],
        # 不足 20 根
        [{"close": 100.0 + index} for index in range(10)],
        [{"close": 100.0} for index in range(19)],
    ],
)
def test_last_scalars_matches_legacy_implementation(rows: list[dict[str, float]]) -> None:
    expected = _legacy_calculate_indicators(rows)
    actual = q.last_scalars(rows)
    assert set(actual) == set(expected)
    for key in expected:
        assert _normalize(actual[key]) == pytest.approx(
            _normalize(expected[key]), nan_ok=True, rel=1e-9, abs=1e-9
        ), key
