export type Explanation = {
  factor: string
  value: string | number
  score: number
  reason: string
}

export type Timing = {
  label: string
  tone: 'buy' | 'hold' | 'reduce' | 'watch'
  detail: string
}

export type Stock = {
  ts_code: string
  symbol: string
  name: string
  industry: string
  market: string
  price: number | null
  pct_chg: number | null
  total_score: number | null
  technical_score: number | null
  fundamental_score: number | null
  sentiment_score: number | null
  coverage: number | null
  risk_level: string | null
  is_stale: boolean
  quote_time: string | null
  /** `quote`=实时报价；`close`=最近收盘价兜底；`none`=既无报价也无日线（停牌/未同步）。 */
  price_source: 'quote' | 'close' | 'none'
  /** 仅 `price_source === 'close'` 时有值：那根收盘日线是哪天。 */
  price_date: string | null
  pe_ttm: number | null
  pb: number | null
  explanations: Explanation[]
  timing?: Timing | null
}

/** /screener/top 榜单行：Stock 全集 + 榜序 + 近 3 天新闻简报。 */
export type TopBoardRow = Stock & {
  rank: number
  news_sentiment_avg: number | null
  news_3d_count: number
  news_tag: string | null
  news_sentiment: number | null
  news_title: string | null
  news_published_at: string | null
}

/** /picks/daily 次日买点候选：低风险优先、按行业分散的短名单。 */
export type DailyPicks = {
  basis_date: string | null // 最近收盘日（实时数据基准日）
  note: string
  picks: Stock[] // 每只 pick 复用 Stock，timing 已填充且 tone=buy
}

export type SparkPoint = { date: string; close: number }

export type MarketOverview = {
  indices: Array<{ code: string; name: string; price: number; pct_chg: number; spark?: SparkPoint[] }>
  breadth: {
    rising: number
    falling: number
    flat?: number
    average_pct: number
    as_of?: string
    samples?: number
  }
  as_of: string
  source: string
}

export type Status = {
  provider: string
  tushare_configured: boolean
  llm_configured: boolean
  mock_mode: boolean
  quote_refresh_seconds: number
  updated_at: string
  data_mode?: string
  capabilities?: string[]
  provider_status_at?: string | null
  /** 只说明微信推送**配没配**：webhook 地址是凭据，后端永不回传。 */
  notify_configured: boolean
}

/** 推送账本的一行。`content` 是**发出的原文**，不是摘要。 */
export type NotifyEvent = {
  id: number
  kind: string
  status: 'pending' | 'sent' | 'failed' | 'skipped'
  /** 人话版本由后端给：`pending` 不是"已送达"，这个映射不能在前端再写一份。 */
  status_label: string
  http_status: number | null
  errcode: number | null
  error: string
  byte_len: number
  summary: string
  content: string
  created_at: string
}

export type NotifyTestResult = NotifyEvent & {
  /** `true` = 这一分钟已经发过一条，本次**没有真的再发**（界面不能说成"刚发成功"）。 */
  reused: boolean
  detail: string
}

export type NewsItem = {
  id: number
  title: string
  source: string
  published_at: string
  summary: string
  sentiment: number | null
  event_tag: string
  confidence: number | null
  model_version: string
  analysis_mode: string
}

export type Bar = {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  pct_chg: number
}

export type ScoreExplanation = {
  ts_code: string
  name: string
  total_score: number | null
  technical_score: number | null
  fundamental_score: number | null
  sentiment_score: number | null
  risk_score: number | null
  coverage: number
  risk_level: string
  explanations: Explanation[]
  calculated_at: string | null
  rule_version: string
  quote_time: string | null
  is_stale: boolean
}

// —— 量化分析（/quant/*）——————————————————————————————————————————————————————

export type SignalDirection = 'buy' | 'sell' | 'neutral'

