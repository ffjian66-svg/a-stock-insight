import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import type { NewsItem, ScoreExplanation, SparkPoint, Stock, Status, TopBoardRow } from '../lib/api'

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

const spark: SparkPoint[] = Array.from({ length: 40 }, (_, i) => ({
  date: `2026-07-${String((i % 27) + 1).padStart(2, '0')}`,
  close: 3200 + i * 3,
}))

const status: Status = {
  provider: 'Mock 演示源',
  tushare_configured: false,
  llm_configured: false,
  mock_mode: true,
  quote_refresh_seconds: 30,
  updated_at: '2026-09-02T10:00:00',
  data_mode: 'mock',
  capabilities: ['daily', 'financials', 'news', 'quotes', 'stocks', 'trade_calendar'],
  provider_status_at: null,
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
  http.get('/api/v1/stocks/search', () => HttpResponse.json([stocks[0]])),
  http.get('/api/v1/stocks/600519.SH', () => HttpResponse.json(stocks[0])),
  http.get('/api/v1/stocks/:code/daily-bars', () => HttpResponse.json(bars)),
  http.get('/api/v1/stocks/:code/news', () => HttpResponse.json([newsItem])),
  http.get('/api/v1/stocks/:code/score-explanation', () => HttpResponse.json(scoreExplanation)),
  http.post('/api/v1/watchlist', () => HttpResponse.json({ message: '已加入自选' })),
  http.delete('/api/v1/watchlist/:code', () => HttpResponse.json({ message: '已移出自选' })),
  http.post('/api/v1/sync/jobs', () =>
    HttpResponse.json({ id: 1, status: 'success', message: '已更新 4 项数据', items_updated: 4, error_class: '' }),
  ),
]

export const server = setupServer(...handlers)

export function renderPage(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}
