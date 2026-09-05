from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TimingAdvice(BaseModel):
    label: str
    tone: Literal["buy", "hold", "reduce", "watch"]
    detail: str


class WatchlistCreate(BaseModel):
    ts_code: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    note: str = Field(default="", max_length=200)


class SyncRequest(BaseModel):
    job_type: str = Field(
        default="quotes",
        pattern=r"^(quotes|scores|market|news|bootstrap|stocks|calendar|fundamentals)$",
    )


class ApiMessage(BaseModel):
    message: str


class SystemStatus(BaseModel):
    provider: str
    tushare_configured: bool
    llm_configured: bool
    mock_mode: bool
    quote_refresh_seconds: int
    updated_at: datetime
    data_mode: str = "unconfigured"
    capabilities: list[str] = Field(default_factory=list)
    provider_status_at: Optional[datetime] = None


class StockView(BaseModel):
    ts_code: str
    symbol: str
    name: str
    industry: str
    market: str
    price: Optional[float] = None
    pct_chg: Optional[float] = None
    total_score: Optional[float] = None
    technical_score: Optional[float] = None
    fundamental_score: Optional[float] = None
    sentiment_score: Optional[float] = None
    coverage: Optional[float] = None
    risk_level: Optional[str] = None
    is_stale: bool = False
    quote_time: Optional[datetime] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    explanations: list[dict[str, Any]] = Field(default_factory=list)
    timing: Optional[TimingAdvice] = None


class TopBoardRow(StockView):
    """综合评分 TOP-N 榜单行：StockView 全集 + 榜序 + 近 3 天新闻简报。

    继承而非改 StockView，避免把榜单专属字段漏进 /screener、/watchlist 契约。
    新闻口径与 scoring.recalculate_scores 的 news 维度一致（naive 3 天窗口）：
    news_sentiment_avg 为近 3 天有情绪(sentiment 非空)文章均值(-1..1)，raw 恒为空被排除；
    最新一条简报仅看 llm/demo（raw 的 event_tag 恒为“一般资讯”，无信号价值）。
    """

    rank: int = 0
    news_sentiment_avg: Optional[float] = None
    news_3d_count: int = 0
    news_tag: Optional[str] = None
    news_sentiment: Optional[float] = None
    news_title: Optional[str] = None
    news_published_at: Optional[datetime] = None


class QuoteView(BaseModel):
    ts_code: str
    name: str
    price: Optional[float] = None
    pct_chg: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None
    quote_time: Optional[datetime] = None
    is_stale: bool = True
    source: str = ""


class NewsView(BaseModel):
    id: int
    title: str
    source: str
    published_at: datetime
    summary: str
    sentiment: Optional[float] = None
    event_tag: str
    confidence: Optional[float] = None
    model_version: str = "v0"
    analysis_mode: str = "raw"


class ScoreExplanation(BaseModel):
    ts_code: str
    name: str
    total_score: Optional[float] = None
    technical_score: Optional[float] = None
    fundamental_score: Optional[float] = None
    sentiment_score: Optional[float] = None
    risk_score: Optional[float] = None
    coverage: float = 0
    risk_level: str = ""
    explanations: list[dict[str, Any]] = Field(default_factory=list)
    calculated_at: Optional[datetime] = None
    rule_version: str = ""
    quote_time: Optional[datetime] = None
    is_stale: bool = True
