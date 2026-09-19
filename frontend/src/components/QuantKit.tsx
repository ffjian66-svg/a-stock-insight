/* eslint-disable react-refresh/only-export-components --
   这一条规则要的是"组件文件只导出组件"，好让 HMR 能安全地热替换。本文件的要点正相反：
   展示件与它们的格式化契约（`pct` 的空值口径、涨跌配色、`exit_reason` 中文标签）必须
   待在同一个文件里，才不会出现两份 `pct` 慢慢不一致。代价是这两个页面改了要走整页刷新，
   可以接受；代价换到的是契约只有一份。 */
/**
 * 量化页与买卖策略页共用的展示件。
 *
 * 抽出来的理由不是"少写几行"，而是**同一份契约只能有一个副本**：`pct`/`num` 的空值口径、
 * 涨跌配色、`exit_reason` 的中文标签，如果两边各写一份，两边就会慢慢不一致——上一轮已经
 * 吃过「前后端契约靠 fixture 复制导致静默漂移」的亏，前端内部同理。
 *
 * 这里的组件全部是**纯展示**，不取数、不含业务规则。
 */
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { EmptyState } from './Common'
import type { PositionAdvice, QuantMetrics, QuantTrade, SignalDirection } from '../lib/api'

export const tooltipStyle = {
  background: 'hsl(var(--elevated))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 12,
  fontSize: 12,
}

/** 比率与收益率都是小数 → 百分比；样本不足（null）统一显示 `--`。 */
export function pct(value: number | null | undefined, digits = 2) {
  return value == null ? '--' : `${(value * 100).toFixed(digits)}%`
}

export function num(value: number | null | undefined, digits = 2) {
  return value == null ? '--' : value.toFixed(digits)
}

/**
 * **已经是百分数**的值（`expectancy_pct` = −2.31 表示 −2.31%）。
 *
 * 与 `pct` 一字之差但单位完全不同：`pct` 乘 100，本函数不乘。后端同时有这两类字段
 * （`metrics.cumulative_return` 是小数 0.12，`expectancy_pct` 是 −2.31），混用会让页面上
 * 出现"−231%"这种一眼看不出的错，所以刻意不合并成一个函数。**始终带正负号**——
 * 期望值是正是负正是这一页要说的第一件事。
 */
export function signedPct(value: number | null | undefined, digits = 2) {
  if (value == null) return '--'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
}

/** 回测 params 是 Record<string, unknown>，取数值字段得先收窄类型。 */
export function paramNumber(params: Record<string, unknown>, key: string) {
  const value = params[key]
  return typeof value === 'number' ? value : null
}

export const DIRECTION_STYLE: Record<SignalDirection, string> = {
  // A 股配色：红涨绿跌，买入=红、卖出=绿
  buy: 'border-positive/30 bg-positive/10 text-positive',
  sell: 'border-negative/30 bg-negative/10 text-negative',
  neutral: 'border-border text-muted',
}

/**
 * 出场原因的标签。**四种都要有**——`trail` 是移动止盈，`end` 是样本末尾被强制平仓
 * （不是规则触发的出场），把它们混成"信号/结束"会让用户以为自己看到的是策略的
 * 自主出场，从而高估规则的有效性。
 */
export const EXIT_REASON_LABELS: Record<string, string> = {
  stop: '止损',
  trail: '移动止盈',
  end: '样本结束（强制平仓）',
  signal: '信号',
}

export function exitReasonLabel(reason: string) {
  return EXIT_REASON_LABELS[reason] ?? reason
}

/**
 * 持仓建议标签。1:1 照 `Common.TimingTag` 的写法，但配色按**建议的紧急程度**排：
 * 清仓用 `negative`（绿），加仓用 `positive`（红）——A 股配色下"红"是有利方向。
 * 刻意不复用 `TimingTag`：那个的 `tone` 词表（buy/reduce/watch/hold）与本处的
 * `action` 词表（exit/reduce/hold/add/watch）不是同一套，硬套会让两者都变糊。
 */
const ADVICE_STYLE: Record<string, string> = {
  add: 'border-positive/30 bg-positive/10 text-positive',
  exit: 'border-negative/30 bg-negative/10 text-negative',
  reduce: 'border-warning/30 bg-warning/10 text-warning',
  hold: 'border-border text-muted',
  watch: 'border-border text-muted',
}

export function PositionAdviceTag({ advice }: { advice: Pick<PositionAdvice, 'action' | 'action_label' | 'reasons'> }) {
  const palette = ADVICE_STYLE[advice.action] ?? 'border-border text-muted'
  return (
    <span className={`chip whitespace-nowrap ${palette}`} title={advice.reasons.join('；')}>
      {advice.action_label}
    </span>
  )
}

export function MetricCard({
  label,
  value,
  hint,
  tone = '',
}: {
  label: string
  value: string
  hint?: string
  tone?: string
}) {
  return (
    <div className="rounded-xl border border-border bg-elevated/40 px-4 py-3" title={hint}>
      <p className="text-xs text-muted">{label}</p>
      <p className={`metric mt-1 text-lg font-semibold ${tone}`}>{value}</p>
    </div>
  )
}

