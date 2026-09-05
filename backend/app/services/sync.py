from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import (
    DailyBar,
    FundamentalSnapshot,
    LatestQuote,
    NewsArticle,
    ScoreSnapshot,
    Stock,
    SyncRun,
    TradeCalendar,
    WatchlistItem,
)
from app.providers.akshare_news import AkshareNewsSource
from app.providers.base import (
    MarketDataProvider,
    NewsData,
    ProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from app.providers.llm import LlmNewsAnalyzer
from app.providers.mock import MockProvider
from app.providers.tushare import TushareProvider
from app.services.fundamentals import snapshot_metrics
from app.services.indicators import calculate_indicators
from app.services.news_analysis import NewsAnalyzer, analyze_article
from app.services.scoring import score_stock

logger = logging.getLogger(__name__)
DEFAULT_WATCHLIST = ["600519.SH", "300750.SZ", "601318.SH", "688981.SH"]
_SYNC_LOCK = threading.Lock()

# 账号权限类失败（如 TuShare 积分档位不够某接口）后临时禁用该 job，避免调度器
# 反复重试刷日志/SyncRun。窗口到期后会自动复位重试一次；手动触发不受影响。
_DISABLED_JOBS: dict[str, datetime] = {}
_DISABLED_LOCK = threading.Lock()
_DISABLE_WINDOW = timedelta(hours=12)


def disable_job(job_type: str, window: timedelta = _DISABLE_WINDOW) -> None:
    with _DISABLED_LOCK:
        _DISABLED_JOBS[job_type] = datetime.now() + window


def job_disabled(job_type: str) -> bool:
    with _DISABLED_LOCK:
        until = _DISABLED_JOBS.get(job_type)
        if until is None:
            return False
        if until <= datetime.now():
            _DISABLED_JOBS.pop(job_type, None)
            return False
        return True


@lru_cache(maxsize=1)
def get_provider() -> MarketDataProvider:
    settings = get_settings()
    token = settings.tushare_token.get_secret_value()
    if settings.use_mock_data:
        return MockProvider()
    if not token:
        raise RuntimeError("真实数据模式需要配置 TUSHARE_TOKEN")
    return TushareProvider(token, settings.tushare_calls_per_minute, settings.tushare_daily_budget)


def classify_error(exc: BaseException) -> str:
    """把异常归类为稳定的 error_class，供 SyncRun 记录与界面降级提示使用。"""
    if isinstance(exc, ProviderPermissionError):
        return "permission"
    if isinstance(exc, ProviderRateLimitError):
        return "rate_limit"
    import httpx

    if isinstance(exc, httpx.HTTPError):
        return "network"
    if isinstance(exc, RuntimeError) and "TUSHARE_TOKEN" in str(exc):
        return "permission"
    if isinstance(exc, ProviderError):
        return "data"
    return "data"


def _upsert_stocks(session: Session, provider: MarketDataProvider) -> int:
    """幂等刷新全市场股票清单，返回处理条数。"""
    count = 0
    for item in provider.stocks():
        stock = session.get(Stock, item.ts_code) or Stock(ts_code=item.ts_code)
        stock.symbol = item.symbol
        stock.name = item.name
        stock.industry = item.industry
        stock.market = item.market
        stock.is_active = True
        session.add(stock)
        count += 1
    session.flush()
    return count


def seed_demo(session: Session) -> None:
    provider = MockProvider()
    if not session.scalar(select(Stock.ts_code).limit(1)):
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
        session.flush()
        for code in DEFAULT_WATCHLIST:
            session.add(WatchlistItem(ts_code=code))
        for stock in provider.stocks():
            for bar in provider.history(stock.ts_code, 90):
                session.add(
                    DailyBar(
                        ts_code=bar.ts_code,
                        trade_date=bar.trade_date,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        pre_close=bar.pre_close,
                        pct_chg=bar.pct_chg,
                        volume=bar.volume,
                        amount=bar.amount,
                        source="mock",
                    )
                )
            index = provider.stocks().index(stock)
            session.add(
                FundamentalSnapshot(
                    ts_code=stock.ts_code,
                    trade_date=date.today(),
                    pe_ttm=14 + index * 5.2,
                    pb=1.3 + index * 0.6,
                    total_mv=1800 + index * 850,
                    turnover_rate=1.1 + index * 0.35,
                    roe=18 - index * 0.8,
                    revenue_growth=11 + index * 2.1,
                    profit_growth=13 + index * 2.4,
                    debt_ratio=28 + index * 3.5,
                )
            )
        news = [
            ("600519.SH", "消费旺季临近，龙头酒企渠道库存保持稳健", "积极", 0.62),
            ("300750.SZ", "动力电池海外订单持续增长，产业链景气改善", "订单", 0.74),
            ("688981.SH", "先进制程扩产进度受到市场关注", "产能", 0.31),
            ("601318.SH", "保险负债端改革推进，价值率延续改善", "业绩", 0.53),
        ]
        for idx, (code, title, tag, sentiment) in enumerate(news):
            session.add(
                NewsArticle(
                    ts_code=code,
                    title=title,
                    source_name="演示资讯",
                    url=f"mock://news/{idx}",
                    published_at=datetime.now() - timedelta(hours=idx * 3 + 1),
                    summary="系统基于公开信息生成的演示摘要，真实模式将由 LLM 结构化分析。",
                    sentiment=sentiment,
                    event_tag=tag,
                    confidence=0.62 + idx * 0.03,
                    model_version="demo",
                    analysis_mode="demo",
                )
            )
    refresh_quotes(session, provider)
    recalculate_scores(session)
    session.commit()


def _allowed_codes(session: Session) -> set[str]:
    """当前已入库(上市中)股票代码集合，用于过滤全市场日线/估值里的孤儿代码。"""
    return set(session.scalars(select(Stock.ts_code)).all())


def _latest_missing_open_date(session: Session, before: date, lookback: int = 7) -> date | None:
    """最近一个已开市(交易日历)但尚无日线数据的交易日，用于补拉遗漏。

    背景：收盘 job 若在行情数据未就绪时(如 15:20 上游尚未出全)拉到 0 行，
    当天日线就会永久缺失。下一次 market 同步据此自动回补。
    """
    window_start = before - timedelta(days=lookback)
    open_dates = list(
        session.execute(
            select(TradeCalendar.cal_date).where(
                TradeCalendar.cal_date >= window_start,
                TradeCalendar.cal_date < before,
                TradeCalendar.is_open.is_(True),
            )
        ).scalars()
    )
    if not open_dates:
        return None
    have = set(
        session.execute(
            select(DailyBar.trade_date).where(
                DailyBar.trade_date >= window_start,
                DailyBar.trade_date < before,
            )
        ).scalars()
    )
    for cal_date in sorted(open_dates, reverse=True):
        if cal_date not in have:
            return cal_date
    return None


def _sync_market_day(
    session: Session,
    provider: MarketDataProvider,
    allowed: set[str],
    trade_date: date,
) -> int:
    """拉取并 upsert 指定交易日的全市场日线 + 估值快照(daily_basic)，返回日线条数。"""
    bars = [bar for bar in provider.daily(trade_date) if bar.ts_code in allowed]
    session.autoflush = False
    try:
        for bar_data in bars:
            bar = session.scalar(
                select(DailyBar).where(
                    DailyBar.ts_code == bar_data.ts_code,
                    DailyBar.trade_date == bar_data.trade_date,
                )
            )
            values = {
                "open": bar_data.open,
                "high": bar_data.high,
                "low": bar_data.low,
                "close": bar_data.close,
                "pre_close": bar_data.pre_close,
                "pct_chg": bar_data.pct_chg,
                "volume": bar_data.volume,
                "amount": bar_data.amount,
                "source": provider.name,
            }
            if bar is None:
                session.add(
                    DailyBar(ts_code=bar_data.ts_code, trade_date=bar_data.trade_date, **values)
                )
            else:
                for key, value in values.items():
                    setattr(bar, key, value)

        for fundamental_data in provider.fundamentals(trade_date):
            if fundamental_data.ts_code not in allowed:
                continue
            snapshot = session.scalar(
                select(FundamentalSnapshot).where(
                    FundamentalSnapshot.ts_code == fundamental_data.ts_code,
                    FundamentalSnapshot.trade_date == fundamental_data.trade_date,
                )
            )
            values = {
                "pe_ttm": fundamental_data.pe_ttm,
                "pb": fundamental_data.pb,
                "total_mv": fundamental_data.total_mv,
                "turnover_rate": fundamental_data.turnover_rate,
            }
            if snapshot is None:
                session.add(
                    FundamentalSnapshot(
                        ts_code=fundamental_data.ts_code,
                        trade_date=fundamental_data.trade_date,
                        **values,
                    )
                )
            else:
                for key, value in values.items():
                    setattr(snapshot, key, value)
    finally:
        session.autoflush = True
    session.flush()
    return len(bars)


def sync_market(session: Session, trade_date: date | None = None) -> int:
    """同步全市场日线。默认同步今天；若指定 trade_date 则只同步该日(用于回补)。

    未指定日期时，顺带补拉最近一个「已开市但缺失」的交易日，防止收盘 job
    遇上游数据未就绪而漏掉整天。
    """
    provider = get_provider()
    _upsert_stocks(session, provider)
    allowed = _allowed_codes(session)

    dates: list[date] = []
    target = trade_date or date.today()
    dates.append(target)
    if trade_date is None:
        missing = _latest_missing_open_date(session, target)
        if missing is not None:
            dates.append(missing)

    handled = 0
    for sync_date in dates:
        handled += _sync_market_day(session, provider, allowed, sync_date)
    recalculate_scores(session)
    return handled


def bootstrap_market(session: Session) -> int:
    settings = get_settings()
    provider = get_provider()
    _upsert_stocks(session, provider)

    if not session.scalar(select(WatchlistItem.ts_code).limit(1)):
        available_codes = _allowed_codes(session)
        for code in DEFAULT_WATCHLIST:
            if code in available_codes:
                session.add(WatchlistItem(ts_code=code))
    session.flush()
    allowed = _allowed_codes(session)

    current = date.today()
    trade_dates: list[date] = []
    while len(trade_dates) < max(20, settings.bootstrap_history_days):
        if current.weekday() < 5:
            trade_dates.append(current)
        current -= timedelta(days=1)

    # 全量回填为海量写入：关掉 autoflush 避免每条 scalar 触发整表 flush，
    # 并分段 flush+expunge 控制 1.6G 小内存机上的 ORM 身份表膨胀。
    session.autoflush = False
    total = 0
    pending = 0
    try:
        for trade_date in reversed(trade_dates):
            for bar in provider.daily(trade_date):
                if bar.ts_code not in allowed:
                    # 退市/更名等已不在当前上市清单的代码：跳过，避免外键悬空
                    continue
                existing = session.scalar(
                    select(DailyBar).where(
                        DailyBar.ts_code == bar.ts_code,
                        DailyBar.trade_date == bar.trade_date,
                    )
                )
                if existing is None:
                    session.add(
                        DailyBar(
                            ts_code=bar.ts_code,
                            trade_date=bar.trade_date,
                            open=bar.open,
                            high=bar.high,
                            low=bar.low,
                            close=bar.close,
                            pre_close=bar.pre_close,
                            pct_chg=bar.pct_chg,
                            volume=bar.volume,
                            amount=bar.amount,
                            source=provider.name,
                        )
                    )
                    total += 1
                    pending += 1
                    if pending >= 40000:
                        session.flush()
                        session.expunge_all()
                        pending = 0

        for fundamental_data in provider.fundamentals(date.today()):
            if fundamental_data.ts_code not in allowed:
                continue
            snapshot = session.scalar(
                select(FundamentalSnapshot).where(
                    FundamentalSnapshot.ts_code == fundamental_data.ts_code,
                    FundamentalSnapshot.trade_date == fundamental_data.trade_date,
                )
            )
            if snapshot is None:
                session.add(
                    FundamentalSnapshot(
                        ts_code=fundamental_data.ts_code,
                        trade_date=fundamental_data.trade_date,
                        pe_ttm=fundamental_data.pe_ttm,
                        pb=fundamental_data.pb,
                        total_mv=fundamental_data.total_mv,
                        turnover_rate=fundamental_data.turnover_rate,
                    )
                )
    finally:
        session.autoflush = True
    session.flush()
    refresh_quotes(session, provider)
    recalculate_scores(session)
    return total


def sync_stocks(session: Session) -> int:
    """仅刷新全市场股票清单（每周执行），不拉日线。"""
    provider = get_provider()
    return _upsert_stocks(session, provider)


def sync_calendar(session: Session, span: int = 35) -> int:
    """拉取以今天为中心的交易日历，写入 TradeCalendar。"""
    provider = get_provider()
    today = date.today()
    rows = provider.calendar(today - timedelta(days=span), today + timedelta(days=span))
    count = 0
    for cal_date, is_open in rows:
        row = session.get(TradeCalendar, cal_date) or TradeCalendar(cal_date=cal_date)
        row.is_open = is_open
        session.add(row)
        count += 1
    session.flush()
    return count


def sync_fundamentals(session: Session) -> int:
    """对自选股做财务指标（ROE/营收与净利增长/负债率）增量回填。

    以报告期为 trade_date 写入独立快照行，单票数据缺失时跳过不整批失败。
    """
    provider = get_provider()
    codes = list(session.scalars(select(WatchlistItem.ts_code)))
    if not codes:
        return 0
    count = 0
    for data in provider.financials(codes):
        snapshot = session.scalar(
            select(FundamentalSnapshot).where(
                FundamentalSnapshot.ts_code == data.ts_code,
                FundamentalSnapshot.trade_date == data.trade_date,
            )
        )
        values = {
            "roe": data.roe,
            "revenue_growth": data.revenue_growth,
            "profit_growth": data.profit_growth,
            "debt_ratio": data.debt_ratio,
        }
        if snapshot is None:
            snapshot = FundamentalSnapshot(ts_code=data.ts_code, trade_date=data.trade_date)
            session.add(snapshot)
        for key, value in values.items():
            if value is not None:
                setattr(snapshot, key, value)
        count += 1
    session.flush()
    recalculate_scores(session)
    return count


def _add_news_article(
    session: Session,
    analyzer: NewsAnalyzer | None,
    settings: Settings,
    ts_code: str,
    item: NewsData,
    seen: set[str],
) -> tuple[int, bool]:
    """单条新闻按 url 去重后提交 LLM 分析并入库。

    返回 (是否新增 0/1, 是否情绪性新增)。情绪性(llm/demo)才有 sentiment 会影响评分，
    raw(无 LLM key/降级)不带情绪、不影响评分，调用方据此决定要不要跑全市场重算。
    """
    if item.url in seen or session.scalar(
        select(NewsArticle.id).where(NewsArticle.url == item.url)
    ):
        return 0, False
    seen.add(item.url)
    analysis = analyze_article(item.title, item.content, analyzer, settings.llm_model)
    session.add(
        NewsArticle(
            ts_code=ts_code,
            title=item.title[:300],
            source_name=item.source[:80],
            url=item.url,
            published_at=item.published_at,
            summary=str(analysis["summary"]),
            sentiment=analysis["sentiment"],
            event_tag=str(analysis["event_tag"]),
            confidence=analysis["confidence"],
            model_version=str(analysis["model_version"]),
            analysis_mode=str(analysis["analysis_mode"]),
        )
    )
    return 1, str(analysis["analysis_mode"]) in ("llm", "demo")


def _sync_news_stream(
    session: Session,
    analyzer: NewsAnalyzer | None,
    settings: Settings,
    watchlist: dict[str, tuple[str, str]],
    provider: MarketDataProvider,
    start: datetime,
    end: datetime,
) -> int:
    """TuShare/mock 路径：拉全市场流后按股票名/代码全文匹配自选股。"""
    seen: set[str] = set()
    inserted = 0
    llm_added = False
    for item in provider.news(start, end):
        haystack = f"{item.title}\n{item.content}"
        matched_code = next(
            (
                code
                for code, (name, symbol) in watchlist.items()
                if name in haystack or symbol in haystack or code in haystack
            ),
            None,
        )
        if matched_code is None:
            continue
        added, llm = _add_news_article(session, analyzer, settings, matched_code, item, seen)
        inserted += added
        llm_added = llm_added or llm
    session.flush()
    if llm_added:
        recalculate_scores(session)
    return inserted


def _sync_news_akshare(
    session: Session,
    analyzer: NewsAnalyzer | None,
    settings: Settings,
    universe: dict[str, tuple[str, str]],
    start: datetime,
) -> int:
    """akshare 路径：按 universe（自选 ∪ 综合评分前 N）逐个拉东财个股新闻，直接归属该股。

    universe 为空时直接返回；个别股票拉取失败只跳过该股，全部失败才聚合成一条
    告警，避免自选+top-N 上百只逐条刷日志噪音。
    """
    if not universe:
        return 0
    source = AkshareNewsSource()
    seen: set[str] = set()
    inserted = 0
    llm_added = False
    failures = 0
    for code, (_, symbol) in universe.items():
        try:
            items = source.fetch(symbol)
        except ProviderError:
            failures += 1
            continue
        for item in items:
            if item.published_at < start:
                continue
            added, llm = _add_news_article(session, analyzer, settings, code, item, seen)
            inserted += added
            llm_added = llm_added or llm
    if failures:
        if failures == len(universe):
            logger.warning("AkShare 拉取 %d 只股票新闻全部失败，跳过本轮", failures)
        else:
            logger.info(
                "AkShare 拉取 %d/%d 只股票新闻失败，已跳过", failures, len(universe)
            )
    session.flush()
    if llm_added:
        recalculate_scores(session)
    return inserted


def sync_news(session: Session) -> int:
    settings = get_settings()
    end = datetime.now()
    start = end - timedelta(minutes=max(30, settings.news_refresh_minutes * 2))
    watchlist = {
        row.ts_code: (row.stock.name, row.stock.symbol)
        for row in session.scalars(select(WatchlistItem)).all()
    }
    api_key = settings.llm_api_key.get_secret_value()
    analyzer = (
        LlmNewsAnalyzer(api_key, settings.llm_base_url, settings.llm_model) if api_key else None
    )
    if not settings.use_mock_data and settings.news_source.strip().lower() == "akshare":
        # 真实 akshare：universe = 自选 ∪ 综合评分前 news_top_n(去重)；仅自选时与旧行为一致
        universe = dict(watchlist)
        if settings.news_top_n > 0:
            top_rows = session.execute(
                select(Stock.ts_code, Stock.name, Stock.symbol)
                .join(ScoreSnapshot, ScoreSnapshot.ts_code == Stock.ts_code)
                .where(ScoreSnapshot.total_score.is_not(None))
                .order_by(ScoreSnapshot.total_score.desc(), Stock.ts_code)
                .limit(settings.news_top_n)
            ).all()
            for ts_code, name, symbol in top_rows:
                universe.setdefault(ts_code, (name, symbol))
        return _sync_news_akshare(session, analyzer, settings, universe, start)
    provider = get_provider()
    return _sync_news_stream(session, analyzer, settings, watchlist, provider, start, end)


def refresh_quotes(session: Session, provider: MarketDataProvider | None = None) -> int:
    provider = provider or get_provider()
    codes = list(session.scalars(select(WatchlistItem.ts_code)))
    quotes = provider.quotes(codes)
    for quote in quotes:
        current = session.get(LatestQuote, quote.ts_code)
        values = dict(
            price=quote.price,
            pct_chg=quote.pct_chg,
            volume=quote.volume,
            amount=quote.amount,
            quote_time=quote.quote_time,
            source=quote.source,
            is_stale=quote.is_stale,
            fetched_at=datetime.now(),
        )
        if current:
            for key, value in values.items():
                setattr(current, key, value)
        else:
            session.add(LatestQuote(ts_code=quote.ts_code, **values))
    session.flush()
    return len(quotes)


def recalculate_scores(session: Session) -> None:
    cutoff = datetime.now() - timedelta(days=3)
    for stock in session.scalars(select(Stock)).all():
        bars = session.scalars(
            select(DailyBar).where(DailyBar.ts_code == stock.ts_code).order_by(DailyBar.trade_date)
        ).all()
        indicators = calculate_indicators([{"close": bar.close} for bar in bars])
        snapshots = list(
            session.scalars(
                select(FundamentalSnapshot)
                .where(FundamentalSnapshot.ts_code == stock.ts_code)
                .order_by(FundamentalSnapshot.trade_date.desc())
            ).all()
        )
        # 估值取自最新交易日快照，质量指标取最新有值的一期财务快照
        data = snapshot_metrics(snapshots)
        sentiments = list(
            session.scalars(
                select(NewsArticle.sentiment).where(
                    NewsArticle.ts_code == stock.ts_code,
                    NewsArticle.published_at >= cutoff,
                    NewsArticle.sentiment.is_not(None),
                )
            )
        )
        sentiment = sum(sentiments) / len(sentiments) if sentiments else None
        result = score_stock(indicators, data, sentiment)
        score = session.get(ScoreSnapshot, stock.ts_code) or ScoreSnapshot(ts_code=stock.ts_code)
        score.total_score, score.technical_score, score.fundamental_score = (
            result.total,
            result.technical,
            result.fundamental,
        )
        score.sentiment_score, score.risk_score, score.coverage = (
            result.sentiment,
            result.risk,
            result.coverage,
        )
        score.risk_level, score.explanations, score.calculated_at = (
            result.risk_level,
            result.explanations,
            datetime.now(),
        )
        session.add(score)
    session.flush()


def run_sync(session: Session, job_type: str) -> SyncRun:
    with _SYNC_LOCK:
        return _run_sync_locked(session, job_type)


def _run_sync_locked(session: Session, job_type: str) -> SyncRun:
    run = SyncRun(job_type=job_type, status="running")
    session.add(run)
    session.commit()
    try:
        if job_type == "scores":
            recalculate_scores(session)
            count = session.scalar(select(func.count()).select_from(Stock)) or 0
        elif job_type in {
            "quotes",
            "market",
            "news",
            "stocks",
            "calendar",
            "fundamentals",
        }:
            handlers: dict[str, Callable[[Session], int]] = {
                "quotes": refresh_quotes,
                "market": sync_market,
                "news": sync_news,
                "stocks": sync_stocks,
                "calendar": sync_calendar,
                "fundamentals": sync_fundamentals,
            }
            count = handlers[job_type](session)
        elif job_type == "bootstrap":
            if get_settings().use_mock_data:
                seed_demo(session)
                count = session.scalar(select(func.count()).select_from(Stock)) or 0
            else:
                count = bootstrap_market(session)
        else:
            raise ValueError(f"未知任务类型: {job_type}")
        run.status = "success"
        run.message = f"已更新 {count} 项数据"
        run.items_updated = count
    except Exception as exc:
        run_id = run.id
        session.rollback()
        run = session.get(SyncRun, run_id)
        if run is None:
            run = SyncRun(job_type=job_type)
            session.add(run)
        run.status = "failed"
        run.message = str(exc)[:280]
        run.error_class = classify_error(exc)
        if run.error_class == "permission":
            disable_job(job_type)
    run.finished_at = datetime.now()
    session.add(run)
    session.commit()
    return run
