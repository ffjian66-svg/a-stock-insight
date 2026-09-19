from __future__ import annotations

from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Any

from app.providers.base import (
    BarData,
    Capability,
    FundamentalData,
    MarketDataProvider,
    NewsData,
    ProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
    QuoteData,
    StockData,
)
from app.providers.rate_limiter import SlidingWindowRateLimiter


def _parse_tushare_date(value: Any) -> date | None:
    """TuShare 的 trade_date 是 `YYYYMMDD` 字符串（或 int），解析失败返回 None。"""
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


class TushareProvider(MarketDataProvider):
    name = "tushare"

    def __init__(self, token: str, calls_per_minute: int, daily_budget: int) -> None:
        if not token:
            raise ProviderPermissionError("未配置 TuShare Token")
        import tushare as ts

        ts.set_token(token)
        self.ts = ts
        self.pro = ts.pro_api(token)
        self.limiter = SlidingWindowRateLimiter(calls_per_minute, daily_budget)

    def capabilities(self) -> set[Capability]:
        return {
            Capability.STOCKS,
            Capability.DAILY,
            Capability.FUNDAMENTALS,
            Capability.FINANCIALS,
            Capability.TRADE_CALENDAR,
            Capability.QUOTES,
            Capability.NEWS,
        }

    def _call(self, method: str, **kwargs: Any) -> Any:
        self.limiter.acquire()
        try:
            return getattr(self.pro, method)(**kwargs)
        except Exception as exc:
            message = str(exc)
            if "权限" in message or "积分" in message:
                raise ProviderPermissionError("当前 TuShare 账户无此接口权限") from exc
            if "频率" in message or "每分钟" in message:
                raise ProviderRateLimitError("TuShare 接口触发频率限制") from exc
            raise ProviderError("TuShare 数据请求失败") from exc

    def stocks(self) -> list[StockData]:
        frame = self._call(
            "stock_basic",
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,industry,market",
        )
        return [
            StockData(
                str(r.ts_code),
                str(r.symbol),
                str(r.name),
                str(r.industry or "未知"),
                str(r.market or "未知"),
            )
            for r in frame.itertuples()
        ]

    def daily(self, trade_date: date) -> list[BarData]:
        frame = self._call("daily", trade_date=trade_date.strftime("%Y%m%d"))
        return [
            BarData(
                str(r.ts_code),
                trade_date,
                float(r.open),
                float(r.high),
                float(r.low),
                float(r.close),
                float(r.pre_close),
                float(r.pct_chg),
                float(r.vol),
                float(r.amount),
            )
            for r in frame.itertuples()
        ]

    def stock_history(self, code: str, start: date, end: date) -> list[BarData]:
        frame = self._call(
            "daily",
            ts_code=code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        rows: list[BarData] = []
        for r in frame.itertuples():
            trade_date = _parse_tushare_date(r.trade_date)
            if trade_date is None:
                continue
            rows.append(
                BarData(
                    code,
                    trade_date,
                    float(r.open),
                    float(r.high),
                    float(r.low),
                    float(r.close),
                    float(r.pre_close),
                    float(r.pct_chg),
                    float(r.vol),
                    float(r.amount),
                )
            )
        rows.sort(key=lambda bar: bar.trade_date)
        return rows

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or str(value).strip() in {"", "nan", "None"}:
            return None
        return float(value)

    def fundamentals(self, trade_date: date) -> list[FundamentalData]:
        frame = self._call(
            "daily_basic",
            trade_date=trade_date.strftime("%Y%m%d"),
            fields="ts_code,trade_date,turnover_rate,pe_ttm,pb,total_mv",
        )
        return [
            FundamentalData(
                str(row.ts_code),
                trade_date,
                self._optional_float(row.pe_ttm),
                self._optional_float(row.pb),
                self._optional_float(row.total_mv),
                self._optional_float(row.turnover_rate),
            )
            for row in frame.itertuples()
        ]

    def financials(self, codes: list[str]) -> list[FundamentalData]:
        result: list[FundamentalData] = []
        for code in codes:
            try:
                frame = self._call(
                    "fina_indicator",
                    ts_code=code,
                    fields="end_date,roe,or_yoy,netprofit_yoy,debt_to_assets",
                )
            except ProviderPermissionError:
                raise
            except ProviderRateLimitError:
                raise
            except ProviderError:
                # 单票数据缺失或异常，跳过并保留其它票
                continue
            if frame is None or frame.empty:
                continue
            latest = frame.sort_values("end_date", ascending=False).iloc[0]
            end_date = str(latest["end_date"])
            try:
                as_of = datetime.strptime(end_date, "%Y%m%d").date()
            except ValueError:
                as_of = date.today()
            result.append(
                FundamentalData(
                    ts_code=code,
                    trade_date=as_of,
                    roe=self._optional_float(latest["roe"]),
                    revenue_growth=self._optional_float(latest["or_yoy"]),
                    profit_growth=self._optional_float(latest["netprofit_yoy"]),
                    debt_ratio=self._optional_float(latest["debt_to_assets"]),
                )
            )
        return result

    def calendar(self, start: date, end: date) -> list[tuple[date, bool]]:
        frame = self._call(
            "trade_cal",
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields="cal_date,is_open",
        )
        days: list[tuple[date, bool]] = []
        for row in frame.itertuples():
            try:
                cal_date = datetime.strptime(str(row.cal_date), "%Y%m%d").date()
            except ValueError:
                continue
            days.append((cal_date, bool(int(row.is_open))))
        return days

    def index_history(self, code: str, days: int = 40) -> list[dict[str, object]]:
        start = (datetime.now() - timedelta(days=max(days * 3, 200))).date()
        frame = self._call(
            "index_daily",
            ts_code=code,
            start_date=start.strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            fields="trade_date,close",
        )
        if frame is None or frame.empty:
            return []
        frame = frame.sort_values("trade_date", ascending=False).head(days)
        return [
            {"date": str(row.trade_date), "close": float(row.close)}
            for row in frame.iloc[::-1].itertuples()
        ]

    def news(self, start: datetime, end: datetime) -> list[NewsData]:
        frame = self._call(
            "news",
            src="sina",
            start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
            fields="datetime,title,content,channels",
        )
        result: list[NewsData] = []
        for row in frame.itertuples():
            published_at = datetime.fromisoformat(str(row.datetime))
            title = str(row.title)
            digest = sha256(
                f"{published_at.isoformat()}|{title}|{row.content}".encode()
            ).hexdigest()[:24]
            result.append(
                NewsData(
                    title=title,
                    content=str(row.content or ""),
                    source=str(row.channels or "新浪财经"),
                    published_at=published_at,
                    url=f"tushare://sina/{digest}",
                )
            )
        return result

    def quotes(self, codes: list[str]) -> list[QuoteData]:
        if not codes:
            return []
        self.limiter.acquire()
        try:
            frame = self.ts.realtime_quote(ts_code=",".join(codes), src="sina")
        except Exception as exc:
            raise ProviderError("TuShare 实时行情请求失败") from exc
        if frame is None or frame.empty:
            return []
        result: list[QuoteData] = []
        for _, row in frame.iterrows():
            normalized = {str(key).lower(): value for key, value in row.items()}
            code = str(normalized.get("ts_code") or normalized.get("code") or "").upper()
            if code.isdigit() and len(code) == 6:
                code += ".SH" if code.startswith(("6", "9")) else ".SZ"
            price = self._optional_float(normalized.get("price")) or 0.0
            pre_close = self._optional_float(normalized.get("pre_close")) or price
            pct = (price / pre_close - 1) * 100 if pre_close else 0.0
            quote_time = datetime.now()
            date_text = str(normalized.get("date") or "").strip()
            time_text = str(normalized.get("time") or "").strip()
            if date_text and time_text:
                try:
                    quote_time = datetime.fromisoformat(f"{date_text} {time_text}")
                except ValueError:
                    pass
            is_stale = (datetime.now() - quote_time).total_seconds() > 180
            result.append(
                QuoteData(
                    code,
                    price,
                    round(pct, 2),
                    self._optional_float(normalized.get("volume")) or 0.0,
                    self._optional_float(normalized.get("amount")) or 0.0,
                    quote_time,
                    self.name,
                    is_stale,
                )
            )
        return result
