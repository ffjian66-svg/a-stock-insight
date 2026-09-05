import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { DashboardPage } from './DashboardPage'
import { renderPage, server } from '../test/utils'

describe('DashboardPage', () => {
  it('渲染演示数据横幅、指数与自选股票', async () => {
    renderPage(<DashboardPage />)
    expect(await screen.findByText('演示数据源')).toBeInTheDocument()
    expect(screen.getByText('上证指数')).toBeInTheDocument()
    expect(screen.getByText('创业板指')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getAllByText('贵州茅台').length).toBeGreaterThanOrEqual(1)
    })
    expect(screen.getAllByText('宁德时代').length).toBeGreaterThanOrEqual(1)
  })

  it('自选与榜单为空时显示空态', async () => {
    server.use(
      http.get('/api/v1/watchlist', () => HttpResponse.json([])),
      http.get('/api/v1/screener/top', () => HttpResponse.json([])),
    )
    renderPage(<DashboardPage />)
    expect(await screen.findByText('自选列表为空')).toBeInTheDocument()
    expect(await screen.findByText('暂无榜单数据')).toBeInTheDocument()
  })

  it('数据服务异常时展示错误框', async () => {
    server.use(http.get('/api/v1/market/overview', () => HttpResponse.json({}, { status: 500 })))
    renderPage(<DashboardPage />)
    expect(await screen.findByText(/数据服务暂不可用/)).toBeInTheDocument()
  })
})
