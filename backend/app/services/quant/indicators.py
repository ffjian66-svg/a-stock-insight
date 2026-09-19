"""指标「序列」引擎：输入等长值序列，输出等长序列（预热期填 None）。

与 `app.services.indicators.calculate_indicators`（只给最后一位标量）的关系：
后者只需标量、且已被 sync/timing 消费，这里提供图表叠加与回测共用的完整序列。
本模块不依赖 pandas，不抛异常，任何位置算不出来就是 None（绝不产生 NaN/inf）。

关于 `wilder` / `rsi` 口径：`rsi(..., wilder=False)` 复刻现有
`calculate_indicators` 的「14 日简单均值」口径（含 loss 均值为 0 时返回 None，
对应 pandas 的 NaN），保证择时阈值语义不变；量化分析页用 Wilder 口径。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

__all__ = [
    "amplitude",
    "atr",
    "boll",
    "clean",
    "donchian",
    "ema",
    "indicator_series",
    "kdj",
    "last_scalars",
    "ma",
    "macd",
    "obv",
    "returns",
    "roc",
    "rsi",
    "true_range",
    "volatility",
]

MA_WINDOWS: tuple[int, ...] = (5, 10, 20, 60)
MIN_BARS = 20


def clean(value: object) -> float | None:
    """把任意输入归一化成有限 float；None/非数/NaN/inf 一律返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _series(values: Sequence[object]) -> list[float | None]:
    return [clean(value) for value in values]


def _stdev(samples: list[float], ddof: int) -> float | None:
    if len(samples) - ddof < 1:
        return None
    mean = sum(samples) / len(samples)
    variance = sum((value - mean) ** 2 for value in samples) / (len(samples) - ddof)
    return math.sqrt(variance)


def ma(values: Sequence[object], window: int) -> list[float | None]:
    """简单移动平均；窗口内出现缺失即视为未形成。"""
    series = _series(values)
    size = len(series)
    out: list[float | None] = [None] * size
    if window < 1:
        return out
    total = 0.0
    valid = 0
    for index in range(size):
        value = series[index]
        if value is None:
            total, valid = 0.0, 0
            continue
        total += value
        valid += 1
        if valid > window:
            dropped = series[index - window]
            total -= dropped if dropped is not None else 0.0
            valid = window
        if valid == window:
            out[index] = total / window
    return out


def ema(values: Sequence[object], span: int, *, adjust: bool = False) -> list[float | None]:
    """指数移动平均。

    adjust=False 为递推口径（图表/回测通用）；adjust=True 复刻 pandas
    `.ewm(span=...).mean()`，仅供 `last_scalars` 还原历史 macd 数值。
    """
    series = _series(values)
    size = len(series)
    out: list[float | None] = [None] * size
    if span < 1:
        return out
    alpha = 2.0 / (span + 1.0)
    prev: float | None = None
    numerator = 0.0
    denominator = 0.0
    for index in range(size):
        value = series[index]
        if value is None:
            continue
        if prev is None:
            prev = value
            numerator, denominator = value, 1.0
            out[index] = value
            continue
        if adjust:
            numerator = numerator * (1 - alpha) + value
            denominator = denominator * (1 - alpha) + 1.0
            out[index] = numerator / denominator
        else:
            prev = (1 - alpha) * prev + alpha * value
            out[index] = prev
    return out


def returns(values: Sequence[object]) -> list[float | None]:
    """逐日收益率（小数），首日与缺失位为 None。"""
    series = _series(values)
    size = len(series)
    out: list[float | None] = [None] * size
    for index in range(1, size):
        current, previous = series[index], series[index - 1]
        if current is None or previous is None or previous == 0:
            continue
        out[index] = current / previous - 1
    return out


def volatility(values: Sequence[object], window: int = 20) -> list[float | None]:
    """滚动收益率标准差 ×100（不年化），与 `calculate_indicators` 的 volatility 同口径。

    窗口内缺失的收益率被跳过而非作废——这样正好复刻 pandas
    `close.pct_change().tail(20).std()` 在「刚好 20 根」时只取到 19 个收益率的边界行为。
    """
    series = _series(values)
    size = len(series)
    daily = returns(series)
    out: list[float | None] = [None] * size
    for index in range(size):
        start = max(0, index - window + 1)
        samples = [value for value in daily[start : index + 1] if value is not None]
        out[index] = None if len(samples) < 2 else _stdev(samples, 1)
    return out


