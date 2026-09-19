import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, CalendarDays, Newspaper, ShieldAlert } from 'lucide-react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Change, EmptyState, Loading, PriceNote, SentimentChip } from '../components/Common'
import { api } from '../lib/api'

const tooltipStyle = {
  background: 'hsl(var(--elevated))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 12,
  fontSize: 12,
}

function formatVolume(value: number) {
  if (value >= 1e8) return `${(value / 1e8).toFixed(1)}亿`
  if (value >= 1e4) return `${(value / 1e4).toFixed(0)}万`
  return `${value}`
}

// 「明日操作」的行一直在传 `state={{ from: '/tomorrow' }}`，但这里没有对应条目，返回按钮
// 于是退化成泛化的「返回」——补上，让"从哪来回哪去"这条路走得通。
const ORIGIN_NAMES: Record<string, string> = {
  '/': '市场总览',
  '/screener': '智能选股',
  '/tomorrow': '明日操作',
}

export function StockDetailPage() {
  const { code = '600519.SH' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from
  const inAppNav = location.key !== 'default'
  const backLabel = !inAppNav ? '返回市场总览' : from && ORIGIN_NAMES[from] ? `返回${ORIGIN_NAMES[from]}` : '返回'
  const stock = useQuery({ queryKey: ['stock', code], queryFn: () => api.detail(code) })
  const bars = useQuery({ queryKey: ['bars', code], queryFn: () => api.bars(code) })
  const news = useQuery({ queryKey: ['news', code], queryFn: () => api.news(code) })
  const explanation = useQuery({
    queryKey: ['explanation', code],
    queryFn: () => api.scoreExplanation(code),
    enabled: !!stock.data,
  })

  if (stock.isLoading) return <div className="p-8"><Loading /></div>
  if (!stock.data) return null
  const s = stock.data
  const ruleVersion = explanation.data?.rule_version
  const calculatedAt = explanation.data?.calculated_at
  const dayBars = bars.data ?? []
  const volumeTone = (index: number) => (dayBars[index]?.pct_chg ?? 0) >= 0 ? 'hsl(var(--positive))' : 'hsl(var(--negative))'

  return (
    <div className="space-y-6 p-5 pb-16 lg:p-8">
      {inAppNav ? (
        <button
          type="button"
          aria-label={backLabel}
          onClick={() => navigate(-1)}
          className="inline-flex items-center gap-2 text-sm text-muted hover:text-primary"
        >
          <ArrowLeft size={16} />
          {backLabel}
        </button>
      ) : (
        <Link to="/" aria-label={backLabel} className="inline-flex items-center gap-2 text-sm text-muted hover:text-primary">
          <ArrowLeft size={16} />
          {backLabel}
        </Link>
      )}

      <section className="panel p-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-3xl font-semibold">{s.name}</h1>
              <span className="chip border-border text-muted">{s.ts_code}</span>
              <Link
                to={`/quant?code=${s.ts_code}`}
                state={{ from: location.pathname }}
                className="chip border-primary/30 bg-primary/10 text-primary"
              >
                量化分析 →
              </Link>
              <Link
                to={`/strategy?code=${s.ts_code}`}
                state={{ from: location.pathname }}
                className="chip border-primary/30 bg-primary/10 text-primary"
              >
                买卖策略 →
              </Link>
            </div>
            <p className="mt-2 text-sm text-muted">
              {s.industry} · {s.market}
            </p>
          </div>
          <div className="text-right">
            <p className="metric text-3xl font-semibold">¥ {s.price?.toFixed(2) ?? '--'}</p>
            <Change value={s.pct_chg} />
            <PriceNote stock={s} className="mt-1 text-xs text-muted" />
          </div>
        </div>
        <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-5">
          {[
            ['综合评分', s.total_score],
            ['技术面', s.technical_score],
            ['基本面', s.fundamental_score],
            ['新闻情绪', s.sentiment_score],
            ['PE(TTM)', s.pe_ttm],
          ].map(([label, value]) => (
            <div className="rounded-xl bg-elevated p-4" key={String(label)}>
              <p className="text-xs text-muted">{label}</p>
              <p className="metric mt-2 text-xl font-semibold">
                {typeof value === 'number' ? value.toFixed(1) : '--'}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.6fr_1fr]">
        <div className="panel p-5">
          <div className="mb-5 flex items-center justify-between">
            <h2 className="font-semibold">90 日价格与成交量</h2>
            <span className="text-xs text-muted"><CalendarDays className="mr-1 inline" size={14} />日线复权前</span>
          </div>
          <div className="h-56">
            <ResponsiveContainer>
              <AreaChart data={dayBars}>
                <defs>
                  <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                    <stop offset="1" stopColor="hsl(var(--primary))" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: 'hsl(var(--muted))', fontSize: 11 }} minTickGap={34} />
                <YAxis domain={['auto', 'auto']} tick={{ fill: 'hsl(var(--muted))', fontSize: 11 }} width={48} />
                <Tooltip contentStyle={tooltipStyle} />
                <Area dataKey="close" type="monotone" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#priceFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 h-20">
            <ResponsiveContainer>
              <BarChart data={dayBars} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
                <XAxis dataKey="date" tick={false} axisLine={false} height={4} />
                <YAxis hide domain={[0, 'dataMax']} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(value) => [formatVolume(Number(value)), '成交量']}
                  labelFormatter={(label) => String(label)}
                  cursor={{ fill: 'hsl(var(--elevated))' }}
                />
                <Bar dataKey="volume" radius={[2, 2, 0, 0]}>
                  {dayBars.map((_, index) => (
                    <Cell key={index} fill={volumeTone(index)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="panel p-5">
          <div className="flex items-center gap-2">
            <ShieldAlert size={18} className="text-warning" />
            <h2 className="font-semibold">评分证据</h2>
          </div>
          <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted">
            <span className="chip border-border">{`覆盖率 ${((s.coverage ?? 0) * 100).toFixed(0)}%`}</span>
            {ruleVersion ? <span className="chip border-border">{`规则 ${ruleVersion}`}</span> : null}
            {calculatedAt ? (
              <span className="chip border-border">计算于 {new Date(calculatedAt).toLocaleString('zh-CN')}</span>
            ) : null}
          </div>
          {s.explanations.length === 0 ? (
            <EmptyState
              title="暂无评分证据"
              hint="该股票尚未完成综合评分。请回到总览刷新实时行情，或手动触发一次评分同步。"
            />
          ) : (
            <div className="mt-5 space-y-5">
              {s.explanations.map((item) => (
                <div key={item.factor}>
                  <div className="flex justify-between text-sm">
                    <span>{item.factor}</span>
                    <span className="metric font-semibold text-primary">{item.score.toFixed(0)}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted">{item.reason}</p>
                  <div className="mt-2 h-1 overflow-hidden rounded bg-elevated">
                    <div className="h-full bg-primary" style={{ width: `${item.score}%` }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="panel p-5">
        <div className="flex items-center gap-2">
          <Newspaper size={18} className="text-primary" />
          <h2 className="font-semibold">相关资讯与情绪</h2>
          {news.data && news.data.length > 0 ? (
            <span className="chip border-border text-muted">{news.data.length} 条</span>
          ) : null}
        </div>
        {news.data && news.data.length === 0 ? (
          <EmptyState title="暂无相关资讯" hint="目前没有与该股票直接关联的增量资讯。" />
        ) : (
          <div className="mt-4 divide-y divide-border">
            {news.data?.map((item) => (
              <article className="py-4" key={item.id}>
                <div className="flex flex-wrap items-center gap-2">
                  <SentimentChip sentiment={item.sentiment} eventTag={item.event_tag} />
                  <h3 className="font-medium">{item.title}</h3>
                </div>
                <p className="mt-2 text-sm leading-6 text-muted">{item.summary}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span>{item.source} · {new Date(item.published_at).toLocaleString('zh-CN')}</span>
                  {item.analysis_mode === 'llm' || item.analysis_mode === 'demo' ? (
                    <span className="chip border-primary/20 bg-primary/10 text-primary">
                      {`模型 v${item.model_version} 分析`}
                    </span>
                  ) : (
                    <span className="chip border-warning/20 bg-warning/10 text-warning" title="未配置 LLM 或分析失败，以下为原文摘要">
                      原文降级
                    </span>
                  )}
                  {item.confidence != null ? (
                    <span className="text-muted">{`置信 ${(item.confidence * 100).toFixed(0)}%`}</span>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
