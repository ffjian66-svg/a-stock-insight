"""风险与绩效指标：年化收益/波动、夏普、索提诺、最大回撤、卡玛、Alpha/Beta。

纯函数，输入收盘价序列（可选基准），输出 dict；样本不足的位置一律 None 而不是报错。
收益率与回撤均为小数（0.12 = 12%），展示层的百分比格式化由 API/前端负责。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime

__all__ = [
    "MIN_BARS",
    "TRADING_DAYS",
    "benchmark_map",
    "drawdown_series",
    "max_drawdown",
    "parse_date",
    "performance_metrics",
]

TRADING_DAYS = 252
MIN_BARS = 20  # 少于该根数不产出任何比率指标


def parse_date(value: object) -> date | None:
    """把 provider 的日期归一化成 date。

    TuShare 返回 ``20260831``，Mock 返回 ``2026-08-31``——两者都要能吃下，
    否则基准序列在真实数据源下会**静默为空**。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:]))
        except ValueError:
            return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _returns(closes: Sequence[float]) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    for index in range(1, len(closes)):
        previous, current = closes[index - 1], closes[index]
        if not previous or not math.isfinite(previous) or not math.isfinite(current):
            continue
        out[index] = current / previous - 1
    return out


def _stdev(samples: Sequence[float], ddof: int = 1) -> float | None:
    if len(samples) - ddof < 1:
        return None
    mean = sum(samples) / len(samples)
    variance = sum((value - mean) ** 2 for value in samples) / (len(samples) - ddof)
    return math.sqrt(variance)


def drawdown_series(closes: Sequence[float]) -> list[float]:
    """每个时点相对历史最高收盘的回撤（≤0）。"""
    out: list[float] = []
    peak = float("-inf")
    for close in closes:
        peak = max(peak, close)
        out.append(close / peak - 1 if peak > 0 else 0.0)
    return out


def max_drawdown(closes: Sequence[float]) -> tuple[float, int, int, int] | None:
    """最大回撤及其区间：返回 (回撤, 峰值位, 谷底位, 修复位)。"""
    if len(closes) < 2:
        return None
    drawdowns = drawdown_series(closes)
    trough = min(range(len(drawdowns)), key=lambda index: drawdowns[index])
    worst = drawdowns[trough]
    if worst >= 0:
        return 0.0, 0, 0, 0
    peak = trough
    for index in range(trough, -1, -1):
        if closes[index] >= closes[trough] / (1 + worst):
            peak = index
            break
    recovered = len(closes) - 1
    for index in range(trough, len(closes)):
        if closes[index] >= closes[peak]:
            recovered = index
            break
    return worst, peak, trough, recovered


def performance_metrics(
    closes: Sequence[float],
    *,
    dates: Sequence[date] | None = None,
    benchmark: dict[date, float] | None = None,
    risk_free: float = 0.0,
    periods: int = TRADING_DAYS,
) -> dict[str, float | int | None]:
    """计算风险/绩效指标。基准按**日期对齐**后才算 Beta/Alpha，避免错位。"""
    result: dict[str, float | int | None] = {
        "bars": len(closes),
        "cumulative_return": None,
        "annualized_return": None,
        "annualized_volatility": None,
        "sharpe": None,
        "sortino": None,
        "max_drawdown": None,
        "max_drawdown_days": None,
        "calmar": None,
        "win_rate": None,
        "beta": None,
        "alpha": None,
        "correlation": None,
    }
    if len(closes) < MIN_BARS or any(not math.isfinite(c) or c <= 0 for c in closes):
        return result

    daily = [value for value in _returns(closes) if value is not None]
    count = len(daily)
    if count < 2:
        return result
    start, end = closes[0], closes[-1]
    cumulative = end / start - 1
    annualized = (end / start) ** (periods / count) - 1
    annual_vol = _stdev(daily)
    annual_vol = None if annual_vol is None else annual_vol * math.sqrt(periods)
    rf_daily = risk_free / periods
    excess = [value - rf_daily for value in daily]
    mean_excess = sum(excess) / count
    sharpe = None if not annual_vol else mean_excess * periods / annual_vol
    downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in excess) / count)
    sortino = None if downside == 0 else mean_excess * periods / (downside * math.sqrt(periods))
    worst = max_drawdown(closes)
    drawdown = None if worst is None else worst[0]
    drawdown_days = None if worst is None else worst[3] - worst[1]
    calmar = None if not drawdown else annualized / abs(drawdown)

    result.update(
        cumulative_return=cumulative,
        annualized_return=annualized,
        annualized_volatility=annual_vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=drawdown,
        max_drawdown_days=drawdown_days,
        calmar=calmar,
        win_rate=sum(1 for value in daily if value > 0) / count,
    )

    if benchmark and dates and len(dates) == len(closes):
        aligned_stock: list[float] = []
        aligned_bench: list[float] = []
        previous_close: float | None = None
        previous_bench: float | None = None
        for index, close in enumerate(closes):
            bench_close = benchmark.get(dates[index])
            if (
                bench_close
                and previous_close
                and previous_bench
                and math.isfinite(bench_close)
            ):
                aligned_stock.append(close / previous_close - 1)
                aligned_bench.append(bench_close / previous_bench - 1)
            previous_close, previous_bench = close, bench_close
        if len(aligned_stock) >= MIN_BARS:
            size = len(aligned_stock)
            mean_stock = sum(aligned_stock) / size
            mean_bench = sum(aligned_bench) / size
            covariance = (
                sum(
                    (s - mean_stock) * (b - mean_bench)
                    for s, b in zip(aligned_stock, aligned_bench)
                )
                / size
            )
            # Beta 与相关系数共用同一套总体矩（ddof=0）：混用样本矩会让"序列对自身"
            # 算出 β=1 却 ρ≠1
            stock_var = sum((value - mean_stock) ** 2 for value in aligned_stock) / size
            bench_var = sum((value - mean_bench) ** 2 for value in aligned_bench) / size
            if bench_var:
                beta = covariance / bench_var
                result["beta"] = beta
                result["alpha"] = (
                    mean_stock - rf_daily - beta * (mean_bench - rf_daily)
                ) * periods
                if stock_var:
                    result["correlation"] = covariance / math.sqrt(stock_var * bench_var)
    return result


def benchmark_map(points: Iterable[dict[str, object]]) -> dict[date, float]:
    """provider `index_history` 的返回（可能混用 ``YYYYMMDD`` / ``YYYY-MM-DD``）转日期字典。"""
    mapping: dict[date, float] = {}
    for point in points:
        day = parse_date(point.get("date"))
        close = point.get("close")
        if day is None or not isinstance(close, (int, float)):
            continue
        mapping[day] = float(close)
    return mapping
