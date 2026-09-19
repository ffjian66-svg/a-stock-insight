import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { TomorrowPage } from './TomorrowPage'
import { renderPage, server, simplePlanBoard, simplePlanSectors } from '../test/utils'

/** 所有板块的行数之和——「分组不丢行」的哨兵都用它，而不是某一行 fixture 的长度。 */
const totalRows = simplePlanSectors.reduce((sum, sector) => sum + sector.rows.length, 0)

/** 表格里的股票名（按 DOM 顺序）。整表链接序是"前端不重排"的哨兵，多处都用它。 */
function rowNames(container: HTMLElement): (string | null)[] {
  return [...container.querySelectorAll('a[href^="/stock/"]')].map((node) => node.textContent)
}

/**
 * 先等到**数据真的到了**再断言。
 *
 * 这里等的必须是行内的东西，不能等标题——标题在 loading 态就已经在 DOM 里了，拿它当等待
 * 条件会立刻返回，后面每条断言都跑在骨架屏上（`getByText` 于是全红，而原因看起来像"渲染
 * 坏了"）。等一个只有接口能给的字面量最稳。
 *
 * **本函数不带任何筛选**，而下面那些精确计数（`toHaveLength(2)`/`toHaveLength(3)`、以及
 * `toBe(totalRows)`）全都以这个为前提。日后若有人给默认视图加筛选，红的是它们，不是渲染。
 */
async function renderRows() {
  renderPage(<TomorrowPage />)
  expect(await screen.findByText('1575.95 – 1600.28')).toBeInTheDocument()
}

/**
 * 筛选态：URL 直接给出条件（也是"刷新/返回能还原"那条契约的实测入口）。
 *
 * 等的是**板块中位数 chip**，不是某一行——被筛掉的恰恰可能是那一行，拿它当等待条件会在
 * 筛选态下永远等不到，而红出来的原因看起来像"筛选没生效"。
 */
async function renderFiltered(entry: string) {
  const result = renderPage(<TomorrowPage />, [entry])
  expect(await screen.findByText('库内样本中位 291 根 / 窗口 500 根')).toBeInTheDocument()
  return result
}

