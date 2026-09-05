from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from app.db.models import (
    DailyBar,
    FundamentalSnapshot,
    LatestQuote,
    NewsArticle,
    ScoreSnapshot,
    Stock,
    TradeCalendar,
    WatchlistItem,
)
from app.providers.base import NewsData, ProviderError, ProviderPermissionError
from app.providers.mock import MockProvider
from app.services.sync import (
    bootstrap_market,
    run_sync,
    seed_demo,
    sync_calendar,
    sync_fundamentals,
    sync_market,
    sync_news,
)
from sqlalchemy import func, select


def _seed_minimal(session, watch_codes: list[str] | None = None) -> None:
    provider = MockProvider()
    for item in provider.stocks():
        session.add(
            Stock(
                ts_code=item.ts_code,
                symbol=item.symbol,
                name=item.name,
                industry=item.industry,
                market=item.market,
            )
        )
    for code in watch_codes or []:
        session.add(WatchlistItem(ts_code=code))
    session.flush()


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_seed_demo_is_idempotent(db_session) -> None:
    seed_demo(db_session)
    first = _count(db_session, Stock)
    seed_demo(db_session)
    assert _count(db_session, Stock) == first == 8
    assert _count(db_session, WatchlistItem) == 4
    assert _count(db_session, LatestQuote) == 4
    # 演示新闻带上模型元数据
    from app.db.models import NewsArticle

    news = list(db_session.scalars(select(NewsArticle)).all())
    assert news and all(item.analysis_mode == "demo" for item in news)
    assert news[0].confidence is not None


def test_sync_market_is_idempotent(db_session, mock_provider) -> None:
    _seed_minimal(db_session, ["600519.SH", "300750.SZ"])
    today = date.today()
    db_session.commit()

    sync_market(db_session)
    db_session.commit()
    bars_after_first = db_session.scalar(
        select(func.count())
        .select_from(DailyBar)
        .where(DailyBar.trade_date == today)
    )
    returned = sync_market(db_session)
    db_session.commit()
    bars_after_second = db_session.scalar(
        select(func.count())
        .select_from(DailyBar)
        .where(DailyBar.trade_date == today)
    )
    assert returned == 8
    assert bars_after_first == 8
    assert bars_after_second == bars_after_first
    # fundamentals upsert 不重复生成行
    assert _count(db_session, FundamentalSnapshot) == 8


def test_run_sync_quotes_reports_success_and_count(db_session, mock_provider) -> None:
    _seed_minimal(db_session, ["600519.SH", "601318.SH"])
    db_session.commit()
    result = run_sync(db_session, "quotes")
    assert result.status == "success"
    assert result.error_class == ""
    assert result.items_updated == 2
    assert "2" in result.message
    assert _count(db_session, LatestQuote) == 2


def test_run_sync_failure_records_error_class(db_session, mock_provider, monkeypatch) -> None:
    _seed_minimal(db_session, ["600519.SH"])
    db_session.commit()

    def boom(codes: list[str]):
        raise ProviderPermissionError("账户无财务接口权限")

    monkeypatch.setattr(mock_provider, "financials", boom)
    result = run_sync(db_session, "fundamentals")
    assert result.status == "failed"
    assert result.error_class == "permission"
    assert result.items_updated == 0


def test_sync_fundamentals_upserts_report_snapshot(db_session, mock_provider) -> None:
    _seed_minimal(db_session, ["600519.SH"])
    db_session.commit()
    first = sync_fundamentals(db_session)
    second = sync_fundamentals(db_session)
    assert first == 1
    assert second == 1
    row = db_session.scalar(
        select(FundamentalSnapshot).where(FundamentalSnapshot.ts_code == "600519.SH")
    )
    assert row is not None
    assert row.roe is not None
    assert row.revenue_growth is not None
    assert row.profit_growth is not None
    assert row.debt_ratio is not None
    assert _count(db_session, FundamentalSnapshot) == 1


def test_sync_fundamentals_empty_watchlist_is_noop(db_session, mock_provider) -> None:
    assert sync_fundamentals(db_session) == 0


