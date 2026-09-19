"""日线取数的三个入口，以及"哪些端点允许回补"这条纪律的唯一落点。

抽成独立模块的理由不是复用本身，而是**这条纪律只能存在一份**：

- `load_bars`（允许回补）只给**单标的**端点用——一次请求一次上游调用，可接受。
- `stored_bars`（绝不回补）给**列表**端点用。列表端点可能一次看到 N 个标的，
  逐个回补正是当初把 TuShare 配额打穿的 bug。

两边的差异写在这里、被 `test_quant_scan_never_triggers_backfill` 这类测试盯着，好过在
两个路由文件里各写一遍然后慢慢漂移。

`stored_bars` 与 ORM→dict 的映射现在**定义在 `services/history.py`**（它们只依赖
`db.models`，而「明日操作」的计算搬进了服务层、不能反向 import 这里），本模块照旧
re-export 它们，纪律的说明留在上头这段。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Stock
from app.services.history import bars_to_rows, ensure_bar_history, stored_bars
from app.services.quant import performance as performance_engine
from app.services.sync import get_provider

BENCHMARK_CODE = "000001.SH"
BENCHMARK_NAME = "上证指数"

__all__ = [
    "BENCHMARK_CODE",
    "BENCHMARK_NAME",
    "benchmark",
    "load_bars",
    "require_stock",
    "stored_bars",
]


def require_stock(session: Session, ts_code: str) -> Stock:
    """未知代码一律 404。

    `PRAGMA foreign_keys=ON` 生效中，所以这不是"友好一点"：漏掉这一步，后面任何写
    `position_trades` 的请求都会让 SQLite 抛 IntegrityError 变成 500。
    """
    stock = session.get(Stock, ts_code.upper())
    if stock is None:
        raise HTTPException(404, "未找到该股票")
    return stock


def load_bars(
    session: Session, code: str, days: int, *, backfill: bool = True
) -> list[dict[str, Any]]:
    """取最近 `days` 根日线，转成 quant 内核要的普通 dict（内核不认识 ORM 对象）。

    默认 `backfill=True`：首次访问按需回补两年日线。列表端点必须显式传
    `backfill=False`，见模块 docstring。
    """
    bars = (
        ensure_bar_history(session, code, min_bars=days)
        if backfill
        else stored_bars(session, code, days)
    )
    return bars_to_rows(bars)[-days:]


def benchmark(days: int) -> tuple[str, dict[Any, float]]:
    """上证指数收盘序列；provider 不可用时返回空字典，Beta/Alpha 自然降级为 None。"""
    try:
        points = get_provider().index_history(BENCHMARK_CODE, days)
    except Exception:
        points = []
    return BENCHMARK_NAME, performance_engine.benchmark_map(points)
