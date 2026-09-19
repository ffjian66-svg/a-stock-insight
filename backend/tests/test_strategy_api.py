"""买卖策略 API 测试：计划形状、诚实层的真实性、11 策略对比、持仓流水。

这个文件里最重要的一条是 `test_plan_expectancy_is_recomputed_from_real_trades`：
计划里展示的统计必须能由 `/backtest` 的成交明细**独立重算**出来。只断言"字段存在"是
不够的——那对一个写死的常量同样成立，而写死的常量正是本功能最不能有的东西。
"""

from __future__ import annotations

import pytest


def _session():
    """client 用的那个库的会话工厂。

    必须在调用时取属性：conftest 把 `SessionLocal` 换成了临时库，模块级 import 会
    绑定到旧的（生产）工厂上。
    """
    import app.db.session as db_session_mod

    return db_session_mod.SessionLocal()


def _seed_stock_without_bars(ts_code: str = "000001.SZ", name: str = "无日线股") -> None:
    """造一只**没有任何库内日线**的股票，用来验证降级路径（不 500、不编分数）。"""
    from app.db.models import Stock

    with _session() as session:
        if session.get(Stock, ts_code) is None:
            session.add(
                Stock(
                    ts_code=ts_code,
                    symbol=ts_code[:6],
                    name=name,
                    industry="测试",
                    market="SZ",
                )
            )
            session.commit()


def _post_trade(client, **overrides):
    payload = {
        "ts_code": "600519.SH",
        "side": "buy",
        "trade_date": "2026-01-05",
        "price": 1500.0,
        "shares": 200.0,
    }
    payload.update(overrides)
    return client.post("/api/v1/strategy/positions/trades", json=payload)


# ======================================================================================
# 1：计划
# ======================================================================================
def test_plan_returns_every_block_the_page_renders(client) -> None:
    body = client.get("/api/v1/strategy/plan/600519.SH?strategy=fusion&days=500").json()

    assert body["ts_code"] == "600519.SH"
    assert body["strategy"] == "fusion"
    assert body["strategy_label"]
    assert body["action"] in {"buy", "watch", "avoid"}
    assert body["action_label"]

    # 固定 5 条入场条件 + 5 条失效条件，永远全给（页面靠 satisfied 渲染 ✓/✗）
    assert [c["key"] for c in body["entry_conditions"]] == [
        "score_gate",
        "family_gate",
        "not_extended",
        "above_stop",
        "expectancy_gate",
    ]
    assert [r["key"] for r in body["exit_rules"]] == [
        "fusion_exit",
        "stop_hit",
        "trail_hit",
        "trend_break",
        "time_stop",
    ]
    for condition in body["entry_conditions"]:
        assert condition["label"] and condition["detail"]

    # 价格几何：入场区不许落在止损之下，否则一买就触发止损
    zone = body["entry_zone"]
    assert zone["low"] <= zone["high"]
    assert zone["low"] >= body["stop_loss"]["recommended"]
    stop = body["stop_loss"]
    assert stop["recommended"] < body["price"]
    assert stop["distance_pct"] < 0
    assert stop["atr_level"] is None or stop["atr_level"] > 0
    # 止盈只有移动止盈这一种机制
    assert body["take_profit"]["level"] < body["take_profit"]["high_water"]

    sizing = body["sizing"]
    assert sizing["shares"] % 100 == 0
    assert "①" in sizing["formula"] and "③" in sizing["formula"]
    assert body["expected_hold"]["basis"] in {"measured", "heuristic"}
    assert len(body["score_history"]) > 0
    assert body["scores"]["trend"]["buy_threshold"] == 65.0
    assert body["scores"]["trend"]["sell_threshold"] == 45.0


def test_plan_honesty_carries_the_caveats_that_must_not_be_footnotes(client) -> None:
    body = client.get("/api/v1/strategy/plan/600519.SH?strategy=fusion").json()
    honesty = body["honesty"]
    assert honesty["caveat"]
    assert "未复权" in honesty["caveat"]
    # 满仓口径 vs 1% 风险预算缩仓**不可比**——少了这句，页面会按仓位倍数夸大收益
    assert "不可比" in honesty["caveat"]
    assert honesty["verdict"] in {"positive", "negative", "insufficient"}
    assert honesty["verdict_text"]
    assert honesty["strategy_label"]
    # 没有基准数字，页面会反向撒谎（跌 15% 的策略也许在创造价值）
    assert "benchmark_return" in honesty
    assert "excess_return" in honesty