def test_sync_calendar_writes_span_and_marks_weekends(db_session, mock_provider) -> None:
    count = sync_calendar(db_session)
    assert count == 71
    assert _count(db_session, TradeCalendar) == 71
    sunday = date.today() + timedelta(days=(6 - date.today().weekday()) % 7)
    row = db_session.get(TradeCalendar, sunday)
    assert row is not None and row.is_open is False


def test_run_sync_bootstrap_mock_uses_seed_demo(db_session, mock_provider, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sync.get_settings",
        lambda: SimpleNamespace(use_mock_data=True),
    )
    result = run_sync(db_session, "bootstrap")
    assert result.status == "success"
    assert _count(db_session, Stock) == 8
    assert _count(db_session, WatchlistItem) == 4


def test_bootstrap_market_real_branch_seeds_watchlist_when_stocks_present(
    db_session, mock_provider, monkeypatch
) -> None:
    """回归: stocks 已存在但自选为空时(真实 bootstrap 场景), 不再因
    scalars(select(Stock.ts_code)) 返回 str 而抛 AttributeError。"""
    provider = MockProvider()
    for item in provider.stocks():
        db_session.add(
            Stock(
                ts_code=item.ts_code,
                symbol=item.symbol,
                name=item.name,
                industry=item.industry,
                market=item.market,
            )
        )
    db_session.flush()
    assert _count(db_session, WatchlistItem) == 0

    monkeypatch.setattr(
        "app.services.sync.get_settings",
        lambda: SimpleNamespace(bootstrap_history_days=3),
    )
    count = bootstrap_market(db_session)

    assert count >= 0
    codes = set(db_session.scalars(select(WatchlistItem.ts_code)).all())
    assert codes == {"600519.SH", "300750.SZ", "601318.SH", "688981.SH"}


def test_bootstrap_market_skips_codes_missing_from_stock_table(
    db_session, mock_provider, monkeypatch
) -> None:
    """回归: 全市场日线里出现当前上市清单外的孤儿代码(如已退市)时,
    直接跳过而不是因外键约束让整批 bootstrap 失败。"""
    from app.providers.base import BarData

    real_daily = mock_provider.daily

    def daily_with_orphan(trade_date):
        rows = list(real_daily(trade_date))
        rows.append(
            BarData("999999.SZ", trade_date, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0)
        )
        return rows

    monkeypatch.setattr(mock_provider, "daily", daily_with_orphan)
    monkeypatch.setattr(
        "app.services.sync.get_settings",
        lambda: SimpleNamespace(bootstrap_history_days=3),
    )

    count = bootstrap_market(db_session)

    assert count >= 0
    orphan = db_session.scalar(select(DailyBar).where(DailyBar.ts_code == "999999.SZ"))
    assert orphan is None  # 孤儿代码被跳过, 未触发外键失败
    assert _count(db_session, DailyBar) > 0
    assert _count(db_session, WatchlistItem) == 4


def test_sync_market_accepts_explicit_trade_date(db_session, mock_provider) -> None:
    """回归: 指定 trade_date 时只补该日，允许收盘 job 漏拉后手动回补。"""
    _seed_minimal(db_session)
    backfill = date(2026, 8, 19)
    returned = sync_market(db_session, trade_date=backfill)
    db_session.commit()
    assert returned == 8
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DailyBar)
            .where(DailyBar.trade_date == backfill)
        )
        == 8
    )
    # 只写了指定日，没有顺带写今天
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DailyBar)
            .where(DailyBar.trade_date == date.today())
        )
        == 0
    )


def test_sync_market_default_catches_up_missing_open_day(db_session, mock_provider) -> None:
    """回归: 默认 market 同步在日历标记开市但日线缺失时，自动回补最近一个交易日。"""
    _seed_minimal(db_session)
    today = date.today()
    yesterday = today - timedelta(days=1)
    db_session.add_all(
        [
            TradeCalendar(cal_date=yesterday, is_open=True),
            TradeCalendar(cal_date=today, is_open=True),
        ]
    )
    db_session.commit()

    returned = sync_market(db_session)
    db_session.commit()
    assert returned == 16  # 今天 8 + 补拉昨日 8
    for d in (yesterday, today):
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(DailyBar)
                .where(DailyBar.trade_date == d)
            )
            == 8
        )


