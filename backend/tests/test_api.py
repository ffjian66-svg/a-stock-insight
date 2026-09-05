from __future__ import annotations


def test_system_status_exposes_mode_and_capabilities(client) -> None:
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mock_mode"] is True
    assert payload["data_mode"] == "mock"
    assert "financials" in payload["capabilities"]
    assert "trade_calendar" in payload["capabilities"]
    assert payload["provider_status_at"] is None


def test_market_overview_breadth_and_index_spark(client) -> None:
    response = client.get("/api/v1/market/overview")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["indices"]) == 3
    assert {item["code"] for item in payload["indices"]} == {"000001.SH", "399001.SZ", "399006.SZ"}
    for item in payload["indices"]:
        assert item["price"] > 0
        assert 0 < len(item["spark"]) <= 40
    breadth = payload["breadth"]
    assert breadth["rising"] + breadth["falling"] + breadth["flat"] == breadth["samples"] == 8
    assert "as_of" in breadth


def test_watchlist_lists_seeded_stocks_with_scores(client) -> None:
    response = client.get("/api/v1/watchlist")
    assert response.status_code == 200
    rows = response.json()
    assert [row["ts_code"] for row in rows] == ["600519.SH", "300750.SZ", "601318.SH", "688981.SH"]
    first = rows[0]
    assert first["name"] == "贵州茅台"
    assert first["price"] is not None
    assert first["total_score"] is not None
    assert len(first["explanations"]) == 4
    assert first["coverage"] == 1


def test_watchlist_add_remove_and_validation(client) -> None:
    added = client.post("/api/v1/watchlist", json={"ts_code": "000858.SZ"})
    assert added.status_code == 200
    assert "已加入自选" in added.json()["message"]
    assert len(client.get("/api/v1/watchlist").json()) == 5

    # 重复加入幂等
    again = client.post("/api/v1/watchlist", json={"ts_code": "000858.SZ"})
    assert again.status_code == 200
    assert len(client.get("/api/v1/watchlist").json()) == 5

    # 未知股票 404，非法代码格式 422
    assert client.post("/api/v1/watchlist", json={"ts_code": "999999.SZ"}).status_code == 404
    assert client.post("/api/v1/watchlist", json={"ts_code": "茅台"}).status_code == 422

    removed = client.delete("/api/v1/watchlist/000858.SZ")
    assert removed.status_code == 200
    codes = [row["ts_code"] for row in client.get("/api/v1/watchlist").json()]
    assert "000858.SZ" not in codes


def test_watchlist_quotes_only_includes_stocks_with_quote(client) -> None:
    client.post("/api/v1/watchlist", json={"ts_code": "000858.SZ"})
    response = client.get("/api/v1/watchlist/quotes")
    assert response.status_code == 200
    rows = response.json()
    # 新加入的 000858 还没有最新报价，因此被 inner join 排除
    assert {row["ts_code"] for row in rows} == {"600519.SH", "300750.SZ", "601318.SH", "688981.SH"}
    assert all(row["price"] > 0 for row in rows)
    assert rows[0]["name"] == "贵州茅台"
    assert rows[0]["source"] == "mock"


def test_search_matches_chinese_name(client) -> None:
    response = client.get("/api/v1/stocks/search", params={"q": "茅台"})
    assert response.status_code == 200
    rows = response.json()
    assert rows
    assert rows[0]["ts_code"] == "600519.SH"
    assert rows[0]["price"] is not None


def test_stock_detail_daily_bars_and_404(client) -> None:
    detail = client.get("/api/v1/stocks/600519.SH")
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == "贵州茅台"
    bars = client.get("/api/v1/stocks/600519.SH/daily-bars").json()
    assert 20 <= len(bars) <= 90
    assert {"date", "close", "volume", "pct_chg"} <= set(bars[0])
    assert client.get("/api/v1/stocks/999999.SZ").status_code == 404


def test_stock_news_exposes_analysis_metadata(client) -> None:
    rows = client.get("/api/v1/stocks/600519.SH/news").json()
    assert rows
    item = rows[0]
    assert item["analysis_mode"] == "demo"
    assert item["confidence"] is not None
    assert item["event_tag"]
    assert client.get("/api/v1/stocks/000858.SZ/news").json() == []


def test_score_explanation_returns_evidence(client) -> None:
    response = client.get("/api/v1/stocks/600519.SH/score-explanation")
    assert response.status_code == 200
    body = response.json()
    assert body["ts_code"] == "600519.SH"
    assert body["coverage"] == 1
    assert body["rule_version"] == "v1"
    assert body["calculated_at"] is not None
    assert len(body["explanations"]) == 4
    assert client.get("/api/v1/stocks/999999.SZ/score-explanation").status_code == 404


