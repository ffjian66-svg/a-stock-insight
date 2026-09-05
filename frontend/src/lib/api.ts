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
  watchlistQuotes: () => request<Array<{ ts_code: string; name: string; price: number | null; pct_chg: number | null; quote_time: string | null; is_stale: boolean }>>('/watchlist/quotes'),
  screener: (min = 0, industry = '', sort = 'score') => request<Stock[]>(`/screener?min_score=${min}&sort=${sort}&industry=${encodeURIComponent(industry)}`),
  search: (q: string) => request<Stock[]>(`/stocks/search?q=${encodeURIComponent(q)}`),
  detail: (code: string) => request<Stock>(`/stocks/${code}`),
  bars: (code: string) => request<Bar[]>(`/stocks/${code}/daily-bars`),
  news: (code: string) => request<NewsItem[]>(`/stocks/${code}/news`),
  scoreExplanation: (code: string) => request<ScoreExplanation>(`/stocks/${code}/score-explanation`),
  addWatch: (ts_code: string) => request<{ message: string }>('/watchlist', { method: 'POST', body: JSON.stringify({ ts_code }) }),
  removeWatch: (code: string) => request<{ message: string }>(`/watchlist/${code}`, { method: 'DELETE' }),
  sync: (job_type = 'quotes') => request<{ id: number; status: string; message: string; items_updated?: number; error_class?: string }>('/sync/jobs', { method: 'POST', body: JSON.stringify({ job_type }) }),
}
