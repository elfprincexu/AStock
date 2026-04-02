# AStock Project Notes

## Build & Test Commands

### Docker 全容器化部署 (推荐用于其他机器)

```bash
# 一键部署（构建 + 启动全部 7 个服务）
cp .env.example .env   # 编辑配置
docker compose -f docker-compose.prod.yml up -d --build

# 查看状态
docker compose -f docker-compose.prod.yml ps

# 查看日志
docker compose -f docker-compose.prod.yml logs -f backend

# 停止
docker compose -f docker-compose.prod.yml down

# 数据库备份
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U astock astock > backup.sql
```

### Linux / macOS (bash)

```bash
# One-click start/stop
bash start.sh
bash stop.sh

# Start Docker services (PostgreSQL, Redis, Grafana)
docker compose up -d

# Start backend
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Start Celery worker
cd backend && celery -A celery_app.celery worker --loglevel=info

# Start Celery beat (scheduled tasks)
cd backend && celery -A celery_app.celery beat --loglevel=info

# Start frontend
cd frontend && npm run dev

# Run all tests
python -m pytest tests/ -v

# Run specific test suites
python -m pytest tests/test_sina_api.py -v
python -m pytest tests/test_tencent_api.py -v
python -m pytest tests/test_aggregator.py -v
```

### Windows (PowerShell)

```powershell
# One-click start/stop
.\start.ps1
.\stop.ps1

# Start Docker services (requires Docker Desktop running)
docker compose up -d

# Start backend
cd backend; python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Start Celery worker (--pool=solo REQUIRED on Windows)
cd backend; celery -A celery_app.celery worker --loglevel=info --pool=solo

# Start Celery beat
cd backend; celery -A celery_app.celery beat --loglevel=info

# Start frontend
cd frontend; npm run dev

# Run all tests
python -m pytest tests\ -v
```

**Windows-specific notes:**
- Celery Worker MUST use `--pool=solo` (Windows lacks `os.fork()`)
- Log files go to `%TEMP%\astock-*.log` (not `/tmp/`)
- Use `pyenv-win` instead of `pyenv` for Python version management
- PowerShell execution policy may need: `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser`

## Data Source Architecture

The system uses six stock data providers, managed by `StockDataAggregator` with priority-based fallback.

**Configuration:** `DATA_SOURCE_PRIORITY` env var (default: `"akshare,tushare,baostock,eastmoney,sina,tencent"`)

### Provider Capabilities

| Provider | Realtime | Klines (complete) | Klines (incomplete) | Fundamentals | Industry | Search | BJ Support |
|----------|----------|--------------------|---------------------|--------------|----------|--------|------------|
| akshare | wraps Sina (sync, slow) | `ak.stock_zh_a_daily` | — | — | `ak.stock_individual_info_em` | — | Realtime only (via `stock_zh_a_spot_em`); klines not supported for BJ |
| tushare | not supported | `pro.daily` + `pro.daily_basic` (requires token) | — | — | — | — | Klines supported (`{code}.BJ` format) |
| baostock | not supported | `bs.query_history_k_data_plus` (free) | — | — | — | — | Not supported |
| EastMoney (东方财富) | `push2.eastmoney.com` | `push2his.eastmoney.com` | — | — | — | `searchapi.eastmoney.com` | Klines via secid `0.{code}`; realtime may redirect to `push2delay` |
| Sina (新浪) | `hq.sinajs.cn` (GB18030) | — | `money.finance.sina.com.cn` | — | — | — | Not supported (returns price=0) |
| Tencent (腾讯) | `qt.gtimg.cn` (GBK, HTTP) | — | `web.ifzq.gtimg.cn` | `qt.gtimg.cn` (PE/PB/市值) | — | — | Realtime + fundamentals via `bj{code}` prefix; klines not supported |

- **Complete kline sources** (akshare, tushare, baostock, eastmoney): provide `amount` + `turnover_rate`
- **Incomplete kline sources** (sina, tencent): missing `amount` (set to 0) and `turnover_rate` (set to None)

### Fallback Behavior by Data Type

| Data Type | Fallback Chain | Notes |
|-----------|----------------|-------|
| **Realtime quotes** | sina → tencent → eastmoney → akshare (tushare/baostock skipped) | Reordered for latency: fast HTTP scrapers first, heavy akshare last. tushare/baostock don't support realtime. |
| **Daily K-lines** | Phase 1: akshare → tushare → baostock → eastmoney; Phase 2 (if all P1 fail): sina → tencent | Phase 2 only used as last resort (data incomplete) |
| **Fundamentals** | Tencent (direct HTTP, not via aggregator) | Always Tencent; fast, reliable, no fallback needed |
| **Industry** | EastMoney via akshare (best-effort) | Only fetched when field is empty; may be rate-limited |
| **Search** | EastMoney only | Others don't have search APIs |

**BJ (Beijing Exchange) effective chains:**
- **Realtime**: tencent (primary); sina/akshare don't support BJ realtime, eastmoney redirects to delayed endpoint
- **Klines**: eastmoney (primary via secid `0.{code}`); akshare/baostock/sina don't support BJ klines
- **Fundamentals**: tencent (via `bj{code}` prefix)
- **Search**: EastMoney (BJ stocks detected by code pattern: starts with 4/8/92)

### Unified Fetch Pipeline (manual + scheduled share the same 4 steps)

Both manual fetch (`POST /api/stocks/{id}/fetch`) and scheduled fetch (`daily_update_task`) execute identical logic:

| Step | Action | Data Source | Writes To | Skip Condition |
|------|--------|-------------|-----------|----------------|
| 1 | Realtime quote | Aggregator fallback chain | `quote_snapshots` | Never skipped |
| 2 | Daily K-lines (gap-aware) | Aggregator two-phase fallback | `daily_klines` (upsert) | `latest_kline_date >= latest_expected_trading_date()` |
| 3 | Fundamentals (PE/PB/市值) | Tencent direct HTTP | `stock_profiles` (upsert) | Never skipped |
| 4 | Industry | EastMoney via akshare | `stock_profiles.industry` | Already has industry value |

**K-line gap-aware logic** (`latest_expected_trading_date()` in `app/utils.py`):
- Weekday 16:00+ CST → expects today's data (market closed)
- Weekday before 16:00 → expects previous trading day
- Saturday → Friday; Sunday → Friday; Monday pre-16:00 → last Friday
- If DB already has kline up to expected date → **skip entirely** (no network request)
- Otherwise fetch `min(gap_days + 10, 500)` days; first-time stocks fetch `KLINE_INITIAL_LIMIT` (default 2500, ~10 years)

**Implementation:**
- Manual: async in `stocks.py` → `fetch_stock()` (FastAPI event loop)
- Scheduled: sync in `tasks.py` → `_full_fetch_one_stock_sync()` (Celery worker)

### FetchLog Recording

`fetch_logs.source` column (VARCHAR 200) records all data sources used per fetch:
- Format: `行情:{source},K线:{source},基本面:tencent[,行业:eastmoney]`
- Example: `行情:sina,K线:akshare,基本面:tencent`
- `message` column contains human-readable detail:
  `实时行情(sina) | K线11条(akshare) | 基本面(tencent,PE=19.58,PB=7.76) | 行业已有=酿酒行业`

