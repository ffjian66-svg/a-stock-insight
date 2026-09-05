from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from app.providers.base import (
    BarData,
    Capability,
    FundamentalData,
    MarketDataProvider,
    NewsData,
    QuoteData,
    StockData,
)

STOCKS = [
    StockData("600519.SH", "600519", "贵州茅台", "白酒", "主板"),
    StockData("300750.SZ", "300750", "宁德时代", "电池", "创业板"),
    StockData("601318.SH", "601318", "中国平安", "保险", "主板"),
    StockData("000858.SZ", "000858", "五粮液", "白酒", "主板"),
    StockData("688981.SH", "688981", "中芯国际", "半导体", "科创板"),
    StockData("600036.SH", "600036", "招商银行", "银行", "主板"),
    StockData("002594.SZ", "002594", "比亚迪", "汽车整车", "主板"),
    StockData("000333.SZ", "000333", "美的集团", "家用电器", "主板"),
]

BASE = {
    "000001.SH": 3861.42,
    "399001.SZ": 12308.65,
    "399006.SZ": 2776.18,
    "600519.SH": 1486.2,
    "300750.SZ": 231.58,
    "601318.SH": 58.74,
    "000858.SZ": 121.86,
    "688981.SH": 87.45,
    "600036.SH": 44.12,
    "002594.SZ": 107.36,
    "000333.SZ": 75.68,
}


class MockProvider(MarketDataProvider):
    name = "mock"

    def capabilities(self) -> set[Capability]:
        return set(Capability)

    def financials(self, codes: list[str]) -> list[FundamentalData]:
        result: list[FundamentalData] = []
        for code in codes:
            index = next(
                (i for i, item in enumerate(STOCKS) if item.ts_code == code), 0
            )
            result.append(
                FundamentalData(
                    code,
                    date.today(),
                    roe=round(18 - index * 0.8, 2),
                    revenue_growth=round(11 + index * 2.1, 2),
                    profit_growth=round(13 + index * 2.4, 2),
                    debt_ratio=round(28 + index * 3.5, 2),
                )
            )
        return result

    def calendar(self, start: date, end: date) -> list[tuple[date, bool]]:
        days: list[tuple[date, bool]] = []
        current = start
        while current <= end:
            days.append((current, current.weekday() < 5))
            current += timedelta(days=1)
        return days

    def index_history(self, code: str, days: int = 40) -> list[dict[str, object]]:
        today = date.today()
        rows = self.history(code, days) if code in BASE else []
        if rows:
            return [{"date": bar.trade_date.isoformat(), "close": bar.close} for bar in rows]
        # 未配置的指数代码也给出确定性合成序列，便于界面演示
        base = BASE.get(code, 3000.0)
        result: list[dict[str, object]] = []
        for offset in range(days):
            current = today - timedelta(days=days - offset)
            if current.weekday() >= 5:
                continue
            wave = math.sin(current.toordinal() / 4) * 0.015
            close = round(base * (1 + wave + (offset - days) * 0.0004), 2)
            result.append({"date": current.isoformat(), "close": close})
        return result

    def stocks(self) -> list[StockData]:
        return STOCKS

    def daily(self, trade_date: date) -> list[BarData]:
        return [self._bar(stock.ts_code, trade_date, 0) for stock in STOCKS]

    def history(self, code: str, days: int = 90) -> list[BarData]:
        today = date.today()
        rows: list[BarData] = []
        offset = 0
        while len(rows) < days:
            current = today - timedelta(days=offset)
            offset += 1
            if current.weekday() < 5:
                rows.append(self._bar(code, current, len(rows)))
        return list(reversed(rows))

    def _bar(self, code: str, trade_date: date, offset: int) -> BarData:
        base = BASE.get(code, 50.0)
        wave = math.sin((trade_date.toordinal() - offset) / 5) * 0.022
        trend = (45 - offset) * 0.0008
        close = round(base * (1 + wave + trend), 2)
        pre_close = round(close / (1 + math.sin(trade_date.toordinal()) * 0.008), 2)
        return BarData(
            code,
            trade_date,
            round(pre_close * 0.995, 2),
            round(max(close, pre_close) * 1.012, 2),
            round(min(close, pre_close) * 0.988, 2),
            close,
            pre_close,
            round((close / pre_close - 1) * 100, 2),
            520000 + offset * 2300,
            28.6e8,
        )

    def fundamentals(self, trade_date: date) -> list[FundamentalData]:
        return [
            FundamentalData(
                stock.ts_code,
                trade_date,
                14 + index * 5.2,
                1.3 + index * 0.6,
                1800 + index * 850,
                1.1 + index * 0.35,
            )
            for index, stock in enumerate(STOCKS)
        ]

    def news(self, start: datetime, end: datetime) -> list[NewsData]:
        return []

    def quotes(self, codes: list[str]) -> list[QuoteData]:
        now = datetime.now()
        result = []
        for index, code in enumerate(codes):
            base = BASE.get(code, 50.0)
            pct = round(math.sin(now.minute / 8 + index) * 2.8, 2)
            result.append(
                QuoteData(
                    code,
                    round(base * (1 + pct / 100), 2),
                    pct,
                    820000 + index * 93000,
                    36.8e8 + index * 4.1e8,
                    now,
                    self.name,
                )
            )
        return result
