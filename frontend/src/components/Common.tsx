import type { ReactNode } from 'react'
import { ArrowDownRight, ArrowUpRight, Inbox } from 'lucide-react'
import type { Timing } from '../lib/api'
import { useToasts } from '../lib/toast'

export function Change({ value }: { value: number | null }) {
  const positive = (value ?? 0) >= 0
  return (
    <span className={`metric inline-flex items-center gap-1 ${positive ? 'positive' : 'negative'}`}>
      {positive ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />} {positive ? '+' : ''}
      {(value ?? 0).toFixed(2)}%
    </span>
  )
}

export function Score({ value }: { value: number | null }) {
  const score = value ?? 0
  return (
    <div className="flex items-center gap-3">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-elevated">
        <div className="h-full rounded-full bg-primary" style={{ width: `${score}%` }} />
      </div>
      <span className="metric font-semibold">{value?.toFixed(0) ?? '--'}</span>
    </div>
  )
}

export function Loading({ label }: { label?: string }) {
  return (
    <div className="panel animate-pulse p-8 text-sm text-muted">{label ?? '正在汇总行情与因子数据…'}</div>
  )
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded-panel border border-warning/30 bg-warning/10 p-5 text-sm text-warning">
      {message}
    </div>
  )
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <Inbox size={28} className="text-muted/60" />
      <p className="text-sm font-medium">{title}</p>
      {hint ? <p className="max-w-md text-xs leading-5 text-muted">{hint}</p> : null}
    </div>
  )
}

export function Notice({ tone, children }: { tone: 'warning' | 'info'; children: ReactNode }) {
  const palette =
    tone === 'warning'
      ? 'border-warning/30 bg-warning/10 text-warning'
      : 'border-primary/30 bg-primary/10 text-foreground'
  return <div className={`flex items-center gap-2 rounded-xl border px-4 py-3 text-xs leading-5 ${palette}`}>{children}</div>
}

export function SentimentChip({ sentiment, eventTag }: { sentiment: number | null; eventTag: string }) {
  const tone =
    sentiment == null
      ? 'border-border text-muted'
      : sentiment > 0.15
        ? 'border-positive/30 bg-positive/10 text-positive'
        : sentiment < -0.15
          ? 'border-negative/30 bg-negative/10 text-negative'
          : 'border-warning/30 bg-warning/10 text-warning'
  const label = sentiment == null ? '中性' : sentiment > 0.15 ? '积极' : sentiment < -0.15 ? '消极' : '中性'
  return (
    <span className={`chip ${tone}`} title={sentiment == null ? '无情绪判定' : `情绪分 ${sentiment}`}>
      {eventTag || label}
    </span>
  )
}

export function TimingTag({ timing }: { timing: Timing | null | undefined }) {
  if (!timing) return null
  const palette =
    timing.tone === 'buy'
      ? 'border-positive/30 bg-positive/10 text-positive'
      : timing.tone === 'reduce'
        ? 'border-negative/30 bg-negative/10 text-negative'
        : timing.tone === 'watch'
          ? 'border-warning/30 bg-warning/10 text-warning'
          : 'border-border text-muted' // hold → 中性
  return (
    <span className={`chip whitespace-nowrap ${palette}`} title={timing.detail}>
      {timing.label}
    </span>
  )
}

export function Toasts() {
  const items = useToasts()
  if (items.length === 0) return null
  return (
    <div className="pointer-events-none fixed right-4 top-4 z-50 flex flex-col gap-2">
      {items.map((item) => (
        <div
          key={item.id}
          className={`pointer-events-auto rounded-xl border px-4 py-3 text-sm shadow-panel backdrop-blur-xl ${
            item.tone === 'error'
              ? 'border-positive/40 bg-positive/10 text-positive'
              : item.tone === 'info'
                ? 'border-border bg-elevated text-muted'
                : 'border-negative/40 bg-negative/10 text-negative'
          }`}
        >
          {item.message}
        </div>
      ))}
    </div>
  )
}
