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
    // 次日买点候选区块随总览渲染
    expect(await screen.findByRole('heading', { name: '次日买点候选' })).toBeInTheDocument()
  })

  it('自选、榜单与买点候选为空时显示空态', async () => {
    server.use(
      http.get('/api/v1/watchlist', () => HttpResponse.json([])),
      http.get('/api/v1/screener/top', () => HttpResponse.json([])),
      http.get('/api/v1/picks/daily', () =>
        HttpResponse.json({ basis_date: null, note: '', picks: [] }),
      ),
    )
    renderPage(<DashboardPage />)
    expect(await screen.findByText('自选列表为空')).toBeInTheDocument()
    expect(await screen.findByText('暂无榜单数据')).toBeInTheDocument()
    expect(await screen.findByText('暂无次日买点候选')).toBeInTheDocument()
  })

  it('数据服务异常时展示错误框', async () => {
    server.use(http.get('/api/v1/market/overview', () => HttpResponse.json({}, { status: 500 })))
    renderPage(<DashboardPage />)
    expect(await screen.findByText(/数据服务暂不可用/)).toBeInTheDocument()
  })

  it('首页带上持仓摘要与逐行的买卖策略入口', async () => {
    renderPage(<DashboardPage />)
    expect(await screen.findByRole('heading', { name: '我的持仓' })).toBeInTheDocument()

    // 三块列表都要先渲染出来，否则下面的计数会跟渲染时序赛跑
    await screen.findByRole('heading', { name: '次日买点候选' })
    await screen.findByRole('heading', { name: '综合评分大屏' })

    // 首页上同一只票出现在自选表、次日候选、评分榜三处，每处都给了入口，
    // 所以是 getAllByRole——**而且每一条都必须指向同一个带该标的代码的地址**：
    // 三个入口指向两个不同的地方，才是真的会让人点错。
    const maotai = screen.getAllByRole('link', { name: '贵州茅台的买卖策略' })
    expect(maotai.length).toBeGreaterThanOrEqual(3)
    expect(maotai.every((link) => link.getAttribute('href') === '/strategy?code=600519.SH')).toBe(true)

    const catl = screen.getAllByRole('link', { name: '宁德时代的买卖策略' })
    expect(catl.every((link) => link.getAttribute('href') === '/strategy?code=300750.SZ')).toBe(true)

    // 原来的个股页入口不能被挤掉
    expect(screen.getByRole('link', { name: '查看贵州茅台' })).toHaveAttribute(
      'href',
      '/stock/600519.SH',
    )
  })
})
