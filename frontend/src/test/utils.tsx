import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import type {
  BuySellPlan,
  ExpectancyBlock,
  ExpectancyRow,
  FamilyScores,
  NewsItem,
  NotifyEvent,
  NotifyTestResult,
  PoolStats,
  PoolStatsRow,
  PositionAdvice,
  PositionTradeRow,
  QuantBacktest,
  QuantFactors,
  QuantIndicatorPoint,
  QuantPerformance,
  QuantScanRow,
  QuantSeries,
  ScoreExplanation,
  SimplePlanBoard,
  SimplePlanRow,
  SimpleSector,
  SparkPoint,
  Stock,
  Status,
  StrategyBacktest,
  TopBoardRow,
} from '../lib/api'

export const explanation = (name: string, score: number) => ({
  factor: name,
  value: name,
  score,
  reason: `关于${name}的透明评分依据`,
})

const baseStock = (
  ts_code: string,
  name: string,
  industry: string,
  total: number,
): Stock => ({
  ts_code,
  symbol: ts_code.slice(0, 6),
  name,
  industry,
  market: '主板',
  price: 100 + total,
  pct_chg: 1.2,
  total_score: total,
  technical_score: total - 4,
  fundamental_score: total - 2,
  sentiment_score: 60,
  coverage: 1,
  risk_level: '低',
  is_stale: false,
  price_source: 'quote',
  price_date: null,
  quote_time: '2026-09-02T10:00:00',
  pe_ttm: 22,
  pb: 8.1,
  explanations: [
    explanation('趋势与动量', total - 4),
    explanation('质量与估值', total - 2),
    explanation('新闻情绪', 60),
    explanation('波动风险', 90),
  ],
})

export const stocks: Stock[] = [
  baseStock('600519.SH', '贵州茅台', '白酒', 82),
  baseStock('300750.SZ', '宁德时代', '电池', 74),
  baseStock('601318.SH', '中国平安', '保险', 68),
]

export const boardRows: TopBoardRow[] = [
  {
    ...stocks[0],
    rank: 1,
    news_sentiment_avg: 0.62,
    news_3d_count: 3,
    news_tag: '积极',
    news_sentiment: 0.62,
    news_title: '消费旺季临近，龙头酒企渠道库存保持稳健',
    news_published_at: '2026-09-02T09:00:00',
  },
  {
    ...stocks[1],
    rank: 2,
    news_sentiment_avg: null,
    news_3d_count: 0,
    news_tag: null,
    news_sentiment: null,
    news_title: null,
    news_published_at: null,
  },
  {
    ...stocks[2],
    rank: 3,
    news_sentiment_avg: 0.3,
    news_3d_count: 1,
    news_tag: '订单',
    news_sentiment: 0.3,
    news_title: '动力电池海外订单持续增长',
    news_published_at: '2026-09-02T08:00:00',
  },
]

/** /picks/daily 次日买点候选 fixture：茅台/宁德/平安 各带 tone=buy 时机，行业互不相同。 */
export const pickRows: Stock[] = stocks.map((stock, index) => ({
  ...stock,
  timing: {
    label: index === 0 ? '回调MA20(≈1280)，可分批' : '沿MA20上行(RSI≈52)，现价可分批',
    tone: 'buy',
    detail: `现价 ${(stock.price ?? 0).toFixed(2)}，MA20 1280.0，RSI 52 — 回调MA20，可分批`,
  },
}))

const spark: SparkPoint[] = Array.from({ length: 40 }, (_, i) => ({
  date: `2026-07-${String((i % 27) + 1).padStart(2, '0')}`,
  close: 3200 + i * 3,
}))

export const status: Status = {
  provider: 'Mock 演示源',
  tushare_configured: false,
  llm_configured: false,
  mock_mode: true,
  quote_refresh_seconds: 30,
  updated_at: '2026-09-02T10:00:00',
  data_mode: 'mock',
  capabilities: ['daily', 'financials', 'news', 'quotes', 'stocks', 'trade_calendar'],
  provider_status_at: null,
  notify_configured: true,
}

/**
 * 推送账本。**刻意混进一条 `pending`**：它是"插了行但进程在发送前断了"——没有送达。
 * 界面把它显示成"已送达"就是一次静默的谎言，所以这条必须出现在桩数据里。
 */
export const notifyEvents: NotifyEvent[] = [
  {
    id: 3,
    kind: 'daily',
    status: 'sent',
    status_label: '已送达',
    http_status: 200,
    errcode: 0,
    error: '',
    byte_len: 558,
    summary: '明日操作 · 2026-09-18',
    content: '# 明日操作 · 2026-09-18\n**今日无买入候选**',
    created_at: '2026-09-18T19:00:00',
  },
  {
    id: 2,
    kind: 'sync',
    status: 'failed',
    status_label: '失败',
    http_status: 200,
    errcode: 93000,
    error: 'invalid webhook url',
    byte_len: 320,
    summary: '同步失败 · 日线行情',
    content: '# 同步失败 · 日线行情',
    created_at: '2026-09-18T17:31:00',
  },
  {
    id: 1,
    kind: 'test',
    status: 'pending',
    status_label: '未送达（进程中断）',
    http_status: null,
    errcode: null,
    error: '',
    byte_len: 96,
    summary: '推送测试',
    content: '# 推送测试',
    created_at: '2026-09-17T21:04:00',
  },
]

export const notifyTestResult: NotifyTestResult = {
  id: 4,
  kind: 'test',
  status: 'sent',
  status_label: '已送达',
  http_status: 200,
  errcode: 0,
  error: '',
  byte_len: 96,
  summary: '推送测试',
  content: '# 推送测试',
  created_at: '2026-09-18T19:30:00',
  reused: false,
  detail: '已发出。手机上没收到就是群机器人被移出群或地址已失效——账本里会记 errcode。',
}

