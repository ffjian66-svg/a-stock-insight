# A 股实时分析系统实施计划

## Context

当前目标目录为空。需要从零搭建一个本地单用户的 A 股研究系统：以 TuShare Pro 为商业数据源，完成全市场批量更新和自选股交易时段准实时刷新，并用可解释的技术面、基本面、新闻情绪评分筛选候选股票。系统仅提供研究辅助，不执行交易，也不输出确定性买卖承诺。

采用选择性借鉴策略：参考相邻 `daily_stock_analysis` 项目的 Provider、交易日历与 FastAPI 组织方式，但不复制其庞大的 Agent、通知、回测和多市场体系。

## 技术与产品方案

- 后端：Python 3.11、FastAPI、SQLAlchemy 2、SQLite WAL、APScheduler、TuShare SDK、pandas/NumPy、OpenAI-compatible LLM client。
- 前端：React、Vite、TypeScript、Tailwind CSS、TanStack Query、React Router、Recharts。
- 运行形态：本机单进程服务，默认绑定 `127.0.0.1`；开发期前后端分离，生产构建由 FastAPI 托管静态文件。
- 数据口径：全市场基础资料、日线、每日指标和财务摘要批量更新；仅自选股在开盘时段按可配置周期刷新最新报价。
- 新闻分析：增量采集与自选股相关的 TuShare 新闻；LLM 仅返回结构化摘要、情绪、事件标签与置信度，失败时保留原始新闻并明确降级。
- 设计方向：参考 Linear/Stripe 式高密度专业终端；深墨蓝中性色、冷青强调色，A 股红涨绿跌并同时显示正负号；克制渐变、清晰层级、固定风险声明。行情看板不依赖装饰图片，品牌空状态/数据流背景资产会使用生成图并存入前端资源目录。

## 实施步骤

### 1. 初始化工程与安全基线

创建根级工程配置、后端包和前端 Vite 工程：

- `pyproject.toml`：运行依赖及 Ruff、Mypy、Pytest 配置。
- `package.json`：统一启动、检查和构建命令。
- `.env.example`、`.gitignore`：仅声明变量名；忽略 `.env`、数据库、日志及构建产物。
- `backend/app/core/config.py`：读取 `TUSHARE_TOKEN`、OpenAI-compatible `LLM_API_KEY/BASE_URL/MODEL`、频率预算与数据库配置；敏感字段禁止序列化或写日志。
- `backend/app/main.py`：FastAPI 生命周期、受限 CORS、异常处理、API 路由和静态资源托管。

不创建登录系统；若监听非回环地址则要求配置本地 API Key。

### 2. 建立数据库与标准领域模型

在 `backend/app/db/models.py`、`session.py` 和 `backend/alembic/` 中实现：

- `Stock`、`TradeCalendar`、`DailyBar`、`LatestQuote`
- `FundamentalSnapshot`、`WatchlistItem`
- `NewsArticle`、`NewsAnalysis`
- `ScoreSnapshot`、`SyncRun`、`ProviderStatus`

所有时序数据设置业务唯一键以支持 upsert；记录 `source`、`as_of`、`fetched_at`、`is_stale`。同步任务保存状态、游标、调用量和错误分类，支持中断恢复。

### 3. 实现 Provider 契约与 TuShare 接入

- `backend/app/providers/base.py`：定义股票列表、交易日历、日线、每日指标、财务、实时行情、新闻等能力及统一 DTO/异常。
- `backend/app/providers/tushare.py`：封装 TuShare 字段映射、分页、日期和股票代码标准化。
- `backend/app/providers/rate_limiter.py`：配置化每分钟/每日预算、有限指数退避、冷却和能力熔断。
- `backend/app/providers/mock.py`：无密钥启动、测试和 UI 演示数据。
- `backend/app/providers/llm.py`：OpenAI-compatible 结构化输出；限制输入长度、超时、禁止工具调用，并防御新闻文本中的提示注入。

权限不足不重试；限频进入冷却；网络错误有限重试。实时接口不可用时回退最新日线并标记“非实时”，财务或新闻缺失则降低覆盖率，不伪造数据。

### 4. 实现同步调度

在 `backend/app/services/sync.py`、`backend/app/scheduler/jobs.py` 实现幂等任务：

- 每周同步股票列表、交易日历。
- 交易日收盘后同步全市场日线与每日估值。
- 晚间增量同步最新财务指标。
- 交易时段仅轮询自选股报价，默认 30 秒，避开午休与非交易日。
- 自选股新闻每 15 分钟增量同步，仅将未分析内容提交 LLM。
- 启动时仅补偿遗漏任务，不无条件全量拉取；数据库锁防止手动与定时任务重叠。

### 5. 实现可解释分析引擎

