from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Stock, SyncRun, TradeCalendar
from app.db.session import SessionLocal
from app.services import board as board_service
from app.services import notify
from app.services import positions as position_service
from app.services.pool import POOL_JOB, run_pool_job
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
        _alert_disabled(job_type)
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
        _alert_sync_result(session, result)


def _execute_pool() -> None:
    """cron 入口。

    **刻意不走 `_execute`**：那条路径调 `run_sync`，而 `run_sync` 被排他的 `_SYNC_LOCK`
    包住，本任务要跑约 4 分钟——持锁期间 30 秒一次的行情刷新全部堵塞。
    也刻意不查 `job_disabled`：那套机制针对 provider 权限不足，而池化只读本地日线。
    """
    run_pool_job()
    with SessionLocal() as session:
        # `run_pool_job()` 回传的是行数、不是 run 行，所以读最新一条 `job_type="pool"`。
        # 它必然就是刚才那次（本任务 `max_instances=1` 且 `_POOL_LOCK` 串行），
        # 而"已有实例在跑"那一支**根本不写行**（`pool.py:193-197` 传的是 run_id=None，
        # `_finish_run` 遇到 None 直接返回），所以读不到伪失败。
        latest = session.scalar(
            select(SyncRun)
            .where(SyncRun.job_type == POOL_JOB)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
        if latest is not None:
            _alert_sync_result(session, latest)


def _previous_run(db: Session, job_type: str, *, exclude_id: int | None) -> SyncRun | None:
    """同 job 的上一次运行。

    **按 `id` 倒序，不按 `started_at`**：墙钟没有唯一性，而池化那类自己开事务的任务
    会与之交错，同一秒两行时"上一次"就成了掷骰子。
    """
    stmt = select(SyncRun).where(SyncRun.job_type == job_type)
    if exclude_id is not None:
        stmt = stmt.where(SyncRun.id != exclude_id)
    return db.scalar(stmt.order_by(SyncRun.id.desc()).limit(1))


def _alert_sync_result(db: Session, result: SyncRun) -> None:
    """白名单内的任务：失败告警 / 上次失败这次成功 → 恢复通知。

    状态**只读 `SyncRun` 自己的历史**，不新增状态列（`create_all` 不会 ALTER，
    给已存在的表加列在线上库会炸而测试全绿）。

    ## 已知且接受的污染

    `POST /api/v1/sync/jobs`（手工触发）往同一张表写行，所以手工失败会抑制当天的定时
    告警、手工成功会额外产生一条"已恢复"。这是可接受的：任何一次成功都确实回答了
    "这个 job 最近不健康吗"=否，语义站得住；为此加一个 `source` 列要付上面那个代价。

    ## 恢复通知可能"孤零零"来一条

    只看"上一次是失败"，所以如果那次失败的告警当时是 `skipped`（webhook 还没配），
    恢复通知会显得没有前因。代价是多一条无害的消息，好过为此把账本再 join 进来。
    """
    job_type = result.job_type
    if job_type not in notify.SYNC_ALERT_JOBS:
        return
    # 没有 `error_class` 的 "failed" 是**跳过**不是失败（`pool._finish_run` 在"已有一次
    # 在跑"那一支就是这种）。跳过不需要人处理，不告警。
    if result.status != "success" and not result.error_class:
        return
    today = date.today()
    if result.status != "success":
        notify.push(
            db,
            kind="sync",
            key=notify.dedup_key("syncfail", today, job_type),
            content=notify.render_sync_alert(
                job_type, result.error_class or "", result.message, as_of=today
            ),
        )
        return
    previous = _previous_run(db, job_type, exclude_id=result.id)
    if previous is not None and previous.status != "success":
        notify.push(
            db,
            kind="sync_ok",
            key=notify.dedup_key("syncok", today, job_type),
            content=notify.render_sync_recovered(job_type, as_of=today),
        )


def _alert_disabled(job_type: str) -> None:
    """任务被临时禁用（只在 `SYNC_DISABLED_JOBS` 里那两个高频任务上会真的发生）。

    原因从**那一次权限失败**的 `SyncRun` 里取：`disable_job` 只存一个到期时刻
    （`sync.py:50`），而禁用总是紧跟在一次 `error_class="permission"` 的失败之后
    （`sync.py:785-786`），那次失败的 `message` 就是上游的原话——不必给 `disable_job`
    加参数、不必动 `SyncRun` 的表结构。
    """
    if job_type not in notify.SYNC_DISABLED_JOBS:
        return
    with SessionLocal() as session:
        reason = session.scalar(
            select(SyncRun.message)
            .where(SyncRun.job_type == job_type, SyncRun.error_class == "permission")
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
        notify.push(
            session,
            kind="sync_off",
            key=notify.dedup_key("syncoff", date.today(), job_type),
            content=notify.render_sync_disabled(job_type, reason or ""),
        )


def _evening_notify() -> None:
    """傍晚的日报 + 止损告警。一天最多两条消息。

    **一个 job 里榜单只算一次**，持仓各算一次——这些都是十几秒级的活，绝不能分成两个
    job 各算一遍。整体包 try/except：推送是附加功能，绝不能让它影响调度器或别的任务。
    """
    settings = get_settings()
    if not settings.notify_configured:
        # 没配就**连榜单都不算**（`build_board` 要 10 秒级）：为一页不会有人收到的日报
        # 每晚烧一次 CPU 没有意义。所以这里也写不出一行 `skipped` 账本——"没配"这件事
        # 由 `/settings` 的卡片和 `/system/status` 的 `notify_configured` 明说。
        logger.info("微信推送未配置，傍晚任务跳过")
        return
    try:
        with SessionLocal() as session:
            board = board_service.build_board(session)
            advices = [
                position_service.advise_for(
                    session,
                    stock,
                    equity=settings.notify_equity,
                    days=board_service.PLAN_DAYS_DEFAULT,
                )
                for stock in position_service.traded_stocks(session)
            ]
            # 幂等键用**数据日期**而不是今天：同一天重跑（手工触发 + cron）算出来的是
            # 同一份东西，不该发第二遍；而数据没更新时（收盘同步失败）键也不变。
            notify.push(
                session,
                kind="daily",
                key=notify.dedup_key("daily", board.basis_date),
                content=notify.render_daily(
                    board,
                    advices,
                    max_bytes=settings.notify_max_bytes,
                    equity=settings.notify_equity,
                ),
            )
            triggered = [item for item in advices if notify.is_triggered(item)]
            if triggered:
                # 先日报后告警：急的在**后**发，也就是最新的那条、最靠近输入框的那条。
                notify.push(
                    session,
                    kind="stop",
                    key=notify.dedup_key("stop", board.basis_date),
                    content=notify.render_stop_alert(
                        triggered,
                        as_of=board.basis_date,
                        max_bytes=settings.notify_max_bytes,
                    ),
                )
    except Exception:  # 绝不能让推送影响调度器
        logger.exception("傍晚推送任务异常")


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
    # 收盘同步定在 17:30：当日 K 线 TuShare 已定型，避免 15:20 过早拉到 0 行、
    # 数据延后一天才入库；即便偶发未就绪，sync_market 次日会自动补拉不丢失。
    scheduler.add_job(
        _execute,
        "cron",
        day_of_week="mon-fri",
        hour=17,
        minute=30,
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
    # 18:45：排在 17:30 收盘同步与 18:30 评分之后，用的是当天已定型的 K 线。
    # 只跑工作日——池化读的是日线，周末跑一遍结果与周五晚间完全相同。
    scheduler.add_job(
        _execute_pool,
        "cron",
        day_of_week="mon-fri",
        hour=18,
        minute=45,
        id="pool-stats-refresh",
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
    # 傍晚推送：排在 18:45 池化之后，是全交易日的收尾（一天最多两条消息）。
    # 只在工作日跑——周末没有新的收盘数据，日报与周五那份逐字相同。
    scheduler.add_job(
        _evening_notify,
        "cron",
        day_of_week="mon-fri",
        hour=settings.notify_daily_hour,
        minute=0,
        id="evening-notify",
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