const newsItem: NewsItem = {
  id: 1,
  title: '消费旺季临近，龙头酒企渠道库存保持稳健',
  source: '演示资讯',
  published_at: '2026-09-02T09:00:00',
  summary: '系统生成的演示摘要：渠道库存与动销保持稳健。',
  sentiment: 0.62,
  event_tag: '积极',
  confidence: 0.8,
  model_version: 'gpt-x',
  analysis_mode: 'llm',
}

const scoreExplanation: ScoreExplanation = {
  ts_code: '600519.SH',
  name: '贵州茅台',
  total_score: 82,
  technical_score: 78,
  fundamental_score: 80,
  sentiment_score: 60,
  risk_score: 90,
  coverage: 1,
  risk_level: '低',
  explanations: [
    explanation('趋势与动量', 78),
    explanation('质量与估值', 80),
    explanation('新闻情绪', 60),
    explanation('波动风险', 90),
  ],
  calculated_at: '2026-09-02T10:00:00',
  rule_version: 'v1',
  quote_time: '2026-09-02T10:00:00',
  is_stale: false,
}

const bars = Array.from({ length: 90 }, (_, i) => ({
  date: `2026-05-${String((i % 28) + 1).padStart(2, '0')}`,
  open: 1480 + i,
  high: 1495 + i,
  low: 1472 + i,
  close: 1486 + i,
  volume: 800000 + i * 1000,
  pct_chg: 0.5,
}))

// —— 量化分析 fixture ———————————————————————————————————————————————————————

const quantPoints: QuantIndicatorPoint[] = Array.from({ length: 120 }, (_, i) => ({
  trade_date: `2026-04-${String((i % 28) + 1).padStart(2, '0')}`,
  close: 1480 + i,
  volume: 800000 + i * 1000,
  ma5: i < 5 ? null : 1478 + i,
  ma10: i < 10 ? null : 1476 + i,
  ma20: i < 20 ? null : 1474 + i,
  ma60: i < 60 ? null : 1470 + i,
  boll_up: i < 20 ? null : 1520 + i,
  boll_mid: i < 20 ? null : 1474 + i,
  boll_low: i < 20 ? null : 1428 + i,
  dif: i < 26 ? null : 12.5,
  dea: i < 26 ? null : 10.2,
  macd_hist: i < 26 ? null : 4.6,
  kdj_k: i < 9 ? null : 62.4,
  kdj_d: i < 9 ? null : 58.1,
  kdj_j: i < 9 ? null : 71.0,
  rsi: i < 14 ? null : 55.6,
  atr14: i < 14 ? null : 24.3,
}))

const quantSeries: QuantSeries = {
  ts_code: '600519.SH',
  name: '贵州茅台',
  industry: '白酒',
  window_days: 250,
  bars: quantPoints.length,
  indicators: quantPoints,
  signals: [
    { name: 'ma_cross', label: 'MA5 上穿 MA20', direction: 'buy', detail: '短期均线上穿中期均线', strength: 0.25, level: null },
    { name: 'atr_stop', label: 'ATR 风控位', direction: 'neutral', detail: '跌破该价位减仓', strength: 0, level: 1531.4 },
  ],
  signal_score: 72,
  latest: { ma20: 1594, macd_hist: 4.6, atr14: 24.3 },
}

const quantPerformance: QuantPerformance = {
  ts_code: '600519.SH',
  name: '贵州茅台',
  window_days: 250,
  benchmark: '上证指数',
  metrics: {
    bars: 250,
    cumulative_return: 0.184,
    annualized_return: 0.221,
    annualized_volatility: 0.286,
    sharpe: 0.72,
    sortino: 0.94,
    max_drawdown: -0.153,
    max_drawdown_days: 42,
    calmar: 1.44,
    win_rate: 0.532,
    beta: 0.87,
    alpha: 0.061,
    correlation: 0.63,
  },
  equity: quantPoints.map((point, index) => ({
    trade_date: point.trade_date,
    close: point.close,
    drawdown: index < 60 ? 0 : -0.05,
  })),
}

const quantBacktest: QuantBacktest = {
  ts_code: '600519.SH',
  name: '贵州茅台',
  strategy: 'ma_cross',
  strategies: ['ma_cross', 'macd_cross', 'kdj_cross', 'boll_reversion', 'donchian', 'trend_combo', 'timing', 'buy_hold'],
  // 初始资金与后端 BacktestParams 默认值一致（100 万才买得起任意个股一手）
  params: { initial_cash: 1000000, caveat: '未复权：除权除息的跳空会被计为真实亏损，分红送转未建模；仅供研究参考。' },
  metrics: { bars: 120, cumulative_return: 0.132, max_drawdown: -0.089, sharpe: 0.81, beta: 0.9, alpha: 0.04 },
  // 键名必须与后端 `_trade_stats` 一致，否则组件读到 undefined 会渲染成 0
  stats: { trade_count: 2, win_rate: 0.5, avg_hold_bars: 24, avg_win: 12000, avg_loss: -8000, profit_factor: 1.5 },
  equity: quantPoints.map((point, index) => ({
    trade_date: point.trade_date,
    equity: 1000000 + index * 1100,
    close: point.close,
    position: index > 60 ? 1 : 0,
    drawdown: 0,
  })),
  trades: [
    { entry_date: '2026-05-06', entry_price: 1500, shares: 100, exit_date: '2026-06-11', exit_price: 1620, pnl: 12000, pnl_pct: 8, hold_bars: 26, exit_reason: 'signal' },
    { entry_date: '2026-07-02', entry_price: 1560, shares: 100, exit_date: '2026-08-03', exit_price: 1607, pnl: 4700, pnl_pct: 3.01, hold_bars: 22, exit_reason: 'end' },
  ],
}