def test_plan_expectancy_is_recomputed_from_real_trades(client) -> None:
    """展示的统计必须来自真实回测，而不是任何常量或重复实现的公式。"""
    plan = client.get("/api/v1/strategy/plan/600519.SH?strategy=fusion&days=500").json()
    trades = client.get("/api/v1/strategy/backtest/600519.SH?strategy=fusion&days=500").json()[
        "trades"
    ]
    honesty = plan["honesty"]

    assert honesty["trade_count"] == len(trades)
    if trades:
        assert honesty["avg_hold_bars"] == pytest.approx(
            sum(t["hold_bars"] for t in trades) / len(trades), abs=0.05
        )
        assert honesty["expectancy_pct"] == pytest.approx(
            sum(t["pnl_pct"] for t in trades) / len(trades), abs=0.01
        )
        wins = [t for t in trades if t["pnl_pct"] > 0]
        assert honesty["win_rate"] == pytest.approx(len(wins) / len(trades), abs=1e-6)


def test_different_strategies_produce_different_expectancy(client) -> None:
    """若两行数字相同，说明那数字没跟着规则走。"""
    values = {
        name: client.get(f"/api/v1/strategy/plan/600519.SH?strategy={name}").json()["honesty"]
        for name in ("ma_cross", "kdj_cross", "fusion")
    }
    fingerprints = {
        (item["trade_count"], item["expectancy_pct"]) for item in values.values()
    }
    assert len(fingerprints) > 1
    for name, item in values.items():
        assert item["strategy"] == name


def test_plan_rejects_an_unknown_strategy_with_400(client) -> None:
    response = client.get("/api/v1/strategy/plan/600519.SH?strategy=guaranteed_money")
    assert response.status_code == 400
    assert "未知策略" in response.json()["detail"]


def test_plan_404s_on_an_unknown_code(client) -> None:
    assert client.get("/api/v1/strategy/plan/999999.SH").status_code == 404


# ======================================================================================
# 2：回测
# ======================================================================================
def test_backtest_exposes_eleven_strategies_and_the_expectancy(client) -> None:
    body = client.get("/api/v1/strategy/backtest/600519.SH?strategy=fusion").json()
    assert len(body["strategies"]) == 11
    assert body["fused"] is True
    assert body["expectancy"]["strategy"] == "fusion"
    for trade in body["trades"]:
        assert trade["exit_reason"] in {"signal", "stop", "trail", "end"}

    plain = client.get("/api/v1/strategy/backtest/600519.SH?strategy=ma_cross").json()
    assert plain["fused"] is False
    assert len(plain["strategies"]) == 11


def test_quant_backtest_still_returns_exactly_eight(client) -> None:
    """回归护栏：`/quant/backtest` 的 strategies 是钉死的契约，加融合策略不许动它。"""
    body = client.get("/api/v1/quant/backtest/600519.SH").json()
    assert len(body["strategies"]) == 8
    assert "fusion" not in body["strategies"]
    assert "expectancy" not in body


# ======================================================================================
# 3：11 策略实测对比
# ======================================================================================
def test_expectancy_table_has_eleven_rows_and_exactly_one_baseline(client) -> None:
    rows = client.get("/api/v1/strategy/expectancy/600519.SH").json()
    assert len(rows) == 11
    assert [row["strategy"] for row in rows if row["is_baseline"]] == ["buy_hold"]


def test_expectancy_never_labels_a_losing_row_positive(client) -> None:
    """本功能的核心契约：期望为负的行**不可能**是 positive。"""
    rows = client.get("/api/v1/strategy/expectancy/600519.SH").json()
    for row in rows:
        assert row["verdict"] in {"positive", "negative", "insufficient"}
        if row["verdict"] == "positive":
            assert row["expectancy_pct"] is not None and row["expectancy_pct"] > 0
            assert row["trade_count"] >= 20  # 样本门槛是硬门槛
        if row["trade_count"] < 20:
            assert row["verdict"] == "insufficient"
        assert row["verdict_text"]


