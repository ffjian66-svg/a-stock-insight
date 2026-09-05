from __future__ import annotations

from typing import Any, Protocol


class NewsAnalyzer(Protocol):
    """OpenAI-compatible 结构化新闻分析器的最小接口。"""

    def analyze(self, title: str, content: str) -> dict[str, object]: ...


def analyze_article(
    title: str,
    content: str,
    analyzer: NewsAnalyzer | None,
    model_version: str = "v0",
) -> dict[str, object]:
    """对单篇新闻做结构化分析。

    - analyzer 为空或调用失败时：保留原文摘要并标记 ``raw`` 降级，不伪造情绪。
    - 成功时返回 LLM 摘要、情绪、事件标签与置信度，标记 ``llm``。
    """
    raw_summary = (content or title).strip()[:240] or title[:1000]
    fallback: dict[str, object] = {
        "summary": raw_summary,
        "sentiment": None,
        "event_tag": "一般资讯",
        "confidence": None,
        "model_version": "v0",
        "analysis_mode": "raw",
    }
    if analyzer is None:
        return fallback
    try:
        payload = analyzer.analyze(title, content)
    except Exception:
        return fallback
    summary = str(payload.get("summary") or raw_summary)[:1000]
    return {
        "summary": summary,
        "sentiment": _to_float(payload.get("sentiment"), -1.0, 1.0),
        "event_tag": str(payload.get("event_tag") or "一般资讯")[:40],
        "confidence": _to_float(payload.get("confidence"), 0.0, 1.0),
        "model_version": model_version,
        "analysis_mode": "llm",
    }


def _to_float(value: Any, lower: float, upper: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(lower, min(upper, number))