const quantFactors: QuantFactors = {
  definitions: [
    { name: 'momentum_20', label: '20日动量', group: '趋势与动量', weight: 0.35, direction: 1 },
    { name: 'momentum_60', label: '60日动量', group: '趋势与动量', weight: 0.3, direction: 1 },
    { name: 'reversal_5', label: '5日反转', group: '趋势与动量', weight: 0.2, direction: -1 },
    { name: 'turnover', label: '换手活跃度', group: '趋势与动量', weight: 0.15, direction: 1 },
    { name: 'value_ep', label: '盈利收益率', group: '质量与估值', weight: 0.35, direction: 1 },
    { name: 'value_bp', label: '账面市值比', group: '质量与估值', weight: 0.15, direction: 1 },
    { name: 'quality_roe', label: 'ROE', group: '质量与估值', weight: 0.3, direction: 1 },
    { name: 'quality_growth', label: '利润增速', group: '质量与估值', weight: 0.2, direction: 1 },
    { name: 'sentiment', label: '新闻情绪', group: '新闻情绪', weight: 1, direction: 1 },
    { name: 'low_vol', label: '低波动', group: '波动风险', weight: 1, direction: 1 },
  ],
  groups: ['趋势与动量', '质量与估值', '新闻情绪', '波动风险'],
  group_weights: { 趋势与动量: 0.4, 质量与估值: 0.3, 新闻情绪: 0.2, 波动风险: 0.1 },
  rule_version: 'v2',
  rows: [
    {
      ts_code: '600519.SH',
      name: '贵州茅台',
      industry: '白酒',
      composite: 82.4,
      coverage: 1,
      factors: { momentum_20: { label: '20日动量', raw: 8.2, score: 91 }, momentum_60: { label: '60日动量', raw: 21.4, score: 88 } },
      trade_date: '2026-09-10',
      calculated_at: '2026-09-10T18:00:00',
    },
    {
      ts_code: '300750.SZ',
      name: '宁德时代',
      industry: '电池',
      composite: 74.1,
      coverage: 0.8,
      factors: { momentum_20: { label: '20日动量', raw: 4.4, score: 66 } },
      trade_date: '2026-09-10',
      calculated_at: '2026-09-10T18:00:00',
    },
  ],
}

const quantScanRows: QuantScanRow[] = [
  { ts_code: '600519.SH', name: '贵州茅台', industry: '白酒', total_score: 82.4, price: 1594.2, signal: 'ma_cross', label: 'MA5 上穿 MA20', direction: 'buy', detail: '短期均线上穿中期均线', strength: 0.25, level: null },
  { ts_code: '300750.SZ', name: '宁德时代', industry: '电池', total_score: 74.1, price: 231.6, signal: 'macd_cross', label: 'MACD 金叉', direction: 'buy', detail: 'DIF 上穿 DEA', strength: 0.25, level: null },
]

// —— 买卖策略 fixture ———————————————————————————————————————————————————————
// 诚实口径的 fixture 必须**默认是负期望**：本功能的核心契约就是负期望被如实顶到最前。
// 如果 fixture 默认给 positive，测试就会在"乐观文案渲染得出来"这件事上变绿，而那是
// 这一页最不需要被证明的性质。要测正期望就在具体用例里覆盖 handler。

const strategyScores: FamilyScores = {
  trend: {
    name: 'trend',
    label: '趋势族',
    score: 58.3,
    buy_threshold: 65,
    sell_threshold: 45,
    buy_rules: ['ma_cross'],
    sell_rules: [],
    direction: 'hold',
  },
  reversion: {
    name: 'reversion',
    label: '回归族',
    score: 50,
    buy_threshold: 65,
    sell_threshold: 45,
    buy_rules: [],
    sell_rules: [],
    direction: 'hold',
  },
  fusion: {
    name: 'fusion',
    label: '多策略融合',
    score: 54.98,
    buy_threshold: 62,
    sell_threshold: 42,
    buy_rules: ['ma_cross'],
    sell_rules: [],
    direction: 'hold',
  },
}

/** 负期望的实测记录——页面上"不许渲染成买入"的那个前提。 */
export const expectancyBlock: ExpectancyBlock = {
  strategy: 'fusion',
  strategy_label: '多策略融合',
  window_days: 500,
  bars: 480,
  trade_count: 18,
  win_rate: 0.3333,
  avg_win_pct: 3.42,
  avg_loss_pct: -3.1,
  profit_factor: 0.59,
  expectancy_pct: -2.31,
  expectancy_per_bar_pct: -0.09,
  avg_hold_bars: 25.4,
  forced_end_trades: 1,
  cumulative_return: -0.218,
  max_drawdown: -0.34,
  benchmark_return: -0.112,
  excess_return: -0.106,
  verdict: 'negative',
  verdict_text:
    '该规则在本标的最近 480 个交易日实测 18 笔，每笔期望 -2.31%，66.7% 的交易是亏的；按此规则操作的历史结果是亏钱的，请勿据此下单。',
  caveat:
    '未复权：除权除息的跳空会被计为真实亏损，分红送转未建模；仅供研究参考。满仓单标的口径：期望/笔与仓位无关，可参考；累计收益与最大回撤与本页 1% 风险预算的缩仓结果不可比。',
}

