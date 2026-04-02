"""
新浪财经 API 连通性与数据格式测试。

这些测试直接调用新浪财经公开接口，验证:
  - 接口可达、返回 200
  - 返回数据结构符合预期（统一格式）
  - 关键字段存在且类型正确

依赖: 网络连接 (不需要本地服务)
"""
import time
import requests
import pytest

from conftest import requires_network, TEST_STOCK_CODE, TEST_STOCK_MARKET

# ── Constants ─────────────────────────────────────────────────────────────────

_REALTIME_URL_TPL = "https://hq.sinajs.cn/list={symbol}"
_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/CN_MarketData.getKLineData"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn",
}

REQUEST_TIMEOUT = 15

# ── Probe ─────────────────────────────────────────────────────────────────────


def _probe_sina() -> bool:
    """Check if hq.sinajs.cn is reachable and returning data."""
    try:
        resp = requests.get(
            _REALTIME_URL_TPL.format(symbol=f"sh{TEST_STOCK_CODE}"),
            headers=_HEADERS,
            timeout=10,
        )
        return resp.status_code == 200 and len(resp.content) > 20
    except Exception:
        return False


_sina_ok = _probe_sina()
requires_sina = pytest.mark.skipif(
    not _sina_ok,
    reason="hq.sinajs.cn not returning data",
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 实时行情接口
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
@requires_sina
class TestSinaRealtimeAPI:
    """测试 hq.sinajs.cn 实时行情接口。"""

    def _fetch_realtime(self, code: str, market: str) -> str:
        """Fetch raw realtime string from Sina."""
        symbol = f"{market.lower()}{code}"
        resp = requests.get(
            _REALTIME_URL_TPL.format(symbol=symbol),
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.content.decode("gb18030", errors="replace")

    def _parse_fields(self, raw: str) -> list[str]:
        """Extract comma-separated fields from the JS var assignment."""
        start = raw.index('"') + 1
        end = raw.rindex('"')
        return raw[start:end].split(",")

    def test_realtime_returns_200_with_data(self):
        """接口应返回 HTTP 200 且有数据。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        assert "hq_str_" in raw, f"响应格式异常: {raw[:100]}"

    def test_realtime_has_enough_fields(self):
        """应返回至少 32 个逗号分隔字段。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        assert len(fields) >= 32, f"字段数不足: {len(fields)}"

    def test_realtime_name_not_empty(self):
        """field[0] 应为股票名称（非空）。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        assert len(fields[0]) > 0, "名称为空"

    def test_realtime_price_positive(self):
        """field[3] (当前价) 应为正数。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        price = float(fields[3])
        assert price > 0, f"价格应 > 0, 实际: {price}"

    def test_realtime_ohlc_numeric(self):
        """open/high/low/prev_close 应可转为浮点数。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        open_ = float(fields[1])
        prev_close = float(fields[2])
        high = float(fields[4])
        low = float(fields[5])
        assert open_ > 0, f"开盘价异常: {open_}"
        assert prev_close > 0, f"昨收异常: {prev_close}"
        assert high >= low, f"最高 {high} < 最低 {low}"

    def test_realtime_volume_and_amount(self):
        """field[8] (成交量/股) 和 field[9] (成交额/元) 应为非负数。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        volume = float(fields[8])
        amount = float(fields[9])
        assert volume >= 0, f"成交量为负: {volume}"
        assert amount >= 0, f"成交额为负: {amount}"

    def test_realtime_sz_market(self):
        """深交所股票也应正常返回。"""
        raw = self._fetch_realtime("000001", "SZ")
        fields = self._parse_fields(raw)
        assert len(fields) >= 32, "深交所字段数不足"
        assert float(fields[3]) > 0, "深交所价格异常"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 日 K 线接口
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
@requires_sina
class TestSinaKlineAPI:
    """测试 Sina 日 K 线接口。"""

    def _fetch_klines(self, code: str, market: str, limit: int = 5) -> list:
        symbol = f"{market.lower()}{code}"
        resp = requests.get(
            _KLINE_URL,
            params={
                "symbol": symbol,
                "scale": "240",
                "ma": "no",
                "datalen": str(limit),
            },
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def test_kline_returns_list(self):
        """K 线接口应返回 JSON 数组。"""
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        assert isinstance(data, list), f"应为 list, 实际: {type(data)}"
        assert len(data) > 0, "K 线数据为空"

    def test_kline_has_required_fields(self):
        """每条 K 线应包含 day, open, high, low, close, volume。"""
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        required = {"day", "open", "high", "low", "close", "volume"}
        for item in data:
            missing = required - set(item.keys())
            assert not missing, f"缺少字段: {missing}, 实际: {item.keys()}"

    def test_kline_date_format(self):
        """日期应为 YYYY-MM-DD 格式。"""
        from datetime import datetime
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        for item in data:
            dt = datetime.strptime(item["day"], "%Y-%m-%d")
            assert dt.year >= 2000, f"日期异常: {item['day']}"

    def test_kline_ohlcv_numeric(self):
        """OHLCV 应可转为浮点数且 high >= low。"""
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        for item in data:
            o, h, l, c = float(item["open"]), float(item["high"]), float(item["low"]), float(item["close"])
            v = float(item["volume"])
            assert h >= l, f"最高 {h} < 最低 {l}"
            assert v >= 0, f"成交量为负: {v}"

    def test_kline_respects_limit(self):
        """datalen 参数应限制返回条数。"""
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET, limit=3)
        assert len(data) <= 3, f"请求 3 条, 实际 {len(data)} 条"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SinaClient 统一格式测试
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
@requires_sina
class TestSinaClientUnified:
    """测试 SinaClient 输出符合统一格式。"""

    @pytest.mark.asyncio
    async def test_realtime_unified_format(self):
        """SinaClient.get_realtime_quote 应返回统一格式 dict。"""
        from app.services.sina import SinaClient
        client = SinaClient(timeout=15)
        try:
            quote = await client.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
            # Check all required unified keys
            required_keys = {
                "code", "name", "price", "open", "high", "low", "close",
                "prev_close", "volume", "amount", "change_pct",
                "turnover_rate", "timestamp",
            }
            missing = required_keys - set(quote.keys())
            assert not missing, f"统一格式缺少字段: {missing}"
            assert quote["price"] > 0
            assert quote["code"] == TEST_STOCK_CODE
            assert isinstance(quote["volume"], int)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_kline_unified_format(self):
        """SinaClient.get_daily_klines 应返回统一格式 list[dict]。"""
        from app.services.sina import SinaClient
        client = SinaClient(timeout=15)
        try:
            klines = await client.get_daily_klines(
                TEST_STOCK_CODE, TEST_STOCK_MARKET, limit=5
            )
            assert len(klines) > 0
            required_keys = {
                "date", "open", "high", "low", "close",
                "volume", "amount", "change_pct", "turnover_rate",
            }
            for k in klines:
                missing = required_keys - set(k.keys())
                assert not missing, f"统一格式缺少字段: {missing}"
                from datetime import date
                assert isinstance(k["date"], date)
                assert k["high"] >= k["low"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_search_raises(self):
        """SinaClient.search_stock 应抛出 DataSourceError。"""
        from app.services.sina import SinaClient, SinaError
        client = SinaClient()
        try:
            with pytest.raises(SinaError):
                await client.search_stock("茅台")
        finally:
            await client.close()
