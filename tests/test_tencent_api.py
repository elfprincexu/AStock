"""
腾讯财经 API 连通性与数据格式测试。

这些测试直接调用腾讯财经公开接口，验证:
  - 接口可达、返回 200
  - 返回数据结构符合预期（统一格式）
  - 关键字段存在且类型正确

依赖: 网络连接 (不需要本地服务)
"""
import requests
import pytest

from conftest import requires_network, TEST_STOCK_CODE, TEST_STOCK_MARKET

# ── Constants ─────────────────────────────────────────────────────────────────

_REALTIME_URL_TPL = "http://qt.gtimg.cn/q={symbol}"
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}

REQUEST_TIMEOUT = 15

# ── Probe ─────────────────────────────────────────────────────────────────────


def _probe_tencent() -> bool:
    """Check if qt.gtimg.cn is reachable and returning data."""
    try:
        resp = requests.get(
            _REALTIME_URL_TPL.format(symbol=f"sh{TEST_STOCK_CODE}"),
            headers=_HEADERS,
            timeout=10,
        )
        return resp.status_code == 200 and len(resp.content) > 20
    except Exception:
        return False


_tencent_ok = _probe_tencent()
requires_tencent = pytest.mark.skipif(
    not _tencent_ok,
    reason="qt.gtimg.cn not returning data",
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 实时行情接口
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
@requires_tencent
class TestTencentRealtimeAPI:
    """测试 qt.gtimg.cn 实时行情接口。"""

    def _fetch_realtime(self, code: str, market: str) -> str:
        """Fetch raw realtime string from Tencent."""
        symbol = f"{market.lower()}{code}"
        resp = requests.get(
            _REALTIME_URL_TPL.format(symbol=symbol),
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.content.decode("gbk", errors="replace")

    def _parse_fields(self, raw: str) -> list[str]:
        """Extract tilde-separated fields from the JS var assignment."""
        start = raw.index('"') + 1
        end = raw.rindex('"')
        return raw[start:end].split("~")

    def test_realtime_returns_200_with_data(self):
        """接口应返回 HTTP 200 且有数据。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        assert "v_sh" in raw or "v_sz" in raw, f"响应格式异常: {raw[:100]}"

    def test_realtime_has_enough_fields(self):
        """应返回至少 50 个 ~ 分隔字段。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        assert len(fields) >= 50, f"字段数不足: {len(fields)}"

    def test_realtime_name_and_code(self):
        """field[1] = 名称, field[2] = 代码。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        assert len(fields[1]) > 0, "名称为空"
        assert fields[2] == TEST_STOCK_CODE, f"代码不匹配: {fields[2]}"

    def test_realtime_price_positive(self):
        """field[3] (当前价) 应为正数。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        price = float(fields[3])
        assert price > 0, f"价格应 > 0, 实际: {price}"

    def test_realtime_ohlc_fields(self):
        """open/high/low/prev_close 应可转为浮点数。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        prev_close = float(fields[4])
        open_ = float(fields[5])
        high = float(fields[33])
        low = float(fields[34])
        assert open_ > 0, f"开盘价异常: {open_}"
        assert prev_close > 0, f"昨收异常: {prev_close}"
        assert high >= low, f"最高 {high} < 最低 {low}"

    def test_realtime_volume_and_amount(self):
        """field[6] (成交量/手) 和 field[37] (成交额/万元) 应为非负数。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        volume_lots = float(fields[6])
        amount_wan = float(fields[37])
        assert volume_lots >= 0, f"成交量为负: {volume_lots}"
        assert amount_wan >= 0, f"成交额为负: {amount_wan}"

    def test_realtime_change_pct(self):
        """field[32] (涨跌幅 %) 应可转为浮点数。"""
        raw = self._fetch_realtime(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        fields = self._parse_fields(raw)
        change_pct = float(fields[32])
        assert -20 <= change_pct <= 20, f"涨跌幅异常: {change_pct}%"

    def test_realtime_sz_market(self):
        """深交所股票也应正常返回。"""
        raw = self._fetch_realtime("000001", "SZ")
        fields = self._parse_fields(raw)
        assert len(fields) >= 50, "深交所字段数不足"
        assert float(fields[3]) > 0, "深交所价格异常"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 日 K 线接口
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
@requires_tencent
class TestTencentKlineAPI:
    """测试腾讯日 K 线接口。"""

    def _fetch_klines(self, code: str, market: str, limit: int = 5) -> list:
        symbol = f"{market.lower()}{code}"
        resp = requests.get(
            _KLINE_URL,
            params={"param": f"{symbol},day,,,{limit},qfq"},
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", {})
        stock_data = data.get(symbol, {})
        return stock_data.get("qfqday") or stock_data.get("day") or []

    def test_kline_returns_list(self):
        """K 线接口应返回数组。"""
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        assert isinstance(data, list), f"应为 list, 实际: {type(data)}"
        assert len(data) > 0, "K 线数据为空"

    def test_kline_row_has_6_elements(self):
        """每条 K 线应有至少 6 个元素 [date, open, close, high, low, vol]。"""
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        for row in data:
            assert len(row) >= 6, f"元素数不足: {len(row)}, 行: {row}"

    def test_kline_date_format(self):
        """日期应为 YYYY-MM-DD 格式。"""
        from datetime import datetime
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        for row in data:
            dt = datetime.strptime(row[0], "%Y-%m-%d")
            assert dt.year >= 2000, f"日期异常: {row[0]}"

    def test_kline_ohlcv_numeric(self):
        """OHLCV 应可转为浮点数且 high >= low。"""
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        for row in data:
            o, c, h, l = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            v = float(row[5])
            assert h >= l, f"最高 {h} < 最低 {l}"
            assert v >= 0, f"成交量为负: {v}"

    def test_kline_respects_limit(self):
        """limit 参数应限制返回条数。"""
        data = self._fetch_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET, limit=3)
        assert len(data) <= 5, f"请求 3 条, 实际 {len(data)} 条 (Tencent may return a few more)"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TencentClient 统一格式测试
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
@requires_tencent
class TestTencentClientUnified:
    """测试 TencentClient 输出符合统一格式。"""

    @pytest.mark.asyncio
    async def test_realtime_unified_format(self):
        """TencentClient.get_realtime_quote 应返回统一格式 dict。"""
        from app.services.tencent import TencentClient
        client = TencentClient(timeout=15)
        try:
            quote = await client.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
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
        """TencentClient.get_daily_klines 应返回统一格式 list[dict]。"""
        from app.services.tencent import TencentClient
        client = TencentClient(timeout=15)
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
        """TencentClient.search_stock 应抛出 DataSourceError。"""
        from app.services.tencent import TencentClient, TencentError
        client = TencentClient()
        try:
            with pytest.raises(TencentError):
                await client.search_stock("茅台")
        finally:
            await client.close()
