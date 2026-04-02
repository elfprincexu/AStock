"""
东方财富 API 连通性与数据格式测试。

这些测试直接调用东方财富公开接口，验证:
  - 接口可达、返回 200
  - 返回数据结构符合预期
  - 关键字段存在且类型正确

依赖: 网络连接 (不需要本地服务)

注意: push2.eastmoney.com 和 push2his.eastmoney.com 有 IP/地区限制，
在某些网络环境下会直接关闭连接。相关测试会自动 skip。
"""
import time
import threading

import requests
import pytest

from conftest import (
    requires_network,
    TEST_STOCK_CODE,
    TEST_STOCK_MARKET,
)

# ── Constants ─────────────────────────────────────────────────────────────────

UT_TOKEN = "fa5fd1943c7b386f172d6893dbbd1177"
SEARCH_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"

REALTIME_URL = "https://push2.eastmoney.com/api/qt/stock/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_DELAY = 3.0
MIN_REQUEST_INTERVAL = 1.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

# Module-level response cache and rate limiter
_response_cache: dict[str, dict] = {}
_last_request_time: float = 0.0
_lock = threading.Lock()


def _get(url: str, params: dict) -> dict:
    """GET with browser headers, retry, rate-limiting, and caching."""
    cache_key = f"{url}?{'&'.join(f'{k}={v}' for k,v in sorted(params.items()))}"
    if cache_key in _response_cache:
        return _response_cache[cache_key]

    global _last_request_time
    with _lock:
        elapsed = time.monotonic() - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    session = requests.Session()
    session.headers.update(_HEADERS)

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
            _response_cache[cache_key] = body
            with _lock:
                _last_request_time = time.monotonic()
            return body
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.HTTPError,
        ) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def _probe_push2() -> bool:
    """Test if push2.eastmoney.com reliably returns data (2 consecutive OK)."""
    import time as _time
    for _ in range(2):
        try:
            resp = requests.get(
                REALTIME_URL,
                params={"secid": "1.600519", "fields": "f57", "ut": UT_TOKEN},
                headers=_HEADERS,
                timeout=10,
            )
            if resp.status_code != 200:
                return False
            _time.sleep(1)
        except Exception:
            return False
    return True


def _probe_push2his() -> bool:
    """Test if push2his.eastmoney.com reliably returns data (2 consecutive OK)."""
    import time as _time
    for _ in range(2):
        try:
            resp = requests.get(
                KLINE_URL,
                params={
                    "secid": "1.600519", "fields1": "f1", "fields2": "f51",
                    "klt": "101", "fqt": "1", "beg": "0", "end": "20500101",
                    "lmt": "1", "ut": UT_TOKEN,
                },
                headers=_HEADERS,
                timeout=10,
            )
            if resp.status_code != 200:
                return False
            _time.sleep(1)
        except Exception:
            return False
    return True


# Probe once at module load to decide skip markers
_push2_ok = _probe_push2()
_push2his_ok = _probe_push2his()

