# AStock 数据架构文档

> 本文档详细描述 AStock 系统的所有数据存储、表结构、依赖关系、数据流向，
> 用于 debug、手动查询、问题排查。

---

## 目录

1. [基础设施总览](#1-基础设施总览)
2. [连接信息速查](#2-连接信息速查)
3. [PostgreSQL 表结构详解](#3-postgresql-表结构详解)
4. [表关系与外键依赖](#4-表关系与外键依赖)
5. [数据流向图](#5-数据流向图)
6. [各表读写来源](#6-各表读写来源)
7. [Redis 用途](#7-redis-用途)
8. [本地 CSV 数据文件](#8-本地-csv-数据文件)
9. [Grafana 数据源](#9-grafana-数据源)
10. [常用调试 SQL](#10-常用调试-sql)
11. [数据生命周期与维护](#11-数据生命周期与维护)

---

## 1. 基础设施总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                        AStock 数据存储架构                           │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐   ┌──────────┐   ┌────────────┐   ┌────────────┐  │
│  │ PostgreSQL   │   │  Redis   │   │  Grafana   │   │ CSV Files  │  │
│  │ (主数据库)   │   │ (消息队列)│   │ (监控面板) │   │ (静态数据) │  │
│  │ Port: 5432   │   │ Port:6379│   │ Port: 3000 │   │ data/      │  │
│  │ 17 张表      │   │ DB0+DB1  │   │ 读PG数据   │   │ 6 个文件   │  │
│  └─────────────┘   └──────────┘   └────────────┘   └────────────┘  │
│         ▲                ▲               ▲                           │
│         │                │               │                           │
│  ┌──────┴────┐    ┌──────┴───┐    ┌──────┴───┐                     │
│  │ FastAPI    │    │ Celery   │    │ Grafana  │                     │
│  │ Backend    │    │ Worker   │    │ Server   │                     │
│  │ Port: 8000 │    │ + Beat   │    │          │                     │
│  └───────────┘    └──────────┘    └──────────┘                     │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

所有 Docker 服务通过 `docker-compose.yml` 管理：

| 服务 | 镜像 | 容器名 | 端口 | 数据持久化 |
|------|------|--------|------|-----------|
| PostgreSQL | postgres:16-alpine | astock-postgres | 5432 | `./pgdata:/var/lib/postgresql/data` |
| Redis | redis:7-alpine | astock-redis | 6379 | 无持久化（仅队列） |
| Grafana | grafana/grafana:11.1.0 | astock-grafana | 3000 | `./grafana_data:/var/lib/grafana` |

---

## 2. 连接信息速查

### PostgreSQL

```bash
# 连接命令 (从宿主机)
psql -h localhost -p 5432 -U astock -d astock
# 密码: astock123

# 或者通过 docker
docker exec -it astock-postgres psql -U astock -d astock

# 连接字符串
# 异步 (FastAPI): postgresql+asyncpg://astock:astock123@localhost:5432/astock
# 同步 (Celery):  postgresql+psycopg2://astock:astock123@localhost:5432/astock
```

### Redis

```bash
# 连接命令
redis-cli -h localhost -p 6379

# 查看 Celery 任务队列
redis-cli -n 0 LLEN celery   # 待处理任务数
redis-cli -n 1 KEYS '*'      # 任务结果
```

### Grafana

```
URL:      http://localhost:3000
用户名:   admin
密码:     admin
数据源:   AStock-PostgreSQL (自动配置)
```

---

## 3. PostgreSQL 表结构详解

数据库共 **17 张表**，按功能分为 6 个模块：

### 模块一：股票基础数据

#### 3.1 `stocks` — 股票主表

核心主表，所有其他表通过 `stock_id` 外键引用。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | 内部ID |
| `code` | VARCHAR(10) | UNIQUE, INDEX, NOT NULL | - | 股票代码 (如 `600519`, `000001`) |
| `name` | VARCHAR(50) | NOT NULL | - | 股票名称 (如 `贵州茅台`) |
| `market` | VARCHAR(10) | NOT NULL | `'SH'` | 市场: `SH`=上交所, `SZ`=深交所, `BJ`=北交所 |
| `is_active` | BOOLEAN | - | `true` | 是否激活 |
| `is_favorite` | BOOLEAN | - | `false` | 是否自选 |
| `created_at` | TIMESTAMP | - | `now()` | 创建时间 |

**索引:** `code` 列有唯一索引

**数据量:** ~5,684 行

---

#### 3.2 `stock_profiles` — 股票基本面

每只股票一条记录 (1:1)，存储 PE/PB/市值/行业等基本面数据。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `stock_id` | INTEGER | FK→stocks.id, UNIQUE, NOT NULL | - | |
| `industry` | VARCHAR(50) | NULLABLE | - | 行业 (如 `酿酒行业`, `银行`) |
| `sector` | VARCHAR(200) | NULLABLE | - | 板块 (保留字段) |
| `total_market_cap` | FLOAT | NULLABLE | - | 总市值 (元) |
| `circ_market_cap` | FLOAT | NULLABLE | - | 流通市值 (元) |
| `pe_ttm` | FLOAT | NULLABLE | - | 市盈率TTM |
| `pb` | FLOAT | NULLABLE | - | 市净率 |
| `total_shares` | BIGINT | NULLABLE | - | 总股本 (股) |
| `circ_shares` | BIGINT | NULLABLE | - | 流通股本 (股) |
| `updated_at` | TIMESTAMP | - | `now()` | 最后更新时间 |

**数据来源:** PE/PB/市值 → 腾讯财经API, 行业 → 东方财富via akshare

**数据量:** ~5,683 行

---

### 模块二：行情数据

#### 3.3 `daily_klines` — 日K线

**系统中数据量最大的表**，存储每只股票每个交易日的 OHLCV 数据。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `stock_id` | INTEGER | FK→stocks.id, NOT NULL | - | |
| `date` | DATE | NOT NULL | - | 交易日期 |
| `open` | FLOAT | NOT NULL | - | 开盘价 |
| `high` | FLOAT | NOT NULL | - | 最高价 |
| `low` | FLOAT | NOT NULL | - | 最低价 |
| `close` | FLOAT | NOT NULL | - | 收盘价 |
| `volume` | BIGINT | NOT NULL | - | 成交量 (股) |
| `amount` | FLOAT | NOT NULL | - | 成交额 (元), 不完整数据源设为 0 |
| `change_pct` | FLOAT | NOT NULL | - | 涨跌幅 (%) |
| `turnover_rate` | FLOAT | NULLABLE | - | 换手率 (%), 不完整数据源为 NULL |
| `created_at` | TIMESTAMP | - | `now()` | |

**约束:** `UniqueConstraint("stock_id", "date")` — 每只股票每天只有一条

**数据量:** ~9,900,000 行 (约 5,476 只股票 x ~1,800 交易日)

**数据来源优先级:**
- Phase 1 (完整数据): akshare → tushare → baostock → eastmoney (含 amount + turnover_rate)
- Phase 2 (不完整数据，仅最后兜底): sina → tencent (amount=0, turnover_rate=NULL)

**写入模式:** UPSERT (ON CONFLICT DO UPDATE) — 幂等安全

---

#### 3.4 `quote_snapshots` — 实时行情快照

每次获取实时行情时插入一条，用于记录历史行情变化。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `stock_id` | INTEGER | FK→stocks.id, NOT NULL | - | |
| `price` | FLOAT | NOT NULL | - | 当前价 |
| `open` | FLOAT | NOT NULL | - | 开盘价 |
| `high` | FLOAT | NOT NULL | - | 最高价 |
| `low` | FLOAT | NOT NULL | - | 最低价 |
| `close` | FLOAT | NOT NULL | - | 收盘价 |
| `volume` | BIGINT | NOT NULL | - | 成交量 |
| `amount` | FLOAT | NOT NULL | - | 成交额 |
| `change_pct` | FLOAT | NOT NULL | - | 涨跌幅 |
| `timestamp` | TIMESTAMP | NOT NULL | - | 行情时间戳 |
| `created_at` | TIMESTAMP | - | `now()` | |

**索引:** `Index("ix_quote_snapshots_stock_id_timestamp", "stock_id", "timestamp")`

**去重逻辑:** 插入前检查最新一条，如果 price + volume 相同则跳过

---

### 模块三：数据抓取审计

#### 3.5 `fetch_logs` — 抓取日志

记录所有数据抓取操作的结果，用于审计追踪和问题排查。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `stock_id` | INTEGER | FK→stocks.id, NULLABLE | - | NULL 表示汇总记录 |
| `fetch_type` | VARCHAR(20) | NOT NULL | - | 见下方类型表 |
| `source` | VARCHAR(200) | NULLABLE | - | 格式: `行情:sina,K线:akshare,基本面:tencent` |
| `status` | VARCHAR(10) | NOT NULL | - | `success` / `error` / `partial` |
| `message` | TEXT | NULLABLE | - | 人类可读的详细信息 |
| `created_at` | TIMESTAMP | - | `now()` | |

**fetch_type 取值:**

| fetch_type | 触发方式 | 说明 |
|------------|---------|------|
| `manual_full_fetch` | UI "抓取" 按钮 | 手动单股全量抓取 |
| `scheduled_full_fetch` | Celery 定时任务 | 定时全量抓取 (每股一条) |
| `daily_update_summary` | Celery 定时任务 | 整次运行汇总 (stock_id=NULL) |
| `lite` | UI "全部抓取" 按钮 | 轻量异步抓取 (仅行情+K线) |
| `realtime` | 定时行情抓取 | 仅实时行情 |
| `kline` | 手动K线抓取 | 仅K线数据 |

---

#### 3.6 `app_settings` — 系统配置

键值对形式存储运行时配置和状态。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `key` | VARCHAR(100) | UNIQUE, INDEX, NOT NULL | - | 配置键 |
| `value` | TEXT | NULLABLE | - | 配置值 |
| `updated_at` | TIMESTAMP | - | `now()` | |

**常用 key 清单:**

| key | 说明 | 示例值 |
|-----|------|--------|
| `daily_update_enabled` | 是否启用定时更新 | `"true"` |
| `daily_update_hour` | 定时更新小时 | `"16"` |
| `daily_update_minute` | 定时更新分钟 | `"0"` |
| `daily_update_timezone` | 时区 | `"Asia/Shanghai"` |
| `daily_update_last_run` | 上次运行时间 | ISO 时间戳 |
| `daily_update_last_status` | 上次运行状态 | `"success"` / `"error"` |
| `daily_update_last_message` | 上次运行消息 | `"更新5680只,成功5670,失败10"` |

---

### 模块四：AI 分析

#### 3.7 `analysis_history` — AI 分析历史

存储 LLM 生成的股票分析报告。注意：通过 `stock_code` (字符串) 关联，**非外键**。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `stock_code` | VARCHAR(10) | INDEX, NOT NULL | - | 股票代码 (非FK) |
| `stock_name` | VARCHAR(50) | NOT NULL | `""` | 股票名称 |
| `report_type` | VARCHAR(20) | NOT NULL | `"detailed"` | 报告类型 |
| `model_used` | VARCHAR(100) | NULLABLE | - | LLM模型 (如 `gemini/gemini-2.5-flash`) |
| `sentiment_score` | INTEGER | NULLABLE | - | 情绪分数 0-100 (50=中性) |
| `operation_advice` | VARCHAR(20) | NULLABLE | - | 操作建议 (买入/卖出/持有...) |
| `trend_prediction` | VARCHAR(20) | NULLABLE | - | 趋势预测 (上升/下降/震荡) |
| `current_price` | FLOAT | NULLABLE | - | 分析时价格 |
| `change_pct` | FLOAT | NULLABLE | - | 分析时涨跌幅 |
| `report_json` | TEXT | NULLABLE | - | 完整 LLM 响应 (JSON) |
| `context_json` | TEXT | NULLABLE | - | 分析上下文快照 (JSON) |
| `created_at` | TIMESTAMP | - | `now()` | |

**索引:** `Index("ix_analysis_history_stock_code_created", "stock_code", "created_at")`

**去重:** 同一只股票同一天只生成一次 (除非 force_refresh=true)

---

### 模块五：策略交易

#### 3.8 `trade_strategies` — 交易策略

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `name` | VARCHAR(100) | NOT NULL | - | 策略名称 |
| `stock_code` | VARCHAR(10) | INDEX, NOT NULL | - | 目标股票代码 (非FK) |
| `stock_name` | VARCHAR(50) | NOT NULL | `""` | 目标股票名称 |
| `market` | VARCHAR(10) | NOT NULL | `"SH"` | 市场 |
| `mode` | VARCHAR(10) | NOT NULL | `"simulated"` | `simulated`=模拟, `live`=实盘 |
| `status` | VARCHAR(20) | NOT NULL | `"draft"` | 状态 (见下方状态机) |
| `current_step_order` | INTEGER | NOT NULL | `1` | 当前执行到第几步 |
| `sim_initial_cash` | FLOAT | NOT NULL | `100000.0` | 初始资金 |
| `sim_cash` | FLOAT | NOT NULL | `100000.0` | 当前现金 |
| `sim_holdings` | INTEGER | NOT NULL | `0` | 当前持仓数量 |
| `sim_avg_cost` | FLOAT | NOT NULL | `0.0` | 持仓均价 |
| `notes` | TEXT | NULLABLE | - | 备注 |
| `error_message` | TEXT | NULLABLE | - | 错误信息 |
| `created_at` | TIMESTAMP | - | `now()` | |
| `updated_at` | TIMESTAMP | - | `now()` | |

**策略状态机:**
```
draft → active → completed
  ↓        ↓
  ↓      paused → active (可恢复)
  ↓        ↓
  └──→ cancelled ←──┘
         error (异常自动设置)
```

**索引:** `Index("ix_trade_strategies_status", "status")`

---

#### 3.9 `trade_steps` — 策略步骤

策略内按顺序执行的步骤，每步包含触发条件和买卖动作。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `strategy_id` | INTEGER | FK→trade_strategies.id (CASCADE), NOT NULL | - | |
| `step_order` | INTEGER | NOT NULL | `1` | 执行顺序 |
| `name` | VARCHAR(100) | NOT NULL | `""` | 步骤名称 |
| `condition_logic` | VARCHAR(5) | NOT NULL | `"AND"` | 条件逻辑: `AND` / `OR` |
| `action_type` | VARCHAR(10) | NOT NULL | - | `buy` / `sell` |
| `quantity` | INTEGER | NOT NULL | - | 交易数量 (股) |
| `price_type` | VARCHAR(10) | NOT NULL | `"market"` | `market`=市价, `limit`=限价 |
| `limit_price` | FLOAT | NULLABLE | - | 限价价格 |
| `status` | VARCHAR(20) | NOT NULL | `"waiting"` | 见下方状态机 |
| `fill_price` | FLOAT | NULLABLE | - | 成交价 |
| `fill_quantity` | INTEGER | NULLABLE | - | 成交数量 |
| `filled_at` | TIMESTAMP | NULLABLE | - | 成交时间 |
| `created_at` | TIMESTAMP | - | `now()` | |

**步骤状态机:** `waiting → watching → triggered → filled` (或 `failed`)

**索引:** `Index("ix_trade_steps_strategy_order", "strategy_id", "step_order")`

**级联删除:** 删除策略时自动删除所有步骤

---

#### 3.10 `trade_conditions` — 触发条件

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `step_id` | INTEGER | FK→trade_steps.id (CASCADE), NOT NULL | - | |
| `field` | VARCHAR(30) | NOT NULL | - | 监控字段 (见下表) |
| `operator` | VARCHAR(5) | NOT NULL | - | `>=`, `<=`, `>`, `<`, `==` |
| `value` | FLOAT | NOT NULL | - | 触发阈值 |
| `is_met` | BOOLEAN | NOT NULL | `false` | 是否已满足 |
| `met_at` | TIMESTAMP | NULLABLE | - | 满足时间 |
| `created_at` | TIMESTAMP | - | `now()` | |

**可用监控字段:**

| field | 说明 |
|-------|------|
| `price` | 当前价格 |
| `open_price` | 开盘价格 |
| `change_pct` | 涨跌幅% |
| `rise_pct` | 涨幅% (较昨收，下跌时为0) |
| `fall_pct` | 跌幅% (较昨收，上涨时为0) |
| `profit_pct` | 浮盈比例% (基于持仓均价) |
| `loss_pct` | 浮亏比例% (基于持仓均价) |
| `volume` | 成交量 |
| `turnover_rate` | 换手率% |

---

#### 3.11 `trade_executions` — 执行事件日志

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `strategy_id` | INTEGER | FK→trade_strategies.id (CASCADE), NOT NULL | - | |
| `step_id` | INTEGER | FK→trade_steps.id (SET NULL), NULLABLE | - | |
| `event_type` | VARCHAR(30) | NOT NULL | - | 事件类型 |
| `message` | TEXT | NULLABLE | - | 事件描述 |
| `price_snapshot` | FLOAT | NULLABLE | - | 事件发生时价格 |
| `created_at` | TIMESTAMP | - | `now()` | |

**event_type 取值:** `strategy_started`, `strategy_paused`, `strategy_cancelled`, `condition_met`, `step_triggered`, `order_filled`, `order_failed`, `strategy_completed`, `error`

**索引:** `Index("ix_trade_executions_strategy_created", "strategy_id", "created_at")`

---

### 模块六：量化分析

#### 3.12 `quant_factor_daily` — 因子每日数据

存储每只股票每天的 50 个因子原始值和排名。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `stock_id` | INTEGER | FK→stocks.id, NOT NULL | - | |
| `date` | DATE | NOT NULL | - | 交易日期 |
| `raw_factors` | JSON | NULLABLE | - | 50个因子原始值 `{"ep": 0.05, "bp": 0.3, ...}` |
| `rank_factors` | JSON | NULLABLE | - | 50个因子横截面排名 |
| `composite_score` | FLOAT | NULLABLE | - | 综合打分 (0-100) |
| `composite_rank` | INTEGER | NULLABLE | - | 综合排名 |
| `in_universe` | BOOLEAN | - | `true` | 是否在选股池内 |
| `exclude_reason` | VARCHAR(100) | NULLABLE | - | 排除原因 (ST/IPO<60天/流动性不足等) |

**约束:** `UniqueConstraint("stock_id", "date")`
**索引:** `Index("ix_quant_factor_date", "date")`

**注意:** 目前分析在内存中运行，结果通过 API 返回，不持久化到此表。

---

#### 3.13 `quant_ic_history` — 因子 IC 历史

记录每个因子每天的截面 IC 值。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `date` | DATE | NOT NULL | - | 交易日期 |
| `factor_name` | VARCHAR(50) | NOT NULL | - | 因子名 (如 `ep`, `mom_5d`, `rsi_14`) |
| `ic_value` | FLOAT | NULLABLE | - | Spearman IC |
| `rank_ic_value` | FLOAT | NULLABLE | - | Rank IC |
| `forward_days` | INTEGER | - | `10` | 前瞻收益天数 |

**约束:** `UniqueConstraint("date", "factor_name")`
**索引:** `Index("ix_quant_ic_date", "date")`

---

#### 3.14 `quant_portfolios` — 量化组合

记录每次再平衡时的持仓组合。

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `run_id` | VARCHAR(36) | NOT NULL | - | 运行批次ID (UUID) |
| `rebalance_date` | DATE | NOT NULL | - | 再平衡日期 |
| `stock_id` | INTEGER | FK→stocks.id, NULLABLE | - | |
| `stock_code` | VARCHAR(10) | NULLABLE | - | 冗余存储 |
| `stock_name` | VARCHAR(50) | NULLABLE | - | 冗余存储 |
| `weight` | FLOAT | NULLABLE | - | 持仓权重 |
| `rank` | INTEGER | NULLABLE | - | 综合排名 |
| `composite_score` | FLOAT | NULLABLE | - | 综合分数 |
| `action` | VARCHAR(10) | NULLABLE | - | `new`/`hold`/`exit` |
| `sector` | VARCHAR(50) | NULLABLE | - | 所属行业 |

**索引:** `Index("ix_quant_portfolio_run_date", "run_id", "rebalance_date")`

---

#### 3.15 `quant_backtest_results` — 回测结果

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `run_id` | VARCHAR(36) | UNIQUE, NOT NULL | - | 运行批次ID |
| `created_at` | TIMESTAMP | - | `now()` | |
| `config` | JSON | NULLABLE | - | 运行参数 (top_n, rebalance_freq 等) |
| `total_return` | FLOAT | NULLABLE | - | 总收益率 (%) |
| `annual_return` | FLOAT | NULLABLE | - | 年化收益率 (%) |
| `annual_volatility` | FLOAT | NULLABLE | - | 年化波动率 (%) |
| `sharpe_ratio` | FLOAT | NULLABLE | - | 夏普比率 |
| `max_drawdown` | FLOAT | NULLABLE | - | 最大回撤 (%) |
| `calmar_ratio` | FLOAT | NULLABLE | - | 卡尔玛比率 |
| `win_rate` | FLOAT | NULLABLE | - | 胜率 (%) |
| `avg_turnover` | FLOAT | NULLABLE | - | 平均换手率 |
| `total_cost` | FLOAT | NULLABLE | - | 总交易成本 |
| `equity_curve` | JSON | NULLABLE | - | 净值曲线 `[{date, nav}]` |
| `monthly_returns` | JSON | NULLABLE | - | 月度收益 `[{month, return}]` |
| `factor_ic_summary` | JSON | NULLABLE | - | 因子IC摘要 |
| `current_portfolio` | JSON | NULLABLE | - | 当前持仓快照 |
| `auto_iterate` | BOOLEAN | - | `false` | 是否启用自动迭代 |
| `iterate_status` | VARCHAR(20) | NULLABLE | - | 迭代状态 (idle/running/paused/error) |
| `next_iterate_date` | DATE | NULLABLE | - | 下次迭代日期 |
| `total_iterations` | INTEGER | - | `0` | 累计迭代次数 |
| `live_nav` | FLOAT | - | `1.0` | 实盘净值 |
| `live_return_pct` | FLOAT | - | `0.0` | 实盘累计收益率 |

#### 3.16 `quant_iterations` — 量化迭代记录

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `run_id` | VARCHAR(36) | NOT NULL, 索引 | - | 关联回测结果的 run_id |
| `iteration_num` | INTEGER | NOT NULL | - | 迭代期数 (0=初始) |
| `start_date` | DATE | NOT NULL | - | 本期起始日期 |
| `end_date` | DATE | NULLABLE | - | 本期结束日期 |
| `portfolio` | JSON | NULLABLE | - | 本期持仓 `[{stock_code, stock_name, industry, score, weight}]` |
| `new_buys` | JSON | NULLABLE | - | 新买入股票代码列表 |
| `new_sells` | JSON | NULLABLE | - | 新卖出股票代码列表 |
| `period_return_pct` | FLOAT | NULLABLE | - | 本期收益率 (%) |
| `cumulative_return_pct` | FLOAT | NULLABLE | - | 累计收益率 (%) |
| `nav` | FLOAT | - | `1.0` | 当前净值 |
| `stock_returns` | JSON | NULLABLE | - | 个股收益明细 |
| `status` | VARCHAR(20) | - | `active` | 状态 (active/completed/error) |
| `message` | VARCHAR(500) | NULLABLE | - | 状态说明 |
| `created_at` | TIMESTAMP | - | `now()` | 创建时间 |
| `completed_at` | TIMESTAMP | NULLABLE | - | 完成时间 |

#### 3.17 `users` — 用户表

| 列名 | 类型 | 约束 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | INTEGER | PK, 自增 | - | |
| `username` | VARCHAR(50) | UNIQUE, 索引 | - | 用户名 |
| `hashed_password` | VARCHAR(200) | NOT NULL | - | bcrypt 哈希密码 |
| `display_name` | VARCHAR(100) | NULLABLE | - | 显示名称 |
| `role` | VARCHAR(20) | - | `user` | 角色 (admin/user) |
| `permissions` | JSON | NULLABLE | - | 功能权限 (参见 DEFAULT_USER_PERMISSIONS) |
| `is_active` | BOOLEAN | - | `true` | 是否启用 |
| `created_at` | TIMESTAMP | - | `now()` | 创建时间 |
| `updated_at` | TIMESTAMP | - | `now()` | 更新时间 |

---

## 4. 表关系与外键依赖

```
stocks (PK: id)
  │
  ├──→ quote_snapshots.stock_id      (FK, NOT NULL)
  ├──→ daily_klines.stock_id          (FK, NOT NULL)
  ├──→ fetch_logs.stock_id            (FK, NULLABLE — 汇总记录无stock_id)
  ├──→ stock_profiles.stock_id        (FK, UNIQUE — 一对一)
  ├──→ quant_factor_daily.stock_id    (FK, NOT NULL)
  └──→ quant_portfolios.stock_id      (FK, NULLABLE)

trade_strategies (PK: id)
  │
  ├──→ trade_steps.strategy_id        (FK, CASCADE — 删策略自动删步骤)
  │     └──→ trade_conditions.step_id  (FK, CASCADE — 删步骤自动删条件)
  │
  └──→ trade_executions.strategy_id   (FK, CASCADE — 删策略自动删日志)
        trade_executions.step_id       (FK, SET NULL — 删步骤保留日志)

analysis_history                       (stock_code 字符串关联，无外键)
quant_ic_history                       (独立表，无外键)
quant_backtest_results                 (独立表，无外键)
  └──→ quant_iterations.run_id         (逻辑关联，非外键)
app_settings                           (独立键值表)
users                                  (独立表，无外键)
```

**级联删除规则:**
- 删除 `trade_strategies` → 自动删除其下所有 `trade_steps` + `trade_conditions` + `trade_executions`
- 删除 `trade_steps` → 自动删除其下所有 `trade_conditions`；对应 `trade_executions.step_id` 置 NULL
- 删除 `stocks` → **不会级联** (需手动处理关联数据)

---

## 5. 数据流向图

### 5.1 数据采集流 (外部API → 数据库)

```
                    ┌──────────────────────────────────────────────┐
                    │           外部数据源 (6个)                     │
                    │                                              │
                    │  akshare   tushare   baostock                │
                    │  eastmoney  sina     tencent                 │
                    └──────────┬───────────────────────────────────┘
                               │
                     StockDataAggregator (优先级降级)
                               │
              ┌────────────────┼────────────────────┐
              │                │                    │
              ▼                ▼                    ▼
    ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐
    │ 实时行情     │  │ 日K线数据     │  │ 基本面+行业       │
    │ (5个源降级)  │  │ (两阶段降级)  │  │ (腾讯+东财)       │
    └──────┬──────┘  └──────┬───────┘  └────────┬─────────┘
           │                │                    │
           ▼                ▼                    ▼
    quote_snapshots   daily_klines        stock_profiles
      (INSERT)        (UPSERT)              (UPSERT)
                                                │
                            ┌───────────────────┘
                            ▼
                      fetch_logs (每次抓取记录审计)
```

### 5.2 功能数据流

```
daily_klines ──────→ AI 分析 ──────→ analysis_history
     +                  │
stock_profiles          │ (LLM调用)
     +                  │
quote_snapshots ────────┘

daily_klines ──────→ 量化分析 ──────→ API 响应 (内存)
     +                  │              (quant_* 表设计已有但暂未持久化)
stock_profiles          │ (50因子+IC+ML)
                        │
                        └──→ quant_factor_daily / quant_ic_history
                             quant_portfolios / quant_backtest_results

quote_snapshots ───→ 策略交易引擎 ──→ trade_executions
     +                  │               trade_steps (状态更新)
trade_strategies        │               trade_strategies (模拟盈亏)
  + steps               │
  + conditions          │ (每5秒tick)
                        └──→ 实盘 broker (可选)
```

### 5.3 定时任务流

```
Celery Beat (每10分钟)
    │
    └──→ check_scheduled_update()
           │  读取 app_settings (时间配置)
           │  判断是否到点 + 今天是否已执行
           │
           └──→ daily_update_task()
                  │  加载所有 active 股票 (~5,684只)
                  │
                  └──→ 逐股执行 _full_fetch_one_stock_sync():
                        ├── 1. 实时行情 → quote_snapshots
                        ├── 2. 日K线 (gap-aware) → daily_klines
                        ├── 3. 基本面 (PE/PB/市值) → stock_profiles
                        ├── 4. 行业 (best-effort) → stock_profiles
                        └── 5. 日志 → fetch_logs
                        
                  节奏: 0.1-0.3s/股, 每50股停2-5s
                  耗时: 约30-60分钟完成全部

Celery Beat (每5秒, 交易时间)
    │
    └──→ trade_monitor_tick()
           ├── 查找 active 策略
           ├── 批量获取实时行情
           └── trade_engine.tick()
                 ├── 评估条件 → trade_conditions (更新 is_met)
                 ├── 触发步骤 → trade_steps (状态推进)
                 ├── 模拟成交 → trade_strategies (更新现金/持仓)
                 └── 记录事件 → trade_executions
```

---

## 6. 各表读写来源

### 写入来源

| 表 | 写入方 | 触发条件 | 操作 |
|----|--------|---------|------|
| **stocks** | `routers/stocks.py` | 用户手动添加 | INSERT |
| | `scripts/download_all_data.py` | 初始化批量导入 | UPSERT |
| | `tasks.py` (日常更新) | 腾讯返回新名称 | UPDATE name |
| **daily_klines** | `routers/stocks.py` | 手动抓取 | UPSERT |
| | `tasks.py` (daily_update) | 定时更新 | UPSERT |
| | `scripts/download_all_data.py` | 初始化批量导入 | UPSERT |
| **quote_snapshots** | `routers/stocks.py` | 手动/批量抓取 | INSERT (去重) |
| | `tasks.py` (daily_update) | 定时更新 | INSERT (去重) |
| **stock_profiles** | `routers/stocks.py` | 手动抓取 | UPSERT |
| | `tasks.py` (daily_update) | 定时更新 | UPSERT |
| | `scripts/download_all_data.py` | 初始化批量导入 | UPSERT |
| **fetch_logs** | 所有抓取路径 | 每次数据抓取 | INSERT |
| **app_settings** | `tasks.py` / `routers/schedule.py` | 定时任务/用户配置 | UPSERT |
| **analysis_history** | `services/ai_analysis.py` | 用户触发AI分析 | INSERT |
| **trade_strategies** | `routers/trade.py` | 用户创建/修改策略 | INSERT/UPDATE |
| | `services/trade_engine.py` | 引擎执行 | UPDATE (状态/资金) |
| **trade_steps** | `routers/trade.py` | 用户创建策略 | INSERT |
| | `services/trade_engine.py` | 引擎执行 | UPDATE (状态/成交) |
| **trade_conditions** | `routers/trade.py` | 用户创建策略 | INSERT |
| | `services/trade_engine.py` | 引擎评估 | UPDATE (is_met) |
| **trade_executions** | `routers/trade.py` + engine | 任何策略事件 | INSERT |

### 读取来源

| 表 | 主要读取方 | 用途 |
|----|-----------|------|
| **stocks** | `routers/stocks.py` (列表), `tasks.py` (批量更新), `routers/quant.py` (选股) | 核心引用表 |
| **daily_klines** | `routers/quotes.py` (行情页), `services/ai_analysis.py` (AI), `services/quant_engine.py` (量化), `tasks.py` (gap检测) | 数据量最大的读操作 |
| **quote_snapshots** | `routers/quotes.py` (行情页), 去重检查 | |
| **stock_profiles** | `routers/quotes.py`, `routers/stocks.py` (enriched列表), `routers/quant.py` (筛选), `services/ai_analysis.py` (AI) | |
| **fetch_logs** | `routers/quotes.py` (日志页) | |
| **app_settings** | `tasks.py` (定时检查), `routers/schedule.py` (UI) | |
| **analysis_history** | `routers/ai.py` (历史/报告), AI去重检查 | |
| **trade_*** | `routers/trade.py` (UI), `services/trade_engine.py` (引擎tick) | |
| **quant_factor_daily** | `routers/quant.py` (因子状态查询) | 目前仅查询，不写入 |

---

## 7. Redis 用途

Redis **仅用于 Celery**，不做应用级缓存：

| DB | 用途 | 说明 |
|----|------|------|
| DB 0 | Celery Broker | 任务消息队列 (待执行任务) |
| DB 1 | Celery Result Backend | 任务执行结果 |

```bash
# 查看待处理任务数
redis-cli -n 0 LLEN celery

# 查看所有任务结果键
redis-cli -n 1 KEYS '*'

# 清空任务队列 (危险!)
redis-cli -n 0 FLUSHDB
```

---

## 8. 本地 CSV 数据文件

位于 `data/` 目录，为静态参考数据，**不随数据库自动同步**：

| 文件 | 行数 | 用途 | 内容 |
|------|------|------|------|
| `all_a_shares.csv` | 5,683 | 全A股列表 | code, name |
| `all_constituents.csv` | 5,683 | 全A股代码 | code (向后兼容) |
| `csi300_constituents.csv` | 301 | 沪深300成分股 | code, name, inclusion_date |
| `csi500_constituents.csv` | 501 | 中证500成分股 | code, name, inclusion_date |
| `stock_industries.csv` | 4,787 | 行业分类 | code, industry |
| `stock_profiles_snapshot.csv` | 5,684 | 基本面快照 | code, name, industry, pe_ttm, pb, market_cap |

这些文件由 `scripts/download_all_data.py` 生成，用于初始化导入参考。

---

## 9. Grafana 数据源

### 配置文件

- 数据源: `grafana/provisioning/datasources/postgres.yml`
- 面板: `grafana/provisioning/dashboards/astock.json`

### 面板

| 面板 | 类型 | SQL 查询目标 |
|------|------|-------------|
| 最新价格概览 | Table | `quote_snapshots` (最新行情) |
| 日K线-收盘价 | Timeseries | `daily_klines` (close 字段) |
| 日K线-成交量 | Timeseries | `daily_klines` (volume 字段) |

---

## 10. 常用调试 SQL

### 数据概览

```sql
-- 各表数据量
SELECT 'stocks' AS tbl, COUNT(*) FROM stocks
UNION ALL SELECT 'daily_klines', COUNT(*) FROM daily_klines
UNION ALL SELECT 'quote_snapshots', COUNT(*) FROM quote_snapshots
UNION ALL SELECT 'stock_profiles', COUNT(*) FROM stock_profiles
UNION ALL SELECT 'fetch_logs', COUNT(*) FROM fetch_logs
UNION ALL SELECT 'analysis_history', COUNT(*) FROM analysis_history
UNION ALL SELECT 'app_settings', COUNT(*) FROM app_settings
UNION ALL SELECT 'trade_strategies', COUNT(*) FROM trade_strategies
UNION ALL SELECT 'trade_executions', COUNT(*) FROM trade_executions
UNION ALL SELECT 'quant_factor_daily', COUNT(*) FROM quant_factor_daily;
```

### 股票基础查询

```sql
-- 查找股票
SELECT id, code, name, market, is_favorite FROM stocks WHERE code = '600519';
SELECT id, code, name, market FROM stocks WHERE name LIKE '%茅台%';

-- 自选股列表
SELECT id, code, name, market FROM stocks WHERE is_favorite = true;

-- 各市场股票数
SELECT market, COUNT(*) FROM stocks WHERE is_active = true GROUP BY market;
```

### K线数据查询

```sql
-- 某只股票最近10条K线
SELECT date, open, high, low, close, volume, amount, change_pct, turnover_rate
FROM daily_klines
WHERE stock_id = (SELECT id FROM stocks WHERE code = '600519')
ORDER BY date DESC LIMIT 10;

-- K线日期范围
SELECT s.code, s.name, MIN(dk.date) AS earliest, MAX(dk.date) AS latest, COUNT(*) AS rows
FROM daily_klines dk JOIN stocks s ON dk.stock_id = s.id
WHERE s.code = '600519'
GROUP BY s.code, s.name;

-- 缺少K线的股票 (有 stocks 记录但无 kline)
SELECT s.code, s.name FROM stocks s
LEFT JOIN daily_klines dk ON s.id = dk.stock_id
WHERE dk.id IS NULL AND s.is_active = true;

-- K线数据最旧的10只股票
SELECT s.code, s.name, MAX(dk.date) AS latest_kline
FROM daily_klines dk JOIN stocks s ON dk.stock_id = s.id
GROUP BY s.code, s.name
ORDER BY latest_kline ASC LIMIT 10;
```

### 行情快照查询

```sql
-- 某只股票最新行情
SELECT price, open, high, low, close, volume, amount, change_pct, timestamp
FROM quote_snapshots
WHERE stock_id = (SELECT id FROM stocks WHERE code = '600519')
ORDER BY created_at DESC LIMIT 1;

-- 今日所有快照
SELECT s.code, s.name, qs.price, qs.change_pct, qs.volume, qs.timestamp
FROM quote_snapshots qs JOIN stocks s ON qs.stock_id = s.id
WHERE qs.timestamp::date = CURRENT_DATE
ORDER BY qs.change_pct DESC;
```

### 基本面查询

```sql
-- 某只股票基本面
SELECT s.code, s.name, sp.industry, sp.pe_ttm, sp.pb,
       sp.total_market_cap / 1e8 AS "总市值(亿)",
       sp.circ_market_cap / 1e8 AS "流通市值(亿)",
       sp.updated_at
FROM stock_profiles sp JOIN stocks s ON sp.stock_id = s.id
WHERE s.code = '600519';

-- 行业分布
SELECT industry, COUNT(*) AS cnt
FROM stock_profiles WHERE industry IS NOT NULL
GROUP BY industry ORDER BY cnt DESC;

-- 低估值大盘股
SELECT s.code, s.name, sp.industry, sp.pe_ttm, sp.pb,
       sp.total_market_cap / 1e8 AS "市值(亿)"
FROM stock_profiles sp JOIN stocks s ON sp.stock_id = s.id
WHERE sp.pe_ttm > 0 AND sp.pe_ttm < 15 AND sp.pb < 2
  AND sp.total_market_cap > 50000000000
ORDER BY sp.total_market_cap DESC;

-- 缺少行业的股票
SELECT s.code, s.name FROM stocks s
JOIN stock_profiles sp ON s.id = sp.stock_id
WHERE sp.industry IS NULL AND s.is_active = true;
```

### 抓取日志查询

```sql
-- 最近的抓取记录
SELECT fl.created_at, s.code, s.name, fl.fetch_type, fl.source, fl.status, fl.message
FROM fetch_logs fl LEFT JOIN stocks s ON fl.stock_id = s.id
ORDER BY fl.created_at DESC LIMIT 20;

-- 最近一次定时任务汇总
SELECT * FROM fetch_logs
WHERE fetch_type = 'daily_update_summary'
ORDER BY created_at DESC LIMIT 1;

-- 抓取失败的记录
SELECT fl.created_at, s.code, s.name, fl.fetch_type, fl.message
FROM fetch_logs fl LEFT JOIN stocks s ON fl.stock_id = s.id
WHERE fl.status = 'error'
ORDER BY fl.created_at DESC LIMIT 20;
```

### 定时任务配置查询

```sql
-- 查看所有配置
SELECT key, value, updated_at FROM app_settings ORDER BY key;

-- 查看定时任务状态
SELECT key, value FROM app_settings
WHERE key LIKE 'daily_update%' ORDER BY key;
```

### AI 分析查询

```sql
-- 某只股票的分析历史
SELECT id, stock_code, stock_name, model_used, sentiment_score,
       operation_advice, trend_prediction, current_price, created_at
FROM analysis_history
WHERE stock_code = '600519'
ORDER BY created_at DESC;

-- 最近的 AI 分析报告
SELECT stock_code, stock_name, sentiment_score, operation_advice, created_at
FROM analysis_history
ORDER BY created_at DESC LIMIT 10;
```

### 策略交易查询

```sql
-- 所有策略概览
SELECT ts.id, ts.name, ts.stock_code, ts.stock_name, ts.mode, ts.status,
       ts.sim_cash, ts.sim_holdings, ts.sim_avg_cost,
       (SELECT COUNT(*) FROM trade_steps WHERE strategy_id = ts.id) AS steps
FROM trade_strategies ts ORDER BY ts.updated_at DESC;

-- 某策略的步骤和条件
SELECT step.step_order, step.name, step.action_type, step.quantity,
       step.status, step.fill_price,
       cond.field, cond.operator, cond.value, cond.is_met
FROM trade_steps step
JOIN trade_conditions cond ON cond.step_id = step.id
WHERE step.strategy_id = 1
ORDER BY step.step_order, cond.id;

-- 策略执行日志
SELECT te.event_type, te.message, te.price_snapshot, te.created_at
FROM trade_executions te
WHERE te.strategy_id = 1
ORDER BY te.created_at DESC;

-- 活跃策略
SELECT id, name, stock_code, stock_name, status, current_step_order
FROM trade_strategies WHERE status = 'active';
```

### 数据质量检查

```sql
-- K线数据完整性 (amount=0 表示不完整数据源)
SELECT s.code, s.name, COUNT(*) AS zero_amount_rows
FROM daily_klines dk JOIN stocks s ON dk.stock_id = s.id
WHERE dk.amount = 0
GROUP BY s.code, s.name
ORDER BY zero_amount_rows DESC LIMIT 20;

-- turnover_rate 为 NULL 的K线占比
SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE turnover_rate IS NULL) AS null_turnover,
  ROUND(100.0 * COUNT(*) FILTER (WHERE turnover_rate IS NULL) / COUNT(*), 1) AS pct
FROM daily_klines;

-- 重复快照检测
SELECT stock_id, price, volume, COUNT(*) AS dupes
FROM quote_snapshots
GROUP BY stock_id, price, volume
HAVING COUNT(*) > 1
ORDER BY dupes DESC LIMIT 10;

-- 数据库大小
SELECT pg_size_pretty(pg_database_size('astock')) AS db_size;

-- 各表磁盘占用
SELECT relname AS table_name,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
```

---

## 11. 数据生命周期与维护

### 数据初始化顺序

```
1. docker compose up -d         ← 启动 PG/Redis/Grafana
2. 启动 FastAPI                  ← init_db() 自动建表 (Base.metadata.create_all)
3. scripts/download_all_data.py  ← 导入全A股: stocks + klines + profiles
4. 启动 Celery Worker + Beat    ← 开始定时更新
```

**注意:** 没有使用 Alembic 迁移。表结构变更通过 `create_all()` 自动处理（只创建不存在的表，不会修改已有表的列）。如需变更已有列，需手动 `ALTER TABLE`。

### 数据增长预估

| 表 | 当前量 | 日增量 | 月增量 |
|----|--------|--------|--------|
| daily_klines | ~990万 | ~5,700 (每股1行) | ~12.5万 |
| quote_snapshots | 增长快 | ~5,700×N (N=每日抓取次数) | 视抓取频率 |
| fetch_logs | 增长快 | ~5,700/次 | 视抓取频率 |
| 其他表 | 小 | 极少 | 极少 |

### 数据清理建议

```sql
-- 清理30天前的行情快照 (保留K线即可)
DELETE FROM quote_snapshots WHERE created_at < NOW() - INTERVAL '30 days';

-- 清理90天前的抓取日志
DELETE FROM fetch_logs WHERE created_at < NOW() - INTERVAL '90 days';

-- 清理180天前的AI分析历史 (可选)
DELETE FROM analysis_history WHERE created_at < NOW() - INTERVAL '180 days';

-- VACUUM 回收空间
VACUUM ANALYZE quote_snapshots;
VACUUM ANALYZE fetch_logs;
```

### 备份

```bash
# 全库备份
docker exec astock-postgres pg_dump -U astock astock > backup_$(date +%Y%m%d).sql

# 仅备份核心数据 (不含快照和日志)
docker exec astock-postgres pg_dump -U astock astock \
  -t stocks -t daily_klines -t stock_profiles -t app_settings \
  -t trade_strategies -t trade_steps -t trade_conditions -t trade_executions \
  -t analysis_history \
  > backup_core_$(date +%Y%m%d).sql

# 恢复
cat backup_20260325.sql | docker exec -i astock-postgres psql -U astock astock
```
