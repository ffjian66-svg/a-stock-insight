import { useMutation, useQuery } from '@tanstack/react-query'
import { Activity, BarChart3, Binoculars, Plus, Search, Settings, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, ScrollRestoration, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { toast } from '../lib/toast'
import { Change, Toasts } from './Common'

const nav = [
  { to: '/', label: '市场总览', icon: Activity },
  { to: '/screener', label: '智能选股', icon: Binoculars },
  { to: '/stock/600519.SH', label: '个股研判', icon: BarChart3 },
  { to: '/settings', label: '系统设置', icon: Settings },
]

function GlobalSearch() {
  const navigate = useNavigate()
  const location = useLocation()
  const containerRef = useRef<HTMLDivElement>(null)
  const [term, setTerm] = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(term.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [term])

  useEffect(() => {
    if (!open) return
    const handler = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const search = useQuery({
    queryKey: ['search', debounced],
    queryFn: () => api.search(debounced),
    enabled: debounced.length > 0,
    staleTime: 10_000,
  })
  const add = useMutation({
    mutationFn: api.addWatch,
    onSuccess: (result) => {
      toast(result.message)
    },
    onError: (error: Error) => toast(error.message || '加入失败', 'error'),
  })

  const results = search.data ?? []
  const goStock = (code: string) => {
    setOpen(false)
    setTerm('')
    setDebounced('')
    navigate(`/stock/${code}`, { state: { from: location.pathname } })
  }

  return (
    <div ref={containerRef} className="relative w-full max-w-lg">
      <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
      <input
        role="searchbox"
        aria-label="全局搜索股票"
        placeholder="搜索股票代码 / 名称…"
        className="field w-full pl-9"
        value={term}
        onChange={(event) => {
          setTerm(event.target.value)
          setOpen(true)
        }}
        onFocus={() => term.trim() && setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && results.length > 0) goStock(results[0].ts_code)
          if (event.key === 'Escape') setOpen(false)
        }}
      />
      {term ? (
        <button
          aria-label="清空搜索"
          onClick={() => {
            setTerm('')
            setDebounced('')
          }}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted hover:text-foreground"
        >
          <X size={14} />
        </button>
      ) : null}
      {open && term.trim() ? (
        <div className="panel absolute left-0 right-0 top-12 z-40 max-h-96 overflow-y-auto p-2">
          {search.isFetching ? (
            <p className="p-3 text-xs text-muted">正在搜索…</p>
          ) : results.length === 0 ? (
            <p className="p-3 text-xs text-muted">
              {debounced ? '没有匹配的股票，请尝试其它名称或 6 位代码。' : '输入股票名称或代码开始搜索。'}
            </p>
          ) : (
            results.map((stock) => (
              <div
                key={stock.ts_code}
                className="group flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 transition hover:bg-elevated"
                onClick={() => goStock(stock.ts_code)}
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {stock.name}
                    <span className="ml-2 text-xs text-muted">{stock.ts_code}</span>
                  </p>
                  <p className="mt-0.5 text-xs text-muted">{stock.industry}</p>
                </div>
                <span className="metric text-sm font-semibold">
                  {stock.price != null ? `¥ ${stock.price.toFixed(2)}` : '--'}
                </span>
                <Change value={stock.pct_chg} />
                <button
                  aria-label={`将 ${stock.name} 加入自选`}
                  title="加入自选"
                  onClick={(event) => {
                    event.stopPropagation()
                    add.mutate(stock.ts_code)
                  }}
                  className="grid size-8 shrink-0 place-items-center rounded-lg border border-border text-muted opacity-70 transition group-hover:opacity-100 hover:border-primary/40 hover:text-primary"
                >
                  <Plus size={15} />
                </button>
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  )
}

export function Layout() {
  return (
    <div className="min-h-screen bg-page">
      <ScrollRestoration />
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-60 border-r border-border bg-surface/80 p-5 backdrop-blur-xl lg:block">
        <div className="mb-9 flex items-center gap-3 px-2">
          <div className="grid size-10 place-items-center rounded-xl bg-primary/15 text-primary ring-1 ring-primary/30">
            <Sparkles size={20} />
          </div>
          <div>
            <p className="font-semibold">A股洞察</p>
            <p className="text-xs text-muted">INTELLIGENCE TERMINAL</p>
          </div>
        </div>
        <nav className="space-y-1">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
            >
              <Icon size={17} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="absolute bottom-6 left-5 right-5 rounded-xl border border-border bg-elevated/70 p-3 text-xs leading-5 text-muted">
          <span className="mb-1 block text-foreground">研究辅助工具</span>
          评分不构成投资建议，请独立判断并控制风险。
        </div>
      </aside>
      <main className="min-h-screen lg:pl-60">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-4 border-b border-border bg-background/75 px-5 backdrop-blur-xl lg:px-8">
          <div className="hidden shrink-0 md:block">
            <p className="text-sm font-medium">A 股全域研究工作台</p>
            <p className="text-xs text-muted">数据 · 信号 · 证据</p>
          </div>
          <GlobalSearch />
          <div className="ml-auto flex shrink-0 items-center gap-2 text-xs text-muted">
            <span className="size-2 animate-pulseSoft rounded-full bg-negative" />
            本地服务运行中
          </div>
        </header>
        <Outlet />
      </main>
      <footer className="fixed bottom-0 left-0 right-0 z-30 border-t border-warning/20 bg-background/90 py-2 text-center text-[11px] text-warning backdrop-blur lg:left-60">
        所有评分和摘要仅供研究参考，不构成投资建议；市场有风险，投资需谨慎。
      </footer>
      <Toasts />
    </div>
  )
}
