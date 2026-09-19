import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { NextDayPicks } from './NextDayPicks'
import { pickRows, renderPage, server } from '../test/utils'

describe('NextDayPicks', () => {
  it('渲染次日买点候选行、buy 时机标签与基准日', async () => {
    renderPage(<NextDayPicks />)
    expect(await screen.findByRole('heading', { name: '次日买点候选' })).toBeInTheDocument()
    expect(await screen.findByText('基于 2026-09-02 收盘 · 下一交易日参考')).toBeInTheDocument()
    // 行内个股链接带来源（返回总览），茅台为回调买点、其余为沿 MA20 上行
    expect(screen.getByRole('link', { name: '贵州茅台' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '宁德时代' })).toBeInTheDocument()
    expect(screen.getByText('回调MA20(≈1280)，可分批')).toBeInTheDocument()
    // 参考理由列展示 timing.detail
    expect(screen.getByText(/现价 182\.00，MA20 1280\.0/)).toBeInTheDocument()
  })

  it('每行都能直接进该标的的买卖策略', async () => {
    renderPage(<NextDayPicks />)
    expect(await screen.findByRole('link', { name: '贵州茅台的买卖策略' })).toHaveAttribute(
      'href',
      '/strategy?code=600519.SH',
    )
  })

  it('盘后回退到收盘价时，价格下面标出「收盘」与日期', async () => {
    server.use(
      http.get('/api/v1/picks/daily', () =>
        HttpResponse.json({
          basis_date: '2026-09-16',
          note: '测试',
          picks: [{ ...pickRows[0], is_stale: true, price_source: 'close', price_date: '2026-09-16' }],
        }),
      ),
    )
    renderPage(<NextDayPicks />)
    expect(await screen.findByText('收盘 09-16')).toBeInTheDocument()
  })

  it('有实时报价时价格下面不出现「收盘」字样', async () => {
    renderPage(<NextDayPicks />)
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument()
    expect(screen.queryByText(/^收盘/)).not.toBeInTheDocument()
  })

  it('空名单时显示空态', async () => {
    server.use(
      http.get('/api/v1/picks/daily', () =>
        HttpResponse.json({ basis_date: null, note: '', picks: [] }),
      ),
    )
    renderPage(<NextDayPicks />)
    expect(await screen.findByText('暂无次日买点候选')).toBeInTheDocument()
  })

  it('接口异常时显示错误提示', async () => {
    server.use(
      http.get('/api/v1/picks/daily', () => HttpResponse.json({}, { status: 500 })),
    )
    renderPage(<NextDayPicks />)
    expect(await screen.findByText(/次日买点候选计算失败/)).toBeInTheDocument()
  })
})
