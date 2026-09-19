import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Crosshair, Info, Search, ShieldAlert } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { EmptyState, ErrorBox, Loading, Notice } from '../components/Common'
import {
  ACTION_STYLE,
  BacktestEquityChart,
  MetricCard,
  PositionAdviceTag,
  TradeTable,
  VERDICT_LABELS,
  VERDICT_STYLE,
  VERDICT_TONE,
  num,
  pct,
  signedPct,
} from '../components/QuantKit'
import type {
  BuySellPlan,
  FamilyScoreView,
  PoolStats,
  PoolStatsRow,
  PositionAdvice,
  PositionTradeRow,
} from '../lib/api'
import { api } from '../lib/api'
import { toast } from '../lib/toast'

const DEFAULT_CODE = '600519.SH'
const DEFAULT_EQUITY = 1_000_000
const WINDOWS = [120, 250, 500]

export function StrategyPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const code = searchParams.get('code') ?? DEFAULT_CODE
  const strategy = searchParams.get('strategy') ?? 'fusion'
  const days = Number(searchParams.get('days') ?? '500')
  const equity = Number(searchParams.get('equity') ?? String(DEFAULT_EQUITY))
  const status = searchParams.get('status') ?? 'holding'
  const [term, setTerm] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

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

  const queryClient = useQueryClient()
  // `keepPreviousData`：改策略/本金/窗口时保留上一份结果，否则整页会闪成 Loading 再回来，
  // 而"闪一下"在这个页面上尤其糟——用户会以为数字变了，其实只是还没到。
  const plan = useQuery({
    queryKey: ['strategy-plan', code, strategy, days, equity],
    queryFn: () => api.strategyPlan(code, strategy, days, equity),
    placeholderData: keepPreviousData,
  })
  const backtest = useQuery({
    queryKey: ['strategy-backtest', code, strategy, days],
    queryFn: () => api.strategyBacktest(code, strategy, days),
    placeholderData: keepPreviousData,
  })
  const expectancy = useQuery({
    queryKey: ['strategy-expectancy', code, days],
    queryFn: () => api.strategyExpectancy(code, days),
    placeholderData: keepPreviousData,
  })
  const positions = useQuery({
    queryKey: ['strategy-positions', status, equity, days],
    queryFn: () => api.positions(status, equity, days),
  })
  const ledger = useQuery({
    queryKey: ['strategy-ledger', expanded],
    queryFn: () => api.positionTrades(expanded ?? ''),
    enabled: expanded != null,
  })
  // 全市场汇总：只读已物化的结果（现场算要 4 分钟，绝不能挂在请求里）。
  const pool = useQuery({
    queryKey: ['strategy-pool'],
    queryFn: () => api.poolStats(),
    placeholderData: keepPreviousData,
  })
  // 手动触发的那次汇总。任务号放 state 而不是 URL：它描述的是「此刻正在跑什么」，
  // 不是页面位置——刷新页面后不该假装还有一次在跑。
  const [poolRunId, setPoolRunId] = useState<number | null>(null)
  const poolRun = useQuery({
    queryKey: ['strategy-pool-run', poolRunId],
    queryFn: () => api.syncJob(poolRunId as number),
    enabled: poolRunId != null,
    // 只在还在跑时轮询。10 秒一次：整个任务要 4 分钟，比这更密只会白撞数据库。
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 10_000 : false),
  })
  const refreshPool = useMutation({
    mutationFn: () => api.refreshPoolStats(),
    onSuccess: (job) => {
      setPoolRunId(job.id)
      toast(job.message, 'info')
    },
    onError: (failure: Error) => toast(failure.message, 'error'),
  })
  const finishedRun = poolRun.data
  useEffect(() => {
    if (!finishedRun || finishedRun.status === 'running') return
    setPoolRunId(null) // 停掉轮询
    void queryClient.invalidateQueries({ queryKey: ['strategy-pool'] })
    toast(finishedRun.message, finishedRun.status === 'success' ? 'success' : 'error')
  }, [finishedRun, queryClient])

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    const next = term.trim().toUpperCase()
    if (next) patch({ code: next })
    setTerm('')
  }

  const data = plan.data
  if (!data) {
    if (plan.isError) {
      return (
        <div className="p-5 lg:p-8">
          <ErrorBox message={(plan.error as Error).message} />
        </div>
      )
    }
    return (
      <div className="p-5 lg:p-8">
        <Loading label="正在读取买卖计划…" />
      </div>
    )
  }

  // 策略下拉的标签取自 `/expectancy`（11 行，每行都带 strategy_label）。
  // 在这里再写一份标签表就是第二个副本——QuantKit 抽取的理由正是不要第二份。
  const strategyOptions = (expectancy.data ?? []).map((row) => ({
    value: row.strategy,
    label: row.strategy_label,
  }))
  if (strategyOptions.length === 0) {
    strategyOptions.push({ value: strategy, label: data.strategy_label })
  }

  return (
    <div className="space-y-6 p-5 pb-16 lg:p-8">
      <section className="panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="flex items-center gap-2 text-2xl font-semibold">
                <Crosshair size={22} className="text-primary" />
                买卖策略
              </h1>
              <Link
                to={`/quant?code=${code}&days=${days}`}
                state={{ from: location.pathname }}
                className="chip border-primary/30 bg-primary/10 text-primary"
              >
                量化分析 →
              </Link>
            </div>
            <p className="mt-2 text-sm text-muted">
              单股买卖计划、11 个策略在该标的上的实测期望、以及持仓流水与调整建议。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <form onSubmit={submitSearch} className="flex items-center gap-2">
              <label className="sr-only" htmlFor="strategy-code">
                股票代码
              </label>
              <div className="flex items-center gap-2 rounded-xl border border-border bg-elevated/40 px-3 py-2">
                <Search size={14} className="text-muted" />
                <input
                  id="strategy-code"
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
            <label className="sr-only" htmlFor="strategy-pick">
              策略
            </label>
            <select
              id="strategy-pick"
              value={strategy}
              onChange={(event) => patch({ strategy: event.target.value })}
              className="rounded-xl border border-border bg-elevated/40 px-3 py-2 text-sm"
            >
              {strategyOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-2 text-sm text-muted">
              本金
              <input
                type="number"
                min={10000}
                step={10000}
                value={equity}
                onChange={(event) => patch({ equity: Number(event.target.value) || DEFAULT_EQUITY })}
                className="w-32 rounded-xl border border-border bg-elevated/40 px-3 py-2 text-sm"
                title="风险预算与波动上限都按这个本金计算"
              />
            </label>
            <div className="flex items-center gap-1 rounded-xl border border-border p-1">
              {WINDOWS.map((window) => (
                <button
                  key={window}
                  type="button"
                  onClick={() => patch({ days: window })}
                  className={`rounded-lg px-3 py-1.5 text-xs ${days === window ? 'bg-primary/15 text-primary' : 'text-muted hover:text-foreground'}`}
                >
                  {window}天
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* 免责声明在**所有数字之上**且不可关闭——这句话是这一页存在的前提，不是脚注。 */}
      <Notice tone="warning">
        <ShieldAlert size={14} className="shrink-0" />
        <span>
          本页每个结论都由历史数据实测得出：<strong>多数规则在真实数据上是亏钱的</strong>
          ，页面会如实展示实测期望与样本量。所有内容仅供研究参考，不构成投资建议。
        </span>
      </Notice>

      {/* 重新取数失败时保留旧数据是好事，但必须说出来：否则用户会拿一份过期的计划照着做 */}
      {plan.isError ? <ErrorBox message={(plan.error as Error).message} /> : null}

      <section className="panel p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">
              {data.name}
              <span className="ml-2 text-sm font-normal text-muted">{data.ts_code}</span>
            </h2>
            <p className="mt-1 text-xs text-muted">
              {data.industry} · {data.strategy_label} · 样本 {data.bars} 个交易日
              {data.as_of ? ` · 截至 ${data.as_of}` : ''}
            </p>
          </div>
          <Link to={`/stock/${data.ts_code}`} className="text-sm text-muted transition-colors hover:text-primary">
            个股详情 →
          </Link>
        </div>
        {data.is_stale ? (
          <div className="mt-3">
            <Notice tone="warning">
              <span>
                当日收盘数据尚未同步，本计划基于 {data.as_of ?? '较早'} 的收盘价计算，可能已经过期。
              </span>
            </Notice>
          </div>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className={`chip whitespace-nowrap ${ACTION_STYLE[data.action]}`}>{data.action_label}</span>
          <span className="text-xs text-muted">现价 {num(data.price)}（{data.price_source}）</span>
        </div>

        {data.reasons.length > 0 ? (
          <ul className="mt-3 space-y-1 text-sm text-muted">
            {data.reasons.map((reason) => (
              <li key={reason}>· {reason}</li>
            ))}
          </ul>
        ) : null}

        {/* 诚实层：嵌在计划卡内部、默认展开、紧跟 action chip。计划与它的实测记录是同一个
            视觉对象——这是"结论必须挂实测"这条契约的结构性满足，不是排版偏好。 */}
        <div className={`mt-4 rounded-xl border p-4 ${VERDICT_STYLE[data.honesty.verdict] ?? 'border-border'}`}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="chip border-current whitespace-nowrap">
              {VERDICT_LABELS[data.honesty.verdict] ?? data.honesty.verdict}
            </span>
            <span className="text-xs opacity-80">
              {data.honesty.strategy_label} · 实测 {data.honesty.trade_count} 笔
              {data.honesty.trade_count > 0 ? `（样本 ${data.honesty.bars} 个交易日）` : ''}
            </span>
          </div>
          <p className="mt-3 text-sm leading-6">{data.honesty.verdict_text}</p>
          <div className="mt-4 grid grid-cols-2 gap-3 text-foreground md:grid-cols-4 xl:grid-cols-7">
            <MetricCard
              label="期望收益/笔"
              value={signedPct(data.honesty.expectancy_pct)}
              hint="胜率×平均盈利 + 败率×平均亏损，等于每笔盈亏百分比的均值"
              tone={(data.honesty.expectancy_pct ?? 0) < 0 ? 'negative' : ''}
            />
            <MetricCard
              label="期望收益/bar"
              value={signedPct(data.honesty.expectancy_per_bar_pct)}
              hint="防止「5 天 1%」看起来和「60 天 1%」一样"
            />
            <MetricCard label="交易胜率（按笔）" value={pct(data.honesty.win_rate, 1)} hint="与风险绩效里的「日胜率」同名不同义" />
            <MetricCard label="盈亏比" value={num(data.honesty.profit_factor)} hint="总盈利 / 总亏损" />
            <MetricCard label="交易次数" value={`${data.honesty.trade_count}`} hint="少于 20 笔一律标「样本不足」，不论正负" />
            <MetricCard label="平均持有" value={`${num(data.honesty.avg_hold_bars, 1)} 天`} />
            <MetricCard
              label="样本末尾平仓"
              value={`${data.honesty.forced_end_trades} 笔`}
              hint="样本结束时的强制平仓，不是规则触发的出场"
            />
          </div>
          <div className="mt-4 grid gap-2 text-xs md:grid-cols-3">
            <p>
              该策略累计 <span className={`metric ${(data.honesty.cumulative_return ?? 0) < 0 ? 'negative' : 'positive'}`}>{pct(data.honesty.cumulative_return)}</span>
            </p>
            <p>
              同期买入持有 <span className="metric">{pct(data.honesty.benchmark_return)}</span>
            </p>
            <p>
              超额 <span className={`metric ${(data.honesty.excess_return ?? 0) < 0 ? 'negative' : 'positive'}`}>{pct(data.honesty.excess_return)}</span>
            </p>
          </div>
          <p className="mt-3 text-xs leading-5 opacity-80">{data.honesty.caveat}</p>
        </div>

        <div className="mt-5 grid gap-6 lg:grid-cols-2">
          <div>
            <p className="text-xs text-muted">入场条件（全部满足才会给出买入结论）</p>
            <ul className="mt-2 space-y-2">
              {data.entry_conditions.map((condition) => (
                <li key={condition.key} className="flex items-start gap-2 text-sm">
                  <span className={condition.satisfied ? 'text-positive' : 'text-muted'}>
                    {condition.satisfied ? '✓' : '✗'}
                  </span>
                  <span>
                    <span className={condition.satisfied ? '' : 'text-muted'}>{condition.label}</span>
                    {condition.detail ? <span className="ml-2 text-xs text-muted">{condition.detail}</span> : null}
                  </span>
                </li>
              ))}
            </ul>

            <p className="mt-5 text-xs text-muted">失效与清仓条件</p>
            <ul className="mt-2 space-y-2">
              {data.exit_rules.map((rule) => (
                <li key={rule.key} className="flex items-start gap-2 text-sm">
                  <span className={rule.triggered ? 'text-warning' : 'text-muted'}>{rule.triggered ? '!' : '·'}</span>
                  <span>
                    <span className={rule.triggered ? 'text-warning' : 'text-muted'}>{rule.label}</span>
                    {rule.detail ? <span className="ml-2 text-xs text-muted">{rule.detail}</span> : null}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <PriceLadder plan={data} />
        </div>

        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-border p-4">
            <p className="text-xs text-muted">仓位建议</p>
            <p className="metric mt-1 text-lg font-semibold">{data.sizing.shares} 股</p>
            <p className="mt-1 text-xs text-muted">
              约 {num(data.sizing.amount, 0)} 元 · 占本金 {num(data.sizing.weight_pct, 1)}% · 触发止损约亏{' '}
              {num(data.sizing.risk_amount, 0)} 元（{num(data.sizing.risk_pct * 100, 2)}% 本金）
            </p>
            <p className="mt-3 break-all font-mono text-xs leading-5 text-muted">{data.sizing.formula}</p>
            <p className={`mt-3 text-xs leading-5 ${data.sizing.affordable ? 'text-muted' : 'text-warning'}`}>
              {data.sizing.note}
            </p>
          </div>
          <div className="rounded-xl border border-border p-4">
            <p className="text-xs text-muted">
              预期持有期（{data.expected_hold.basis === 'measured' ? '实测' : '经验值'}）
            </p>
            <p className="metric mt-1 text-lg font-semibold">{data.expected_hold.bars ?? '—'} 个交易日</p>
            <p className="mt-1 text-xs text-muted">
              区间 {data.expected_hold.low}–{data.expected_hold.high} 个交易日
            </p>
            <p className="mt-3 text-xs leading-5 text-muted">{data.expected_hold.note}</p>
          </div>
        </div>
      </section>

      <section className="panel p-6">
        <h2 className="text-lg font-semibold">分数明细</h2>
        <p className="mt-1 text-xs text-muted">
          族分用<strong className="text-foreground">固定分母</strong>：证据薄弱时向 50 漂移，只有全族规则一致才到
          100。与量化页的「信号强度」不是同一个口径。
        </p>
        <div className="mt-4 space-y-5">
          {[data.scores.trend, data.scores.reversion, data.scores.fusion].map((family) => (
            <ScoreRow key={family.name} family={family} />
          ))}
        </div>
        {data.score_history.length > 0 ? (
          <div className="mt-6 overflow-x-auto">
            <p className="text-xs text-muted">最近 {data.score_history.length} 个交易日的分数</p>
            <table className="mt-2 w-full text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-border text-left">
                  <th className="py-2">日期</th>
                  <th className="py-2">趋势族</th>
                  <th className="py-2">回归族</th>
                  <th className="py-2">融合分</th>
                </tr>
              </thead>
              <tbody>
                {data.score_history.map((point) => (
                  <tr key={point.trade_date} className="border-b border-border/50">
                    <td className="py-1.5">{point.trade_date}</td>
                    <td className="metric py-1.5">{num(point.trend, 1)}</td>
                    <td className="metric py-1.5">{num(point.reversion, 1)}</td>
                    <td className="metric py-1.5">{num(point.fusion, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="panel p-6">
        <h2 className="text-lg font-semibold">策略实测对比</h2>
        <p className="mt-1 text-xs text-muted">
          该股 11 个策略各自的实测期望，按期望值降序。少于 20 笔一律标「样本不足」；买入持有永远留在榜上作基准。
        </p>
        {expectancy.isError ? <ErrorBox message={(expectancy.error as Error).message} /> : null}
        {expectancy.data?.length ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-border text-left">
                  <th className="py-2">策略</th>
                  <th className="py-2">期望/笔</th>
                  <th className="py-2">笔数</th>
                  <th className="py-2">胜率</th>
                  <th className="py-2">平均持有</th>
                  <th className="py-2">累计</th>
                  <th className="py-2">买入持有</th>
                  <th className="py-2">超额</th>
                  <th className="py-2">结论</th>
                </tr>
              </thead>
              <tbody>
                {expectancy.data.map((row) => (
                  <tr key={row.strategy} className="border-b border-border/50">
                    <td className="py-2">
                      <button
                        type="button"
                        onClick={() => patch({ strategy: row.strategy })}
                        className="hover:text-primary"
                      >
                        {row.strategy_label}
                      </button>
                      {row.is_baseline ? <span className="chip ml-2 border-border text-muted">基准</span> : null}
                    </td>
                    <td className={`metric py-2 ${(row.expectancy_pct ?? 0) < 0 ? 'text-negative' : ''}`}>
                      {signedPct(row.expectancy_pct)}
                    </td>
                    <td className="metric py-2">{row.trade_count}</td>
                    <td className="metric py-2">{pct(row.win_rate, 1)}</td>
                    <td className="metric py-2">{num(row.avg_hold_bars, 1)}</td>
                    <td className="metric py-2">{pct(row.cumulative_return)}</td>
                    <td className="metric py-2 text-muted">{pct(row.benchmark_return)}</td>
                    <td className={`metric py-2 ${(row.excess_return ?? 0) < 0 ? 'text-negative' : ''}`}>
                      {pct(row.excess_return)}
                    </td>
                    <td className={`py-2 ${VERDICT_TONE[row.verdict] ?? 'text-muted'}`}>
                      {VERDICT_LABELS[row.verdict] ?? row.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="暂无实测数据" hint="本地缺少该标的日线时无法回测，请先同步数据。" />
        )}
      </section>

      <section className="panel p-6">
        <h2 className="text-lg font-semibold">回测明细</h2>
        <p className="mt-1 text-xs text-muted">
          止损与移动止盈用的就是计划里显示的参数（ATR 2.0 / 移动止盈 2.0），所以这组成交与上面的实测期望同源。
        </p>
        {backtest.isLoading ? <Loading label="正在回测…" /> : null}
        {backtest.isError ? <ErrorBox message={(backtest.error as Error).message} /> : null}
        {backtest.data ? (
          <>
            <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
              <MetricCard label="累计收益" value={pct(backtest.data.metrics.cumulative_return)} />
              <MetricCard label="最大回撤" value={pct(backtest.data.metrics.max_drawdown)} />
              <MetricCard label="夏普比率" value={num(backtest.data.metrics.sharpe)} />
              <MetricCard label="交易次数" value={`${backtest.data.stats.trade_count ?? 0}`} />
              <MetricCard label="交易胜率（按笔）" value={pct(backtest.data.stats.win_rate, 1)} />
              <MetricCard label="平均持有" value={`${num(backtest.data.stats.avg_hold_bars, 1)} 天`} />
            </div>
            <BacktestEquityChart data={backtestEquity(backtest.data.equity)} />
            <TradeTable trades={backtest.data.trades} />
          </>
        ) : null}
      </section>

      <PoolSection
        data={pool.data}
        isLoading={pool.isLoading}
        error={pool.error as Error | null}
        isRefreshing={refreshPool.isPending || poolRunId != null}
        onRefresh={() => refreshPool.mutate()}
      />

      <PositionsSection
        rows={positions.data ?? []}
        isLoading={positions.isLoading}
        error={positions.error as Error | null}
        status={status}
        onStatus={(next) => patch({ status: next })}
        code={code}
        expanded={expanded}
        onExpand={(next) => setExpanded(next)}
        ledger={ledger.data ?? []}
        ledgerLoading={ledger.isLoading}
        onChanged={() => {
          void queryClient.invalidateQueries({ queryKey: ['strategy-positions'] })
          void queryClient.invalidateQueries({ queryKey: ['strategy-ledger'] })
        }}
      />

      <Notice tone="info">
        <Info size={14} className="shrink-0" />
        <span>
          口径：未复权（除权除息跳空计为真实亏损）；信号次日开盘成交；A 股 T+1、100 股整手；含佣金、印花税与
          5bp 滑点假设；涨跌停与停牌未建模。回测是<strong>满仓单标的</strong>，与上方 1% 风险预算的缩仓结果不可比——
          期望/笔与仓位无关可参考，累计收益与最大回撤不可。入场价区是区间，挂单未成交属正常情形。
        </span>
      </Notice>
    </div>
  )
}

/** 千分位。池化数字动辄「203,874 笔」，裸数字读不出量级。 */
function grouped(value: number) {
  return value.toLocaleString('en-US')
}

/**
 * 全市场实测汇总：11 条规则在**全部股票**上各跑一遍的池化结果。
 *
 * ## 为什么自成一节而不是给「策略实测对比」加列
 *
 * 那一节的行是**点选策略的按钮**（点一下整页换标的），且每行都是"这只股票"的实测；
 * 池化说的是"所有股票"，是另一个统计量。混进同一张表，读者会把两者当成同一件事的
 * 两个刻度——而这正是本节全部文案在防的事。
 *
 * ## 列序是刻意的：股级中位数、正股占比排在期望/笔**之前**
 *
 * 实测常态是**池化均值为正、中位股票为负**（11 条规则里每一条的股票中位数都是负的），
 * 右偏分布下少数股票的盈利把均值抬到中位数之上。先给均值就是先给一个会骗人的数，
 * 所以"典型股票"的两个度量必须排在它前面。
 */
function PoolSection({
  data,
  isLoading,
  error,
  isRefreshing,
  onRefresh,
}: {
  data: PoolStats | undefined
  isLoading: boolean
  error: Error | null
  isRefreshing: boolean
  onRefresh: () => void
}) {
  const computed = data?.computed_at != null
  const totalTrades = (data?.rows ?? []).reduce((sum, row) => sum + row.trade_count, 0)
  // 「约几个月」按每月 21 个交易日折算——A 股每月约 21 个交易日，写死这个换算比
  // 直接不换算好：读者需要的是"这段窗口有多长"，不是"96 根 bar"。
  const months = data ? (data.bars_median / 21).toFixed(1) : '--'

  return (
    <section className="panel p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">全市场实测汇总</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-muted">
            把 11 条规则在库内<strong className="text-foreground">每一只</strong>有足够日线的股票上各跑一遍，
            再把所有成交汇总起来——这是本页唯一能把样本量从「几十笔」抬到「十万笔」量级的口径。
            上面几栏说的是「这只股票」，本栏说的是「全部股票」，<strong className="text-foreground">两者不可互相顶替</strong>。
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={isRefreshing}
          className="button-primary px-3 py-2 text-sm disabled:opacity-60"
        >
          {isRefreshing ? '正在计算…' : computed ? '重新计算' : '立即计算'}
        </button>
      </div>

      {isLoading ? <Loading label="正在读取全市场汇总…" /> : null}
      {error ? <ErrorBox message={error.message} /> : null}

      {!isLoading && !error && !computed ? (
        <EmptyState
          title="尚未计算全市场汇总"
          hint="这一步要在本地日线上跑约 4 分钟（5,500 余只股票、57 万余根 bar），只读本地数据、不消耗任何数据源配额。点右上角「立即计算」开始；算完这一栏会自动出现结果。"
        />
      ) : null}

      {computed && data ? (
        <>
          {/* 分母与窗口和结论一起给：只说「20 万笔」会被读成「20 万个独立观测」 */}
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            <span>
              <strong className="metric text-foreground">{grouped(data.universe_used)}</strong>
              {` 只计入（库内 ${grouped(data.universe_total)} 只）`}
            </span>
            <span>
              中位 <strong className="metric text-foreground">{data.bars_median}</strong> 个交易日 ≈ {months} 个月
            </span>
            <span>
              各规则合计 <strong className="metric text-foreground">{grouped(totalTrades)}</strong> 笔
            </span>
            <span>
              窗口 {data.window_from} ~ {data.window_to}（各股首末交易日的<strong className="text-foreground">中位数</strong>）
            </span>
            <span>统计于 {new Date(data.computed_at as string).toLocaleString('zh-CN')}</span>
            <span>内核 {data.rule_version}</span>
            <span>
              止损 {num(data.stop_loss_atr, 1)}×ATR / 移动止盈 {num(data.trail_atr, 1)}×ATR
            </span>
          </div>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-border text-left">
                  <th className="py-2">规则名</th>
                  {/* 这两列必须在「期望/笔」之前：均值为正而中位股为负是常态 */}
                  <th className="py-2" title="只统计成交 ≥ 5 笔的股票；括号里是分母">
                    股级中位数
                  </th>
                  <th className="py-2" title="分母同上：期望为正的股票占比">
                    正股占比
                  </th>
                  <th className="py-2">期望/笔</th>
                  <th className="py-2" title="已扣掉同期等权买入持有；按 bar 算才能跨持有期比较">
                    每 bar 超额
                  </th>
                  <th className="py-2">笔数</th>
                  <th className="py-2">有成交股票</th>
                  <th className="py-2">胜率</th>
                  <th className="py-2">平均持有</th>
                  <th className="py-2" title="每只股票的末笔都是样本末尾强制平仓，不是规则出场">
                    强平占比
                  </th>
                  <th className="py-2">结论</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <PoolRowView key={row.strategy} row={row} />
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-3 text-xs leading-5 text-muted">{data.note}</p>

          <div className="mt-3 space-y-2">
            <Notice tone="warning">
              <ShieldAlert size={14} className="shrink-0" />
              <span>
                <strong>这一栏不是这只股票的预期。</strong>它说的是「这条规则在整个市场、这段时间上平均如何」。
                否决是单向的：全市场为负时，本页只会把「买入」降级为「观望」，
                <strong>绝不会</strong>因为全市场好看而把「观望 / 不建议」升成「买入」——一个标的自己的实测样本不足时，
                全市场那十万笔也不构成买入理由（它们不是这只股票上的证据）。
              </span>
            </Notice>
            {/* 偏差逐条披露的**唯一副本在后端**（`backtest.POOL_CAVEAT`）：前端再写一份，
                两处迟早会不一致，而不一致的偏差说明比没有更糟 */}
            <Notice tone="info">
              <Info size={14} className="shrink-0" />
              <span>{data.caveat}</span>
            </Notice>
          </div>
        </>
      ) : null}
    </section>
  )
}

/** 汇总表的一行。基准行不参与否决，标出来免得读者以为它也能被"否决"或"放行"。 */
function PoolRowView({ row }: { row: PoolStatsRow }) {
  // 只统计成交 ≥ positive_stock_min_trades 笔的股票——分母直接印在数字旁边，
  // 免得「中位股 −1.11%」被当成"全部 5,307 只的中位数"（实测分母只有 344 只）
  const denominator = `${grouped(row.stock_denominator)} 只`
  // 分母为 0 但有成交 = 这条规则每只股票的笔数都达不到门槛（实测：买入持有恒为 1 笔）。
  // 那种 `--` 不是"还没算"，必须在 title 里说清楚，否则会被读成缺数据。
  const structural = row.stock_denominator === 0 && row.stocks_with_trades > 0
  const naTitle = structural
    ? `不适用：该规则在每只股票上都不足 ${row.positive_stock_min_trades} 笔（共 ${grouped(row.stocks_with_trades)} 只有成交），达不到统计门槛`
    : `只统计成交 ≥ ${row.positive_stock_min_trades} 笔的股票，分母 ${denominator}`
  return (
    <tr className="border-b border-border/50">
      <td className="py-2">
        {row.strategy_label}
        {row.is_baseline ? <span className="chip ml-2 border-border text-muted">基准</span> : null}
      </td>
      <td
        className={`metric py-2 ${(row.median_stock_expectancy_pct ?? 0) < 0 ? 'text-negative' : ''}`}
        title={naTitle}
      >
        {structural ? '不适用' : signedPct(row.median_stock_expectancy_pct)}
        <span className="ml-1 text-xs text-muted">({denominator})</span>
      </td>
      <td className="metric py-2" title={naTitle}>
        {structural ? '不适用' : pct(row.positive_stock_share, 1)}
      </td>
      <td className={`metric py-2 ${(row.expectancy_pct ?? 0) < 0 ? 'text-negative' : ''}`}>
        {signedPct(row.expectancy_pct)}
      </td>
      <td className={`metric py-2 ${(row.excess_per_bar_pct ?? 0) < 0 ? 'text-negative' : ''}`}>
        {signedPct(row.excess_per_bar_pct)}
      </td>
      <td className="metric py-2">{grouped(row.trade_count)}</td>
      <td className="metric py-2">{grouped(row.stocks_with_trades)}</td>
      <td className="metric py-2">{pct(row.win_rate, 1)}</td>
      <td className="metric py-2">{num(row.avg_hold_bars, 1)}</td>
      <td className="metric py-2">{pct(row.forced_end_share, 1)}</td>
      <td className={`py-2 ${VERDICT_TONE[row.verdict] ?? 'text-muted'}`}>
        {VERDICT_LABELS[row.verdict] ?? row.verdict}
      </td>
    </tr>
  )
}

/** 回测净值与买入持有基准同图：把收盘价归一化到初始资金，只作形状对照。 */
function backtestEquity(equity: Array<{ trade_date: string; equity: number; close: number }>) {
  const first = equity[0]
  if (!first) return []
  return equity.map((point) => ({
    ...point,
    benchmark: (point.close / first.close) * first.equity,
  }))
}

/** 价格梯：纯 div + 百分比定位。计划里就是四条价位，不值得为它引入一张图表。 */
function PriceLadder({ plan }: { plan: BuySellPlan }) {
  const lines: Array<{ key: string; label: string; value: number; tone: string }> = [
    { key: 'price', label: '现价', value: plan.price, tone: 'bg-foreground' },
    { key: 'stop', label: '建议止损', value: plan.stop_loss.recommended, tone: 'bg-negative' },
    { key: 'entry_low', label: '入场下限', value: plan.entry_zone.low, tone: 'bg-primary' },
    { key: 'entry_high', label: '入场上限', value: plan.entry_zone.high, tone: 'bg-primary' },
  ]
  if (plan.take_profit.level != null) {
    lines.push({ key: 'trail', label: '移动止盈位', value: plan.take_profit.level, tone: 'bg-warning' })
  }
  const values = lines.map((line) => line.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  // 退化区间（全部价位相同）不做除法——那会得到 NaN% 的样式，元素会静默消失
  const span = max - min || 1
  const at = (value: number) => ((value - min) / span) * 100

  return (
    <div>
      <p className="text-xs text-muted">价格梯（{plan.stop_loss.source}）</p>
      <div className="relative mt-3 h-52 rounded-xl border border-border bg-elevated/20">
        {/* 入场价区画成一条带子，而不是两条独立的线——"在这个区间挂单"是它唯一的含义 */}
        <div
          className="absolute left-28 right-24 bg-primary/10"
          style={{ bottom: `${at(plan.entry_zone.low)}%`, height: `${Math.max(at(plan.entry_zone.high) - at(plan.entry_zone.low), 1)}%` }}
        />
        {lines.map((line) => (
          <div
            key={line.key}
            className="absolute left-2 right-2 flex -translate-y-1/2 items-center gap-2"
            style={{ bottom: `${at(line.value)}%` }}
          >
            <span className="w-24 shrink-0 text-xs text-muted">{line.label}</span>
            <span className={`h-0.5 flex-1 rounded ${line.tone}`} />
            <span className="metric w-20 text-right text-xs">{line.value.toFixed(2)}</span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs leading-5 text-muted">{plan.entry_zone.note}</p>
      <p className="mt-1 text-xs leading-5 text-muted">{plan.stop_loss.note}</p>
      <p className="mt-1 text-xs leading-5 text-muted">{plan.take_profit.note}</p>
    </div>
  )
}

/** 单行分数条：复用个股页的分数条模式，叠一条阈值刻度。 */
function ScoreRow({ family }: { family: FamilyScoreView }) {
  return (
    <div>
      <div className="flex justify-between text-sm">
        <span>
          {family.label}
          <span className="ml-2 text-xs text-muted">
            {family.direction === 'buy' ? '偏多' : family.direction === 'sell' ? '偏空' : '中性'}
          </span>
        </span>
        <span className="metric font-semibold text-primary">{family.score.toFixed(1)}</span>
      </div>
      <div className="relative mt-2 h-1.5 overflow-hidden rounded bg-elevated">
        <div className="h-full bg-primary" style={{ width: `${family.score}%` }} />
        <span
          className="absolute top-0 h-full border-l border-dashed border-muted"
          style={{ left: `${family.buy_threshold}%` }}
          title={`买入线 ${family.buy_threshold}`}
        />
        <span
          className="absolute top-0 h-full border-l border-dashed border-warning"
          style={{ left: `${family.sell_threshold}%` }}
          title={`卖出线 ${family.sell_threshold}`}
        />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {family.buy_rules.map((rule) => (
          <span key={`buy-${rule}`} className="chip border-positive/30 bg-positive/10 text-positive">
            {rule} · 买
          </span>
        ))}
        {family.sell_rules.map((rule) => (
          <span key={`sell-${rule}`} className="chip border-negative/30 bg-negative/10 text-negative">
            {rule} · 卖
          </span>
        ))}
        {family.buy_rules.length === 0 && family.sell_rules.length === 0 ? (
          <span className="text-xs text-muted">该族当前没有方向性规则命中</span>
        ) : null}
      </div>
    </div>
  )
}

type LedgerDraft = {
  ts_code: string
  side: 'buy' | 'sell'
  trade_date: string
  price: string
  shares: string
  stop_price: string
  note: string
}

const emptyDraft = (ts_code: string = DEFAULT_CODE, side: 'buy' | 'sell' = 'buy'): LedgerDraft => ({
  ts_code,
  side,
  trade_date: new Date().toISOString().slice(0, 10),
  price: '',
  shares: '',
  stop_price: '',
  note: '',
})

function PositionsSection({
  rows,
  isLoading,
  error,
  status,
  onStatus,
  code,
  expanded,
  onExpand,
  ledger,
  ledgerLoading,
  onChanged,
}: {
  rows: PositionAdvice[]
  isLoading: boolean
  error: Error | null
  status: string
  onStatus: (next: string) => void
  code: string
  expanded: string | null
  onExpand: (next: string | null) => void
  ledger: PositionTradeRow[]
  ledgerLoading: boolean
  onChanged: () => void
}) {
  const [draft, setDraft] = useState<LedgerDraft>(() => emptyDraft(code))

  const create = useMutation({
    mutationFn: api.addPositionTrade,
    onSuccess: () => {
      toast('已记录这笔流水')
      setDraft((prev) => emptyDraft(prev.ts_code, prev.side))
      onChanged()
    },
    // 服务端的 422（如卖出超过持仓）就发生在这一页上，必须原样显示而不是吞掉
    onError: (failure: Error) => toast(failure.message, 'error'),
  })
  const remove = useMutation({
    mutationFn: api.removePositionTrade,
    onSuccess: () => {
      toast('已删除该笔流水')
      onChanged()
    },
    onError: (failure: Error) => toast(failure.message, 'error'),
  })

  const fillFrom = (row: PositionAdvice, side: 'buy' | 'sell') =>
    setDraft({
      ts_code: row.ts_code,
      side,
      trade_date: new Date().toISOString().slice(0, 10),
      price: row.price == null ? '' : String(row.price),
      shares: side === 'sell' ? String(row.shares) : '',
      stop_price: row.stop_price == null ? '' : String(row.stop_price),
      note: '',
    })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const price = Number(draft.price)
    const shares = Number(draft.shares)
    if (!draft.ts_code.trim() || !(price > 0) || !(shares > 0)) {
      toast('标的、价格与股数都必须填写且大于 0', 'error')
      return
    }
    create.mutate({
      ts_code: draft.ts_code.trim().toUpperCase(),
      side: draft.side,
      trade_date: draft.trade_date,
      price,
      shares,
      stop_price: draft.stop_price === '' ? null : Number(draft.stop_price),
      note: draft.note,
    })
  }

  return (
    <section className="panel p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">持仓跟踪与调整建议</h2>
        <div className="flex items-center gap-1 rounded-xl border border-border p-1">
          {[
            ['holding', '持仓中'],
            ['closed', '已清仓'],
            ['all', '全部'],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => onStatus(value)}
              className={`rounded-lg px-3 py-1.5 text-xs ${status === value ? 'bg-primary/15 text-primary' : 'text-muted hover:text-foreground'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <form onSubmit={submit} className="mt-4 grid gap-3 md:grid-cols-4 xl:grid-cols-7">
        <label className="text-xs text-muted">
          标的
          <input
            value={draft.ts_code}
            onChange={(event) => setDraft({ ...draft, ts_code: event.target.value })}
            placeholder="600519.SH"
            className="mt-1 w-full rounded-xl border border-border bg-elevated/40 px-3 py-2 text-sm text-foreground"
          />
        </label>
        <label className="text-xs text-muted">
          方向
          <select
            value={draft.side}
            onChange={(event) => setDraft({ ...draft, side: event.target.value as 'buy' | 'sell' })}
            className="mt-1 w-full rounded-xl border border-border bg-elevated/40 px-3 py-2 text-sm text-foreground"
          >
            <option value="buy">买入</option>
            <option value="sell">卖出</option>
          </select>
        </label>
        <label className="text-xs text-muted">
          日期
          <input
            type="date"
            value={draft.trade_date}
            onChange={(event) => setDraft({ ...draft, trade_date: event.target.value })}
            className="mt-1 w-full rounded-xl border border-border bg-elevated/40 px-3 py-2 text-sm text-foreground"
          />
        </label>
        <label className="text-xs text-muted">
          价格
          <input
            type="number"
            step="0.01"
            value={draft.price}
            onChange={(event) => setDraft({ ...draft, price: event.target.value })}
            className="mt-1 w-full rounded-xl border border-border bg-elevated/40 px-3 py-2 text-sm text-foreground"
          />
        </label>
        <label className="text-xs text-muted">
          股数
          <input
            type="number"
            step="100"
            value={draft.shares}
            onChange={(event) => setDraft({ ...draft, shares: event.target.value })}
            className="mt-1 w-full rounded-xl border border-border bg-elevated/40 px-3 py-2 text-sm text-foreground"
          />
        </label>
        <label className="text-xs text-muted">
          止损位（可选）
          <input
            type="number"
            step="0.01"
            value={draft.stop_price}
            onChange={(event) => setDraft({ ...draft, stop_price: event.target.value })}
            className="mt-1 w-full rounded-xl border border-border bg-elevated/40 px-3 py-2 text-sm text-foreground"
          />
        </label>
        <div className="flex items-end">
          <button type="submit" disabled={create.isPending} className="button-primary w-full px-3 py-2 text-sm">
            {create.isPending ? '记录中…' : `记录${draft.side === 'buy' ? '买入' : '卖出'}`}
          </button>
        </div>
      </form>
      <p className="mt-2 text-xs text-muted">
        流水只存原始成交：股数、加权成本、浮盈、距止损全部实时重算，所以库里不会有变陈旧的计算值。
        自己填的止损会标记来源为「自定」，与回测规则的止损区分开。
      </p>

      {isLoading ? <Loading label="正在汇总持仓…" /> : null}
      {error ? <ErrorBox message={error.message} /> : null}

      {!isLoading && !error && rows.length === 0 ? (
        <EmptyState
          title="暂无流水记录"
          hint="用上面的表单记录一笔真实买入（或先看该股的买卖计划），持仓与建议会出现在这里。"
        />
      ) : null}

      {rows.length > 0 ? (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-muted">
              <tr className="border-b border-border text-left">
                <th className="py-2">标的</th>
                <th className="py-2">股数</th>
                <th className="py-2">加权成本</th>
                <th className="py-2">现价</th>
                <th className="py-2">浮动盈亏</th>
                <th className="py-2">已实现</th>
                <th className="py-2">持有</th>
                <th className="py-2">止损位</th>
                <th className="py-2">距止损</th>
                <th className="py-2">融合分</th>
                <th className="py-2">建议</th>
                <th className="py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.ts_code} className="border-b border-border/50 align-top">
                  <td className="py-2">
                    <Link to={`/strategy?code=${row.ts_code}`} className="hover:text-primary">
                      {row.name || row.ts_code}
                    </Link>
                    <p className="text-xs text-muted">{row.ts_code}</p>
                  </td>
                  <td className="metric py-2">
                    {row.shares}
                    <p className="text-xs text-muted">{row.trade_count} 笔</p>
                  </td>
                  <td className="metric py-2">{num(row.avg_cost)}</td>
                  <td className="metric py-2">{num(row.price)}</td>
                  <td className={`metric py-2 ${(row.pnl_pct ?? 0) < 0 ? 'text-negative' : 'positive'}`}>
                    {signedPct(row.pnl_pct)}
                    <p className="text-xs text-muted">{num(row.pnl, 0)} 元</p>
                  </td>
                  <td className="metric py-2">{num(row.realized_pnl, 0)}</td>
                  <td className="metric py-2">
                    {row.hold_bars} 天
                    <p className="text-xs text-muted">{row.first_entry_date ?? ''}</p>
                  </td>
                  <td className="metric py-2">
                    {num(row.stop_price)}
                    <p className="text-xs text-muted">
                      {row.stop_source === 'user' ? '自定' : row.stop_source === 'rule' ? '规则' : '无'}
                    </p>
                  </td>
                  <td className={`metric py-2 ${(row.stop_distance_pct ?? 0) < 0 ? 'text-warning' : ''}`}>
                    {signedPct(row.stop_distance_pct)}
                  </td>
                  <td className="metric py-2">{num(row.scores?.fusion.score, 1)}</td>
                  <td className="py-2">
                    <PositionAdviceTag advice={row} />
                    <ul className="mt-1 space-y-0.5 text-xs text-muted">
                      {row.reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </td>
                  <td className="py-2">
                    <div className="flex flex-col items-start gap-1 text-xs">
                      <button type="button" className="hover:text-primary" onClick={() => fillFrom(row, 'sell')}>
                        记录卖出
                      </button>
                      <button
                        type="button"
                        className="hover:text-primary"
                        title="按 1% 风险预算重新算一次仓位，不要因为「看好」放大单笔风险"
                        onClick={() => fillFrom(row, 'buy')}
                      >
                        记录加仓
                      </button>
                      <button
                        type="button"
                        className="hover:text-primary"
                        onClick={() => onExpand(expanded === row.ts_code ? null : row.ts_code)}
                      >
                        {expanded === row.ts_code ? '收起流水' : '查看流水'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {rows.map((row) =>
        row.data_warning ? (
          <div key={`warn-${row.ts_code}`} className="mt-2">
            <Notice tone="warning">
              <span>
                {row.name || row.ts_code}：{row.data_warning}
              </span>
            </Notice>
          </div>
        ) : null,
      )}

      {expanded ? (
        <div className="mt-5 rounded-xl border border-border p-4">
          <p className="text-sm font-medium">{expanded} 的流水明细</p>
          {ledgerLoading ? <Loading label="正在读取流水…" /> : null}
          {ledger.length === 0 && !ledgerLoading ? (
            <EmptyState title="该标的没有流水" hint="记录一笔买入后即可在这里看到并删除错录。" />
          ) : null}
          {ledger.length > 0 ? (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs text-muted">
                  <tr className="border-b border-border text-left">
                    <th className="py-2">日期</th>
                    <th className="py-2">方向</th>
                    <th className="py-2">价格</th>
                    <th className="py-2">股数</th>
                    <th className="py-2">费用</th>
                    <th className="py-2">止损位</th>
                    <th className="py-2">备注</th>
                    <th className="py-2">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.map((trade) => (
                    <tr key={trade.id} className="border-b border-border/50">
                      <td className="py-2">{trade.trade_date}</td>
                      <td className="py-2">
                        <span
                          className={`chip ${trade.side === 'buy' ? 'border-positive/30 bg-positive/10 text-positive' : 'border-negative/30 bg-negative/10 text-negative'}`}
                        >
                          {trade.side === 'buy' ? '买入' : '卖出'}
                        </span>
                      </td>
                      <td className="metric py-2">{num(trade.price)}</td>
                      <td className="metric py-2">{trade.shares}</td>
                      <td className="metric py-2">{num(trade.fee)}</td>
                      <td className="metric py-2">{num(trade.stop_price)}</td>
                      <td className="py-2 text-muted">{trade.note}</td>
                      <td className="py-2">
                        <button
                          type="button"
                          className="text-xs text-muted hover:text-negative"
                          onClick={() => remove.mutate(trade.id)}
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
