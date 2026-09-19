from __future__ import annotations

from app.api.routes import stock_view
from app.db.models import DailyBar, LatestQuote, ScoreSnapshot, Stock
from app.services.picks import PICKS_MIN_COVERAGE, PICKS_MIN_TOTAL, stock_query
from app.services.quant.factors import GROUP_WEIGHTS
from sqlalchemy import func, select


def test_picks_coverage_floor_is_reachable_without_a_news_score() -> None:
    """覆盖门槛必须 ≤「除新闻外每维都有分」的权重，否则这张清单结构性地永远为空。

    这是 2026-09-16 的真实故障：门槛写成 0.9，而四组权重是 0.40/0.30/0.20/0.10，
    够到 0.9 必须有新闻情绪分（0.20）。但新闻只认近 3 天，全市场只有 2 只有分、
    其余 5505 只都停在 0.8 —— 候选清单常年是空的，而空清单看起来像"今天没有
    可买标的"，不像"门槛写错了"。这条断言就是钉住这个区别。
    """
    without_news = round(1 - GROUP_WEIGHTS["新闻情绪"], 2)
    assert PICKS_MIN_COVERAGE <= without_news, (
        "覆盖率门槛高于「缺新闻维度」时的可达覆盖度，次日买点候选将永远为空"
    )


def test_picks_daily_keeps_a_stock_whose_only_missing_dimension_is_news(
    client, db_session, monkeypatch
) -> None:
    """回归钉子：满市场都没有新闻分时，候选清单仍必须出得来。

    把一只票设成线上最常见的状态（total_score 达标、coverage=0.8，即只缺新闻维度），
    再把时机判定钉成 buy（那是另一套规则，此处不是被测对象），清单就必须包含它。
    """
    # 选股口径住在 services.picks（/picks/daily 与 /strategy/simple 共用），所以桩打在那里——
    # 打在 app.api.routes 上不会生效，而失效的桩会让这条测试静默变成"断言清单为空"
    import app.services.picks as picks_mod

    monkeypatch.setattr(
        picks_mod,
        "compute_timing_for_codes",
        lambda db, codes: {
            code: {"label": "可分批买入", "tone": "buy", "detail": "测试桩"} for code in codes
        },
    )
    stock = db_session.scalar(select(Stock).order_by(Stock.ts_code))
    assert stock is not None
    score = db_session.scalar(select(ScoreSnapshot).where(ScoreSnapshot.ts_code == stock.ts_code))
    assert score is not None
    score.total_score = 80.0
    score.coverage = round(1 - GROUP_WEIGHTS["新闻情绪"], 2)  # 0.8 = 只缺新闻
    db_session.commit()

    body = client.get("/api/v1/picks/daily").json()
    picked = {row["ts_code"] for row in body["picks"]}
    assert stock.ts_code in picked
    # 文案不能说"覆盖≥90%"——门槛是 0.8，写 90% 就是一句可被一眼推翻的假话
    assert "≥80%" in body["note"]
    assert "90%" not in body["note"]


def test_stock_view_falls_back_to_the_latest_close_when_the_quote_is_missing(
    client, db_session
) -> None:
    """没有实时报价时用最近收盘价兜底，并如实标注来源与日期。

    盘后新浪不返数据、latest_quotes 会掉到 0 行，此时每个列表的价格列都是 --，
    而最近收盘价其实就在 daily_bars 里。
    """
    stock = db_session.scalar(select(Stock).order_by(Stock.ts_code))
    assert stock is not None
    # 兜底取的是**全局最近交易日**那根 bar，所以测试 bar 必须放在那一天
    latest = db_session.scalar(select(func.max(DailyBar.trade_date)))
    assert latest is not None
    db_session.query(DailyBar).filter(DailyBar.ts_code == stock.ts_code).delete()
    db_session.query(LatestQuote).filter(LatestQuote.ts_code == stock.ts_code).delete()
    db_session.add(
        DailyBar(
            ts_code=stock.ts_code,
            trade_date=latest,
            open=10.0,
            high=11.0,
            low=9.5,
            close=10.5,
            pre_close=10.0,
            pct_chg=5.0,
            volume=1000.0,
            amount=10500.0,
        )
    )
    db_session.commit()

    row = db_session.execute(stock_query().where(Stock.ts_code == stock.ts_code)).first()
    assert row is not None
    view = stock_view(row)
    assert view.price == 10.5
    assert view.pct_chg == 5.0
    assert view.price_source == "close"
    assert view.price_date == latest.isoformat()
    # 没有实时报价这件事依然是事实，只是不再等于"没有价格"
    assert view.is_stale is True


