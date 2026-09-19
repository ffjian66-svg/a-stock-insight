import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CalendarRange, Crosshair, Search, Sunrise } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { EmptyState, ErrorBox, Loading, Notice, PriceNote } from '../components/Common'
import { ACTION_STYLE, VERDICT_TONE, num, signedPct } from '../components/QuantKit'
import { api, type SimplePlanRow } from '../lib/api'

/**
 * 「明日操作」：昨天收盘算出来的四个数字，明天照单执行。按板块分组，板块内龙头（市值最大
 * 的候选）排前面；70 个板块太长，所以顶部有板块下拉与页内关键词筛选。
 *
 * 这一页**不新增任何计算**，只是把 `decision.build_plan` 已经算好的字段按"明天怎么下单"的
 * 密度摆出来。四条纪律在这里最容易破，所以写在最上面：
 *
 * 1. **结论逐字来自后端**（`row.action_label`）。绝不写
 *    `action === 'buy' ? … : …` 或由 `honesty.verdict` 反推——后端的 `action` 是四条信号
 *    条件 + 实测期望符号 + 全市场否决的合成结果，前端重推会静默删掉其中几项，
 *    负期望就重新渲染成"买入"了。
 * 2. **持有天数的来源必须标出来**。`basis === 'heuristic'` 时说"经验值"，说实测值就成了
 *    把"我们不知道"换成"我们建议"。
 * 3. **板块内按 `rows` 的原序渲染，前端不重排**。市值降序是后端给的，重排一次就等于前端
 *    自己发明了一套"谁是龙头"的口径，而那一套不在任何测试的视野里。
 * 4. **筛选只决定"显示哪些行"，不改任何口径**。序号是板块内的**原**序号（不重编号），
 *    `candidates`/`selected` 的披露与 `skipped` 名单都**不跟着筛**，顶部计数永远说全量。
 *    一份被筛过的视图绝不能被读成完整的——那是这一页唯一的失败模式。
 *
 * 价格锚点是库内最后一根**收盘**（不是实时报价），所以显示「收盘 MM-DD」而不是「现价」。
 */
