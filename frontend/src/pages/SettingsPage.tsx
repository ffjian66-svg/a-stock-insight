import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, KeyRound, MessageSquare, RefreshCw, Send, Wrench } from 'lucide-react'
import { api } from '../lib/api'
import { toast } from '../lib/toast'

/** 账本里的 kind → 人话。与后端 `notify.kind` 的六个取值一一对应。 */
const notifyKindLabels: Record<string, string> = {
  daily: '收盘日报',
  stop: '持仓触发',
  sync: '同步失败',
  sync_ok: '同步恢复',
  sync_off: '任务被禁用',
  test: '测试消息',
}

/**
 * 状态色。`sent` 用与"可用"同一套（本页 ready 也是这个色），`failed`/`pending` 用警告色：
 * `pending` 是"插了行但进程在发送前断了"——**没有送达**，它不该长得像成功。
 */
function notifyStatusStyle(status: string): string {
  if (status === 'sent') return 'border-negative/20 bg-negative/10 text-negative'
  if (status === 'failed' || status === 'pending') return 'border-warning/20 bg-warning/10 text-warning'
  return 'border-border text-muted'
}

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
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['status'], queryFn: api.status })
  const events = useQuery({ queryKey: ['notify-events'], queryFn: () => api.notifyEvents(20) })
  const sendTest = useMutation({
    mutationFn: api.notifyTest,
    onSuccess: (result) => {
      // `reused` 必须说出来：不说的话，界面会把上一行 `sent` 显示成"刚刚发成功"，
      // 而这一分钟其实一条都没发出去。
      if (result.reused) toast(result.detail, 'info')
      else if (result.status === 'sent') toast('测试消息已发出，请查看手机', 'success')
      else toast(result.detail, 'error')
      void queryClient.invalidateQueries({ queryKey: ['notify-events'] })
    },
    onError: (failure: Error) => toast(failure.message, 'error'),
  })
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
      Icon: MessageSquare,
      title: '微信推送',
      value: data?.notify_configured ? '已配置' : '未配置',
      ready: Boolean(data?.notify_configured),
    },
    {
      Icon: RefreshCw,
      title: '自选刷新周期',
      value: `${data?.quote_refresh_seconds ?? 30} 秒`,
      ready: true,
    },
  ]
  const capabilities = data?.capabilities ?? []
  const ledger = events.data ?? []

  return (
    <div className="space-y-6 p-5 pb-16 lg:p-8">
      <div>
        <p className="eyebrow">SYSTEM CONTROL</p>
        <h1 className="mt-2 text-3xl font-semibold">数据与模型设置</h1>
        <p className="mt-2 text-sm text-muted">密钥仅由本地后端环境读取，前端不会获取或显示密钥内容。</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
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
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <MessageSquare size={18} className="text-primary" />
            <h2 className="font-semibold">微信推送</h2>
          </div>
          <button
            className="chip border-primary/20 bg-primary/10 text-primary disabled:opacity-50"
            disabled={sendTest.isPending}
            onClick={() => sendTest.mutate()}
            type="button"
          >
            <Send size={14} /> {sendTest.isPending ? '发送中…' : '发送测试消息'}
          </button>
        </div>
        <p className="mt-3 text-sm leading-6 text-muted">
          每个交易日 19:00 推一条收盘日报（当天没有买入候选也照推，压成最短形态——沉默分不清是「推送坏了」「没同步」还是「真没候选」）；
          持仓收盘触及止损或移动止盈时另推一条告警；收盘同步失败、恢复、被禁用时各推一条。
          测试消息每分钟最多一条。
        </p>
        {data?.notify_configured ? null : (
          <p className="mt-3 text-sm leading-6 text-warning">
            未配置：在服务器 <code className="text-primary">.env</code> 里填{' '}
            <code className="text-primary">WECOM_WEBHOOK_URL</code>（企业微信群机器人地址）后重启服务。
            未配置时不会发送，账本里记为「未配置·未发送」，不是失败。
          </p>
        )}

        <div className="mt-5">
          <h3 className="text-sm text-muted">最近推送</h3>
          {events.isLoading ? (
            <p className="mt-2 text-sm text-muted">读取中…</p>
          ) : ledger.length === 0 ? (
            <p className="mt-2 text-sm text-muted">
              还没有推送记录。配好 webhook 后按上面的按钮发一条。账本留的是每次发出的原文，用来回看「我们到底告诉过你什么」。
            </p>
          ) : (
            <ul aria-label="最近推送" className="mt-3 space-y-2">
              {ledger.map((event) => (
                <li className="rounded-xl border border-border p-3 text-sm" key={event.id}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`chip ${notifyStatusStyle(event.status)}`}>{event.status_label}</span>
                    <span className="chip border-border text-muted">
                      {notifyKindLabels[event.kind] ?? event.kind}
                    </span>
                    <span className="text-xs text-muted">
                      {new Date(event.created_at).toLocaleString('zh-CN')}
                    </span>
                    <span className="text-xs text-muted">{event.byte_len} 字节</span>
                  </div>
                  {event.summary ? <p className="mt-2">{event.summary}</p> : null}
                  {event.error ? (
                    <p className="mt-1 text-xs text-warning">
                      {event.errcode != null ? `errcode ${event.errcode} · ` : ''}
                      {event.error}
                    </p>
                  ) : null}
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-muted">查看原文</summary>
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs leading-5 text-muted">
                      {event.content}
                    </pre>
                  </details>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="panel p-6">
        <h2 className="font-semibold">本地配置指南</h2>
        <ol className="mt-4 list-inside list-decimal space-y-3 text-sm leading-6 text-muted">
          <li>复制根目录 <code className="text-primary">.env.example</code> 为 <code className="text-primary">.env</code>。</li>
          <li>填写 TuShare Token 与兼容 OpenAI 的 LLM API 配置，请勿把密钥提交到版本库。</li>
          <li>将 <code className="text-primary">USE_MOCK_DATA</code> 改为 <code className="text-primary">false</code> 后重启后端。</li>
          <li>真实模式下，系统会在每个交易日 18:00 自动回填 ROE / 营收增长 / 净利增长 / 负债率。</li>
          <li>
            需要微信推送时：在企业微信里建一个只有自己的群 → 添加群机器人 → 复制 webhook 地址，填到{' '}
            <code className="text-primary">.env</code> 的 <code className="text-primary">WECOM_WEBHOOK_URL</code>。
            <span className="text-warning">改完必须重启后端</span>（配置只在启动时读入）。
            这个地址是凭据——拿到它的人能往该群发消息，请勿提交到版本库。
          </li>
        </ol>
      </section>
    </div>
  )
}