/**
 * 结论 chip 的配色。从 `StrategyPage` 提到这里（**类名字符串逐字照搬**，两个页面共用一份）：
 * 「观望」是黄、「不建议买入」是绿——A 股配色下绿是不利方向。改配色等于同时改两个页面，
 * 而 `StrategyPage.test.tsx` 正断言着 `border-negative`/`text-positive` 的在场与缺席。
 */
export const ACTION_STYLE: Record<'buy' | 'watch' | 'avoid', string> = {
  buy: 'border-positive/30 bg-positive/10 text-positive',
  watch: 'border-warning/30 bg-warning/10 text-warning',
  avoid: 'border-negative/30 bg-negative/10 text-negative',
}

export const VERDICT_STYLE: Record<string, string> = {
  negative: 'border-negative/30 bg-negative/10 text-negative',
  insufficient: 'border-warning/30 bg-warning/10 text-warning',
  // positive **刻意不用庆祝性配色**：样本内正期望不构成任何买入理由，颜色不该暗示"可以买"
  positive: 'border-border text-muted',
}

export const VERDICT_LABELS: Record<string, string> = {
  negative: '实测期望为负',
  insufficient: '样本不足',
  positive: '实测期望为正',
}

/** 结论列的**文字**配色。`VERDICT_STYLE` 那套是带底色的 chip，用在表格里会是一堆色块。 */
export const VERDICT_TONE: Record<string, string> = {
  negative: 'text-negative',
  insufficient: 'text-warning',
  // 与 VERDICT_STYLE.positive 同一个理由：样本内正期望不构成买入理由，不该用庆祝色
  positive: 'text-muted',
}

export function MetricGrid({ metrics }: { metrics: QuantMetrics }) {
  const cards: Array<[string, string, string]> = [
    ['累计收益', pct(metrics.cumulative_return), '区间首尾收盘价之比'],
    ['年化收益', pct(metrics.annualized_return), '按 252 交易日几何年化'],
    ['年化波动', pct(metrics.annualized_volatility), '日收益率标准差 × √252'],
    ['夏普比率', num(metrics.sharpe), '超额收益 / 年化波动'],
    ['索提诺比率', num(metrics.sortino), '只惩罚下行波动'],
    ['最大回撤', pct(metrics.max_drawdown), '相对历史最高收盘'],
    [
      '回撤天数',
      metrics.max_drawdown_days == null ? '--' : `${metrics.max_drawdown_days}`,
      '峰值到修复的交易日数',
    ],
    ['卡玛比率', num(metrics.calmar), '年化收益 / 最大回撤'],
    ['日胜率', pct(metrics.win_rate, 1), '上涨交易日占比'],
    ['Beta', num(metrics.beta), '相对上证指数'],
    ['Alpha', pct(metrics.alpha), '年化超额收益'],
    ['相关性', num(metrics.correlation), '与上证指数的相关系数'],
  ]
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
      {cards.map(([label, value, hint]) => (
        <MetricCard key={label} label={label} value={value} hint={hint} />
      ))}
    </div>
  )
}

export function BacktestEquityChart({ data }: { data: Array<Record<string, unknown>> }) {
  return (
    <div className="mt-6 h-72">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" opacity={0.4} />
          <XAxis dataKey="trade_date" tick={{ fontSize: 11 }} minTickGap={40} />
          <YAxis tick={{ fontSize: 11 }} width={64} domain={['auto', 'auto']} />
          <Tooltip contentStyle={tooltipStyle} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Line
            dataKey="benchmark"
            name="买入持有（基准）"
            stroke="hsl(var(--muted))"
            strokeDasharray="4 4"
            dot={false}
            strokeWidth={1.4}
          />
          <Line
            dataKey="equity"
            name="策略净值"
            stroke="hsl(var(--primary))"
            dot={false}
            strokeWidth={1.8}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

export function TradeTable({ trades }: { trades: QuantTrade[] }) {
  if (trades.length === 0) {
    return <EmptyState title="该区间未触发交易" hint="换一个策略或拉长窗口再看看。" />
  }
  return (
    <div className="mt-6 overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs text-muted">
          <tr className="border-b border-border text-left">
            <th className="py-2">买入日</th>
            <th className="py-2">买入价</th>
            <th className="py-2">股数</th>
            <th className="py-2">卖出日</th>
            <th className="py-2">卖出价</th>
            <th className="py-2">持有</th>
            <th className="py-2">盈亏</th>
            <th className="py-2">原因</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade) => (
            <tr
              key={`${trade.entry_date}-${trade.exit_date}-${trade.entry_price}`}
              className="border-b border-border/50"
            >
              <td className="py-2">{trade.entry_date}</td>
              <td className="metric py-2">{trade.entry_price.toFixed(2)}</td>
              <td className="metric py-2">{trade.shares}</td>
              <td className="py-2">{trade.exit_date}</td>
              <td className="metric py-2">{trade.exit_price.toFixed(2)}</td>
              <td className="py-2">{trade.hold_bars} 天</td>
              <td className={`metric py-2 ${trade.pnl_pct >= 0 ? 'positive' : 'negative'}`}>
                {trade.pnl_pct >= 0 ? '+' : ''}
                {trade.pnl_pct.toFixed(2)}%
              </td>
              <td className="py-2 text-muted">{exitReasonLabel(trade.exit_reason)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