export const strategyPlan: BuySellPlan = {
  ts_code: '600519.SH',
  name: '贵州茅台',
  industry: '白酒',
  strategy: 'fusion',
  strategy_label: '多策略融合',
  as_of: '2026-09-10',
  bars: 480,
  price: 1594.2,
  price_source: 'close',
  is_stale: false,
  action: 'avoid',
  action_label: '不建议买入',
  reasons: ['信号已触发，但该规则在此标的实测期望为负，不建议据此买入', '融合分 54.98 未达 62'],
  entry_conditions: [
    { key: 'score_gate', label: '融合分 ≥ 62', satisfied: false, detail: '当前 54.98，差 7.02' },
    { key: 'family_gate', label: '族确认（族分 ≥ 65 且 ≥ 2 条规则同向）', satisfied: false, detail: '趋势族 58.3，仅 1 条规则确认' },
    { key: 'not_extended', label: '现价 ≤ MA20 × 1.10', satisfied: true, detail: '现价 1594.20，上限 1753.40' },
    { key: 'above_stop', label: '现价高于建议止损', satisfied: true, detail: '现价 1594.20 > 止损 1545.60' },
    { key: 'expectancy_gate', label: '实测期望不为负且样本 ≥ 20 笔', satisfied: false, detail: '每笔期望 -2.31%（18 笔）' },
  ],
  entry_zone: {
    low: 1575.95,
    high: 1600.28,
    reference: 1594.2,
    source: 'ATR 几何（融合族偏移）',
    note: '挂单未成交属正常情形，不要为了成交追价。',
  },
  stop_loss: {
    atr_level: 1545.6,
    structure_level: 1520.3,
    recommended: 1545.6,
    distance_pct: -3.05,
    source: 'ATR 2.0 倍 + 20 日结构低点取近者',
    note: 'A 股 T+1 且无盘中保护，止损越宽越容易被跳空穿透。',
  },
  take_profit: {
    level: 1552.4,
    high_water: 1601.2,
    note: '吊灯止损：最高价回落 2×ATR14 触发；建仓后按建仓以来最高价重算。',
  },
  sizing: {
    shares: 400,
    lots: 4,
    amount: 637680,
    weight_pct: 63.77,
    risk_amount: 29768,
    risk_pct: 0.01,
    affordable: true,
    formula:
      '① 风险预算：floor(1,000,000×1% ÷ (1594.20-1545.60) ÷ 100)×100 = 200 股  ② 波动上限：floor(1,000,000×20% ÷ 1594.20 ÷ 100)×100 = 100 股（20 日年化波动 28.4%）  ③ 置信度折扣：min(①②)×0.52（融合分 54.98） 取整手 = 400 股',
    note: '占本金 63.8%；若在 1594.20 附近成交并触发止损，单笔亏损约 29,768 元（≈本金的 2.98%）。公式全部印在上面，可手算复核',
  },
  expected_hold: {
    basis: 'measured',
    bars: 25,
    low: 25,
    high: 25,
    note: '取该规则在本标的的实测平均持有 25.4 个交易日。',
  },
  exit_rules: [
    { key: 'score_exit', label: '融合分 ≤ 42', triggered: false, detail: '当前 54.98' },
    { key: 'stop_hit', label: '收盘 ≤ 止损位', triggered: false, detail: '止损 1545.60，现价 1594.20' },
    { key: 'trail_hit', label: '自最高价回落 ≥ 2×ATR', triggered: false, detail: '最高 1601.20，止盈位 1552.40' },
    { key: 'ma_dead', label: 'MA5 下穿 MA20 且趋势分 < 50', triggered: false, detail: 'MA5 位于 MA20 上方' },
    { key: 'time_stop', label: '持有 ≥ 2× 实测平均持有且浮亏', triggered: false, detail: '未建仓' },
  ],
  scores: strategyScores,
  // 日期必须逐日倒退、不能靠 `i % 28` 拼——那会撞出重复的 key，React 会静默少渲染几行
  score_history: Array.from({ length: 30 }, (_, i) => ({
    trade_date: new Date(Date.UTC(2026, 8, 10) - i * 86_400_000).toISOString().slice(0, 10),
    trend: 52 + (i % 7),
    reversion: 48 + (i % 5),
    fusion: 50 + (i % 6),
  })),
  honesty: expectancyBlock,
}

export const strategyBacktest: StrategyBacktest = {
  ...quantBacktest,
  strategy: 'fusion',
  strategies: [
    'ma_cross',
    'macd_cross',
    'kdj_cross',
    'boll_reversion',
    'donchian',
    'trend_combo',
    'timing',
    'buy_hold',
    'trend_follow',
    'mean_reversion',
    'fusion',
  ],
  expectancy: expectancyBlock,
  fused: true,
  trades: [
    {
      entry_date: '2026-05-06',
      entry_price: 1500,
      shares: 100,
      exit_date: '2026-06-11',
      exit_price: 1620,
      pnl: 12000,
      pnl_pct: 8,
      hold_bars: 26,
      exit_reason: 'signal',
    },
    {
      entry_date: '2026-07-02',
      entry_price: 1560,
      shares: 100,
      exit_date: '2026-07-09',
      exit_price: 1510,
      pnl: -5000,
      pnl_pct: -3.2,
      hold_bars: 5,
      exit_reason: 'trail',
    },
    {
      entry_date: '2026-08-03',
      entry_price: 1580,
      shares: 100,
      exit_date: '2026-08-20',
      exit_price: 1570,
      pnl: -1000,
      pnl_pct: -0.6,
      hold_bars: 12,
      exit_reason: 'stop',
    },
  ],
}