## Key Files

### Docker Deployment
- `docker-compose.prod.yml` — Full production deployment (7 services: postgres, redis, backend, celery-worker, celery-beat, frontend, grafana)
- `docker-compose.yml` — Infrastructure only (development: PostgreSQL + Redis + Grafana)
- `backend/Dockerfile` — Backend image (Python 3.12, build context is project root to include scripts/data)
- `frontend/Dockerfile` — Frontend image (multi-stage: Node 20 build → Nginx serve)
- `frontend/nginx.conf` — Nginx config (static files + /api reverse proxy to backend:8000)
- `.env.example` — Environment variable template for deployment
- `.dockerignore` / `backend/.dockerignore` / `frontend/.dockerignore` — Build exclusion rules
- `DEPLOY.md` — Detailed deployment guide (requirements, data init, operations, troubleshooting)

### Services (multi-source data layer)
- `backend/app/services/base.py` — Abstract `DataSourceClient` interface + `DataSourceError`
- `backend/app/services/aggregator.py` — `StockDataAggregator` with priority-based fallback + `_KLINE_COMPLETE_SOURCES`
- `backend/app/services/akshare_client.py` — akshare client (realtime via `ak.stock_zh_a_spot_em`, klines via `ak.stock_zh_a_daily`)
- `backend/app/services/tushare_client.py` — Tushare client (klines only, requires `TUSHARE_TOKEN`)
- `backend/app/services/baostock_client.py` — Baostock client (klines only, free, no token needed)
- `backend/app/services/eastmoney.py` — EastMoney client (realtime, klines, search)
- `backend/app/services/sina.py` — Sina client (realtime GB18030, klines incomplete)
- `backend/app/services/tencent.py` — Tencent client (realtime GBK, klines incomplete, fundamentals direct)
- `backend/app/services/__init__.py` — Unified exports

### Core
- `backend/app/config.py` — Settings (includes `DATA_SOURCE_PRIORITY`, `DATA_SOURCE_TIMEOUT`)
- `backend/app/utils.py` — Shared utilities (`latest_expected_trading_date()` for K-line gap-aware logic)
- `backend/app/auth.py` — Password hashing (bcrypt), JWT creation/verification, FastAPI dependencies
- `backend/app/models.py` — SQLAlchemy models (User, Stock, DailyKline, QuoteSnapshot, FetchLog, StockProfile, AppSetting, AnalysisHistory, TradeStrategy, TradeStep, TradeCondition, TradeExecution, QuantFactorDaily, QuantICHistory, QuantPortfolio, QuantBacktestResult, QuantIteration)
- `backend/app/schemas.py` — Pydantic schemas (StockProfileOut, StockEnrichedOut, StockListResponse, etc.)
- `backend/app/routers/auth.py` — Authentication API router (login, user CRUD, permissions)
- `backend/app/routers/stocks.py` — Stock API routes (uses aggregator; `/enriched` paginated endpoint with profile data)
- `backend/app/routers/quotes.py` — Quote/kline/profile/logs routes
- `backend/app/routers/screener.py` — Smart screener routes (presets, custom screen, industries)
- `backend/app/routers/schedule.py` — Schedule settings API (GET/PUT settings, status, manual trigger)
- `backend/app/routers/newssentiment.py` — News/announcements + retail sentiment API routes
- `backend/app/routers/ai.py` — AI analysis API router (async analyze, history, reports)
- `backend/app/routers/config.py` — Configuration management API router
- `backend/app/routers/trade.py` — Strategy trading API router (strategies CRUD, broker, executions)
- `backend/app/routers/quant.py` — Quantitative analysis API router (async analyze, history CRUD, factors, presets)
- `backend/app/services/news_service.py` — EastMoney news search + announcements fetcher
- `backend/app/services/sentiment_service.py` — EastMoney Guba scraper with keyword-based sentiment scoring
- `backend/app/services/screener.py` — Screening engine (fundamental + technical + volume pattern detection)
- `backend/app/tasks.py` — Celery tasks (daily_update_task with unified full fetch, check_scheduled_update, fetch_stock_task, fetch_all_stocks_task, trade_monitor_tick, check_quant_iterations)

### Tests
- `tests/test_eastmoney_api.py` — 17 tests: EastMoney API connectivity
- `tests/test_sina_api.py` — 15 tests: Sina raw API + SinaClient unified format
- `tests/test_tencent_api.py` — 16 tests: Tencent raw API + TencentClient unified format
- `tests/test_aggregator.py` — 14 tests: Fallback logic, kline restrictions, cross-source consistency
- `tests/test_database.py` — 15 tests: PostgreSQL CRUD
- `tests/test_backend_api.py` — 9 tests: FastAPI endpoints
- `tests/test_grafana.py` — 9 tests: Grafana health/datasources/dashboards
- `tests/test_fetch_pipeline.py` — 5 tests: End-to-end fetch pipeline

### Documentation
- `docs/eastmoney_fields.md` — EastMoney API field reference
- `docs/data_sources.md` — Multi-source field mappings and unified format spec

## Database

- PostgreSQL connection: `postgresql+asyncpg://astock:astock123@localhost:5432/astock`
- Tables (17): `users`, `stocks`, `daily_klines`, `quote_snapshots`, `fetch_logs`, `stock_profiles`, `app_settings`, `analysis_history`, `trade_strategies`, `trade_steps`, `trade_conditions`, `trade_executions`, `quant_factor_daily`, `quant_ic_history`, `quant_portfolios`, `quant_backtest_results`, `quant_iterations`
- `fetch_logs.source` column (VARCHAR 200, nullable): records all data sources used per fetch (format: `行情:X,K线:Y,基本面:tencent`)
- `stock_profiles` table: industry, PE, PB, market cap (unique per stock_id)
- `app_settings` table: key-value store for schedule configuration and runtime state

## Data Scripts

- `scripts/download_all_data.py` — Full A-share data downloader (SH + SZ + BJ)
  - Fetches ALL A-share stocks (~5,700) from SSE (akshare), SZSE (Tencent discovery), BSE (akshare)
  - `--klines-only`: 10-year daily K-lines from akshare/Sina
  - `--fundamentals-only`: PE/PB/market cap from Tencent (fast, batch 40 per request)
  - `--industry-only`: Industry data from EastMoney (rate-limited, may fail)
  - `--skip-klines`: Skip K-line download
  - `--force`: Force re-download
  - Supports Beijing exchange (BJ) stocks with `92xxxx` codes
  - Resume support: skips stocks with >= 2000 existing kline rows
- `scripts/download_index_klines.py` — Original kline download script (deprecated, use download_all_data.py)

## Data Coverage (as of 2026-03-26)

| Metric | Value |
|--------|-------|
| Total stocks | 5,684 (SH: 2,309, SZ: 3,075, BJ: 300) |
| Stocks with K-lines | 5,521 (97.1%) |
| K-line date range | 1991-01-02 to 2026-03-26 |
| Total K-line rows | ~10.2M |
| Stocks with PE/PB | 5,684 (100%) |
| Stocks with industry | 5,554 (97.7%) |