def rsi(values: Sequence[object], period: int = 14, *, wilder: bool = False) -> list[float | None]:
    """相对强弱指标；平均涨幅/跌幅均为 0 时返回 None（legacy 的 NaN 语义）。"""
    series = _series(values)
    size = len(series)
    out: list[float | None] = [None] * size
    if period < 1 or size < period + 1:
        return out
    gains: list[float | None] = [None] * size
    losses: list[float | None] = [None] * size
    for index in range(1, size):
        current, previous = series[index], series[index - 1]
        if current is None or previous is None:
            continue
        delta = current - previous
        gains[index] = max(delta, 0.0)
        losses[index] = max(-delta, 0.0)

    def _emit(index: int, avg_gain: float, avg_loss: float) -> None:
        if avg_loss == 0:
            return
        out[index] = 100 - 100 / (1 + avg_gain / avg_loss)

    if not wilder:
        for index in range(period, size):
            window_gains = gains[index - period + 1 : index + 1]
            window_losses = losses[index - period + 1 : index + 1]
            if any(value is None for value in window_gains + window_losses):
                continue
            _emit(
                index,
                sum(window_gains) / period,  # type: ignore[arg-type]
                sum(window_losses) / period,  # type: ignore[arg-type]
            )
        return out

    avg_gain: float | None = None
    avg_loss: float | None = None
    seed_gains: list[float] = []
    seed_losses: list[float] = []
    for index in range(1, size):
        gain, loss = gains[index], losses[index]
        if gain is None or loss is None:
            avg_gain = avg_loss = None
            seed_gains, seed_losses = [], []
            continue
        if avg_gain is None or avg_loss is None:
            seed_gains.append(gain)
            seed_losses.append(loss)
            if len(seed_gains) < period:
                continue
            avg_gain = sum(seed_gains) / period
            avg_loss = sum(seed_losses) / period
            seed_gains, seed_losses = [], []
        else:
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
        _emit(index, avg_gain, avg_loss)
    return out


def macd(
    values: Sequence[object], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, list[float | None]]:
    """MACD：DIF=EMA(fast)-EMA(slow)，DEA=EMA(DIF,signal)，柱=2×(DIF-DEA)（通达信口径）。"""
    series = _series(values)
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    dif: list[float | None] = [
        None if (a is None or b is None) else a - b for a, b in zip(fast_ema, slow_ema)
    ]
    dea = ema(dif, signal)
    hist: list[float | None] = [
        None if (a is None or b is None) else 2 * (a - b) for a, b in zip(dif, dea)
    ]
    return {"dif": dif, "dea": dea, "hist": hist}


def boll(
    values: Sequence[object], window: int = 20, k: float = 2.0
) -> dict[str, list[float | None]]:
    """布林带：中轨=MA，上下轨=中轨±k×总体标准差（ddof=0，同花顺/通达信口径）。"""
    series = _series(values)
    size = len(series)
    mid = ma(series, window)
    upper: list[float | None] = [None] * size
    lower: list[float | None] = [None] * size
    if window < 1:
        return {"mid": mid, "upper": upper, "lower": lower}
    for index in range(size):
        center = mid[index]
        if center is None:
            continue
        chunk = series[index - window + 1 : index + 1]
        if any(value is None for value in chunk):
            continue
        sample = [float(value) for value in chunk]  # type: ignore[arg-type]
        deviation = math.sqrt(sum((value - center) ** 2 for value in sample) / window)
        upper[index] = center + k * deviation
        lower[index] = center - k * deviation
    return {"mid": mid, "upper": upper, "lower": lower}


def true_range(
    highs: Sequence[object], lows: Sequence[object], closes: Sequence[object]
) -> list[float | None]:
    """真实波幅：max(H-L, |H-C_prev|, |L-C_prev|)，首根退化为 H-L。"""
    high = _series(highs)
    low = _series(lows)
    close = _series(closes)
    size = min(len(high), len(low), len(close))
    out: list[float | None] = [None] * size
    for index in range(size):
        if high[index] is None or low[index] is None:
            continue
        previous = close[index - 1] if index >= 1 else None
        if previous is None:
            out[index] = high[index] - low[index]  # type: ignore[operator]
            continue
        out[index] = max(
            high[index] - low[index],  # type: ignore[operator]
            abs(high[index] - previous),  # type: ignore[operator]
            abs(low[index] - previous),  # type: ignore[operator]
        )
    return out


