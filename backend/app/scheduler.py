from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import Stock, TradeCalendar
from app.db.session import SessionLocal
from app.services.sync import job_disabled, run_sync

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def is_trading_session(now: datetime | None = None) -> bool:
    """纯时段判断：交易日 + 开盘时段（含上午/下午，剔除午休）。"""
    current = now or datetime.now(SHANGHAI)
    if current.weekday() >= 5:
        return False
    minute = current.hour * 60 + current.minute
    return 9 * 60 + 30 <= minute <= 11 * 60 + 30 or 13 * 60 <= minute <= 15 * 60


def _calendar_status(day: date) -> bool | None:
    """交易日历已知则返回当日是否开市，未知返回 None。"""
    with SessionLocal() as session:
        row = session.get(TradeCalendar, day)
        return row.is_open if row else None


def _execute(job_type: str) -> None:
    if job_disabled(job_type):
        logger.info("同步任务[%s] 因账号权限不足被临时禁用，跳过", job_type)
        return
    if job_type == "quotes":
        calendar_open = _calendar_status(date.today())
        if calendar_open is False:
            return
        if calendar_open is None and not is_trading_session():
            return
    with SessionLocal() as session:
        result = run_sync(session, job_type)
        if result.status != "success":
            logger.warning("同步任务失败[%s]: %s", result.error_class, result.message)


def create_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=SHANGHAI)
    scheduler.add_job(
        _execute,
        "interval",
        seconds=max(15, settings.quote_refresh_seconds),
        args=["quotes"],
        id="watchlist-quotes",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _execute,
        "interval",
        minutes=max(5, settings.news_refresh_minutes),
        args=["news"],
        id="watchlist-news",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _execute,
        "cron",
        day_of_week="mon-fri",
        hour=15,
        minute=20,
        args=["market"],
        id="market-close-sync",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _execute,
        "cron",
        day_of_week="mon-fri",
        hour=18,
        minute=0,
        args=["fundamentals"],
        id="evening-fundamentals",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _execute,
        "cron",
        day_of_week="mon-fri",
        hour=18,
        minute=30,
        args=["scores"],
        id="score-refresh",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _execute,
        "cron",
        day_of_week="sat",
        hour=9,
        minute=5,
        args=["calendar"],
        id="weekly-calendar",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _execute,
        "cron",
        day_of_week="sat",
        hour=9,
        minute=10,
        args=["stocks"],
        id="weekly-stock-list",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def run_startup_compensation() -> None:
    """启动时补偿遗漏任务：真实模式下仅当库为空时补一次 bootstrap，不做无条件全量拉取。"""
    settings = get_settings()
    if settings.use_mock_data or not settings.enable_scheduler:
        return
    try:
        with SessionLocal() as session:
            if session.scalar(select(func.count()).select_from(Stock)):
                return
        result = run_sync_with_session("bootstrap")
        if result:
            logger.info("启动补偿 bootstrap 完成: %s", result)
    except Exception as exc:  # Token 缺失等场景不阻断服务启动
        logger.warning("启动补偿跳过: %s", exc)


def run_sync_with_session(job_type: str) -> str:
    with SessionLocal() as session:
        result = run_sync(session, job_type)
        return result.message


def start_startup_compensation() -> None:
    threading.Thread(target=run_startup_compensation, daemon=True).start()