def test_expectancy_rows_are_sorted_by_measured_expectancy(client) -> None:
    rows = client.get("/api/v1/strategy/expectancy/600519.SH").json()
    values = [row["expectancy_pct"] for row in rows if row["expectancy_pct"] is not None]
    assert values == sorted(values, reverse=True)
    # 零成交（None）排在最后，不许混进排行榜中间
    ranked = [row["expectancy_pct"] is None for row in rows]
    assert ranked == sorted(ranked)


def test_expectancy_404s_on_an_unknown_code(client) -> None:
    assert client.get("/api/v1/strategy/expectancy/999999.SH").status_code == 404


def test_expectancy_never_backfills_quota(client, mock_provider) -> None:
    """一次要跑 11 个策略，一旦允许回补就是 11 次上游调用。"""
    calls: list[str] = []

    class Watched(type(mock_provider)):
        def stock_history(self, code, start, end):  # type: ignore[no-untyped-def]
            calls.append(code)
            return super().stock_history(code, start, end)

    import app.services.history as history_mod

    original = history_mod.get_provider
    history_mod.get_provider = lambda: Watched()  # type: ignore[assignment]
    try:
        assert client.get("/api/v1/strategy/expectancy/600519.SH").status_code == 200
    finally:
        history_mod.get_provider = original  # type: ignore[assignment]
    assert calls == []


# ======================================================================================
# 4-8：持仓流水
# ======================================================================================
def test_empty_positions_is_an_empty_list_not_an_error(client) -> None:
    assert client.get("/api/v1/strategy/positions").json() == []


def test_post_then_list_position_with_rule_derived_stop(client) -> None:
    created = _post_trade(client)
    assert created.status_code == 201
    body = created.json()
    assert body["ts_code"] == "600519.SH"
    assert body["status"] == "holding"
    assert body["shares"] == 200.0
    assert body["trade_count"] == 1
    assert body["action"] in {"exit", "reduce", "hold", "add", "watch"}
    assert body["action_label"]
    # 有库内日线 → 止损由规则推导，而不是"没有"
    assert body["stop_source"] in {"rule", "user"}
    if body["stop_source"] == "rule":
        assert body["stop_price"] is not None
        assert body["stop_distance_pct"] is not None

    listed = client.get("/api/v1/strategy/positions").json()
    assert [item["ts_code"] for item in listed] == ["600519.SH"]
    assert listed[0]["shares"] == 200.0

    trades = client.get("/api/v1/strategy/positions/600519.SH/trades").json()
    assert len(trades) == 1
    assert trades[0]["side"] == "buy"
    assert trades[0]["name"] == "贵州茅台"


def test_weighted_average_cost_and_realized_pnl_are_hand_checkable(client) -> None:
    """手工核对多笔流水：卖出不改均价、只减股数（券商口径）。"""
    _post_trade(client, price=1000.0, shares=200.0, fee=0.0)
    _post_trade(client, trade_date="2026-01-06", price=1200.0, shares=100.0, fee=0.0)
    # 均价 = (1000×200 + 1200×100) / 300 = 1066.667
    after_two_buys = client.get("/api/v1/strategy/positions").json()[0]
    assert after_two_buys["shares"] == 300.0
    assert after_two_buys["avg_cost"] == pytest.approx(1066.6667, abs=1e-3)
    assert after_two_buys["cost"] == pytest.approx(320000.0, abs=0.01)
    assert after_two_buys["realized_pnl"] == 0.0

    # 卖 100 股 @1300：已实现 = (1300 − 1066.667) × 100 = 23333.33；均价不变
    sold = _post_trade(
        client,
        side="sell",
        trade_date="2026-01-07",
        price=1300.0,
        shares=100.0,
        fee=0.0,
    )
    assert sold.status_code == 201
    body = sold.json()
    assert body["shares"] == 200.0
    assert body["avg_cost"] == pytest.approx(1066.6667, abs=1e-3)
    assert body["realized_pnl"] == pytest.approx(23333.33, abs=0.01)


def test_selling_more_than_held_is_rejected(client) -> None:
    """重要防线：卖出股数不得超过持仓。"""
    _post_trade(client, shares=100.0)
    response = _post_trade(client, side="sell", price=1500.0, shares=200.0)
    assert response.status_code == 422
    assert "超过当前持仓" in response.json()["detail"]
    # 被拒之后持仓不变
    assert client.get("/api/v1/strategy/positions").json()[0]["shares"] == 100.0


