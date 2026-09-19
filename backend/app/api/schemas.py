from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class TimingAdvice(BaseModel):
    label: str
    tone: Literal["buy", "hold", "reduce", "watch"]
    detail: str


class WatchlistCreate(BaseModel):
    ts_code: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    note: str = Field(default="", max_length=200)


class SyncRequest(BaseModel):
    job_type: str = Field(
        default="quotes",
        pattern=r"^(quotes|scores|market|news|bootstrap|stocks|calendar|fundamentals)$",
    )


class ApiMessage(BaseModel):
    message: str


class SyncJobView(BaseModel):
    """一个后台作业的即时状态。与 `GET /sync/jobs/{id}` 的字段同名同义——前端拿它轮询。

    单独建模型是为了让「触发」的响应里带上 `id`：把任务号塞进 `message` 再由前端用正则
    抠出来，是这个项目明确不要的那种脆弱耦合（message 是给人读的，随时会被改文案）。
    """

    id: int
    job_type: str
    status: str
    message: str


class SystemStatus(BaseModel):
    provider: str
    tushare_configured: bool
    llm_configured: bool
    mock_mode: bool
    quote_refresh_seconds: int
    updated_at: datetime
    data_mode: str = "unconfigured"
    capabilities: list[str] = Field(default_factory=list)
    provider_status_at: Optional[datetime] = None
    # 照抄 `tushare_configured` 的做法：**只暴露"配没配"，永不回传 URL 本身**
    # （webhook 地址是凭据，拿到它的人能往那个群里发消息）。
    notify_configured: bool = False


class NotifyEventView(BaseModel):
    """推送账本的一行。`content` 是**发出的原文**——账本的用处就是事后能读回
    "我们到底告诉过你什么"，只给摘要等于把这条信息丢掉。"""

    id: int
    kind: str
    status: str
    # 人话版本由后端给（`notify.STATUS_LABELS`）：`pending` 不是"已送达"，
    # 这个映射不能在前端各写一份。
    status_label: str
    http_status: Optional[int] = None
    errcode: Optional[int] = None
    error: str = ""
    byte_len: int
    summary: str
    content: str
    created_at: datetime


class NotifyTestResult(NotifyEventView):
    """测试推送的结果 = 账本那一行 + 两句人话。

    `reused` 单独标出来，是因为 `push` 的幂等键在**同一分钟内**会撞上：
    `reused=True` 表示这一分钟已经发过一条，本次**没有真的再发**——不标出来，
    界面上一次"已送达"就会看起来像刚刚那次成功了。
    """

    reused: bool = False
    detail: str = ""


class StockView(BaseModel):
    ts_code: str
    symbol: str
    name: str
    industry: str
    market: str
    price: Optional[float] = None
    pct_chg: Optional[float] = None
    total_score: Optional[float] = None
    technical_score: Optional[float] = None
    fundamental_score: Optional[float] = None
    sentiment_score: Optional[float] = None
    coverage: Optional[float] = None
    risk_level: Optional[str] = None
    is_stale: bool = False
    quote_time: Optional[datetime] = None
    # 价格来源：quote=实时报价；close=最近收盘价兜底（盘后无报价时）；
    # none=既无报价也无日线（停牌/未同步），此时 price 为 null。
    price_source: str = "quote"
    # price_source == "close" 时的那个收盘日（ISO），用于页面标注「收盘 09-16」。
    price_date: Optional[str] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    explanations: list[dict[str, Any]] = Field(default_factory=list)
    timing: Optional[TimingAdvice] = None


class TopBoardRow(StockView):
    """综合评分 TOP-N 榜单行：StockView 全集 + 榜序 + 近 3 天新闻简报。

    继承而非改 StockView，避免把榜单专属字段漏进 /screener、/watchlist 契约。
    新闻口径与 scoring.recalculate_scores 的 news 维度一致（naive 3 天窗口）：
    news_sentiment_avg 为近 3 天有情绪(sentiment 非空)文章均值(-1..1)，raw 恒为空被排除；
    最新一条简报仅看 llm/demo（raw 的 event_tag 恒为“一般资讯”，无信号价值）。
    """

    rank: int = 0
    news_sentiment_avg: Optional[float] = None
    news_3d_count: int = 0
    news_tag: Optional[str] = None
    news_sentiment: Optional[float] = None
    news_title: Optional[str] = None
    news_published_at: Optional[datetime] = None