/** 指标图上的一根 bar；预热期（样本不足）的指标为 null。 */
export type QuantIndicatorPoint = {
  trade_date: string
  close: number
  volume: number | null
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
  boll_up: number | null
  boll_mid: number | null
  boll_low: number | null
  dif: number | null
  dea: number | null
  macd_hist: number | null
  kdj_k: number | null
  kdj_d: number | null
  kdj_j: number | null
  rsi: number | null
  atr14: number | null
}

export type QuantSignal = {
  name: string
  label: string
  direction: SignalDirection
  detail: string
  strength: number
  level: number | null
}

export type QuantSeries = {
  ts_code: string
  name: string
  industry: string
  window_days: number
  bars: number
  indicators: QuantIndicatorPoint[]
  signals: QuantSignal[]
  signal_score: number | null
  latest: Record<string, number | null>
}

/** 绩效/回测指标：比率与收益一律是小数（0.12 = 12%），样本不足时为 null。 */
export type QuantMetrics = Record<string, number | null>

export type QuantPerformance = {
  ts_code: string
  name: string
  window_days: number
  benchmark: string
  metrics: QuantMetrics
  equity: Array<{ trade_date: string; close: number; drawdown: number }>
}

export type QuantTrade = {
  entry_date: string
  entry_price: number
  shares: number
  exit_date: string
  exit_price: number
  pnl: number
  pnl_pct: number
  hold_bars: number
  exit_reason: string
}

export type QuantBacktest = {
  ts_code: string
  name: string
  strategy: string
  strategies: string[]
  params: Record<string, unknown>
  metrics: QuantMetrics
  stats: QuantMetrics
  equity: Array<{ trade_date: string; equity: number; close: number; position: number; drawdown: number }>
  trades: QuantTrade[]
}

export type QuantFactorDefinition = {
  name: string
  label: string
  group: string
  weight: number
  direction: number
}

export type QuantFactorRow = {
  ts_code: string
  name: string
  industry: string
  composite: number | null
  coverage: number
  factors: Record<string, { label?: string; raw: number | null; score: number | null }>
  trade_date: string | null
  calculated_at: string | null
}

export type QuantFactors = {
  definitions: QuantFactorDefinition[]
  groups: string[]
  group_weights: Record<string, number>
  rule_version: string
  rows: QuantFactorRow[]
}

export type QuantScanRow = {
  ts_code: string
  name: string
  industry: string
  total_score: number | null
  price: number | null
  signal: string
  label: string
  direction: SignalDirection
  detail: string
  strength: number
  level: number | null
}

// —— 买卖策略（/strategy/*）——————————————————————————————————————————————————
// 字段名与后端 `api/schemas.py` 的买卖策略块 1:1；那边有两个 `extra="forbid"` 的模型，
// 所以这里的任何拼写错误都会在真机上报错，而不是静默渲染 undefined。

/** 一个策略族在一只标的上的当前分数。`score` 用**固定分母**，证据薄时向 50 漂移。 */
export type FamilyScoreView = {
  name: string
  label: string
  score: number
  buy_threshold: number
  sell_threshold: number
  buy_rules: string[]
  sell_rules: string[]
  direction: 'buy' | 'sell' | 'hold'
}

export type FamilyScores = {
  trend: FamilyScoreView
  reversion: FamilyScoreView
  fusion: FamilyScoreView
}

export type PlanCondition = {
  key: string
  label: string
  satisfied: boolean
  detail: string
}

export type PriceZone = {
  low: number
  high: number
  reference: number
  source: string
  note: string
}

export type StopPlan = {
  atr_level: number | null
  structure_level: number | null
  recommended: number
  distance_pct: number
  source: string
  note: string
}

export type TrailPlan = {
  level: number | null
  high_water: number
  note: string
}

export type PositionSizing = {
  shares: number
  lots: number
  amount: number
  weight_pct: number
  risk_amount: number
  risk_pct: number
  affordable: boolean
  formula: string
  note: string
}

