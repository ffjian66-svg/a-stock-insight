import { useQuery } from '@tanstack/react-query'
import { Briefcase } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ErrorBox } from './Common'
import { PositionAdviceTag, signedPct } from './QuantKit'
import { api } from '../lib/api'
import type { PositionAdvice } from '../lib/api'

const EQUITY = 1_000_000
const DAYS = 500

/** 一行浮盈：把「这只票赚了多少钱」和「赚了几个点」放在一起，
 *  因为只给百分比时，小仓位的大涨幅会看起来比大仓位的小涨幅更重要。 */
function Pnl({ row }: { row: PositionAdvice }) {
  if (row.pnl == null) return <span className="text-xs text-muted">--</span>
  return (
    <div>
      <span className={`metric font-semibold ${row.pnl >= 0 ? 'text-positive' : 'text-negative'}`}>
        {signedPct(row.pnl_pct)}
      </span>
      <p className="mt-0.5 text-xs text-muted">
        {row.pnl >= 0 ? '+' : '-'}
        {Math.abs(row.pnl).toLocaleString('zh-CN', { maximumFractionDigits: 0 })} 元
      </p>
    </div>
  )
}

/**
 * 首页的持仓摘要。
 *
 * 数据取 `/strategy/positions`——按设计，**列表端点只用库内日线、绝不回补**，
 * 所以打开首页不会因为这块消耗 TuShare 配额，也不会被某只票缺日线卡住：
 * 缺日线的行会带着 `data_warning` 降级显示，而不是让整块打不开。
 *
 * 这里**只读**：录入、改止损、清仓都在 /strategy 页做，避免两个地方各写一套流水表单。
 */
export function PositionsBrief() {
  const query = useQuery({
    queryKey: ['positions-brief'],
    queryFn: () => api.positions('holding', EQUITY, DAYS),
  })
  const rows = query.data ?? []
  const totalPnl = rows.reduce((sum, row) => sum + (row.pnl ?? 0), 0)
  const needAttention = rows.filter((row) => row.action === 'exit' || row.action === 'reduce')

  return (
    <section className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-5">
        <div>
          <p className="eyebrow flex items-center gap-1.5">
            <Briefcase size={14} />
            POSITIONS · 今日建议
          </p>
          <h2 className="mt-1 text-lg font-semibold">我的持仓</h2>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {rows.length > 0 ? (
            <div className="text-right">
              <p className="text-xs text-muted">合计浮盈</p>
              <p className={`metric font-semibold ${totalPnl >= 0 ? 'text-positive' : 'text-negative'}`}>
                {totalPnl >= 0 ? '+' : '-'}
                {Math.abs(totalPnl).toLocaleString('zh-CN', { maximumFractionDigits: 0 })} 元
              </p>
            </div>
          ) : null}
          <Link to="/strategy" className="chip border-primary/30 bg-primary/10 text-primary">
            管理持仓 →
          </Link>
        </div>
      </div>

      {query.isLoading ? (
        <p className="animate-pulse p-5 text-sm text-muted">正在读取持仓…</p>
      ) : query.isError ? (
        <div className="p-5">
          <ErrorBox message={(query.error as Error).message || '持仓读取失败'} />
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3 p-5">
          <p className="text-sm text-muted">
            还没有持仓记录。在买卖策略页录入一笔真实成交后，这里会显示浮盈、距止损与调整建议。
          </p>
          <Link to="/strategy" className="button-ghost text-sm">
            去录入第一笔
          </Link>
        </div>
      ) : (
        <>
          {needAttention.length > 0 ? (
            <p className="border-b border-border bg-warning/5 px-5 py-2.5 text-xs text-warning">
              有 {needAttention.length} 只需要处理：
              {needAttention.map((row) => `${row.name}（${row.action_label}）`).join('、')}
            </p>
          ) : null}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-border">
                  <th className="px-5 py-3 font-medium">标的</th>
                  <th className="py-3 pr-4 font-medium">股数 / 加权成本</th>
                  <th className="py-3 pr-4 font-medium">现价</th>
                  <th className="py-3 pr-4 font-medium">浮动盈亏</th>
                  <th className="py-3 pr-4 font-medium">距止损</th>
                  <th className="py-3 pr-5 font-medium">建议</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.ts_code} className="border-b border-border/70 transition hover:bg-elevated/50">
                    <td className="px-5 py-3">
                      <Link
                        to={`/stock/${row.ts_code}`}
                        state={{ from: '/' }}
                        className="font-medium hover:text-primary"
                      >
                        {row.name}
                      </Link>
                      <p className="mt-0.5 text-xs text-muted">
                        {row.ts_code} · 持有 {row.hold_bars} 个交易日
                      </p>
                    </td>
                    <td className="metric py-3 pr-4 whitespace-nowrap">
                      {row.shares.toLocaleString('zh-CN')}
                      <p className="mt-0.5 text-xs text-muted">¥ {row.avg_cost.toFixed(2)}</p>
                    </td>
                    <td className="metric py-3 pr-4 whitespace-nowrap">¥ {row.price?.toFixed(2) ?? '--'}</td>
                    <td className="py-3 pr-4 whitespace-nowrap">
                      <Pnl row={row} />
                    </td>
                    <td className="metric py-3 pr-4 whitespace-nowrap">
                      {row.stop_price == null ? (
                        <span className="text-xs text-muted">--</span>
                      ) : (
                        <>
                          ¥ {row.stop_price.toFixed(2)}
                          <p
                            className={`mt-0.5 text-xs ${row.stop_triggered ? 'text-negative' : 'text-muted'}`}
                          >
                            {row.stop_triggered ? '已跌破' : signedPct(row.stop_distance_pct)}
                            {row.stop_source === 'user' ? ' · 自定' : ''}
                          </p>
                        </>
                      )}
                    </td>
                    <td className="py-3 pr-5">
                      <PositionAdviceTag advice={row} />
                      {row.data_warning ? (
                        <p className="mt-1 max-w-56 truncate text-[11px] text-warning" title={row.data_warning}>
                          {row.data_warning}
                        </p>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