export function TomorrowPage() {
  const query = useQuery({ queryKey: ['simple-plan'], queryFn: api.simplePlan, staleTime: 60_000 })
  const data = query.data
  // `?? []` 会在每次渲染产生新数组，而下面 `visible` 的 useMemo 依赖它——包一层才有稳定的
  // 依赖（否则那个 memo 每渲染必重算，等于没有）。数据本身只在请求落地时换一次。
  const sectors = useMemo(() => data?.sectors ?? [], [data])
  const skipped = data?.skipped ?? []
  const rowCount = sectors.reduce((sum, sector) => sum + sector.rows.length, 0)

  const [searchParams, setSearchParams] = useSearchParams()
  const industry = searchParams.get('industry') ?? ''
  const urlQ = searchParams.get('q') ?? ''
  const [term, setTerm] = useState(urlQ)
  const [debounced, setDebounced] = useState(urlQ)
  // 我们自己写进 URL 的最后一个值。用来区分"URL 变了是因为我们在打字"与"URL 变了是因为
  // 用户按了返回/清除筛选"——见下面第三个 effect。
  const flushed = useRef(urlQ)

  // 与 `StrategyPage.tsx` / `QuantPage.tsx` 逐字同形的泛型版本（`null` = 删键），用
  // `useCallback` 包住让下面那个 effect 的依赖是稳定的。
  const patch = useCallback(
    (next: Record<string, string | null>) => {
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev)
          for (const [key, value] of Object.entries(next)) {
            if (value == null) params.delete(key)
            else params.set(key, String(value))
          }
          return params
        },
        // replace：筛选态不占历史条目（刷新/返回都还原，但"后退"不会退出一串中间态）。
        // 注意 replace 会重建 history key，`ScrollRestoration` 因此把页面滚回顶部——切板块时
        // 表格从 250 行缩到 10 行，滚回顶部恰好是想要的，所以不为它加 preventScrollReset。
        { replace: true },
      )
    },
    [setSearchParams],
  )

  // 输入框**不**由 URL 直驱：`setSearchParams` 经 data router 走 startTransition，把 value
  // 直接绑在上面会在打字时回退到旧值 / 丢字，中文输入法下更明显。本地 state + 250ms 防抖，
  // 形状照 `Layout.tsx` 的全局搜索。
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(term.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [term])

  useEffect(() => {
    if (debounced === flushed.current) return
    flushed.current = debounced
    patch({ q: debounced || null })
  }, [debounced, patch])

  // 外部变化（返回/前进、清除筛选）→ 回填输入框。`urlQ !== flushed.current` 这个守卫是
  // **必须**的：没有它，我们自己 flush 出去的 URL 会回填输入框，把两次击键之间新打的字覆盖掉。
  useEffect(() => {
    if (urlQ !== flushed.current) {
      flushed.current = urlQ
      setTerm(urlQ)
    }
  }, [urlQ])

  const filtered = industry !== '' || debounced !== ''

  // 板块顺序、板块内顺序**都照抄后端**，这里只做「取出显示哪些」。板块内把原始下标一起带上，
  // 序号才不会被筛选重编号。
  const visible = useMemo(() => {
    const needle = debounced.trim().toLowerCase()
    const hit = (row: SimplePlanRow) =>
      needle === '' ||
      row.name.toLowerCase().includes(needle) ||
      row.ts_code.toLowerCase().includes(needle)
    return (
      sectors
        .filter((sector) => industry === '' || sector.industry === industry)
        .map((sector) => ({
          sector,
          rows: sector.rows
            .map((row, index) => ({ row, index }))
            .filter(({ row }) => hit(row)),
        }))
        // 行被筛空的板块整段不渲染。它那句「候选 N 只」的披露因此也看不到了——由顶部的
        // 全量计数与下面的横幅代偿（两者都不跟着筛）。
        .filter(({ rows }) => rows.length > 0)
    )
  }, [sectors, industry, debounced])

  const visibleRowCount = visible.reduce((sum, item) => sum + item.rows.length, 0)

  // 「全部」的论域是**已截断的榜单**（70 个板块），不是全市场行业表——所以把板块数印出来。
  const hasIndustryOption = sectors.some((sector) => sector.industry === industry)

  const clearFilters = () => {
    // 三个本地 state 一起清，表格才会**立刻**回到全量：只清 URL 的话要等 250ms 防抖，
    // 这期间「清除筛选」按钮还在、表格还是筛过的。
    flushed.current = ''
    setTerm('')
    setDebounced('')
    patch({ industry: null, q: null })
  }

  // 三个分支说的是三件不同的事，**不能合成一个模板**：只有板块没关键词时说「匹配「」的候选」
  // 会印出一对空引号，读起来像页面坏了。
  const filterHint = [
    industry !== '' && debounced !== ''
      ? `板块「${industry}」下没有名称或代码匹配「${debounced}」的候选。`
      : industry !== ''
        ? `板块「${industry}」今日没有候选，或板块名已变化。`
        : `本页没有名称或代码匹配「${debounced}」的候选；要按板块找，请用上方的板块下拉框。`,
    '若它确实在候选池里，也可能是因库内日线不足落在下方「样本太短未列入」名单中——那份名单不跟着筛选变化。',
  ].join('')

  return (
    <section className="p-5 pb-16 lg:p-8">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="eyebrow flex items-center gap-1.5">
            <Sunrise size={14} />
            TOMORROW&apos;S ORDERS
          </p>
          <h2 className="mt-1 text-xl font-semibold">明日买点候选</h2>
          {data?.note ? (
            <p className="mt-1 max-w-3xl text-xs leading-5 text-muted">{data.note}</p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* 全量口径，**永远**不跟着筛选变——有筛选时下面另加一个 chip 说"当前显示"。
              两个数字同时在屏，才不会被读成"一共就这么多"。 */}
          {data ? (
            <span className="chip whitespace-nowrap border-border bg-elevated text-muted">
              {sectors.length} 个板块 · 共 {rowCount} 只
            </span>
          ) : null}
          {data && filtered ? (
            <span className="chip whitespace-nowrap border-primary/20 bg-primary/10 text-primary">
              当前显示 {visible.length} 个板块 · {visibleRowCount} 只
            </span>
          ) : null}
          {/* `bars_median` 是**所有板块**行级 bars 的中位数（`api.ts` 的 `SimplePlanBoard`
              文档钉着）。别"顺手"把它改成对筛选后的行取中位数——那会让同一个链接在不同筛选下
              显示两个不同的样本量，而它是用来披露"窗口 500 根 vs 实际拿到多少根"的。 */}
          {data ? (
            <span
              className="chip whitespace-nowrap border-warning/20 bg-warning/10 text-warning"
              title="本页只读库内已有日线，绝不向上游回补（保护上游配额）。样本因此可能比单股页少，结论只会更保守——所以这里的数字不必与单股页逐位相同。中位数取的是所有板块的行，不随筛选变化。"
            >
              库内样本中位 {data.bars_median} 根 / 窗口 {data.window_days} 根
            </span>
          ) : null}
          {data?.basis_date ? (
            <span className="chip whitespace-nowrap border-primary/20 bg-primary/10 text-primary">
              基于 {data.basis_date} 收盘 · 下一交易日参考
            </span>
          ) : null}
        </div>
      </div>

      {/* 这一页长得最像"荐股"，所以它比别的页更需要把话说在前面。 */}
      <div className="mb-4">
        <Notice tone="warning">
          <span>
            四个数字都来自既有的规则计算，是<strong>研究记录而不是投资建议</strong>：本应用不接券商、
            不会自动下单，也不预测行情。结论那列多数会是「观望」——融合策略在单只股票上的两年样本
            通常不足 20 笔，样本不足时规则就不给方向，这是设计而不是故障。
            {data?.caveat ? ` ${data.caveat}` : ''}
          </span>
        </Notice>
      </div>

      {/* 板块内按市值降序，所以「第一个就是龙头」这句话在这页最容易被读成"它更好"。 */}
      <div className="mb-4">
        <Notice tone="info">
          <span>
            板块内的顺序是<strong>总市值降序</strong>，排第一的就是该板块的龙头股。但市值
            <strong>只决定排列顺序</strong>，不参与任何买卖判断——同一只股票不管排在板块第几位，
            它的买入价、止损位和结论都逐位相同。「龙头」指的也是<strong>通过上面门槛的候选里</strong>
            市值最大的那只，不是该板块市值最大的股票：板块里市值更大但没过门槛的股票不在本名单内。
          </span>
        </Notice>
      </div>

      {/* 筛选条。两个控件都**只筛已经取回的数据**：端点刻意不接受任何参数，~14s 是"给全市场
          候选现算计划"的成本，筛选不可能让它变快。 */}
      <div className="panel mb-4 flex flex-wrap items-end gap-4 p-5">
        <div>
          <label className="mb-2 block text-xs text-muted" htmlFor="tomorrow-industry">
            板块
          </label>
          <select
            id="tomorrow-industry"
            aria-label="板块"
            className="field w-52"
            value={industry}
            onChange={(e) => patch({ industry: e.target.value || null })}
          >
            <option value="">全部板块{data ? `（${sectors.length}）` : ''}</option>
            {/* URL 里的板块今日没有候选（收藏夹/分享链接）时，仍要有一个匹配的 option，
                否则受控 select 会渲染成一片空白。 */}
            {industry !== '' && !hasIndustryOption ? (
              <option value={industry}>{industry}（今日无候选）</option>
            ) : null}
            {sectors.map((sector) => (
              <option key={sector.industry} value={sector.industry}>
                {sector.industry}
              </option>
            ))}
          </select>
        </div>
        <div>
          {/* label 里**不出现**「搜索」二字：顶栏全局搜索的可访问名是「全局搜索股票」，
              日后按 /搜索/ 取 label 会一次命中两个。同理输入框必须是 type="text"
              （type="search" 会拿到隐式 searchbox role，与顶栏那个重复）。 */}
          <label className="mb-2 block text-xs text-muted" htmlFor="tomorrow-keyword">
            代码或名称
          </label>
          <div className="relative">
            <Search
              size={15}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
            />
            <input
              id="tomorrow-keyword"
              type="text"
              aria-label="筛选本页股票"
              className="field w-56 pl-9"
              placeholder="如 600519 或 茅台"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
            />
          </div>
        </div>
        {filtered ? (
          <button className="button-ghost" onClick={clearFilters}>
            清除筛选
          </button>
        ) : null}
        <p className="ml-auto text-xs text-muted">
          {filtered ? `筛选中 · 显示 ${visibleRowCount} / ${rowCount} 只` : '按板块或关键词缩小范围'}
        </p>
      </div>

      {/* 分享出去的 `?q=五粮液` 打开后是一张只有一行、序号还是 02 的表，表头却仍写着
          「候选 28 只，按市值列出前 10 只」。这三件事 chip 说不出口，所以单独说。 */}
      {filtered && rowCount > 0 ? (
        <div className="mb-4">
          <Notice tone="info">
            <span>
              当前按
              {industry !== '' ? `板块「${industry}」` : ''}
              {industry !== '' && debounced !== '' ? ' + ' : ''}
              {debounced !== '' ? `关键词「${debounced}」` : ''}
              筛选，显示 {visibleRowCount} 只（全部板块共 {rowCount} 只）。表里的序号是
              <strong>板块内的原序号</strong>，跳号说明该行被筛掉了，不是缺行；下方
              「样本太短未列入」名单是<strong>全市场口径，不跟着筛选变化</strong>。
            </span>
          </Notice>
        </div>
      ) : null}

      {query.isLoading ? (
        <Loading label="正在为全市场候选现算买卖计划，约需 10–20 秒…" />
      ) : query.error ? (
        <div className="panel p-5">
          <ErrorBox message="明日买点候选读取失败，请稍后重试。" />
        </div>
      ) : rowCount === 0 ? (
        // 真·没有候选。**这一支优先于筛选空态**：数据为空时即使 URL 里还挂着 `?industry=白酒`，
        // 也必须说「暂无明日买点候选」——说成"筛选没匹配"就把"候选池是空的"讲成了"你没搜对"。
        <div className="panel">
          <EmptyState
            title="暂无明日买点候选"
            hint="低风险且评分充足的“可分批买入”标的为空。请等收盘同步（17:30）后再看，或到智能选股放宽评分门槛。"
          />
        </div>
      ) : visibleRowCount === 0 ? (
        // 与上一支**不是一句话**：上面那句断言的是候选池的事实，在筛选态下是假话。
        <div className="panel">
          <EmptyState title="当前筛选没有匹配的候选" hint={filterHint} />
        </div>
      ) : (
        <div className="panel">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1080px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  <th className="px-5 py-3 font-medium">#</th>
                  <th className="py-3 font-medium">股票</th>
                  <th className="py-3 font-medium">推荐买入价格</th>
                  <th className="py-3 font-medium">推荐持有天数</th>
                  <th className="py-3 font-medium">推荐卖出价格</th>
                  <th className="py-3 pr-5 font-medium">结论</th>
                </tr>
              </thead>
              <tbody>
                {visible.map(({ sector, rows }) => (
                  <Fragment key={sector.industry}>
                    {/* 板块分组行。用一张表 + 分组行、而不是每板块一张表：70 张表会有 420 个
                        columnheader，屏读起来是一堆碎片，而且列宽各表独立、对不齐。 */}
                    <tr className="border-b border-border bg-elevated/40">
                      <td colSpan={6} className="px-5 py-2.5">
                        <span className="chip border-primary/20 bg-primary/10 text-primary">
                          {sector.industry}
                        </span>
                        <span className="ml-2 text-xs text-muted">
                          {/* 无筛选时这一格**逐字保持原样**（既有断言按整串匹配「2 只（候选 28 只…）」）。 */}
                          {filtered
                            ? `筛选后显示 ${rows.length} / ${sector.rows.length} 只`
                            : `${sector.rows.length} 只`}
                          {/* 两个差值说的不是一件事，分开印：这个是"我们按市值截断了"。它说的是
                              整个板块、**不跟着筛选缩**——跟着算就等于把"我们挑过了"重写成
                              "筛出来就这些"。 */}
                          {sector.selected < sector.candidates
                            ? `（候选 ${sector.candidates} 只，按市值列出前 ${sector.selected} 只）`
                            : ''}
                          {/* …这个是"有候选因库内日线不足没进表"，同样是板块整体口径。 */}
                          {sector.rows.length < sector.selected
                            ? `（另有 ${sector.selected - sector.rows.length} 只因样本太短未列入）`
                            : ''}
                        </span>
                      </td>
                    </tr>
                    {/* `rows` 是 `sector.rows` 的子序列，`index` 是它在**未筛**数组里的位置。
                        先 filter 再 map 会把搜出来的板块内第 2 行渲染成 01——凭空造出一个
                        "排名"。跳号（01、03）才是诚实的读数：那不是缺行，是被筛掉了。 */}
                    {rows.map(({ row, index }) => (
                      <tr
                        key={row.ts_code}
                        className="border-b border-border/70 align-top transition hover:bg-elevated/50"
                      >
                        {/* 序号是**板块内**的：跨板块连号会读成一张全市场排名，而这一页的
                            排序只在一个板块内部有意义。 */}
                        <td className="px-5 py-4 text-xs text-muted">
                          {String(index + 1).padStart(2, '0')}
                        </td>
                        <td className="py-4 pr-4">
                          <div className="flex items-center gap-2">
                            <Link
                              to={`/stock/${row.ts_code}`}
                              state={{ from: '/tomorrow' }}
                              className="font-medium hover:text-primary"
                            >
                              {row.name}
                            </Link>
                            <Link
                              aria-label={`${row.name}的买卖策略`}
                              title="买卖策略详情"
                              to={`/strategy?code=${row.ts_code}`}
                              className="text-muted hover:text-primary"
                            >
                              <Crosshair size={15} />
                            </Link>
                          </div>
                          <p className="mt-0.5 text-xs text-muted">
                            {row.ts_code} · {row.industry}
                          </p>
                          <p className="mt-0.5 text-xs text-muted">样本 {row.bars} 个交易日</p>
                        </td>
                        <BuyPriceCell row={row} />
                        <HoldCell row={row} />
                        <SellPriceCell row={row} />
                        <td className="py-4 pr-5">
                          <span className={`chip whitespace-nowrap ${ACTION_STYLE[row.action]}`}>
                            {row.action_label}
                          </span>
                          <p className={`mt-2 text-xs leading-5 ${VERDICT_TONE[row.honesty.verdict] ?? 'text-muted'}`}>
                            {row.honesty.strategy_label} · 实测 {row.honesty.trade_count} 笔
                          </p>
                          <p
                            className="mt-1 max-w-80 text-xs leading-5 text-muted"
                            title={row.honesty.caveat}
                          >
                            {row.honesty.verdict_text}
                          </p>
                        </td>
                      </tr>
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 不足 20 根的标的**不进表**：20 根以下 ATR14/MA20 无定义、止损退化成"现价×0.92"，
          把它渲染成候选行里的四个 `--` 会读成"这只是没问题、只是缺数字"。 */}
      {skipped.length > 0 ? (
        <div className="mt-4">
          <Notice tone="info">
            <div>
              <p className="text-sm font-medium">另有 {skipped.length} 只候选因样本太短未列入上表</p>
              <ul className="mt-2 space-y-1 text-xs leading-5">
                {skipped.map((item) => (
                  <li key={item.ts_code}>
                    {item.name || item.ts_code}（{item.ts_code}
                    {item.industry ? ` · ${item.industry}` : ''}）：{item.reason}
                  </li>
                ))}
              </ul>
            </div>
          </Notice>
        </div>
      ) : null}

      {data ? (
        <p className="mt-4 flex items-center gap-1.5 text-xs text-muted">
          <CalendarRange size={13} />
          四个数字都以库内最后一根收盘为基准；盘中价格变动不会改变它们，第二天开盘前请重新看一眼。
        </p>
      ) : null}
    </section>
  )
}

/** 推荐买入价格：一个区间 + 它的基准价。`entry_zone.note` 逐字进 title，不改写。 */
function BuyPriceCell({ row }: { row: SimplePlanRow }) {
  return (
    <td className="py-4 pr-4 whitespace-nowrap" title={row.entry_zone.note}>
      <span className="metric font-semibold">
        {num(row.entry_zone.low)} – {num(row.entry_zone.high)}
      </span>
      <p className="mt-0.5 text-xs text-muted">基准 {num(row.entry_zone.reference)}</p>
      <PriceNote stock={row} />
    </td>
  )
}

/**
 * 推荐持有天数。**来源必须写在脸上**：`measured` 是本标的实测平均持有期，
 * `heuristic` 是策略族的经验区间。不标来源就等于把"我们不知道"换成了"我们建议"。
 */
function HoldCell({ row }: { row: SimplePlanRow }) {
  const measured = row.expected_hold.basis === 'measured'
  return (
    <td className="py-4 pr-4 whitespace-nowrap" title={row.expected_hold.note}>
      <span className={`metric font-semibold ${measured ? '' : 'text-muted'}`}>
        {measured
          ? `约 ${row.expected_hold.bars} 个交易日`
          : `${row.expected_hold.low}–${row.expected_hold.high} 个交易日`}
      </span>
      <div className="mt-0.5">
        <span
          className={`chip ${
            measured
              ? 'border-positive/20 bg-positive/10 text-positive'
              : 'border-warning/20 bg-warning/10 text-warning'
          }`}
        >
          {measured ? '实测均值' : '经验值'}
        </span>
      </div>
    </td>
  )
}

/**
 * 推荐卖出价格：两个出场价，含义完全不同，所以分开写。
 *
 * - **止损**是"错了就认"的价位，用 A 股配色的绿（不利方向）。
 * - **移动止盈位**是"自最高价回落这么多就卖"的**触发价**，未建仓时它经常**低于现价**
 *   （取最近 10 根最高价 − 2×ATR）。它不是目标价、不是承诺收益，所以用 amber 而不是红——
 *   拿它当"预期卖点"来读会高估这套规则的收益。
 *
 * `level` 为 null（ATR 缺失）时显示 `--`，**绝不退化成 0.00**——那会是一个能成交的价位。
 */
function SellPriceCell({ row }: { row: SimplePlanRow }) {
  const level = row.take_profit.level
  return (
    <td className="py-4 pr-4 whitespace-nowrap">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs text-muted">止损</span>
        <span className="metric font-semibold text-negative">{num(row.stop_loss.recommended)}</span>
      </div>
      <p className="mt-0.5 text-right text-xs text-muted">
        距基准 {signedPct(row.stop_loss.distance_pct)}
      </p>
      <div className="mt-2 flex items-baseline justify-between gap-3">
        <span className="text-xs text-muted">移动止盈位</span>
        <span className={`metric font-semibold ${level == null ? 'text-muted' : 'text-warning'}`}>
          {num(level)}
        </span>
      </div>
      <p className="mt-0.5 text-right text-xs text-muted">自最高价回落触发</p>
    </td>
  )
}