requires_push2 = pytest.mark.skipif(
    not _push2_ok,
    reason="push2.eastmoney.com not returning data (IP/network restriction)",
)
requires_push2his = pytest.mark.skipif(
    not _push2his_ok,
    reason="push2his.eastmoney.com not returning data (IP/network restriction)",
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 实时行情接口
# ═══════════════════════════════════════════════════════════════════════════════

@requires_network
@requires_push2
class TestRealtimeAPI:
    """测试 push2.eastmoney.com 实时行情接口。"""

    def _fetch(self, secid: str) -> dict:
        return _get(REALTIME_URL, {
            "secid": secid,
            "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f168,f170",
            "ut": UT_TOKEN,
        })

    def test_realtime_returns_200(self):
        """接口应返回 HTTP 200。"""
        body = self._fetch(f"1.{TEST_STOCK_CODE}")
        assert "data" in body, f"响应缺少 'data' 字段: {list(body.keys())}"

    def test_realtime_data_has_required_fields(self):
        """返回数据应包含所有必要字段。"""
        body = self._fetch(f"1.{TEST_STOCK_CODE}")
        data = body["data"]
        required = ["f43", "f44", "f45", "f46", "f47", "f48", "f57", "f58", "f59"]
        missing = [f for f in required if f not in data]
        assert not missing, f"实时行情缺少字段: {missing}"

    def test_realtime_stock_code_matches(self):
        """f57 应返回请求的股票代码。"""
        body = self._fetch(f"1.{TEST_STOCK_CODE}")
        assert str(body["data"]["f57"]) == TEST_STOCK_CODE

    def test_realtime_precision_field(self):
        """f59 (小数精度) 应为整数, 通常 A 股为 2。"""
        body = self._fetch(f"1.{TEST_STOCK_CODE}")
        f59 = body["data"]["f59"]
        assert isinstance(f59, int), f"f59 类型应为 int, 实际: {type(f59)}"
        assert f59 >= 0, f"f59 应 >= 0, 实际: {f59}"

    def test_realtime_price_is_positive_integer(self):
        """价格原始值应为正整数 (需除以 10^f59)。"""
        body = self._fetch(f"1.{TEST_STOCK_CODE}")
        data = body["data"]
        f43 = data["f43"]
        assert isinstance(f43, (int, float)), f"f43 类型异常: {type(f43)}"
        assert f43 > 0, f"f43 应 > 0, 实际: {f43}"

    def test_realtime_sz_market(self):
        """深交所股票 (secid=0.xxx) 也应正常返回。"""
        body = self._fetch("0.000001")  # 平安银行
        assert body.get("data") is not None, "深交所股票查询返回空 data"
        assert str(body["data"]["f57"]) == "000001"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 日 K 线接口
# ═══════════════════════════════════════════════════════════════════════════════

@requires_network
@requires_push2his
class TestKlineAPI:
    """测试 push2his.eastmoney.com 日 K 线接口。"""

    def _fetch_klines(self, secid: str, limit: int = 5) -> dict:
        return _get(KLINE_URL, {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": "0",
            "end": "20500101",
            "lmt": str(limit),
            "ut": UT_TOKEN,
        })

    def test_kline_returns_200_with_data(self):
        """K 线接口应返回 200 且包含 data。"""
        body = self._fetch_klines(f"1.{TEST_STOCK_CODE}")
        assert "data" in body
        assert body["data"] is not None

    def test_kline_has_klines_array(self):
        """data.klines 应为非空数组。"""
        body = self._fetch_klines(f"1.{TEST_STOCK_CODE}")
        klines = body["data"].get("klines", [])
        assert isinstance(klines, list), f"klines 类型异常: {type(klines)}"
        assert len(klines) > 0, "K 线数据为空"

    def test_kline_record_has_11_fields(self):
        """每条 K 线记录应包含 11 个逗号分隔的字段。"""
        body = self._fetch_klines(f"1.{TEST_STOCK_CODE}")
        for line in body["data"]["klines"]:
            fields = line.split(",")
            assert len(fields) == 11, f"K 线字段数应为 11, 实际 {len(fields)}: {line}"

    def test_kline_date_format(self):
        """K 线日期字段应为 YYYY-MM-DD 格式。"""
        from datetime import datetime
        body = self._fetch_klines(f"1.{TEST_STOCK_CODE}")
        for line in body["data"]["klines"]:
            date_str = line.split(",")[0]
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            assert dt.year >= 2000, f"日期异常: {date_str}"

    def test_kline_ohlcv_are_numeric(self):
        """OHLCV 字段应可转为浮点数。"""
        body = self._fetch_klines(f"1.{TEST_STOCK_CODE}")
        for line in body["data"]["klines"]:
            parts = line.split(",")
            open_ = float(parts[1])
            close = float(parts[2])
            high = float(parts[3])
            low = float(parts[4])
            volume = float(parts[5])
            assert high >= low, f"最高价 {high} < 最低价 {low}"
            assert high >= open_, f"最高价 {high} < 开盘价 {open_}"
            assert high >= close, f"最高价 {high} < 收盘价 {close}"
            assert volume >= 0, f"成交量为负: {volume}"

    def test_kline_respects_limit(self):
        """lmt 参数应限制返回条数。"""
        body = self._fetch_klines(f"1.{TEST_STOCK_CODE}")
        klines = body["data"]["klines"]
        assert len(klines) <= 5, f"请求 5 条, 实际返回 {len(klines)} 条"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 股票搜索接口
# ═══════════════════════════════════════════════════════════════════════════════

@requires_network
class TestSearchAPI:
    """测试 searchapi.eastmoney.com 搜索接口。"""

    def _search(self, keyword: str) -> dict:
        return _get(SEARCH_URL, {
            "input": keyword,
            "type": "14",
            "token": SEARCH_TOKEN,
            "count": "5",
        })

    def test_search_by_code(self):
        """按代码搜索应返回结果。"""
        body = self._search(TEST_STOCK_CODE)
        table = body.get("QuotationCodeTable", {})
        data = table.get("Data", [])
        assert len(data) > 0, f"搜索 '{TEST_STOCK_CODE}' 无结果"

    def test_search_by_name(self):
        """按名称搜索应返回结果。"""
        body = self._search("茅台")
        table = body.get("QuotationCodeTable", {})
        data = table.get("Data", [])
        assert len(data) > 0, "搜索 '茅台' 无结果"

    def test_search_result_has_required_fields(self):
        """搜索结果应包含 Code, Name, MktNum。"""
        body = self._search(TEST_STOCK_CODE)
        items = body["QuotationCodeTable"]["Data"]
        for item in items:
            assert "Code" in item, f"搜索结果缺少 Code: {item}"
            assert "Name" in item, f"搜索结果缺少 Name: {item}"
            assert "MktNum" in item, f"搜索结果缺少 MktNum: {item}"

    def test_search_mktnum_values(self):
        """MktNum 应为 '0' (SZ) 或 '1' (SH) 等。"""
        body = self._search(TEST_STOCK_CODE)
        items = body["QuotationCodeTable"]["Data"]
        for item in items:
            mkt = str(item["MktNum"])
            assert mkt in ("0", "1", "2"), f"未知的 MktNum: {mkt}"

    def test_search_nonexistent_returns_empty(self):
        """搜索不存在的股票应返回空结果或 None。"""
        body = self._search("ZZZZZZ999999")
        table = body.get("QuotationCodeTable", {})
        data = table.get("Data")
        assert data is None or len(data) == 0
