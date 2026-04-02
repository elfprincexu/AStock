<h1 align="center">
  AStock - A股全栈量化数据平台
</h1>

<p align="center">
  <strong>集成 6 大数据源、10 个功能模块，覆盖自选股、行情、选股、量化、AI 诊断、策略交易的完整投研平台</strong>
</p>

<p align="center">
  <a href="#快速开始"><img src="https://img.shields.io/badge/-快速开始-blue?style=for-the-badge" alt="快速开始"></a>
  <a href="#docker-全容器化部署"><img src="https://img.shields.io/badge/-Docker%20部署-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker 部署"></a>
  <a href="DEPLOY.md"><img src="https://img.shields.io/badge/-部署指南-green?style=for-the-badge" alt="部署指南"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Vue-3.4-4FC08D?logo=vue.js&logoColor=white" alt="Vue 3">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white" alt="Redis">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

---

## 项目亮点

- **6 大数据源自动 fallback** — akshare、tushare、baostock、东方财富、新浪、腾讯，任一数据源不可用时自动切换
- **50 因子量化选股引擎** — 跨截面多因子分析 + IC 验证 + LightGBM 增强 + Walk-forward 回测
- **AI 智能诊断** — 接入 Gemini / OpenAI / DeepSeek / 通义千问等 LLM，一键生成技术分析报告
- **条件单策略交易** — 支持模拟盘 + 平安证券 QMT 实盘，多步骤条件触发自动执行
- **Docker 一键部署** — 7 个容器全容器化，无需安装 Python / Node.js
- **完整权限体系** — JWT 认证 + 角色权限 + 细粒度模块访问控制

---

## 界面预览
<p align="center">
  <img src="docs/screenshots/dashboard.png" width="80%" alt="行情数据">
</p>

<details>
<summary>查看更多截图</summary>

| 功能 | 截图 |
|------|------|
| 自选股管理 | <img src="docs/screenshots/stocks.png" width="600"> |
| K 线图 | <img src="docs/screenshots/kline.png" width="600"> |
| 智能选股 | <img src="docs/screenshots/screener.png" width="600"> |
| 量化分析 | <img src="docs/screenshots/quant.png" width="600"> |
| AI 诊断 | <img src="docs/screenshots/ai.png" width="600"> |
| 策略交易 | <img src="docs/screenshots/trade.png" width="600"> |

</details>

---

## 目录

