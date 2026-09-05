import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { Stock } from '../lib/api'
import { ScreenerPage } from './ScreenerPage'
import { renderPage, server, stocks } from '../test/utils'

describe('ScreenerPage', () => {
  it('渲染候选并统计数量', async () => {
    renderPage(<ScreenerPage />)
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument()
    expect(screen.getByText('宁德时代')).toBeInTheDocument()
    expect(screen.getByText(/当前 3 个候选/)).toBeInTheDocument()
  })

  it('点击 + 触发加入自选请求', async () => {
    const user = userEvent.setup()
    const posted: string[] = []
    server.use(
      http.post('/api/v1/watchlist', async ({ request }) => {
        const body = (await request.json()) as { ts_code?: string }
        posted.push(body.ts_code ?? '')
        return HttpResponse.json({ message: '已加入自选' })
      }),
    )
    renderPage(<ScreenerPage />)
    const button = await screen.findByRole('button', { name: '加入贵州茅台自选' })
    await user.click(button)
    await waitFor(() => expect(posted).toEqual(['600519.SH']))
  })

  it('无候选时展示空态', async () => {
    server.use(http.get('/api/v1/screener', () => HttpResponse.json([])))
    renderPage(<ScreenerPage />)
    expect(await screen.findByText('没有符合条件的研究候选')).toBeInTheDocument()
  })

  it('展示带操作时机标签的候选', async () => {
    const tagged: Stock[] = [
      {
        ...stocks[0],
        timing: { label: '现价可分批', tone: 'buy', detail: '现价 182.00，MA20 180.00 — 现价可分批' },
      },
      ...stocks.slice(1),
    ]
    server.use(http.get('/api/v1/screener', () => HttpResponse.json(tagged)))
    renderPage(<ScreenerPage />)
    const chip = await screen.findByText('现价可分批')
    expect(chip).toBeInTheDocument()
    expect(chip.closest('span')).toHaveAttribute('title', '现价 182.00，MA20 180.00 — 现价可分批')
    // 未带标签的候选显示占位符
    expect(screen.getAllByText('--').length).toBeGreaterThan(0)
  })

  it('从 URL 读取筛选条件并回填控件（从详情返回时还原）', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/screener?min=75&industry=白酒']}>
          <ScreenerPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByLabelText('最低综合评分')).toHaveValue(75)
    expect(screen.getByLabelText('行业')).toHaveValue('白酒')
    unmount()
  })
})
