"""全市场池化的取数、落库、任务编排与端点。

这里最要紧的三条：
- `test_recompute_never_touches_the_provider` —— 它要遍历 5,564 只股票，一旦允许回补
  就是 5,564 次上游调用，一次跑光 TuShare 配额。
- `test_failure_keeps_the_previous_snapshot` —— 中途崩溃必须留着上一份完整快照，
  绝不能半写（页面会拿半张表去否决买入）。
- `test_concurrent_trigger_never_leaves_a_run_stuck_in_running` —— 被拒的那次如果停在
  running，前端会一直转圈。
"""

from __future__ import annotations

import pytest
from app.db.models import StrategyPoolStats, SyncRun
from app.services import pool as pool_service
from app.services.quant.pool import PoolAccumulator

POOL = "/api/v1/strategy/pool"


def _session():
    """client 用的那个库的会话工厂（调用时取属性，见 test_strategy_api 的说明）。"""
    import app.db.session as db_session_mod

    return db_session_mod.SessionLocal()


def _stored() -> dict[str, StrategyPoolStats]:
    with _session() as session:
        return {row.strategy: row for row in session.query(StrategyPoolStats).all()}


def _snapshot() -> dict[str, tuple]:
    return {
        code: (row.computed_at, row.trade_count, row.expectancy_pct, row.verdict)
        for code, row in _stored().items()
    }


# ======================================================================================
# 1：配额纪律
# ======================================================================================
def test_recompute_never_touches_the_provider(client, mock_provider) -> None:
    """遍历全市场却允许回补 = 一次跑光 TuShare 配额。"""
    calls: list[str] = []

    class Watched(type(mock_provider)):
        def stock_history(self, code, start, end):  # type: ignore[no-untyped-def]
            calls.append(code)
            return super().stock_history(code, start, end)

        def history(self, code, days=90):  # type: ignore[no-untyped-def]
            calls.append(code)
            return super().history(code, days)

        def daily(self, trade_date):  # type: ignore[no-untyped-def]
            calls.append("*")
            return super().daily(trade_date)

    watched = Watched()
    import app.services.history as history_mod

    monkey = history_mod.get_provider
    history_mod.get_provider = lambda: watched  # type: ignore[assignment]
    try:
        with _session() as session:
            pool_service.recompute_market_stats(session)
    finally:
        history_mod.get_provider = monkey  # type: ignore[assignment]

    assert calls == [], f"池化不许碰 provider，却调用了 {calls}"
    # 静态上也确认一遍：模块命名空间里根本没有 get_provider 这个名字
    assert "get_provider" not in vars(pool_service)


# ======================================================================================
# 2：落库形状与幂等
# ======================================================================================
def test_recompute_writes_one_row_per_strategy(client) -> None:
    from app.services.quant import backtest as backtest_engine

    with _session() as session:
        written = pool_service.recompute_market_stats(session)

    stored = _stored()
    assert written == len(backtest_engine.all_strategy_names())
    assert set(stored) == set(backtest_engine.all_strategy_names())

    row = stored["fusion"]
    # 宇宙口径必须落库，页面没有第二个地方能拿到它
    assert row.universe_total >= row.universe_used >= row.stocks_with_trades >= 0
    assert row.bars_median > 0
    assert row.universe_used > 0
    assert row.window_from is not None and row.window_to is not None
    # 血缘：没有 rule_version，改了内核之后这张表会带着新时间戳继续用旧数字否决买入
    assert row.rule_version == "v2"
    assert row.min_bars == 60
    assert (row.stop_loss_atr, row.trail_atr) == (2.0, 2.0)
    # 买入持有是基准行：超额恒为 0
    assert stored["buy_hold"].excess_per_bar_pct == 0.0


def test_recompute_is_idempotent(client) -> None:
    with _session() as session:
        pool_service.recompute_market_stats(session)
    first = _snapshot()

    with _session() as session:
        written = pool_service.recompute_market_stats(session)

    second = _snapshot()
    assert written == len(second)
    # upsert 而不是 delete+insert：行数不变、数字逐位不变（computed_at 会更新，故不计入）
    assert {code: value[1:] for code, value in first.items()} == {
        code: value[1:] for code, value in second.items()
    }


