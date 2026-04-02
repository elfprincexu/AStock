# 多数据源字段映射与统一格式规范

本文档详细说明 AStock 系统中六个数据源的原始 API 字段格式，以及它们如何映射到统一的内部数据结构。

系统使用 `StockDataAggregator` 进行优先级 fallback 管理，默认优先级: `akshare → tushare → baostock → eastmoney → sina → tencent`

东方财富的字段详解请参阅 [eastmoney_fields.md](eastmoney_fields.md)。

---

## 目录

1. [统一数据格式](#1-统一数据格式)
2. [数据源概览 (6源)](#2-数据源概览)
3. [新浪财经 (Sina)](#3-新浪财经-sina)
4. [腾讯财经 (Tencent)](#4-腾讯财经-tencent)
5. [各源对比](#5-各源对比)
6. [已知限制](#6-已知限制)

---

## 1. 统一数据格式

无论数据来自哪个提供商，所有客户端 (`EastMoneyClient`, `SinaClient`, `TencentClient`) 输出完全相同的 dict 结构。

### 1.1 实时行情 (Realtime Quote)

```python
{
    "code": str,           # 股票代码，如 "600519"
    "name": str,           # 股票名称，如 "贵州茅台"
    "price": float,        # 最新价 (元)
    "open": float,         # 开盘价 (元)
    "high": float,         # 最高价 (元)
    "low": float,          # 最低价 (元)
    "close": float,        # 收盘价 = 最新价 (元)
    "prev_close": float,   # 昨收价 (元)
    "volume": int,         # 成交量 (股, 非手)
    "amount": float,       # 成交额 (元)
    "change_pct": float,   # 涨跌幅 (%, 如 2.35 表示 +2.35%)
    "turnover_rate": float, # 换手率 (%, 新浪为 0.0)
    "timestamp": datetime, # 快照时间
}
```

### 1.2 日 K 线 (Daily Kline)

```python
{
    "date": date,            # 交易日
    "open": float,           # 开盘价 (元, 前复权)
    "high": float,           # 最高价 (元, 前复权)
    "low": float,            # 最低价 (元, 前复权)
    "close": float,          # 收盘价 (元, 前复权)
    "volume": int,           # 成交量 (股)
    "amount": float,         # 成交额 (元, Sina/Tencent 为 0.0)
    "change_pct": float,     # 涨跌幅 (%)
    "turnover_rate": float | None,  # 换手率 (%, Sina/Tencent 为 None)
}
```

---

## 2. 数据源概览

### 2.1 六源能力矩阵

| 数据源 | 实时行情 | K线 (完整) | K线 (不完整) | 基本面 | 行业 | 搜索 |
|--------|---------|-----------|-------------|--------|------|------|
| **akshare** | 封装新浪 (同步, 慢) | `ak.stock_zh_a_daily` | — | — | `ak.stock_individual_info_em` | — |
| **tushare** | 不支持 | `pro.daily` + `pro.daily_basic` (需 token) | — | — | — | — |
| **baostock** | 不支持 | `bs.query_history_k_data_plus` (免费) | — | — | — | — |
| **eastmoney** | `push2.eastmoney.com` | `push2his.eastmoney.com` | — | — | — | `searchapi.eastmoney.com` |
| **sina** | `hq.sinajs.cn` (GB18030) | — | `money.finance.sina.com.cn` | — | — | — |
| **tencent** | `qt.gtimg.cn` (GBK, HTTP) | — | `web.ifzq.gtimg.cn` | `qt.gtimg.cn` (PE/PB/市值) | — | — |

- **完整K线源** (akshare, tushare, baostock, eastmoney): 提供 `amount` (成交额) + `turnover_rate` (换手率)
- **不完整K线源** (sina, tencent): 缺少 `amount` (设为 0) 和 `turnover_rate` (设为 None)

### 2.2 各数据类型 Fallback 链

| 数据类型 | Fallback 链 | 备注 |
|---------|------------|------|
| **实时行情** | akshare → ~~tushare~~ → ~~baostock~~ → eastmoney → sina → tencent | tushare/baostock 不支持实时行情; akshare 封装新浪 (同步, 慢). 实际有效: eastmoney → sina → tencent |
| **日K线** | Phase 1: akshare → tushare → baostock → eastmoney; Phase 2 (P1全部失败): sina → tencent | Phase 2 仅作最后手段 (数据不完整) |
| **基本面** | 腾讯 (直接 HTTP, 不走 aggregator) | 始终腾讯; 快速可靠, 无需 fallback |
| **行业** | 东方财富 via akshare (尽力而为) | 仅在字段为空时获取; 可能被限速 |
| **搜索** | 仅东方财富 | 其他源无搜索 API |

### 2.3 akshare

`backend/app/services/akshare_client.py`

**实时行情:** 调用 `ak.stock_zh_a_spot_em()` (底层封装东方财富), 返回全市场快照。由于是同步调用且数据量大, 在 aggregator 中排在较后位置。

**日K线:** 调用 `ak.stock_zh_a_daily(symbol, start_date, end_date, adjust="qfq")`，返回前复权日K。数据完整 (含 amount, turnover_rate)。

**行业信息:** 调用 `ak.stock_individual_info_em(symbol)` 获取个股行业分类 (来源东方财富)。

### 2.4 tushare

`backend/app/services/tushare_client.py`

**前提:** 需要在 `.env` 配置 `TUSHARE_TOKEN`。

**日K线:** 调用 `pro.daily()` + `pro.daily_basic()` 合并，提供完整的 OHLCV + 成交额 + 换手率。

**实时行情:** 不支持。

### 2.5 baostock

`backend/app/services/baostock_client.py`

**免费使用:** 无需 token，通过 `bs.login()` 匿名登录。

**日K线:** 调用 `bs.query_history_k_data_plus()`，提供完整的 OHLCV + 成交额 + 换手率。

**实时行情:** 不支持。

---

## 3. 新浪财经 (Sina)

### 3.1 实时行情

**URL:** `https://hq.sinajs.cn/list={symbol}`

**编码:** GB18030

**请求头:** 必须设置 `Referer: https://finance.sina.com.cn`

**Symbol 格式:** `sh600519` (上交所) / `sz000001` (深交所)

**响应格式:** JS 变量赋值语句

```
var hq_str_sh600519="贵州茅台,1523.00,1515.00,1530.50,1535.00,1518.00,1530.00,1530.50,2563100,3904568320.00,...,2024-06-14,15:00:00,...";
```

#### 字段位置映射 (逗号分隔, 0-indexed)

| 位置 | 字段名 | 说明 | 单位 | 转换为统一格式 |
|------|--------|------|------|---------------|
| `[0]` | name | 股票名称 | — | `→ name` |
| `[1]` | open | 开盘价 | 元 | `→ open` |
| `[2]` | prev_close | 昨收价 | 元 | `→ prev_close` |
| `[3]` | price | 最新价 | 元 | `→ price`, `→ close` |
| `[4]` | high | 最高价 | 元 | `→ high` |
| `[5]` | low | 最低价 | 元 | `→ low` |
| `[6]` | bid | 买一价 | 元 | (未使用) |
| `[7]` | ask | 卖一价 | 元 | (未使用) |
| `[8]` | volume | 成交量 | **股** | `→ volume` (无需转换) |
| `[9]` | amount | 成交额 | **元** | `→ amount` (无需转换) |
| `[10]`~`[19]` | 买盘五档 | 买1量/买1价/买2量/... | — | (未使用) |
| `[20]`~`[29]` | 卖盘五档 | 卖1量/卖1价/卖2量/... | — | (未使用) |
| `[30]` | date | 交易日 | YYYY-MM-DD | (参考) |
| `[31]` | time | 时间 | HH:mm:ss | (参考) |

**涨跌幅计算:** `change_pct = (price - prev_close) / prev_close * 100`

**不可用字段:** `turnover_rate` (换手率), 固定输出 `0.0`

### 3.2 日 K 线

**URL:** `https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData`

**编码:** UTF-8

**请求参数:**

| 参数 | 说明 | 示例 |
|------|------|------|
| `symbol` | 股票代码 | `sh600519` |
| `scale` | K 线周期 (分钟) | `240` = 日K |
| `ma` | 是否返回均线 | `no` |
| `datalen` | 返回条数 | `120` |

**响应格式:** JSON 数组

```json
[
  {
    "day": "2024-06-14",
    "open": "1523.000",
    "high": "1535.000",
    "low": "1518.000",
    "close": "1530.500",
    "volume": "3031859"
  }
]
```

#### 字段映射

| JSON 字段 | 说明 | 单位 | 转换为统一格式 |
|-----------|------|------|---------------|
| `day` | 交易日 | YYYY-MM-DD | `→ date` (解析为 date) |
| `open` | 开盘价 | 元 (字符串) | `→ open` (float) |
| `high` | 最高价 | 元 (字符串) | `→ high` (float) |
| `low` | 最低价 | 元 (字符串) | `→ low` (float) |
| `close` | 收盘价 | 元 (字符串) | `→ close` (float) |
| `volume` | 成交量 | **股** (字符串) | `→ volume` (int) |

**不可用字段:**
- `amount`: 成交额，输出 `0.0`
- `turnover_rate`: 换手率，输出 `None`
- `change_pct`: 从连续收盘价计算得出 (`(close - prev_close) / prev_close * 100`)

---

## 4. 腾讯财经 (Tencent)

### 4.1 实时行情

**URL:** `http://qt.gtimg.cn/q={symbol}` (**注意: HTTP, 非 HTTPS**)

**编码:** GBK

**Symbol 格式:** `sh600519` (上交所) / `sz000001` (深交所)

**响应格式:** JS 变量赋值，字段以 `~` 分隔

```
v_sh600519="1~贵州茅台~600519~1530.50~1515.00~1523.00~25631~...~2.35~...~1535.00~1518.00~...~390456.83~...~2.03~..."
```

#### 字段位置映射 (`~` 分隔, 0-indexed)

| 位置 | 字段名 | 说明 | 单位 | 转换为统一格式 |
|------|--------|------|------|---------------|
| `[0]` | market | 市场代码 | — | (未使用) `1`=SH |
| `[1]` | name | 股票名称 | — | `→ name` |
| `[2]` | code | 股票代码 | — | (校验用) |
| `[3]` | price | 最新价 | 元 | `→ price`, `→ close` |
| `[4]` | prev_close | 昨收价 | 元 | `→ prev_close` |
| `[5]` | open | 开盘价 | 元 | `→ open` |
| `[6]` | volume | 成交量 | **手** | `× 100 → volume (股)` |
| `[7]` | buy_volume | 外盘成交量 | 手 | (未使用) |
| `[8]` | sell_volume | 内盘成交量 | 手 | (未使用) |
| ... | ... | 买卖盘五档 | — | (未使用) |
| `[30]` | datetime | 日期时间 | YYYYMMDDHHmmss | (参考) |
| `[31]` | change_amount | 涨跌额 | 元 | (未使用) |
| `[32]` | change_pct | 涨跌幅 | **%** | `→ change_pct` (直接使用) |
| `[33]` | high | 最高价 | 元 | `→ high` |
| `[34]` | low | 最低价 | 元 | `→ low` |
| `[35]` | price/vol/amount | 最新价/成交量/成交额 | — | (未使用) |
| `[36]` | volume_lots | 成交量 | 万手 | (未使用) |
| `[37]` | amount | 成交额 | **万元** | `× 10000 → amount (元)` |
| `[38]` | turnover_rate | 换手率 | **%** | `→ turnover_rate` (直接使用) |

**与新浪对比:** 腾讯提供 `turnover_rate` 和 `change_pct` (新浪不提供), 但 `volume` 单位是手 (新浪是股), `amount` 单位是万元 (新浪是元)。

### 4.2 日 K 线

**URL:** `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get`

**编码:** UTF-8 (JSON)

**请求参数:**

| 参数 | 说明 | 示例 |
|------|------|------|
| `param` | 格式: `{symbol},day,,,{limit},qfq` | `sh600519,day,,,120,qfq` |

其中 `qfq` = 前复权 (前复权)。

**响应格式:** 嵌套 JSON

```json
{
  "code": 0,
  "data": {
    "sh600519": {
      "qfqday": [
        ["2024-06-14", "1523.00", "1530.50", "1535.00", "1518.00", "25631.00"],
        ["2024-06-13", "1515.00", "1522.50", "1528.00", "1510.00", "31200.00"]
      ]
    }
  }
}
```

#### 字段位置映射

| 数组位置 | 字段名 | 说明 | 单位 | 转换为统一格式 |
|----------|--------|------|------|---------------|
| `[0]` | date | 交易日 | YYYY-MM-DD | `→ date` (解析为 date) |
| `[1]` | open | 开盘价 | 元 (字符串) | `→ open` (float) |
| `[2]` | close | 收盘价 | 元 (字符串) | `→ close` (float) |
| `[3]` | high | 最高价 | 元 (字符串) | `→ high` (float) |
| `[4]` | low | 最低价 | 元 (字符串) | `→ low` (float) |
| `[5]` | volume | 成交量 | **手** (字符串) | `× 100 → volume (股)` |

**不可用字段:**
- `amount`: 成交额，输出 `0.0`
- `turnover_rate`: 换手率，输出 `None`
- `change_pct`: 从连续收盘价计算得出

**注意:** 响应中 K 线数据可能在 `qfqday` (前复权) 或 `day` (不复权) 字段下，代码会依次尝试。

---

## 5. 各源对比

### 5.1 实时行情字段完整度

| 字段 | akshare | tushare | baostock | 东方财富 | 新浪 | 腾讯 |
|------|---------|---------|----------|---------|------|------|
| price (最新价) | via Sina/EM | — | — | `f43/10^f59` | `fields[3]` | `fields[3]` |
| open (开盘价) | via Sina/EM | — | — | `f46/10^f59` | `fields[1]` | `fields[5]` |
| high (最高价) | via Sina/EM | — | — | `f44/10^f59` | `fields[4]` | `fields[33]` |
| low (最低价) | via Sina/EM | — | — | `f45/10^f59` | `fields[5]` | `fields[34]` |
| volume (成交量) | via Sina/EM | — | — | `f47 × 100` (手→股) | `fields[8]` (股) | `fields[6] × 100` (手→股) |
| amount (成交额) | via Sina/EM | — | — | `f48` (元) | `fields[9]` (元) | `fields[37] × 10000` (万元→元) |
| change_pct (涨跌幅) | via Sina/EM | — | — | `f170 / 100` (%) | **计算得出** | `fields[32]` (%) |
| turnover_rate (换手率) | via Sina/EM | — | — | `f168 / 100` (%) | **不可用** (=0) | `fields[38]` (%) |

### 5.2 日 K 线字段完整度

| 字段 | akshare | tushare | baostock | 东方财富 | 新浪 | 腾讯 |
|------|---------|---------|----------|---------|------|------|
| date | ✓ | ✓ | ✓ | `klines[i][0]` | `item["day"]` | `row[0]` |
| OHLC | ✓ | ✓ | ✓ | `klines[i][1-4]` | `item["open/high/low/close"]` | `row[1-4]` |
| volume | ✓ (股) | ✓ (股) | ✓ (股) | `klines[i][5]` (股) | `item["volume"]` (股) | `row[5] × 100` (手→股) |
| amount | ✓ (元) | ✓ (元) | ✓ (元) | `klines[i][6]` (元) | **不可用** (=0) | **不可用** (=0) |
| change_pct | ✓ (%) | ✓ (%) | ✓ (%) | `klines[i][8]` (%) | **计算得出** | **计算得出** |
| turnover_rate | ✓ (%) | ✓ (%) | ✓ (%) | `klines[i][10]` (%) | **不可用** (=None) | **不可用** (=None) |

### 5.3 功能支持

| 功能 | akshare | tushare | baostock | 东方财富 | 新浪 | 腾讯 |
|------|---------|---------|----------|---------|------|------|
| 实时行情 | 封装新浪 (慢) | ✗ | ✗ | ✓ | ✓ | ✓ |
| 日 K 线 | ✓ (完整) | ✓ (完整, 需token) | ✓ (完整, 免费) | ✓ (完整) | ✓ (缺 amount/turnover) | ✓ (缺 amount/turnover) |
| 基本面 (PE/PB/市值) | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ |
| 行业信息 | ✓ (via EM) | ✗ | ✗ | ✗ | ✗ | ✗ |
| 股票搜索 | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| 编码 | UTF-8 | UTF-8 | UTF-8 | UTF-8 | GB18030 | GBK |
| 协议 | HTTPS | HTTPS | HTTPS | HTTPS | HTTPS | **HTTP** (实时) / HTTPS (K线) |
| 认证 | 无 | 需 Token | 匿名登录 | 无 | 需 Referer | 无 |

---

## 6. 已知限制

### 6.1 akshare

- `ak.stock_zh_a_spot_em()` 是同步调用，获取全市场数据较慢，不适合高频实时行情
- `ak.stock_zh_a_daily()` 底层调用东方财富，可能受到相同的 IP/限速限制
- 行业数据获取 (`ak.stock_individual_info_em`) 可能被限速

### 6.2 tushare

- 需要注册并获取 `TUSHARE_TOKEN`
- 不提供实时行情接口
- 免费 token 有每日调用次数限制

### 6.3 baostock

- 需要先 `bs.login()` 建立会话 (匿名免费)
- 不提供实时行情接口
- 数据更新可能有延迟 (一般 T+1)

### 6.4 东方财富

- `push2.eastmoney.com` 和 `push2his.eastmoney.com` 可能对部分 IP/地区进行访问限制
- 价格字段返回原始整数，需配合 `f59` (小数精度) 转换
- `ut` 和 `token` 是硬编码公开令牌，理论上长期稳定但可能变更

### 6.5 新浪财经

- 必须设置 `Referer: https://finance.sina.com.cn`，否则被拦截
- 实时行情返回 GB18030 编码，需手动解码
- K 线 `scale=240` 表示日K (240分钟)，不是标准的 "day" 参数
- 不提供成交额、换手率字段
- 涨跌幅通过前后收盘价差计算，首条记录为 0.0

### 6.6 腾讯财经

- 实时行情使用 **HTTP** (非 HTTPS)，`qt.gtimg.cn` 不支持 HTTPS
- 实时行情返回 GBK 编码，需手动解码
- 成交量单位为「手」(需 ×100 转「股」)，成交额单位为「万元」(需 ×10000 转「元」)
- K 线数据前复权结果在 `qfqday` 或 `day` 字段下，位置不固定
- K 线不提供成交额、换手率字段

### 6.7 Fallback 策略限制

- **K 线两阶段 fallback:** Phase 1 (akshare → tushare → baostock → eastmoney) 提供完整数据; Phase 2 (sina → tencent) 仅在 Phase 1 全部失败时使用，缺少 `amount` 和 `turnover_rate`。
- **搜索仅从东方财富获取:** 其他五个数据源不提供股票搜索 API。
- **基本面仅从腾讯获取:** PE/PB/市值数据直接通过 Tencent HTTP 获取，不走 aggregator。
- **行业信息仅从东方财富获取:** 通过 akshare 封装调用，可能被限速。
- **实时行情有效 fallback 顺序:** tushare 和 baostock 不支持实时行情，akshare 封装新浪 (同步慢)。实际有效链: eastmoney → sina → tencent。
