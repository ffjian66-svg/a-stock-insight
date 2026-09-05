from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ScoreResult:
    total: Optional[float]
    technical: Optional[float]
    fundamental: Optional[float]
    sentiment: Optional[float]
    risk: Optional[float]
    coverage: float
    risk_level: str
    explanations: list[dict[str, object]]


def score_stock(
    indicators: dict[str, float | None],
    fundamentals: dict[str, float | None],
    sentiment: float | None,
) -> ScoreResult:
    explanations: list[dict[str, object]] = []
    technical: Optional[float] = None
    if indicators.get("ma20") and indicators.get("ma5"):
        trend = 72 if float(indicators["ma5"] or 0) > float(indicators["ma20"] or 0) else 42
        rsi = indicators.get("rsi")
        rsi_adjustment = 8 if rsi and 45 <= rsi <= 68 else (-10 if rsi and rsi > 78 else 0)
        technical = max(0, min(100, trend + rsi_adjustment))
        explanations.append(
            {
                "factor": "趋势与动量",
                "value": f"MA5/MA20，RSI {rsi}",
                "score": technical,
                "reason": "短期均线位置与动量区间共同评估",
            }
        )

    fundamental: Optional[float] = None
    available = [v for v in fundamentals.values() if v is not None]
    if available:
        pe = fundamentals.get("pe_ttm")
        roe = fundamentals.get("roe")
        growth = fundamentals.get("profit_growth")
        fundamental = (
            50
            + (12 if pe and 0 < pe < 30 else -4)
            + (15 if roe and roe > 12 else 0)
            + (12 if growth and growth > 10 else 0)
        )
        fundamental = max(0, min(100, fundamental))
        explanations.append(
            {
                "factor": "质量与估值",
                "value": f"PE {pe or '缺失'} / ROE {roe or '缺失'}",
                "score": fundamental,
                "reason": "基于估值、盈利质量和成长性的透明阈值",
            }
        )

    sentiment_score = None if sentiment is None else round((sentiment + 1) * 50, 1)
    if sentiment_score is not None:
        explanations.append(
            {
                "factor": "新闻情绪",
                "value": sentiment,
                "score": sentiment_score,
                "reason": "近三日相关新闻的结构化情绪结果",
            }
        )

    volatility = indicators.get("volatility")
    risk = None if volatility is None else max(0, min(100, 100 - float(volatility) * 12))
    if risk is not None:
        explanations.append(
            {
                "factor": "波动风险",
                "value": f"20日波动 {volatility}%",
                "score": round(risk, 1),
                "reason": "波动越低，风险控制得分越高",
            }
        )

    weighted = [(technical, 0.4), (fundamental, 0.3), (sentiment_score, 0.2), (risk, 0.1)]
    present = [(value, weight) for value, weight in weighted if value is not None]
    coverage = round(sum(weight for _, weight in present), 2)
    total = (
        round(sum(float(value) * weight for value, weight in present) / coverage, 1)
        if coverage >= 0.6
        else None
    )
    risk_level = (
        "低"
        if risk is not None and risk >= 72
        else "中"
        if risk is not None and risk >= 48
        else "高"
    )
    return ScoreResult(
        total, technical, fundamental, sentiment_score, risk, coverage, risk_level, explanations
    )