- `backend/app/services/indicators.py`：MA5/10/20/60、MACD、RSI、布林带、波动率、量价趋势。
- `backend/app/services/fundamentals.py`：估值、营收/净利增长、ROE、负债与现金流规则。
- `backend/app/services/news_analysis.py`：摘要、情绪、事件类型、置信度和模型版本。
- `backend/app/services/scoring.py`：初始权重为技术 40%、基本面 30%、新闻 20%、流动性/风险 10%。

每项评分保存指标值、阈值、贡献、日期、来源和解释。缺失分项时按可用权重归一化并展示覆盖率；覆盖率低于阈值则不生成总分。结果表述为“研究候选/风险等级”，不表述为投资建议。

### 6. 提供 API

在 `backend/app/api/v1/` 提供 Pydantic 强类型接口：

- `GET /health`、`GET /system/status`
- `GET /market/overview`
- `GET /stocks/search`
- `GET /stocks/{ts_code}`、`/daily-bars`、`/news`、`/score-explanation`
- `GET/POST/DELETE /watchlist`、`GET /watchlist/quotes`
- `GET /screener`：分页、排序、行业、分数、估值和数据覆盖率筛选
- `POST /sync/jobs`、`GET /sync/jobs/{id}`

系统状态仅返回 Token 是否配置及 Provider 能力状态，绝不回传 Token。限制分页、手动同步频率和参数边界。

### 7. 建立视觉系统与交互看板

先在 `frontend/src/index.css` 和 `frontend/tailwind.config.ts` 定义完整语义化设计 token、渐变、阴影、动效和红涨绿跌状态，再实现可复用组件：

- `frontend/src/components/ui/`：Button、Card、Badge、Table、Tabs、Drawer、Toast、Skeleton。
- `frontend/src/components/charts/`：指数走势、K 线/成交量、评分雷达或分项条。
- `frontend/src/layouts/TerminalLayout.tsx`：侧边导航、全局搜索、数据源状态、更新时间。
- `frontend/src/pages/DashboardPage.tsx`：指数概览、自选股、市场宽度、候选股票、同步状态、新闻流。
- `frontend/src/pages/ScreenerPage.tsx`：可组合筛选、排序、分页与加入自选。
- `frontend/src/pages/StockDetailPage.tsx`：行情、技术指标、财务、新闻和评分解释抽屉。
- `frontend/src/pages/SettingsPage.tsx`：Provider 能力、刷新策略和隐私提示，仅显示密钥配置状态。
- `frontend/src/api/client.ts`：统一 API 错误、新鲜度与降级状态处理。

所有按钮必须有真实状态更新或 API 行为；无 Token、无数据、限频、权限不足和 LLM 失败均提供明确可操作提示。主界面持续显示“仅供研究参考，不构成投资建议；市场有风险，投资需谨慎”。

### 8. 测试与验证

后端：

- `backend/tests/unit/`：代码标准化、指标、评分、覆盖率、限频和异常分类。
- `backend/tests/contract/`：TuShare 与 Mock Provider 契约。
- `backend/tests/integration/`：临时 SQLite、同步幂等、任务恢复、API 和降级路径。
- 执行 `ruff check`、`mypy backend`、`pytest`。

前端：

- Vitest + Testing Library + MSW 覆盖自选增删、筛选、评分解释和失败提示。
- 执行 ESLint、`tsc --noEmit`、Vitest、Vite production build。
- 启动 Mock 模式完成端到端用户故事：初始化数据 → 搜索股票 → 加入自选 → 刷新报价 → 进入个股页 → 查看评分证据 → 在筛选器筛出候选。
- 用 browser-agent 检查控制台/网络错误并在 1440px、1280px 视口截图验收；确认功能匹配、视觉质量、布局与对比度均通过。
- 最后通过 `mcp__quest__run_preview` 向用户展示可交互页面。

## 关键参考与关键文件

参考但不直接复制：

- `/Users/xiaojian/program/ai_agent/ai_stock_analysis/daily_stock_analysis/data_provider/base.py`
- `/Users/xiaojian/program/ai_agent/ai_stock_analysis/daily_stock_analysis/src/core/trading_calendar.py`
- `/Users/xiaojian/program/ai_agent/ai_stock_analysis/daily_stock_analysis/api/app.py`

本次实现的关键文件：

- `/Users/xiaojian/program/ai_agent/ai_stock_new/backend/app/providers/base.py`
- `/Users/xiaojian/program/ai_agent/ai_stock_new/backend/app/providers/tushare.py`
- `/Users/xiaojian/program/ai_agent/ai_stock_new/backend/app/db/models.py`
- `/Users/xiaojian/program/ai_agent/ai_stock_new/backend/app/services/sync.py`
- `/Users/xiaojian/program/ai_agent/ai_stock_new/backend/app/services/scoring.py`
- `/Users/xiaojian/program/ai_agent/ai_stock_new/frontend/src/index.css`
- `/Users/xiaojian/program/ai_agent/ai_stock_new/frontend/src/pages/DashboardPage.tsx`
- `/Users/xiaojian/program/ai_agent/ai_stock_new/frontend/src/pages/StockDetailPage.tsx`