def test_a_sale_dated_before_the_buy_is_rejected(client) -> None:
    """总股数够、但时间上不可能——只比较"总卖出 vs 总买入"会放过这种流水。

    `summarize_ledger` 也抓不到它：它把超卖夹到 0，是显示函数不是校验器。
    """
    _post_trade(client, trade_date="2026-03-01", shares=200.0)
    response = _post_trade(
        client, side="sell", trade_date="2026-01-05", price=1500.0, shares=100.0
    )
    assert response.status_code == 422
    assert "负数" in response.json()["detail"]

    # 证明这条校验不是形式主义：显示层对同一份流水毫无察觉——它把负数那一步夹到 0，
    # 于是最终报出 200 股，而按经济含义此刻只该有 100 股（先卖后买在时间上不成立）。
    from datetime import date

    from app.services.quant import decision as decision_mod

    impossible = [
        decision_mod.LedgerEntry("sell", date(2026, 1, 5), 1500.0, 100.0, 0.0),
        decision_mod.LedgerEntry("buy", date(2026, 3, 1), 1500.0, 200.0, 0.0),
    ]
    displayed = decision_mod.summarize_ledger(impossible)
    assert displayed is not None
    assert displayed.shares > 100.0  # 夹到 0 之后反而多算了 100 股


def test_patch_targets_the_row_by_id_not_by_field_equality(client) -> None:
    """两笔同日同价的流水会互相冒充——按字段全等去定位会改错行而校验照过。"""
    _post_trade(client, trade_date="2026-01-05", price=1500.0, shares=100.0, note="第一笔")
    _post_trade(client, trade_date="2026-01-05", price=1500.0, shares=100.0, note="第二笔")
    rows = client.get("/api/v1/strategy/positions/600519.SH/trades").json()
    assert [row["note"] for row in rows] == ["第一笔", "第二笔"]

    second = rows[1]["id"]
    patched = client.patch(
        f"/api/v1/strategy/positions/trades/{second}", json={"shares": 250.0}
    )
    assert patched.status_code == 200
    assert patched.json()["shares"] == 350.0  # 100 + 250

    after = client.get("/api/v1/strategy/positions/600519.SH/trades").json()
    assert [row["shares"] for row in after] == [100.0, 250.0]  # 改的是第二笔
    assert [row["note"] for row in after] == ["第一笔", "第二笔"]


def test_patch_into_an_oversell_is_rejected_and_leaves_the_row_alone(client) -> None:
    _post_trade(client, shares=100.0)
    rows = client.get("/api/v1/strategy/positions/600519.SH/trades").json()
    response = client.patch(
        f"/api/v1/strategy/positions/trades/{rows[0]['id']}",
        json={"side": "sell", "shares": 100.0},
    )
    assert response.status_code == 422
    assert "负数" in response.json()["detail"]
    assert client.get("/api/v1/strategy/positions").json()[0]["shares"] == 100.0


def test_selling_everything_marks_the_position_closed(client) -> None:
    _post_trade(client, shares=100.0)
    sold = _post_trade(client, side="sell", trade_date="2026-02-01", price=1600.0, shares=100.0)
    assert sold.status_code == 201
    assert sold.json()["status"] == "closed"
    assert sold.json()["shares"] == 0.0

    assert client.get("/api/v1/strategy/positions").json() == []  # 默认只看 holding
    all_rows = client.get("/api/v1/strategy/positions?status=all").json()
    assert len(all_rows) == 1
    assert all_rows[0]["status"] == "closed"
    assert all_rows[0]["realized_pnl"] != 0.0  # 已实现盈亏在清仓后仍可见


def test_post_rejects_an_unknown_code_with_404_not_500(client) -> None:
    """先查 Stock：否则 SQLite 的外键会抛 IntegrityError 变成 500。"""
    response = _post_trade(client, ts_code="999999.SH")
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("field", "value"),
    [("shares", 0), ("price", 0), ("price", -1), ("shares", -100)],
)
def test_post_rejects_non_positive_numbers(client, field, value) -> None:
    assert _post_trade(client, **{field: value}).status_code == 422


def test_post_rejects_a_malformed_code(client) -> None:
    assert _post_trade(client, ts_code="600519").status_code == 422


def test_a_user_supplied_stop_makes_the_divergence_visible(client) -> None:
    """用户改过止损意味着屏上的实测期望不再描述他自己的风险——这一点必须显形。"""
    body = _post_trade(client, stop_price=1400.0).json()
    assert body["stop_source"] == "user"
    assert body["stop_price"] == 1400.0


