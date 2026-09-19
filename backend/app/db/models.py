from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Stock(Base):
    __tablename__ = "stocks"
    ts_code: Mapped[str] = mapped_column(String(12), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(40), index=True)
    industry: Mapped[str] = mapped_column(String(40), default="未知")
    market: Mapped[str] = mapped_column(String(20), default="主板")
    list_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TradeCalendar(Base):
    __tablename__ = "trade_calendar"
    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False)


class DailyBar(Base):
    __tablename__ = "daily_bars"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    pre_close: Mapped[float] = mapped_column(Float)
    pct_chg: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="tushare")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class LatestQuote(Base):
    __tablename__ = "latest_quotes"
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), primary_key=True)
    price: Mapped[float] = mapped_column(Float)
    pct_chg: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    quote_time: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(20))
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class FundamentalSnapshot(Base):
    __tablename__ = "fundamental_snapshots"
    __table_args__ = (UniqueConstraint("ts_code", "trade_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    pe_ttm: Mapped[Optional[float]] = mapped_column(Float)
    pb: Mapped[Optional[float]] = mapped_column(Float)
    total_mv: Mapped[Optional[float]] = mapped_column(Float)
    turnover_rate: Mapped[Optional[float]] = mapped_column(Float)
    roe: Mapped[Optional[float]] = mapped_column(Float)
    revenue_growth: Mapped[Optional[float]] = mapped_column(Float)
    profit_growth: Mapped[Optional[float]] = mapped_column(Float)
    debt_ratio: Mapped[Optional[float]] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), primary_key=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    stock: Mapped[Stock] = relationship()


class NewsArticle(Base):
    __tablename__ = "news_articles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    source_name: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    published_at: Mapped[datetime] = mapped_column(DateTime)
    summary: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[Optional[float]] = mapped_column(Float)
    event_tag: Mapped[str] = mapped_column(String(40), default="一般资讯")
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(40), default="v0")
    analysis_mode: Mapped[str] = mapped_column(String(20), default="raw")


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), primary_key=True)
    total_score: Mapped[Optional[float]] = mapped_column(Float)
    technical_score: Mapped[Optional[float]] = mapped_column(Float)
    fundamental_score: Mapped[Optional[float]] = mapped_column(Float)
    sentiment_score: Mapped[Optional[float]] = mapped_column(Float)
    risk_score: Mapped[Optional[float]] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float)
    risk_level: Mapped[str] = mapped_column(String(20))
    explanations: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    rule_version: Mapped[str] = mapped_column(String(20), default="v1")


class FactorSnapshot(Base):
    """单只股票的原始因子值与分位分（由 recalculate_scores 落库，量化页零成本读取）。"""

    __tablename__ = "factor_snapshots"
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), primary_key=True)
    trade_date: Mapped[Optional[date]] = mapped_column(Date)
    factors: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    composite: Mapped[Optional[float]] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    rule_version: Mapped[str] = mapped_column(String(20), default="v2")