/** 11 行排行榜：期望值降序，`buy_hold` 作为带标签的基准，样本不足硬标 verdict。 */
export const expectancyRows: ExpectancyRow[] = [
  { ...expectancyBlock, strategy: 'boll_reversion', strategy_label: '布林带回归', expectancy_pct: 15.1, trade_count: 9, verdict: 'insufficient', is_baseline: false },
  { ...expectancyBlock, strategy: 'kdj_cross', strategy_label: 'KDJ 交叉', expectancy_pct: 10.0, trade_count: 22, verdict: 'positive', is_baseline: false },
  { ...expectancyBlock, strategy: 'buy_hold', strategy_label: '买入持有（基准）', expectancy_pct: -9.4, trade_count: 1, verdict: 'insufficient', is_baseline: true },
  { ...expectancyBlock, strategy: 'macd_cross', strategy_label: 'MACD 金叉/死叉', expectancy_pct: -15.7, trade_count: 24, verdict: 'negative', is_baseline: false },
  { ...expectancyBlock, strategy: 'ma_cross', strategy_label: 'MA 金叉/死叉', expectancy_pct: -21.8, trade_count: 31, verdict: 'negative', is_baseline: false },
  { ...expectancyBlock, strategy: 'fusion', strategy_label: '多策略融合', expectancy_pct: -2.31, verdict: 'negative', is_baseline: false },
  ...(['donchian', 'trend_combo', 'timing', 'trend_follow', 'mean_reversion'] as const).map((name) => ({
    ...expectancyBlock,
    strategy: name,
    strategy_label: name,
    expectancy_pct: -12.5,
    trade_count: 6,
    verdict: 'insufficient' as const,
    is_baseline: false,
  })),
]

// —— 全市场实测汇总 fixture ————————————————————————————————————————————————————
// 数字照抄真机实测（5,515 只 / 中位 96 个交易日），关键的一条是**均值为正、而中位股票为负**
// （右偏分布，少数股票的盈利把均值抬到中位数之上）。这一节存在的全部理由就是不能只给均值，
// 所以 fixture 必须长成那个会骗人的形状——若做成"均值正、中位也正"，下面那些断言会在一个
// 现实里不存在的形状上变绿。

function poolRow(
  over: Partial<PoolStatsRow> & Pick<PoolStatsRow, 'strategy' | 'strategy_label'>,
): PoolStatsRow {
  return {
    is_baseline: false,
    trade_count: 13_625,
    stocks_with_trades: 5_307,
    win_rate: 0.52,
    avg_win_pct: 2.4,
    avg_loss_pct: -2.1,
    profit_factor: 1.18,
    expectancy_pct: 0.26,
    expectancy_per_bar_pct: 0.03,
    avg_hold_bars: 9.3,
    forced_end_trades: 1_144,
    forced_end_share: 0.084,
    benchmark_expectancy_pct: -12.45,
    benchmark_expectancy_per_bar_pct: -0.12,
    excess_per_bar_pct: 0.148,
    median_stock_expectancy_pct: -1.11,
    positive_stock_share: 0.323,
    positive_stock_min_trades: 5,
    // 分母比 stocks_with_trades 小一个量级——这正是必须把它印出来的原因
    stock_denominator: 344,
    verdict: 'positive',
    ...over,
  }
}

export const poolStatsRows: PoolStatsRow[] = [
  // 标签照抄接口返回（`backtest.strategy_label`），别自己另起一个名字
  poolRow({ strategy: 'mean_reversion', strategy_label: '均值回归族' }),
  poolRow({
    strategy: 'donchian',
    strategy_label: '唐奇安通道突破',
    trade_count: 8_817,
    stocks_with_trades: 4_850,
    expectancy_pct: -1.61,
    excess_per_bar_pct: -0.127,
    median_stock_expectancy_pct: -0.8,
    positive_stock_share: 0.338,
    stock_denominator: 130,
    avg_hold_bars: 6.5,
    forced_end_share: 0.036,
    verdict: 'negative',
  }),
  poolRow({
    strategy: 'buy_hold',
    strategy_label: '买入持有（基准）',
    is_baseline: true,
    trade_count: 5_515,
    stocks_with_trades: 5_515,
    win_rate: 0.176,
    // 基准行自己就是基准：超额恒为 0，不是"拿它减自己"
    excess_per_bar_pct: 0,
    expectancy_pct: -12.45,
    avg_hold_bars: 103.1,
    forced_end_share: 1,
    // 结构性的：它每只股票恰好 1 笔，永远达不到 5 笔门槛，故两个度量恒为 None（页面渲染 `--`）
    median_stock_expectancy_pct: null,
    positive_stock_share: null,
    stock_denominator: 0,
    verdict: 'negative',
  }),
]

export const poolStats: PoolStats = {
  computed_at: '2026-09-16T18:52:00',
  rule_version: 'v2',
  universe_total: 5_564,
  universe_used: 5_515,
  bars_total: 574_286,
  bars_median: 96,
  window_from: '2026-04-14',
  window_to: '2026-09-16',
  min_bars: 60,
  stop_loss_atr: 2,
  trail_atr: 2,
  note: '「股票中位数」与「正股占比」只统计成交 ≥ 5 笔的股票（分母见每行的 stock_denominator）；每条规则的池化样本见 stocks_with_trades。',
  caveat:
    '未复权：除权除息的跳空会被计为真实亏损，分红送转未建模；仅供研究参考。 全市场池化口径：①生存偏差——库里只有当前上市的股票，已退市的缺席，结果系统性偏乐观；②成交不独立——所有股票共享同一段行情，20 万笔不等于 20 万个独立观测。',
  rows: poolStatsRows,
}

/**
 * 「明日操作」页的行 fixture。三行刻意覆盖三种组合：
 *
 * 1. `heuristic + insufficient + watch`——线上最常见的形态（两年日线只出 ~10 笔成交）；
 * 2. `negative + avoid`——**负期望**，页面绝不许把它渲染成「买入」；
 * 3. `measured + negative`——有实测持有期，但规则仍不赚钱。`measured` 只要求
 *    `verdict != 'insufficient'`，**不**要求 positive，所以这一行本身合法且必须显示实测均值。
 *
 * `take_profit.level` 照本仓惯例给**低于** `price` 的值：未建仓时它是「最近 10 根最高价 −
 * 2×ATR」，是触发价不是目标价。amber 那条分支因此默认就被走到；null 分支在用例里覆盖。
 * 第四行专门用来覆盖 `level: null` → `--`（不许退化成 0.00）。
 */
