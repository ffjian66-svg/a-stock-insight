import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { renderPage } from '../test/utils'
import { StockDetailPage } from './StockDetailPage'

// 从「智能选股」push 进详情的双条目历史（initialIndex=1 → 落在详情，key≠default）
function renderDetailFromScreener() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[{ pathname: '/screener' }, { pathname: '/stock/600519.SH', state: { from: '/screener' } }]}
        initialIndex={1}
      >
        <Routes>
          <Route path="/screener" element={<div>SCREENER_STUB</div>} />
          <Route path="/stock/:code" element={<StockDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('StockDetailPage', () => {
  it('展示评分证据、覆盖率与规则版本', async () => {
    renderPage(<StockDetailPage />)
    expect(await screen.findByRole('heading', { name: '贵州茅台' })).toBeInTheDocument()
    expect(await screen.findByText('覆盖率 100%')).toBeInTheDocument()
    expect(await screen.findByText('规则 v1')).toBeInTheDocument()
    expect(screen.getByText('趋势与动量')).toBeInTheDocument()
    expect(screen.getByText('质量与估值')).toBeInTheDocument()
  })

  it('展示新闻情绪、置信度与模型徽标', async () => {
    renderPage(<StockDetailPage />)
    expect(
      await screen.findByText('消费旺季临近，龙头酒企渠道库存保持稳健'),
    ).toBeInTheDocument()
    expect(await screen.findByText('模型 vgpt-x 分析')).toBeInTheDocument()
    expect(screen.getByText('积极')).toBeInTheDocument()
    expect(screen.getByText('置信 80%')).toBeInTheDocument()
  })

  it('从智能选股进入个股后，点「返回智能选股」回到来源页', async () => {
    const user = userEvent.setup()
    renderDetailFromScreener()
    const back = await screen.findByRole('button', { name: '返回智能选股' })
    await user.click(back)
    expect(await screen.findByText('SCREENER_STUB')).toBeInTheDocument()
  })

  it('直接打开详情（无站内历史）时，展示「返回市场总览」安全链接', async () => {
    renderPage(<StockDetailPage />)
    expect(await screen.findByRole('link', { name: '返回市场总览' })).toBeInTheDocument()
  })
})
