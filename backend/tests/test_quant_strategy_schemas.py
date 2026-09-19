"""内核 dataclass ↔ pydantic 响应 / 数据库表的边界契约。

这个文件防的是一类**静默**故障：内核给 `Plan` 加了一个字段、pydantic 镜像忘了跟，于是
API 少返回一个键，而前端拿到 `undefined` 渲染成空白——不报错、不进日志，只有人肉盯着
页面才能发现。上一轮已经吃过「前后端契约靠 fixture 复制导致漂移」的亏，所以这里的钉子是
**字段集合双向相等**，不是"能 validate 通过"（后者对多出来的字段是瞎的）。

`BuySellPlan`/`PositionAdvice` 另外开了 `extra="forbid"`：字段集合漂移会在 API 边界直接
抛 ValidationError，不必等到测试。
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from typing import Any

import pytest
from app.api import schemas
from app.db import models
from app.services.quant import backtest as bt
from app.services.quant import decision as decision_mod
from pydantic import ValidationError

START = date(2025, 1, 1)

# 内核 dataclass → 它的 pydantic 镜像。加了一个而没有对应关系，本身就该在这里显形。
MIRRORS: list[tuple[type, type]] = [
    (decision_mod.Plan, schemas.BuySellPlan),
    (decision_mod.PlanCondition, schemas.PlanCondition),
    (decision_mod.PriceZone, schemas.PriceZone),
    (decision_mod.StopPlan, schemas.StopPlan),
    (decision_mod.TrailPlan, schemas.TrailPlan),
    (decision_mod.PositionSizing, schemas.PositionSizing),
    (decision_mod.HoldEstimate, schemas.HoldEstimate),
    (decision_mod.ExitRule, schemas.ExitRule),
    (decision_mod.ScorePoint, schemas.ScorePoint),
    (decision_mod.FamilyScoreView, schemas.FamilyScoreView),
    (decision_mod.FamilyScores, schemas.FamilyScores),
    (decision_mod.PositionAdvice, schemas.PositionAdvice),
    (bt.Expectancy, schemas.ExpectancyBlock),
]


def _bars(closes: list[float], spread: float = 1.0) -> list[dict[str, object]]:
    return [
        {
            "trade_date": START + timedelta(days=index),
            "open": close,
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": 1_000_000.0,
        }
        for index, close in enumerate(closes)
    ]


def _oversold_decline(count: int = 120) -> list[dict[str, object]]:
    """信号侧 4 条入场条件全满足的形状（见 test_quant_decision 的说明）。"""
    return _bars([200.0 - index * 0.55 for index in range(count)])


def _expectancy(**overrides: Any) -> bt.Expectancy:
    base: dict[str, Any] = {
        "strategy": "fusion",
        "strategy_label": "多策略融合",
        "window_days": 500,
        "bars": 500,
        "trade_count": 24,
        "win_rate": 0.4,
        "avg_win_pct": 4.0,
        "avg_loss_pct": -4.2,
        "profit_factor": 0.63,
        "expectancy_pct": -2.31,
        "expectancy_per_bar_pct": -0.1925,
        "avg_hold_bars": 12.0,
        "forced_end_trades": 1,
        "cumulative_return": -0.22,
        "max_drawdown": -0.31,
        "benchmark_return": -0.05,
        "excess_return": -0.17,
        "verdict": "negative",
        "verdict_text": "实测 24 笔，每笔期望 -2.31%。",
        "caveat": bt.EXPECTANCY_CAVEAT,
    }
    base.update(overrides)
    return bt.Expectancy(**base)


def _plan(**kwargs: Any) -> decision_mod.Plan:
    return decision_mod.build_plan(
        _oversold_decline(),
        ts_code="600519.SH",
        name="贵州茅台",
        industry="白酒",
        expectancy=kwargs.pop("expectancy", None) or _expectancy(),
        **kwargs,
    )


# ======================================================================================
# 字段集合双向相等——本文件的主要钉子
# ======================================================================================
@pytest.mark.parametrize(("kernel", "mirror"), MIRRORS, ids=[m[1].__name__ for m in MIRRORS])
def test_mirror_field_sets_match_exactly(kernel: type, mirror: type) -> None:
    kernel_fields = {field.name for field in dataclasses.fields(kernel)}
    mirror_fields = set(mirror.model_fields)  # type: ignore[attr-defined]
    assert kernel_fields == mirror_fields, (
        f"内核有而响应缺：{sorted(kernel_fields - mirror_fields)}；"
        f"响应有而内核无：{sorted(mirror_fields - kernel_fields)}"
    )


def test_every_kernel_result_type_has_a_mirror() -> None:
    """新增一个要上 API 的 dataclass 却忘了加镜像，会在这里失败。"""
    mirrored = {kernel for kernel, _ in MIRRORS}
    exported = {
        obj
        for name in decision_mod.__all__
        if dataclasses.is_dataclass(obj := getattr(decision_mod, name))
    }
    # 三个例外都不是响应体：LedgerEntry 是纯输入（POST body 由 PositionTradeCreate
    # 承担），Holding 是 summarize_ledger 的中间结果，其字段已被 PositionAdvice 摊平，
    # MarketEdge 也是纯输入（全市场汇总由 strategy_routes 从物化表读出来后注入
    # build_plan，对外走的是 PoolStatsRow，字段集刻意不同，见其 docstring）。
    assert exported - mirrored == {
        decision_mod.LedgerEntry,
        decision_mod.Holding,
        decision_mod.MarketEdge,
    }


def test_position_trade_row_covers_every_stored_column() -> None:
    """流水响应必须能装下库里所有会被展示的列，否则录入后立刻"丢"了几个字段。"""
    columns = {column.name for column in models.PositionTrade.__table__.columns}
    assert columns - {"id"} <= set(schemas.PositionTradeRow.model_fields)


# ======================================================================================
# 往返：asdict → model_validate
# ======================================================================================
def test_plan_round_trips_through_asdict() -> None:
    plan = _plan(equity=1_000_000.0)
    payload = schemas.BuySellPlan.model_validate(dataclasses.asdict(plan))

    assert payload.ts_code == plan.ts_code
    assert payload.action == plan.action
    assert payload.strategy_label == plan.strategy_label
    # 数字逐位相等：往返不许引入任何舍入或丢精度
    assert payload.price == plan.price
    assert payload.entry_zone.low == plan.entry_zone.low
    assert payload.stop_loss.recommended == plan.stop_loss.recommended
    assert payload.sizing.shares == plan.sizing.shares
    assert payload.sizing.formula == plan.sizing.formula
    assert payload.expected_hold.basis == plan.expected_hold.basis
    # 诚实层必须原样穿过——这是"每个结论都挂着实测记录"的机械保证
    assert payload.honesty.expectancy_pct == plan.honesty.expectancy_pct
    assert payload.honesty.trade_count == plan.honesty.trade_count
    assert payload.honesty.verdict == "negative"
    assert payload.honesty.caveat == bt.EXPECTANCY_CAVEAT
    assert "未复权" in payload.honesty.caveat
    assert "不可比" in payload.honesty.caveat  # 满仓口径的那句必须在
    # 列表型字段：内核是 tuple，响应是 list，内容与顺序不变
    assert payload.entry_conditions[0].key == plan.entry_conditions[0].key
    assert [item.key for item in payload.exit_rules] == [r.key for r in plan.exit_rules]
    assert len(payload.entry_conditions) == 5
    assert len(payload.exit_rules) == 5
    assert payload.score_history[0].trade_date == plan.score_history[0].trade_date


def test_plan_round_trip_preserves_none_as_null_not_zero() -> None:
    """零成交时 `expectancy_pct` 是 None。往返不能把它变成 0.0——
    "没成交"和"成交了但打平"是两回事，后者会读成"这策略还行"。"""
    plan = _plan(
        expectancy=_expectancy(
            trade_count=0,
            win_rate=None,
            avg_win_pct=None,
            avg_loss_pct=None,
            profit_factor=None,
            expectancy_pct=None,
            expectancy_per_bar_pct=None,
            avg_hold_bars=None,
            forced_end_trades=0,
            verdict="insufficient",
        )
    )
    payload = schemas.BuySellPlan.model_validate(dataclasses.asdict(plan))
    assert payload.honesty.expectancy_pct is None
    assert payload.honesty.win_rate is None
    assert payload.honesty.avg_hold_bars is None
    assert payload.expected_hold.basis == "heuristic"
    assert payload.expected_hold.bars is None


def test_position_advice_round_trips_with_and_without_scores() -> None:
    trades = [
        decision_mod.LedgerEntry("buy", START, 1500.0, 200.0, 9.0),
        decision_mod.LedgerEntry("buy", START + timedelta(days=10), 1600.0, 100.0, 7.2),
        decision_mod.LedgerEntry("sell", START + timedelta(days=20), 1700.0, 100.0, 8.5),
    ]
    advice = decision_mod.advise_position(
        _oversold_decline(),
        trades,
        ts_code="600519.SH",
        name="贵州茅台",
        expectancy=_expectancy(),
    )
    payload = schemas.PositionAdvice.model_validate(dataclasses.asdict(advice))
    assert payload.status == "holding"
    assert payload.shares == advice.shares == 200.0
    assert payload.avg_cost == advice.avg_cost
    assert payload.realized_pnl == advice.realized_pnl
    assert payload.scores is not None
    assert payload.scores.fusion.score == advice.scores.fusion.score  # type: ignore[union-attr]

    # 无日线时内核给 scores=None（"绝不编造分数"），响应必须是 null 而不是空对象
    degraded = decision_mod.advise_position(_bars([100.0] * 5), trades, ts_code="600519.SH")
    degraded_payload = schemas.PositionAdvice.model_validate(dataclasses.asdict(degraded))
    assert degraded_payload.scores is None
    assert degraded_payload.data_warning
    assert degraded_payload.action == "watch"


# ======================================================================================
# 边界：枚举值把关
# ======================================================================================
def test_unknown_action_is_rejected_rather_than_passed_through() -> None:
    """动作是决策表的输出，枚举外的值意味着内核加了新动作而响应没跟上——必须响。"""
    payload = dataclasses.asdict(_plan())
    payload["action"] = "strong_buy"
    with pytest.raises(ValidationError):
        schemas.BuySellPlan.model_validate(payload)


def test_a_field_the_kernel_grew_fails_loudly() -> None:
    """extra="forbid" 的意义：内核加字段而镜像没跟 → API 边界报错，不是静默丢字段。"""
    payload = dataclasses.asdict(_plan())
    payload["brand_new_field"] = 1
    with pytest.raises(ValidationError):
        schemas.BuySellPlan.model_validate(payload)


def test_expectancy_row_marks_the_baseline_and_defaults_to_not_baseline() -> None:
    row = schemas.ExpectancyRow.model_validate(dataclasses.asdict(_expectancy()))
    assert row.is_baseline is False
    assert row.verdict == "negative"
    baseline = schemas.ExpectancyRow.model_validate(
        {**dataclasses.asdict(_expectancy(strategy="buy_hold")), "is_baseline": True}
    )
    assert baseline.is_baseline is True


def test_strategy_backtest_extends_the_pinned_quant_backtest() -> None:
    """`/strategy/backtest` 是 `/quant/backtest` 的加法扩展，原字段一个都不能少。"""
    assert set(schemas.QuantBacktest.model_fields) <= set(schemas.StrategyBacktest.model_fields)
    assert set(schemas.StrategyBacktest.model_fields) - set(schemas.QuantBacktest.model_fields) == {
        "expectancy",
        "fused",
    }


# ======================================================================================
# 表：新表靠 create_all 自动出现（无 Alembic），但外键是开着的
# ======================================================================================
def test_position_trades_table_is_registered_for_create_all(engine) -> None:
    """无 Alembic：新表必须在 `Base.metadata` 里才会被 create_all 建出来。

    这里同时钉住「给已有表加列不会生效」那条陷阱的另一面——`strategy`/`stop_price`
    只存在于新表上，所以它们必须跟着表一起被建出来。
    """
    from sqlalchemy import inspect

    assert "position_trades" in models.Base.metadata.tables
    columns = {column["name"] for column in inspect(engine).get_columns("position_trades")}
    assert {"ts_code", "side", "trade_date", "price", "shares", "fee"} <= columns
    assert {"stop_price", "strategy", "note", "created_at"} <= columns


def test_a_trade_for_an_unknown_stock_is_rejected_by_the_database(db_session) -> None:
    """`PRAGMA foreign_keys=ON` 生效中：未知代码必须由路由先 404，
    否则 SQLite 抛 IntegrityError 变成 500。这条测的是「数据库确实会拦」。"""
    from sqlalchemy.exc import IntegrityError

    db_session.add(
        models.PositionTrade(
            ts_code="999999.SH", side="buy", trade_date=START, price=10.0, shares=100.0
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_trade_for_a_known_stock_round_trips(db_session) -> None:
    db_session.add(models.Stock(ts_code="600519.SH", symbol="600519", name="贵州茅台"))
    db_session.add(
        models.PositionTrade(
            ts_code="600519.SH",
            side="buy",
            trade_date=START,
            price=1500.0,
            shares=100.0,
            stop_price=1425.0,
            strategy="fusion",
            note="试探仓",
        )
    )
    db_session.commit()
    stored = db_session.query(models.PositionTrade).one()
    assert stored.id is not None
    assert stored.stop_price == 1425.0
    assert stored.strategy == "fusion"
    assert stored.shares == 100.0
    # ORM 实例不能直接喂给内核：内核吃的是 frozen dataclass（见 LedgerEntry）
    entry = decision_mod.LedgerEntry(
        "buy", stored.trade_date, stored.price, stored.shares, stored.fee
    )
    assert decision_mod.summarize_ledger([entry]).shares == 100.0
