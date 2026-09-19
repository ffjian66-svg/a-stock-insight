"""持仓建议的计算。从 `api/strategy_routes.py` 搬下来，理由与 `services/board.py` 逐字相同：
傍晚的**止损告警**与 `/strategy/positions` 必须是同一次计算。

## 为什么必须共用

`PositionAdvice.stop_triggered` / `trail_triggered` 是"要不要叫醒你"的唯一判据。若推送自己
再按收盘价比一遍止损位，就会长出**第二个"触发"定义**：页面与手机迟早一个说破了、一个说没破，
而两者看上去同样确定。所以两条路径共用 `advise_for`。

## 为什么返回内核 dataclass

`services/picks.py` 有一条成文禁令：**服务层不 import `app.api.schemas`**。所以这里交
`decision.PositionAdvice`（内核 dataclass，见 `app/services/quant/decision.py`），
路由层再用 `model_validate(asdict(...))` 转成响应模型。那一步仍然留在路由层，
`extra="forbid"` 因此照旧能抓住"内核字段 ↔ 响应字段"的漂移。

## 只用库内日线

`stored_bar_rows`，**绝不回补**：本模块会被列表端点与傍晚任务对每个持仓各调一次，
允许回补就是"持仓越多、上游调用越多"，正是当初把 TuShare 配额打穿的模式
（见 `services/history.py` 的 "不许回补" 落点）。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PositionTrade, Stock
from app.services.history import stored_bar_rows
from app.services.quant import backtest as backtest_engine
from app.services.quant import decision
from app.services.quant.indicators import MIN_BARS

# 与 `/strategy/plan` 同一个口径。留一个模块级名字是延续 `strategy_routes` 的既有写法
# （那边是 `LIVE_PARAMS = backtest_engine.LIVE_PARAMS`），也让本模块的调用点读起来短。
LIVE_PARAMS = backtest_engine.LIVE_PARAMS


def ledger_rows(db: Session, ts_code: str) -> list[PositionTrade]:
    """该标的的全部流水，按**日期 + 主键**排序——这个顺序是唯一确定的身份来源。"""
    return list(
        db.scalars(
            select(PositionTrade)
            .where(PositionTrade.ts_code == ts_code)
            .order_by(PositionTrade.trade_date, PositionTrade.id)
        ).all()
    )


def ledger_entries(db: Session, ts_code: str) -> list[decision.LedgerEntry]:
    """流水行 → 内核要的 `LedgerEntry`。**全仓唯一一处映射**（与 `bars_to_rows` 同理：
    手抄第二遍就会让某条路径悄悄少一个字段，而少了 `fee` 的错误是静默的）。"""
    return [
        decision.LedgerEntry(
            side=row.side,
            trade_date=row.trade_date,
            price=row.price,
            shares=row.shares,
            fee=row.fee,
        )
        for row in ledger_rows(db, ts_code)
    ]


def traded_codes(db: Session) -> list[str]:
    """录入过流水的代码，去重升序。

    **刻意不在 SQL 里筛"还持有着"**：一笔流水是不是构成持仓要由 `summarize_ledger` 按日期
    回放才知道（可能已清仓），SQL 层只有"有没有流水"这一个事实。筛错了的后果是悄悄漏掉一只
    持仓——而漏掉的持仓正是"该报警却没报"。
    """
    return list(
        db.scalars(select(PositionTrade.ts_code).distinct().order_by(PositionTrade.ts_code)).all()
    )


def traded_stocks(db: Session) -> list[Stock]:
    """`traded_codes` 对应的 `Stock` 行，顺序一致。

    库里查不到的代码直接跳过而不是抛：`require_stock` 那句 404 是给**用户请求**用的，
    傍晚任务的正确反应是"少看一只"而不是整条消息失败。
    """
    codes = traded_codes(db)
    if not codes:
        return []
    found = {
        stock.ts_code: stock
        for stock in db.scalars(select(Stock).where(Stock.ts_code.in_(codes)))
    }
    return [found[code] for code in codes if code in found]


def advise_for(
    db: Session, stock: Stock, *, equity: float, days: int
) -> decision.PositionAdvice:
    """按标的聚合流水并给出建议（内核 dataclass，未转响应模型）。"""
    stored = ledger_rows(db, stock.ts_code)
    trades = ledger_entries(db, stock.ts_code)
    rows = stored_bar_rows(db, stock.ts_code, days)

    expectancy: Optional[backtest_engine.Expectancy] = None
    if len(rows) >= MIN_BARS:
        # 用流水里最近一次买入时记的策略作为这只持仓的规则；缺省 fusion
        strategy = next(
            (row.strategy for row in reversed(stored) if row.side == "buy" and row.strategy),
            "fusion",
        )
        if backtest_engine.is_known_strategy(strategy):
            try:
                result = backtest_engine.backtest(rows, strategy, LIVE_PARAMS)
                expectancy = backtest_engine.trade_expectancy(
                    result, strategy=strategy, window_days=days
                )
            except ValueError:
                expectancy = None

    # 用户最近一次录入的止损位优先。它存在且偏离规则，本身就是诚实特性：「你的止损
    # 已经不等于产生那组实测期望的止损了」，见 stop_source。
    user_stop = next((row.stop_price for row in reversed(stored) if row.stop_price), None)
    return decision.advise_position(
        rows,
        trades,
        ts_code=stock.ts_code,
        name=stock.name,
        industry=stock.industry,
        equity=equity,
        expectancy=expectancy,
        user_stop=user_stop,
    )
