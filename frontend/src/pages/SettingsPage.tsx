import { useQuery } from '@tanstack/react-query'
import { Database, KeyRound, RefreshCw, Wrench } from 'lucide-react'
import { api } from '../lib/api'

const capabilityLabels: Record<string, string> = {
  history: '历史行情',
  quotes: '实时报价',
  news: '资讯检索',
  search: '股票搜索',
  indexes: '指数行情',
  index_history: '指数走势',
  financials: '财务数据回填',
  trade_calendar: '交易日历',
  fundamentals: '财务数据回填',
}

export function SettingsPage() {
  const { data } = useQuery({ queryKey: ['status'], queryFn: api.status })
  const cards = [
    {
      Icon: Database,
      title: '行情数据源',
      value: data?.provider ?? '--',
      ready: Boolean(data?.tushare_configured || data?.mock_mode),
      readyText: data?.mock_mode ? '演示模式' : '已配置',
    },
    {
      Icon: KeyRound,
      title: 'LLM 新闻分析',
      value: data?.llm_configured ? '已配置' : '未配置',
      ready: Boolean(data?.llm_configured),
    },
    {
      Icon: RefreshCw,
      title: '自选刷新周期',
      value: `${data?.quote_refresh_seconds ?? 30} 秒`,
      ready: true,
    },
  ]
  const capabilities = data?.capabilities ?? []

  return (
    <div className="space-y-6 p-5 pb-16 lg:p-8">
      <div>
        <p className="eyebrow">SYSTEM CONTROL</p>
        <h1 className="mt-2 text-3xl font-semibold">数据与模型设置</h1>
        <p className="mt-2 text-sm text-muted">密钥仅由本地后端环境读取，前端不会获取或显示密钥内容。</p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {cards.map(({ Icon, title, value, ready, readyText }) => (
          <div className="panel p-5" key={title}>
            <div className="flex items-start justify-between">
              <div className="grid size-10 place-items-center rounded-xl bg-primary/10 text-primary">
                <Icon size={19} />
              </div>
              <span className={`chip ${ready ? 'border-negative/20 bg-negative/10 text-negative' : 'border-warning/20 bg-warning/10 text-warning'}`}>
                {readyText ?? (ready ? '可用' : '待配置')}
              </span>
            </div>
            <h2 className="mt-5 font-semibold">{title}</h2>
            <p className="mt-1 text-sm text-muted">{value}</p>
          </div>
        ))}
      </div>

      <section className="panel p-6">
        <div className="flex items-center gap-2">
          <Wrench size={18} className="text-primary" />
          <h2 className="font-semibold">运行能力</h2>
        </div>
        <div className="mt-4 space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted">数据模式</span>
            <span className="chip border-primary/20 bg-primary/10 text-primary">
              {data?.mock_mode ? '演示（合成数据）' : data?.data_mode === 'live' ? '实时' : '未配置'}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted">Provider 支持</span>
            {capabilities.length === 0 ? (
              <span className="chip border-border text-muted">未知</span>
            ) : (
              capabilities.map((capability) => (
                <span className="chip border-border text-muted" key={capability}>
                  {capabilityLabels[capability] ?? capability}
                </span>
              ))
            )}
          </div>
          {data?.provider_status_at ? (
            <p className="text-xs text-muted">最近 Provider 探活 {new Date(data.provider_status_at).toLocaleString('zh-CN')}</p>
          ) : null}
        </div>
      </section>

      <section className="panel p-6">
        <h2 className="font-semibold">本地配置指南</h2>
        <ol className="mt-4 list-inside list-decimal space-y-3 text-sm leading-6 text-muted">
          <li>复制根目录 <code className="text-primary">.env.example</code> 为 <code className="text-primary">.env</code>。</li>
          <li>填写 TuShare Token 与兼容 OpenAI 的 LLM API 配置，请勿把密钥提交到版本库。</li>
          <li>将 <code className="text-primary">USE_MOCK_DATA</code> 改为 <code className="text-primary">false</code> 后重启后端。</li>
          <li>真实模式下，系统会在每个交易日 18:00 自动回填 ROE / 营收增长 / 净利增长 / 负债率。</li>
        </ol>
      </section>
    </div>
  )
}
