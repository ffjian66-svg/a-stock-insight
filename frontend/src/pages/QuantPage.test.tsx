import { http, HttpResponse } from 'msw'
import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { QuantPage } from './QuantPage'
import { renderPage, server } from '../test/utils'

describe('QuantPage', () => {
  it('渲染指标、信号、绩效、回测与因子榜', async () => {
    renderPage(<QuantPage />)
    expect(await screen.findByText('量化分析')).toBeInTheDocument()

    // 信号面板：chip 文案由 label + 可选风控价位两段拼成，用 title 定位更稳
    const buyChip = await screen.findByTitle('短期均线上穿中期均线')
    expect(buyChip).toHaveTextContent('MA5 上穿 MA20')
    expect(screen.getByTitle('跌破该价位减仓')).toHaveTextContent('ATR 风控位')

    // 绩效卡：比率按小数→百分比渲染，样本不足才显示 --
    expect((await screen.findAllByText('夏普比率')).length).toBeGreaterThan(0)
    expect(screen.getByText('0.72')).toBeInTheDocument()
    expect(screen.getByText('-15.30%')).toBeInTheDocument()

    // 回测：统计卡按后端 `_trade_stats` 的键名取值（曾整体读成 0）
    expect(await screen.findByText(/未复权/)).toBeInTheDocument()
    expect(screen.getByText('交易次数').nextElementSibling).toHaveTextContent('2')
    expect(screen.getByText('2026-05-06')).toBeInTheDocument()
    expect(screen.getAllByText('+8.00%').length).toBeGreaterThan(0)

    // 因子榜：定义列 + 综合分位
    expect(await screen.findByText('20日动量')).toBeInTheDocument()
    expect(screen.getAllByText('82.4').length).toBeGreaterThan(0)
  })

  it('样本不足的绩效指标显示为 -- 而不是 0', async () => {
    server.use(
      http.get('/api/v1/quant/performance/:code', () =>
        HttpResponse.json({
          ts_code: '600519.SH',
          name: '贵州茅台',
          window_days: 250,
          benchmark: '',
          metrics: { bars: 12, sharpe: null, max_drawdown: null, calmar: null },
          equity: [],
        }),
      ),
    )
    renderPage(<QuantPage />)
    expect((await screen.findAllByText('夏普比率')).length).toBeGreaterThan(0)
    // 关键：null 不能退化成 0 或 NaN
    expect(screen.getAllByText('--').length).toBeGreaterThan(0)
    expect(screen.queryByText('NaN')).not.toBeInTheDocument()
    expect(screen.queryByText('NaN%')).not.toBeInTheDocument()
  })

  it('无因子快照时给出空态提示', async () => {
    server.use(
      http.get('/api/v1/quant/factors', () =>
        HttpResponse.json({
          definitions: [],
          groups: [],
          group_weights: {},
          rule_version: '',
          rows: [],
        }),
      ),
    )
    renderPage(<QuantPage />)
    expect(await screen.findByText('暂无因子快照')).toBeInTheDocument()
  })

  it('扫描无信号时展示空态', async () => {
    server.use(http.get('/api/v1/quant/scan', () => HttpResponse.json([])))
    renderPage(<QuantPage />)
    expect(await screen.findByText('当前没有触发信号')).toBeInTheDocument()
  })

  it('接口报错时展示错误信息', async () => {
    server.use(
      http.get('/api/v1/quant/series/:code', () =>
        HttpResponse.json({ detail: '未找到该股票' }, { status: 404 }),
      ),
    )
    renderPage(<QuantPage />)
    expect(await screen.findByText('未找到该股票')).toBeInTheDocument()
  })

  it('切换窗口会重新按天数请求', async () => {
    const requested: string[] = []
    server.use(
      http.get('/api/v1/quant/series/:code', ({ request }) => {
        requested.push(new URL(request.url).searchParams.get('days') ?? '')
        return HttpResponse.json({
          ts_code: '600519.SH',
          name: '贵州茅台',
          industry: '白酒',
          window_days: 120,
          bars: 0,
          indicators: [],
          signals: [],
          signal_score: null,
          latest: {},
        })
      }),
    )
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    renderPage(<QuantPage />)
    await user.click(await screen.findByRole('button', { name: '120天' }))
    expect(requested).toContain('120')
  })

  it('买卖策略入口带上当前标的与窗口，切换后不用重填', async () => {
    renderPage(<QuantPage />, ['/?code=300750.SZ&days=120'])
    const link = await screen.findByRole('link', { name: /买卖策略/ })
    expect(link).toHaveAttribute('href', '/strategy?code=300750.SZ&days=120')
  })
})
