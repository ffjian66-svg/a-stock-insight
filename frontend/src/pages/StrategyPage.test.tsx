import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { StrategyPage } from './StrategyPage'
import {
  expectancyBlock,
  poolStats,
  poolStatsRows,
  positionRows,
  renderPage,
  server,
  strategyPlan,
} from '../test/utils'

describe('StrategyPage', () => {
  it('渲染买卖计划：结论、入场条件、价格梯、仓位与三个族分', async () => {
    renderPage(<StrategyPage />)
    expect(await screen.findByText('买卖策略')).toBeInTheDocument()

    // 结论 chip 与理由
    expect(screen.getByText('不建议买入')).toBeInTheDocument()
    expect(screen.getByText(/信号已触发，但该规则在此标的实测期望为负/)).toBeInTheDocument()

    // 入场条件 5 条都要在屏上，未满足的不能藏起来
    expect(screen.getByText('融合分 ≥ 62')).toBeInTheDocument()
    expect(screen.getByText('族确认（族分 ≥ 65 且 ≥ 2 条规则同向）')).toBeInTheDocument()
    expect(screen.getByText('实测期望不为负且样本 ≥ 20 笔')).toBeInTheDocument()
    expect(screen.getByText('现价高于建议止损')).toBeInTheDocument()
    expect(screen.getByText('现价 ≤ MA20 × 1.10')).toBeInTheDocument()

    // 价格梯：四条价位（现价/止损/入场上下限 + 移动止盈）
    expect(screen.getByText('建议止损')).toBeInTheDocument()
    expect(screen.getByText('移动止盈位')).toBeInTheDocument()
    expect(screen.getAllByText('1594.20').length).toBeGreaterThan(0)
    expect(screen.getAllByText('1545.60').length).toBeGreaterThan(0)
    expect(screen.getAllByText('1575.95').length).toBeGreaterThan(0)

    // 仓位：股数 + 公式原文（用户要能手算复核）
    expect(screen.getAllByText(/400 股/).length).toBeGreaterThan(0)
    expect(screen.getByText(/① 风险预算/)).toBeInTheDocument()

    // 三个族分 + 阈值刻度
    expect(screen.getAllByText('趋势族').length).toBeGreaterThan(1)
    expect(screen.getAllByText('回归族').length).toBeGreaterThan(1)
    expect(screen.getAllByText('多策略融合').length).toBeGreaterThan(0)
    expect(screen.getAllByTitle('买入线 65').length).toBeGreaterThan(1)
    expect(screen.getAllByTitle('买入线 62').length).toBeGreaterThan(0)
    expect(screen.getAllByTitle('卖出线 42').length).toBeGreaterThan(0)
  })

  it('负期望如实顶到计划卡最前，且诚实层用负面配色', async () => {
    renderPage(<StrategyPage />)
    // 排行榜的结论列里也有这五个字，所以是 getAllByText——那正是排行榜该做的事
    expect((await screen.findAllByText('实测期望为负')).length).toBeGreaterThan(0)
    // verdict 原文（不是摘要）必须在屏上
    const verdict = screen.getByText(/按此规则操作的历史结果是亏钱的，请勿据此下单/)
    expect(verdict).toBeInTheDocument()
    // 「未复权」在页面上有两处（计划卡的实测口径 + 全市场汇总的池化口径），故是 getAll
    expect(screen.getAllByText(/未复权：除权除息的跳空会被计为真实亏损/).length).toBeGreaterThan(0)

    // 配色本身就是这条契约的一部分：诚实层容器必须是 negative 而不是庆祝色
    const banner = verdict.closest('[class*="border-"]')
    expect(banner?.className).toContain('border-negative')
    expect(banner?.className).not.toContain('text-positive')
  })

  it('屏幕上的期望值是接口返回的实测值，不是写死的文案', async () => {
    // 负值：换成 -3.12
    server.use(
      http.get('/api/v1/strategy/plan/:code', () =>
        HttpResponse.json({
          ...strategyPlan,
          honesty: { ...expectancyBlock, expectancy_pct: -3.12 },
        }),
      ),
    )
    const first = renderPage(<StrategyPage />)
    expect(await screen.findByText('-3.12%')).toBeInTheDocument()
    first.unmount()

    // 正值：同一处必须跟着变，且**不许**变成买入结论
    server.use(
      http.get('/api/v1/strategy/plan/:code', () =>
        HttpResponse.json({
          ...strategyPlan,
          action: 'watch' as const,
          action_label: '继续观望',
          honesty: {
            ...expectancyBlock,
            expectancy_pct: 4.5,
            trade_count: 24,
            verdict: 'positive' as const,
            verdict_text: '实测每笔期望 +4.50%（24 笔），同期买入持有 -11.20%；历史样本不代表未来。',
          },
        }),
      ),
    )
    renderPage(<StrategyPage />)
    expect(await screen.findByText('+4.50%')).toBeInTheDocument()
    expect(screen.getByText(/历史样本不代表未来/)).toBeInTheDocument()
    // 正期望也只是"样本内的正"：配色不许变成庆祝色，买入结论仍要求入场条件全部满足
    const banner = screen.getByText(/历史样本不代表未来/).closest('[class*="border-"]')
    expect(banner?.className).toContain('border-border')
    expect(banner?.className).not.toContain('text-positive')
  })

  it('样本不足时给警示文案而不是乐观文案', async () => {
    server.use(
      http.get('/api/v1/strategy/plan/:code', () =>
        HttpResponse.json({
          ...strategyPlan,
          honesty: {
            ...expectancyBlock,
            trade_count: 6,
            verdict: 'insufficient' as const,
            verdict_text: '实测每笔期望 +1.20%，但只有 6 笔成交，样本不足以判断任何结论，勿据此下单。',
          },
        }),
      ),
    )
    // 排行榜与全市场汇总都清空：它们各自都可能有 positive 行，不清空的话下面那句
    // 「没有乐观文案」就不是在说计划卡，而是被别处碰巧没出现的文案空转通过
    server.use(http.get('/api/v1/strategy/expectancy/:code', () => HttpResponse.json([])))
    server.use(
      http.get('/api/v1/strategy/pool', () =>
        HttpResponse.json({ ...poolStats, computed_at: null, rows: [] }),
      ),
    )
    renderPage(<StrategyPage />)
    expect(await screen.findByText(/样本不足以判断任何结论，勿据此下单/)).toBeInTheDocument()
    expect(screen.getAllByText('样本不足').length).toBeGreaterThan(0)
    expect(screen.queryByText('实测期望为正')).not.toBeInTheDocument()
    // 6 笔这个数字本身必须在屏上——"样本不足"的来源要可见
    expect(screen.getAllByText('6').length).toBeGreaterThan(0)
  })

  it('策略实测对比表列出全部策略并保留基准行', async () => {
    renderPage(<StrategyPage />)
    expect(await screen.findByText('策略实测对比')).toBeInTheDocument()
    expect(screen.getAllByText('买入持有（基准）').length).toBeGreaterThan(0)
    expect(screen.getAllByText('基准').length).toBeGreaterThan(0)
    // 经典策略也在亏钱——这正是用户不该把坏结果读成"页面坏了"的依据
    expect(screen.getAllByText('-21.80%').length).toBeGreaterThan(0)
    expect(screen.getByText('-15.70%')).toBeInTheDocument()
    expect(screen.getByText('+15.10%')).toBeInTheDocument()
    expect(screen.getAllByText('样本不足').length).toBeGreaterThan(0)
  })

  it('全市场汇总：真实分母与窗口在屏，且「中位股」两个度量排在均值之前', async () => {
    renderPage(<StrategyPage />)
    expect(await screen.findByText('全市场实测汇总')).toBeInTheDocument()
    const section = screen.getByText('全市场实测汇总').closest('section') as HTMLElement

    // 分母与窗口必须与结论同时给：只写「20 万笔」会被读成「20 万个独立观测」
    expect(section).toHaveTextContent('5,515 只计入（库内 5,564 只）')
    expect(section).toHaveTextContent('中位 96 个交易日')
    expect(section).toHaveTextContent('2026-04-14 ~ 2026-09-16')

    // 列序是本节的中心思想：均值为正、中位股为负是常态，先给均值就是在骗人
    const headerRow = screen.getByText('股级中位数').closest('tr') as HTMLElement
    const labels = within(headerRow)
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent ?? '')
    expect(labels).toContain('期望/笔')
    expect(labels.indexOf('股级中位数')).toBeGreaterThanOrEqual(0)
    expect(labels.indexOf('股级中位数')).toBeLessThan(labels.indexOf('期望/笔'))
    expect(labels.indexOf('正股占比')).toBeLessThan(labels.indexOf('期望/笔'))
    // 强平占比要在屏上：每只股票的末笔都是样本末尾平仓，不是规则出场
    expect(labels).toContain('强平占比')
  })

  it('全市场汇总：均值为正与中位股为负并排出现，基准行被标出来', async () => {
    renderPage(<StrategyPage />)
    const section = await screen.findByText('全市场实测汇总')
    const scope = section.closest('section') as HTMLElement

    // 均值 +0.26% 与中位股 -1.11% 是同一行的两个数。只显示前者就是本节要防的骗人方式
    expect(scope).toHaveTextContent('+0.26%')
    expect(scope).toHaveTextContent('-1.11%')
    // 分母印在数字旁边：这个度量的分母比"有成交的股票数"小一个量级（344 对 5,307），
    // 不印出来就会把「中位股 -1.11%」当成全部 5,307 只的中位数
    expect(scope).toHaveTextContent('(344 只)')

    // 基准行：标签是接口给的「买入持有（基准）」，此外还挂着 `基准` chip
    const baseline = within(scope).getByText('买入持有（基准）').closest('tr') as HTMLElement
    expect(within(baseline).getByText('基准')).toBeInTheDocument()
    // 基准行自己就是基准：超额记 0，而不是"拿它减自己"
    expect(baseline).toHaveTextContent('+0.00%')
    expect(baseline).toHaveTextContent('-12.45%')
    // 它每只股票恰好 1 笔，永远达不到 5 笔门槛，所以两个度量是「不适用」而不是「还没算」——
    // 两者的区别必须看得见，否则会被读成缺数据
    expect(baseline).toHaveTextContent('不适用')
    // 两列（中位数、正股占比）都要带上原因，不能只给一个孤零零的 `--`
    expect(within(baseline).getAllByTitle(/不适用：该规则在每只股票上都不足 5 笔/)).toHaveLength(2)
  })

  it('全市场汇总：口径与偏差逐条在屏，且点明「不是这只股票的预期」与「否决单向」', async () => {
    renderPage(<StrategyPage />)
    const scope = (await screen.findByText('全市场实测汇总')).closest('section') as HTMLElement

    expect(scope).toHaveTextContent(/这一栏不是这只股票的预期/)
    expect(scope).toHaveTextContent(/否决是单向的/)
    expect(scope).toHaveTextContent(/绝不会/)
    // 偏差披露由后端下发（唯一副本），这里钉住它确实被渲染出来了
    expect(scope).toHaveTextContent(/生存偏差/)
    expect(scope).toHaveTextContent(/成交不独立/)
    expect(scope).toHaveTextContent(/分母见每行的 stock_denominator/)
  })

  it('全市场汇总的数字来自接口，不是写死的文案', async () => {
    server.use(
      http.get('/api/v1/strategy/pool', () =>
        HttpResponse.json({
          ...poolStats,
          universe_used: 4_321,
          bars_median: 88,
          rows: [{ ...poolStatsRows[0], median_stock_expectancy_pct: -3.33, stock_denominator: 999 }],
        }),
      ),
    )
    renderPage(<StrategyPage />)
    const scope = (await screen.findByText('全市场实测汇总')).closest('section') as HTMLElement

    expect(scope).toHaveTextContent('4,321 只计入')
    expect(scope).toHaveTextContent('中位 88 个交易日')
    expect(scope).toHaveTextContent('-3.33%')
    expect(scope).toHaveTextContent('(999 只)')
    // 旧值必须消失——否则说明屏上是写死的
    expect(scope).not.toHaveTextContent('5,515 只计入')
  })

  it('全市场汇总：没算过时给空态与「立即计算」，绝不显示 0 笔', async () => {
    const user = (await import('@testing-library/user-event')).default.setup()
    let triggered = 0
    server.use(
      http.get('/api/v1/strategy/pool', () =>
        HttpResponse.json({ ...poolStats, computed_at: null, rows: [] }),
      ),
      http.post('/api/v1/strategy/pool/refresh', () => {
        triggered += 1
        return HttpResponse.json(
          { id: 7, job_type: 'pool', status: 'running', message: '已开始计算' },
          { status: 202 },
        )
      }),
      // 轮询停在 running：把「进行中」这一态留在屏上，好断言按钮不会回到可点状态
      http.get('/api/v1/sync/jobs/:id', () =>
        HttpResponse.json({ id: 7, job_type: 'pool', status: 'running', message: '正在计算' }),
      ),
    )
    renderPage(<StrategyPage />)

    expect(await screen.findByText('尚未计算全市场汇总')).toBeInTheDocument()
    // 空态不能露出一张 0 笔 / 0.00% 的表——那会被读成"算过了，结果是零"
    expect(screen.queryByText('股级中位数')).not.toBeInTheDocument()
    expect(screen.queryByText('+0.00%')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '立即计算' }))
    expect(triggered).toBe(1)
    expect(await screen.findByRole('button', { name: '正在计算…' })).toBeDisabled()
  })

  it('持仓表渲染浮盈、距止损与建议，已跌破止损的行给出清仓', async () => {
    renderPage(<StrategyPage />)
    expect(await screen.findByText('持仓跟踪与调整建议')).toBeInTheDocument()
    expect(screen.getByText('继续持有')).toBeInTheDocument()
    expect(screen.getByText('跌破止损，清仓')).toBeInTheDocument()
    expect(screen.getByText('+4.85%')).toBeInTheDocument()
    expect(screen.getByText('-3.05%')).toBeInTheDocument()
    // 自填止损与规则止损必须区分开——否则"你的止损已经偏离规则"这件事看不见
    expect(screen.getByText('自定')).toBeInTheDocument()
    expect(screen.getByText('规则')).toBeInTheDocument()
    // 缺日线的持仓降级显示，不能整张表打不开
    expect(screen.getByText(/本地暂无该标的日线，请先同步数据/)).toBeInTheDocument()
  })

  it('可以展开某只标的的流水明细并删除错录', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    let deleted = ''
    server.use(
      http.delete('/api/v1/strategy/positions/trades/:id', ({ params }) => {
        deleted = String(params.id)
        return HttpResponse.json({ message: '已删除' })
      }),
    )
    renderPage(<StrategyPage />)
    const buttons = await screen.findAllByRole('button', { name: '查看流水' })
    await user.click(buttons[0])
    expect(await screen.findByText('600519.SH 的流水明细')).toBeInTheDocument()
    // 这个日期在持仓表的"首次建仓"列里也有一份，用 getAllByText
    expect(screen.getAllByText('2026-08-26').length).toBeGreaterThan(1)
    await user.click(screen.getByRole('button', { name: '删除' }))
    expect(deleted).toBe('1')
  })

  it('记录一笔买入会提交原始成交并刷新持仓', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    let payload: Record<string, unknown> | null = null
    server.use(
      http.post('/api/v1/strategy/positions/trades', async ({ request }) => {
        payload = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(positionRows[0], { status: 201 })
      }),
    )
    renderPage(<StrategyPage />)
    await user.clear(await screen.findByLabelText('标的'))
    await user.type(screen.getByLabelText('标的'), '300750.SZ')
    await user.type(screen.getByLabelText('价格'), '231.6')
    await user.type(screen.getByLabelText('股数'), '300')
    await user.click(screen.getByRole('button', { name: '记录买入' }))

    expect(payload).toMatchObject({ ts_code: '300750.SZ', side: 'buy', price: 231.6, shares: 300 })
    // 提交成功后表单被重置——这是 onSuccess 真的跑过的证据（toast 挂在 Layout 上，本页渲染不到）
    expect(await screen.findByRole('button', { name: '记录买入' })).toBeInTheDocument()
    expect((screen.getByLabelText('价格') as HTMLInputElement).value).toBe('')
  })

  it('切换策略会按新策略重新请求计划', async () => {
    const { default: userEvent } = await import('@testing-library/user-event')
    const user = userEvent.setup()
    const requested: string[] = []
    server.use(
      http.get('/api/v1/strategy/plan/:code', ({ request }) => {
        requested.push(new URL(request.url).searchParams.get('strategy') ?? '')
        return HttpResponse.json(strategyPlan)
      }),
    )
    renderPage(<StrategyPage />)
    await user.selectOptions(await screen.findByLabelText('策略'), 'ma_cross')
    expect(requested).toContain('ma_cross')
  })

  it('计划接口报错时展示错误信息', async () => {
    server.use(
      http.get('/api/v1/strategy/plan/:code', () =>
        HttpResponse.json({ detail: '未找到该股票' }, { status: 404 }),
      ),
    )
    renderPage(<StrategyPage />)
    expect(await screen.findByText('未找到该股票')).toBeInTheDocument()
  })

  it('排行榜与持仓为空时给出空态而不是空白', async () => {
    server.use(
      http.get('/api/v1/strategy/expectancy/:code', () => HttpResponse.json([])),
      http.get('/api/v1/strategy/positions', () => HttpResponse.json([])),
    )
    renderPage(<StrategyPage />)
    expect(await screen.findByText('暂无实测数据')).toBeInTheDocument()
    expect(screen.getByText('暂无流水记录')).toBeInTheDocument()
  })

  it('量化分析入口带上当前标的与窗口，两个分析页可来回切', async () => {
    renderPage(<StrategyPage />, ['/?code=300750.SZ&days=120'])
    const link = await screen.findByRole('link', { name: /量化分析/ })
    expect(link).toHaveAttribute('href', '/quant?code=300750.SZ&days=120')
  })
})