## Data Files

- `data/all_a_shares.csv` — Full A-share stock list with names (5,682 stocks)
- `data/all_constituents.csv` — All stock codes (backward compatible)
- `data/csi300_constituents.csv` — CSI 300 constituent list
- `data/csi500_constituents.csv` — CSI 500 constituent list
- `data/stock_industries.csv` — Stock-to-industry mapping (4,786 stocks)
- `data/stock_profiles_snapshot.csv` — Full profiles snapshot (5,683 stocks)

## Screener Preset Strategies

- `value_largecap`: 大盘价值股 (market cap > 500亿, PE < 15, PB < 2)
- `growth_midcap`: 中盘成长股 (100-1000亿, PE 10-40)
- `low_position`: 底部区域股 (price percentile < 30% in 250 days)
- `volume_breakout`: 放量突破 (5-day volume > 2x 60-day average)
- `quiet_accumulation`: 缩量蓄势 (volume < 0.5x historical)
- `baijiu_sector`: 白酒板块 (酿酒行业)
- `bank_sector`: 银行金融 (银行+金融行业+非银金融+多元金融+券商信托, PB < 1.5)
- `tech_sector`: 科技硬件 (半导体/电子元件/通信设备/光学光电子/消费电子/电子信息/电子器件/电子设备/计算机设备/电子化学品/安防设备)
- `new_energy_sector`: 新能源 (电力设备/电网设备/输配电气/电源设备/电气设备/电机/汽车零部件/电池/光伏设备/风电设备/能源金属)
- `pharma_sector`: 医药生物 (中药/化学制药/生物制品/医疗器械/医疗服务/医药商业/医药制造/生物制药/医药生物/医疗行业)

## Volume Pattern Detection

Custom screening with "放量上涨→缩量回调" pattern detection. No preset strategy — users configure
all parameters via the custom screen form.

### Key parameters (`vp_*` prefix on `ScreenerCriteria`):
- `vp_lookback_months` (default 4): How many months of data to scan
- `vp_min_surge_weeks` (default 3): Minimum consecutive weeks with volume > base
- `vp_surge_vol_ratio` (default 1.4): Weekly volume must exceed this multiple of 4-week base average
- `vp_min_surge_gain` / `vp_max_surge_gain` (default 10-30%): Cumulative price gain range during surge
- `vp_min_pullback_pct` (default 10%): Min % decline from peak
- `vp_min_pullback_weeks` (default 2): Min weeks elapsed after surge
- `vp_recent_vol_days` (default 5): Recent period for daily volume averaging
- `vp_recent_5d_vs_minweek_max` (default 1.1): Max ratio of sum-of-last-5-days total volume / min weekly volume in lookback (hard filter)
- `vp_latest_vs_minday_max` (default 1.1): Max ratio of latest 1-day vol / min daily vol in lookback; supports <1 (hard filter)
- `vp_min_score` (default 30): Minimum composite score (0-100)

### Reference fields returned per result:
- `base_weekly_vol`: 4-week average weekly volume before surge
- `max_surge_vol_ratio`: Peak weekly volume / base during surge
- `pullback_min_weekly_vol`: Lowest weekly volume during pullback phase
- `min_daily_vol`: Minimum daily volume in the lookback window
- `latest_daily_vol`: Most recent day's volume
- `recent_5d_total_vol`: Sum of last 5 trading days' volume
- `recent_5d_vs_minweek`: recent_5d_total_vol / min weekly volume ratio
- `latest_vs_minday`: Latest day volume / min daily volume ratio

## Stock API (prefix: `/api/stocks`)

- `GET /stocks/enriched` — Paginated stock list with profile data (industry, PE, PB, market cap, latest K-line date)
  - Query params: `favorites_only`, `keyword`, `page`, `page_size`
  - Response: `{ total: int, items: StockEnrichedOut[] }`
- `GET /stocks/` — Stock list with optional search (keyword, limit query params for remote search mode)
- `GET /stocks/search` — Search stocks by keyword (code or name) via EastMoney search API
- `POST /stocks/` — Add a stock to watchlist (validates via search first)
- `GET /stocks/{stock_id}` — Get stock by ID
- `PUT /stocks/{stock_id}` — Update stock (name, market, active status)
- `DELETE /stocks/{stock_id}` — Soft-delete stock (sets is_active=False)
- `POST /stocks/{stock_id}/fetch` — Manual full fetch (4-step pipeline: realtime, klines, fundamentals, industry)
- `POST /stocks/{stock_id}/fetch-lite` — Lightweight async-only fetch (eastmoney/sina/tencent only, no fundamentals/industry)
- `POST /stocks/fetch-all` — Trigger data fetch for all active stocks in watchlist

## News & Sentiment (相关资讯 + 散户情绪)

### Data Sources
- **EastMoney Announcements** (`np-anotice-stock.eastmoney.com`): Official company filings and announcements
- **EastMoney News Search** (`search-api-web.eastmoney.com`): Financial news articles (JSONP format)
- **EastMoney Guba** (`guba.eastmoney.com`): Retail investor forum posts (HTML scraping)

> Xueqiu and 同花顺 APIs require authentication/cookies and are not yet supported. The system is designed to be extensible for these sources.

### API Endpoints
- `GET /api/newssentiment/news/{stock_id}` — Combined news + announcements for a stock
  - Query params: `page` (default 1), `page_size` (default 50)
  - Returns: `[{title, date, source, url, type, category, summary}]`
  - `type` is either `"news"` or `"announcement"`
- `GET /api/newssentiment/sentiment/{stock_id}` — Daily retail sentiment statistics
  - Query params: `days` (default 30, max 90)
  - Returns: `[{date, comment_count, total_posts, sentiment_score, avg_read_count, avg_reply_count, source}]`
  - `comment_count`: unique users (deduplicated by user ID from Guba)
  - `sentiment_score`: 0-100 (50=neutral, >50=bullish, <50=bearish), keyword-based

### Sentiment Scoring
- Keyword-based analysis of Guba post titles
- Bullish keywords: 看多, 看涨, 利好, 加仓, 抄底, 突破, etc.
- Bearish keywords: 看空, 看跌, 利空, 减仓, 割肉, 暴跌, etc.
- Score formula: `(bull_count - bear_count) / total * 50 + 50` → maps to 0-100 scale
- Intended as future weight factor for strategy decisions

### Key Files
- `backend/app/services/news_service.py` — News fetching (EastMoney announcements + search)
- `backend/app/services/sentiment_service.py` — Guba scraping + sentiment scoring
- `backend/app/routers/newssentiment.py` — API router
- `frontend/src/App.vue` — "相关资讯" tab (timeline) + "散户情绪" tab (ECharts trend chart)
- `frontend/src/api/index.js` — `getStockNews()`, `getStockSentiment()`

## Daily Scheduled Update System

### Architecture
- Celery Beat runs `check_scheduled_update` every 10 minutes on weekdays (Mon-Fri)
- Celery Beat runs `fetch_all_stocks_task("realtime")` every 30 minutes during trading hours (CST 9:30-15:00, weekdays)
- Checks `app_settings` DB table for configured time and whether it's time to run
- Dispatches `daily_update_task` which runs unified full fetch per stock