export type HoldEstimate = {
  basis: 'measured' | 'heuristic'
  bars: number | null
  low: number
  high: number
  note: string
}

export type ExitRule = {
  key: string
  label: string
  triggered: boolean
  detail: string
}

export type ScorePoint = {
  trade_date: string
  trend: number
  reversion: number
  fusion: number
}

/** 一个策略在一只标的上的**实测**记录。页面上每个结论都必须挂着它。 */
export type ExpectancyBlock = {
  strategy: string
  strategy_label: string
  window_days: number
  bars: number
  trade_count: number
  /** 按**笔**。回测的 `metrics.win_rate` 是按日——同名不同义，别混用。 */
  win_rate: number | null
  avg_win_pct: number | null
  avg_loss_pct: number | null
  profit_factor: number | null
  expectancy_pct: number | null
  expectancy_per_bar_pct: number | null
  avg_hold_bars: number | null
  /** 样本末尾被强制平仓的笔数——那不是规则触发的出场。 */
  forced_end_trades: number
  cumulative_return: number | null
  max_drawdown: number | null
  benchmark_return: number | null
  excess_return: number | null
  verdict: 'positive' | 'negative' | 'insufficient'
  verdict_text: string
  caveat: string
}

export type BuySellPlan = {
  ts_code: string
  name: string
  industry: string
  strategy: string
  strategy_label: string
  as_of: string | null
  bars: number
  price: number
  price_source: string
  is_stale: boolean
  /** `avoid` = 信号已触发但实测期望为负——**永不允许**渲染成「买入」。 */
  action: 'buy' | 'watch' | 'avoid'
  action_label: string
  reasons: string[]
  entry_conditions: PlanCondition[]
  entry_zone: PriceZone
  stop_loss: StopPlan
  take_profit: TrailPlan
  sizing: PositionSizing
  expected_hold: HoldEstimate
  exit_rules: ExitRule[]
  scores: FamilyScores
  score_history: ScorePoint[]
  honesty: ExpectancyBlock
}

export type StrategyBacktest = QuantBacktest & {
  expectancy: ExpectancyBlock | null
  fused: boolean
}

/** `/expectancy` 的一行；`is_baseline` 是买入持有那一行（永远留在榜上）。 */
export type ExpectancyRow = ExpectancyBlock & { is_baseline: boolean }

/**
 * 一条规则在**全市场**（跨股票池化）的实测汇总。**不是这只股票的预期**，
 * 两者不可互相顶替，所以刻意不继承 `ExpectancyBlock`：`window_days`/`bars` 是单标的
 * 语义，`cumulative_return`/`max_drawdown`/`benchmark_return`/`excess_return` 在池化下
 * 没有意义（池化成交列表没有资金配置，算不出净值曲线）。
 */
export type PoolStatsRow = {
  strategy: string
  strategy_label: string
  /** 买入持有那一行——它不参与否决（见后端 decision.MarketEdge）。 */
  is_baseline: boolean
  trade_count: number
  /** 这条规则在多少只股票上真出了成交。与 `stock_denominator` 不是一回事。 */
  stocks_with_trades: number
  win_rate: number | null
  avg_win_pct: number | null
  avg_loss_pct: number | null
  profit_factor: number | null
  expectancy_pct: number | null
  expectancy_per_bar_pct: number | null
  avg_hold_bars: number | null
  forced_end_trades: number
  /** 每只股票的末笔都是样本末尾强制平仓，不是规则出场。 */
  forced_end_share: number | null
  /** 同期等权买入持有的每笔期望——这段窗口里市场是涨是跌，全看它。 */
  benchmark_expectancy_pct: number | null
  benchmark_expectancy_per_bar_pct: number | null
  /** 扣掉基准之后的每 bar 超额。没有它就会反向撒谎。 */
  excess_per_bar_pct: number | null
  /**
   * 「反池化」的两个度量：实测常态是**均值正而中位股负**（右偏，少数股票的盈利把均值
   * 抬到中位数之上）。只展示均值就是在骗人，故这两项必须与均值并排。
   */
  median_stock_expectancy_pct: number | null
  positive_stock_share: number | null
  /** 上面两项只统计成交 ≥ 这个笔数的股票。 */
  positive_stock_min_trades: number
  /** 上面两项的分母（达到笔数门槛的股票数）。 */
  stock_denominator: number
  verdict: 'positive' | 'negative' | 'insufficient'
}

