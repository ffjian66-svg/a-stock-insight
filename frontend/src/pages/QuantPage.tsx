import { useQuery } from '@tanstack/react-query'
import { Activity, LineChart, Search, TrendingUp } from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { EmptyState, ErrorBox, Loading } from '../components/Common'
import {
  BacktestEquityChart,
  DIRECTION_STYLE,
  MetricCard,
  MetricGrid,
  TradeTable,
  num,
  paramNumber,
  pct,
  tooltipStyle,
} from '../components/QuantKit'
import { api } from '../lib/api'

const DEFAULT_CODE = '600519.SH'
const WINDOWS = [120, 250, 500]
const STRATEGY_LABELS: Record<string, string> = {
  ma_cross: '均线金叉/死叉',
  macd_cross: 'MACD 金叉/死叉',
  kdj_cross: 'KDJ 金叉/死叉',
  boll_reversion: '布林带回归',
  donchian: '唐奇安通道突破',
  trend_combo: '趋势共振',
  timing: '站点择时规则',
  buy_hold: '买入持有（基准）',
}

export function QuantPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const code = searchParams.get('code') ?? DEFAULT_CODE
  const days = Number(searchParams.get('days') ?? '250')
  const strategy = searchParams.get('strategy') ?? 'ma_cross'
  const costs = searchParams.get('costs') !== 'false'
  const [term, setTerm] = useState('')

  const patch = (next: Record<string, string | number | null>) => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        for (const [key, value] of Object.entries(next)) {
          if (value == null) params.delete(key)
          else params.set(key, String(value))
        }
        return params
      },
      { replace: true },
    )
  }

  const series = useQuery({ queryKey: ['quant-series', code, days], queryFn: () => api.quantSeries(code, days) })
  const performance = useQuery({
    queryKey: ['quant-performance', code, days],
    queryFn: () => api.quantPerformance(code, days),
  })
  const backtest = useQuery({
    queryKey: ['quant-backtest', code, strategy, days, costs],
    queryFn: () => api.quantBacktest(code, strategy, days, costs),
  })
  const factorBoard = useQuery({ queryKey: ['quant-factors'], queryFn: () => api.quantFactors(20) })
  const scan = useQuery({ queryKey: ['quant-scan'], queryFn: () => api.quantScan('', '', 15) })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const next = term.trim().toUpperCase()
    if (next) patch({ code: next })
    setTerm('')
  }

  const points = series.data?.indicators ?? []
  const trades = backtest.data?.trades ?? []
  const backtestEquity = (backtest.data?.equity ?? []).map((point) => ({
    ...point,
    // 买入持有基准：以同一段收盘价归一化到初始资金，仅作形状对照（未计成本与整手）
    benchmark: backtest.data && backtest.data.equity.length > 0
      ? (point.close / backtest.data.equity[0].close) * backtest.data.equity[0].equity
      : null,
  }))

  return (
    <div className="space-y-6 p-5 pb-16 lg:p-8">
      <section className="panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="flex items-center gap-2 text-2xl font-semibold">
                <TrendingUp size={22} className="text-primary" />
                量化分析
              </h1>
              <Link
                to={`/strategy?code=${code}&days=${days}`}
                state={{ from: location.pathname }}
                className="chip border-primary/30 bg-primary/10 text-primary"
              >
                买卖策略 →
              </Link>
            </div>
            <p className="mt-2 text-sm text-muted">
              指标序列、择时信号、风险绩效与策略回测共用同一套规则；首次访问某只标的会自动回补两年日线。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <form onSubmit={submit} className="flex items-center gap-2">
              <label className="sr-only" htmlFor="quant-code">
                股票代码
              </label>
              <div className="flex items-center gap-2 rounded-xl border border-border bg-elevated/40 px-3 py-2">
                <Search size={14} className="text-muted" />
                <input
                  id="quant-code"
                  value={term}
                  onChange={(event) => setTerm(event.target.value)}
                  placeholder={code}
                  className="w-32 bg-transparent text-sm outline-none placeholder:text-muted"
                />
              </div>
              <button type="submit" className="button-primary px-3 py-2 text-sm">
                查看
              </button>
            </form>
            <div className="flex items-center gap-1 rounded-xl border border-border p-1">
              {WINDOWS.map((window) => (
                <button
                  key={window}
                  type="button"
                  onClick={() => patch({ days: window === 250 ? null : window })}
                  className={`rounded-lg px-3 py-1.5 text-xs ${days === window ? 'bg-primary/15 text-primary' : 'text-muted hover:text-foreground'}`}
                >
                  {window}天
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      {series.isLoading ? <Loading label="正在计算指标与信号…" /> : null}
      {series.isError ? <ErrorBox message={(series.error as Error).message} /> : null}

      {series.data ? (
        <>
          <section className="panel p-6">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">
                  {series.data.name}
                  <span className="ml-2 text-sm font-normal text-muted">{series.data.ts_code}</span>
                </h2>
                <p className="mt-1 text-xs text-muted">
                  {series.data.industry} · 最近 {series.data.bars} 个交易日
                </p>
              </div>
              <Link
                to={`/stock/${series.data.ts_code}`}
                className="text-sm text-muted transition-colors hover:text-primary"
              >
                个股详情 →
              </Link>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              {series.data.signals.length === 0 ? (
                <span className="chip border-border text-muted">当前无明确信号</span>
              ) : (
                series.data.signals.map((signal) => (
                  <span
                    key={signal.name}
                    className={`chip ${DIRECTION_STYLE[signal.direction]}`}
                    title={signal.detail}
                  >
                    {signal.label}
                    {signal.level != null ? ` · ${signal.level.toFixed(2)}` : ''}
                  </span>
                ))
              )}
              <span className="ml-1 text-xs text-muted">
                信号强度 {series.data.signal_score == null ? '--' : series.data.signal_score.toFixed(0)}
              </span>
            </div>
          </section>

          <section className="panel p-6">
            <h2 className="flex items-center gap-2 text-lg font-semibold">
              <LineChart size={18} className="text-primary" />
              指标图
            </h2>
            <div className="mt-4 h-80">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={points} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" opacity={0.4} />
                  <XAxis dataKey="trade_date" tick={{ fontSize: 11 }} minTickGap={40} />
                  <YAxis yAxisId="price" domain={['auto', 'auto']} tick={{ fontSize: 11 }} width={56} />
                  <YAxis yAxisId="volume" hide domain={[0, (max: number) => max * 4]} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar yAxisId="volume" dataKey="volume" name="成交量" fill="hsl(var(--muted))" opacity={0.25} isAnimationActive={false} />
                  <Line yAxisId="price" dataKey="boll_up" name="BOLL上轨" stroke="hsl(var(--muted))" strokeDasharray="4 4" dot={false} strokeWidth={1} />
                  <Line yAxisId="price" dataKey="boll_low" name="BOLL下轨" stroke="hsl(var(--muted))" strokeDasharray="4 4" dot={false} strokeWidth={1} />
                  <Line yAxisId="price" dataKey="ma5" name="MA5" stroke="hsl(var(--warning))" dot={false} strokeWidth={1.4} />
                  <Line yAxisId="price" dataKey="ma20" name="MA20" stroke="hsl(var(--primary))" dot={false} strokeWidth={1.4} />
                  <Line yAxisId="price" dataKey="ma60" name="MA60" stroke="hsl(var(--negative))" dot={false} strokeWidth={1.4} />
                  <Line yAxisId="price" dataKey="close" name="收盘" stroke="hsl(var(--foreground))" dot={false} strokeWidth={1.8} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            <div className="mt-6 grid gap-6 lg:grid-cols-2">
              <div>
                <p className="text-xs text-muted">MACD（12-26-9）</p>
                <div className="mt-2 h-44">
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={points} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                      <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" opacity={0.4} />
                      <XAxis dataKey="trade_date" tick={{ fontSize: 11 }} minTickGap={40} />
                      <YAxis tick={{ fontSize: 11 }} width={56} />
                      <Tooltip contentStyle={tooltipStyle} />
                      <ReferenceLine y={0} stroke="hsl(var(--border))" />
                      <Bar dataKey="macd_hist" name="柱" fill="hsl(var(--primary))" opacity={0.5} isAnimationActive={false} />
                      <Line dataKey="dif" name="DIF" stroke="hsl(var(--warning))" dot={false} strokeWidth={1.4} />
                      <Line dataKey="dea" name="DEA" stroke="hsl(var(--negative))" dot={false} strokeWidth={1.4} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              </div>
              <div>
                <p className="text-xs text-muted">KDJ（9-3-3）</p>
                <div className="mt-2 h-44">
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={points} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                      <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" opacity={0.4} />
                      <XAxis dataKey="trade_date" tick={{ fontSize: 11 }} minTickGap={40} />
                      <YAxis tick={{ fontSize: 11 }} width={56} />
                      <Tooltip contentStyle={tooltipStyle} />
                      <ReferenceLine y={80} stroke="hsl(var(--negative))" strokeDasharray="3 3" />
                      <ReferenceLine y={20} stroke="hsl(var(--positive))" strokeDasharray="3 3" />
                      <Line dataKey="kdj_k" name="K" stroke="hsl(var(--primary))" dot={false} strokeWidth={1.4} />
                      <Line dataKey="kdj_d" name="D" stroke="hsl(var(--warning))" dot={false} strokeWidth={1.4} />
                      <Line dataKey="kdj_j" name="J" stroke="hsl(var(--negative))" dot={false} strokeWidth={1.2} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>
          </section>
        </>
      ) : null}

      {performance.data ? (
        <section className="panel p-6">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="flex items-center gap-2 text-lg font-semibold">
              <Activity size={18} className="text-primary" />
              风险与绩效
            </h2>
            <p className="text-xs text-muted">
              基准：{performance.data.benchmark || '不可用'} · 样本 {performance.data.metrics.bars ?? 0} 个交易日
            </p>
          </div>
          <div className="mt-4">
            <MetricGrid metrics={performance.data.metrics} />
          </div>
          <div className="mt-6 h-40">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={performance.data.equity} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" opacity={0.4} />
                <XAxis dataKey="trade_date" tick={{ fontSize: 11 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} width={56} tickFormatter={(value: number) => `${(value * 100).toFixed(0)}%`} />
                <Tooltip contentStyle={tooltipStyle} formatter={(value: number) => `${(value * 100).toFixed(2)}%`} />
                <Area dataKey="drawdown" name="回撤" stroke="hsl(var(--negative))" fill="hsl(var(--negative))" fillOpacity={0.2} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </section>
      ) : null}

      <section className="panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">策略回测</h2>
          <div className="flex flex-wrap items-center gap-3">
            <label className="sr-only" htmlFor="quant-strategy">
              策略
            </label>
            <select
              id="quant-strategy"
              value={strategy}
              onChange={(event) => patch({ strategy: event.target.value })}
              className="rounded-xl border border-border bg-elevated/40 px-3 py-2 text-sm"
            >
              {(backtest.data?.strategies ?? Object.keys(STRATEGY_LABELS)).map((name) => (
                <option key={name} value={name}>
                  {STRATEGY_LABELS[name] ?? name}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-2 text-sm text-muted">
              <input
                type="checkbox"
                checked={costs}
                onChange={(event) => patch({ costs: event.target.checked ? null : 'false' })}
              />
              计入交易成本
            </label>
          </div>
        </div>

        {backtest.isLoading ? <Loading label="正在回测…" /> : null}
        {backtest.isError ? <ErrorBox message={(backtest.error as Error).message} /> : null}

        {backtest.data ? (
          <>
            <p className="mt-3 text-xs text-muted">{String(backtest.data.params.caveat ?? '')}</p>
            <div className="mt-4">
              <MetricGrid metrics={backtest.data.metrics} />
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <MetricCard label="交易次数" value={`${backtest.data.stats.trade_count ?? 0}`} />
              <MetricCard label="胜率" value={pct(backtest.data.stats.win_rate, 1)} />
              <MetricCard label="平均持有" value={`${num(backtest.data.stats.avg_hold_bars, 1)} 天`} />
              <MetricCard label="平均盈利" value={num(backtest.data.stats.avg_win, 0)} />
              <MetricCard label="盈亏比" value={num(backtest.data.stats.profit_factor)} />
              <MetricCard
                label="期末净值"
                value={num(backtestEquity.length ? backtestEquity[backtestEquity.length - 1].equity : null, 0)}
                hint={`初始资金 ${num(paramNumber(backtest.data.params, 'initial_cash'), 0)} 元；期末已强制平仓`}
              />
            </div>

            <BacktestEquityChart data={backtestEquity} />

            <TradeTable trades={trades} />
          </>
        ) : null}
      </section>

      <section className="panel p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-lg font-semibold">因子分位榜</h2>
          <p className="text-xs text-muted">
            横截面分位口径 {factorBoard.data?.rule_version || ''} · 组内权重
            {Object.entries(factorBoard.data?.group_weights ?? {})
              .map(([group, weight]) => `${group} ${weight}`)
              .join(' / ')}
          </p>
        </div>
        {factorBoard.data?.rows.length ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-border text-left">
                  <th className="py-2">标的</th>
                  <th className="py-2">行业</th>
                  <th className="py-2">综合分位</th>
                  {(factorBoard.data.definitions ?? []).map((definition) => (
                    <th key={definition.name} className="py-2" title={definition.group}>
                      {definition.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {factorBoard.data.rows.map((row) => (
                  <tr key={row.ts_code} className="border-b border-border/50">
                    <td className="py-2">
                      <Link to={`/quant?code=${row.ts_code}`} className="hover:text-primary">
                        {row.name}
                      </Link>
                    </td>
                    <td className="py-2 text-muted">{row.industry}</td>
                    <td className="metric py-2 font-semibold">{num(row.composite, 1)}</td>
                    {factorBoard.data.definitions.map((definition) => (
                      <td key={definition.name} className="metric py-2 text-muted">
                        {num(row.factors[definition.name]?.score ?? null, 0)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="暂无因子快照" hint="执行一次「综合评分」同步后即可看到横截面分位。" />
        )}
      </section>

      <section className="panel p-6">
        <h2 className="text-lg font-semibold">全市场信号扫描</h2>
        <p className="mt-1 text-xs text-muted">在综合评分靠前的标的池里现算信号，按强度排序。</p>
        {scan.data?.length ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-border text-left">
                  <th className="py-2">标的</th>
                  <th className="py-2">行业</th>
                  <th className="py-2">信号</th>
                  <th className="py-2">方向</th>
                  <th className="py-2">说明</th>
                  <th className="py-2">现价</th>
                  <th className="py-2">综合分</th>
                </tr>
              </thead>
              <tbody>
                {scan.data.map((row) => (
                  <tr key={`${row.ts_code}-${row.signal}`} className="border-b border-border/50">
                    <td className="py-2">
                      <Link to={`/quant?code=${row.ts_code}`} className="hover:text-primary">
                        {row.name}
                      </Link>
                    </td>
                    <td className="py-2 text-muted">{row.industry}</td>
                    <td className="py-2">{row.label}</td>
                    <td className="py-2">
                      <span className={`chip ${DIRECTION_STYLE[row.direction]}`}>
                        {row.direction === 'buy' ? '买入' : row.direction === 'sell' ? '卖出' : '中性'}
                      </span>
                    </td>
                    <td className="py-2 text-muted">{row.detail}</td>
                    <td className="metric py-2">{num(row.price)}</td>
                    <td className="metric py-2">{num(row.total_score, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="当前没有触发信号" hint="信号是低频事件，稍后再看或放宽扫描池。" />
        )}
      </section>
    </div>
  )
}