def test_failure_keeps_the_previous_snapshot(client, monkeypatch) -> None:
    """中途崩溃必须留着上一份完整快照。半写的表会被页面拿去否决买入。

    **不要在这里调 `monkeypatch.undo()`**：`client` fixture 用的是同一个 monkeypatch
    实例（pytest 的 `monkeypatch` 是函数级单例），undo 会把它的库隔离补丁一起撤掉，
    后面的查询就打到另一个库上去了——那会让这条断言失去意义（它自己踩过一次）。
    读库不经过 `PoolAccumulator`，所以根本不需要还原 `finalize`。
    """
    with _session() as session:
        pool_service.recompute_market_stats(session)
    before = _snapshot()
    assert before

    def boom(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("模拟计算中途崩溃")

    monkeypatch.setattr(PoolAccumulator, "finalize", boom)
    with pytest.raises(RuntimeError):
        with _session() as session:
            pool_service.recompute_market_stats(session)

    after = _snapshot()
    assert after == before  # 逐位不变，包括 computed_at


# ======================================================================================
# 3：任务编排
# ======================================================================================
def test_run_job_creates_and_finishes_its_own_sync_run(client) -> None:
    count = pool_service.run_pool_job()

    assert count is not None and count > 0
    with _session() as session:
        run = session.query(SyncRun).filter_by(job_type="pool").one()
    assert run.status == "success"
    assert run.items_updated == count
    assert run.finished_at is not None


def test_concurrent_trigger_never_leaves_a_run_stuck_in_running(client) -> None:
    """被锁拒掉的那次必须把 SyncRun 结掉并说明原因——停在 running 前端会一直转圈。"""
    run_id = pool_service.start_run()
    assert pool_service._POOL_LOCK.acquire(blocking=False)
    try:
        assert pool_service.run_pool_job(run_id=run_id) is None
    finally:
        pool_service._POOL_LOCK.release()

    with _session() as session:
        run = session.get(SyncRun, run_id)
    assert run is not None
    assert run.status == "failed"
    assert "已有一次全市场汇总在执行" in run.message
    assert run.finished_at is not None


def test_refresh_endpoint_returns_a_pollable_run_id(client) -> None:
    """响应里必须带**结构化的** `id`，前端才能轮询。

    不能只把任务号写进 `message` 让前端正则抠——message 是给人读的文案，改一次措辞
    轮询就静默失效，而这里的静默失效表现是"点了按钮，转圈，然后什么也没有"。
    """
    response = client.post(f"{POOL}/refresh")
    assert response.status_code == 202
    body = response.json()
    assert body["id"] > 0
    assert body["job_type"] == "pool"
    assert body["status"] == "running"

    with _session() as session:
        run = session.query(SyncRun).filter_by(job_type="pool").one()
    assert run.id == body["id"]
    # 端点建的行必须能被既有的作业查询接口查到，否则前端无从轮询
    assert client.get(f"/api/v1/sync/jobs/{run.id}").status_code == 200


def test_pool_is_not_a_generic_sync_job(client) -> None:
    """池化刻意不进 `POST /sync/jobs`：那条路径会把 4 分钟的任务塞进排他的同步锁。"""
    assert client.post("/api/v1/sync/jobs", json={"job_type": "pool"}).status_code == 422
    assert client.post("/api/v1/sync/jobs", json={"job_type": "quotes"}).status_code == 200


# ======================================================================================
# 4：端点
# ======================================================================================
def test_endpoint_returns_empty_state_instead_of_zeros(client) -> None:
    """没算过 → `computed_at=None` + `rows=[]`。**绝不能返回 0 笔 / 0.00%**，
    那会被读成"算过了，结果是零"。"""
    body = client.get(POOL).json()

    assert body["computed_at"] is None
    assert body["rows"] == []
    # 口径仍然要给：页面在没有数字时也要能说明这套汇总是什么口径
    assert body["min_bars"] == 60
    assert "生存偏差" in body["caveat"]


def test_endpoint_reports_the_denominators_with_every_answer(client) -> None:
    """分母必须和结论一起给。用户看不到"5,515 只 / 中位 96 个交易日"，就会把
    "20 万笔"读成"20 万个独立观测"。"""
    with _session() as session:
        pool_service.recompute_market_stats(session)

    body = client.get(POOL).json()
    assert body["computed_at"] is not None
    assert body["bars_median"] > 0
    assert body["universe_used"] > 0
    assert body["window_from"] is not None
    assert "不是这只股票的预期" in body["note"] or "stock_denominator" in body["note"]

    rows = {row["strategy"]: row for row in body["rows"]}
    assert len(rows) == 11
    assert rows["buy_hold"]["is_baseline"] is True
    for row in body["rows"]:
        assert row["stocks_with_trades"] >= 0
        # 反池化度量必须与均值一起给，否则"均值正、中位股负"会被藏起来
        assert "median_stock_expectancy_pct" in row
        assert "positive_stock_share" in row
        assert row["positive_stock_min_trades"] == 5


def test_endpoint_plan_refuses_to_buy_when_the_pool_says_no(client) -> None:
    """端到端：汇总否决之后，`/plan` 不再给买入结论。"""
    with _session() as session:
        pool_service.recompute_market_stats(session)
        # 直接把 donchian 那条改成"两条都为负"，制造一次确定的否决
        row = session.get(StrategyPoolStats, "donchian")
        assert row is not None
        row.verdict = "negative"
        row.expectancy_pct = -1.614
        row.excess_per_bar_pct = -0.128
        row.trade_count = 8_817
        session.commit()

    body = client.get("/api/v1/strategy/plan/600519.SH?strategy=donchian").json()
    # 个股这一层说什么都可以，但**只要不是买入**就说明否决没有放行
    if body["honesty"]["verdict"] == "positive":
        assert body["action"] == "watch"
        assert "全市场" in body["action_label"]
    else:
        assert body["action"] != "buy"
