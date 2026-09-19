from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ApiMessage,
    DailyPicks,
    NewsView,
    QuoteView,
    ScoreExplanation,
    StockView,
    SyncRequest,
    SystemStatus,
    TimingAdvice,
    TopBoardRow,
    WatchlistCreate,
)
from app.core.config import get_settings
from app.db.models import (
    DailyBar,
    FundamentalSnapshot,
    LatestQuote,
    NewsArticle,
    ProviderStatus,
    ScoreSnapshot,
    Stock,
    SyncRun,
    WatchlistItem,
)
from app.db.session import get_db
from app.services import picks
from app.services.sync import get_provider, run_sync
from app.services.timing import compute_timing_for_codes

router = APIRouter(prefix="/api/v1")

INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
}

# 首页大屏榜单口径：剔除科创板(688/689.SH)与创业板(300/301.SZ)，只呈现沪深主板标的。
# 用 ts_code 前缀判定、不依赖 Stock.market 文本标签，两种 provider 下都成立。
_NON_MAIN_BOARD = or_(
    Stock.ts_code.like("688%.SH"),
    Stock.ts_code.like("689%.SH"),
    Stock.ts_code.like("300%.SZ"),
    Stock.ts_code.like("301%.SZ"),
)

def stock_view(
    row: tuple[
        Stock, LatestQuote | None, ScoreSnapshot | None, FundamentalSnapshot | None, DailyBar | None
    ],
) -> StockView:
    """把一行 `services.picks.stock_query` 结果映射成对外视图。

    `bar` 是**全局最近交易日**的那根日线，只为一件事存在：`latest_quotes` 为空时兜底价格。
    实测盘后新浪不返数据、`latest_quotes` 会掉到 0 行，于是每个列表的价格列整片 `--`，
    而最近收盘价其实一直躺在 daily_bars 里（2026-09-16 实测覆盖 5550/5564 只）。
    兜底时把来源标成 `close` 并带上日期，让页面能写「收盘 09-16」而不是让人以为这是实时价。
    `is_stale` 保持 True——没有实时报价这件事依然是事实，只是不再等于「没有价格」。
    """
    stock, quote, score, fundamental, bar = row
    if quote is not None:
        price, pct_chg, price_source = quote.price, quote.pct_chg, "quote"
    elif bar is not None:
        price, pct_chg, price_source = bar.close, bar.pct_chg, "close"
    else:
        price, pct_chg, price_source = None, None, "none"
    return StockView(
        ts_code=stock.ts_code,
        symbol=stock.symbol,
        name=stock.name,
        industry=stock.industry,
        market=stock.market,
        price=price,
        pct_chg=pct_chg,
        price_source=price_source,
        # 只在价格真的来自那根收盘 bar 时才给日期。有实时报价时 bar 也在 join 里，
        # 顺手把它的日期带出去会让人以为这个价是收盘价——所以这里必须跟着 price_source 走。
        price_date=bar.trade_date.isoformat() if price_source == "close" and bar else None,
        is_stale=quote.is_stale if quote else True,
        quote_time=quote.quote_time if quote else None,
        total_score=score.total_score if score else None,
        technical_score=score.technical_score if score else None,
        fundamental_score=score.fundamental_score if score else None,
        sentiment_score=score.sentiment_score if score else None,
        coverage=score.coverage if score else None,
        risk_level=score.risk_level if score else None,
        explanations=score.explanations if score else [],
        pe_ttm=fundamental.pe_ttm if fundamental else None,
        pb=fundamental.pb if fundamental else None,
    )


def _provider_capabilities() -> list[str]:
    try:
        provider = get_provider()
    except Exception:
        return []
    return sorted(capability.value for capability in provider.capabilities())


_SPARK_CACHE: dict[str, tuple[float, list[dict[str, object]]]] = {}
_SPARK_TTL_SECONDS = 300.0


def index_spark(code: str, days: int = 40) -> list[dict[str, object]]:
    """指数最近收盘序列，带进程内 TTL 缓存，避免每次轮询都请求指数日线。"""
    now = time.monotonic()
    cached = _SPARK_CACHE.get(code)
    if cached and now - cached[0] < _SPARK_TTL_SECONDS:
        return cached[1]
    try:
        provider = get_provider()
        series = provider.index_history(code, days)
    except Exception:
        series = []
    _SPARK_CACHE[code] = (now, series)
    return series


@router.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "timestamp": datetime.now(), "service": "A股洞察终端"}


