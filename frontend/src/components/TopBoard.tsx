import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BarChart3, Newspaper } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Change, EmptyState, ErrorBox, SentimentChip } from './Common'
import { api, type TopBoardRow } from '../lib/api'

const RANGE_OPTIONS = [10, 20, 50]

function Dim({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-[11px] text-muted">{label}</span>
      <span className="metric font-semibold">{value?.toFixed(0) ?? '--'}</span>
    </div>
  )
}

function NewsBrief({ row }: { row: TopBoardRow }) {
  if (!row.news_title || !row.news_tag) {
    return <span className="text-xs text-muted">--</span>
  }
  return (
    <div className="max-w-56">
      <SentimentChip sentiment={row.news_sentiment} eventTag={row.news_tag} />
      <p className="mt-1 truncate text-xs text-muted" title={`${row.news_title}\n${row.news_published_at ?? ''}`}>
        {row.news_title}
      </p>
    </div>
  )
}

export function TopBoard() {
  const [n, setN] = useState(50)
  const query = useQuery({ queryKey: ['board-top', n], queryFn: () => api.boardTop(n) })
  const rows = query.data ?? []

  // 头部聚合：客户端现算当前榜单行
  const avgScore = rows.length
    ? rows.reduce((sum, row) => sum + (row.total_score ?? 0), 0) / rows.length
    : null
  const newsCovered = rows.filter((row) => row.sentiment_score != null).length
  const industryCount = new Map<string, number>()
  for (const row of rows) industryCount.set(row.industry, (industryCount.get(row.industry) ?? 0) + 1)
  const topIndustries = [...industryCount.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([industry]) => industry)

  return (
    <section>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow flex items-center gap-1.5">
            <BarChart3 size={14} />
            TOP-N BOARD
          </p>
          <h2 className="mt-1 text-xl font-semibold">综合评分大屏</h2>
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-border bg-elevated/50 p-0.5">
          {RANGE_OPTIONS.map((option) => (
            <button
              key={option}
              aria-pressed={option === n}
              onClick={() => setN(option)}
              className={`rounded-md px-3 py-1 text-xs transition ${
                option === n ? 'bg-primary/15 font-semibold text-primary' : 'text-muted hover:text-foreground'
              }`}
            >
              前 {option}
            </button>
          ))}
        </div>
      </div>

      {query.isLoading ? (
        <div className="panel animate-pulse p-6 text-sm text-muted">正在汇总综合评分前 {n} 名…</div>
      ) : query.error ? (
        <div className="panel p-5">
          <ErrorBox message="榜单数据加载失败，请稍后重试。" />
        </div>
      ) : rows.length === 0 ? (
        <div className="panel">
          <EmptyState title="暂无榜单数据" hint="完成评分同步（score_snapshots）后，这里展示全市场综合评分最高的标的。" />
        </div>
      ) : (
        <div className="panel">
          <div className="flex flex-wrap items-center gap-x-8 gap-y-3 border-b border-border px-5 py-4">
            <div>
              <p className="text-xs text-muted">榜内均分</p>
              <p className="metric mt-1 text-2xl font-semibold text-primary">
                {avgScore == null ? '--' : avgScore.toFixed(1)}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted">新闻情绪已覆盖</p>
              <p className="metric mt-1 text-2xl font-semibold">
                {newsCovered} / {rows.length}
              </p>
            </div>
            <div className="min-w-40">
              <p className="text-xs text-muted">领跑行业</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {topIndustries.length ? (
                  topIndustries.map((industry) => (
                    <span key={industry} className="chip border-primary/20 bg-primary/10 text-primary">
                      {industry}
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-muted">--</span>
                )}
              </div>
            </div>
            <p className="ml-auto hidden items-center gap-1.5 text-xs text-muted md:flex">
              <Newspaper size={13} />
              新闻情绪按近 3 天计
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[1080px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  <th className="px-5 py-3 font-medium">#</th>
                  <th className="py-3 font-medium">股票</th>
                  <th className="py-3 font-medium">价格 / 涨跌</th>
                  <th className="py-3 font-medium">技术</th>
                  <th className="py-3 font-medium">基本面</th>
                  <th className="py-3 font-medium">情绪</th>
                  <th className="py-3 font-medium">覆盖率</th>
                  <th className="py-3 font-medium">风险</th>
                  <th className="py-3 font-medium">最新新闻</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.ts_code} className="border-b border-border/70 transition hover:bg-elevated/50">
                    <td className="px-5 py-3 text-xs text-muted">{String(row.rank).padStart(2, '0')}</td>
                    <td className="py-3 pr-4">
                      <Link
                        to={`/stock/${row.ts_code}`}
                        state={{ from: '/' }}
                        className="font-medium hover:text-primary"
                      >
                        {row.name}
                      </Link>
                      <p className="mt-0.5 text-xs text-muted">{row.ts_code} · {row.industry}</p>
                    </td>
                    <td className="py-3 pr-4 whitespace-nowrap">
                      <span className="metric font-semibold">¥ {row.price?.toFixed(2) ?? '--'}</span>
                      <div className="mt-0.5">
                        {row.pct_chg != null ? <Change value={row.pct_chg} /> : <span className="text-xs text-muted">--</span>}
                      </div>
                    </td>
                    <td className="py-3 pr-4 whitespace-nowrap"><Dim label="技" value={row.technical_score} /></td>
                    <td className="py-3 pr-4 whitespace-nowrap"><Dim label="基" value={row.fundamental_score} /></td>
                    <td className="py-3 pr-4 whitespace-nowrap"><Dim label="情" value={row.sentiment_score} /></td>
                    <td className="metric py-3 pr-4 whitespace-nowrap">{((row.coverage ?? 0) * 100).toFixed(0)}%</td>
                    <td className="py-3 pr-4 whitespace-nowrap">
                      <span className="chip border-warning/20 bg-warning/10 text-warning">{row.risk_level ?? '--'}风险</span>
                    </td>
                    <td className="py-3 pr-5"><NewsBrief row={row} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}