describe('TomorrowPage', () => {
  it('四列表头在屏，数字来自接口而不是前端硬编码', async () => {
    await renderRows()
    expect(screen.getByRole('heading', { name: '明日买点候选' })).toBeInTheDocument()

    for (const header of ['推荐买入价格', '推荐持有天数', '推荐卖出价格', '结论']) {
      expect(screen.getByRole('columnheader', { name: header })).toBeInTheDocument()
    }
    // 换过的、只在 fixture 里存在的价位必须原样出现——写死的数字过不了这条
    expect(screen.getByText('1575.95 – 1600.28')).toBeInTheDocument()
    expect(screen.getByText('1545.60')).toBeInTheDocument()
    expect(screen.getByText('1552.40')).toBeInTheDocument()
    // 基准价与来源日期（价格锚定在库内收盘上，不是实时报价）
    expect(screen.getByText('基准 1594.20')).toBeInTheDocument()
    expect(screen.getAllByText('收盘 09-16').length).toBeGreaterThan(0)
  })

  it('结论逐字来自后端，不由 verdict 反推', async () => {
    await renderRows()

    // 负期望那两行的文案只有接口能给出：前端若写 action = verdict === 'positive' ? … 就会
    // 渲染成「观望」之类，这条断言随即变红
    expect(screen.getAllByText('不建议买入：该规则在此标的实测期望为负')).toHaveLength(2)
    expect(screen.getAllByText('观望：样本不足，不建议据此买入')).toHaveLength(2)
    // 笔数必须印出来，否则「实测」二字是空的。（24 笔那行在元信息与 verdict_text 里各出现
    // 一次，所以按"至少一处"断言，不按唯一性。）
    expect(screen.getAllByText(/实测 24 笔/).length).toBeGreaterThan(0)
    expect(screen.getByText(/实测 3 笔/)).toBeInTheDocument()
  })

  it('持有多头行标「经验值」并给出区间，不冒充实测值', async () => {
    await renderRows()

    expect(screen.getAllByText('10–40 个交易日')).toHaveLength(3)
    expect(screen.getAllByText('经验值')).toHaveLength(3)
    // `expected_hold.note` 逐字进 title（整格可悬停）——它已经写着"不是本标的的实测结果"
    expect(screen.getAllByText('经验值')[0].closest('td')).toHaveAttribute(
      'title',
      expect.stringContaining('不是本标的的实测结果'),
    )
  })

  it('有实测持有期时给实测均值并标「实测均值」，即使该规则不赚钱', async () => {
    await renderRows()

    // measured 只要求 verdict != insufficient，不要求 positive：这一行在屏上是
    // 「实测均值 17 天」+「不建议买入：实测期望为负」并存，这是对的
    expect(screen.getByText('约 17 个交易日')).toBeInTheDocument()
    expect(screen.getByText('实测均值')).toBeInTheDocument()
  })

  it('移动止盈位为 null 时显示 --，绝不退化成 0.00', async () => {
    await renderRows()

    // 五粮液那行 ATR 缺失：止盈位不可用
    expect(screen.queryByText('0.00')).not.toBeInTheDocument()
    expect(screen.getAllByText('--').length).toBeGreaterThan(0)
  })

  it('移动止盈位标出「自最高价回落触发」——它常低于现价，是触发价不是目标价', async () => {
    await renderRows()

    // 按**所有板块的行数之和**数，不按某一行 fixture 的长度：分组一旦漏掉一个板块，
    // 这条会跟着变红，而「每行都渲染了」这个直觉就不会再是默认成立的。
    expect(screen.getAllByText('自最高价回落触发').length).toBe(totalRows)
  })

  it('板块分组表头在屏，板块名与只数正确', async () => {
    await renderRows()

    // 限定在表内找板块名：筛选下拉的 `<option>白酒</option>` 也带着同样的文字，而
    // RTL 的 getByText **会匹配 option**，不限定就是"找到多个"而抛错。
    const table = within(screen.getByRole('table'))
    for (const industry of ['白酒', '保险', '电池']) {
      expect(table.getByText(industry)).toBeInTheDocument()
    }
    expect(screen.getByText(/2 只（候选 28 只，按市值列出前 10 只）/)).toBeInTheDocument()
    expect(screen.getByText('3 个板块 · 共 4 只')).toBeInTheDocument()
  })

  it('板块下拉的选项按数据生成，不是硬编码的行业表', async () => {
    await renderRows()

    const select = screen.getByRole('combobox', { name: '板块' })
    // 默认「全部板块」，且「全部」的论域（70 个板块那种量级）要印出来
    expect(select).toHaveValue('')
    expect(within(select).getByRole('option', { name: '全部板块（3）' })).toBeInTheDocument()
    for (const industry of ['白酒', '保险', '电池']) {
      expect(within(select).getByRole('option', { name: industry })).toBeInTheDocument()
    }
  })

  it('按板块筛选：只剩该板块，且顶部照旧说全量', async () => {
    const { container } = await renderFiltered('/tomorrow?industry=白酒')

    expect(screen.getByLabelText('板块')).toHaveValue('白酒')
    const table = within(screen.getByRole('table'))
    expect(table.getByText('白酒')).toBeInTheDocument()
    expect(table.queryByText('保险')).not.toBeInTheDocument()
    expect(table.queryByText('电池')).not.toBeInTheDocument()
    expect(rowNames(container)).toEqual(['贵州茅台', '五粮液'])

    // 全量口径不跟着筛——「3 个板块 · 共 4 只」与「当前显示 1 个板块 · 2 只」同时在屏，
    // 才不会被读成"一共就 2 只"。两个数字缺一个，这一页就有一种撒谎的方式。
    expect(screen.getByText('3 个板块 · 共 4 只')).toBeInTheDocument()
    expect(screen.getByText('当前显示 1 个板块 · 2 只')).toBeInTheDocument()
  })

  it('搜索不重编号：板块内第 2 行仍是 02，跳号是被筛掉了', async () => {
    await renderFiltered('/tomorrow?q=五粮液')

    expect(screen.getByText('五粮液')).toBeInTheDocument()
    expect(screen.queryByText('贵州茅台')).not.toBeInTheDocument()

    // 序号必须是它在**未筛**板块里的位次（02）。先 filter 再 map 会渲染成 01，
    // 那等于给"筛出来的唯一一只"凭空造了个第一名。
    const row = screen.getByText('五粮液').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText('02')).toBeInTheDocument()
    expect(within(row as HTMLElement).queryByText('01')).not.toBeInTheDocument()
  })

  it('搜索按代码也命中，且大小写不敏感', async () => {
    await renderFiltered('/tomorrow?q=600519.sh')

    expect(screen.getByText('贵州茅台')).toBeInTheDocument()
    expect(screen.queryByText('五粮液')).not.toBeInTheDocument()
  })

  it('板块与关键词是 AND', async () => {
    const { container } = await renderFiltered('/tomorrow?industry=白酒&q=茅台')

    expect(screen.getByLabelText('板块')).toHaveValue('白酒')
    expect(screen.getByLabelText('筛选本页股票')).toHaveValue('茅台')
    expect(rowNames(container)).toEqual(['贵州茅台'])
  })

  it('筛选态下板块的截断披露仍是原口径，另印筛选后的可见数', async () => {
    await renderFiltered('/tomorrow?q=五粮液')

    // 两句必须都在，而且是**放在同一格里的两句不同的话**：前者说的是整个板块
    // （我们按市值截断了 28 → 10），后者说的是这次筛选（2 行里露出 1 行）。
    // 把前者的数字改成筛选后的，就等于把"我们挑过了"重写成"筛出来就这些"。
    expect(screen.getByText(/候选 28 只，按市值列出前 10 只/)).toBeInTheDocument()
    expect(
      screen.getByText(/筛选后显示 1 \/ 2 只（候选 28 只，按市值列出前 10 只）/),
    ).toBeInTheDocument()
  })

  it('"样本太短"名单不跟着筛选缩——它是一份账，不是视图', async () => {
    await renderFiltered('/tomorrow?industry=白酒')

    // 中芯国际在半导体、与白酒无关。筛到白酒时它必须还在屏上，否则筛选就静默地
    // 把"有候选没进表"这件事删掉了。
    expect(screen.getByText(/另有 1 只候选因样本太短未列入上表/)).toBeInTheDocument()
    expect(screen.getByText(/库内只有 12 根日线/)).toBeInTheDocument()
  })

  it('筛选生效时说清"序号是原序号、名单不跟着筛"', async () => {
    await renderFiltered('/tomorrow?industry=白酒&q=五粮液')

    expect(screen.getByText(/板块内的原序号/)).toBeInTheDocument()
    expect(screen.getByText(/不跟着筛选变化/)).toBeInTheDocument()
  })

  it('筛选无结果时说的是"筛选没匹配"，不是"没有候选"', async () => {
    renderPage(<TomorrowPage />, ['/tomorrow?q=不存在的名字'])

    // 这两句不能混用：后者断言的是候选池的事实（"低风险且评分充足的标的为空"），
    // 在筛选态下那是假话——候选池里明明有 4 只。
    expect(await screen.findByText('当前筛选没有匹配的候选')).toBeInTheDocument()
    expect(screen.queryByText('暂无明日买点候选')).not.toBeInTheDocument()
    // 关键词没命中板块名不该让人以为搜索坏了，要指路
    expect(screen.getByText(/请用上方的板块下拉框/)).toBeInTheDocument()
  })

  it('搜一个只在"样本太短"名单里的股票：空态要指向那份名单', async () => {
    renderPage(<TomorrowPage />, ['/tomorrow?q=中芯国际'])

    expect(await screen.findByText('当前筛选没有匹配的候选')).toBeInTheDocument()
    // 它**就在这一页上**（名单里），所以"没匹配"必须解释成"没进表"。不解释的话，
    // 用户看到的是"搜索找不到一只明明印在屏幕上的股票"。
    expect(screen.getAllByText(/中芯国际/).length).toBeGreaterThan(1)
    expect(screen.getByText(/落在下方/)).toBeInTheDocument()
  })

  it('候选池为空时即使 URL 带着板块筛选，也仍说"暂无候选"', async () => {
    // 分支顺序是契约：`rowCount === 0` 优先于"筛选无结果"。反过来的话，"候选池是空的"
    // 会被讲成"你没搜对"。
    server.use(
      http.get('/api/v1/strategy/simple', () =>
        HttpResponse.json({ ...simplePlanBoard, sectors: [], skipped: [] }),
      ),
    )
    renderPage(<TomorrowPage />, ['/tomorrow?industry=白酒'])

    expect(await screen.findByText('暂无明日买点候选')).toBeInTheDocument()
    expect(screen.queryByText('当前筛选没有匹配的候选')).not.toBeInTheDocument()
  })

  it('"清除筛选"把两个条件一起清掉，表格立刻回到全量', async () => {
    const user = userEvent.setup()
    const { container } = await renderFiltered('/tomorrow?industry=白酒&q=五粮液')
    expect(screen.getByLabelText('板块')).toHaveValue('白酒')

    await user.click(screen.getByRole('button', { name: '清除筛选' }))

    // "立刻"是重点：只清 URL 的话要等 250ms 防抖，这期间按钮还在、表还是筛过的。
    await waitFor(() => expect(screen.getByLabelText('板块')).toHaveValue(''))
    expect(screen.getByLabelText('筛选本页股票')).toHaveValue('')
    expect(rowNames(container)).toEqual(['贵州茅台', '五粮液', '中国平安', '宁德时代'])
    expect(screen.queryByRole('button', { name: '清除筛选' })).not.toBeInTheDocument()
  })

  it('打字防抖后按关键词筛行，输入框不被 URL 回填覆盖', async () => {
    const user = userEvent.setup()
    renderPage(<TomorrowPage />)

    const input = await screen.findByLabelText('筛选本页股票')
    expect(await screen.findByText('1575.95 – 1600.28')).toBeInTheDocument()
    await user.type(input, '五粮液')

    // 250ms 防抖之后表格才缩到一行——这中间每一击键都没有被 URL 回填抹掉
    await waitFor(() => expect(screen.queryByText('贵州茅台')).not.toBeInTheDocument())
    expect(input).toHaveValue('五粮液')
    expect(screen.getByText('五粮液')).toBeInTheDocument()
  })

  it('筛选不改对后端的请求：端点一个参数都不接受', async () => {
    const urls: string[] = []
    server.use(
      http.get('/api/v1/strategy/simple', ({ request }) => {
        urls.push(request.url)
        return HttpResponse.json(simplePlanBoard)
      }),
    )
    await renderFiltered('/tomorrow?industry=白酒&q=茅台')

    // 筛选**纯客户端**。这一条是"后端一行不改"的唯一真守卫：参数一旦被塞进请求，
    // 要么端点得新增 query 解析，要么筛选变成静默失效。
    expect(urls.length).toBeGreaterThan(0)
    for (const url of urls) expect(new URL(url).search).toBe('')
  })

  it('URL 里的板块今日无候选时，下拉不渲染成空白', async () => {
    await renderFiltered('/tomorrow?industry=早就没候选的板块')

    // 受控 select 的 value 匹配不到任何 option 时，浏览器渲染成一片空白（React 不报错），
    // 表格则静默进空态——控件变成无字的，用户看不出自己在筛什么。
    expect(screen.getByLabelText('板块')).toHaveValue('早就没候选的板块')
    expect(
      screen.getByRole('option', { name: '早就没候选的板块（今日无候选）' }),
    ).toBeInTheDocument()
    expect(screen.getByText('当前筛选没有匹配的候选')).toBeInTheDocument()
    // 只有板块、没有关键词时**不许**印出「匹配「」的候选」那对空引号
    expect(screen.getByText(/今日没有候选，或板块名已变化/)).toBeInTheDocument()
    expect(screen.queryByText(/匹配「」/)).not.toBeInTheDocument()
  })

  it('截断与样本不足分开说，不混成一句', async () => {
    await renderRows()

    // 这两个差值说的不是一件事：前者是「我们按市值截断了」，后者是「有候选因库内日线不足
    // 没进表」。页面把后者说成前者，就等于把"数据没拉到"讲成"我们挑过了"。
    expect(screen.getByText(/候选 28 只，按市值列出前 10 只/)).toBeInTheDocument()
    expect(screen.getByText(/另有 8 只因样本太短未列入/)).toBeInTheDocument()
    // 没有截断的板块不该印这两句——否则每行都挂一段免责，读者会开始忽略它
    expect(screen.queryByText(/候选 1 只，按市值列出前 1 只/)).not.toBeInTheDocument()
  })

  it('板块内的行序照抄后端，前端不重排', async () => {
    const { container } = renderPage(<TomorrowPage />)
    expect(await screen.findByText('1575.95 – 1600.28')).toBeInTheDocument()

    // 板块顺序来自 `sectors` 数组，板块内顺序来自 `sector.rows` 数组——两者都是后端给的。
    // fixture 的白酒刻意是茅台(1800 亿) 在 五粮液(4350 亿) **之前**，即故意不是市值序：
    // 前端只要加任何一个 sort，这个列表就会变。
    expect(rowNames(container)).toEqual(['贵州茅台', '五粮液', '中国平安', '宁德时代'])
  })

  it('把「市值只决定顺序」与「龙头是候选内的」两句说在明处', async () => {
    await renderRows()

    // 这一页长得最像荐股，而"板块第一行"最容易被读成"这只最好"。两句话都不许只留在
    // 接口文案里：一句说市值不参与买卖判断，一句说龙头只在本名单内选。
    expect(screen.getByText(/只决定排列顺序/)).toBeInTheDocument()
    // 这一句**故意在两处出现**：接口的 `note`（"为什么是这些股票"）与这块 Notice
    // （"为什么它排第一"）是两条不同的阅读路径。所以按"至少一处"断言，不按唯一性
    // ——与本文件上面「实测 24 笔」的处理一致。
    expect(screen.getAllByText(/不是该板块市值最大的股票/).length).toBeGreaterThan(0)
  })

  it('每行都能进个股页与 /strategy', async () => {
    await renderRows()

    expect(screen.getByRole('link', { name: '贵州茅台' })).toHaveAttribute('href', '/stock/600519.SH')
    expect(screen.getByRole('link', { name: '贵州茅台的买卖策略' })).toHaveAttribute(
      'href',
      '/strategy?code=600519.SH',
    )
  })

  it('披露库内样本中位与基准日，并说清本页不回补', async () => {
    await renderRows()

    expect(screen.getByText('库内样本中位 291 根 / 窗口 500 根')).toBeInTheDocument()
    expect(screen.getByText('基于 2026-09-16 收盘 · 下一交易日参考')).toBeInTheDocument()
    expect(screen.getByText(/绝不向上游回补/)).toBeInTheDocument()
  })

  it('样本太短的候选单列出来并说明原因，不混进表', async () => {
    await renderRows()

    expect(screen.getByText(/另有 1 只候选因样本太短未列入上表/)).toBeInTheDocument()
    expect(screen.getByText(/库内只有 12 根日线/)).toBeInTheDocument()
    // 它不是一个候选行：表里没有它的链接
    expect(screen.queryByRole('link', { name: '中芯国际' })).not.toBeInTheDocument()
  })

  it('空名单时显示空态', async () => {
    server.use(
      http.get('/api/v1/strategy/simple', () =>
        HttpResponse.json({ ...simplePlanBoard, sectors: [], skipped: [] }),
      ),
    )
    renderPage(<TomorrowPage />)
    expect(await screen.findByText('暂无明日买点候选')).toBeInTheDocument()
  })

  it('板块在、行全被样本不足挡掉时，仍是空态而不是一张只有表头的表', async () => {
    // 这个组合是真会出现的：某板块的候选全部低于 MIN_BARS。此时 sectors 非空但一行都列不出来
    // ——按 `sectors.length === 0` 判空会渲染出一张只有分组表头、下面什么都没有的表。
    server.use(
      http.get('/api/v1/strategy/simple', () =>
        HttpResponse.json({
          ...simplePlanBoard,
          sectors: [{ industry: '白酒', candidates: 3, selected: 3, rows: [] }],
        }),
      ),
    )
    renderPage(<TomorrowPage />)
    expect(await screen.findByText('暂无明日买点候选')).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: '推荐买入价格' })).not.toBeInTheDocument()
  })

  it('接口异常时显示错误提示', async () => {
    server.use(
      http.get('/api/v1/strategy/simple', () => HttpResponse.json({}, { status: 500 })),
    )
    renderPage(<TomorrowPage />)
    expect(await screen.findByText(/明日买点候选读取失败/)).toBeInTheDocument()
  })
})