def test_screener_filters_and_sorts(client) -> None:
    default_rows = client.get("/api/v1/screener").json()
    assert len(default_rows) == 8
    totals = [row["total_score"] or 0 for row in default_rows]
    assert totals == sorted(totals, reverse=True)

    liquor = client.get("/api/v1/screener", params={"industry": "白酒"}).json()
    assert len(liquor) == 2
    assert {row["ts_code"] for row in liquor} == {"600519.SH", "000858.SZ"}

    pe_rows = client.get("/api/v1/screener", params={"sort": "pe"}).json()
    pes = [row["pe_ttm"] for row in pe_rows]
    assert all(value is not None for value in pes)
    assert pes == sorted(pes)

    filtered = client.get("/api/v1/screener", params={"min_score": 90}).json()
    assert all(row["total_score"] >= 90 for row in filtered)
    assert client.get("/api/v1/screener", params={"sort": "bogus"}).status_code == 422


def test_screener_top_returns_ranked_board_with_news_brief(client) -> None:
    """综合评分 TOP-N 榜单：按 total 降序、带榜序，demo 新闻简报只落在自选 4 只上。"""
    rows = client.get("/api/v1/screener/top", params={"n": 8}).json()
    assert len(rows) == 8
    totals = [row["total_score"] for row in rows]
    assert totals == sorted(totals, reverse=True)
    assert [row["rank"] for row in rows] == list(range(1, 9))
    # 榜单行带全量 StockView 形状，且新增简报字段齐全
    for row in rows:
        assert {"ts_code", "name", "price", "rank"} <= set(row)
        assert row["news_3d_count"] >= 0
        if row["news_title"] is None:
            assert row["news_tag"] is None
            assert row["news_sentiment"] is None
            assert row["news_sentiment_avg"] is None
            assert row["news_published_at"] is None

    watch = {"600519.SH", "300750.SZ", "601318.SH", "688981.SH"}
    with_news = {row["ts_code"] for row in rows if row["news_title"] is not None}
    assert with_news == watch  # 只有自选 4 只被 seed 了 demo 新闻
    for row in rows:
        if row["ts_code"] in watch:
            assert row["news_tag"]
            assert isinstance(row["news_sentiment"], float)
            assert isinstance(row["news_sentiment_avg"], float)
            assert row["news_3d_count"] == 1


def test_screener_top_slices_n_and_validates(client) -> None:
    top3 = client.get("/api/v1/screener/top", params={"n": 3}).json()
    assert len(top3) == 3
    assert [row["rank"] for row in top3] == [1, 2, 3]
    totals = [row["total_score"] for row in top3]
    assert totals == sorted(totals, reverse=True)
    # n 越界：0 与 101 都 422
    assert client.get("/api/v1/screener/top", params={"n": 0}).status_code == 422
    assert client.get("/api/v1/screener/top", params={"n": 101}).status_code == 422


def test_screener_rows_carry_timing_advice(client) -> None:
    """每只候选行带操作时机标签；只断言形状，不断言具体标签/倾向。"""
    rows = client.get("/api/v1/screener").json()
    assert rows
    for row in rows:
        assert "timing" in row
        if row["timing"] is not None:
            assert set(row["timing"]) == {"label", "tone", "detail"}
            assert row["timing"]["tone"] in {"buy", "hold", "reduce", "watch"}
            assert row["timing"]["label"]


def test_sync_job_lifecycle(client) -> None:
    created = client.post("/api/v1/sync/jobs", json={"job_type": "quotes"})
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "success"
    assert body["items_updated"] == 4
    assert body["error_class"] == ""

    status = client.get(f"/api/v1/sync/jobs/{body['id']}").json()
    assert status["job_type"] == "quotes"
    assert status["status"] == "success"

    assert client.post("/api/v1/sync/jobs", json={"job_type": "bogus"}).status_code == 422
    assert client.get("/api/v1/sync/jobs/99999").status_code == 404


def test_sync_calendar_and_fundamentals_via_api(client) -> None:
    calendar = client.post("/api/v1/sync/jobs", json={"job_type": "calendar"})
    assert calendar.status_code == 200
    assert calendar.json()["items_updated"] == 71
    fundamentals = client.post("/api/v1/sync/jobs", json={"job_type": "fundamentals"})
    assert fundamentals.status_code == 200
    assert fundamentals.json()["items_updated"] == 4
