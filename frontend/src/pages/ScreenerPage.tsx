import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, SlidersHorizontal } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { Change, EmptyState, Loading, Score, TimingTag } from '../components/Common'
import { api } from '../lib/api'
import { toast } from '../lib/toast'

export function ScreenerPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const min = Number(searchParams.get('min') ?? '60')
  const industry = searchParams.get('industry') ?? ''
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['screener', min, industry], queryFn: () => api.screener(min, industry) })

  // 筛选条件保存在 URL（replace 同一历史条目）：返回本页 / 刷新都能原样还原
  const updateFilters = (patch: { min?: number; industry?: string }) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (patch.min !== undefined) {
          if (patch.min === 60) next.delete('min')
          else next.set('min', String(patch.min))
        }
        if (patch.industry !== undefined) {
          if (patch.industry === '') next.delete('industry')
          else next.set('industry', patch.industry)
        }
        return next
      },
      { replace: true },
    )
  }
  const add = useMutation({
    mutationFn: api.addWatch,
    onSuccess: (result) => {
      toast(result.message)
      client.invalidateQueries({ queryKey: ['watchlist'] })
    },
    onError: (error: Error) => toast(error.message || '加入自选失败', 'error'),
  })

  return (
    <div className="space-y-6 p-5 pb-16 lg:p-8">
      <div>
        <p className="eyebrow">FACTOR SCREENER</p>
        <h1 className="mt-2 text-3xl font-semibold">智能选股</h1>
        <p className="mt-2 text-sm text-muted">用透明规则缩小研究范围，每个候选都保留证据与数据覆盖率。</p>
      </div>

      <div className="panel flex flex-wrap items-end gap-4 p-5">
        <div>
          <label className="mb-2 block text-xs text-muted">最低综合评分</label>
          <input
            aria-label="最低综合评分"
            className="field w-40"
            type="number"
            min="0"
            max="100"
            value={min}
            onChange={(e) => updateFilters({ min: Number(e.target.value) })}
          />
        </div>
        <div>
          <label className="mb-2 block text-xs text-muted">行业</label>
          <select aria-label="行业" className="field w-44" value={industry} onChange={(e) => updateFilters({ industry: e.target.value })}>
            <option value="">全部行业</option>
            <option>白酒</option>
            <option>电池</option>
            <option>银行</option>
            <option>半导体</option>
          </select>
        </div>
        <button className="button-ghost" onClick={() => query.refetch()}>
          <SlidersHorizontal size={16} />
          应用筛选
        </button>
        <p className="ml-auto text-xs text-muted">当前 {query.data?.length ?? 0} 个候选</p>
      </div>

      {query.isLoading ? (
        <Loading />
      ) : query.data && query.data.length === 0 ? (
        <div className="panel">
          <EmptyState
            title="没有符合条件的研究候选"
            hint="试着降低最低综合评分、切换行业，或先在总览页为自选补充数据覆盖率。"
          />
        </div>
      ) : (
        <div className="panel overflow-x-auto">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="px-5 py-4">排名 / 股票</th>
                <th>价格</th>
                <th>涨跌</th>
                <th>评分</th>
                <th>PE(TTM)</th>
                <th>覆盖率</th>
                <th>风险</th>
                <th>操作时机</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {query.data?.map((stock, index) => (
                <tr key={stock.ts_code} className="border-b border-border/70 hover:bg-elevated/50">
                  <td className="px-5 py-4">
                    <Link to={`/stock/${stock.ts_code}`} state={{ from: '/screener' }} className="font-medium hover:text-primary">
                      <span className="mr-3 text-xs text-muted">{String(index + 1).padStart(2, '0')}</span>
                      {stock.name}
                    </Link>
                    <p className="ml-8 text-xs text-muted">{stock.ts_code} · {stock.industry}</p>
                  </td>
                  <td className="metric">¥ {stock.price?.toFixed(2) ?? '--'}</td>
                  <td><Change value={stock.pct_chg} /></td>
                  <td><Score value={stock.total_score} /></td>
                  <td className="metric">{stock.pe_ttm?.toFixed(1) ?? '--'}</td>
                  <td className="metric">{((stock.coverage ?? 0) * 100).toFixed(0)}%</td>
                  <td><span className="chip border-warning/20 bg-warning/10 text-warning">{stock.risk_level ?? '--'}风险</span></td>
                  <td className="pr-4">
                    {stock.timing ? (
                      <TimingTag timing={stock.timing} />
                    ) : (
                      <span className="text-xs text-muted">--</span>
                    )}
                  </td>
                  <td className="pr-5">
                    <button
                      aria-label={`加入${stock.name}自选`}
                      title="加入自选"
                      onClick={() => add.mutate(stock.ts_code)}
                      className="grid size-9 place-items-center rounded-lg border border-border text-muted hover:border-primary/40 hover:text-primary"
                    >
                      <Plus size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
