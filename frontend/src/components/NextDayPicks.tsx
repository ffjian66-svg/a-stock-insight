import { useQuery } from '@tanstack/react-query'
import { CalendarRange, Crosshair } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Change, EmptyState, ErrorBox, PriceNote, Score, TimingTag } from './Common'
import { api, type DailyPicks, type Stock } from '../lib/api'

function BuyReason({ stock }: { stock: Stock }) {
  const detail = stock.timing?.detail
  if (!detail) return <span className="text-xs text-muted">--</span>
  return (
    <span className="block max-w-72 truncate text-xs leading-5 text-muted" title={detail}>
      {detail}
    </span>
  )
}

export function NextDayPicks() {
  const query = useQuery({ queryKey: ['next-picks'], queryFn: api.picksDaily, staleTime: 60_000 })
  const data: DailyPicks | undefined = query.data
  const picks = data?.picks ?? []

  return (
    <section>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="eyebrow flex items-center gap-1.5">
            <CalendarRange size={14} />
            NEXT-SESSION PICKS
          </p>
          <h2 className="mt-1 text-xl font-semibold">次日买点候选</h2>
          {data?.note ? <p className="mt-1 max-w-3xl text-xs leading-5 text-muted">{data.note}</p> : null}
        </div>
        {data?.basis_date ? (
          <span className="chip whitespace-nowrap border-primary/20 bg-primary/10 text-primary">
            基于 {data.basis_date} 收盘 · 下一交易日参考
          </span>
        ) : null}
      </div>

      {query.isLoading ? (
        <div className="panel animate-pulse p-6 text-sm text-muted">正在计算次日买点候选…</div>
      ) : query.error ? (
        <div className="panel p-5">
          <ErrorBox message="次日买点候选计算失败，请稍后重试。" />
        </div>
      ) : picks.length === 0 ? (
        <div className="panel">
          <EmptyState
            title="暂无次日买点候选"
            hint="低风险且评分充足的“可分批买入”标的为空：请等收盘同步（17:30）后再看，或到智能选股放宽评分门槛。"
          />
        </div>
      ) : (
        <div className="panel">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  <th className="px-5 py-3 font-medium">#</th>
                  <th className="py-3 font-medium">股票</th>
                  <th className="py-3 font-medium">最新价 / 涨跌</th>
                  <th className="py-3 font-medium">综合评分</th>
                  <th className="py-3 font-medium">风险</th>
                  <th className="py-3 font-medium">操作时机</th>
                  <th className="py-3 pr-5 font-medium">参考理由</th>
                </tr>
              </thead>
              <tbody>
                {picks.map((stock, index) => (
                  <tr key={stock.ts_code} className="border-b border-border/70 transition hover:bg-elevated/50">
                    <td className="px-5 py-3 text-xs text-muted">{String(index + 1).padStart(2, '0')}</td>
                    <td className="py-3 pr-4">
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/stock/${stock.ts_code}`}
                          state={{ from: '/' }}
                          className="font-medium hover:text-primary"
                        >
                          {stock.name}
                        </Link>
                        <Link
                          aria-label={`${stock.name}的买卖策略`}
                          title="买卖策略"
                          to={`/strategy?code=${stock.ts_code}`}
                          className="text-muted hover:text-primary"
                        >
                          <Crosshair size={15} />
                        </Link>
                      </div>
                      <p className="mt-0.5 text-xs text-muted">{stock.ts_code} · {stock.industry}</p>
                    </td>
                    <td className="py-3 pr-4 whitespace-nowrap">
                      <span className="metric font-semibold">¥ {stock.price?.toFixed(2) ?? '--'}</span>
                      <div className="mt-0.5">
                        {stock.pct_chg != null ? (
                          <Change value={stock.pct_chg} />
                        ) : (
                          <span className="text-xs text-muted">--</span>
                        )}
                      </div>
                      <PriceNote stock={stock} />
                    </td>
                    <td className="py-3 pr-4 whitespace-nowrap">
                      <Score value={stock.total_score} />
                    </td>
                    <td className="py-3 pr-4 whitespace-nowrap">
                      <span className="chip border-warning/20 bg-warning/10 text-warning">
                        {stock.risk_level ?? '--'}风险
                      </span>
                    </td>
                    <td className="py-3 pr-4 whitespace-nowrap">
                      <TimingTag timing={stock.timing} />
                    </td>
                    <td className="py-3 pr-5">
                      <BuyReason stock={stock} />
                    </td>
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
