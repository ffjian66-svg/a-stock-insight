# A 股洞察终端（a-stock-insight）

本地优先的 A 股实时研究与**可解释综合评分**系统。后端用 TuShare / AkShare 拉真实行情与新闻，前端 React 负责研究界面；不依赖任何云服务，数据和评分证据全部落在本地 SQLite。

> ⚠️ 仅供研究参考，不构成投资建议。所有评分/情绪摘要都可追溯证据，请独立判断、控制风险。

## 特性

- **四维可解释评分**：趋势与动量、质量与估值、新闻情绪、波动风险 → `0..100` 综合分，逐维度给出依据（何时用、覆盖率多少一清二楚）。
- **实时行情与指数广度**：自选股 30s 轮询报价，首页指数 + 全市场涨跌广度。
- **新闻情绪分析**：自选 + 综合评分 TOP-N 股票按代码拉东财新闻，接入 LLM 做结构化情绪/事件标签/摘要；无 LLM Key 时自动降级为原文摘要、情绪不参与评分。
- **首页综合评分大屏**：`GET /screener/top` 榜单（10/20/50 可切换），每行带最新新闻简报。
- **智能选股 / 个股档案**：筛选器条件保存在 URL、返回导航跟随来源；个股页含 K 线、新闻时间线、评分证据展开。
- **全流程可测试**：后端 pytest（mock 演示源全隔离）、前端 vitest + MSW 拦截。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.10+ · FastAPI · SQLAlchemy 2 · APScheduler · SQLite(WAL) |
| 行情/财务 | TuShare Pro（fina_indicator 等，需自己申请 Token） |
| 新闻 | AkShare `stock_news_em`（东财个股新闻，免 Token） |
| 前端 | React 18 · Vite · TailwindCSS · TanStack Query · Recharts |
| LLM（可选） | 任意 OpenAI 兼容接口（默认 DeepSeek 配置示例） |

## 目录结构

```
.
├── backend
│   ├── app
│   │   ├── api/           # FastAPI 路由与契约(schemas)
│   │   ├── core/          # 配置(env→pydantic-settings)
│   │   ├── db/            # SQLAlchemy 模型 / session(WAL)
│   │   ├── providers/     # tushare / akshare / mock 数据源与 LLM
│   │   ├── services/      # 同步、指标、评分、新闻分析、操作时机
│   │   └── scheduler.py   # 后台定时同步
│   └── tests/             # pytest（隔离临时库）
├── frontend/              # React 界面（src/pages、components、lib、test）
├── .qoder/specs/          # 产品规格说明（中文）
└── pyproject.toml         # 后端依赖与 pytest/ruff/mypy 配置
```

## 快速开始（演示模式，无需任何 Token）

后端默认 `USE_MOCK_DATA=true`，用本地合成行情即可跑通全部界面。

```bash
# 后端
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --app-dir backend --port 8000
```

```bash
# 前端（另开终端）
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173，已代理 /api → :8000
```

首次启动自动 seed 演示数据（8 只股票 + 90 天 K 线 + 演示新闻与评分）。可用 `POST /api/v1/sync/jobs` 手动触发各同步 job（`quotes|news|market|scores|fundamentals|calendar|stocks|bootstrap`）。

## 切换真实数据

复制 `.env.example` 为 `.env` 并填写：

| 变量 | 说明 |
|---|---|
| `USE_MOCK_DATA=false` | 关闭演示源 |
| `TUSHARE_TOKEN=...` | TuShare Pro Token（积分需覆盖所用接口，新闻已改用 AkShare 不再依赖 TuShare news） |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 可选：新闻情绪 LLM（OpenAI 兼容） |
| `NEWS_TOP_N=50` | 真实模式下新闻同步除自选外并入综合评分前 N |
| `ALLOWED_HOSTS` | 非本机访问时加入你的地址 |

## 测试与代码质量

```bash
pytest                    # 后端全部测试
ruff check .              # 后端 lint（100 列）
cd frontend && npm test   # 前端 vitest（MSW 拦截网络）
cd frontend && npm run build
```

## 免责声明

本项目为个人研究工具：行情/财务/新闻均来自第三方公开数据源，可能有延迟或错误；LLM 情绪仅为文本的自动化解读。任何评分与摘要都不构成投资建议。
