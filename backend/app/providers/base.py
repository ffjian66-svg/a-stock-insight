from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class Capability(str, Enum):
    STOCKS = "stocks"
    DAILY = "daily"
    FUNDAMENTALS = "fundamentals"
    FINANCIALS = "financials"
    TRADE_CALENDAR = "trade_calendar"
    QUOTES = "quotes"
    NEWS = "news"


class ProviderError(RuntimeError):
    pass


class ProviderPermissionError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


@dataclass(frozen=True)
class StockData:
    ts_code: str
    symbol: str
    name: str
    industry: str
    market: str


@dataclass(frozen=True)
class BarData:
    ts_code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    pct_chg: float
    volume: float
    amount: float


@dataclass(frozen=True)
class FundamentalData:
    ts_code: str
    trade_date: date
    pe_ttm: float | None = None
    pb: float | None = None
    total_mv: float | None = None
    turnover_rate: float | None = None
    roe: float | None = None
    revenue_growth: float | None = None
    profit_growth: float | None = None
    debt_ratio: float | None = None


@dataclass(frozen=True)
class NewsData:
    title: str
    content: str
    source: str
    published_at: datetime
    url: str


@dataclass(frozen=True)
class QuoteData:
    ts_code: str
    price: float
    pct_chg: float
    volume: float
    amount: float
    quote_time: datetime
    source: str
    is_stale: bool = False


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    def capabilities(self) -> set[Capability]: ...

    @abstractmethod
    def stocks(self) -> list[StockData]: ...

    @abstractmethod
    def daily(self, trade_date: date) -> list[BarData]: ...

    @abstractmethod
    def fundamentals(self, trade_date: date) -> list[FundamentalData]: ...

    @abstractmethod
    def quotes(self, codes: list[str]) -> list[QuoteData]: ...

    @abstractmethod
    def news(self, start: datetime, end: datetime) -> list[NewsData]: ...

    @abstractmethod
    def financials(self, codes: list[str]) -> list[FundamentalData]:
        """最新一期财务指标（ROE/营收与净利增长/负债率），trade_date 为报告期。"""

    @abstractmethod
    def calendar(self, start: date, end: date) -> list[tuple[date, bool]]:
        """区间内每个自然日是否为交易日。"""

    def index_history(self, code: str, days: int = 40) -> list[dict[str, object]]:
        """指数最近收盘序列，用于迷你走势；不支持时返回空列表。"""
        return []