class DailyPicks(BaseModel):
    """次日买点候选：低风险优先、按行业分散的实时计算短名单。

    复用 StockView（自带 timing，每行 tone=buy，detail 即买点参考）；basis_date 为最近
    一根日线 trade_date（盘中/盘后均指最近收盘日），note 说明入选口径。
    """

    basis_date: Optional[date] = None
    note: str = ""
    picks: list[StockView] = Field(default_factory=list)


class QuoteView(BaseModel):
    ts_code: str
    name: str
    price: Optional[float] = None
    pct_chg: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None
    quote_time: Optional[datetime] = None
    is_stale: bool = True
    source: str = ""


class NewsView(BaseModel):
    id: int
    title: str
    source: str
    published_at: datetime
    summary: str
    sentiment: Optional[float] = None
    event_tag: str
    confidence: Optional[float] = None
    model_version: str = "v0"
    analysis_mode: str = "raw"


class ScoreExplanation(BaseModel):
    ts_code: str
    name: str
    total_score: Optional[float] = None
    technical_score: Optional[float] = None
    fundamental_score: Optional[float] = None
    sentiment_score: Optional[float] = None
    risk_score: Optional[float] = None
    coverage: float = 0
    risk_level: str = ""
    explanations: list[dict[str, Any]] = Field(default_factory=list)
    calculated_at: Optional[datetime] = None
    rule_version: str = ""
    quote_time: Optional[datetime] = None
    is_stale: bool = True


# —— 量化分析（/quant/*）——————————————————————————————————————————————————————


class QuantIndicatorPoint(BaseModel):
    """指标图上的一根 bar：收盘 + 全部叠加/副图指标，预热期为 None。"""

    trade_date: date
    close: float
    volume: Optional[float] = None
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    boll_up: Optional[float] = None
    boll_mid: Optional[float] = None
    boll_low: Optional[float] = None
    dif: Optional[float] = None
    dea: Optional[float] = None
    macd_hist: Optional[float] = None
    kdj_k: Optional[float] = None
    kdj_d: Optional[float] = None
    kdj_j: Optional[float] = None
    rsi: Optional[float] = None
    atr14: Optional[float] = None


class QuantSignal(BaseModel):
    name: str
    label: str
    direction: Literal["buy", "sell", "neutral"]
    detail: str
    strength: float = 0.0
    level: Optional[float] = None


class QuantSeries(BaseModel):
    ts_code: str
    name: str
    industry: str = ""
    window_days: int
    bars: int
    indicators: list[QuantIndicatorPoint] = Field(default_factory=list)
    signals: list[QuantSignal] = Field(default_factory=list)
    signal_score: Optional[float] = None
    latest: dict[str, Any] = Field(default_factory=dict)


class QuantPerformance(BaseModel):
    ts_code: str
    name: str
    window_days: int
    benchmark: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    equity: list[dict[str, Any]] = Field(default_factory=list)


class QuantTrade(BaseModel):
    entry_date: date
    entry_price: float
    shares: int
    exit_date: date
    exit_price: float
    pnl: float
    pnl_pct: float
    hold_bars: int
    exit_reason: str


class QuantBacktest(BaseModel):
    ts_code: str
    name: str
    strategy: str
    strategies: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    equity: list[dict[str, Any]] = Field(default_factory=list)
    trades: list[QuantTrade] = Field(default_factory=list)


class QuantFactorDefinition(BaseModel):
    name: str
    label: str
    group: str
    weight: float
    direction: int


class QuantFactorRow(BaseModel):
    ts_code: str
    name: str
    industry: str = ""
    composite: Optional[float] = None
    coverage: float = 0
    factors: dict[str, Any] = Field(default_factory=dict)
    trade_date: Optional[date] = None
    calculated_at: Optional[datetime] = None


class QuantFactors(BaseModel):
    definitions: list[QuantFactorDefinition] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    group_weights: dict[str, float] = Field(default_factory=dict)
    rule_version: str = ""
    rows: list[QuantFactorRow] = Field(default_factory=list)


class QuantScanRow(BaseModel):
    ts_code: str
    name: str
    industry: str = ""
    total_score: Optional[float] = None
    price: Optional[float] = None
    signal: str
    label: str
    direction: Literal["buy", "sell", "neutral"]
    detail: str
    strength: float = 0.0
    level: Optional[float] = None