/** 全市场实测汇总。从未计算过时 `computed_at=null` + `rows=[]`——**绝不是 0 笔**。 */
export type PoolStats = {
  computed_at: string | null
  rule_version: string
  universe_total: number
  universe_used: number
  bars_total: number
  /** 中位股票有多少根 bar——**决定窗口多长的是它**，不是平均值。 */
  bars_median: number
  window_from: string | null
  window_to: string | null
  min_bars: number
  stop_loss_atr: number | null
  trail_atr: number | null
  note: string
  caveat: string
  rows: PoolStatsRow[]
}

/**
 * 「明日操作」页的一行：四个数字 + 一句结论。
 *
 * 子结构**直接复用** `PriceZone`/`StopPlan`/`TrailPlan`/`HoldEstimate`/`ExpectancyBlock`，
 * 不写瘦身镜像——后端也是原样复用的（`SimplePlanRow` 四个子模型与单股页同一个）。多带的
 * 字段（`atr_level`/`high_water`/`sizing` 之类）不渲染就是，复制一份子集反而会随内核改字段
 * 而静默过期。
 */
export type SimplePlanRow = {
  ts_code: string
  name: string
  industry: string
  /** 库内最后一根收盘——**不是**实时报价（见 `price_source`）。 */
  price: number
  /**
   * 后端的字段是 `str`，但唯一的产生者是 `stock_view` 那个三分支，所以这里收窄成同一个
   * 联合类型（`PriceSourceLike`），否则传进 `<PriceNote stock={row} />` 是类型错误。
   * 本页目前只会是 `close`——四个数字锚定在库内收盘上，不跟实时报价走。
   */
  price_source: 'quote' | 'close' | 'none'
  price_date: string | null
  is_stale: boolean
  /** 库内实际参与实测的根数，不是请求的窗口（两者常差好几倍）。 */
  bars: number
  /** `avoid` = 实测期望为负——**永不允许**渲染成「买入」。 */
  action: 'buy' | 'watch' | 'avoid'
  /** 逐字来自后端 `Plan.action_label`。前端**不许**由 `honesty.verdict` 反推。 */
  action_label: string
  entry_zone: PriceZone
  stop_loss: StopPlan
  take_profit: TrailPlan
  expected_hold: HoldEstimate
  honesty: ExpectancyBlock
}

/** 候选里库内日线不足 20 根的标的——**不进表**，单独列出来并说明原因。 */
export type SimpleSkipped = {
  ts_code: string
  name: string
  industry: string
  bars: number
  reason: string
}

/**
 * 一个板块的候选行。三个数字必须分开读，页面也必须分开说：
 *
 * - `selected < candidates` = 「还有更多同板块候选没列出来」（我们按市值截断了）。
 * - `rows.length < selected` = 「有候选因库内日线不足没进表」（数据没拉到，不是因为挑了）。
 *
 * 后端刻意不设 `shown` 字段（它等于 `rows.length`），所以这里也不推一个出来。
 */
export type SimpleSector = {
  industry: string
  /** 过门槛与时机两步的候选数，**截断之前**的事实。 */
  candidates: number
  /** 按市值截断后入选的只数 = `min(candidates, 每板块上限)`；可能大于 `rows.length`。 */
  selected: number
  rows: SimplePlanRow[]
}

