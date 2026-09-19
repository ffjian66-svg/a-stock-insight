import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TopBoard } from './TopBoard'
import { renderPage, server } from '../test/utils'

describe('TopBoard', () => {
  it('渲染榜单行与最新新闻简报', async () => {
    renderPage(<TopBoard />)
    expect(await screen.findByRole('heading', { name: '综合评分大屏' })).toBeInTheDocument()
    // 榜单行与行内个股链接
    expect(await screen.findByRole('link', { name: '贵州茅台' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '宁德时代' })).toBeInTheDocument()
    // 有 llm/demo 简报的行渲染事件标签，无新闻的行落 '--'
    expect(await screen.findByText('积极')).toBeInTheDocument()
    expect(screen.getByText('订单')).toBeInTheDocument()
    expect(screen.getByText('消费旺季临近，龙头酒企渠道库存保持稳健')).toBeInTheDocument()
  })

  it('头部聚合展示均分与新闻覆盖只数', async () => {
    renderPage(<TopBoard />)
    // (82 + 74 + 68) / 3 = 74.7；三行 fixture 的 sentiment_score 均非空 → 3/3
    expect(await screen.findByText('74.7')).toBeInTheDocument()
    expect(await screen.findByText('3 / 3')).toBeInTheDocument()
  })

  it('可切换 10/20/50 触发重新拉取', async () => {
    const user = userEvent.setup()
    renderPage(<TopBoard />)
    const ten = await screen.findByRole('button', { name: '前 10' })
    const fifty = screen.getByRole('button', { name: '前 50' })
    expect(fifty).toHaveAttribute('aria-pressed', 'true')
    await user.click(ten)
    await waitFor(() => expect(ten).toHaveAttribute('aria-pressed', 'true'))
    expect(fifty).toHaveAttribute('aria-pressed', 'false')
  })

  it('每行都能直接进该标的的买卖策略', async () => {
    renderPage(<TopBoard />)
    expect(await screen.findByRole('link', { name: '贵州茅台的买卖策略' })).toHaveAttribute(
      'href',
      '/strategy?code=600519.SH',
    )
  })

  it('榜单为空时显示空态', async () => {
    server.use(http.get('/api/v1/screener/top', () => HttpResponse.json([])))
    renderPage(<TopBoard />)
    expect(await screen.findByText('暂无榜单数据')).toBeInTheDocument()
  })

  it('接口异常时显示错误提示', async () => {
    server.use(
      http.get('/api/v1/screener/top', () => HttpResponse.json({}, { status: 500 })),
    )
    renderPage(<TopBoard />)
    expect(await screen.findByText(/榜单数据加载失败/)).toBeInTheDocument()
  })
})