# ======================================================================================
# 买卖策略：计划 / 诚实层 / 持仓
# 字段与 `services/quant/decision.py` 的 dataclass 1:1，使 `BuySellPlan.model_validate(
# asdict(plan))` 就是三行适配。往返由 test_quant_strategy_schemas.py 钉住。
# ======================================================================================
class ExpectancyBlock(BaseModel):
    """一个策略在一只标的上的**实测**记录——UI 的每个结论都必须挂着它。"""

    strategy: str
    strategy_label: str
    window_days: int
    bars: int
    trade_count: int
    win_rate: Optional[float] = None  # 按**笔**（metrics.win_rate 是按日，同名不同义）
    avg_win_pct: Optional[float] = None
    avg_loss_pct: Optional[float] = None
    profit_factor: Optional[float] = None
    expectancy_pct: Optional[float] = None
    expectancy_per_bar_pct: Optional[float] = None
    avg_hold_bars: Optional[float] = None
    forced_end_trades: int = 0
    cumulative_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    benchmark_return: Optional[float] = None
    excess_return: Optional[float] = None
    verdict: Literal["positive", "negative", "insufficient"]
    verdict_text: str = ""
    caveat: str = ""


class ExpectancyRow(ExpectancyBlock):
    is_baseline: bool = False  # buy_hold 那一行——永远保留，否则排行榜会反向撒谎


class PoolStatsRow(BaseModel):
    """一条规则**跨股票池化**的实测汇总。**不是这只股票的预期**，两者不可互相顶替。

    刻意**不继承** `ExpectancyBlock`：`window_days`/`bars` 是单标的语义，
    `cumulative_return`/`max_drawdown`/`benchmark_return`/`excess_return` 在池化下没有意义
    （池化成交列表没有资金配置，算不出净值曲线），继承只会多出四列永远是 `--` 的字段。
    """

    strategy: str
    strategy_label: str
    is_baseline: bool = False
    trade_count: int = 0
    stocks_with_trades: int = 0
    win_rate: Optional[float] = None
    avg_win_pct: Optional[float] = None
    avg_loss_pct: Optional[float] = None
    profit_factor: Optional[float] = None
    expectancy_pct: Optional[float] = None
    expectancy_per_bar_pct: Optional[float] = None
    avg_hold_bars: Optional[float] = None
    forced_end_trades: int = 0
    forced_end_share: Optional[float] = None
    # 基准（同期等权买入持有）与相对它的每 bar 超额。没有基准就会反向撒谎：
    # 这段窗口里市场跌了约 12%，一条打平的规则其实在创造价值。
    benchmark_expectancy_pct: Optional[float] = None
    benchmark_expectancy_per_bar_pct: Optional[float] = None
    excess_per_bar_pct: Optional[float] = None
    # 「反池化」的两个度量：均值正而中位股负是实测常态（右偏），故必须与均值并排。
    median_stock_expectancy_pct: Optional[float] = None
    positive_stock_share: Optional[float] = None
    positive_stock_min_trades: int = 0
    stock_denominator: int = 0
    verdict: Literal["positive", "negative", "insufficient"] = "insufficient"


class PoolStats(BaseModel):
    """全市场实测汇总。从未计算过时 `computed_at=None` + `rows=[]`——**绝不返回 0 笔 / 0.00%**，
    那会被读成"算过了，结果是零"。"""

    computed_at: Optional[datetime] = None
    rule_version: str = ""
    universe_total: int = 0
    universe_used: int = 0
    bars_total: int = 0
    bars_median: int = 0
    window_from: Optional[date] = None
    window_to: Optional[date] = None
    min_bars: int = 0
    stop_loss_atr: Optional[float] = None
    trail_atr: Optional[float] = None
    note: str = ""
    caveat: str = ""
    rows: list[PoolStatsRow] = Field(default_factory=list)


class PlanCondition(BaseModel):
    key: str
    label: str
    satisfied: bool
    detail: str = ""


class PriceZone(BaseModel):
    low: float
    high: float
    reference: float
    source: str = ""
    note: str = ""


class StopPlan(BaseModel):
    atr_level: Optional[float] = None
    structure_level: Optional[float] = None
    recommended: float
    distance_pct: float
    source: str = ""
    note: str = ""


class TrailPlan(BaseModel):
    level: Optional[float] = None
    high_water: float
    note: str = ""


