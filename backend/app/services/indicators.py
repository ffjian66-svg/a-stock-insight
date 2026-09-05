from __future__ import annotations

import pandas as pd


def calculate_indicators(rows: list[dict[str, float]]) -> dict[str, float | None]:
    if len(rows) < 20:
        return {
            "ma5": None,
            "ma10": None,
            "ma20": None,
            "ma60": None,
            "rsi": None,
            "macd": None,
            "volatility": None,
        }
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
