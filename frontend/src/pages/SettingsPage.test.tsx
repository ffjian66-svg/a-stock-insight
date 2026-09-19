import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsPage } from './SettingsPage'
import { notifyTestResult, renderPage, server, status } from '../test/utils'
import { toast } from '../lib/toast'

vi.mock('../lib/toast', () => ({ toast: vi.fn(), useToasts: () => [] }))

/** 「微信推送」有两处标题（卡片 + 面板），断言卡片时按面板容器收窄。 */
async function notifyCard(): Promise<HTMLElement> {
  const [heading] = await screen.findAllByRole('heading', { name: '微信推送' })
  return heading.closest('.panel') as HTMLElement
}

// mock 是模块级的，调用会跨用例累积——不清掉，"没调用过成功提示"这类否定断言
// 会被前面用例的调用污染（失败形态看起来像功能坏了）。
beforeEach(() => {
  vi.mocked(toast).mockClear()
})

const postTest = () => server.use(http.post('/api/v1/notify/test', () => HttpResponse.json(notifyTestResult)))

describe('SettingsPage 微信推送', () => {
  it('卡片只说配没配，账本倒序列出推送并标出未送达的那条', async () => {
    renderPage(<SettingsPage />)

    // 卡片先渲染、`status` 后到，所以要等它翻到「已配置」
    expect(await within(await notifyCard()).findByText('已配置')).toBeInTheDocument()

    // 最新的一条在最前：19:00 的日报。按账本这个 list 取行——直接用文本查会把每行
    // 展开的原文（`<pre>` 里也是同一句话）也算进来，顺序就串了。
    const rows = within(await screen.findByRole('list', { name: '最近推送' })).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('明日操作 · 2026-09-18')
    expect(rows[1]).toHaveTextContent('同步失败 · 日线行情')
    expect(rows[2]).toHaveTextContent('推送测试')

    // `pending` = 插了行但进程在发送前断了。显示成「已送达」就是一次静默的谎言。
    expect(screen.getByText('未送达（进程中断）')).toBeInTheDocument()
    // 企业微信会 HTTP 200 + errcode 非 0，errcode 必须露出来，否则看不出密钥已失效
    expect(screen.getByText(/errcode 93000 · invalid webhook url/)).toBeInTheDocument()
    // 账本存的是原文，要能读回来
    expect(screen.getByText(/# 明日操作 · 2026-09-18/)).toBeInTheDocument()
  })

  it('未配置时明说去 .env 填什么，并且不说成"失败"', async () => {
    server.use(
      http.get('/api/v1/system/status', () => HttpResponse.json({ ...status, notify_configured: false })),
    )
    renderPage(<SettingsPage />)

    expect(await within(await notifyCard()).findByText('未配置')).toBeInTheDocument()
    expect(screen.getByText(/未配置时不会发送/)).toBeInTheDocument()
    // 面板提示与配置指南都会点名这个变量，所以是 getAll
    expect(screen.getAllByText(/WECOM_WEBHOOK_URL/).length).toBeGreaterThan(0)
    expect(screen.getByText(/记为「未配置·未发送」，不是失败/)).toBeInTheDocument()
  })

  it('配置指南写明凭据与"改完要重启"', async () => {
    renderPage(<SettingsPage />)
    expect(await screen.findByText(/改完必须重启后端/)).toBeInTheDocument()
    expect(screen.getByText(/请勿提交到版本库/)).toBeInTheDocument()
  })

  it('按测试按钮：发一条并把结果说出来', async () => {
    postTest()
    renderPage(<SettingsPage />)
    const button = await screen.findByRole('button', { name: /发送测试消息/ })

    await userEvent.click(button)

    await waitFor(() => expect(toast).toHaveBeenCalledWith('测试消息已发出，请查看手机', 'success'))
  })

  it('一分钟内重复按：说"没有重发"，绝不报成刚发成功', async () => {
    server.use(
      http.post('/api/v1/notify/test', () =>
        HttpResponse.json({
          ...notifyTestResult,
          reused: true,
          detail: '这一分钟内已经发过一条测试消息，本次没有重发（上面是那一条的结果）。',
        }),
      ),
    )
    renderPage(<SettingsPage />)
    const button = await screen.findByRole('button', { name: /发送测试消息/ })

    await userEvent.click(button)

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.stringContaining('本次没有重发'), 'info'),
    )
    expect(toast).not.toHaveBeenCalledWith('测试消息已发出，请查看手机', 'success')
  })

  it('企业微信拒绝时把 errcode 原话顶到界面上', async () => {
    server.use(
      http.post('/api/v1/notify/test', () =>
        HttpResponse.json({
          ...notifyTestResult,
          status: 'failed',
          status_label: '失败',
          errcode: 93000,
          error: 'invalid webhook url',
          detail: '没发出去：invalid webhook url',
        }),
      ),
    )
    renderPage(<SettingsPage />)
    const button = await screen.findByRole('button', { name: /发送测试消息/ })

    await userEvent.click(button)

    await waitFor(() => expect(toast).toHaveBeenCalledWith('没发出去：invalid webhook url', 'error'))
  })

  it('未配置时按钮拿到 400 的 detail，而不是一句"请求失败"', async () => {
    server.use(
      http.post('/api/v1/notify/test', () =>
        HttpResponse.json({ detail: '未配置微信推送：请在服务器 .env 里填 WECOM_WEBHOOK_URL…' }, { status: 400 }),
      ),
    )
    renderPage(<SettingsPage />)
    const button = await screen.findByRole('button', { name: /发送测试消息/ })

    await userEvent.click(button)

    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining('WECOM_WEBHOOK_URL'), 'error'))
  })
})