def test_stock_view_prefers_the_live_quote_over_the_close(
    client, db_session
) -> None:
    """反过来也要钉住：有实时报价时**不许**回退到收盘价。

    这一条是防"兜底写反了"——把 live 价换成昨收会静默显示一个过期价格，
    比显示 -- 更糟，因为它看起来是好的。
    """
    # stock_query 已经 outerjoin 了 LatestQuote，这里只筛掉没有报价的行，不再加一次 join
    row = db_session.execute(
        stock_query().where(LatestQuote.ts_code.is_not(None))
    ).first()
    assert row is not None
    quote = row[1]
    assert quote is not None
    view = stock_view(row)
    assert view.price == quote.price
    assert view.price_source == "quote"
    assert view.price_date is None


def test_stock_view_reports_no_price_when_there_is_neither_quote_nor_bar(
    client, db_session
) -> None:
    """停牌股既无报价也无当日日线：价格为 null 且来源是 none，不许拿旧收盘假装有价。"""
    stock = db_session.scalar(select(Stock).order_by(Stock.ts_code))
    assert stock is not None
    db_session.query(DailyBar).filter(DailyBar.ts_code == stock.ts_code).delete()
    db_session.query(LatestQuote).filter(LatestQuote.ts_code == stock.ts_code).delete()
    db_session.commit()

    row = db_session.execute(stock_query().where(Stock.ts_code == stock.ts_code)).first()
    assert row is not None
    view = stock_view(row)
    assert view.price is None
    assert view.price_source == "none"
    assert view.price_date is None


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
    assert body["rule_version"] == "v2"  # 横截面分位口径
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
    """综合评分 TOP-N 榜单：剔除科创/创业板后只剩 6 只主板，按 total 降序、带榜序，
    demo 新闻简报只落在主板自选 2 只上。"""
    rows = client.get("/api/v1/screener/top", params={"n": 8}).json()
    codes = [row["ts_code"] for row in rows]
    assert len(rows) == 6
    # 科创板(688981)/创业板(300750)被剔除，其余均为沪深主板
    assert "300750.SZ" not in codes and "688981.SH" not in codes
    totals = [row["total_score"] for row in rows]
    assert totals == sorted(totals, reverse=True)
    assert [row["rank"] for row in rows] == list(range(1, 7))
    # 榜单行带全量 StockView 形状，且新增简报字段齐全
    for row in rows:
        assert {"ts_code", "name", "price", "rank"} <= set(row)
        assert row["news_3d_count"] >= 0
        if row["news_title"] is None:
            assert row["news_tag"] is None
            assert row["news_sentiment"] is None
            assert row["news_sentiment_avg"] is None
            assert row["news_published_at"] is None

    # 主板自选 600519/601318 被 seed 了 demo 新闻；000858(主板但非自选) 无简报
    with_news = {row["ts_code"] for row in rows if row["news_title"] is not None}
    assert with_news == {"600519.SH", "601318.SH"}
    for row in rows:
        if row["ts_code"] in with_news:
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


def test_picks_daily_returns_diversified_buy_shortlist(client) -> None:
    """次日买点候选：纯实时计算，只含 tone=buy，低风险优先 + 单行业至多 2 只、最多 8 只。"""
    response = client.get("/api/v1/picks/daily")
    assert response.status_code == 200
    body = response.json()
    assert {"basis_date", "note", "picks"} <= set(body)
    assert body["note"]
    assert body["basis_date"] is None or body["basis_date"][:4].isdigit()

    picks = body["picks"]
    # demo 种子若满足 buy 则非空；不满足时为空是合法空态，直接通过（结构断言仍生效）
    for row in picks:
        assert row["total_score"] >= PICKS_MIN_TOTAL
        assert row["coverage"] >= PICKS_MIN_COVERAGE
        assert row["timing"] is not None
        assert row["timing"]["tone"] == "buy"
    assert len(picks) <= 8
    industry_counts = {}
    for row in picks:
        industry_counts[row["industry"]] = industry_counts.get(row["industry"], 0) + 1
    assert max(industry_counts.values(), default=0) <= 2
    # 低风险优先：风险序「低≤中≤高」在返回列表里单调不降
    risk_order = {"低": 0, "中": 1, "高": 2}
    ranks = [risk_order.get(row["risk_level"] or "高", 3) for row in picks]
    assert ranks == sorted(ranks)


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