### API Endpoints
- `GET /api/schedule/settings` — Current schedule configuration (hour, minute, timezone, enabled)
- `PUT /api/schedule/settings` — Update schedule settings
- `GET /api/schedule/status` — Runtime status (last run time/status/message, data statistics)
- `POST /api/schedule/trigger` — Manually trigger daily update task

### app_settings Keys
- `daily_update_hour` (default: 16) — Hour to run (0-23)
- `daily_update_minute` (default: 0) — Minute to run (0-59)
- `daily_update_timezone` (default: Asia/Shanghai) — Timezone
- `daily_update_enabled` (default: true) — Whether scheduled updates are active
- `daily_update_last_run` — ISO timestamp of last run
- `daily_update_last_status` — "success" or "error"
- `daily_update_last_message` — Human-readable result summary

### daily_update_task Behavior

Uses unified `_full_fetch_one_stock_sync()` per stock (same logic as manual fetch):
1. Finds all active stocks and their latest K-line date in DB
2. For each stock, runs the 4-step unified fetch pipeline:
   - Realtime quote via aggregator (fallback: akshare → eastmoney → sina → tencent) → `quote_snapshots`
   - Gap-aware K-line fill via aggregator (Phase 1: akshare → tushare → baostock → eastmoney; Phase 2: sina → tencent) → `daily_klines`
     - Skips if `latest_kline_date >= latest_expected_trading_date()` (see `app/utils.py`)
   - Fundamentals (PE/PB/市值) from Tencent direct HTTP → `stock_profiles`
   - Industry from EastMoney via akshare (best effort, skips if already present) → `stock_profiles.industry`
3. Writes individual FetchLog per stock (`fetch_type="scheduled_full_fetch"`, `source` records all sources)
4. Writes summary FetchLog (`fetch_type="daily_update_summary"`)
5. Records status in `app_settings` table

### Fetch Type Reference

| fetch_type | Source | Description |
|------------|--------|-------------|
| `manual_full_fetch` | UI "抓取" button | Manual single-stock full fetch (async endpoint) |
| `scheduled_full_fetch` | Celery daily_update_task | Scheduled per-stock full fetch (sync) |
| `daily_update_summary` | Celery daily_update_task | Overall run summary |
| `batch_lite_fetch` | UI "全部抓取" button | Lightweight fetch (async-only sources + fundamentals + industry) |
| `all` | POST /stocks/fetch-all | Full 4-step pipeline for all active stocks (gap-aware klines + fundamentals + industry) |

### Running the Scheduler
```bash
# Start Celery worker (processes tasks)
cd backend && celery -A celery_app.celery worker --loglevel=info

# Start Celery beat (dispatches scheduled tasks)
cd backend && celery -A celery_app.celery beat --loglevel=info
```

## Performance Notes
- Stock list uses remote search mode (fetches on-demand, max 50 results per search)
- Initial page load fetches only favorites + first page of enriched stocks (~14KB vs ~814KB before)
- Quote stock selector uses remote search instead of rendering all 5,684 options
- Browser back button navigation supported via pushState/popstate with hash-based routing

## UI Color Convention (A股惯例: 红涨绿跌)