def test_patch_updates_only_the_given_fields(client) -> None:
    trade_id = _post_trade(client).json()  # 先拿到 advice；明细里取 id
    rows = client.get("/api/v1/strategy/positions/600519.SH/trades").json()
    trade_id = rows[0]["id"]

    patched = client.patch(
        f"/api/v1/strategy/positions/trades/{trade_id}",
        json={"stop_price": 1425.0},
    )
    assert patched.status_code == 200
    assert patched.json()["stop_source"] == "user"
    assert patched.json()["stop_price"] == 1425.0

    # 没提到的字段不许被清成 None（exclude_unset 的意义）
    rows = client.get("/api/v1/strategy/positions/600519.SH/trades").json()
    assert rows[0]["price"] == 1500.0
    assert rows[0]["shares"] == 200.0
    assert rows[0]["side"] == "buy"
    assert rows[0]["trade_date"] == "2026-01-05"


def test_patch_cannot_create_an_oversell(client) -> None:
    _post_trade(client, shares=100.0)
    trade_id = client.get("/api/v1/strategy/positions/600519.SH/trades").json()[0]["id"]
    response = client.patch(
        f"/api/v1/strategy/positions/trades/{trade_id}",
        json={"side": "sell", "shares": 500.0},
    )
    assert response.status_code == 422


def test_delete_removes_the_row_and_the_position(client) -> None:
    _post_trade(client)
    trade_id = client.get("/api/v1/strategy/positions/600519.SH/trades").json()[0]["id"]
    assert client.delete(f"/api/v1/strategy/positions/trades/{trade_id}").status_code == 200
    assert client.get("/api/v1/strategy/positions/600519.SH/trades").json() == []
    assert client.get("/api/v1/strategy/positions").json() == []


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_unknown_trade_id_is_404(client, method) -> None:
    if method == "patch":
        response = client.patch("/api/v1/strategy/positions/trades/424242", json={"price": 1.0})
    else:
        response = client.delete("/api/v1/strategy/positions/trades/424242")
    assert response.status_code == 404


def test_positions_never_backfill_quota(client, mock_provider) -> None:
    """列表端点对每个持仓各调一次——允许回补就是"持仓越多、上游调用越多"。"""
    _post_trade(client)
    calls: list[str] = []

    class Watched(type(mock_provider)):
        def stock_history(self, code, start, end):  # type: ignore[no-untyped-def]
            calls.append(code)
            return super().stock_history(code, start, end)

    import app.services.history as history_mod

    original = history_mod.get_provider
    history_mod.get_provider = lambda: Watched()  # type: ignore[assignment]
    try:
        assert client.get("/api/v1/strategy/positions").status_code == 200
    finally:
        history_mod.get_provider = original  # type: ignore[assignment]
    assert calls == []


# ======================================================================================
# 降级：库内无日线
# ======================================================================================
def test_a_position_without_local_bars_degrades_instead_of_failing(client) -> None:
    """绝不 500、绝不编造分数：`scores=None` + 非空 data_warning。"""
    _seed_stock_without_bars()
    response = _post_trade(client, ts_code="000001.SZ", price=10.0, shares=1000.0)
    assert response.status_code == 201
    body = response.json()
    assert body["scores"] is None
    assert body["action"] == "watch"
    assert body["data_warning"]
    assert "同步" in body["data_warning"]
    # 持仓数字本身不受影响——用户录的流水不能因为缺日线就消失
    assert body["shares"] == 1000.0
    assert body["avg_cost"] == 10.0
    assert body["trade_count"] == 1
    assert body["stop_source"] == "none"

    listed = client.get("/api/v1/strategy/positions").json()
    assert len(listed) == 1
    assert listed[0]["data_warning"]


def test_expectancy_is_empty_for_a_code_without_local_bars(client) -> None:
    _seed_stock_without_bars()
    assert client.get("/api/v1/strategy/expectancy/000001.SZ").json() == []


def test_the_degraded_path_does_not_swallow_a_real_404(client) -> None:
    """降级分支不能被滥用成"什么都返回 200"——未知代码仍然 404。"""
    assert client.get("/api/v1/strategy/positions/999999.SH/trades").status_code == 404
