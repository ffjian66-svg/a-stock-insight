from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from app.providers.base import (
    Capability,
    FundamentalData,
    ProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from app.providers.mock import MockProvider
from app.providers.tushare import TushareProvider


def _fake_tushare(call_fn):
    """绕开 __init__ 的网络/tushare 依赖，只替换 _call 为可控桩。"""
    provider = TushareProvider.__new__(TushareProvider)
    provider._call = call_fn  # type: ignore[method-assign]
    return provider


# ---------- Mock 契约 ----------


def test_mock_financials_map_fundamental_metrics() -> None:
    provider = MockProvider()
    result = provider.financials(["600519.SH", "300750.SZ"])
    assert len(result) == 2
    first = next(item for item in result if item.ts_code == "600519.SH")
    assert isinstance(first, FundamentalData)
    assert first.roe is not None and first.roe > 0
    assert first.revenue_growth is not None
    assert first.profit_growth is not None
    assert first.debt_ratio is not None
    assert first.trade_date == date.today()


def test_mock_calendar_marks_weekdays_open() -> None:
    start = date(2026, 9, 1)
    days = MockProvider().calendar(start, start + timedelta(days=13))
    assert len(days) == 14
    assert days[0] == (date(2026, 9, 1), True)  # 周二
    assert (date(2026, 9, 6), False) in days  # 周日休市


def test_mock_index_history_known_index() -> None:
    rows = MockProvider().index_history("000001.SH", days=40)
    assert 0 < len(rows) <= 40
    assert {"date", "close"} <= set(rows[0])


def test_mock_index_history_falls_back_to_synthetic() -> None:
    rows = MockProvider().index_history("999999.SH", days=40)
    # 合成序列跳过周末，数量约等于区间内交易日
    assert len(rows) >= 20
    assert all(isinstance(row["close"], float) for row in rows)
    assert all(row["date"] < rows[index + 1]["date"] for index, row in enumerate(rows[:-1]))


def test_mock_capabilities_include_new_abilities() -> None:
    caps = MockProvider().capabilities()
    assert Capability.FINANCIALS in caps
    assert Capability.TRADE_CALENDAR in caps


# ---------- Tushare 契约（假 frame） ----------


def test_tushare_requires_token_before_network() -> None:
    with pytest.raises(ProviderPermissionError):
        TushareProvider("", 120, 5000)


def test_tushare_capabilities_exposed() -> None:
    provider = _fake_tushare(lambda method, **kwargs: None)
    caps = provider.capabilities()
    assert Capability.FINANCIALS in caps
    assert Capability.TRADE_CALENDAR in caps


def test_tushare_financials_take_latest_report_period_and_map_fields() -> None:
    frame = pd.DataFrame(
        [
            {
                "end_date": "20251231",
                "roe": 16.0,
                "or_yoy": 9.5,
                "netprofit_yoy": 11.0,
                "debt_to_assets": 31.0,
            },
            {
                "end_date": "20260630",
                "roe": 18.5,
                "or_yoy": 12.3,
                "netprofit_yoy": 15.7,
                "debt_to_assets": 28.4,
            },
        ]
    )
    provider = _fake_tushare(
        lambda method, **kwargs: frame if kwargs.get("ts_code") == "600519.SH" else frame
    )
    result = provider.financials(["600519.SH"])
    assert len(result) == 1
    latest = result[0]
    assert latest.trade_date == date(2026, 6, 30)
    assert latest.roe == 18.5
    assert latest.revenue_growth == 12.3  # or_yoy
    assert latest.profit_growth == 15.7  # netprofit_yoy
    assert latest.debt_ratio == 28.4  # debt_to_assets


def test_tushare_financials_skip_empty_and_data_error_per_code() -> None:
    ok_frame = pd.DataFrame(
        [
            {
                "end_date": "20260630",
                "roe": 18.0,
                "or_yoy": 10.0,
                "netprofit_yoy": 12.0,
                "debt_to_assets": 30.0,
            }
        ]
    )
    empty_frame = pd.DataFrame(
        columns=["end_date", "roe", "or_yoy", "netprofit_yoy", "debt_to_assets"]
    )

    def call(method: str, **kwargs: object):
        code = kwargs.get("ts_code")
        if code == "600519.SH":
            return ok_frame
        if code == "688981.SH":
            return empty_frame
        raise ProviderError("单票数据异常")

    provider = _fake_tushare(call)
    result = provider.financials(["600519.SH", "688981.SH", "601318.SH"])
    assert [item.ts_code for item in result] == ["600519.SH"]


@pytest.mark.parametrize(
    "exc,expected",
    [
        (ProviderPermissionError("无权限"), ProviderPermissionError),
        (ProviderRateLimitError("限频"), ProviderRateLimitError),
    ],
)
def test_tushare_financials_re_raise_permission_and_rate_limit(exc, expected) -> None:
    provider = _fake_tushare(lambda method, **kwargs: (_ for _ in ()).throw(exc))
    with pytest.raises(expected):
        provider.financials(["600519.SH"])


def test_tushare_calendar_parses_dates_and_is_open() -> None:
    frame = pd.DataFrame(
        [
            {"cal_date": "20260601", "is_open": 1},
            {"cal_date": "20260607", "is_open": 0},
            {"cal_date": "not-a-date", "is_open": 1},
        ]
    )
    provider = _fake_tushare(lambda method, **kwargs: frame)
    days = provider.calendar(date(2026, 6, 1), date(2026, 6, 7))
    assert days == [(date(2026, 6, 1), True), (date(2026, 6, 7), False)]


def test_tushare_optional_float_handles_nan_and_blanks() -> None:
    assert TushareProvider._optional_float(float("nan")) is None
    assert TushareProvider._optional_float("") is None
    assert TushareProvider._optional_float(None) is None
    assert TushareProvider._optional_float("18.5") == 18.5


def test_tushare_daily_and_stock_mapping_from_fake_frames() -> None:
    basic = pd.DataFrame(
        [
            {
                "ts_code": "600519.SH",
                "symbol": "600519",
                "name": "贵州茅台",
                "industry": "白酒",
                "market": "主板",
            }
        ]
    )
    daily = pd.DataFrame(
        [
            {
                "ts_code": "600519.SH",
                "open": 1480.0,
                "high": 1500.0,
                "low": 1475.0,
                "close": 1490.0,
                "pre_close": 1470.0,
                "pct_chg": 1.36,
                "vol": 30000.0,
                "amount": 4.4e9,
            }
        ]
    )
    frames = {"stock_basic": basic, "daily": daily}

    def call(method: str, **kwargs: object) -> pd.DataFrame:
        return frames[method]

    provider = _fake_tushare(call)
    stocks = provider.stocks()
    assert stocks[0].name == "贵州茅台"
    bar = provider.daily(date(2026, 9, 1))[0]
    assert bar.ts_code == "600519.SH"
    assert bar.close == 1490.0
    assert bar.trade_date == date(2026, 9, 1)


def test_tushare_index_history_latest_days_ascending() -> None:
    frame = pd.DataFrame(
        [
            {"trade_date": "20260630", "close": 3200.0},
            {"trade_date": "20260601", "close": 3100.0},
            {"trade_date": "20260602", "close": 3120.0},
        ]
    )
    provider = _fake_tushare(lambda method, **kwargs: frame)
    rows = provider.index_history("000001.SH", days=40)
    dates = [row["date"] for row in rows]
    assert dates == ["20260601", "20260602", "20260630"]
    assert rows[-1]["close"] == 3200.0