The entire frontend follows A-share market convention:
- **红色 (#f56c6c / #ef5350)** = 涨/盈利/看多 (up, gain, bullish)
- **绿色 (#67c23a / #26a65b)** = 跌/亏损/看空 (down, loss, bearish)
- **橙色 (#e6a23c)** = 中性/警告 (neutral, warning)

This applies to: candlestick charts, volume bars, realtime quotes, returns, backtest metrics, monthly returns heatmap, quant scores, IC values, sentiment scores.

**Exception**: System status indicators (启用/禁用, 成功/失败, 条件已满足 etc.) use standard convention (green=OK, red=error).

## Configuration Management

Web-based configuration management page (sidebar: "配置管理") for editing system settings at runtime.

### Architecture
- Settings persisted to `backend/.env` file via `PUT /api/config/settings`
- In-memory `settings` object updated immediately (no restart needed for LLM/data source changes)
- Service port changes saved to .env but require manual service restart
- API keys are masked in GET responses (first 4 + last 4 chars shown)

### API Endpoints
- `GET /api/config/settings` — All config grouped by category (llm, data_source, service_ports)
- `PUT /api/config/settings` — Partial update, writes .env + updates runtime settings
- `POST /api/config/test-llm` — Send a test prompt to verify LLM connectivity

### Settings Categories

**LLM (AI Analysis):**
- `LITELLM_MODEL` — Model identifier (e.g. `openai/llama-3-3-70b-instruct`)
- `OPENAI_API_KEY` — API key (masked in UI)
- `OPENAI_BASE_URL` — Custom endpoint URL for OpenAI-compatible providers
- `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `LLM_REQUEST_TIMEOUT`, `LLM_SSL_VERIFY`

**Data Sources:**
- `DATA_SOURCE_PRIORITY` — Comma-separated provider list (default: `akshare,tushare,baostock,eastmoney,sina,tencent`)
- `DATA_SOURCE_TIMEOUT` — Request timeout in seconds
- `TUSHARE_TOKEN` — Optional Tushare API token (required for tushare klines)

**Service Ports (reference, requires restart):**
- Backend (8000), Frontend (5174), PostgreSQL (5432), Redis (6379), Grafana (3000)

### Key Files
- `backend/app/routers/config.py` — Config API router
- `frontend/src/App.vue` — "配置管理" page UI (Section 6)
- `frontend/src/api/index.js` — `getConfigSettings()`, `updateConfigSettings()`, `testLLM()`

## AI Analysis Integration (Built-in)

AStock includes built-in AI-powered stock analysis using LLM (ported from the daily_stock_analysis project).

### Architecture
- **No external service dependency** — All analysis runs within AStock's backend process
- Technical indicators (MA, MACD, RSI, support/resistance) computed from AStock's own kline data
- LLM calls via `litellm` library (supports Gemini, OpenAI, Anthropic, DeepSeek, etc.)
- In-memory async task management for analysis jobs (no Celery needed)
- Results persisted to `analysis_history` PostgreSQL table
- Frontend displays results in "AI智能诊断" tab under the quotes section

### Configuration (in `backend/.env`)
- `LITELLM_MODEL` — LLM model identifier (e.g., `gemini/gemini-2.5-flash`, `openai/gpt-4o-mini`)
- `LLM_TEMPERATURE` (default: `0.7`) — LLM temperature
- `LLM_MAX_TOKENS` (default: `8192`) — Max output tokens
- `LLM_REQUEST_TIMEOUT` (default: `120`) — Timeout in seconds for LLM API calls
- `LLM_SSL_VERIFY` (default: `true`) — Set `false` for corporate/self-signed SSL certs
- API keys (set the one matching your model):
  - `GEMINI_API_KEY`
  - `OPENAI_API_KEY` (also used for Chinese providers — automatically mirrored to DEEPSEEK_API_KEY, DASHSCOPE_API_KEY, MOONSHOT_API_KEY, VOLCENGINE_API_KEY)
  - `ANTHROPIC_API_KEY`
- OpenAI-compatible endpoint:
  - `OPENAI_BASE_URL` — Custom API base URL (for self-hosted or corporate LLM services)

### Supported Chinese LLM Providers

litellm v1.82+ natively supports these Chinese providers (no `OPENAI_BASE_URL` needed):

| Provider | Native Prefix | Example Model | API Key Source |
|----------|---------------|---------------|----------------|
| DeepSeek 深度求索 | `deepseek/` | `deepseek/deepseek-chat` | platform.deepseek.com |
| 通义千问 Qwen | `dashscope/` | `dashscope/qwen-plus` | dashscope.console.aliyun.com |
| 月之暗面 Moonshot/Kimi | `moonshot/` | `moonshot/moonshot-v1-8k` | platform.moonshot.cn |
| 豆包 Doubao (火山引擎) | `volcengine/` | `volcengine/doubao-seed-2-0-pro-260215` | volcengine.com |

These providers use `openai/` prefix + `OPENAI_BASE_URL`:

| Provider | Model Example | OPENAI_BASE_URL |
|----------|---------------|-----------------|
| 智谱 GLM | `openai/glm-4-flash` | `https://open.bigmodel.cn/api/paas/v4` |
| 硅基流动 SiliconFlow | `openai/Qwen/Qwen3-8B` | `https://api.siliconflow.cn/v1` |
| 零一万物 Yi | `openai/yi-large` | `https://api.lingyiwanwu.com/v1` |
| 阶跃星辰 Stepfun | `openai/step-2-16k` | `https://api.stepfun.com/v1` |

Frontend "配置管理" page includes a **服务商预设** dropdown for one-click provider setup.

### API Endpoints
- `GET /api/ai/health` — Check if LLM is configured
- `POST /api/ai/analyze` — Trigger AI analysis for a stock (async mode with polling)
- `GET /api/ai/status/{task_id}` — Poll analysis task status
- `GET /api/ai/history/{stock_code}` — Get recent analysis history for a stock
- `GET /api/ai/report/{record_id}` — Get a specific analysis report

### Analysis Pipeline
1. Load 60+ days of kline data from `daily_klines` table
2. Compute technical indicators: MA5/10/20/60, MACD, RSI, bias rates, support/resistance
3. Fetch realtime quote via aggregator (optional, falls back to latest kline)
4. Build structured markdown prompt with stock info + technicals
5. Call LLM with trading policy system prompt -> get JSON "decision dashboard"
6. Parse/validate response, save to `analysis_history` table

### Report Structure
The analysis report includes:
- **Meta**: stock code/name, price, change%, model used, timestamp
- **Summary**: analysis conclusions, operation advice, trend prediction, sentiment score (0-100)
- **Strategy**: ideal buy, secondary buy, stop loss, take profit price levels
- **Details**: raw LLM result, context snapshot

### Key Files
- `backend/app/services/ai_analysis.py` — Core analysis service (trend analyzer, LLM integration, task management)
- `backend/app/services/ai_schemas.py` — Pydantic schemas for LLM output validation
- `backend/app/routers/ai.py` — AI API router (direct implementation)
- `backend/app/models.py` — `AnalysisHistory` model
- `backend/app/config.py` — LLM settings (LITELLM_MODEL, API keys)
- `frontend/src/App.vue` — "AI智能诊断" tab UI (in quotes section)
- `frontend/src/api/index.js` — AI API client methods (aiAnalyze, aiStatus, aiHistory, aiReport)

### Running
```bash
# 1a. Configure LLM in .env (example with Gemini)
cat >> backend/.env << 'EOF'
LITELLM_MODEL=gemini/gemini-2.5-flash
GEMINI_API_KEY=your-api-key
EOF

# 1b. Or use an OpenAI-compatible endpoint (e.g. corporate LLM, vLLM, Ollama)
cat >> backend/.env << 'EOF'
LITELLM_MODEL=openai/llama-3-3-70b-instruct
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-llm-service.com/v1/
LLM_SSL_VERIFY=false
EOF

# 2. Start AStock backend
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3. Start AStock frontend
cd frontend && npm run dev
```

## Strategy Trading Module

Self-contained conditional order engine with simulated/live mode switching.

### Architecture
- **Condition Engine** (`trade_engine.py`): evaluates conditions against real-time quotes, advances steps sequentially
- **Simulated Broker**: built-in paper trading with cash/holdings tracking
- **Live Broker** (`brokers/pingan.py`): 平安证券 QMT/xtquant integration for real trading
- **Broker Adapter** (`brokers/base.py`): abstract interface for broker implementations
- **Celery Monitor**: `trade_monitor_tick` task runs every 5 seconds, fetches quotes for active strategies

### Core Concepts
- **Strategy**: top-level entity targeting one stock, containing sequential steps
- **Step**: one action (buy/sell) with trigger conditions, executed in order (step 1 completes -> step 2 activates)
- **Condition**: a comparison (field operator value), combined with AND/OR logic within a step
- **Condition fields**: `price`, `open_price`, `change_pct`, `rise_pct`, `fall_pct`, `profit_pct`, `loss_pct`, `volume`, `turnover_rate`
- **Step status flow**: `waiting` -> `watching` -> `triggered` -> `filled` (or `failed`)
- **Strategy status flow**: `draft` -> `active` -> `completed` (or `paused`/`cancelled`/`error`)

### Condition Field Reference

| Field | Label | Description |
|-------|-------|-------------|
| `price` | 当前价格 | Current market price |
| `open_price` | 开盘价格 | Today's opening price |
| `change_pct` | 涨跌幅% | Overall price change percentage |
| `rise_pct` | 涨幅% (较昨收) | Rise % from prev close (0 when falling) |
| `fall_pct` | 跌幅% (较昨收) | Fall % from prev close (0 when rising) |
| `profit_pct` | 浮盈比例% | Unrealized profit % based on avg cost |
| `loss_pct` | 浮亏比例% | Unrealized loss % based on avg cost |
| `volume` | 成交量 | Trading volume |
| `turnover_rate` | 换手率% | Turnover rate |

### Live Trading (平安证券 QMT)

**Prerequisites:**
1. Install 平安证券 QMT client (download from their website)
2. `pip install xtquant` (or copy from QMT installation)
3. Start QMT Mini client and log in
4. Configure in AStock: 配置管理 -> 实盘交易配置

**Configuration** (in `backend/.env`):
```
BROKER_ACCOUNT=your_account_number
BROKER_PASSWORD=your_password
BROKER_QMT_PATH=C:/平安证券/QMT/bin.x64
```

**How it works:**
- When strategy mode is "live", orders are submitted to the real broker via xtquant
- The trade engine connects to QMT Mini, submits orders, and tracks fills via callbacks
- Account info (cash, positions) is queried from the broker in real-time
- If broker is not configured/connected, live orders fail gracefully with error message

### Database Tables
- `trade_strategies` — strategy definition with simulation state (cash, holdings, avg_cost)
- `trade_steps` — sequential steps with conditions and action
- `trade_conditions` — individual conditions (field, operator, value)
- `trade_executions` — event log (strategy_started, condition_met, order_filled, etc.)

### API Endpoints
- `GET /api/trade/strategies` — List all strategies (filter by status, stock_code)
- `POST /api/trade/strategies` — Create strategy with steps and conditions
- `GET /api/trade/strategies/{id}` — Get strategy detail
- `PUT /api/trade/strategies/{id}` — Update strategy (only when not active)
- `DELETE /api/trade/strategies/{id}` — Delete strategy
- `POST /api/trade/strategies/{id}/activate` — Start monitoring conditions
- `POST /api/trade/strategies/{id}/pause` — Pause monitoring
- `POST /api/trade/strategies/{id}/cancel` — Cancel strategy
- `POST /api/trade/strategies/{id}/reset` — Reset to draft, clear all fills and logs
- `GET /api/trade/strategies/{id}/executions` — Execution event log
- `POST /api/trade/strategies/{id}/test-tick` — Manual quote injection for testing
- `POST /api/trade/strategies/{id}/auto-tick` — Fetch real-time quote and run trade engine (no Celery needed)
- `GET /api/trade/strategies/{id}/intraday` — Intraday minute klines via Sina (scale=5/15/30/60, default 5)
- `GET /api/trade/summary` — Overview statistics
- `GET /api/trade/broker/status` — Broker connection status and config check
- `POST /api/trade/broker/connect` — Connect to broker
- `POST /api/trade/broker/disconnect` — Disconnect from broker
- `GET /api/trade/broker/account` — Live account info and positions

### Key Files
- `backend/app/models.py` — TradeStrategy, TradeStep, TradeCondition, TradeExecution models
- `backend/app/services/trade_engine.py` — Core condition evaluation engine
- `backend/app/services/brokers/__init__.py` — Broker package exports
- `backend/app/services/brokers/base.py` — Abstract broker interface (BaseBroker, OrderResult, etc.)
- `backend/app/services/brokers/pingan.py` — 平安证券 QMT adapter via xtquant
- `backend/app/routers/trade.py` — Trading API router with inline schemas
- `backend/app/tasks.py` — `trade_monitor_tick` Celery task
- `backend/celery_app.py` — Beat schedule for trade monitor (every 5s)
- `frontend/src/App.vue` — "策略交易" page (Section 7)
- `frontend/src/api/index.js` — Trading API client methods

### Example: Buy-then-Sell Strategy
```json
{
  "name": "低吸高抛",
  "stock_code": "000001",
  "stock_name": "平安银行",
  "market": "SZ",
  "mode": "simulated",
  "sim_initial_cash": 100000,
  "steps": [
    {
      "name": "低位买入",
      "condition_logic": "AND",
      "action_type": "buy",
      "quantity": 1000,
      "price_type": "limit",
      "limit_price": 10.50,
      "conditions": [
        {"field": "price", "operator": "<=", "value": 10.50}
      ]
    },
    {
      "name": "止盈卖出",
      "condition_logic": "OR",
      "action_type": "sell",
      "quantity": 1000,
      "price_type": "market",
      "conditions": [
        {"field": "profit_pct", "operator": ">=", "value": 5.0},
        {"field": "loss_pct", "operator": ">=", "value": 3.0}
      ]
    }
  ]
}
```

### Example: Opening Price + Rise/Fall Condition Strategy
```json
{
  "name": "开盘低开买入",
  "stock_code": "600519",
  "stock_name": "贵州茅台",
  "market": "SH",
  "mode": "live",
  "steps": [
    {
      "name": "开盘跌幅超2%时买入",
      "condition_logic": "AND",
      "action_type": "buy",
      "quantity": 100,
      "price_type": "limit",
      "limit_price": 1500,
      "conditions": [
        {"field": "fall_pct", "operator": ">=", "value": 2.0},
        {"field": "open_price", "operator": "<=", "value": 1500}
      ]
    },
    {
      "name": "涨幅达3%止盈",
      "condition_logic": "OR",
      "action_type": "sell",
      "quantity": 100,
      "price_type": "market",
      "conditions": [
        {"field": "rise_pct", "operator": ">=", "value": 3.0},
        {"field": "loss_pct", "operator": ">=", "value": 2.0}
      ]
    }
  ]
}
```

## Quantitative Analysis Module (Cross-Sectional)

Cross-sectional multi-factor quantitative stock selection engine inspired by Qlib and Alpha101. Supports universe pre-filtering with 12 preset strategies, 50 factors across 8 categories, and outputs a ranked Top-N portfolio with biweekly rebalancing, A-share noise filtering, and realistic backtesting.

### Architecture
- **Cross-Sectional Approach**: Ranks stocks against each other per date (not single-stock time-series)
- **Pre-Filtering**: DB-level filtering by industry, market cap, PE, PB, market — reduces universe from ~5,000 to 300-1,500 stocks for faster computation
- **Factor Engine** (`quant_engine.py`): 50 factors across 8 categories (value, momentum, reversal, volume, volatility, technical, moneyflow, stat)
- **Universe Filtering**: ST exclusion, IPO < 60 days, limit-up/down, liquidity < 1000万/day, BJ exchange exclusion
- **IC Analysis**: Spearman Rank IC per cross-section, IC_IR validation, directional consistency
- **IC-Decay Weighted Combination**: Exponential-decay IC-weighted factor combination with signed weights
- **ML Enhancement**: LightGBM (preferred) or sklearn GBM classifier for forward return prediction
- **Composite Signal**: IC-weighted score + ML prediction blend (0-100 score)
- **Portfolio Construction**: Buffer zone (entry ≤ 25, exit > 40) with turnover control
- **Walk-forward Backtest**: T+1 execution delay, realistic A-share transaction costs (buy 7.6bps, sell 13.2bps)

### Factor Validity Logic
A factor is valid when ALL THREE conditions are met:
- `|IC_mean| >= 0.03` (meaningful predictive signal; academic standard: >=0.03 meaningful, >=0.05 strong)
- `|IC_IR| >= 0.3` (sufficient predictive power)
- `max(ic_pos_rate, 1 - ic_pos_rate) >= 0.55` (directional consistency)

### Factor Categories (50 factors total)

| Category | Label | Count | Description |
|----------|-------|-------|-------------|
| value | 价值 | 3 | EP, BP, negative log market cap |
| momentum | 动量 | 6 | 5/10/20/60/120-day returns, volume-weighted return |
| reversal | 反转 | 4 | 5/20-day reversal, overnight/intraday returns |
| volume | 量能 | 7 | Vol ratios (5/20, 5/60), Amihud illiquidity, turnover, vol-price corr |
| volatility | 波动 | 6 | 20/60-day realized vol, idiosyncratic vol, max drawdown, downside vol |
| technical | 技术 | 10 | RSI(14/6), Bollinger, price position, MACD, bias, MA cross |
| moneyflow | 资金流 | 7 | VWAP bias, shadow ratios, up-volume pct, net buy, smart money flow (Alpha101-style) |
| stat | 统计 | 7 | Skew, kurtosis, autocorrelation, high-low correlation, ret-vol rank correlation |

### Preset Universe Strategies (12 presets)

| Preset | Label | Filter Rules |
|--------|-------|-------------|
| all | 全部A股 | No filter (ST/BJ excluded by universe filter) |
| csi300_like | 沪深300风格 | Market cap > 500亿, PE > 0 |
| csi500_like | 中证500风格 | Market cap 100-800亿, PE > 0, PB > 0 |
| small_cap | 小盘股 | Market cap < 100亿 |
| value | 价值股 | PE < 20, PB < 2, market cap > 50亿 |
| growth | 成长股 | PE 15-80, market cap > 100亿 |
| high_dividend | 高股息 | PE < 15, PB < 3, market cap > 200亿 |
| baijiu | 白酒板块 | Industry = 酿酒行业 |
| bank | 银行金融 | Industries: 银行, 金融行业, 非银金融, 多元金融, 券商信托, 金融; PB < 1.5 |
| tech_hardware | 科技硬件 | Industries: 半导体, 电子元件, 通信设备, 光学光电子, 消费电子, 电子信息, 电子器件, 电子设备, 计算机设备, 电子化学品, 安防设备 |
| new_energy | 新能源 | Industries: 电力设备, 电网设备, 输配电气, 电源设备, 电气设备, 电机, 汽车零部件, 电池, 光伏设备, 风电设备, 能源金属 |
| pharma | 医药生物 | Industries: 中药, 化学制药, 生物制品, 医疗器械, 医疗服务, 医药商业, 医药制造, 生物制药, 医药生物, 医疗行业 |

### Universe Filtering Rules (after pre-filter)
1. **ST/PT stocks**: Excluded (name contains ST or PT)
2. **IPO < 60 days**: Excluded (new listings are speculative)
3. **Limit-up/down**: Excluded on days with 9.8%+ price change (illiquid)
4. **Low liquidity**: Excluded if average daily turnover < 1000万元
5. **BJ exchange**: Excluded (low liquidity)

### Portfolio Construction
- **Buffer zone mechanism**: New entries must rank ≤ 25 (top_n - 5), existing holdings exit only if rank > 40 (top_n + 10)
- **Default top_n**: 30 stocks
- **Default rebalance frequency**: 10 trading days (biweekly)
- **Industry neutralization**: Optional, configurable via UI toggle

### Backtest Parameters
- **Forward return horizon**: 10 trading days
- **T+1 execution delay**: Orders placed on signal day, executed next day's open
- **Transaction costs**: Buy 7.6bps (commission + stamp tax), Sell 13.2bps
- **Initial capital**: 1,000,000 (1M CNY)
- **Equal weight**: Each stock gets 1/top_n allocation

### API Endpoints
- `POST /api/quant/analyze` — Start async cross-sectional analysis. Returns `{task_id, status, stock_count, message}` immediately. Results auto-saved to DB.
  - Query params: `top_n` (default 30), `rebalance_freq` (default 10), `industry_neutral` (bool), `preset`, `industries` (comma-sep), `market_cap_min`, `market_cap_max`, `pe_min`, `pe_max`, `pb_min`, `pb_max`, `markets` (comma-sep), `backtest_start` (YYYY-MM-DD), `backtest_end` (YYYY-MM-DD). Timeout 600s.
- `GET /api/quant/status/{task_id}` — Poll analysis task status. Returns `{task_id, status, message, stock_count, result?, run_id?}`. Status: pending/running/completed/error.
- `GET /api/quant/history` — List saved analysis results (newest first). Params: `limit`, `offset`. Returns `{total, items}`.
- `GET /api/quant/result/{run_id}` — Get full saved analysis result (portfolio, backtest, factors).
- `PUT /api/quant/result/{run_id}` — Update name/notes of a saved result. Body: `{name?, notes?}`.
- `DELETE /api/quant/result/{run_id}` — Delete a saved analysis result.
- `GET /api/quant/factors` — List all 50 factor definitions with categories
- `GET /api/quant/stock-profile/{stock_id}` — Single stock factor profile (raw values)
- `GET /api/quant/factor-status` — Pre-computed factor data status
- `GET /api/quant/industries` — List available industries with stock counts
- `GET /api/quant/presets` — List all 12 preset universe strategies

### Async Analysis Flow
1. Frontend calls `POST /analyze` with filter params → gets `task_id`
2. Frontend polls `GET /status/{task_id}` every 2 seconds
3. Backend runs analysis in background thread pool (CPU-bound), saves to `QuantBacktestResult` DB on completion
4. Poll returns `status: "completed"` with full result + `run_id`
5. All results auto-saved with auto-generated name from filters

### Response Structure (POST /api/quant/analyze)
```json
{
  "task_id": "uuid-string",
  "status": "pending",
  "stock_count": 350,
  "message": "分析任务已提交，共 350 只股票"
}
```

### Response Structure (GET /api/quant/status/{task_id} — completed)
```json
{
  "task_id": "uuid-string",
  "status": "completed",
  "message": "分析完成，耗时 45s",
  "stock_count": 350,
  "run_id": "uuid-string",
  "result": { ... full analysis result ... }
}
```

### Full Analysis Result Structure (from status or GET /result/{run_id})
```json
{
  "success": true,
  "run_id": "uuid-string",
  "name": "沪深300风格 Top30",
  "analysis_date": "2026-03-20",
  "elapsed_seconds": 45.2,
  "filters_applied": {
    "preset": "csi300_like",
    "industries": null,
    "market_cap_min": 50000000000,
    "market_cap_max": null
  },
  "universe": {
    "total_stocks": 350,
    "in_universe": 280,
    "excluded": 70,
    "exclude_breakdown": {"ST": 5, "IPO<60天": 3, "涨跌停": 10, "流动性不足": 50}
  },
  "signal": { "valid_factors": 32, "total_factors": 50 },
  "portfolio": {
    "size": 30,
    "config": {"top_n": 30, "rebalance_freq": 10, "industry_neutral": false},
    "stocks": [{"rank": 1, "stock_code": "600519", "stock_name": "贵州茅台", "industry": "酿酒行业", "score": 82.5, "pe_ttm": 25.3, "pb": 8.1, "total_market_cap": 2.1e12}]
  },
  "backtest": {
    "success": true, "total_return": 45.2, "annual_return": 18.5, "sharpe_ratio": 1.23,
    "max_drawdown": -15.3, "win_rate": 58.2, "annual_turnover": 320.5,
    "equity_curve": [{"date": "2025-01-02", "nav": 1.0}],
    "period_returns": [{"period": 1, "start": "2025-01-02", "end": "2025-01-16", "return": 2.5}]
  },
  "factors": [{"name": "ep", "category": "value", "category_label": "价值", "label": "盈利收益率", "description": "1/PE_TTM", "ic_mean": 0.035, "ic_ir": 0.52, "is_valid": true, "weight": 0.08}],
  "ml_model": {"available": true, "model_type": "LightGBM", "accuracy": 0.55, "weight_ratio": 0.4},
  "data_splits": {
    "data_date_range": ["2024-01-02", "2026-03-20"],
    "total_dates": 500,
    "ic_date_range": ["2024-01-02", "2026-03-20"],
    "ic_dates_sampled": 480,
    "ml_train_date_range": ["2024-01-02", "2025-09-15"],
    "ml_train_dates": 400, "ml_train_samples": 120000,
    "ml_test_date_range": ["2025-09-16", "2026-03-20"],
    "ml_test_dates": 100, "ml_test_samples": 30000,
    "ml_train_ratio": 0.8,
    "backtest_date_range": ["2024-01-02", "2026-03-20"],
    "backtest_days": 500
  }
}
```

### Database Models
- `QuantFactorDaily` — Per stock per date factor values + ranks + universe flag
- `QuantICHistory` — Daily cross-sectional IC per factor
- `QuantPortfolio` — Portfolio composition at each rebalance date
- `QuantBacktestResult` — Backtest run metadata, performance metrics, and full result data
  - Extended columns: `name`, `notes`, `analysis_date`, `elapsed_seconds`, `filters_applied` (JSON), `universe_info` (JSON), `signal_info` (JSON), `ml_model_info` (JSON), `factors` (JSON), `config` (JSON), `data_splits` (JSON)
  - Performance metrics: `total_return`, `annual_return`, `annual_volatility`, `sharpe_ratio`, `max_drawdown`, `calmar_ratio`, `win_rate`, `avg_turnover`, `total_cost`
  - Result data: `equity_curve` (JSON), `monthly_returns` (JSON, stores `period_returns` per-rebalance-cycle data), `factor_ic_summary` (JSON), `current_portfolio` (JSON)

### Key Files
- `backend/app/services/quant_engine.py` — Cross-sectional factor computation (50 factors), IC analysis, ML training, portfolio construction, backtest
- `backend/app/services/quant_iterate.py` — Auto-iteration service (period return calculation, re-analysis, iteration record management)
- `backend/app/routers/quant.py` — API router (async analyze with task polling, history CRUD, factors, stock-profile, factor-status, industries, presets, iteration management)
- `backend/app/models.py` — QuantFactorDaily, QuantICHistory, QuantPortfolio, QuantBacktestResult, QuantIteration models
- `backend/app/tasks.py` — `check_quant_iterations` Celery task (daily at 17:00 CST)
- `backend/celery_app.py` — Beat schedule includes `check-quant-iterations`
- `frontend/src/App.vue` — Section 8: 量化选股 UI (preset selector, filters, async task polling, result display, history tab with CRUD + iteration management)
- `frontend/src/api/index.js` — `runQuantAnalysis()`, `getQuantTaskStatus()`, `getQuantHistory()`, `getQuantResult()`, `updateQuantResult()`, `deleteQuantResult()`, `getQuantFactors()`, `getQuantPresets()`, `getQuantIndustries()`, `toggleQuantIterate()`, `getQuantIterations()`, `triggerQuantIteration()`

### Auto-Iteration (Periodic Rebalancing)

Saved quant analysis results can be set to automatically re-run analysis and rebalance portfolios on a schedule matching the original `rebalance_freq` parameter.

**How it works:**
1. User enables "自动迭代" toggle on a history record → creates initial iteration (iteration #0) with the original portfolio
2. Celery Beat runs `check_quant_iterations` daily at 17:00 CST → finds records where `next_iterate_date <= today`
3. For each due record, `run_single_iteration()` is called:
   - Calculates actual period returns for current portfolio (from DB kline data)
   - Marks current iteration as completed with return stats
   - Re-runs full cross-sectional analysis with the same `filters_applied`
   - Creates new iteration with new portfolio, tracking buys/sells vs previous
   - Updates parent record: NAV, cumulative return, next iteration date, `current_portfolio` (latest)
4. User can also manually trigger iteration via "立即执行迭代" button in the iteration dialog

**Viewing iteration portfolios:**
- **Main result view**: When viewing a result with iterations, a "切换期数" selector appears in the portfolio card. Users can switch between the original portfolio and any iteration's portfolio.
- **Iteration dialog**: Opened via "迭代" button in history tab. Shows iteration selector with full portfolio table per iteration, including buy/sell annotations, stock returns, and a summary timeline at the bottom.
- Each `QuantIteration` stores its own `portfolio` JSON (full stock list), while `QuantBacktestResult.current_portfolio` always holds the latest iteration's portfolio.

**Database Models:**
- `QuantBacktestResult` extended with: `auto_iterate`, `iterate_status`, `next_iterate_date`, `total_iterations`, `live_nav`, `live_return_pct`
- `QuantIteration`: per-iteration records with `iteration_num`, `start_date`, `end_date`, `portfolio` (JSON), `new_buys`/`new_sells` (JSON), `period_return_pct`, `cumulative_return_pct`, `nav`, `stock_returns` (JSON), `status`, `message`

**API Endpoints:**
- `POST /api/quant/result/{run_id}/iterate` — Enable/disable auto-iteration (body: `{auto_iterate: bool}`)
- `GET /api/quant/result/{run_id}/iterations` — Get iteration timeline with all iterations (includes full `portfolio` array per iteration)
- `POST /api/quant/result/{run_id}/iterate-now` — Manually trigger one iteration cycle (async, returns task_id)

## Authentication & Authorization

### Architecture
- **JWT-based authentication** with bcrypt password hashing
- **Role-based access**: `admin` (full access) and `user` (permission-based)
- **Feature permissions**: Fine-grained per-user toggle for each module
- Default admin seeded on first startup: `username=AStock`, `password=AStock123!`
- Token stored in `localStorage` key `astock_token`, attached via Axios interceptor
- 401 responses trigger auto-logout via interceptor callback

### Default Permissions for New Users
| Permission | Label | Default |
|-----------|-------|---------|
| stocks | 自选股管理 | Yes |
| quotes | 行情数据 | Yes |
| strategy | 策略交易 | No |
| screener | 智能选股 | No |
| quant | 量化分析 | No |
| logs | 抓取日志 | Yes |
| schedule | 定时更新 | No |
| config | 配置管理 | No |

Admins have all permissions automatically (no per-key checks).

### API Endpoints (prefix: `/api/auth`)
- `POST /login` — OAuth2 form login, returns `{ access_token, token_type, user }`
- `GET /me` — Current user info
- `PUT /me/password` — Change own password (requires `old_password`, `new_password`)
- `GET /users` — List all users (admin only)
- `POST /users` — Create user (admin only)
- `GET /users/{id}` — Get user detail (admin only)
- `PUT /users/{id}` — Update user (admin only)
- `DELETE /users/{id}` — Delete user (admin only, cannot delete last admin)
- `GET /permissions` — List available permission keys with labels

### Database
- `users` table: id, username (unique), hashed_password, display_name, role, permissions (JSON), is_active, created_at, updated_at

### Key Files
- `backend/app/auth.py` — Password hashing (bcrypt), JWT creation/verification, FastAPI dependencies (`get_current_user`, `get_current_admin`, `require_permission`)
- `backend/app/routers/auth.py` — Auth API router with inline Pydantic schemas
- `backend/app/models.py` — `User` model, `DEFAULT_USER_PERMISSIONS`
- `backend/app/config.py` — `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES` (24h)
- `backend/app/main.py` — Auth router registration, admin seed on startup
- `frontend/src/App.vue` — Login page, header user dropdown, permission-based sidebar, user management section (Section 9)
- `frontend/src/api/index.js` — JWT interceptor, 401 auto-logout, auth API methods