export const simplePlanRows: SimplePlanRow[] = [
  {
    ts_code: '600519.SH',
    name: '贵州茅台',
    industry: '白酒',
    price: 1594.2,
    price_source: 'close',
    price_date: '2026-09-16',
    is_stale: false,
    bars: 480,
    action: 'watch',
    action_label: '观望：样本不足，不建议据此买入',
    entry_zone: {
      low: 1575.95,
      high: 1600.28,
      reference: 1594.2,
      source: 'ATR 几何（融合族偏移）',
      note: '按「多策略融合」的 ATR 几何给出（现价 1594.20，ATR14 24.30）；挂单未成交属正常情形，不要在区间外追价',
    },
    stop_loss: {
      atr_level: 1545.6,
      structure_level: 1520.3,
      recommended: 1545.6,
      distance_pct: -3.05,
      source: 'ATR 2.0 倍',
      note: 'A 股 T+1 且无盘中保护，止损越宽越容易被跳空穿透。',
    },
    take_profit: {
      level: 1552.4,
      high_water: 1601.2,
      note: '吊灯止损：最高价回落 2×ATR14 触发。',
    },
    expected_hold: {
      basis: 'heuristic',
      bars: null,
      low: 10,
      high: 40,
      note: '经验值（10-40 个交易日），不是本标的的实测结果——该策略在此标的只有 11 笔成交，不足以统计持有期',
    },
    honesty: {
      ...strategyPlan.honesty,
      trade_count: 11,
      verdict: 'insufficient',
      verdict_text: '该规则在本标的最近 480 个交易日只有 11 笔成交，不足 20 笔，不构成可参考的实测记录。',
    },
  },
  {
    ts_code: '300750.SZ',
    name: '宁德时代',
    industry: '电池',
    price: 231.6,
    price_source: 'close',
    price_date: '2026-09-16',
    is_stale: false,
    bars: 96,
    action: 'avoid',
    action_label: '不建议买入：该规则在此标的实测期望为负',
    entry_zone: {
      low: 226.14,
      high: 235.92,
      reference: 231.6,
      source: 'ATR 几何（融合族偏移）',
      note: '按「多策略融合」的 ATR 几何给出（现价 231.60，ATR14 9.72）；挂单未成交属正常情形，不要在区间外追价',
    },
    stop_loss: {
      atr_level: 212.16,
      structure_level: null,
      recommended: 212.16,
      distance_pct: -8.4,
      source: 'ATR 2.0 倍',
      note: 'A 股 T+1 且无盘中保护，止损越宽越容易被跳空穿透。',
    },
    take_profit: {
      level: 214.8,
      high_water: 234.2,
      note: '吊灯止损：最高价回落 2×ATR14 触发。',
    },
    expected_hold: {
      basis: 'heuristic',
      bars: null,
      low: 10,
      high: 40,
      note: '经验值（10-40 个交易日），不是本标的的实测结果——该策略在此标的只有 6 笔成交，不足以统计持有期',
    },
    honesty: {
      ...strategyPlan.honesty,
      bars: 96,
      trade_count: 6,
      expectancy_pct: -4.17,
      verdict: 'insufficient',
      verdict_text: '该规则在本标的最近 96 个交易日只有 6 笔成交，不足 20 笔，不构成可参考的实测记录。',
    },
  },
  {
    ts_code: '601318.SH',
    name: '中国平安',
    industry: '保险',
    price: 48.62,
    price_source: 'close',
    price_date: '2026-09-16',
    is_stale: false,
    bars: 502,
    action: 'avoid',
    action_label: '不建议买入：该规则在此标的实测期望为负',
    entry_zone: {
      low: 47.88,
      high: 49.11,
      reference: 48.62,
      source: 'ATR 几何（融合族偏移）',
      note: '按「多策略融合」的 ATR 几何给出（现价 48.62，ATR14 0.98）；挂单未成交属正常情形，不要在区间外追价',
    },
    stop_loss: {
      atr_level: 46.66,
      structure_level: 47.2,
      recommended: 47.2,
      distance_pct: -2.92,
      source: '20 日结构低点',
      note: 'A 股 T+1 且无盘中保护，止损越宽越容易被跳空穿透。',
    },
    take_profit: {
      level: 46.9,
      high_water: 48.9,
      note: '吊灯止损：最高价回落 2×ATR14 触发。',
    },
    expected_hold: {
      basis: 'measured',
      bars: 17,
      low: 17,
      high: 17,
      note: '取自本标的实测平均持有 17.3 bar（24 笔样本）',
    },
    honesty: {
      ...strategyPlan.honesty,
      bars: 502,
      trade_count: 24,
      avg_hold_bars: 17.3,
      expectancy_pct: -1.12,
      verdict: 'negative',
      verdict_text:
        '该规则在本标的最近 502 个交易日实测 24 笔，每笔期望 -1.12%；按此规则操作的历史结果是亏钱的，请勿据此下单。',
    },
  },
  {
    ts_code: '000858.SZ',
    name: '五粮液',
    industry: '白酒',
    price: 128.4,
    price_source: 'close',
    price_date: '2026-09-16',
    is_stale: false,
    bars: 60,
    action: 'watch',
    action_label: '观望：样本不足，不建议据此买入',
    entry_zone: {
      low: 126.1,
      high: 129.8,
      reference: 128.4,
      source: 'ATR 几何（融合族偏移）',
      note: 'ATR 缺失，退化为现价单点',
    },
    stop_loss: {
      atr_level: null,
      structure_level: null,
      recommended: 118.13,
      distance_pct: -8.0,
      source: '兜底',
      note: 'ATR 与结构位都不可用，退化为现价 -8%。',
    },
    // ATR 缺失 → 移动止盈位无定义。页面必须显示 `--`，**不许**退化成 0.00
    take_profit: { level: null, high_water: 0, note: 'ATR 缺失，移动止盈位不可用。' },
    expected_hold: {
      basis: 'heuristic',
      bars: null,
      low: 10,
      high: 40,
      note: '经验值（10-40 个交易日），不是本标的的实测结果——该策略在此标的只有 3 笔成交，不足以统计持有期',
    },
    honesty: {
      ...strategyPlan.honesty,
      bars: 60,
      trade_count: 3,
      verdict: 'insufficient',
      verdict_text: '该规则在本标的最近 60 个交易日只有 3 笔成交，不足 20 笔，不构成可参考的实测记录。',
    },
  },
]