- [功能概览](#功能概览)
- [架构概览](#架构概览)
- [快速开始](#快速开始)
- [Docker 全容器化部署](#docker-全容器化部署)
- [数据源架构](#数据源架构)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [配置说明](#配置说明)
- [数据库](#数据库)
- [API 概览](#api-概览)
- [常见问题](#常见问题)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

---

## 功能概览

| 模块 | 说明 |
|------|------|
| **自选股管理** | 股票搜索、添加/编辑/删除自选股，支持收藏夹、分页展示（含行业、PE/PB/市值） |
| **行情数据** | 实时快照、日 K 线（前复权）、分时数据、股票档案，ECharts 可视化 |
| **6 数据源聚合** | akshare、tushare、baostock、东方财富、新浪、腾讯，优先级自动 fallback |
| **智能选股** | 10 个预设策略 + 自定义筛选（基本面 + 技术面），「放量上涨 -> 缩量回调」模式检测 |
| **量化分析** | 50 因子跨截面选股引擎，IC 分析、LightGBM ML 增强、组合构建、Walk-forward 回测 |
| **AI 智能诊断** | 基于 litellm 的 LLM 股票分析，技术指标计算 + 结构化诊断报告 |
| **策略交易** | 条件单引擎，模拟交易（纸面交易）+ 实盘交易（平安证券 QMT） |
| **相关资讯 + 散户情绪** | 东方财富新闻/公告 + 股吧散户情绪评分（关键词情感分析，0-100 量化） |
| **定时更新** | Celery Beat 驱动，每日自动全量更新行情 / K线 / 基本面 / 行业 |
| **配置管理** | Web 端运行时配置（数据源、LLM、券商、端口），写入 `.env` 即时生效 |
| **用户管理** | JWT 认证 + bcrypt 密码加密 + 角色权限控制（admin / user），细粒度模块权限 |

---

## 架构概览

```
┌──────────────┐     ┌───────────────────────────────┐     ┌────────────┐
│   前端 (Vue)  │────▶│   后端 (FastAPI)               │────▶│ PostgreSQL │
│  :5174       │     │   :8000                       │     │  :5432     │
└──────────────┘     │                               │     └────────────┘
                     │  ┌─────────┐ ┌────────┐       │           │
                     │  │ Celery  │ │ Celery │       │     ┌─────┴──────┐
                     │  │ Worker  │ │  Beat  │       │     │  Grafana   │
                     │  └────┬────┘ └────────┘       │     │  :3000     │
                     └───────┼───────────────────────┘     └────────────┘
                             │
                     ┌───────┴───────┐
                     │    Redis      │
                     │    :6379      │
                     └───────────────┘

      ┌──────────────── StockDataAggregator ────────────────┐
      │  优先级 fallback:                                     │
      │  akshare → tushare → baostock → eastmoney → sina    │
      │  → tencent                                           │
      │                                                      │
      │  ┌─────────┐ ┌─────────┐ ┌──────────┐               │
      │  │ akshare │ │ tushare │ │ baostock │               │
      │  └─────────┘ └─────────┘ └──────────┘               │
      │  ┌──────────────┐ ┌──────────┐ ┌───────────────┐    │
      │  │ 东方财富 API  │ │ 新浪财经  │ │  腾讯财经 API │    │
      │  │ push2/search │ │ hq.sinajs│ │  qt.gtimg.cn  │    │
      │  └──────────────┘ └──────────┘ └───────────────┘    │
      └──────────────────────────────────────────────────────┘
```

---

## 快速开始

### 环境要求

| 软件 | 最低版本 | 验证命令 |
|------|----------|----------|
| Docker & Docker Compose | Docker 20+, Compose v2+ | `docker --version && docker compose version` |
| Python | 3.12+ (推荐 pyenv 管理) | `python --version` |
| Node.js | 18+ | `node --version` |
| npm | 8+ | `npm --version` |

### 一键启动

```bash
# Linux / macOS
bash start.sh

# Windows (PowerShell)
.\start.ps1
```

脚本会依次启动 Docker 容器 (PostgreSQL / Redis / Grafana) → FastAPI 后端 → Celery Worker + Beat → Vue 前端。

### 手动分步启动

```bash
# 1. 启动基础设施
docker compose up -d

# 2. 安装依赖
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..

# 3. 启动后端 API
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. 启动 Celery Worker (新终端)
cd backend && celery -A celery_app.celery worker --loglevel=info
# Windows 需加 --pool=solo

# 5. 启动 Celery Beat (新终端, 可选)
cd backend && celery -A celery_app.celery beat --loglevel=info

# 6. 启动前端 (新终端)
cd frontend && npm run dev
```

### 访问地址

| 服务 | 地址 |
|------|------|
| 前端管理界面 | http://localhost:5174 |
| 后端 Swagger API | http://localhost:8000/docs |
| Grafana 看板 | http://localhost:3000 (默认账号 admin/admin) |

> **默认管理员账号**: `AStock` / `AStock123!`（首次启动自动创建）

### 停止服务

```bash
bash stop.sh        # Linux / macOS
.\stop.ps1          # Windows (PowerShell)
```

### 运行测试

```bash
pip install -r tests/requirements.txt
python -m pytest tests/ -v
```

---

## Docker 全容器化部署

适用于在其他机器上独立部署，**无需安装 Python、Node.js**，只需 Docker 和 Docker Compose。

### 环境要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| Docker | 20.10+ | 容器运行时 |
| Docker Compose | 2.0+ | 服务编排（Docker Desktop 自带） |
| 磁盘空间 | 10 GB+ | 镜像约 3GB，数据库按股票数增长 |
| 内存 | 4 GB+ | 量化分析模块需要较多内存 |

### 一键部署

```bash
# 1. 创建环境配置
cp .env.example .env
# 编辑 .env: 修改数据库密码、JWT 密钥、LLM 配置等

# 2. 构建并启动全部 7 个服务
docker compose -f docker-compose.prod.yml up -d --build

# 3. 检查服务状态
docker compose -f docker-compose.prod.yml ps
```

### 服务组成

| 容器 | 镜像 | 端口 | 说明 |
|------|------|------|------|
| `astock-frontend` | 自建 (Nginx + Vue) | 80 | 前端 SPA + API 反向代理 |
| `astock-backend` | 自建 (Python 3.12) | 8000 | FastAPI REST API |
| `astock-celery-worker` | 同 backend | -- | 异步任务执行 (4 并发) |
| `astock-celery-beat` | 同 backend | -- | 定时任务调度 |
| `astock-postgres` | postgres:16-alpine | 5432 | 数据库 |
| `astock-redis` | redis:7-alpine | 6379 | 消息队列 & 缓存 |
| `astock-grafana` | grafana:11.1.0 | 3000 | 可视化监控 |

### Docker 架构图

```
┌─────────────────────────────────────────────────────┐
│                   Docker Network                     │
│                                                      │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐       │
│  │ Frontend │    │ Backend  │    │  Celery  │       │
│  │  (Nginx) │───▶│ (FastAPI)│    │  Worker  │       │
│  │  :80     │/api│  :8000   │    │          │       │
│  └──────────┘    └────┬─────┘    └────┬─────┘       │
│                       │               │              │
│                  ┌────▼───┐      ┌────▼───┐         │
│                  │PostgreSQL│     │  Redis │         │
│                  │  :5432  │     │  :6379 │         │
│                  └─────────┘     └────────┘         │
│                       │                              │
│                  ┌────▼───┐    ┌──────────┐         │
│                  │Grafana │    │  Celery  │         │
│                  │ :3000  │    │   Beat   │         │
│                  └────────┘    └──────────┘         │
└─────────────────────────────────────────────────────┘
```

### 数据初始化

首次部署后数据库为空，有两种方式获取数据：

**方式一：Web 界面**（推荐） — 登录后搜索添加股票，点击「抓取」按钮获取数据。

**方式二：全量下载脚本**（~5700 只 A 股）：

```bash
# 进入 backend 容器
docker compose -f docker-compose.prod.yml exec backend bash

# 全量下载（K 线 + 基本面 + 行业，预计 2-4 小时）
python /app/scripts/download_all_data.py

# 仅 K 线 / 仅基本面
python /app/scripts/download_all_data.py --klines-only
python /app/scripts/download_all_data.py --fundamentals-only
```

### 常用运维命令

```bash
# 查看日志
docker compose -f docker-compose.prod.yml logs -f backend

# 重启单个服务
docker compose -f docker-compose.prod.yml restart backend

# 停止所有服务
docker compose -f docker-compose.prod.yml down

# 数据库备份
docker compose -f docker-compose.prod.yml exec postgres \
  pg_dump -U astock astock > backup_$(date +%Y%m%d).sql

# 更新部署（重新构建镜像，数据不丢失）
docker compose -f docker-compose.prod.yml up -d --build
```

> 更多运维操作请参阅 [DEPLOY.md](DEPLOY.md)。

---

## 数据源架构

系统通过 `StockDataAggregator` 聚合 6 个 A 股数据提供商，按配置优先级自动 fallback。

### 数据源能力矩阵

| 提供商 | 实时行情 | K线 (完整) | K线 (不完整) | 基本面 | 行业 | 搜索 |
|--------|---------|-----------|-------------|--------|------|------|
| **akshare** | 包装 Sina (同步, 较慢) | `ak.stock_zh_a_daily` | -- | -- | `ak.stock_individual_info_em` | -- |
| **tushare** | 不支持 | `pro.daily` (需 token) | -- | -- | -- | -- |
| **baostock** | 不支持 | `bs.query_history_k_data_plus` (免费) | -- | -- | -- | -- |
| **东方财富** | `push2.eastmoney.com` | `push2his.eastmoney.com` | -- | -- | -- | `searchapi.eastmoney.com` |
| **新浪财经** | `hq.sinajs.cn` (GB18030) | -- | `money.finance.sina.com.cn` | -- | -- | -- |
| **腾讯财经** | `qt.gtimg.cn` (GBK) | -- | `web.ifzq.gtimg.cn` | PE/PB/市值 | -- | -- |

### Fallback 策略

| 数据类型 | Fallback 链 | 说明 |
|----------|------------|------|
| **实时行情** | sina → tencent → eastmoney → akshare | 快速 HTTP 优先，重量级 akshare 兜底 |
| **日 K 线** | 阶段一: akshare → tushare → baostock → eastmoney; 阶段二: sina → tencent | 阶段二数据不完整，仅作最后兜底 |
| **基本面** | 腾讯财经 (直接 HTTP) | 始终使用腾讯，快速可靠 |
| **行业** | 东方财富 via akshare (尽力获取) | 仅在字段为空时获取 |
| **搜索** | 仅东方财富 | 其他数据源不提供搜索 API |

### 统一抓取流水线

手动抓取与定时抓取共享相同的 4 步流水线：

| 步骤 | 操作 | 数据源 | 写入表 | 跳过条件 |
|------|------|--------|--------|----------|
| 1 | 实时行情 | 聚合器 fallback | `quote_snapshots` | 不跳过 |
| 2 | 日 K 线 (缺口感知) | 聚合器两阶段 fallback | `daily_klines` | 最新 K 线 >= 预期交易日 |
| 3 | 基本面 (PE/PB/市值) | 腾讯直接 HTTP | `stock_profiles` | 不跳过 |
| 4 | 行业 | 东方财富 via akshare | `stock_profiles.industry` | 已有行业值 |

---

## 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端 | Vue 3 + Vite + Element Plus + ECharts | 3.4 / 5.x / 2.5 / 6.x |
| 后端 API | Python + FastAPI + SQLAlchemy (async) + Pydantic | 3.12 / 0.115 / 2.0 |
| 任务队列 | Celery + Redis | 5.4 / 7.x |
| 数据库 | PostgreSQL | 16 |
| 可视化 | Grafana | 11.1 |
| AI 分析 | litellm (Gemini / OpenAI / Anthropic / DeepSeek / 国产大模型) | 1.40+ |
| 量化引擎 | LightGBM + scikit-learn + NumPy + Pandas | -- |
| 实盘交易 | xtquant (平安证券 QMT) | -- |
| 认证 | JWT + bcrypt | -- |
| 容器化 | Docker + Docker Compose | 20+ / v2+ |

---

## 项目结构

```
AStock/
├── docker-compose.yml           # 基础设施 (开发用: PostgreSQL + Redis + Grafana)
├── docker-compose.prod.yml      # 全容器化生产部署 (7 个服务)
├── start.sh / stop.sh           # 一键启动/停止 (Linux/macOS)
├── start.ps1 / stop.ps1         # 一键启动/停止 (Windows)
├── .env.example                 # 环境变量模板
├── DEPLOY.md                    # Docker 部署详细指南
├── CONTRIBUTING.md              # 贡献指南
├── LICENSE                      # MIT 许可证
│
├── backend/
│   ├── Dockerfile               # 后端 Docker 镜像
│   ├── requirements.txt         # Python 依赖
│   ├── celery_app.py            # Celery 配置 + 定时调度
│   └── app/
│       ├── main.py              # FastAPI 入口, CORS, 自动建表
│       ├── config.py            # 全局配置 (数据源/LLM/JWT 等)
│       ├── models.py            # ORM 模型 (17 张表)
│       ├── schemas.py           # Pydantic 请求/响应模型
│       ├── auth.py              # 认证 (bcrypt + JWT)
│       ├── utils.py             # 工具函数
│       ├── tasks.py             # Celery 任务
│       │
│       ├── routers/             # === API 路由 (10 个模块) ===
│       │   ├── auth.py          # 用户认证
│       │   ├── stocks.py        # 自选股管理
│       │   ├── quotes.py        # 行情查询
│       │   ├── screener.py      # 智能选股
│       │   ├── schedule.py      # 定时更新
│       │   ├── newssentiment.py # 资讯情绪
│       │   ├── ai.py            # AI 诊断
│       │   ├── config.py        # 配置管理
│       │   ├── trade.py         # 策略交易
│       │   └── quant.py         # 量化分析
│       │
│       └── services/            # === 业务逻辑层 ===
│           ├── aggregator.py    # 多数据源聚合 (fallback)
│           ├── akshare_client.py / tushare_client.py / baostock_client.py
│           ├── eastmoney.py / sina.py / tencent.py
│           ├── screener.py      # 选股引擎
│           ├── ai_analysis.py   # AI 分析核心
│           ├── quant_engine.py  # 50 因子量化引擎
│           ├── trade_engine.py  # 条件单引擎
│           ├── news_service.py / sentiment_service.py
│           └── brokers/         # 券商适配 (平安 QMT)
│
├── frontend/
│   ├── Dockerfile               # 前端 Docker 镜像 (Node → Nginx)
│   ├── nginx.conf               # Nginx 配置
│   └── src/
│       ├── App.vue              # 完整管理界面 (9 个功能区)
│       └── api/index.js         # Axios API 封装 + JWT 拦截器
│
├── scripts/
│   └── download_all_data.py     # 全 A 股数据下载器 (~5,700 只)
│
├── tests/                       # 集成测试 (100+ tests)
│   ├── test_eastmoney_api.py    # 东方财富 (17 tests)
│   ├── test_sina_api.py         # 新浪 (15 tests)
│   ├── test_tencent_api.py      # 腾讯 (16 tests)
│   ├── test_aggregator.py       # 聚合器 (14 tests)
│   ├── test_database.py         # 数据库 (15 tests)
│   ├── test_backend_api.py      # API (9 tests)
│   ├── test_grafana.py          # Grafana (9 tests)
│   └── test_fetch_pipeline.py   # 端到端 (5 tests)
│
├── data/                        # 静态数据文件
│   ├── all_a_shares.csv         # 全 A 股列表 (~5,682)
│   └── stock_industries.csv     # 行业映射
│
├── grafana/                     # Grafana 自动配置
│   └── provisioning/
│
└── docs/                        # 文档
    ├── eastmoney_fields.md
    └── data_sources.md
```

---

## 配置说明

后端通过 `backend/.env` 文件管理配置，也可通过 Web 界面「配置管理」页面实时修改。

### 核心配置项

```env
# ========== 基础设施 ==========
DATABASE_URL=postgresql+asyncpg://astock:astock123@localhost:5432/astock
REDIS_URL=redis://localhost:6379/0

# ========== 数据源 ==========
DATA_SOURCE_PRIORITY=akshare,tushare,baostock,eastmoney,sina,tencent
DATA_SOURCE_TIMEOUT=10
TUSHARE_TOKEN=              # 可选, tushare K线需要

# ========== LLM / AI 分析 ==========
LITELLM_MODEL=gemini/gemini-2.5-flash
GEMINI_API_KEY=your-api-key
# 支持: gemini/, openai/, anthropic/, deepseek/, dashscope/, moonshot/ 等
# OpenAI 兼容端点: OPENAI_BASE_URL=https://your-llm-service.com/v1/

# ========== 实盘交易 (仅 Windows + QMT, 可选) ==========
# BROKER_ACCOUNT=你的资金账号
# BROKER_QMT_PATH=D:/平安证券/QMT/bin.x64
```

### 支持的 LLM 提供商

| 提供商 | 模型前缀 | 示例 |
|--------|----------|------|
| Google Gemini | `gemini/` | `gemini/gemini-2.5-flash` |
| OpenAI | `openai/` | `openai/gpt-4o-mini` |
| Anthropic | `anthropic/` | `anthropic/claude-3-5-sonnet-20241022` |
| DeepSeek | `deepseek/` | `deepseek/deepseek-chat` |
| 通义千问 | `dashscope/` | `dashscope/qwen-plus` |
| Moonshot/Kimi | `moonshot/` | `moonshot/moonshot-v1-8k` |
| 智谱/硅基流动等 | `openai/` + `OPENAI_BASE_URL` | 需设置对应 base URL |

> 完整配置模板见 [.env.example](.env.example)

---

## 数据库

PostgreSQL 数据库共 **17 张表**，首次启动后端时自动创建。

| 模块 | 表 | 说明 |
|------|-------|------|
| 用户管理 | `users` | 账号、密码哈希、角色、权限 (JSON) |
| 自选股 | `stocks` | 自选股列表（代码、名称、市场） |
| 行情数据 | `daily_klines`, `quote_snapshots` | 日 K 线、实时行情快照 |
| 股票档案 | `stock_profiles` | 行业、PE、PB、总市值 |
| 抓取日志 | `fetch_logs` | 每次抓取状态、数据源、耗时 |
| 配置 | `app_settings` | 键值对存储（定时更新、运行时状态） |
| AI 诊断 | `analysis_history` | AI 分析记录、评分、报告 |
| 策略交易 | `trade_strategies`, `trade_steps`, `trade_conditions`, `trade_executions` | 策略定义 + 执行日志 |
| 量化分析 | `quant_factor_daily`, `quant_ic_history`, `quant_portfolios`, `quant_backtest_results`, `quant_iterations` | 因子 + IC + 组合 + 回测 + 迭代 |

---

## API 概览

后端提供 **10 个路由模块**，完整 API 文档可在 http://localhost:8000/docs (Swagger UI) 交互调试。

| 前缀 | 模块 | 核心端点 |
|------|------|----------|
| `/api/auth` | 用户认证 | 登录、用户 CRUD、权限管理 |
| `/api/stocks` | 自选股 | 搜索、CRUD、抓取（手动/批量/轻量） |
| `/api/quotes` | 行情 | 实时快照、K 线、档案、日志 |
| `/api/screener` | 选股 | 10 预设策略 + 自定义筛选 |
| `/api/schedule` | 定时更新 | 配置、状态、手动触发 |
| `/api/newssentiment` | 资讯情绪 | 新闻公告 + 散户情绪评分 |
| `/api/ai` | AI 诊断 | 异步分析、历史、报告 |
| `/api/config` | 配置管理 | 读写 .env、LLM 连通测试 |
| `/api/trade` | 策略交易 | 策略 CRUD + 券商对接 + 执行日志 |
| `/api/quant` | 量化分析 | 多因子分析 + 回测 + 历史 CRUD + 迭代 |

### 认证

- `POST /api/auth/login` 获取 JWT Token
- 请求头附带 `Authorization: Bearer <token>`
- 默认管理员: `AStock` / `AStock123!`
- 角色: `admin`（全部权限）、`user`（按配置的模块权限）

---

## 常见问题

<details>
<summary><b>东方财富 API 返回空数据？</b></summary>

`push2.eastmoney.com` 对部分 IP 有限制。系统会自动 fallback，查看 `fetch_logs.source` 确认实际数据源。
</details>

<details>
<summary><b>K 线数据不完整？</b></summary>

确认 `DATA_SOURCE_PRIORITY` 包含完整 K 线源 (akshare/tushare/baostock/eastmoney)。sina/tencent 的 K 线缺少成交额和换手率。
</details>

<details>
<summary><b>AI 诊断无法使用？</b></summary>

需配置 LLM 模型和 API Key。在「配置管理」页面设置 `LITELLM_MODEL` 和对应 API Key，点击「测试连接」验证。
</details>

<details>
<summary><b>Celery Worker 在 Windows 启动报错？</b></summary>

Windows 需加 `--pool=solo` 参数: `celery -A celery_app.celery worker --loglevel=info --pool=solo`
</details>

<details>
<summary><b>如何只使用特定数据源？</b></summary>

修改 `DATA_SOURCE_PRIORITY`，如 `DATA_SOURCE_PRIORITY=sina,tencent`。注意搜索功能仅东方财富支持。
</details>

---

## 贡献指南

欢迎任何形式的贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解开发流程和规范。

---

## 许可证

本项目采用 [MIT 许可证](LICENSE) 开源。

> **免责声明**: 本项目仅供学习和研究使用。股市有风险，投资需谨慎。使用本系统进行实盘交易所产生的一切后果由用户自行承担。本项目不构成任何投资建议。