@router.get("/system/status", response_model=SystemStatus)
def system_status(db: Session = Depends(get_db)) -> SystemStatus:
    settings = get_settings()
    token_configured = bool(settings.tushare_token.get_secret_value())
    mock_mode = settings.use_mock_data
    provider = (
        "Mock 演示源"
        if mock_mode
        else ("TuShare Pro" if token_configured else "未配置")
    )
    status_at = db.scalar(
        select(func.max(ProviderStatus.updated_at)).select_from(ProviderStatus)
    )
    return SystemStatus(
        provider=provider,
        tushare_configured=token_configured,
        llm_configured=bool(settings.llm_api_key.get_secret_value()),
        # 只暴露「配没配」：webhook 是凭据，永不回传 URL 本身（同 `tushare_configured`）。
        notify_configured=settings.notify_configured,
        mock_mode=mock_mode,
        quote_refresh_seconds=settings.quote_refresh_seconds,
        updated_at=datetime.now(),
        data_mode="mock" if mock_mode else ("live" if token_configured else "unconfigured"),
        capabilities=_provider_capabilities(),
        provider_status_at=status_at,
    )


@router.get("/market/overview")
def market_overview(db: Session = Depends(get_db)) -> dict[str, object]:
    provider = get_provider()
    index_quotes = provider.quotes(list(INDEX_NAMES))
    indices = []
    for quote in index_quotes:
        indices.append(
            {
                "code": quote.ts_code,
                "name": INDEX_NAMES.get(quote.ts_code, quote.ts_code),
                "price": quote.price,
                "pct_chg": quote.pct_chg,
                "spark": index_spark(quote.ts_code),
            }
        )
    latest = db.scalar(select(func.max(DailyBar.trade_date)))
    breadth: dict[str, object] = {"rising": 0, "falling": 0, "flat": 0, "average_pct": 0.0}
    if latest:
        changes = list(
            db.scalars(
                select(DailyBar.pct_chg).where(DailyBar.trade_date == latest)
            ).all()
        )
        rising = sum(1 for value in changes if value > 0)
        falling = sum(1 for value in changes if value < 0)
        breadth = {
            "rising": rising,
            "falling": falling,
            "flat": len(changes) - rising - falling,
            "average_pct": round(sum(changes) / len(changes), 2) if changes else 0.0,
            "as_of": latest.isoformat(),
            "samples": len(changes),
        }
    return {
        "indices": indices,
        "breadth": breadth,
        "as_of": datetime.now(),
        "source": "mock" if get_settings().use_mock_data else "tushare",
    }


@router.get("/stocks/search", response_model=list[StockView])
def search_stocks(
    q: str = Query(default="", max_length=40),
    limit: int = Query(default=10, le=30),
    db: Session = Depends(get_db),
) -> list[StockView]:
    query = picks.stock_query()
    if q:
        query = query.where(
            or_(Stock.name.contains(q), Stock.symbol.contains(q), Stock.ts_code.contains(q.upper()))
        )
    return [stock_view(row) for row in db.execute(query.limit(limit)).all()]


@router.get("/stocks/{ts_code}", response_model=StockView)
def stock_detail(ts_code: str, db: Session = Depends(get_db)) -> StockView:
    row = db.execute(picks.stock_query().where(Stock.ts_code == ts_code.upper())).first()
    if not row:
        raise HTTPException(404, "未找到该股票")
    return stock_view(row)