def _akshare_settings(**overrides) -> SimpleNamespace:
    values = dict(
        use_mock_data=False,
        news_source="akshare",
        news_refresh_minutes=15,
        news_top_n=0,  # 默认不扩容，只拉自选
        llm_api_key=SimpleNamespace(get_secret_value=lambda: ""),
        llm_base_url="",
        llm_model="v0",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _seed_score(session, ts_code: str, total: float) -> None:
    """给股票补一条 score_snapshots，供 news_top_n 扩容按综合分取 top。"""
    session.add(
        ScoreSnapshot(
            ts_code=ts_code,
            total_score=total,
            coverage=0.8,
            risk_level="低",
            calculated_at=datetime.now(),
        )
    )


def test_sync_news_akshare_attributes_by_code_and_dedups(db_session, monkeypatch) -> None:
    _seed_minimal(db_session, ["600519.SH", "300750.SZ"])
    db_session.commit()
    now = datetime.now()
    by_symbol = {
        "600519": [
            NewsData(
                "贵州茅台发布中报",
                "中报正文",
                "界面新闻",
                now - timedelta(minutes=2),
                "http://east/a1",
            ),
            # 窗口外旧闻不应入库
            NewsData(
                "三个月前旧闻",
                "旧文",
                "证券时报",
                now - timedelta(hours=3),
                "http://east/old1",
            ),
        ],
        "300750": [
            NewsData(
                "宁德时代签订大单",
                "订单正文",
                "证券时报",
                now - timedelta(minutes=1),
                "http://east/a2",
            ),
        ],
    }

    class FakeSource:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, symbol: str) -> list[NewsData]:
            self.calls.append(symbol)
            return by_symbol.get(symbol, [])

    fake = FakeSource()
    monkeypatch.setattr("app.services.sync.AkshareNewsSource", lambda: fake)
    monkeypatch.setattr("app.services.sync.get_settings", lambda: _akshare_settings())

    inserted = sync_news(db_session)
    db_session.commit()

    assert inserted == 2
    rows = db_session.scalars(select(NewsArticle)).all()
    assert {row.ts_code for row in rows} == {"600519.SH", "300750.SZ"}
    assert all(row.url != "http://east/old1" for row in rows)  # 旧闻被窗口过滤
    assert all(row.analysis_mode == "raw" for row in rows)  # 无 LLM key 时 raw 降级
    assert set(fake.calls) == {"600519", "300750"}  # 按股票代码逐个拉取

    # url 去重：二次同步不再入库
    assert sync_news(db_session) == 0
    db_session.commit()
    assert _count(db_session, NewsArticle) == 2


def test_sync_news_akshare_skips_code_on_provider_error(db_session, monkeypatch) -> None:
    """单只股票拉取失败只跳过该股，不影响其它自选股入库。"""
    _seed_minimal(db_session, ["600519.SH", "300750.SZ"])
    db_session.commit()
    now = datetime.now()

    class FailingSource:
        def fetch(self, symbol: str) -> list[NewsData]:
            if symbol == "300750":
                raise ProviderError("单只股票新闻拉取失败")
            return [
                NewsData(
                    "贵州茅台发布中报",
                    "正文",
                    "界面新闻",
                    now - timedelta(minutes=1),
                    "http://east/b1",
                )
            ]

    monkeypatch.setattr("app.services.sync.AkshareNewsSource", FailingSource)
    monkeypatch.setattr("app.services.sync.get_settings", lambda: _akshare_settings())

    inserted = sync_news(db_session)
    db_session.commit()

    assert inserted == 1
    rows = db_session.scalars(select(NewsArticle)).all()
    assert [row.ts_code for row in rows] == ["600519.SH"]


def test_sync_news_mock_mode_never_builds_akshare_source(
    db_session, mock_provider, monkeypatch
) -> None:
    """use_mock_data=True 时忽略 news_source，不触发任何 akshare 网络调用。"""
    _seed_minimal(db_session, ["600519.SH"])
    db_session.commit()

    class ShouldNotRun:
        def __init__(self) -> None:
            raise AssertionError("mock 模式不应构造 AkShare 新闻源")

    monkeypatch.setattr("app.services.sync.AkshareNewsSource", ShouldNotRun)
    monkeypatch.setattr(
        "app.services.sync.get_settings", lambda: _akshare_settings(use_mock_data=True)
    )

    assert sync_news(db_session) == 0
    db_session.commit()
    assert _count(db_session, NewsArticle) == 0