/**
 * 板块分组 fixture：`simplePlanRows` 按 `industry` 归并成 3 个板块（白酒 2、保险 1、电池 1）。
 *
 * 顺序照后端的规则摆（行数降序、并列按板块名），所以「保险」在「电池」前面（按 Unicode
 * 码点比，保 U+4FDD < 电 U+7535）——前端不排序，这里只是让 fixture 与线上同形。
 *
 * **白酒那两行的先后刻意不是市值序**：库内 mock 是五粮液 4350 亿 > 贵州茅台 1800 亿，而这里
 * 茅台在前。前端只要对行做任何排序，DOM 顺序断言就会红——这是「板块内顺序只由后端决定」
 * 这条纪律的钉子。
 *
 * 白酒的 `candidates`/`selected` 故意大于行数，把两个披露分支都默认走到：
 * `selected < candidates` = 「按市值截断了」，`rows.length < selected` = 「有候选样本太短」。
 * 注意 `selected` 一旦小于 `candidates` 就必然等于每板块上限 10（`min(candidates, 10)`），
 * 所以「候选 28 只、列出前 10 只、其中 8 只没进表」是线上会真实出现的组合。
 */
export const simplePlanSectors: SimpleSector[] = [
  {
    industry: '白酒',
    candidates: 28,
    selected: 10,
    rows: [simplePlanRows[0], simplePlanRows[3]],
  },
  { industry: '保险', candidates: 1, selected: 1, rows: [simplePlanRows[2]] },
  { industry: '电池', candidates: 1, selected: 1, rows: [simplePlanRows[1]] },
]

export const simplePlanBoard: SimplePlanBoard = {
  basis_date: '2026-09-16',
  strategy: 'fusion',
  strategy_label: '多策略融合',
  window_days: 500,
  bars_median: 291,
  note: '综合评分≥65、趋势与动量及质量与估值均有分（四维权重覆盖≥80%，新闻情绪不强制）的“可分批买入”标的中，按行业分板块，每板块最多列出总市值最大的 10 只，不设总数上限；市值只决定板块内的排列顺序，不参与任何买卖判断，缺失市值的排在板块末位。以最近收盘日为基准，供下一交易日参考。「龙头」指通过上述门槛的候选里市值最大的那只，**不是该板块市值最大的股票**——板块里市值更大但未过门槛的股票不在本名单内。本页只用库内日线，绝不向上游回补；实测样本因此可能比单股页少，结论只会更保守。',
  caveat:
    '未复权：除权除息的跳空会被计为真实亏损，分红送转未建模；仅供研究参考。 满仓单标的口径：期望/笔与期望/bar 与仓位无关、可直接参考；累计收益与最大回撤与缩仓结果不可比。',
  sectors: simplePlanSectors,
  skipped: [
    {
      ts_code: '688981.SH',
      name: '中芯国际',
      industry: '半导体',
      bars: 12,
      reason: '库内只有 12 根日线（不足 20 根），MA20 与 ATR14 无定义',
    },
  ],
}

/** 持仓 fixture：一条已跌破止损（应为「清仓」），一条浮盈（应为「继续持有」）。 */
export const positionRows: PositionAdvice[] = [
  {
    ts_code: '600519.SH',
    name: '贵州茅台',
    industry: '白酒',
    status: 'holding',
    shares: 200,
    avg_cost: 1520.5,
    cost: 304100,
    price: 1594.2,
    price_source: 'close',
    is_stale: false,
    pnl: 14740,
    pnl_pct: 4.85,
    realized_pnl: 0,
    hold_bars: 12,
    first_entry_date: '2026-08-26',
    trade_count: 1,
    stop_price: 1545.6,
    stop_source: 'rule',
    stop_distance_pct: -3.05,
    stop_triggered: false,
    trail_level: 1552.4,
    trail_triggered: false,
    scores: strategyScores,
    action: 'hold',
    action_label: '继续持有',
    reasons: ['融合分 54.98（趋势 58.3 / 回归 50.0）', '浮盈 +4.85%，持有 12 个交易日，仓位 31.9%'],
    data_warning: '',
  },
  {
    ts_code: '300750.SZ',
    name: '宁德时代',
    industry: '电池',
    status: 'holding',
    shares: 300,
    avg_cost: 245.8,
    cost: 73740,
    price: 231.6,
    price_source: 'close',
    is_stale: false,
    pnl: -4260,
    pnl_pct: -5.78,
    realized_pnl: 1820.5,
    hold_bars: 33,
    first_entry_date: '2026-07-28',
    trade_count: 3,
    stop_price: 238.4,
    stop_source: 'user',
    stop_distance_pct: 2.94,
    stop_triggered: true,
    trail_level: 236.1,
    trail_triggered: false,
    scores: null,
    action: 'exit',
    action_label: '跌破止损，清仓',
    reasons: ['现价 231.60 已跌破止损 238.40（来源：user）'],
    data_warning: '本地暂无该标的日线，请先同步数据',
  },
]

export const positionTradeRows: PositionTradeRow[] = [
  {
    id: 1,
    ts_code: '600519.SH',
    name: '贵州茅台',
    side: 'buy',
    trade_date: '2026-08-26',
    price: 1520.5,
    shares: 200,
    fee: 5,
    stop_price: 1545.6,
    strategy: 'fusion',
    note: '',
    created_at: '2026-08-26T14:30:00',
  },
]

