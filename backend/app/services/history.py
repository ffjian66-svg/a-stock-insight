"""按需回补个股日线。

每日收盘同步只增量累积，库里通常只有最近几个月；而夏普、最大回撤、策略回测都需要
两年左右的样本。这里在首次访问某只股票时比对库内区间，只向 provider 要缺口那一段，
幂等落库后复用。provider 失败一律降级为「返回已有数据」，绝不让页面 500。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyBar, Stock
from app.providers.base import BarData, MarketDataProvider, ProviderError
from app.services.sync import get_provider

logger = logging.getLogger(__name__)

TARGET_YEARS = 2
# 区间端点比对的容差：跨周末 + 春节长假，端点差十几天仍算「已覆盖」
_EDGE_SLACK = timedelta(days=12)

# 已经尝试过回补的代码。库内最早日期可能是上市日（新股永远补不到区间起点），
# 没有这个标记就会每次请求都白调一次 provider。重启后每股多一次调用，可接受。
_BACKFILLED: set[str] = set()
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _code_lock(code: str) -> threading.Lock:
    """按代码加锁：并发访问同一只股票时不重复拉取。"""
    with _LOCKS_GUARD:
        lock = _LOCKS.get(code)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[code] = lock
        return lock


def history_window(years: int = TARGET_YEARS, today: date | None = None) -> tuple[date, date]:
    end = today or date.today()
    return end - timedelta(days=int(years * 365.25)), end


def stored_bars(session: Session, code: str, days: int) -> list[DailyBar]:
    """只读库内最近 `days` 根日线，绝不触发回补。库内没有就返回空列表——
    调用方负责降级（`scores=None` + `data_warning`），**不是**去上游补。

    从 `app/api/bars.py` 搬到这里：「明日操作」的整套计算已搬进 `services/board.py`，
    而服务层不能反向 import API 层（同款禁令见 `services/picks.py` 的模块说明）。
    函数体逐字未改。
    """
    rows = session.scalars(
        select(DailyBar)
        .where(DailyBar.ts_code == code)
        .order_by(DailyBar.trade_date.desc())
        .limit(days)
    ).all()
    return list(reversed(rows))


def bars_to_rows(bars: Sequence[DailyBar]) -> list[dict[str, Any]]:
    """ORM 日线 → quant 内核要的普通 dict（内核不认识 ORM 对象）。

    **全仓唯一一处映射**：`api.bars.load_bars` 与 `stored_bar_rows` 都走它。手抄第二遍
    就会让某条路径悄悄少一个字段，而少了 `pre_close` 这类字段的错误是静默的。
    """
    return [
        {
            "trade_date": bar.trade_date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "amount": bar.amount,
        }
        for bar in bars
    ]


def stored_bar_rows(session: Session, code: str, days: int) -> list[dict[str, Any]]:
    """`stored_bars` + `bars_to_rows`：只读库内日线并转成内核 dict，**绝不回补**。

    「不许回补」这条纪律的落点：列表端点（含「明日操作」与持仓建议）一律走这里，
    让上游配额只有一个烧穿口——而那个口在 `api.bars.load_bars(backfill=True)`，只给
    单标的端点用。
    """
    return bars_to_rows(stored_bars(session, code, days))


def _load(session: Session, code: str, start: date, end: date) -> list[DailyBar]:
    rows = session.scalars(
        select(DailyBar)
        .where(DailyBar.ts_code == code, DailyBar.trade_date >= start, DailyBar.trade_date <= end)
        .order_by(DailyBar.trade_date)
    ).all()
    return list(rows)


def _covered(rows: list[DailyBar], start: date, end: date) -> bool:
    if not rows:
        return False
    return rows[0].trade_date <= start + _EDGE_SLACK and rows[-1].trade_date >= end - _EDGE_SLACK


def _store(session: Session, code: str, bars: list[BarData]) -> int:
    """只插入库内没有的交易日；返回新增条数。"""
    if not bars:
        return 0
    dates = [bar.trade_date for bar in bars]
    existing = set(
        session.scalars(
            select(DailyBar.trade_date).where(
                DailyBar.ts_code == code, DailyBar.trade_date.in_(dates)
            )
        ).all()
    )
    added = 0
    for bar in bars:
        if bar.trade_date in existing:
            continue
        session.add(
            DailyBar(
                ts_code=code,
                trade_date=bar.trade_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                pre_close=bar.pre_close,
                pct_chg=bar.pct_chg,
                volume=bar.volume,
                amount=bar.amount,
                source="history",
            )
        )
        added += 1
    if added:
        session.flush()
    return added


def ensure_bar_history(
    session: Session,
    code: str,
    *,
    years: int = TARGET_YEARS,
    min_bars: int = 0,
    provider: MarketDataProvider | None = None,
) -> list[DailyBar]:
    """保证库内至少有该股最近 `years` 年的日线，返回区间内的 bars（按日期升序）。

    `min_bars` 用于回测/绩效这类对样本量有硬要求的调用方：即便区间已覆盖，样本仍不足时
    也会再试一次回补（例如库刚建、或该股长期停牌）。库内没有这只股票时直接返回空列表，
    避免外键悬空。
    """
    start, end = history_window(years)
    rows = _load(session, code, start, end)
    if _covered(rows, start, end) and len(rows) >= min_bars:
        return rows
    if code in _BACKFILLED and len(rows) >= min_bars:
        return rows
    if session.get(Stock, code) is None:
        return rows

    with _code_lock(code):
        # 拿锁期间可能已被别的请求补完，重新读一次
        rows = _load(session, code, start, end)
        if _covered(rows, start, end) and len(rows) >= min_bars:
            return rows
        if code in _BACKFILLED and len(rows) >= min_bars:
            return rows
        try:
            fetched = (provider or get_provider()).stock_history(code, start, end)
        except ProviderError as exc:
            logger.warning("回补 %s 历史失败，降级使用库内数据：%s", code, exc)
            _BACKFILLED.add(code)
            return rows
        except Exception:  # provider 异常不应打断请求
            logger.exception("回补 %s 历史异常，降级使用库内数据", code)
            _BACKFILLED.add(code)
            return rows
        added = _store(session, code, fetched)
        _BACKFILLED.add(code)
        if added:
            session.commit()
        return _load(session, code, start, end)
