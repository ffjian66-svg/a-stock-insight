"""量化 API 测试：五个端点的响应形状、404 容错与空数据降级。

client 夹具会在临时库上 seed_demo（8 只 mock 股票），并在首次访问时触发
`ensure_bar_history` 按需回补——顺带验证这条链路在真实请求里也走得通。
"""

from __future__ import annotations

from app.services import history as history_mod


def test_quant_series_returns_indicator_columns_and_signals(client) -> None:
    response = client.get("/api/v1/quant/series/600519.SH?days=120")
    assert response.status_code == 200
    body = response.json()
    assert body["ts_code"] == "600519.SH"
    assert body["bars"] > 60
    assert len(body["indicators"]) == body["bars"]

    first, last = body["indicators"][0], body["indicators"][-1]
    assert first["ma60"] is None  # 预热期
    assert last["ma60"] is not None
    assert last["boll_mid"] is not None
    assert last["kdj_k"] is not None
    assert last["atr14"] is not None

    assert body["latest"]["ma20"] is not None
    assert body["latest"]["macd_hist"] is not None
    for signal in body["signals"]:
        assert signal["direction"] in {"buy", "sell", "neutral"}
        assert signal["detail"]
    assert body["signal_score"] is None or 0 <= body["signal_score"] <= 100


def test_quant_series_backfills_two_years_on_first_access(client, engine) -> None:
    """seed_demo 只铺了 ~90 根，首次访问应把它补到两年（本用例顺带守住这条需求）。"""
    history_mod._BACKFILLED.clear()
    response = client.get("/api/v1/quant/series/300750.SZ?days=500")
    assert response.status_code == 200
    assert response.json()["bars"] > 400


def test_quant_performance_exposes_risk_metrics(client) -> None:
    response = client.get("/api/v1/quant/performance/600519.SH?days=250")
    assert response.status_code == 200
    body = response.json()
    metrics = body["metrics"]
    assert metrics["bars"] > 20
    for key in (
        "cumulative_return",
        "annualized_return",
        "annualized_volatility",
        "sharpe",
        "sortino",
        "max_drawdown",
        "max_drawdown_days",
        "calmar",
        "win_rate",
        "beta",
        "alpha",
        "correlation",
    ):
        assert key in metrics
    assert metrics["max_drawdown"] <= 0
    assert body["benchmark"] == "上证指数"
    assert metrics["beta"] is not None  # 有基准序列时 Beta 必须算得出来
    assert len(body["equity"]) == metrics["bars"]


def test_quant_backtest_returns_equity_trades_and_costs_caveat(client) -> None:
    response = client.get("/api/v1/quant/backtest/600519.SH?strategy=ma_cross&days=250")
    assert response.status_code == 200
    body = response.json()
    assert body["strategy"] == "ma_cross"
    assert body["strategies"] == [
        "ma_cross",
        "macd_cross",
        "kdj_cross",
        "boll_reversion",
        "donchian",
        "trend_combo",
        "timing",
        "buy_hold",
    ]
    assert "未复权" in body["params"]["caveat"]
    assert "买不起一手" not in body["params"]["caveat"]
    assert len(body["equity"]) == body["metrics"]["bars"]
    # 曾经默认资金买不起高价股一手，端点会安静地返回一条直线的净值曲线
    assert body["trades"], "默认参数下必须真的成交过"
    # 前端按这些键名取值，改了就要同步改 QuantPage（曾因键名不符静默渲染成 0）
    assert set(body["stats"]) == {
        "trade_count",
        "win_rate",
        "avg_hold_bars",
        "avg_win",
        "avg_loss",
        "profit_factor",
    }
    for trade in body["trades"]:
        assert trade["shares"] % 100 == 0
        assert trade["entry_date"] <= trade["exit_date"]


def test_quant_backtest_rejects_unknown_strategy(client) -> None:
    assert client.get("/api/v1/quant/backtest/600519.SH?strategy=nope").status_code == 400


def test_quant_factors_lists_definitions_and_ranked_rows(client) -> None:
    response = client.get("/api/v1/quant/factors?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert body["rule_version"] == "v2"
    assert body["groups"] == ["趋势与动量", "质量与估值", "新闻情绪", "波动风险"]
    assert sum(body["group_weights"].values()) == 1.0
    assert {item["name"] for item in body["definitions"]} >= {"momentum_20", "low_vol"}

    rows = body["rows"]
    assert rows, "seed_demo 之后必须至少有评分过的股票"
    scores = [row["composite"] for row in rows]
    assert scores == sorted(scores, reverse=True)
    assert all(row["factors"] for row in rows)


def test_quant_scan_never_triggers_backfill(client, mock_provider) -> None:
    """扫描池有几百只，一旦逐只回补就会在第一次请求打光 TuShare 配额。"""
    calls: list[str] = []

    class Watched(type(mock_provider)):
        def stock_history(self, code, start, end):  # type: ignore[no-untyped-def]
            calls.append(code)
            return super().stock_history(code, start, end)

    watched = Watched()
    import app.services.history as history_mod

    monkey = history_mod.get_provider
    history_mod.get_provider = lambda: watched  # type: ignore[assignment]
    try:
        assert client.get("/api/v1/quant/scan?limit=5").status_code == 200
    finally:
        history_mod.get_provider = monkey  # type: ignore[assignment]
    assert calls == []


def test_quant_scan_filters_by_direction(client) -> None:
    response = client.get("/api/v1/quant/scan?direction=buy&limit=10")
    assert response.status_code == 200
    rows = response.json()
    assert all(row["direction"] == "buy" for row in rows)
    for row in rows:
        assert row["signal"]
        assert row["label"]
        assert row["strength"] > 0


def test_quant_scan_can_filter_a_single_rule(client) -> None:
    response = client.get("/api/v1/quant/scan?signal=rsi&limit=10")
    assert response.status_code == 200
    assert all(row["signal"] == "rsi" for row in response.json())


def test_quant_endpoints_404_on_unknown_code(client) -> None:
    for path in (
        "/api/v1/quant/series/999999.SZ",
        "/api/v1/quant/performance/999999.SZ",
        "/api/v1/quant/backtest/999999.SZ",
    ):
        assert client.get(path).status_code == 404, path
