from __future__ import annotations

from datetime import timedelta

import pytest
from app.db.models import FundamentalSnapshot
from app.providers.base import (
    ProviderPermissionError,
    ProviderRateLimitError,
)
from app.providers.llm import LlmNewsAnalyzer
from app.providers.rate_limiter import SlidingWindowRateLimiter
from app.services.fundamentals import snapshot_metrics
from app.services.news_analysis import analyze_article
from app.services.scoring import score_stock
from app.services.sync import classify_error, disable_job, job_disabled


class StubAnalyzer:
    def __init__(
        self, payload: dict[str, object] | None = None, *, raise_error: bool = False
    ) -> None:
        self.payload = payload
        self.raise_error = raise_error

    def analyze(self, title: str, content: str) -> dict[str, object]:
        if self.raise_error:
            raise RuntimeError("上游超时")
        return self.payload or {}


def test_rate_limiter_exhausts_sliding_window() -> None:
    limiter = SlidingWindowRateLimiter(calls_per_minute=2, daily_budget=1000)
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(ProviderRateLimitError):
        limiter.acquire()


def test_rate_limiter_enforces_daily_budget() -> None:
    limiter = SlidingWindowRateLimiter(calls_per_minute=100, daily_budget=1)
    limiter.acquire()
    with pytest.raises(ProviderRateLimitError):
        limiter.acquire()


def test_classify_error_maps_provider_and_network_errors() -> None:
    assert classify_error(ProviderPermissionError("无权限")) == "permission"
    assert classify_error(ProviderRateLimitError("限频")) == "rate_limit"
    assert classify_error(RuntimeError("缺少 TUSHARE_TOKEN")) == "permission"
    assert classify_error(ValueError("坏数据")) == "data"


def test_classify_error_network() -> None:
    import httpx

    assert classify_error(httpx.ConnectError("连接失败")) == "network"


def test_news_analysis_degrades_to_raw_without_analyzer() -> None:
    content = "很好。" * 200
    result = analyze_article("某公司公告", content, None)
    assert result["analysis_mode"] == "raw"
    assert result["model_version"] == "v0"
    assert result["sentiment"] is None
    assert result["confidence"] is None
    assert len(str(result["summary"])) <= 240


def test_news_analysis_degrades_when_analyzer_raises() -> None:
    result = analyze_article("标题", "正文", StubAnalyzer(raise_error=True))
    assert result["analysis_mode"] == "raw"
    assert result["sentiment"] is None


def test_news_analysis_clamps_sentiment_and_confidence() -> None:
    analyzer = StubAnalyzer(
        {
            "summary": "正面信号明确",
            "sentiment": 5,
            "event_tag": "利好",
            "confidence": -2,
        }
    )
    result = analyze_article("标题", "正文", analyzer, model_version="gpt-x")
    assert result["analysis_mode"] == "llm"
    assert result["model_version"] == "gpt-x"
    assert result["sentiment"] == 1.0
    assert result["confidence"] == 0.0
    assert result["event_tag"] == "利好"


def test_news_analysis_accepts_numeric_strings_and_bad_values() -> None:
    analyzer = StubAnalyzer({"sentiment": "0.7", "confidence": "0.8", "summary": "ok"})
    result = analyze_article("标题", "正文", analyzer)
    assert result["sentiment"] == 0.7
    assert result["confidence"] == 0.8
    boolean_analyzer = StubAnalyzer({"sentiment": True, "confidence": "高"})
    result = analyze_article("标题", "正文", boolean_analyzer)
    assert result["sentiment"] is None
    assert result["confidence"] is None


def test_llm_normalize_drops_unknown_fields() -> None:
    raw = LlmNewsAnalyzer._normalize(
        {
            "summary": "s",
            "sentiment": 0.9,
            "event_tag": "扩产",
            "confidence": 0.6,
            "extra": "ignored",
        }
    )
    assert raw["sentiment"] == 0.9
    assert raw["event_tag"] == "扩产"
    assert "extra" not in raw


def test_snapshot_metrics_takes_latest_non_null_per_column() -> None:
    newer = FundamentalSnapshot(
        ts_code="600519.SH", trade_date="2026-06-30", roe=18.0, pe_ttm=None
    )
    older = FundamentalSnapshot(
        ts_code="600519.SH", trade_date="2026-03-31", roe=None, pe_ttm=24.0
    )
    metrics = snapshot_metrics([newer, older])
    assert metrics["roe"] == 18.0
    assert metrics["pe_ttm"] == 24.0
    assert metrics["debt_ratio"] is None


def test_snapshot_metrics_empty() -> None:
    metrics = snapshot_metrics([])
    assert all(value is None for value in metrics.values())
    assert set(metrics) == {
        "pe_ttm",
        "pb",
        "total_mv",
        "turnover_rate",
        "roe",
        "revenue_growth",
        "profit_growth",
        "debt_ratio",
    }


def test_score_with_sentiment_only_lacks_total() -> None:
    result = score_stock([], {}, 0.5)
    assert result.total is None
    assert result.coverage == 0.2
    assert result.risk_level == "高"


def test_score_clamps_and_labelled_explanations() -> None:
    closes = [100.0 + index * 0.5 for index in range(80)]
    fundamentals = {"pe_ttm": 20.0, "roe": 16.0, "profit_growth": 12.0, "turnover_rate": 2.0}
    result = score_stock(closes, fundamentals, 0.5)
    assert result.total is not None
    assert 0 <= result.total <= 100
    factors = {item["factor"] for item in result.explanations}
    assert factors == {"趋势与动量", "质量与估值", "新闻情绪", "波动风险"}


def test_job_disabled_toggles_and_expires() -> None:
    job = "job-disabled-unit"
    assert job_disabled(job) is False  # 未禁用
    disable_job(job, window=timedelta(hours=1))
    assert job_disabled(job) is True  # 禁用窗口内
    disable_job(job, window=timedelta(seconds=-1))  # 已过期
    assert job_disabled(job) is False  # 过期自动复位
    assert job_disabled(job) is False  # 复位后保持可用
