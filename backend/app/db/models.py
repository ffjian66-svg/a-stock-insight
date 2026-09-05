from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Stock(Base):
    __tablename__ = "stocks"
    ts_code: Mapped[str] = mapped_column(String(12), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(40), index=True)
    industry: Mapped[str] = mapped_column(String(40), default="未知")
    market: Mapped[str] = mapped_column(String(20), default="主板")
    list_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TradeCalendar(Base):
    __tablename__ = "trade_calendar"
    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False)


class DailyBar(Base):
    __tablename__ = "daily_bars"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    pre_close: Mapped[float] = mapped_column(Float)
    pct_chg: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="tushare")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class LatestQuote(Base):
    __tablename__ = "latest_quotes"
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), primary_key=True)
    price: Mapped[float] = mapped_column(Float)
    pct_chg: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    quote_time: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(20))
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class FundamentalSnapshot(Base):
    __tablename__ = "fundamental_snapshots"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    pe_ttm: Mapped[Optional[float]] = mapped_column(Float)
    pb: Mapped[Optional[float]] = mapped_column(Float)
    total_mv: Mapped[Optional[float]] = mapped_column(Float)
    turnover_rate: Mapped[Optional[float]] = mapped_column(Float)
    roe: Mapped[Optional[float]] = mapped_column(Float)
    revenue_growth: Mapped[Optional[float]] = mapped_column(Float)
    profit_growth: Mapped[Optional[float]] = mapped_column(Float)
    debt_ratio: Mapped[Optional[float]] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), primary_key=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    stock: Mapped[Stock] = relationship()


class NewsArticle(Base):
    __tablename__ = "news_articles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    source_name: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    published_at: Mapped[datetime] = mapped_column(DateTime)
    summary: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[Optional[float]] = mapped_column(Float)
    event_tag: Mapped[str] = mapped_column(String(40), default="一般资讯")
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(40), default="v0")
    analysis_mode: Mapped[str] = mapped_column(String(20), default="raw")


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), primary_key=True)
    total_score: Mapped[Optional[float]] = mapped_column(Float)
    technical_score: Mapped[Optional[float]] = mapped_column(Float)
    fundamental_score: Mapped[Optional[float]] = mapped_column(Float)
    sentiment_score: Mapped[Optional[float]] = mapped_column(Float)
    risk_score: Mapped[Optional[float]] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(20))
    explanations: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    rule_version: Mapped[str] = mapped_column(String(20), default="v1")


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    message: Mapped[str] = mapped_column(String(300), default="")
    items_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_class: Mapped[str] = mapped_column(String(20), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class ProviderStatus(Base):
    __tablename__ = "provider_status"
    capability: Mapped[str] = mapped_column(String(40), primary_key=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    message: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
