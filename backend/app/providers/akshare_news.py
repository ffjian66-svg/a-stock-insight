"""AkShare 自选股新闻源（东方财富个股新闻）。

作为 TuShare news（高积分资讯接口）的替代取数源：按 A 股 6 位代码逐个拉取
该股当日最近约 20 条新闻。akshare 在类内部惰性导入（仿 tushare.py），测试
可注入假模块；stock_news_em 的代码参数名在各版本间可能是 ``symbol`` 或
``stock``，构造时用 inspect 探测一次并缓存，避免硬编码。
"""
from __future__ import annotations

import inspect
from datetime import datetime
from hashlib import sha256
from typing import Any

from app.providers.base import NewsData, ProviderError

_SOURCE_FALLBACK = "东方财富"


class AkshareNewsSource:
    def __init__(self, ak: Any | None = None) -> None:
        self._ak = ak if ak is not None else self._import_akshare()
        self._symbol_kw = self._resolve_symbol_kw()

    @staticmethod
    def _import_akshare() -> Any:
        try:
            import akshare
        except ModuleNotFoundError as exc:
            raise ProviderError(
                "AkShare 未安装，无法使用 akshare 新闻源；请 pip install akshare "
                "或设置 NEWS_SOURCE=tushare"
            ) from exc
        return akshare

    def _resolve_symbol_kw(self) -> str:
        fn = getattr(self._ak, "stock_news_em", None)
        if fn is not None:
            try:
                params = inspect.signature(fn).parameters
            except (TypeError, ValueError):
                params = {}
            if "symbol" in params:
                return "symbol"
            if "stock" in params:
                return "stock"
        return ""

    def fetch(self, symbol: str) -> list[NewsData]:
        """拉取单只股票最近新闻并解析为 NewsData，不做时间过滤。"""
        fn = getattr(self._ak, "stock_news_em", None)
        if fn is None:
            raise ProviderError("AkShare 缺少 stock_news_em 接口")
        try:
            frame = fn(**{self._symbol_kw: symbol}) if self._symbol_kw else fn(symbol)
        except Exception as exc:
            raise ProviderError("AkShare 个股新闻请求失败") from exc
        return self._parse(frame)

    def _parse(self, frame: Any) -> list[NewsData]:
        if frame is None:
            return []
        raw_cols = list(getattr(frame, "columns", []))
        records = frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame)
        if not records:
            return []
        title_col = _pick_col(raw_cols, "新闻标题")
        content_col = _pick_col(raw_cols, "新闻内容", "内容")
        source_col = _pick_col(raw_cols, "文章来源", "来源")
        url_col = _pick_col(raw_cols, "新闻链接", "链接", "url")
        time_col = _pick_col(raw_cols, "发布时间", "时间", "public_time")
        if title_col is None or time_col is None:
            return []

        result: list[NewsData] = []
        for record in records:
            title = _clean(record.get(title_col))
            published_at = _parse_datetime(record.get(time_col))
            if not title or published_at is None:
                continue
            content = _clean(record.get(content_col)) if content_col is not None else ""
            source = (
                _clean(record.get(source_col), _SOURCE_FALLBACK)
                if source_col is not None
                else _SOURCE_FALLBACK
            )
            url = _clean(record.get(url_col)) if url_col is not None else ""
            if not url:
                digest = sha256(
                    f"{published_at.isoformat()}|{title}|{content}".encode()
                ).hexdigest()[:24]
                url = f"akshare://em/{digest}"
            result.append(
                NewsData(
                    title=title,
                    content=content,
                    source=source,
                    published_at=published_at,
                    url=url,
                )
            )
        return result


def _pick_col(columns: list[Any], *names: str) -> Any | None:
    for name in names:
        for column in columns:
            if str(column) == name:
                return column
    return None


def _clean(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, float) and value != value:  # NaN
        return default
    text = str(value).strip()
    return text if text and text.lower() != "nan" else default


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):  # pandas.Timestamp
        return value.to_pydatetime()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
