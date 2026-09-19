"""「明日操作」页（`GET /strategy/simple`）：四个数字 + 一句结论。

这一页**不新增任何计算**，它的全部风险都在"把已有的结论抄歪"上，所以测试的重心不是
"字段在不在"，而是两件事：

1. **结论逐字来自 `Plan`，不重算**——`test_the_row_copies_the_conclusion_it_never_recomputes_it`
   是真正干活的那条。负期望必须仍然落在 `avoid`，"从 verdict 推 action"这种简化会在这里红。
2. **绝不向上游回补日线**——列表端点逐个回补正是把 TuShare 配额打穿的 bug。
   `test_the_board_never_asks_the_provider` 与 `test_the_row_never_backfills_either` 各钉一面。
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

# 常量照 `tests/test_api.py` 的做法在模块级导入：它只是个数，不碰 SessionLocal。
from app.services.picks import BOARD_PER_INDUSTRY, PICKS_PER_INDUSTRY


# ======================================================================================
# 造数据
# ======================================================================================
def _session():
    """client 用的那个库的会话工厂。必须在调用时取属性（conftest 换过 SessionLocal）。"""
    import app.db.session as db_session_mod

    return db_session_mod.SessionLocal()


def _force_all_candidates(monkeypatch) -> None:
    """把 8 只演示股全部变成可入选的候选。

    两件事缺一不可：评分达标（否则 SQL 就筛掉了），以及时机判定为 buy（那是另一套规则，
    这里不是被测对象）。同 `tests/test_api.py` 的做法。
    """
    import app.services.picks as picks_mod
    from app.db.models import ScoreSnapshot
    from sqlalchemy import select

    monkeypatch.setattr(
        picks_mod,
        "compute_timing_for_codes",
        lambda db, codes: {
            code: {"label": "可分批买入", "tone": "buy", "detail": "测试桩"} for code in codes
        },
    )
    with _session() as session:
        for score in session.scalars(select(ScoreSnapshot)).all():
            score.total_score = 80.0
            score.coverage = 0.8
        session.commit()


def _seed_bars(
    ts_code: str,
    count: int | None = None,
    *,
    start_price: float = 100.0,
    pre_window: int = 0,
    holidays: int = 0,
    industry: str = "测试",
) -> tuple[int, int]:
    """给一只股票重造日线：最近 `count` 个工作日（`None` = 覆盖满两年窗口），末日是今天。

    `count=None` 这一支是给「与单股页对得上」那条测试用的，不是凑数：`ensure_bar_history`
    的 `_covered` 判据比对的是**整整两年窗口的两个端点**（`history._EDGE_SLACK` 只容 12 天），
    库内日线不够长时 `/strategy/plan` 会去回补、本页不会，两条端点看到的就不是同一批 bar。

    `holidays` 从窗口里均匀扣掉这么多工作日。**不是装饰**：只按周一到周五填，两年有 523 根，
    而 A 股两年实际只有 ~486 个交易日。差别决定这条测试是不是空转——`stored_bars` 的上限是
    "最近 500 根"，523 根时它自动把窗口外的日线挡在外面，两个取数口径于是看不出差别；
    486 根时挡不住。线上是后者（实测 4/8 只候选命中）。

    `pre_window` 额外在窗口起点**之前**再种几根，专门用来钉「两个端点的取数窗口必须一致」：
    `stored_bars` 会把这些算进去，`ensure_bar_history` 不会。

    返回 `(总根数, 落在窗口内的根数)`——两个值都要，用例才能证明"窗口外那几根真的种进去了"
    而不是空转通过。

    `industry` 只在这个代码**还不存在**时用得上（已存在的 `Stock` 不会被改写）。分组用例靠
    它把若干只放进同一个板块。
    """
    import math

    from app.db.models import DailyBar, Stock
    from app.services.history import history_window
    from sqlalchemy import delete

    window_start, window_end = history_window()
    days: list[date] = []
    day = window_start
    while day <= window_end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    if holidays:
        step = max(len(days) // holidays, 2)
        days = [d for i, d in enumerate(days) if i % step != 0]
    if count is not None:
        days = days[-count:]  # 只留最近 count 个工作日，末日仍是今天
    if pre_window:
        earlier: list[date] = []
        day = window_start - timedelta(days=1)
        while len(earlier) < pre_window:
            if day.weekday() < 5:
                earlier.append(day)
            day -= timedelta(days=1)
        days = sorted(earlier) + days

    with _session() as session:
        if session.get(Stock, ts_code) is None:
            session.add(
                Stock(
                    ts_code=ts_code,
                    symbol=ts_code[:6],
                    name="测试股",
                    industry=industry,
                    market="主板",
                )
            )
            session.flush()
        session.execute(delete(DailyBar).where(DailyBar.ts_code == ts_code))
        for i, trade_date in enumerate(days):
            close = round(start_price * (1 + 0.0009 * i + 0.03 * math.sin(i / 7.0)), 2)
            session.add(
                DailyBar(
                    ts_code=ts_code,
                    trade_date=trade_date,
                    open=round(close * 0.995, 2),
                    high=round(close * 1.012, 2),
                    low=round(close * 0.988, 2),
                    close=close,
                    pre_close=round(close * 0.998, 2),
                    pct_chg=0.2,
                    volume=1_000_000.0,
                    amount=close * 1_000_000.0,
                    source="test",
                )
            )
        session.commit()
    return len(days), len(days) - pre_window


def _only_one_candidate(monkeypatch, ts_code: str) -> None:
    """只留一只候选，其余清成不够门槛——便于对单只做逐项比对。"""
    import app.services.picks as picks_mod
    from app.db.models import ScoreSnapshot
    from sqlalchemy import select

    monkeypatch.setattr(
        picks_mod,
        "compute_timing_for_codes",
        lambda db, codes: {
            code: {"label": "可分批买入", "tone": "buy", "detail": "测试桩"}
            for code in codes
            if code == ts_code
        },
    )
    with _session() as session:
        for score in session.scalars(select(ScoreSnapshot)).all():
            score.total_score = 80.0 if score.ts_code == ts_code else 10.0
            score.coverage = 0.8
        session.commit()


# ======================================================================================
# 板块视图的取数与造数
# ======================================================================================
def _flat_rows(body: dict) -> list[dict]:
    """所有板块的行，按页面顺序摊平。

    「每一行都必须有 X」这类性质用它断言——按板块逐层写会让断言本身变成一段要读的代码，
    而它想说的只是"每一行"。分组是否丢行由 `len(_flat_rows(body))` 的总数单钉。
    """
    return [row for sector in body["sectors"] for row in sector["rows"]]


def _sector(body: dict, industry: str) -> dict:
    """取某个板块，缺了就红——不要写 `next(..., None)` 再断言非空。"""
    return next(sector for sector in body["sectors"] if sector["industry"] == industry)


def _seed_candidate(
    ts_code: str,
    *,
    industry: str,
    total_mv: float | None = None,
    bars: int = 25,
    name: str = "测试股",
    start_price: float = 100.0,
    total_score: float = 80.0,
    risk_level: str = "中",
) -> None:
    """造一只**真候选**：Stock + 过门槛的评分 + 日线 + 最新基本面快照。

    `_force_all_candidates` 只改**已存在**的 `ScoreSnapshot`，所以新造的代码必须自己插一条，
    否则它在第 1 步就被 SQL 筛掉了——而那种失败看起来像"分组坏了"，不像"数据没造够"。
    日线默认 25 根（≥ `MIN_BARS`=20），所以它不会被 `skipped` 掉。

    `total_mv` 为 None 时**不插**基本面快照，用来测"市值缺失"那条分支。

    `total_score`/`risk_level` 决定它在 `/picks/daily` 的排序（低风险优先、同档高分优先）——
    造"排在所有人前面"的候选时用得上。
    """
    from app.db.models import FundamentalSnapshot, ScoreSnapshot, Stock
    from sqlalchemy import delete

    _seed_bars(ts_code, bars, industry=industry, start_price=start_price)
    with _session() as session:
        stock = session.get(Stock, ts_code)
        assert stock is not None
        stock.name = name
        stock.industry = industry
        if session.get(ScoreSnapshot, ts_code) is None:
            session.add(ScoreSnapshot(ts_code=ts_code, coverage=0.8, risk_level="中"))
        score = session.get(ScoreSnapshot, ts_code)
        assert score is not None
        score.total_score = total_score
        score.coverage = 0.8
        score.risk_level = risk_level
        session.execute(
            delete(FundamentalSnapshot).where(FundamentalSnapshot.ts_code == ts_code)
        )
        if total_mv is not None:
            session.add(
                FundamentalSnapshot(
                    ts_code=ts_code, trade_date=date.today(), total_mv=total_mv
                )
            )
        session.commit()


def _set_market_cap(ts_code: str, total_mv: float) -> None:
    """把某只股票最新一期快照的总市值改掉（没有快照就造一条）。

    用来做「市值只改顺序、不改结论」的对照——两次请求之间**只**动这一个数。
    """
    from app.db.models import FundamentalSnapshot
    from sqlalchemy import select

    with _session() as session:
        latest = session.scalar(
            select(FundamentalSnapshot)
            .where(FundamentalSnapshot.ts_code == ts_code)
            .order_by(FundamentalSnapshot.trade_date.desc())
        )
        if latest is None:
            session.add(
                FundamentalSnapshot(
                    ts_code=ts_code, trade_date=date.today(), total_mv=total_mv
                )
            )
        else:
            latest.total_mv = total_mv
        session.commit()


# ======================================================================================
# 1：形状
# ======================================================================================
def test_board_returns_the_four_numbers_per_candidate(client, monkeypatch) -> None:
    _force_all_candidates(monkeypatch)
    body = client.get("/api/v1/strategy/simple").json()

    assert body["strategy"] == "fusion"
    assert body["strategy_label"]
    assert body["window_days"] > 0
    # 行数非空：否则下面每条"每行都要有"的断言都是空转通过
    rows = _flat_rows(body)
    assert rows, "候选池非空时却一行都没有"
    assert body["sectors"], "有行就必然有板块"
    # 上限是**每板块** 10 只，不再是全局 8 只
    for sector in body["sectors"]:
        assert len(sector["rows"]) <= BOARD_PER_INDUSTRY

    for row in rows:
        # 四个数字，一个都不许缺——它们非 Optional 正是为了这条
        assert row["entry_zone"]["low"] is not None
        assert row["entry_zone"]["high"] is not None
        assert row["entry_zone"]["reference"] is not None
        assert row["expected_hold"]["low"] is not None
        assert row["expected_hold"]["high"] is not None
        assert row["stop_loss"]["recommended"] is not None
        # 止盈位可以为 None（ATR 缺失），但字段必须在
        assert "level" in row["take_profit"]

        assert row["action"] in {"buy", "watch", "avoid"}
        assert row["action_label"]
        assert row["honesty"]["verdict"] in {"positive", "negative", "insufficient"}
        assert row["honesty"]["verdict_text"]
        # 只统计库内日线，所以根数必然小于等于窗口
        assert 0 < row["bars"] <= body["window_days"]
        # 价格锚点是库内最后一根收盘，不是实时报价——理由见端点的循环注释
        assert row["price_source"] == "close"


def test_bars_and_median_report_the_stored_window_not_the_requested_one(
    client, monkeypatch
) -> None:
    """库内根数如实上报——绝不能把请求的 500 当成实际样本量。

    库内中位 96 根而窗口是 500 是线上常态，把 `window_days` 当成样本量会让"样本不足"
    看起来像"算过了、是负的"。
    """
    _only_one_candidate(monkeypatch, "600519.SH")
    seeded, _in_window = _seed_bars("600519.SH", 96)

    body = client.get("/api/v1/strategy/simple").json()
    row = next(r for r in _flat_rows(body) if r["ts_code"] == "600519.SH")

    assert seeded == 96
    assert row["bars"] == 96  # 库内真实根数，不是 window_days(500)
    assert body["bars_median"] == 96
    assert body["window_days"] == 500
    assert "绝不向上游回补" in body["note"]


# ======================================================================================
# 2：结论必须是从 Plan 抄的（本文件最重要的一条）
# ======================================================================================
def _real_plan(**overrides):
    """用演示股跑一个真 `Plan`，再按需替换字段——测的是搬运，不是内核。"""
    import app.api.strategy_routes as routes_mod
    from app.db.models import Stock
    from app.services.quant import decision
    from sqlalchemy import select

    with _session() as session:
        stock = session.scalar(select(Stock).order_by(Stock.ts_code))
        assert stock is not None
        bars = routes_mod._stored_bar_rows(session, stock.ts_code, routes_mod.PLAN_DAYS_DEFAULT)
        _, expectancy = routes_mod._run(bars, "fusion", routes_mod.PLAN_DAYS_DEFAULT)
        plan = decision.build_plan(
            bars,
            ts_code=stock.ts_code,
            name=stock.name,
            industry=stock.industry,
            strategy="fusion",
            expectancy=expectancy,
        )
    return dataclasses.replace(plan, **overrides)


def test_the_row_copies_the_conclusion_it_never_recomputes_it(client) -> None:
    """`action`/`action_label` 必须是 `Plan` 的原话。

    这不是同义反复：把 `action` 从 `verdict` 推出来（positive→buy，其余→watch）是这次改动最
    容易发生的"简化"，而它会同时删掉四条信号条件与全市场否决——负期望会重新渲染成买入。
    """
    import app.api.strategy_routes as routes_mod

    avoid = routes_mod._simple_row(
        _real_plan(action="avoid", action_label="不建议买入：该规则在此标的实测期望为负")
    )
    assert avoid.action == "avoid"
    assert avoid.action_label == "不建议买入：该规则在此标的实测期望为负"

    # 否决降级后的 watch 带着它自己的专门文案，也必须逐字保留
    vetoed = routes_mod._simple_row(
        _real_plan(action="watch", action_label="观望：该规则在全市场也不赚钱，本次不买入")
    )
    assert vetoed.action == "watch"
    assert vetoed.action_label == "观望：该规则在全市场也不赚钱，本次不买入"

    # 反过来：`buy` 的文案也不许被"统一"成别的
    bought = routes_mod._simple_row(_real_plan(action="buy", action_label="信号与实测均支持买入"))
    assert bought.action == "buy"
    assert bought.action_label == "信号与实测均支持买入"


def test_negative_expectancy_can_never_render_as_buy(client, monkeypatch) -> None:
    """每行都要自洽：显示买入就必须有正期望且样本够。

    如实说明：seed 数据下这条几乎是空转（每行都会是 insufficient），真正的守卫是
    `test_the_row_copies_the_conclusion_it_never_recomputes_it`。这里测的是"不会出现自相
    矛盾的行"，不是"结论算得对"。
    """
    _force_all_candidates(monkeypatch)
    for row in _flat_rows(client.get("/api/v1/strategy/simple").json()):
        if row["action"] == "buy":
            assert row["honesty"]["verdict"] == "positive"
            assert row["honesty"]["trade_count"] >= 20


def test_verdict_insufficient_below_twenty_trades(client, monkeypatch) -> None:
    _force_all_candidates(monkeypatch)
    for row in _flat_rows(client.get("/api/v1/strategy/simple").json()):
        if row["honesty"]["trade_count"] < 20:
            assert row["honesty"]["verdict"] == "insufficient"
            assert row["action"] != "buy"


# ======================================================================================
# 3：配额护栏
# ======================================================================================
def _watch_provider(mock_provider, calls: list[str]):
    """把 agent/services/history 的 `get_provider` 换成记录版，返回还原函数。"""
    import app.services.history as history_mod

    class Watched(type(mock_provider)):
        def stock_history(self, code, start, end):  # type: ignore[no-untyped-def]
            calls.append(code)
            return super().stock_history(code, start, end)

    original = history_mod.get_provider
    history_mod.get_provider = lambda: Watched()  # type: ignore[assignment]
    return lambda: setattr(history_mod, "get_provider", original)


def test_the_board_never_asks_the_provider(client, monkeypatch, mock_provider) -> None:
    """整页每只都不回补。

    这一页一次列几十上百行（线上实测 250 行），逐个回补就是几百次上游调用——正是配额烧穿的
    模式。8 只演示股只是这个形状的下界。
    """
    _force_all_candidates(monkeypatch)
    calls: list[str] = []
    restore = _watch_provider(mock_provider, calls)
    try:
        response = client.get("/api/v1/strategy/simple")
    finally:
        restore()

    assert response.status_code == 200
    assert calls == []


def test_the_row_never_backfills_either(client, monkeypatch, mock_provider) -> None:
    """⚠️ 上面那条会**静默通过**：演示股库里已有 90 根，`ensure_bar_history` 本来就不必回补。

    把库内日线压到 5 根再问一次，才真正走到"要不要回补"的判断上——`_covered` 这时是 False，
    若有人把 `backfill=False` 写成 True，这里就会看见一次上游调用。没有这条，护栏是纸做的。
    """
    _force_all_candidates(monkeypatch)
    seeded, _in_window = _seed_bars("600519.SH", 5, start_price=100.0)
    calls: list[str] = []
    restore = _watch_provider(mock_provider, calls)
    try:
        response = client.get("/api/v1/strategy/simple")
    finally:
        restore()

    assert seeded == 5
    assert response.status_code == 200
    assert calls == []
    # 5 根不足 20 根，所以它必须出现在 skipped 里，而不是带着几个 -- 混进表
    assert "600519.SH" in {item["ts_code"] for item in response.json()["skipped"]}


def test_rows_below_min_bars_are_skipped_with_a_reason_not_shown_with_dashes(
    client, monkeypatch
) -> None:
    """不足 MIN_BARS 的标的列进 `skipped` 并说明原因，绝不进表。

    20 根以下 ATR14/MA20 无定义、止损退到"现价×0.92"兜底——把这种行渲染成候选表里的四个
    `--`，读起来像"这只是没问题、只是缺数字"，那是另一种编造。
    """
    _force_all_candidates(monkeypatch)
    _seed_bars("600519.SH", 5, start_price=100.0)

    body = client.get("/api/v1/strategy/simple").json()
    rows = _flat_rows(body)
    assert "600519.SH" not in {row["ts_code"] for row in rows}
    skipped = next(item for item in body["skipped"] if item["ts_code"] == "600519.SH")
    assert skipped["bars"] == 5
    assert "20" in skipped["reason"]
    # 其余 7 只仍然在表里——跳过的是那一只，不是整张表
    assert len(rows) == 7
    # 白酒板块的 `selected` 仍是 2（它**入选**了），只有 `rows` 变少。这两个数字分开报，
    # 页面才能把"我们按市值挑了"和"这只样本不够"讲成两件事（见 SimpleSector）。
    baijiu = _sector(body, "白酒")
    assert baijiu["candidates"] == 2
    assert baijiu["selected"] == 2
    assert len(baijiu["rows"]) == 1


# ======================================================================================
# 4：与单股页对得上（同样的 bar → 同样的结论）
# ======================================================================================
def test_the_board_matches_the_single_stock_plan_on_the_same_bars(
    client, monkeypatch, mock_provider
) -> None:
    """库内 bar 相同时，本页与 `/strategy/plan` 必须逐项相同。

    bar 相同是有前提的：这里先覆盖满整整两年窗口，`_covered` 判为已覆盖，
    `/strategy/plan` 那侧的回补不会发生，两条端点看到同一批数据。**库内不足 500 根时两条
    端点会分叉**——那是有意的（本页不回补，只会更保守），由 `bars_median`/`bars`/`note`
    披露，不在这里断言。

    这个前提本身也断言掉了（`calls == []`）：`history._BACKFILLED` 是模块级全局、跨测试
    存活，只靠"库内根数够"是不够的——真去回补一次，`/strategy/plan` 就会拿到 provider 的
    另一批 bar，下面逐项比对会以一种看起来像"两页不一致"的方式红掉，而真实原因是测试
    自己没把前提摆平。
    """
    _only_one_candidate(monkeypatch, "600519.SH")
    # 额外在窗口起点之前种 3 根：`stored_bars` 会把它们算进去，`ensure_bar_history` 不会。
    # 不裁齐窗口的话，两条端点会在同一只股票上印出差 1 笔成交、小数位不同的两个期望值。
    #
    # **这里刻意不加 `holidays=`**：`load_bars(backfill=True)` 走的是
    # `ensure_bar_history(min_bars=days=500)`，而 `_covered(...) and len(rows) >= 500` 才短路
    # 回补——窗口内不足 500 根时单股页**必然**去调一次 provider（A 股两年实际只有 ~486 个
    # 交易日，所以线上确实如此）。种成 486 根会让下面 `calls == []` 红掉，而那是回补，
    # 不是本用例要测的东西。窗口裁剪本身由
    # `test_the_board_counts_only_bars_inside_the_two_year_window` 单独钉住——它不经过单股页，
    # 所以能用 486 根那个更真实的形状。
    seeded, in_window = _seed_bars("600519.SH", start_price=120.0, pre_window=3)

    calls: list[str] = []
    restore = _watch_provider(mock_provider, calls)
    try:
        plan = client.get("/api/v1/strategy/plan/600519.SH?strategy=fusion&days=500").json()
        board = client.get("/api/v1/strategy/simple").json()
    finally:
        restore()
    assert calls == [], "两条端点本应只读同一批库内 bar，却有回补发生"
    assert seeded == in_window + 3  # 前提：额外那 3 根真的种进去了，否则本用例是空转

    row = next(item for item in _flat_rows(board) if item["ts_code"] == "600519.SH")
    # 窗口外那 3 根不许计入样本（单股页也不计）；窗口内根数再多也只取最近 `days` 根
    assert row["bars"] == min(in_window, 500)

    assert plan["action"] in {"buy", "watch", "avoid"}  # 端点确实返回了计划
    assert row["action"] == plan["action"]
    assert row["action_label"] == plan["action_label"]
    assert row["price"] == plan["price"]
    assert row["bars"] == plan["bars"] == 500
    assert row["entry_zone"] == plan["entry_zone"]
    assert row["stop_loss"] == plan["stop_loss"]
    assert row["take_profit"] == plan["take_profit"]
    assert row["expected_hold"] == plan["expected_hold"]
    for key in ("trade_count", "expectancy_pct", "verdict", "verdict_text", "caveat"):
        assert row["honesty"][key] == plan["honesty"][key]


def test_the_board_counts_only_bars_inside_the_two_year_window(client, monkeypatch) -> None:
    """库内根数低于上限时，窗口起点之前那几根不许进样本。

    线上实测（2026-09-17）：603323.SH / 600908.SH / 000906.SZ / 601107.SH 库内各有 3 根
    2024-09-11~13 的日线，早于两年窗口起点。本页走 `stored_bars`（"库内最近 N 根"，不看
    窗口），单股页走 `ensure_bar_history`（窗口内根数）——同一只股票于是会在两个屏上印出
    「实测 8 笔 +1.61%」与「实测 7 笔 +2.10%」，四个价位一模一样而成交笔数对不上，用户看不
    出是 3 根 bar 造成的。本用例把那个形状原样种出来。

    `holidays=40` 是**前提，不是凑数**：只按工作日填得 523 根，超过 `stored_bars` 的 500 根
    上限，上限自己就把窗口外那几根挡掉了——于是裁不裁窗口跑出同一个数字，用例空转通过
    （改回 `_stored_bar_rows` 仍 `1 passed`，2026-09-17 验过）。A 股两年实际只有 ~486 个
    交易日，线上正是后者。下面 `seeded <= PLAN_DAYS_DEFAULT` 那句就是钉这个前提。
    """
    from app.api.strategy_routes import PLAN_DAYS_DEFAULT

    _only_one_candidate(monkeypatch, "600519.SH")
    seeded, in_window = _seed_bars("600519.SH", start_price=120.0, pre_window=3, holidays=40)
    assert seeded == in_window + 3, "前提一：窗口外那 3 根真的种进去了"
    assert seeded <= PLAN_DAYS_DEFAULT, (
        f"前提二：总量({seeded})必须不超上限({PLAN_DAYS_DEFAULT})，"
        "否则窗口外那几根会被 stored_bars 的上限挡掉、本用例空转"
    )

    body = client.get("/api/v1/strategy/simple").json()
    row = next(item for item in _flat_rows(body) if item["ts_code"] == "600519.SH")

    # 库内 `seeded` 根全都能被 stored_bars 看见，本页只许用其中的 `in_window` 根
    assert row["bars"] == in_window, "窗口外那几根被算进了实测样本（单股页不算它们）"


def test_the_board_ignores_a_strategy_query_parameter(client, monkeypatch) -> None:
    """没有 strategy 参数是**结构性**的护栏：传 `buy_hold` 也拿不到一整页「买入」。"""
    _force_all_candidates(monkeypatch)
    body = client.get("/api/v1/strategy/simple?strategy=buy_hold").json()
    assert body["strategy"] == "fusion"
    assert body["strategy_label"] != "买入持有（基准）"


# ======================================================================================
# 5：只有一份选股实现
# ======================================================================================
def test_the_selection_runs_the_timing_engine_exactly_once(client, monkeypatch) -> None:
    """`compute_timing_for_codes` 是批量取数，逐个调会把 DB 往返放大成候选池大小倍。

    这也是"选股只有一份实现"的哨兵：谁在端点里内联第二遍选股、或把 helper 调两次，计数就变。
    """
    import app.services.picks as picks_mod
    from app.db.models import ScoreSnapshot
    from sqlalchemy import select

    calls: list[list[str]] = []
    monkeypatch.setattr(
        picks_mod,
        "compute_timing_for_codes",
        lambda db, codes: (
            calls.append(list(codes))
            or {
                code: {"label": "可分批买入", "tone": "buy", "detail": "测试桩"} for code in codes
            }
        ),
    )
    # 评分门槛走真实路径：先让演示股达标，否则候选池为空、timing 压根不会被调用
    with _session() as session:
        for score in session.scalars(select(ScoreSnapshot)).all():
            score.total_score = 80.0
            score.coverage = 0.8
        session.commit()

    assert client.get("/api/v1/picks/daily").status_code == 200
    assert len(calls) == 1

    assert client.get("/api/v1/strategy/simple").status_code == 200
    assert len(calls) == 2  # 又恰好一次，不是零次也不是两次


# ======================================================================================
# 6：口径的诚实层
# ======================================================================================
def test_the_board_carries_the_caveats_that_must_not_be_footnotes(client, monkeypatch) -> None:
    _force_all_candidates(monkeypatch)
    body = client.get("/api/v1/strategy/simple").json()

    # 未复权/满仓单标的这类口径由后端下发（唯一副本），页面只是原样渲染
    assert body["caveat"]
    assert _flat_rows(body)[0]["honesty"]["caveat"] == body["caveat"]
    # 候选口径与选股实现同源（文案跟着阈值走，不是前端字面量）
    assert "每板块" in body["note"]
    assert f"{BOARD_PER_INDUSTRY} 只" in body["note"]
    assert "不设总数上限" in body["note"]
    # 「龙头」的定义边界必须写在文案里：排序只在候选内做，真·板块龙头多数没进名单
    assert "不是该板块市值最大的股票" in body["note"]
    # 缺失市值排末位的口径也要说
    assert "缺失市值的排在板块末位" in body["note"]

    # **反向断言**：短名单那段文案（低风险优先 / 单行业 2 只 / 最多 8 只）在这页一条都不成立。
    # 复用 `picks_note()` 会让页面印出一句用户看一眼就能推翻的假话——这条是那个错误的钉子。
    assert "低风险优先" not in body["note"]
    assert f"单行业至多 {PICKS_PER_INDUSTRY} 只" not in body["note"]


@pytest.mark.parametrize("code", ["999999.SZ"])
def test_an_unknown_code_never_reaches_the_board(client, monkeypatch, code) -> None:
    """候选集里不会凭空多出不是 Stock 的代码（`stock_query` 从 stocks 出发）。"""
    _force_all_candidates(monkeypatch)
    body = client.get("/api/v1/strategy/simple").json()
    assert code not in {row["ts_code"] for row in _flat_rows(body)}


# ======================================================================================
# 7：分板块 + 板块内龙头优先
# ======================================================================================
def _seed_sector(industry: str, *, count: int, first_code: int, top_mv: float = 12_000.0):
    """在同一个板块里造 `count` 只候选，市值从 `top_mv` 起每只递减 100 亿（可预测的降序）。

    返回 `[代码…]`，顺序就是市值降序——"板块内应当是这个顺序"的期望值直接用它，不手写。
    """
    codes = [f"{first_code + i:06d}.SZ" for i in range(count)]
    for i, code in enumerate(codes):
        _seed_candidate(code, industry=industry, total_mv=top_mv - i * 100.0)
    return codes


def test_the_board_groups_by_industry_and_caps_each_sector(client, monkeypatch) -> None:
    """分组 + 每板块上限 10 只。上限是**每板块**的，不是全表的。"""
    import statistics

    _force_all_candidates(monkeypatch)
    codes = _seed_sector("测试A", count=12, first_code=900001)

    body = client.get("/api/v1/strategy/simple").json()
    sectors = body["sectors"]

    assert [s["industry"] for s in sectors].count("测试A") == 1, "同板块不许裂成两块"
    a = _sector(body, "测试A")
    assert a["candidates"] == 12, "门槛后是这个数（截断之前）"
    assert a["selected"] == BOARD_PER_INDUSTRY
    assert len(a["rows"]) == BOARD_PER_INDUSTRY
    # 入选的正是市值前 10（即 `codes[:10]`），第 11、12 只被市值截掉
    assert [row["ts_code"] for row in a["rows"]] == codes[:BOARD_PER_INDUSTRY]
    assert not ({codes[10], codes[11]} & {row["ts_code"] for row in _flat_rows(body)})

    for sector in sectors:
        assert len(sector["rows"]) <= BOARD_PER_INDUSTRY

    # **撞满上限之后必须继续往后走**：`_diversify` 里超配额是 `continue` 而不是 `break`。
    # 测试A 的市值全库最高、又在第 11 只上撞满上限，所以这条不是空转——写成 `break` 的话
    # 整页只剩测试A 一个板块，而上面每一条断言照样通过（2026-09-18 变异验证过）。
    assert len(sectors) > 1, "第一个板块撞满上限后，后面的板块被整片丢掉了"
    assert {"白酒", "电池", "银行"} <= {s["industry"] for s in sectors}

    # 中位数取**所有板块**的行，不是某一个板块的统计量：这里 8 只演示股 90 根 + 12 只新造的
    # 25~90 根混在一起，只取"第一个板块"会得到另一个数
    shown = _flat_rows(body)
    assert body["bars_median"] == int(statistics.median([row["bars"] for row in shown]))


def test_rows_within_a_sector_are_ordered_by_market_cap_descending(client, monkeypatch) -> None:
    """板块内的顺序 = 总市值降序，所以板块第一行就是候选里的龙头。

    用演示股的「白酒」（贵州茅台 / 五粮液）——两只都是真候选。市值**显式改写**而不是沿用
    seed 的 `1800 + index*850`：那样数字一变（或 mock 顺序一变）这条就会以一种看着像
    "排序坏了"的方式红掉。
    """
    _force_all_candidates(monkeypatch)
    _set_market_cap("600519.SH", 9_000.0)  # 贵州茅台
    _set_market_cap("000858.SZ", 1_000.0)  # 五粮液

    body = client.get("/api/v1/strategy/simple").json()
    baijiu = _sector(body, "白酒")
    # 前提：两只都在表里（没有谁被样本不足挡掉），否则下面的顺序断言是空转
    assert baijiu["candidates"] == baijiu["selected"] == len(baijiu["rows"]) == 2
    assert [row["ts_code"] for row in baijiu["rows"]] == ["600519.SH", "000858.SZ"]

    # 反过来把市值对调，顺序必须跟着翻——否则这条测的就不是市值了
    _set_market_cap("600519.SH", 1_000.0)
    _set_market_cap("000858.SZ", 9_000.0)
    flipped = _sector(client.get("/api/v1/strategy/simple").json(), "白酒")
    assert [row["ts_code"] for row in flipped["rows"]] == ["000858.SZ", "600519.SH"]


def test_market_cap_changes_the_order_but_never_the_conclusion(client, monkeypatch) -> None:
    """市值**只**是排序键：把它改成板块最大或最小，四个数字与结论一个字节都不许动。

    这是「市值不放行」那条契约的守卫。市值一旦参与 `entry_conditions` 或任何阈值，这里就会红
    ——而那种改动在页面上看起来只是一个绿色的 ✓（见 `decision._decide_action` 的 docstring）。

    同时把 `/picks/daily` 的 payload 也前后比对：它的排序键是风险+评分，`total_mv` 压根不该
    出现在那条路径上。两次相等 = `_market_cap_key` 没有漏进短名单。
    """
    _force_all_candidates(monkeypatch)
    # 先摆一个确定的初始态：茅台(600519) 是白酒板块里**小**的那只
    _set_market_cap("600519.SH", 1_000.0)
    _set_market_cap("000858.SZ", 5_000.0)

    before = client.get("/api/v1/strategy/simple").json()
    daily_before = client.get("/api/v1/picks/daily").json()

    # 前提一：没有哪个板块顶到上限。否则改市值会改变"谁被截掉"，名单本身就变了，
    # 下面"逐只比对同一批标的"就无从谈起。
    assert all(len(s["rows"]) < BOARD_PER_INDUSTRY for s in before["sectors"]), (
        "演示库不该有板块顶到上限；若这条红了，先改的是本用例而不是实现"
    )
    # 前提二：白酒那两只都在（没被样本不足挡掉），否则顺序断言空转
    assert [row["ts_code"] for row in _sector(before, "白酒")["rows"]] == [
        "000858.SZ",
        "600519.SH",
    ]

    _set_market_cap("600519.SH", 9_999_999.0)  # 翻转：茅台变成板块内市值最大的
    after = client.get("/api/v1/strategy/simple").json()

    assert [row["ts_code"] for row in _sector(after, "白酒")["rows"]] == [
        "600519.SH",
        "000858.SZ",
    ], "市值改了顺序却没跟着翻——那说明排序键根本不是市值"
    assert {row["ts_code"] for row in _flat_rows(before)} == {
        row["ts_code"] for row in _flat_rows(after)
    }

    old = {row["ts_code"]: row for row in _flat_rows(before)}
    new = {row["ts_code"]: row for row in _flat_rows(after)}
    assert old.keys() == new.keys()
    for code, row in old.items():
        assert row == new[code], f"{code} 的结论随市值变了——市值不该影响任何数字"

    assert client.get("/api/v1/picks/daily").json() == daily_before


def test_a_missing_market_cap_sorts_last_without_dropping_the_row(client, monkeypatch) -> None:
    """`total_mv` 缺失的行排在板块末位，且**不因为缺失被丢掉**。

    丢掉它会静默缩小候选集（响应里的 `candidates` 跟着变小），而那正是 `picks.py` 的模块
    docstring 要防的那类改动。排末位则只是"我们没有依据说它最大"。

    三只候选分别是「大市值 / 市值 0 / 市值缺失」，**必须三只**：只比"5000 vs 缺失"是空转
    ——`-(None or 0.0)` 本来就等于 `-0.0`，把缺失排到最前需要市值是负数才可能，所以那种形状
    下 `_market_cap_key` 的第一个键写不写都通过（2026-09-18 变异验证：去掉 `value is None`
    整个套件全绿）。真正的分界是「**已知**很小」与「未知」：市值 0 是已知事实，得排在未知前面，
    这条只有第三只股票能区分出来。
    """
    _force_all_candidates(monkeypatch)
    _seed_candidate("900201.SZ", industry="测试B", total_mv=None)  # 未知
    _seed_candidate("900202.SZ", industry="测试B", total_mv=5_000.0)  # 已知，大
    _seed_candidate("900203.SZ", industry="测试B", total_mv=0.0)  # 已知，极小

    body = client.get("/api/v1/strategy/simple").json()
    b = _sector(body, "测试B")
    assert b["candidates"] == 3, "缺市值的那只必须仍在候选里"
    assert [row["ts_code"] for row in b["rows"]] == [
        "900202.SZ",  # 5000
        "900203.SZ",  # 0 ——已知很小，仍在"未知"之前
        "900201.SZ",  # 未知，末位
    ]


def test_a_sector_over_the_cap_says_so_and_a_skip_is_not_a_truncation(client, monkeypatch) -> None:
    """`candidates / selected / len(rows)` 是三个数，两个差值说的是两件不同的事。

    这里把两件事**同时**造出来：12 只候选（超上限 2 只 = 我们按市值截断了），其中市值最高的
    两只只有 5 根日线（进 `skipped` = 数据没拉到）。若把 schema 简化回 `{total, rows}`，
    页面上就没法把"我们挑了前 10"和"有 2 只样本不够"分开说，这条会红。
    """
    _force_all_candidates(monkeypatch)
    codes = _seed_sector("测试C", count=12, first_code=900301)
    for code in codes[:2]:  # 市值最高的两只样本太短
        _seed_bars(code, 5, industry="测试C")

    body = client.get("/api/v1/strategy/simple").json()
    c = _sector(body, "测试C")
    assert c["candidates"] == 12
    assert c["selected"] == BOARD_PER_INDUSTRY, "它们**入选**了，只是没进表"
    assert len(c["rows"]) == 8
    assert {item["ts_code"] for item in body["skipped"]} >= {codes[0], codes[1]}
    # 三个数字必须能推出两句不同的话——这是页面上那两句文案的数据依据
    assert c["selected"] < c["candidates"], "「还有更多同板块候选」"
    assert len(c["rows"]) < c["selected"], "「有候选因库内日线不足没进表」"


def test_sectors_are_ordered_by_row_count_then_name(client, monkeypatch) -> None:
    """板块顺序：已列行数降序，并列按板块名。第一屏放今天候选最厚的板块。"""
    _force_all_candidates(monkeypatch)
    _seed_sector("测试A", count=4, first_code=900401)

    sectors = client.get("/api/v1/strategy/simple").json()["sectors"]
    counts = [len(s["rows"]) for s in sectors]
    assert counts == sorted(counts, reverse=True)
    # 前提：确实存在并列，否则"并列按名字"这半条是空转
    assert len(set(counts)) < len(counts)
    for i in range(1, len(sectors)):
        if counts[i] == counts[i - 1]:
            assert sectors[i - 1]["industry"] < sectors[i]["industry"]


def test_the_daily_shortlist_keeps_scanning_past_a_full_industry(client, monkeypatch) -> None:
    """超配额是 `continue`，不是 `break`：某行业满了之后，**后面的行业仍然要被取到**。

    这条钉的是 `/picks/daily`（`target=8` 那一支）。把 `continue` 写成 `break` 会让清单静默
    变短，而「行数 ≤ 8」这类断言照样通过——`_diversify` 的 docstring 专门警告过这件事，但
    光有 docstring 拦不住：2026-09-18 实测把 `continue` 改成 `break`，**整个测试套件
    417 条全绿**。演示库没有"某行业满了、后面还有别的行业"的形状（白酒那 2 只正好用满上限、
    且是最后一批），所以必须专门造出来。

    造法：3 只同行业候选，风险「低」+ 分数 99，于是它们排在最前；第 3 只必然撞上"单行业至多
    2 只"。此时若停止扫描，清单就只剩这 2 只。
    """
    _force_all_candidates(monkeypatch)
    for code in ("900601.SZ", "900602.SZ", "900603.SZ"):
        _seed_candidate(
            code, industry="测试D", total_mv=100.0, total_score=99.0, risk_level="低"
        )

    picks = client.get("/api/v1/picks/daily").json()["picks"]
    codes = [item["ts_code"] for item in picks]

    assert sum(1 for code in codes if code.startswith("9006")) == PICKS_PER_INDUSTRY, (
        "单行业上限没生效（或者这 3 只没排在前面，那本用例就是空转）"
    )
    # **关键**：撞上上限之后仍取到了别的行业
    assert any(not code.startswith("9006") for code in codes), (
        "撞上单行业上限后整条扫描就停了——超配额必须是 continue"
    )
    assert len(codes) <= 8  # PICKS_TARGET


def test_every_row_carries_its_sectors_industry(client, monkeypatch) -> None:
    """行上的 `industry` 与它所在板块的 `industry` 必须是同一个值。

    分组键取自 `Stock.industry`（服务层），行上的取自 `Plan.industry`（逐字照抄）。
    两条来源在今天是同一个值，但**没有**任何别的断言把它们绑在一起——一旦有人给行换了
    来源，页面会出现"行挂在 A 板块、行内写着 B 行业"的错位。
    """
    _force_all_candidates(monkeypatch)
    _seed_sector("测试A", count=3, first_code=900501)

    body = client.get("/api/v1/strategy/simple").json()
    assert body["sectors"]
    for sector in body["sectors"]:
        for row in sector["rows"]:
            assert row["industry"] == sector["industry"]
