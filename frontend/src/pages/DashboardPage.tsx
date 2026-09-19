import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Clock3, Crosshair, RefreshCw, Settings, ShieldCheck, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Change, EmptyState, ErrorBox, Loading, Notice, PriceNote, Score } from '../components/Common'
import { MiniChart } from '../components/MiniChart'
import { NextDayPicks } from '../components/NextDayPicks'
import { PositionsBrief } from '../components/PositionsBrief'
import { TopBoard } from '../components/TopBoard'
import { api } from '../lib/api'
import { toast } from '../lib/toast'

export function DashboardPage() {
  const client = useQueryClient()
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview })
  const stocks = useQuery({ queryKey: ['watchlist'], queryFn: api.watchlist })
  const status = useQuery({ queryKey: ['status'], queryFn: api.status })
  const sync = useMutation({
    mutationFn: api.sync,
    onSuccess: (result) => {
      toast(result.message || '行情已刷新')
      client.invalidateQueries({ queryKey: ['watchlist'] })
      client.invalidateQueries({ queryKey: ['overview'] })
    },
    onError: (error: Error) => toast(error.message || '同步失败', 'error'),
  })

  if (overview.isLoading || stocks.isLoading) return <div className="p-5 lg:p-8"><Loading /></div>
  if (overview.error || stocks.error) {
    return (
      <div className="p-5 lg:p-8">
        <ErrorBox message="数据服务暂不可用，请确认后端已启动，或前往设置检查数据源配置。" />
      </div>
    )
  }

  const statusData = status.data

  return (
    <div className="space-y-6 p-5 pb-16 lg:p-8">
      <div className="space-y-3">
        {statusData?.mock_mode ? (
          <Notice tone="info">
            当前为<strong>演示数据源</strong>，行情与指标为本地合成数据，仅供界面演示。
            <Link to="/settings" className="font-semibold text-primary underline-offset-2 hover:underline">配置真实数据</Link>
          </Notice>
        ) : null}
        {statusData && !statusData.mock_mode && !statusData.tushare_configured ? (
          <Notice tone="warning">
            尚未配置 TuShare Token，无法获取真实行情。
            <Link to="/settings" className="font-semibold underline-offset-2 hover:underline">前往设置</Link>
          </Notice>
        ) : null}
        {statusData && !statusData.llm_configured ? (
          <Notice tone="warning">
            未配置 LLM 新闻分析：新闻摘要将使用原文降级，情绪维度不参与评分。
            <Link to="/settings" className="font-semibold underline-offset-2 hover:underline">前往设置</Link>
          </Notice>
        ) : null}
      </div>

      <section className="panel relative overflow-hidden p-5 lg:p-6">
        <img
          src="/images/market-data-flow.png"
          alt="抽象市场数据流背景"
          className="absolute inset-0 size-full object-cover opacity-30"
        />
        <div className="absolute inset-0 bg-hero" />
        <div className="relative max-w-3xl">
          <p className="eyebrow">MARKET PULSE · 沪深京</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight lg:text-3xl">
            从全市场噪声中，<span className="text-primary">提取值得研究的信号</span>
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted">
            融合趋势、估值、财务质量与新闻情绪，给出每一项评分背后的证据，而不是不可解释的结论。
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button className="button-primary" onClick={() => sync.mutate()} disabled={sync.isPending}>
              <RefreshCw size={16} className={sync.isPending ? 'animate-spin' : ''} />
              {sync.isPending ? '同步中' : '刷新实时行情'}
            </button>
            <Link className="button-ghost" to="/screener">
              <Sparkles size={16} />
              查看研究候选
            </Link>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        {(overview.data?.indices ?? []).map((item, index) => (
          <article
            className="panel panel-hover animate-rise p-5"
            style={{ animationDelay: `${index * 70}ms` }}
            key={item.code}
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted">{item.code}</p>
                <h2 className="mt-1 font-medium">{item.name}</h2>
              </div>
              <Change value={item.pct_chg} />
            </div>
            <p className="metric mt-4 text-2xl font-semibold">{item.price.toLocaleString()}</p>
            <div className="mt-3">
              <MiniChart data={item.spark} positive={item.pct_chg >= 0} height={64} />
            </div>
          </article>
        ))}
      </section>

      <PositionsBrief />

      <NextDayPicks />

      <section className="grid gap-6 xl:grid-cols-[1.65fr_1fr]">
        <div className="panel overflow-hidden">
          <div className="flex items-center justify-between border-b border-border p-5">
            <div>
              <p className="eyebrow">WATCHLIST</p>
              <h2 className="mt-1 text-lg font-semibold">实时自选</h2>
            </div>
            <span className="chip border-primary/20 bg-primary/10 text-primary">
              <span className="mr-2 size-1.5 rounded-full bg-primary" />
              {statusData?.provider}
            </span>
          </div>
          {stocks.data && stocks.data.length === 0 ? (
            <EmptyState
              title="自选列表为空"
              hint="使用顶部搜索框查找股票并点击 + 加入自选，交易时段将自动刷新最新报价。"
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-muted">
                  <tr className="border-b border-border">
                    <th className="px-5 py-3 font-medium">股票</th>
                    <th className="px-4 py-3 font-medium">最新价</th>
                    <th className="px-4 py-3 font-medium">涨跌</th>
                    <th className="px-4 py-3 font-medium">综合评分</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {stocks.data?.map((stock) => (
                    <tr key={stock.ts_code} className="border-b border-border/70 transition hover:bg-elevated/60">
                      <td className="px-5 py-4">
                        <p className="font-medium">{stock.name}</p>
                        <p className="mt-1 text-xs text-muted">
                          {stock.ts_code} · {stock.industry}
                        </p>
                      </td>
                      <td className="px-4 py-4">
                        <p className="metric font-semibold">¥ {stock.price?.toFixed(2) ?? '--'}</p>
                        <PriceNote stock={stock} />
                      </td>
                      <td className="px-4 py-4"><Change value={stock.pct_chg} /></td>
                      <td className="px-4 py-4"><Score value={stock.total_score} /></td>
                      <td className="px-5 py-4">
                        <div className="flex items-center gap-3">
                          <Link
                            aria-label={`查看${stock.name}`}
                            to={`/stock/${stock.ts_code}`}
                            state={{ from: '/' }}
                            className="text-muted hover:text-primary"
                          >
                            <ChevronRight size={18} />
                          </Link>
                          <Link
                            aria-label={`${stock.name}的买卖策略`}
                            title="买卖策略"
                            to={`/strategy?code=${stock.ts_code}`}
                            className="text-muted hover:text-primary"
                          >
                            <Crosshair size={17} />
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="panel p-5">
            <div className="flex items-center gap-2">
              <ShieldCheck size={18} className="text-primary" />
              <h2 className="font-semibold">市场广度</h2>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2">
              <div className="rounded-xl bg-positive/10 p-3">
                <p className="text-xs text-muted">上涨</p>
                <p className="metric mt-1.5 text-xl font-semibold text-positive">{overview.data?.breadth.rising ?? 0}</p>
              </div>
              <div className="rounded-xl bg-negative/10 p-3">
                <p className="text-xs text-muted">下跌</p>
                <p className="metric mt-1.5 text-xl font-semibold text-negative">{overview.data?.breadth.falling ?? 0}</p>
              </div>
              <div className="rounded-xl bg-elevated p-3">
                <p className="text-xs text-muted">平盘</p>
                <p className="metric mt-1.5 text-xl font-semibold">{overview.data?.breadth.flat ?? 0}</p>
              </div>
            </div>
            <div className="mt-3 space-y-1 text-xs text-muted">
              <p>
                样本平均涨跌 <Change value={overview.data?.breadth.average_pct ?? 0} />
              </p>
              {overview.data?.breadth.samples ? (
                <p>覆盖 {overview.data.breadth.samples} 只股票（最近交易日）</p>
              ) : null}
            </div>
          </div>
          <div className="panel p-5">
            <div className="flex items-center gap-2">
              <Settings size={18} className="text-primary" />
              <h2 className="font-semibold">系统状态</h2>
            </div>
            <div className="mt-3 space-y-2 text-sm">
              {[
                ['行情数据源', statusData?.provider],
                ['数据模式', statusData?.mock_mode ? '演示' : statusData?.data_mode === 'live' ? '实时' : '未配置'],
                ['TuShare', statusData?.tushare_configured ? '已配置' : '待配置'],
                ['LLM 分析', statusData?.llm_configured ? '已配置' : '待配置'],
                ['刷新间隔', `${statusData?.quote_refresh_seconds ?? 30}s`],
              ].map(([key, value]) => (
                <div className="flex justify-between" key={key}>
                  <span className="text-muted">{key}</span>
                  <span>{value}</span>
                </div>
              ))}
            </div>
            <div className="mt-4 flex items-center gap-1.5 text-xs text-muted">
              <Clock3 size={13} />
              最近状态更新 {statusData ? new Date(statusData.updated_at).toLocaleTimeString('zh-CN') : '--'}
            </div>
          </div>
        </div>
      </section>

      <TopBoard />
    </div>
  )
}