export const handlers = [
  http.get('/api/v1/system/status', () => HttpResponse.json(status)),
  http.get('/api/v1/market/overview', () =>
    HttpResponse.json({
      indices: [
        { code: '000001.SH', name: '上证指数', price: 3200, pct_chg: 0.5, spark },
        { code: '399001.SZ', name: '深证成指', price: 12500, pct_chg: -0.3, spark },
        { code: '399006.SZ', name: '创业板指', price: 2700, pct_chg: 0.9, spark },
      ],
      breadth: {
        rising: 5,
        falling: 2,
        flat: 1,
        average_pct: 0.4,
        as_of: '2026-09-02',
        samples: 8,
      },
      as_of: '2026-09-02T10:00:00',
      source: 'mock',
    }),
  ),
  http.get('/api/v1/watchlist', () => HttpResponse.json(stocks)),
  http.get('/api/v1/watchlist/quotes', () => HttpResponse.json([])),
  http.get('/api/v1/screener', () =>
    HttpResponse.json(stocks.filter((item) => (item.total_score ?? 0) >= 60)),
  ),
  http.get('/api/v1/screener/top', ({ request }) => {
    const url = new URL(request.url)
    const n = Number(url.searchParams.get('n') ?? 50)
    return HttpResponse.json(boardRows.slice(0, n))
  }),
  http.get('/api/v1/picks/daily', () =>
    HttpResponse.json({
      basis_date: '2026-09-02',
      note: '综合评分≥65、趋势与动量及质量与估值均有分（四维权重覆盖≥80%，新闻情绪不强制）的“可分批买入”标的中，低风险优先、单行业至多 2 只、最多 8 只；以最近收盘日为基准，供下一交易日参考。',
      picks: pickRows,
    }),
  ),
  http.get('/api/v1/stocks/search', () => HttpResponse.json([stocks[0]])),
  http.get('/api/v1/stocks/600519.SH', () => HttpResponse.json(stocks[0])),
  http.get('/api/v1/stocks/:code/daily-bars', () => HttpResponse.json(bars)),
  http.get('/api/v1/stocks/:code/news', () => HttpResponse.json([newsItem])),
  http.get('/api/v1/stocks/:code/score-explanation', () => HttpResponse.json(scoreExplanation)),
  http.get('/api/v1/quant/series/:code', () => HttpResponse.json(quantSeries)),
  http.get('/api/v1/quant/performance/:code', () => HttpResponse.json(quantPerformance)),
  http.get('/api/v1/quant/backtest/:code', ({ request }) => {
    const url = new URL(request.url)
    const strategy = url.searchParams.get('strategy') ?? 'ma_cross'
    return HttpResponse.json({ ...quantBacktest, strategy })
  }),
  http.get('/api/v1/quant/factors', () => HttpResponse.json(quantFactors)),
  http.get('/api/v1/quant/scan', () => HttpResponse.json(quantScanRows)),
  // —— 买卖策略：8 个端点一个都不能少（setupServer 是 onUnhandledRequest: 'error'）——
  http.get('/api/v1/strategy/plan/:code', ({ request }) => {
    const url = new URL(request.url)
    const strategy = url.searchParams.get('strategy') ?? 'fusion'
    return HttpResponse.json({ ...strategyPlan, strategy })
  }),
  http.get('/api/v1/strategy/backtest/:code', ({ request }) => {
    const url = new URL(request.url)
    const strategy = url.searchParams.get('strategy') ?? 'fusion'
    return HttpResponse.json({ ...strategyBacktest, strategy })
  }),
  http.get('/api/v1/strategy/expectancy/:code', () => HttpResponse.json(expectancyRows)),
  // 全市场汇总：默认给**已算过**的一份，空态在用例里覆盖（默认空会让所有 14 处 renderPage
  // 都渲染成「尚未计算」，那这一节就几乎没有被渲染到）
  http.get('/api/v1/strategy/pool', () => HttpResponse.json(poolStats)),
  http.get('/api/v1/strategy/simple', () => HttpResponse.json(simplePlanBoard)),
  http.post('/api/v1/strategy/pool/refresh', () =>
    HttpResponse.json(
      { id: 7, job_type: 'pool', status: 'running', message: '已开始计算全市场汇总（任务号 7），约需数分钟。' },
      { status: 202 },
    ),
  ),
  http.get('/api/v1/sync/jobs/:id', () =>
    HttpResponse.json({ id: 7, job_type: 'pool', status: 'success', message: '全市场汇总完成，已更新 11 条' }),
  ),
  http.get('/api/v1/strategy/positions', () => HttpResponse.json(positionRows)),
  http.get('/api/v1/strategy/positions/:code/trades', () => HttpResponse.json(positionTradeRows)),
  http.post('/api/v1/strategy/positions/trades', () => HttpResponse.json(positionRows[0], { status: 201 })),
  http.patch('/api/v1/strategy/positions/trades/:id', () => HttpResponse.json(positionRows[0])),
  http.delete('/api/v1/strategy/positions/trades/:id', () => HttpResponse.json({ message: '已删除' })),
  http.post('/api/v1/watchlist', () => HttpResponse.json({ message: '已加入自选' })),
  http.delete('/api/v1/watchlist/:code', () => HttpResponse.json({ message: '已移出自选' })),
  http.post('/api/v1/sync/jobs', () =>
    HttpResponse.json({ id: 1, status: 'success', message: '已更新 4 项数据', items_updated: 4, error_class: '' }),
  ),
  http.get('/api/v1/notify/events', () => HttpResponse.json(notifyEvents)),
  http.post('/api/v1/notify/test', () => HttpResponse.json(notifyTestResult)),
]

export const server = setupServer(...handlers)

export function renderPage(ui: ReactElement, initialEntries: string[] = ['/']) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}