@router.get("/stocks/{ts_code}/daily-bars")
def daily_bars(
    ts_code: str, limit: int = Query(default=90, ge=20, le=250), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    rows = db.scalars(
        select(DailyBar)
        .where(DailyBar.ts_code == ts_code.upper())
        .order_by(DailyBar.trade_date.desc())
        .limit(limit)
    ).all()
    return [
        {
            "date": row.trade_date,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
            "pct_chg": row.pct_chg,
        }
        for row in reversed(rows)
    ]


@router.get("/stocks/{ts_code}/news", response_model=list[NewsView])
def stock_news(ts_code: str, db: Session = Depends(get_db)) -> list[NewsView]:
    rows = db.scalars(
        select(NewsArticle)
        .where(NewsArticle.ts_code == ts_code.upper())
        .order_by(NewsArticle.published_at.desc())
    ).all()
    return [
        NewsView(
            id=row.id,
            title=row.title,
            source=row.source_name,
            published_at=row.published_at,
            summary=row.summary,
            sentiment=row.sentiment,
            event_tag=row.event_tag,
            confidence=row.confidence,
            model_version=row.model_version,
            analysis_mode=row.analysis_mode,
        )
        for row in rows
    ]


@router.get("/stocks/{ts_code}/score-explanation", response_model=ScoreExplanation)
def stock_score_explanation(ts_code: str, db: Session = Depends(get_db)) -> ScoreExplanation:
    code = ts_code.upper()
    stock = db.get(Stock, code)
    if not stock:
        raise HTTPException(404, "未找到该股票")
    score = db.get(ScoreSnapshot, code)
    quote = db.get(LatestQuote, code)
    if score is None:
        raise HTTPException(404, "该股票暂无评分快照，请先执行评分同步")
    return ScoreExplanation(
        ts_code=code,
        name=stock.name,
        total_score=score.total_score,
        technical_score=score.technical_score,
        fundamental_score=score.fundamental_score,
        sentiment_score=score.sentiment_score,
        risk_score=score.risk_score,
        coverage=score.coverage,
        risk_level=score.risk_level,
        explanations=score.explanations or [],
        calculated_at=score.calculated_at,
        rule_version=score.rule_version,
        quote_time=quote.quote_time if quote else None,
        is_stale=quote.is_stale if quote else True,
    )


@router.get("/screener", response_model=list[StockView])
def screener(
    min_score: float = Query(default=0, ge=0, le=100),
    industry: str = Query(default="", max_length=40),
    sort: str = Query(default="score", pattern=r"^(score|price|pct_chg|pe)$"),
    min_coverage: float = Query(default=0, ge=0, le=1),
    limit: int = Query(default=50, le=100),
    db: Session = Depends(get_db),
) -> list[StockView]:
    query = picks.stock_query().where(ScoreSnapshot.total_score >= min_score)
    if min_coverage > 0:
        query = query.where(ScoreSnapshot.coverage >= min_coverage)
    if industry:
        query = query.where(Stock.industry == industry)
    if sort == "pe":
        query = query.where(FundamentalSnapshot.pe_ttm.is_not(None)).order_by(
            FundamentalSnapshot.pe_ttm.asc()
        )
    elif sort == "price":
        query = query.order_by(LatestQuote.price.desc().nullslast())
    elif sort == "pct_chg":
        query = query.order_by(LatestQuote.pct_chg.desc().nullslast())
    else:
        query = query.order_by(ScoreSnapshot.total_score.desc())
    query = query.limit(limit)
    rows = db.execute(query).all()
    views = [stock_view(row) for row in rows]
    # 价格参考：优先取实时非 stale 报价；盘后无报价时由 compute_timing_for_codes 回退到最近收盘
    fresh_price = {
        r[0].ts_code: (r[1].price if r[1] and r[1].price and not r[1].is_stale else None)
        for r in rows
    }
    timings = compute_timing_for_codes(db, [view.ts_code for view in views], fresh_price)
    result: list[StockView] = []
    for view in views:
        advice = timings.get(view.ts_code)
        result.append(
            view.model_copy(update={"timing": TimingAdvice(**advice) if advice else None})
        )
    return result


@router.get("/screener/top", response_model=list[TopBoardRow])
def screener_top(
    n: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[TopBoardRow]:
    """综合评分 TOP-N 榜单（首页数据大屏数据源）。

    榜单只呈现沪深主板标的，剔除科创板(688/689.SH)与创业板(300/301.SZ)，排名
    在剩余标的内重新计算。排名以 score_snapshots.total_score 为准（同分按 ts_code
    稳定序）；报价/估值沿用 `services.picks.stock_query` 既有 outerjoin 口径，榜单不接 timing（避免
    全量算操作时机）。新闻简报两条聚合口径与 scoring.recalculate_scores 一致，naive
    3 天窗口：近 3 天情绪均值不限定 analysis_mode（raw 的 sentiment 恒 None 被天然
    排除），最新一条仅取 llm/demo。
    """
    rows = db.execute(
        picks.stock_query()
        .where(ScoreSnapshot.total_score.is_not(None), ~_NON_MAIN_BOARD)
        .order_by(ScoreSnapshot.total_score.desc(), Stock.ts_code)
        .limit(n)
    ).all()
    views = [stock_view(row) for row in rows]
    codes = [view.ts_code for view in views]
    if not codes:
        return []

    cutoff = datetime.now() - timedelta(days=3)
    news_agg = {
        ts_code: (avg_sent, cnt)
        for ts_code, avg_sent, cnt in db.execute(
            select(
                NewsArticle.ts_code,
                func.avg(NewsArticle.sentiment),
                func.count(NewsArticle.id),
            )
            .where(
                NewsArticle.ts_code.in_(codes),
                NewsArticle.published_at >= cutoff,
                NewsArticle.sentiment.is_not(None),
            )
            .group_by(NewsArticle.ts_code)
        ).all()
    }
    latest_ranked = (
        select(
            NewsArticle.ts_code,
            NewsArticle.title,
            NewsArticle.sentiment,
            NewsArticle.event_tag,
            NewsArticle.published_at,
            func.row_number()
            .over(
                partition_by=NewsArticle.ts_code,
                order_by=(NewsArticle.published_at.desc(), NewsArticle.id.desc()),
            )
            .label("rn"),
        )
        .where(
            NewsArticle.ts_code.in_(codes),
            NewsArticle.analysis_mode.in_(("llm", "demo")),
        )
        .subquery()
    )
    latest_rows = db.execute(
        select(
            latest_ranked.c.ts_code,
            latest_ranked.c.title,
            latest_ranked.c.sentiment,
            latest_ranked.c.event_tag,
            latest_ranked.c.published_at,
        ).where(latest_ranked.c.rn == 1)
    ).all()
    latest_by_code: dict[str, Any] = {row.ts_code: row for row in latest_rows}

    result: list[TopBoardRow] = []
    for idx, view in enumerate(views, start=1):
        avg_sent, cnt = news_agg.get(view.ts_code, (None, 0))
        recent = latest_by_code.get(view.ts_code)
        data = view.model_dump()
        data.update(
            rank=idx,
            news_sentiment_avg=round(avg_sent, 4) if avg_sent is not None else None,
            news_3d_count=cnt,
            news_tag=recent.event_tag if recent is not None else None,
            news_sentiment=recent.sentiment if recent is not None else None,
            news_title=recent.title if recent is not None else None,
            news_published_at=recent.published_at if recent is not None else None,
        )
        result.append(TopBoardRow(**data))
    return result


@router.get("/picks/daily", response_model=DailyPicks)
def picks_daily(db: Session = Depends(get_db)) -> DailyPicks:
    """次日买点候选（首页"次日买点候选"数据源）。

    选股口径在 `services/picks.select_daily_picks`，这里只把它落到 `StockView` 上。
    `/strategy/simple`（明日操作页）与它共用前三步（同一个 SQL 门槛、同一次现算时机），
    **只差第 3 步**：那边每板块至多 10 只、按市值排序、不封顶，这边低风险优先、单行业 2 只、
    最多 8 只。所以两页的**候选池相同、名单不必互相包含**。

    纯实时计算、不落库。basis_date 为最近一根日线 trade_date（盘中/盘后均为最近收盘日）。
    入选主创板不限，低风险门槛与分散口径已兜底。
    """
    views = [
        stock_view(row).model_copy(update={"timing": TimingAdvice(**advice)})
        for row, advice in picks.select_daily_picks(db)
    ]
    return DailyPicks(
        basis_date=picks.picks_basis_date(db),
        note=picks.picks_note(),
        picks=views,
    )


@router.get("/watchlist", response_model=list[StockView])
def watchlist(db: Session = Depends(get_db)) -> list[StockView]:
    query = (
        picks.stock_query()
        .join(WatchlistItem, WatchlistItem.ts_code == Stock.ts_code)
        .order_by(WatchlistItem.created_at)
    )
    return [stock_view(row) for row in db.execute(query).all()]


@router.get("/watchlist/quotes", response_model=list[QuoteView])
def watchlist_quotes(db: Session = Depends(get_db)) -> list[QuoteView]:
    rows = db.execute(
        select(Stock.name, LatestQuote)
        .join(WatchlistItem, WatchlistItem.ts_code == Stock.ts_code)
        .join(LatestQuote, LatestQuote.ts_code == Stock.ts_code)
        .order_by(WatchlistItem.created_at)
    ).all()
    return [
        QuoteView(
            ts_code=quote.ts_code,
            name=name,
            price=quote.price,
            pct_chg=quote.pct_chg,
            volume=quote.volume,
            amount=quote.amount,
            quote_time=quote.quote_time,
            is_stale=quote.is_stale,
            source=quote.source,
        )
        for name, quote in rows
    ]


@router.post("/watchlist", response_model=ApiMessage)
def add_watchlist(payload: WatchlistCreate, db: Session = Depends(get_db)) -> ApiMessage:
    code = payload.ts_code.upper()
    if not db.get(Stock, code):
        raise HTTPException(404, "未找到该股票")
    if not db.get(WatchlistItem, code):
        db.add(WatchlistItem(ts_code=code, note=payload.note))
        db.commit()
    return ApiMessage(message="已加入自选")


@router.delete("/watchlist/{ts_code}", response_model=ApiMessage)
def remove_watchlist(ts_code: str, db: Session = Depends(get_db)) -> ApiMessage:
    db.execute(delete(WatchlistItem).where(WatchlistItem.ts_code == ts_code.upper()))
    db.commit()
    return ApiMessage(message="已移出自选")


@router.post("/sync/jobs")
def create_sync(payload: SyncRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    run = run_sync(db, payload.job_type)
    return {
        "id": run.id,
        "job_type": run.job_type,
        "status": run.status,
        "message": run.message,
        "items_updated": run.items_updated,
        "error_class": run.error_class,
        "finished_at": run.finished_at,
    }


@router.get("/sync/jobs/{job_id}")
def sync_status(job_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    run = db.get(SyncRun, job_id)
    if not run:
        raise HTTPException(404, "任务不存在")
    return {
        "id": run.id,
        "job_type": run.job_type,
        "status": run.status,
        "message": run.message,
        "items_updated": run.items_updated,
        "error_class": run.error_class,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }
