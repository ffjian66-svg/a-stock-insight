import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { PositionsBrief } from './PositionsBrief'
import { renderPage, server } from '../test/utils'

describe('PositionsBrief', () => {
  it('渲染持仓行：浮盈、距止损、建议与合计', async () => {
    renderPage(<PositionsBrief />)
    expect(await screen.findByRole('heading', { name: '我的持仓' })).toBeInTheDocument()

    // 建议 chip 与它的理由（title）都要在——首页只给结论等于要求用户盲信
    const hold = await screen.findByTitle(/融合分 54\.98/)
    expect(hold).toHaveTextContent('继续持有')
    expect(screen.getByTitle(/现价 231\.60 已跌破止损 238\.40/)).toHaveTextContent('跌破止损，清仓')

    // 浮盈：百分比与金额同时给（只给百分比会让小仓位的大涨幅看起来更重要）
    expect(screen.getByText('+4.85%')).toBeInTheDocument()
    expect(screen.getByText('-5.78%')).toBeInTheDocument()

    // 距止损：正常行给距离，已跌破的行给「已跌破」，用户自定的止损要标出来
    expect(screen.getByText('-3.05%')).toBeInTheDocument()
    expect(screen.getByText(/已跌破 · 自定/)).toBeInTheDocument()

    // 合计浮盈 14740 + (-4260) = 10480
    expect(screen.getByText(/\+10,480/)).toBeInTheDocument()

    // 缺日线的行降级提示，不能让整块打不开
    expect(screen.getByText('本地暂无该标的日线，请先同步数据')).toBeInTheDocument()
  })

  it('有需要处理的仓位时在顶部点名，并给出管理入口', async () => {
    renderPage(<PositionsBrief />)
    expect(await screen.findByText(/有 1 只需要处理：宁德时代（跌破止损，清仓）/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '管理持仓 →' })).toHaveAttribute('href', '/strategy')
  })

  it('没有持仓时给出去录入的入口，而不是空白', async () => {
    server.use(http.get('/api/v1/strategy/positions', () => HttpResponse.json([])))
    renderPage(<PositionsBrief />)
    expect(
      await screen.findByText(/还没有持仓记录。在买卖策略页录入一笔真实成交后/),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '去录入第一笔' })).toHaveAttribute('href', '/strategy')
    expect(screen.queryByText(/合计浮盈/)).not.toBeInTheDocument()
  })

  it('接口报错时展示错误框', async () => {
    server.use(
      http.get('/api/v1/strategy/positions', () =>
        HttpResponse.json({ detail: '持仓服务不可用' }, { status: 500 }),
      ),
    )
    renderPage(<PositionsBrief />)
    expect(await screen.findByText('持仓服务不可用')).toBeInTheDocument()
  })
})