class StrategyPoolStats(Base):
    """一条规则在**全市场**上的实测汇总（11 个策略各一行，由 quant.pool 落库）。

    存在的理由：2 年日线是**按需回补**的，库里只有 117 只有 250 根以上，于是任何单只
    股票都凑不到 `INSUFFICIENT_TRADES`(20) 笔——个股那一栏永远是"样本不足"。跨股票池化
    是唯一能把样本量抬上一个量级的办法，本表就是它的物化结果（页面零成本读取）。

    **物化而非实时算**：全市场跑一遍实测约 4 分钟，绝不能挂在请求里。

    与 `FactorSnapshot` 一样带 `rule_version`：改了 `signals` 的阈值之后，本表若带着一个
    崭新的时间戳继续用旧引擎的数字去否决买入，那就比没有这张表更糟。
    """

    __tablename__ = "strategy_pool_stats"
    strategy: Mapped[str] = mapped_column(String(40), primary_key=True)
    rule_version: Mapped[str] = mapped_column(String(20), default="v2")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    # 产生这组数字的口径：止损/移动止盈倍数与最少根数门槛。不存的话，"同一份数据两次
    # 算出不同结果"将无从解释。
    stop_loss_atr: Mapped[Optional[float]] = mapped_column(Float)
    trail_atr: Mapped[Optional[float]] = mapped_column(Float)
    min_bars: Mapped[int] = mapped_column(Integer, default=0)

    # —— 宇宙：分母必须和结论一起显示，否则"20 万笔"会被读成"20 万个独立观测" ——
    universe_total: Mapped[int] = mapped_column(Integer, default=0)  # 库内股票总数
    universe_used: Mapped[int] = mapped_column(Integer, default=0)  # 达到 min_bars 的只数
    stocks_with_trades: Mapped[int] = mapped_column(Integer, default=0)  # 真出了成交的只数
    bars_total: Mapped[int] = mapped_column(Integer, default=0)
    bars_median: Mapped[int] = mapped_column(Integer, default=0)  # 决定"窗口有多长"的是它
    window_from: Mapped[Optional[date]] = mapped_column(Date)  # 各股首个交易日的中位数
    window_to: Mapped[Optional[date]] = mapped_column(Date)  # 各股末个交易日的中位数

    # —— 聚合（与 schemas.ExpectancyBlock 同名字段同单位，便于并排读） ——
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[Optional[float]] = mapped_column(Float)
    avg_win_pct: Mapped[Optional[float]] = mapped_column(Float)
    avg_loss_pct: Mapped[Optional[float]] = mapped_column(Float)
    profit_factor: Mapped[Optional[float]] = mapped_column(Float)
    expectancy_pct: Mapped[Optional[float]] = mapped_column(Float)
    expectancy_per_bar_pct: Mapped[Optional[float]] = mapped_column(Float)
    avg_hold_bars: Mapped[Optional[float]] = mapped_column(Float)
    forced_end_trades: Mapped[int] = mapped_column(Integer, default=0)
    forced_end_share: Mapped[Optional[float]] = mapped_column(Float)

    # —— 基准与"反池化"度量：单独看池化均值会撒谎 ——
    # 实测结论：三个策略的池化均值为正，而**每个策略的股票中位数都是负的**（右偏分布，
    # 少数股票的盈利把均值抬到中位数之上）。只看均值就是在骗人，故中位数与正股占比
    # 必须与均值同等显眼。
    benchmark_expectancy_pct: Mapped[Optional[float]] = mapped_column(Float)
    benchmark_expectancy_per_bar_pct: Mapped[Optional[float]] = mapped_column(Float)
    excess_per_bar_pct: Mapped[Optional[float]] = mapped_column(Float)
    median_stock_expectancy_pct: Mapped[Optional[float]] = mapped_column(Float)
    positive_stock_share: Mapped[Optional[float]] = mapped_column(Float)
    positive_stock_min_trades: Mapped[int] = mapped_column(Integer, default=0)  # 上面两项的每股门槛
    stock_denominator: Mapped[int] = mapped_column(Integer, default=0)  # 上面两项的分母

    verdict: Mapped[str] = mapped_column(String(20), default="insufficient")


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    message: Mapped[str] = mapped_column(String(300), default="")
    items_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_class: Mapped[str] = mapped_column(String(20), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class ProviderStatus(Base):
    __tablename__ = "provider_status"
    capability: Mapped[str] = mapped_column(String(40), primary_key=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    message: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PositionTrade(Base):
    """一笔真实的买卖流水（用户手工录入，本页的持仓与建议由它实时回放得出）。

    刻意是**流水账**而不是"一条持仓记录"：同一只股票要能分多次买、多次卖，否则算不出
    加权平均成本，也算不出已实现盈亏。`decision.summarize_ledger` 按日期顺序回放这张表。

    **不存任何派生值**（剩余股数/加权成本/浮盈/距止损/得分一律实时重算）。这是刻意的：
    库里存算好的持仓，就得在每次流水增删改时同步更新它，而漏更新一处就会让页面显示一个
    陈旧但看起来很确定的数字——比慢一点糟得多。

    与 `WatchlistItem` 同样的姿态：单用户、无 `user_id`、无归属校验，不暴露到 localhost 之外。
    """

    __tablename__ = "position_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_code: Mapped[str] = mapped_column(ForeignKey("stocks.ts_code"), index=True)
    side: Mapped[str] = mapped_column(String(4), index=True)  # "buy" | "sell"
    trade_date: Mapped[date] = mapped_column(Date)
    price: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    # 买入时记录的止损位。用户改过它意味着屏上的实测期望不再描述他自己的风险——
    # `stop_source: user|rule` 让这个偏离可见，见 decision.advise_position。
    stop_price: Mapped[Optional[float]] = mapped_column(Float)
    strategy: Mapped[str] = mapped_column(String(20), default="fusion")
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    stock: Mapped[Stock] = relationship()


class NotifyEvent(Base):
    """一条微信推送（企业微信群机器人）的账本。存在即已**尝试过**。

    `dedup_key` 非空 + UNIQUE 就是**幂等机制本身**：先插行、抢到唯一键才发送，撞键即已推过。
    刻意保持非空——SQLite 的 UNIQUE 允许任意多个 NULL，可空就等于静默关掉全部幂等。

    `content` 存发出的原文而不是只存摘要：这个表的用处是事后能读回"我们到底告诉过你什么"，
    这跟 `SyncRun` 是同一种姿态（派生值不落库，但发生过的事要留痕）。一天几 KB，忽略不计。

    `status` 的四个值各有含义，别互相顶替：`pending` = 插了行但进程在发送前中断（**没送达**，
    页面上不许读成已送达）、`sent` = 企业微信回了 errcode 0、`failed` = 出网失败或 errcode
    非 0、`skipped` = webhook 没配（不是失败，是没开）。

    **不给已存在的表加列**：`init_db()` 是 `create_all`，只建新表、从不 ALTER。新状态只能
    进新表，否则线上库（有真实数据）会运行时炸，而测试库每次新建所以全绿。
    """

    __tablename__ = "notify_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # daily | stop | sync | sync_ok | sync_off | test
    kind: Mapped[str] = mapped_column(String(16), index=True)
    dedup_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    # 企业微信的 errcode。**HTTP 200 也可能是失败**（93000=webhook 失效、45009=超频），
    # 没有这一列就无法把"密钥已失效"与"已送达"分开。
    errcode: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[str] = mapped_column(String(300), default="")
    byte_len: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