/** 「明日操作」整页。板块内的行与 `skipped` 互斥，两者都不会出现"有行没数字"。 */
export type SimplePlanBoard = {
  basis_date: string | null
  strategy: string
  strategy_label: string
  /** 请求的窗口（固定 500）；真实样本量看 `bars_median` 与行级 `bars`。 */
  window_days: number
  /** **所有板块**行级 `bars` 的中位数——窗口与实际样本的差距由它披露，不是靠平均值。 */
  bars_median: number
  note: string
  caveat: string
  sectors: SimpleSector[]
  skipped: SimpleSkipped[]
}

/** 一个后台作业的即时状态（与后端 `SyncJobView` 同形）。 */
export type SyncJob = {
  id: number
  job_type: string
  status: string
  message: string
}
export type PositionAdvice = {
  ts_code: string
  name: string
  industry: string
  status: 'holding' | 'closed'
  shares: number
  avg_cost: number
  cost: number
  price: number | null
  price_source: string
  is_stale: boolean
  pnl: number | null
  pnl_pct: number | null
  realized_pnl: number
  hold_bars: number
  first_entry_date: string | null
  trade_count: number
  stop_price: number | null
  /** `user` = 用户自己填过的止损，已经不是回测规则里那个了。 */
  stop_source: 'rule' | 'user' | 'none'
  stop_distance_pct: number | null
  stop_triggered: boolean
  trail_level: number | null
  trail_triggered: boolean
  scores: FamilyScores | null
  action: 'exit' | 'reduce' | 'hold' | 'add' | 'watch'
  action_label: string
  reasons: string[]
  data_warning: string
}

export type PositionTradeRow = {
  id: number
  ts_code: string
  name: string
  side: 'buy' | 'sell'
  trade_date: string
  price: number
  shares: number
  fee: number
  stop_price: number | null
  strategy: string
  note: string
  created_at: string | null
}

