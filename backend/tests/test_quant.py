"""量化数据链路测试：provider 区间历史、按需回补的幂等与降级、评分落库。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from app.db.models import DailyBar, FactorSnapshot, FundamentalSnapshot, ScoreSnapshot, Stock
from app.providers.base import ProviderError
from app.providers.mock import BASE, MockProvider
from app.providers.tushare import _parse_tushare_date
from app.services import history as history_mod
from app.services.sync import recalculate_scores
from sqlalchemy import func, select


@pytest.fixture(autouse=True)
def _clear_backfill_cache():
    """`_BACKFILLED` 是进程级缓存，跨测试必须清空，否则第二个用例会误判为"已补过"。"""
    history_mod._BACKFILLED.clear()
    yield
    history_mod._BACKFILLED.clear()


class CountingProvider(MockProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []

    def stock_history(self, code: str, start: date, end: date) -> list[DailyBar]:
        self.calls.append((code, start, end))
        return super().stock_history(code, start, end)


class FailingProvider(MockProvider):
    def stock_history(self, code: str, start: date, end: date) -> list[DailyBar]:
        raise ProviderError("模拟上游故障")


def _seed_stock(session, code: str = "600519.SH", bars: int = 0) -> None:
    session.add(Stock(ts_code=code, symbol=code[:6], name="测试", industry="测试", market="主板"))
    session.flush()
    for index in range(bars):
        session.add(
            DailyBar(
                ts_code=code,
                trade_date=date(2026, 1, 1) + timedelta(days=index),
                open=100.0 + index,
                high=101.0 + index,
                low=99.0 + index,
                close=100.0 + index,
                pre_close=100.0 + index,
                pct_chg=0.0,
                volume=1_000.0,
                amount=1.0e6,
            )
        )
    session.flush()


# --------------------------------------------------------------------------------------
# provider 区间历史
# --------------------------------------------------------------------------------------
def test_mock_stock_history_returns_weekdays_within_the_range() -> None:
    start, end = date(2026, 1, 1), date(2026, 3, 31)
    bars = MockProvider().stock_history("600519.SH", start, end)
    assert bars
    assert all(start <= bar.trade_date <= end for bar in bars)
    assert all(bar.trade_date.weekday() < 5 for bar in bars)
    assert [bar.trade_date for bar in bars] == sorted(bar.trade_date for bar in bars)
    assert all(bar.ts_code == "600519.SH" for bar in bars)


def test_mock_stock_history_does_not_drift_over_long_windows() -> None:
    """两年 ~480 根的 offset 若直接透传，演示价会被压低三成——必须归一化回 0..90。"""
    bars = MockProvider().stock_history("600519.SH", date(2024, 9, 1), date(2026, 9, 1))
    assert len(bars) > 400
    base = BASE["600519.SH"]
    ratios = [bar.close / base for bar in bars]
    assert min(ratios) > 0.9
    assert max(ratios) < 1.1


def test_mock_stock_history_is_deterministic() -> None:
    start, end = date(2026, 1, 1), date(2026, 2, 1)
    assert MockProvider().stock_history("600519.SH", start, end) == MockProvider().stock_history(
        "600519.SH", start, end
    )


def test_stock_history_is_optional_on_the_provider_contract() -> None:
    """未实现该接口的 provider 走基类默认实现返回空列表，而不是被抽象方法逼着报错。

    直接调用基类上的未绑定函数，绕开 MockProvider 的覆写。
    """
    from app.providers.base import MarketDataProvider

    assert not getattr(MarketDataProvider.stock_history, "__isabstractmethod__", False)
    today = date.today()
    assert MarketDataProvider.stock_history(MockProvider(), "600519.SH", today, today) == []


def test_tushare_date_parser_handles_compact_dates() -> None:
    assert _parse_tushare_date("20260831") == date(2026, 8, 31)
    assert _parse_tushare_date(20260831) == date(2026, 8, 31)
    assert _parse_tushare_date("") is None
    assert _parse_tushare_date("2026-08-31") is None


# --------------------------------------------------------------------------------------
# 按需回补
# --------------------------------------------------------------------------------------
def test_ensure_bar_history_backfills_and_then_serves_from_cache(db_session) -> None:
    _seed_stock(db_session)
    provider = CountingProvider()

    rows = history_mod.ensure_bar_history(db_session, "600519.SH", provider=provider)
    assert len(rows) > 400  # 约两年交易日
    assert len(provider.calls) == 1

    again = history_mod.ensure_bar_history(db_session, "600519.SH", provider=provider)
    assert len(again) == len(rows)
    assert len(provider.calls) == 1, "区间已覆盖时不应再打上游"
    assert db_session.scalar(select(func.count()).select_from(DailyBar)) == len(rows)


def test_ensure_bar_history_is_idempotent_across_a_cleared_cache(db_session) -> None:
    """缓存被清（例如进程重启）后重跑：库内已有的交易日不该重复插入。"""
    _seed_stock(db_session)
    provider = CountingProvider()
    first = len(history_mod.ensure_bar_history(db_session, "600519.SH", provider=provider))

    history_mod._BACKFILLED.clear()
    second = len(history_mod.ensure_bar_history(db_session, "600519.SH", provider=provider))
    assert second == first
    assert db_session.scalar(select(func.count()).select_from(DailyBar)) == first


def test_ensure_bar_history_degrades_when_provider_fails(db_session) -> None:
    _seed_stock(db_session)
    rows = history_mod.ensure_bar_history(db_session, "600519.SH", provider=FailingProvider())
    assert rows == []  # 上游挂了也不能抛，返回已有数据（此处为空）
    assert history_mod._BACKFILLED  # 失败也记账，避免每次请求都重试


def test_ensure_bar_history_skips_unknown_stock(db_session) -> None:
    provider = CountingProvider()
    rows = history_mod.ensure_bar_history(db_session, "999999.SZ", provider=provider)
    assert rows == []
    assert provider.calls == [], "库里没有这只股票时不该浪费一次外键注定失败的调用"


def test_ensure_bar_history_extends_a_short_existing_window(db_session) -> None:
    _seed_stock(db_session, bars=5)
    provider = CountingProvider()
    rows = history_mod.ensure_bar_history(db_session, "600519.SH", provider=provider)
    assert len(provider.calls) == 1
    assert len(rows) > 400  # 从 5 根补到约两年
    assert db_session.scalar(select(func.count()).select_from(DailyBar)) == len(rows)


# --------------------------------------------------------------------------------------
# 评分落库（rule_version=v2 + 因子快照）
# --------------------------------------------------------------------------------------
def _seed_scoring_universe(session) -> None:
    for code, step in (("600519.SH", 0.6), ("000001.SZ", -0.3)):
        session.add(
            Stock(ts_code=code, symbol=code[:6], name=code, industry="测试", market="主板")
        )
        session.flush()
        session.add(
            FundamentalSnapshot(
                ts_code=code,
                trade_date=date(2026, 3, 20),
                pe_ttm=20.0,
                pb=3.0,
                total_mv=1.0e5,
                turnover_rate=2.0,
                roe=15.0,
                profit_growth=12.0,
            )
        )
        for index in range(80):
            close = 100.0 + index * step
            session.add(
                DailyBar(
                    ts_code=code,
                    trade_date=date(2026, 1, 1) + timedelta(days=index),
                    open=close,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    pre_close=close,
                    pct_chg=0.0,
                    volume=1_000.0,
                    amount=1.0e6,
                )
            )
    session.flush()


def test_recalculate_scores_marks_v2_and_writes_factor_snapshots(db_session) -> None:
    _seed_scoring_universe(db_session)
    recalculate_scores(db_session)
    db_session.flush()

    scores = {row.ts_code: row for row in db_session.scalars(select(ScoreSnapshot)).all()}
    assert set(scores) == {"600519.SH", "000001.SZ"}
    assert all(row.rule_version == "v2" for row in scores.values())
    # 趋势 0.4 + 质量 0.3 + 波动 0.1 = 0.8；没有新闻，情绪那 0.2 不计入
    assert all(row.coverage == 0.8 for row in scores.values())
    assert all(row.total_score is not None for row in scores.values())
    # 上涨那只的动量分位必须高于下跌那只
    assert scores["600519.SH"].total_score > scores["000001.SZ"].total_score

    snapshots = {row.ts_code: row for row in db_session.scalars(select(FactorSnapshot)).all()}
    assert set(snapshots) == set(scores)
    factors = snapshots["600519.SH"].factors
    assert factors["momentum_20"]["raw"] is not None
    assert factors["momentum_20"]["score"] == 100.0
    assert snapshots["600519.SH"].composite == scores["600519.SH"].total_score
    assert snapshots["600519.SH"].trade_date == date(2026, 3, 21)


def test_recalculate_scores_ignores_stocks_without_bars(db_session) -> None:
    """没有日线的股票不进面板，也不该产生评分行（否则会拉低别人的分位）。"""
    _seed_stock(db_session, code="600519.SH", bars=0)
    _seed_stock(db_session, code="300750.SZ", bars=40)
    recalculate_scores(db_session)
    codes = set(db_session.scalars(select(ScoreSnapshot.ts_code)).all())
    assert codes == {"300750.SZ"}


def test_recalculate_scores_is_repeatable(db_session) -> None:
    _seed_scoring_universe(db_session)
    recalculate_scores(db_session)
    first = db_session.scalar(
        select(ScoreSnapshot.total_score).where(ScoreSnapshot.ts_code == "600519.SH")
    )
    recalculate_scores(db_session)
    second = db_session.scalar(
        select(ScoreSnapshot.total_score).where(ScoreSnapshot.ts_code == "600519.SH")
    )
    assert first == second
    assert db_session.scalar(select(func.count()).select_from(FactorSnapshot)) == 2


def test_factor_snapshot_records_calculation_time(db_session) -> None:
    _seed_scoring_universe(db_session)
    recalculate_scores(db_session)
    row = db_session.get(FactorSnapshot, "600519.SH")
    assert row is not None
    assert isinstance(row.calculated_at, datetime)
    assert row.rule_version == "v2"
