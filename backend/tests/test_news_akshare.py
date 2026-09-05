from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
from app.providers.akshare_news import AkshareNewsSource
from app.providers.base import ProviderError


def _base_frame(**rows) -> pd.DataFrame:
    """构造与 stock_news_em 返回一致的 DataFrame。"""
    return pd.DataFrame(rows)


class _SymbolAk:
    """仿 akshare：接口参数名为 symbol。"""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls: list[str] = []

    def stock_news_em(self, symbol: str = "603777") -> pd.DataFrame:
        self.calls.append(symbol)
        return self.frame


class _StockAk:
    """历史版本 akshare：接口参数名为 stock。"""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def stock_news_em(self, stock: str = "603777") -> pd.DataFrame:
        return self.frame


def test_fetch_maps_columns_and_uses_symbol_kwarg() -> None:
    published = "2026-09-04 19:06:00"
    frame = _base_frame(
        关键词=["600519", "600519"],
        新闻标题=["贵州茅台发布中报", "茅台酒价跟踪"],
        新闻内容=["净利增长 10%", None],
        发布时间=[published, "2026-09-04 10:00:00"],
        文章来源=["界面新闻", ""],
        新闻链接=["http://finance.eastmoney.com/a/1.html", ""],
    )
    ak = _SymbolAk(frame)
    items = AkshareNewsSource(ak=ak).fetch("600519")

    assert ak.calls == ["600519"]
    assert len(items) == 2
    first = items[0]
    assert first.title == "贵州茅台发布中报"
    assert first.content == "净利增长 10%"
    assert first.source == "界面新闻"
    assert first.published_at == datetime(2026, 9, 4, 19, 6, 0)
    assert first.url == "http://finance.eastmoney.com/a/1.html"

    # 缺失内容/来源/链接的行落到默认值，url 为稳定合成值
    second = items[1]
    assert second.content == ""
    assert second.source == "东方财富"
    assert second.url.startswith("akshare://em/")
    # 合成 url 稳定：重复解析同一行不产生新 digest
    again = AkshareNewsSource(ak=ak).fetch("600519")
    assert again[1].url == second.url


def test_fetch_supports_stock_parameter_name() -> None:
    frame = _base_frame(
        新闻标题=["标题"], 发布时间=["2026-09-04 19:06:00"], 关键词=["600519"]
    )
    items = AkshareNewsSource(ak=_StockAk(frame)).fetch("600519")
    assert len(items) == 1
    assert items[0].title == "标题"


def test_fetch_skips_rows_with_unparseable_time() -> None:
    frame = _base_frame(
        新闻标题=["正常", "坏时间"],
        发布时间=["2026-09-04 19:06:00", "昨天下午"],
        文章来源=["界面", "证券"],
        新闻链接=["http://a/1", "http://a/2"],
    )
    items = AkshareNewsSource(ak=_SymbolAk(frame)).fetch("600519")
    assert len(items) == 1
    assert items[0].title == "正常"


def test_fetch_missing_required_columns_returns_empty() -> None:
    frame = pd.DataFrame({"新闻内容": ["正文"]})  # 缺标题与时间列
    assert AkshareNewsSource(ak=_SymbolAk(frame)).fetch("600519") == []


def test_fetch_empty_frame_returns_empty() -> None:
    assert AkshareNewsSource(ak=_SymbolAk(pd.DataFrame())).fetch("600519") == []


def test_fetch_wraps_network_error_as_provider_error() -> None:
    class BoomAk:
        def stock_news_em(self, symbol: str = "603777") -> pd.DataFrame:
            raise RuntimeError("连接超时")

    with pytest.raises(ProviderError, match="AkShare 个股新闻请求失败"):
        AkshareNewsSource(ak=BoomAk()).fetch("600519")


def test_fetch_missing_api_raises_provider_error() -> None:
    class EmptyAk:
        pass

    with pytest.raises(ProviderError, match="缺少 stock_news_em"):
        AkshareNewsSource(ak=EmptyAk()).fetch("600519")


def test_missing_akshare_translates_to_provider_error(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "akshare":
            raise ModuleNotFoundError("No module named 'akshare'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ProviderError, match="AkShare 未安装"):
        AkshareNewsSource()