export type PositionTradeCreate = {
  ts_code: string
  side: 'buy' | 'sell'
  trade_date: string
  price: number
  shares: number
  fee?: number
  stop_price?: number | null
  strategy?: string
  note?: string
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { headers: { 'Content-Type': 'application/json' }, ...options })
  if (!response.ok) {
    let message = '请求失败'
    try {
      const payload = await response.json() as { detail?: string }
      message = payload.detail ?? message
    } catch {
      message = `${message}（HTTP ${response.status}）`
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}
export const api = {
  status: () => request<Status>('/system/status'),
  overview: () => request<MarketOverview>('/market/overview'),
  watchlist: () => request<Stock[]>('/watchlist'),
  boardTop: (n = 50) => request<TopBoardRow[]>(`/screener/top?n=${n}`),
  picksDaily: () => request<DailyPicks>('/picks/daily'),
  watchlistQuotes: () => request<Array<{ ts_code: string; name: string; price: number | null; pct_chg: number | null; quote_time: string | null; is_stale: boolean }>>('/watchlist/quotes'),
  screener: (min = 0, industry = '', sort = 'score') => request<Stock[]>(`/screener?min_score=${min}&sort=${sort}&industry=${encodeURIComponent(industry)}`),
  search: (q: string) => request<Stock[]>(`/stocks/search?q=${encodeURIComponent(q)}`),
  detail: (code: string) => request<Stock>(`/stocks/${code}`),
  bars: (code: string) => request<Bar[]>(`/stocks/${code}/daily-bars`),
  news: (code: string) => request<NewsItem[]>(`/stocks/${code}/news`),
  scoreExplanation: (code: string) => request<ScoreExplanation>(`/stocks/${code}/score-explanation`),
  quantSeries: (code: string, days = 250) => request<QuantSeries>(`/quant/series/${code}?days=${days}`),
  quantPerformance: (code: string, days = 250) => request<QuantPerformance>(`/quant/performance/${code}?days=${days}`),
  quantBacktest: (code: string, strategy = 'ma_cross', days = 500, costs = true) =>
    request<QuantBacktest>(`/quant/backtest/${code}?strategy=${strategy}&days=${days}&costs=${costs}`),
  quantFactors: (limit = 20) => request<QuantFactors>(`/quant/factors?limit=${limit}`),
  quantScan: (signal = '', direction = '', limit = 15) =>
    request<QuantScanRow[]>(`/quant/scan?signal=${signal}&direction=${direction}&limit=${limit}`),
  // —— 买卖策略 ——
  strategyPlan: (code: string, strategy = 'fusion', days = 500, equity = 1_000_000) =>
    request<BuySellPlan>(`/strategy/plan/${code}?strategy=${strategy}&days=${days}&equity=${equity}`),
  strategyBacktest: (code: string, strategy = 'fusion', days = 500, costs = true, trail = true) =>
    request<StrategyBacktest>(`/strategy/backtest/${code}?strategy=${strategy}&days=${days}&costs=${costs}&trail=${trail}`),
  strategyExpectancy: (code: string, days = 500, costs = true) =>
    request<ExpectancyRow[]>(`/strategy/expectancy/${code}?days=${days}&costs=${costs}`),
  poolStats: () => request<PoolStats>('/strategy/pool'),
  /**
   * 「明日操作」：候选名单 + 每只的买入价 / 持有天数 / 卖出价。
   *
   * **没有参数是有意的**——后端不接受任何 query 参数（连 `strategy` 都没有），所以这里也
   * 不给调用方传 `strategy`/`days` 的口子：传 `buy_hold` 拿一整页「买入」是必须防死的那条路。
   */
  simplePlan: () => request<SimplePlanBoard>('/strategy/simple'),
  /** 触发一次全市场汇总（后台跑约 4 分钟）。返回里带任务号，用 `syncJob` 轮询。 */
  refreshPoolStats: () => request<SyncJob>('/strategy/pool/refresh', { method: 'POST' }),
  /** 后台作业状态。`status` 只在 `running` 时才有意义继续轮询。 */
  syncJob: (id: number) => request<SyncJob>(`/sync/jobs/${id}`),
  positions: (status = 'holding', equity = 1_000_000, days = 500) =>
    request<PositionAdvice[]>(`/strategy/positions?status=${status}&equity=${equity}&days=${days}`),
  positionTrades: (code: string) => request<PositionTradeRow[]>(`/strategy/positions/${code}/trades`),
  addPositionTrade: (payload: PositionTradeCreate) =>
    request<PositionAdvice>('/strategy/positions/trades', { method: 'POST', body: JSON.stringify(payload) }),
  updatePositionTrade: (id: number, payload: Partial<PositionTradeCreate>) =>
    request<PositionAdvice>(`/strategy/positions/trades/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  removePositionTrade: (id: number) =>
    request<{ message: string }>(`/strategy/positions/trades/${id}`, { method: 'DELETE' }),
  addWatch: (ts_code: string) => request<{ message: string }>('/watchlist', { method: 'POST', body: JSON.stringify({ ts_code }) }),
  removeWatch: (code: string) => request<{ message: string }>(`/watchlist/${code}`, { method: 'DELETE' }),
  sync: (job_type = 'quotes') => request<{ id: number; status: string; message: string; items_updated?: number; error_class?: string }>('/sync/jobs', { method: 'POST', body: JSON.stringify({ job_type }) }),
  /**
   * 发一条微信推送测试消息。**没有参数是有意的**：后端这个端点不收请求体，
   * 因此不存在可注入的内容（站点无鉴权，拿到 IP 的人也能触发它，文案必须固定）。
   * 服务端限流 1 次/分钟，超了抛 429。
   */
  notifyTest: () => request<NotifyTestResult>('/notify/test', { method: 'POST' }),
  /** 「最近推送」账本，倒序。 */
  notifyEvents: (limit = 20) => request<NotifyEvent[]>(`/notify/events?limit=${limit}`),
}
