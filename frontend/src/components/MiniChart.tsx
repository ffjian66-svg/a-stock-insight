import { useId } from 'react'
import { Area, AreaChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { SparkPoint } from '../lib/api'

const synthetic = Array.from({ length: 24 }, (_, i) => ({ date: `${i}`, close: 50 + Math.sin(i / 2.7) * 8 + i * 0.7 }))

export function MiniChart({ data, positive = true, height = 64 }: { data?: SparkPoint[]; positive?: boolean; height?: number }) {
  const gradientId = useId()
  const series = data && data.length >= 2 ? data : synthetic
  const tone = `var(--${positive ? 'positive' : 'negative'})`
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={series} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={tone} stopOpacity={0.32} />
            <stop offset="1" stopColor={tone} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Tooltip content={() => null} />
        <Area type="monotone" dataKey="close" stroke={tone} strokeWidth={1.8} fill={`url(#${gradientId})`} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