class PositionSizing(BaseModel):
    shares: int
    lots: int
    amount: float
    weight_pct: float
    risk_amount: float
    risk_pct: float
    affordable: bool
    formula: str = ""
    note: str = ""


class HoldEstimate(BaseModel):
    basis: Literal["measured", "heuristic"]
    bars: Optional[int] = None
    low: int
    high: int
    note: str = ""


class ExitRule(BaseModel):
    key: str
    label: str
    triggered: bool
    detail: str = ""


class ScorePoint(BaseModel):
    trade_date: date
    trend: float
    reversion: float
    fusion: float


class FamilyScoreView(BaseModel):
    name: str
    label: str
    score: float
    buy_threshold: float
    sell_threshold: float
    buy_rules: list[str] = Field(default_factory=list)
    sell_rules: list[str] = Field(default_factory=list)
    direction: Literal["buy", "sell", "hold"]


class FamilyScores(BaseModel):
    trend: FamilyScoreView
    reversion: FamilyScoreView
    fusion: FamilyScoreView


class BuySellPlan(BaseModel):
    """单股买卖计划。`action` 是**决策表**的输出，不是分数阈值的直接映射：

    `avoid` = 信号已触发但该规则在此标的实测期望为负（永远不许渲染成"买入"）；
    `watch` = 未满足入场条件，或样本不足以证明有效。见 `decision._decide_action`。
    """

    # extra="forbid"：本模型由 `asdict(decision.Plan)` 直通而来，禁止未知字段就等于把
    # 「内核加了字段但忘了镜像」变成 API 边界上的硬报错，而不是静默丢字段。见
    # tests/test_quant_strategy_schemas.py 的往返测试。
    model_config = ConfigDict(extra="forbid")

    ts_code: str
    name: str = ""
    industry: str = ""
    strategy: str
    strategy_label: str
    as_of: Optional[date] = None
    bars: int
    price: float
    price_source: str = "close"
    is_stale: bool = False
    action: Literal["buy", "watch", "avoid"]
    action_label: str
    reasons: list[str] = Field(default_factory=list)
    entry_conditions: list[PlanCondition] = Field(default_factory=list)
    entry_zone: PriceZone
    stop_loss: StopPlan
    take_profit: TrailPlan
    sizing: PositionSizing
    expected_hold: HoldEstimate
    exit_rules: list[ExitRule] = Field(default_factory=list)
    scores: FamilyScores
    score_history: list[ScorePoint] = Field(default_factory=list)
    honesty: ExpectancyBlock


class SimpleSkipped(BaseModel):
    """候选池里库内日线不足 `MIN_BARS` 的标的——**列出来，但不进表**。

    20 根时 ATR14/MA20 都无定义，`entry_zone` 会塌成一个点、止损退到 `现价 × 0.92` 兜底。
    把这种行渲染成候选表里的几个 `--`，读起来像「这只是没问题、只是缺数字」——那是另一种
    编造。所以它们进 `skipped` 并带上原因，由页面单独说明。
    """

    ts_code: str
    name: str = ""
    industry: str = ""
    bars: int
    reason: str


class SimplePlanRow(BaseModel):
    """「明日操作」页的一行：四个数字 + 一句结论。

    **四个价位字段全部非 Optional**——不是缺省值问题，是"进表就一定有数"这个不变式（见
    `SimpleSkipped`）。任何一个变成 `None` 都应该在写库/构行时炸掉，而不是在页面上显示 `--`。

    `action`/`action_label` 是 `Plan` 的**同一份**输出，逐字照抄，**不在这里重算**：`action`
    已综合了四条信号条件、实测期望正负与全市场否决，任何"从 verdict 推 action"的简化都会
    静默删掉其中几项。
    """

    ts_code: str
    name: str
    industry: str
    price: float
    price_source: str = "close"
    price_date: Optional[str] = None
    is_stale: bool = False
    bars: int  # 库内实际参与实测的根数，不是请求的 window_days
    action: Literal["buy", "watch", "avoid"]
    action_label: str
    entry_zone: PriceZone
    stop_loss: StopPlan
    take_profit: TrailPlan
    expected_hold: HoldEstimate
    honesty: ExpectancyBlock