def atr(
    highs: Sequence[object],
    lows: Sequence[object],
    closes: Sequence[object],
    period: int = 14,
) -> list[float | None]:
    """平均真实波幅（Wilder 平滑），用于止损位与仓位参考。"""
    tr = true_range(highs, lows, closes)
    out: list[float | None] = [None] * len(tr)
    if period < 1:
        return out
    prev: float | None = None
    seed: list[float] = []
    for index, value in enumerate(tr):
        if value is None:
            prev, seed = None, []
            continue
        if prev is None:
            seed.append(value)
            if len(seed) < period:
                continue
            prev = sum(seed) / period
            seed = []
        else:
            current: float = prev
            prev = (current * (period - 1) + value) / period
        out[index] = prev
    return out


def kdj(
    highs: Sequence[object],
    lows: Sequence[object],
    closes: Sequence[object],
    window: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> dict[str, list[float | None]]:
    """随机指标 KDJ；最高价等于最低价（一字板/停牌）时 RSV 取 50 兜底，避免除零。"""
    high = _series(highs)
    low = _series(lows)
    close = _series(closes)
    size = min(len(high), len(low), len(close))
    k_out: list[float | None] = [None] * size
    d_out: list[float | None] = [None] * size
    j_out: list[float | None] = [None] * size
    if window < 1:
        return {"k": k_out, "d": d_out, "j": j_out}
    k_prev = d_prev = 50.0
    for index in range(window - 1, size):
        window_high = [value for value in high[index - window + 1 : index + 1] if value is not None]
        window_low = [value for value in low[index - window + 1 : index + 1] if value is not None]
        current_close = close[index]
        if len(window_high) < window or len(window_low) < window or current_close is None:
            continue
        highest, lowest = max(window_high), min(window_low)
        if highest == lowest:
            rsv = 50.0
        else:
            rsv = (current_close - lowest) / (highest - lowest) * 100
        k_prev = ((k_period - 1) * k_prev + rsv) / k_period
        d_prev = ((d_period - 1) * d_prev + k_prev) / d_period
        k_out[index] = k_prev
        d_out[index] = d_prev
        j_out[index] = 3 * k_prev - 2 * d_prev
    return {"k": k_out, "d": d_out, "j": j_out}


def obv(closes: Sequence[object], volumes: Sequence[object]) -> list[float | None]:
    """能量潮：收涨累加成交量、收跌累减，平盘不变。"""
    close = _series(closes)
    volume = _series(volumes)
    size = min(len(close), len(volume))
    out: list[float | None] = [None] * size
    total = 0.0
    prev_close: float | None = None
    for index in range(size):
        current = close[index]
        if current is None:
            continue
        if prev_close is not None:
            delta = volume[index] or 0.0
            if current > prev_close:
                total += delta
            elif current < prev_close:
                total -= delta
        prev_close = current
        out[index] = total
    return out


def roc(values: Sequence[object], period: int = 20) -> list[float | None]:
    """区间涨跌幅（%）。"""
    series = _series(values)
    out: list[float | None] = [None] * len(series)
    for index in range(period, len(series)):
        base, current = series[index - period], series[index]
        if base is None or current is None or base == 0:
            continue
        out[index] = (current / base - 1) * 100
    return out


def amplitude(
    highs: Sequence[object],
    lows: Sequence[object],
    closes: Sequence[object],
    window: int = 20,
) -> list[float | None]:
    """平均振幅（%）：(H-L)/前收，取 window 日均值。"""
    high = _series(highs)
    low = _series(lows)
    close = _series(closes)
    size = min(len(high), len(low), len(close))
    daily: list[float | None] = [None] * size
    for index in range(size):
        if high[index] is None or low[index] is None:
            continue
        base = close[index - 1] if index >= 1 else close[index]
        if base is None or base == 0:
            continue
        daily[index] = (high[index] - low[index]) / base * 100  # type: ignore[operator]
    return ma(daily, window)


def donchian(
    highs: Sequence[object], lows: Sequence[object], window: int = 20
) -> dict[str, list[float | None]]:
    """唐奇安通道；窗口右端**不含当前 bar**，故 close[i] > upper[i] 是真正的突破。"""
    high = _series(highs)
    low = _series(lows)
    size = min(len(high), len(low))
    upper: list[float | None] = [None] * size
    lower: list[float | None] = [None] * size
    for index in range(window, size):
        window_high = [value for value in high[index - window : index] if value is not None]
        window_low = [value for value in low[index - window : index] if value is not None]
        if len(window_high) < window or len(window_low) < window:
            continue
        upper[index] = max(window_high)
        lower[index] = min(window_low)
    return {"upper": upper, "lower": lower}


def _columns(rows: Sequence[dict[str, float]]) -> dict[str, list[object]]:
    keys = {key for row in rows for key in row}
    return {key: [row.get(key) for row in rows] for key in keys}


def indicator_series(rows: Sequence[dict[str, float]]) -> dict[str, list[float | None]]:
    """把日线行序列展开成全部指标的等长序列，供图表与回测共用。

    rows 至少含 close；含 high/low 时才有 ATR/KDJ/振幅，含 volume 时才有 OBV。
    """
    columns = _columns(rows)
    close = columns.get("close", [])
    high = columns.get("high", close)
    low = columns.get("low", close)
    volume = columns.get("volume", [])
    macd_values = macd(close)
    boll_values = boll(close)
    kdj_values = kdj(high, low, close)
    donchian_values = donchian(high, low)
    series: dict[str, list[float | None]] = {}
    for window in MA_WINDOWS:
        series[f"ma{window}"] = ma(close, window)
    series["ema12"] = ema(close, 12)
    series["ema26"] = ema(close, 26)
    series["dif"] = macd_values["dif"]
    series["dea"] = macd_values["dea"]
    series["macd_hist"] = macd_values["hist"]
    series["boll_up"] = boll_values["upper"]
    series["boll_mid"] = boll_values["mid"]
    series["boll_low"] = boll_values["lower"]
    series["kdj_k"] = kdj_values["k"]
    series["kdj_d"] = kdj_values["d"]
    series["kdj_j"] = kdj_values["j"]
    series["atr14"] = atr(high, low, close)
    series["obv"] = obv(close, volume) if volume else [None] * len(close)
    series["roc20"] = roc(close, 20)
    series["amplitude"] = amplitude(high, low, close)
    series["donchian_up"] = donchian_values["upper"]
    series["donchian_low"] = donchian_values["lower"]
    series["rsi"] = rsi(close, wilder=False)
    series["rsi_wilder"] = rsi(close, wilder=True)
    series["volatility"] = [
        None if value is None else value * 100 for value in volatility(close)
    ]
    return series


def last_scalars(rows: Sequence[dict[str, float]]) -> dict[str, float | None]:
    """`calculate_indicators` 的等价实现：只返回最后一位标量，且保持历史精度与阈值语义。

    与旧实现（pandas 版）的差异只有一处：旧实现在 loss 均值为 0 时返回 NaN，
    这里返回 None（`timing.decide_timing` 本就把非有限值当缺失处理，语义不变）。
    """
    keys = ("ma5", "ma10", "ma20", "ma60", "rsi", "macd", "volatility")
    if len(rows) < MIN_BARS:
        return dict.fromkeys(keys, None)
    columns = _columns(rows)
    close = columns.get("close", [])
    result: dict[str, float | None] = {}
    for window in (5, 10, 20, 60):
        series = ma(close, window)
        value = series[-1] if series else None
        result[f"ma{window}"] = None if value is None else round(value, 2)
    rsi_series = rsi(close, wilder=False)
    rsi_value = rsi_series[-1] if rsi_series else None
    result["rsi"] = None if rsi_value is None else round(rsi_value, 2)
    # 旧实现用 pandas 的 adjust=True EWM，这里保持一致以免历史分数漂移
    fast = ema(close, 12, adjust=True)
    slow = ema(close, 26, adjust=True)
    macd_value = None if (fast[-1] is None or slow[-1] is None) else fast[-1] - slow[-1]
    result["macd"] = None if macd_value is None else round(macd_value, 3)
    vol_series = volatility(close)
    vol_value = vol_series[-1] if vol_series else None
    result["volatility"] = None if vol_value is None else round(vol_value * 100, 2)
    return result
