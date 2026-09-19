"""全市场池化汇总的取数与落库（纯聚合数学在 `quant.pool`）。

## 数据源纪律

**绝不调用 provider**：本模块只读 `daily_bars`，一次运行零 TuShare 配额消耗。它要遍历
5,564 只股票，一旦允许按需回补就是 5,564 次上游调用——正是 `app/api/bars.py` 当初要堵的
配额烧穿模式。深历史股票不足时结果会偏，但不会烧配额。

## 时间与事务

全市场跑一遍实测约 4 分钟。三条硬约束：

1. **不进 `run_sync` / `_SYNC_LOCK`**：那把锁是排他的，持锁 4 分钟会把 30 秒一次的行情
   刷新全堵死。调度器用专用的 `_execute_pool`（见 `app/scheduler.py`）。
2. **计算全程不持有写事务**：只 `select`，写库集中在最后一步。WAL 下读不阻塞写，
   但一个开了 4 分钟的读快照会让 WAL 无法 checkpoint，所以每 500 只主动放掉快照。
3. **中途崩溃时旧快照原样留存**：全部算完才 upsert + 一次 commit，绝不半写。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DailyBar, Stock, StrategyPoolStats, SyncRun
from app.db.session import SessionLocal
from app.services.quant import backtest as backtest_engine
from app.services.quant.pool import POOL_MIN_BARS, PoolAccumulator
from app.services.scoring import RULE_VERSION
from app.services.sync import classify_error

logger = logging.getLogger(__name__)

POOL_JOB = "pool"

# 每处理这么多只股票就放掉当前的读快照。不是为了不阻塞写（WAL 下读本来就不阻塞写），
# 而是不让一个 4 分钟的读事务把 WAL 的 checkpoint 一直挡在后面。
_SNAPSHOT_RELEASE_EVERY = 500

# 池化**不能**用 `sync._SYNC_LOCK`（见模块 docstring），但没有锁就可能同时跑两份，
# 两次 4 分钟的全市场回测叠在 2 vCPU 上会把机器打满。故单独一把**非阻塞**锁：
# 抢不到就直接放弃——排队毫无意义，下一轮 cron 会重来。
_POOL_LOCK = threading.Lock()


def recompute_market_stats(session: Session) -> int:
    """重算全部 11 条规则的全市场汇总，返回写入行数。

    逐股取数、逐股回测，绝不整表载入：57 万条 bar dict 约 300 MB，而线上可用内存只有
    519 MB（`recalculate_scores` 出于同样的理由只留 61 根收盘价）。

    **本函数自己 commit**（与 `recalculate_scores` 的 caller-commits 不同）：它没有调用方
    可以代 commit——调度器的 `_execute` 走 `run_sync`，而这里刻意不走那条路径。
    """
    universe_total = session.scalar(select(func.count()).select_from(Stock)) or 0
    accumulator = PoolAccumulator()

    # 按 code 迭代 `Stock` 而不是 `SELECT DISTINCT ts_code FROM daily_bars`：FK 保证
    # daily_bars ⊆ stocks，同一个集合，少一次 57 万行扫描，且 universe_total 直接得出。
    codes = list(session.scalars(select(Stock.ts_code).order_by(Stock.ts_code)).all())
    for index, code in enumerate(codes, start=1):
        bars = session.scalars(
            select(DailyBar)
            .where(DailyBar.ts_code == code)
            .order_by(DailyBar.trade_date)
        ).all()
        accumulator.add_stock([_bar_row(bar) for bar in bars], backtest_engine.LIVE_PARAMS)
        if index % _SNAPSHOT_RELEASE_EVERY == 0:
            session.rollback()  # 纯读事务，回滚即放掉快照；下一轮 select 开新快照

    window_from, window_to = accumulator.window()
    rows = accumulator.finalize(universe_total=universe_total, rule_version=RULE_VERSION)
    now = datetime.now()
    known = set(backtest_engine.all_strategy_names())
    written = 0
    for row in rows:
        record = session.get(StrategyPoolStats, row.strategy)
        if record is None:
            record = StrategyPoolStats(strategy=row.strategy)
        record.rule_version = RULE_VERSION
        record.computed_at = now
        record.stop_loss_atr = backtest_engine.LIVE_PARAMS.stop_loss_atr
        record.trail_atr = backtest_engine.LIVE_PARAMS.trail_atr
        record.min_bars = POOL_MIN_BARS
        record.universe_total = universe_total
        record.universe_used = accumulator.stocks_used
        record.bars_total = accumulator.bars_total
        record.bars_median = accumulator.bars_median()
        record.window_from = window_from
        record.window_to = window_to
        for name in _AGGREGATE_FIELDS:
            setattr(record, name, getattr(row, name))
        session.add(record)
        written += 1

    # 陈旧行要删：`all_strategy_names()` 收缩之后，留在表里的行会以一个崭新的时间戳
    # 继续参与否决，而它对应的规则早就不存在了。
    for stale in session.scalars(select(StrategyPoolStats)).all():
        if stale.strategy not in known:
            session.delete(stale)

    session.commit()
    logger.info(
        "全市场池化汇总完成：%s 只 / %s 根 / 写入 %s 行",
        accumulator.stocks_used,
        accumulator.bars_total,
        written,
    )
    return written


_AGGREGATE_FIELDS = (
    "trade_count",
    "stocks_with_trades",
    "win_rate",
    "avg_win_pct",
    "avg_loss_pct",
    "profit_factor",
    "expectancy_pct",
    "expectancy_per_bar_pct",
    "avg_hold_bars",
    "forced_end_trades",
    "forced_end_share",
    "benchmark_expectancy_pct",
    "benchmark_expectancy_per_bar_pct",
    "excess_per_bar_pct",
    "median_stock_expectancy_pct",
    "positive_stock_share",
    "positive_stock_min_trades",
    "stock_denominator",
    "verdict",
)


def _bar_row(bar: DailyBar) -> dict[str, Any]:
    return {
        "trade_date": bar.trade_date,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "amount": bar.amount,
    }


# ======================================================================================
# 任务编排（调度器与手动触发共用）
# ======================================================================================
def start_run() -> int:
    """建一条 `SyncRun` 行并立刻返回 id。手动触发先调它，才能马上把 id 给前端轮询。"""
    with SessionLocal() as session:
        run = SyncRun(job_type=POOL_JOB, status="running")
        session.add(run)
        session.commit()
        return run.id


def _finish_run(
    run_id: int | None,
    status: str,
    message: str,
    count: int = 0,
    error_class: str = "",
) -> None:
    """结掉 `SyncRun` 行。**每次开新会话**：计算用的是另一个会话，且它中途会 rollback
    放读快照（见 `recompute_market_stats`），把这一行的 insert 混在同一个会话里会被
    那次 rollback 一起丢掉，任务就永远停在 running。"""
    if run_id is None:
        return
    with SessionLocal() as session:
        run = session.get(SyncRun, run_id)
        if run is None:
            return
        run.status = status
        run.message = message
        run.items_updated = count
        run.error_class = error_class
        run.finished_at = datetime.now()
        session.commit()


def run_pool_job(run_id: int | None = None) -> int | None:
    """跑一次全市场池化汇总。返回写入行数；已有实例在跑或失败时返回 None。

    调用方（路由 / 调度器）负责在后台线程里调它。
    """
    if not _POOL_LOCK.acquire(blocking=False):
        # 绝不能把 run_id 留在 running：前端会一直转圈。明确结掉并说明原因。
        _finish_run(run_id, "failed", "已有一次全市场汇总在执行，本次未运行")
        logger.info("池化汇总已有实例在跑，跳过")
        return None
    own_run = run_id if run_id is not None else start_run()
    try:
        # 计算用独立会话；`finally` 一定结掉 SyncRun，否则崩溃后 status="running"
        # 会永久残留。写库集中在 `recompute_market_stats` 的最后一步，中途崩溃时
        # 上一份快照原样留存。
        with SessionLocal() as session:
            count = recompute_market_stats(session)
    except Exception as exc:
        _finish_run(own_run, "failed", str(exc)[:280], error_class=classify_error(exc))
        logger.warning("池化汇总失败: %s", exc)
        return None
    else:
        _finish_run(own_run, "success", f"已更新 {count} 项数据", count)
        return count
    finally:
        _POOL_LOCK.release()