class SimpleSector(BaseModel):
    """一个板块的候选行。三个数字必须能分开读：候选 → 入选 → 实际列出的行。

    **不设 `shown`**：它等于 `len(rows)`，多一个字段就多一处可以互相矛盾的地方。

    - `candidates`：该板块通过门槛与时机两步的候选数（截断**之前**的事实）。
    - `selected`：按市值截断后入选的只数 = `min(candidates, 每板块上限)`。
    - `len(rows)`：真正列出的行数，可能小于 `selected`——库内日线不足的候选进了 `skipped`。

    两个差值各有各的说法，页面必须分开说：`selected < candidates` 是「还有更多同板块候选没
    列出来」，`len(rows) < selected` 是「有候选因库内日线不足没进表」。混成一句就会把
    "数据没拉到"说成"我们挑过了"。
    """

    industry: str
    candidates: int
    selected: int
    rows: list[SimplePlanRow] = Field(default_factory=list)


class SimplePlanBoard(BaseModel):
    """「明日操作」页的整块数据。候选**池**与 `/picks/daily` 同一份实现（`services.picks`）。

    与 `/picks/daily` 唯一的差别是分组与取舍：本页按行业分组、每板块至多 10 只、板块内按
    总市值降序、不设总数上限；短名单是低风险优先、单行业 2 只、最多 8 只。候选池相同，
    名单不必互相包含。

    `bars_median` 是**必须**的：本页只用库内日线（绝不向上游回补），而库内中位只有约 96 根
    而不是 500 根——不印出来，「实测样本」这四个字就会被读成和单股页一样多。它取的是
    **所有板块**的行，不是某一个板块的统计量。
    """

    basis_date: Optional[date] = None
    strategy: str
    strategy_label: str
    window_days: int
    bars_median: int
    note: str = ""
    caveat: str = ""
    sectors: list[SimpleSector] = Field(default_factory=list)
    skipped: list[SimpleSkipped] = Field(default_factory=list)


class StrategyBacktest(QuantBacktest):
    """`/quant/backtest` 的响应 + 诚实层。`strategies` 里是 11 个（含 3 个融合策略）。"""

    expectancy: Optional[ExpectancyBlock] = None
    fused: bool = False


class PositionTradeRow(BaseModel):
    id: int
    ts_code: str
    name: str = ""
    side: Literal["buy", "sell"]
    trade_date: date
    price: float
    shares: float
    fee: float = 0.0
    stop_price: Optional[float] = None
    strategy: str = "fusion"
    note: str = ""
    created_at: Optional[datetime] = None


class PositionTradeCreate(BaseModel):
    ts_code: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    side: Literal["buy", "sell"]
    trade_date: date
    price: float = Field(gt=0)
    shares: float = Field(gt=0)
    fee: float = Field(default=0.0, ge=0)
    stop_price: Optional[float] = Field(default=None, gt=0)
    strategy: str = Field(default="fusion", max_length=20)
    note: str = Field(default="", max_length=200)


class PositionTradeUpdate(BaseModel):
    """局部更新：只有显式给出的字段会改（`model_dump(exclude_unset=True)`）。"""

    side: Optional[Literal["buy", "sell"]] = None
    trade_date: Optional[date] = None
    price: Optional[float] = Field(default=None, gt=0)
    shares: Optional[float] = Field(default=None, gt=0)
    fee: Optional[float] = Field(default=None, ge=0)
    stop_price: Optional[float] = Field(default=None, gt=0)
    strategy: Optional[str] = Field(default=None, max_length=20)
    note: Optional[str] = Field(default=None, max_length=200)


class PositionAdvice(BaseModel):
    """一只持仓标的的当前状态 + 建议。派生值全部实时重算，库里没有它们的副本。"""

    model_config = ConfigDict(extra="forbid")

    ts_code: str
    name: str = ""
    industry: str = ""
    status: Literal["holding", "closed"]
    shares: float
    avg_cost: float
    cost: float
    price: Optional[float] = None
    price_source: str = "close"
    is_stale: bool = False
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    realized_pnl: float = 0.0
    hold_bars: int = 0
    first_entry_date: Optional[date] = None
    trade_count: int = 0
    stop_price: Optional[float] = None
    stop_source: Literal["rule", "user", "none"] = "none"
    stop_distance_pct: Optional[float] = None
    stop_triggered: bool = False
    trail_level: Optional[float] = None
    trail_triggered: bool = False
    scores: Optional[FamilyScores] = None
    action: Literal["exit", "reduce", "hold", "add", "watch"]
    action_label: str
    reasons: list[str] = Field(default_factory=list)
    data_warning: str = ""
