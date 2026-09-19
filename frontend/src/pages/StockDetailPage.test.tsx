import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { renderPage } from '../test/utils'
import { StockDetailPage } from './StockDetailPage'

// 从某个来源页 push 进详情的双条目历史（initialIndex=1 → 落在详情，key≠default）
function renderDetailFrom(from: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[{ pathname: from }, { pathname: '/stock/600519.SH', state: { from } }]}
        initialIndex={1}
      >
        <Routes>
          <Route path={from} element={<div>ORIGIN_STUB</div>} />
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

  it('提供跳转量化分析的入口', async () => {
    renderPage(<StockDetailPage />)
    const link = await screen.findByRole('link', { name: /量化分析/ })
    expect(link).toHaveAttribute('href', '/quant?code=600519.SH')
  })

  it('提供跳转买卖策略的入口，带上当前标的', async () => {
    renderPage(<StockDetailPage />)
    const link = await screen.findByRole('link', { name: /买卖策略/ })
    expect(link).toHaveAttribute('href', '/strategy?code=600519.SH')
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
    renderDetailFrom('/screener')
    const back = await screen.findByRole('button', { name: '返回智能选股' })
    await user.click(back)
    expect(await screen.findByText('ORIGIN_STUB')).toBeInTheDocument()
  })

  it('从「明日操作」进入个股后，返回按钮说得出「返回明日操作」', async () => {
    renderDetailFrom('/tomorrow')

    // 「明日操作」的行一直在传 `state={{ from: '/tomorrow' }}`，但 ORIGIN_NAMES 里缺这一条，
    // 返回按钮于是退化成泛化的「返回」——用户看不出这是要回到那张 250 行的长表。
    expect(await screen.findByRole('button', { name: '返回明日操作' })).toBeInTheDocument()
  })

  it('直接打开详情（无站内历史）时，展示「返回市场总览」安全链接', async () => {
    renderPage(<StockDetailPage />)
    expect(await screen.findByRole('link', { name: '返回市场总览' })).toBeInTheDocument()
  })
})