def _news(source: str, code: int) -> NewsData:
    return NewsData(
        f"标题{code}",
        "正文",
        source,
        datetime.now() - timedelta(minutes=2),
        f"http://east/top{code}",
    )


def test_sync_news_akshare_merges_top_n_into_universe(db_session, monkeypatch) -> None:
    """news_top_n>0：无自选时也按综合评分前 N 拉新闻并归属正确。"""
    _seed_minimal(db_session)
    _seed_score(db_session, "600519.SH", 90)
    _seed_score(db_session, "000858.SZ", 88)
    _seed_score(db_session, "688981.SH", 70)  # 名次之外，不应拉取
    db_session.commit()
    by_symbol = {
        "600519": [_news("界面新闻", 1)],
        "000858": [_news("证券时报", 2)],
        "688981": [_news("第一财经", 3)],
    }

    class FakeSource:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, symbol: str) -> list[NewsData]:
            self.calls.append(symbol)
            return by_symbol.get(symbol, [])

    fake = FakeSource()
    monkeypatch.setattr("app.services.sync.AkshareNewsSource", lambda: fake)
    monkeypatch.setattr(
        "app.services.sync.get_settings", lambda: _akshare_settings(news_top_n=2)
    )

    inserted = sync_news(db_session)
    db_session.commit()

    assert inserted == 2
    rows = db_session.scalars(select(NewsArticle)).all()
    assert {row.ts_code for row in rows} == {"600519.SH", "000858.SZ"}  # 按得分归属
    assert fake.calls == ["600519", "000858"]  # 自选为空时仅拉 top-2，且按名次序


def test_sync_news_akshare_overlap_fetches_symbol_once(db_session, monkeypatch) -> None:
    """自选与 top-N 重叠的股票 symbol 只 fetch 一次，universe 去重。"""
    _seed_minimal(db_session, ["600519.SH", "300750.SZ"])
    _seed_score(db_session, "600519.SH", 90)  # 已在自选，又进 top-N
    _seed_score(db_session, "000858.SZ", 85)
    db_session.commit()
    by_symbol = {
        "600519": [_news("界面新闻", 1)],
        "300750": [_news("证券时报", 2)],
        "000858": [_news("第一财经", 3)],
    }

    class FakeSource:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, symbol: str) -> list[NewsData]:
            self.calls.append(symbol)
            return by_symbol.get(symbol, [])

    fake = FakeSource()
    monkeypatch.setattr("app.services.sync.AkshareNewsSource", lambda: fake)
    monkeypatch.setattr(
        "app.services.sync.get_settings", lambda: _akshare_settings(news_top_n=2)
    )

    inserted = sync_news(db_session)
    db_session.commit()

    assert inserted == 3
    assert fake.calls == ["600519", "300750", "000858"]  # 自选在前，top-N 补尾
    assert fake.calls.count("600519") == 1
    assert {r.ts_code for r in db_session.scalars(select(NewsArticle))} == {
        "600519.SH",
        "300750.SZ",
        "000858.SZ",
    }


def test_sync_news_akshare_news_top_n_zero_is_watchlist_only(db_session, monkeypatch) -> None:
    """回归：news_top_n=0 时即使有评分快照也只拉自选（旧行为）。"""
    _seed_minimal(db_session, ["600519.SH"])
    _seed_score(db_session, "600519.SH", 99)
    _seed_score(db_session, "000858.SZ", 90)
    db_session.commit()

    class FakeSource:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, symbol: str) -> list[NewsData]:
            self.calls.append(symbol)
            return [_news("界面新闻", 1)] if symbol == "600519" else []

    fake = FakeSource()
    monkeypatch.setattr("app.services.sync.AkshareNewsSource", lambda: fake)
    monkeypatch.setattr(
        "app.services.sync.get_settings", lambda: _akshare_settings(news_top_n=0)
    )

    inserted = sync_news(db_session)
    db_session.commit()

    assert inserted == 1
    assert fake.calls == ["600519"]  # 高分 000858 未被拉取
    assert {r.ts_code for r in db_session.scalars(select(NewsArticle))} == {"600519.SH"}
