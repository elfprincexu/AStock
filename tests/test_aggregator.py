"""
StockDataAggregator 聚合与 fallback 测试。

测试:
  - 多数据源 fallback 逻辑（EastMoney 失败 → Sina → Tencent）
  - 统一输出格式一致性（不同数据源返回相同 key 结构）
  - last_source 属性正确记录数据来源
  - 搜索始终由 EastMoney 处理
  - Kline 只使用完整数据源

依赖: 网络连接
"""
import pytest
from datetime import datetime, date

from conftest import requires_network, TEST_STOCK_CODE, TEST_STOCK_MARKET


# ── Unified format keys ───────────────────────────────────────────────────────

REALTIME_REQUIRED_KEYS = {
    "code", "name", "price", "open", "high", "low", "close",
    "prev_close", "volume", "amount", "change_pct",
    "turnover_rate", "timestamp",
}

KLINE_REQUIRED_KEYS = {
    "date", "open", "high", "low", "close",
    "volume", "amount", "change_pct", "turnover_rate",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Fallback 测试 (async)
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
class TestAggregatorFallback:
    """测试 Aggregator fallback 逻辑。"""

    @pytest.mark.asyncio
    async def test_realtime_returns_data(self):
        """聚合器应从至少一个数据源获取到实时行情。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="eastmoney,sina,tencent")
        try:
            quote = await agg.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
            assert quote is not None
            assert quote["price"] > 0
            assert agg.last_source in ("eastmoney", "sina", "tencent")
        finally:
            await agg.close()

    @pytest.mark.asyncio
    async def test_realtime_unified_format(self):
        """聚合器返回的实时行情应包含所有统一字段。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="eastmoney,sina,tencent")
        try:
            quote = await agg.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
            missing = REALTIME_REQUIRED_KEYS - set(quote.keys())
            assert not missing, f"统一格式缺少字段: {missing}"
        finally:
            await agg.close()

    @pytest.mark.asyncio
    async def test_realtime_last_source_set(self):
        """last_source 应在成功后被设置。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="eastmoney,sina,tencent")
        try:
            await agg.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
            assert agg.last_source != ""
        finally:
            await agg.close()

    @pytest.mark.asyncio
    async def test_sina_only_realtime(self):
        """仅使用 Sina 时应能获取实时行情。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="sina")
        try:
            quote = await agg.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
            assert quote["price"] > 0
            assert agg.last_source == "sina"
        finally:
            await agg.close()

    @pytest.mark.asyncio
    async def test_tencent_only_realtime(self):
        """仅使用 Tencent 时应能获取实时行情。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="tencent")
        try:
            quote = await agg.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
            assert quote["price"] > 0
            assert agg.last_source == "tencent"
        finally:
            await agg.close()

    @pytest.mark.asyncio
    async def test_fallback_skips_failed_source(self):
        """当 EastMoney 不可用时，应 fallback 到 Sina 或 Tencent。

        由于 push2.eastmoney.com 在此环境被封，此测试验证 fallback 生效。
        """
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="eastmoney,sina,tencent")
        try:
            quote = await agg.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
            # Either eastmoney worked or fallback kicked in
            assert quote["price"] > 0
            assert agg.last_source in ("eastmoney", "sina", "tencent")
        finally:
            await agg.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Kline 限制测试
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
class TestAggregatorKlines:
    """测试 Aggregator kline 只使用完整数据源。"""

    @pytest.mark.asyncio
    async def test_kline_sina_only_returns_empty(self):
        """Sina-only 聚合器 kline 应返回空列表（数据不完整）。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="sina")
        try:
            klines = await agg.get_daily_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET, limit=5)
            assert klines == [], "Sina klines should be skipped (incomplete)"
        finally:
            await agg.close()

    @pytest.mark.asyncio
    async def test_kline_tencent_only_returns_empty(self):
        """Tencent-only 聚合器 kline 应返回空列表（数据不完整）。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="tencent")
        try:
            klines = await agg.get_daily_klines(TEST_STOCK_CODE, TEST_STOCK_MARKET, limit=5)
            assert klines == [], "Tencent klines should be skipped (incomplete)"
        finally:
            await agg.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Search 测试
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
class TestAggregatorSearch:
    """测试 Aggregator search 始终由 EastMoney 处理。"""

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        """搜索应从 EastMoney 返回结果。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="eastmoney,sina,tencent")
        try:
            results = await agg.search_stock("茅台")
            assert len(results) > 0
            codes = [r["code"] for r in results]
            assert TEST_STOCK_CODE in codes
        finally:
            await agg.close()

    @pytest.mark.asyncio
    async def test_search_sina_only_fails(self):
        """Sina-only 聚合器 search 应失败（Sina 不支持搜索）。"""
        from app.services.aggregator import StockDataAggregator
        from app.services.base import DataSourceError
        agg = StockDataAggregator(priority="sina")
        try:
            with pytest.raises(DataSourceError):
                await agg.search_stock("茅台")
        finally:
            await agg.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 数据一致性测试 — 不同数据源输出格式统一
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
class TestCrossSourceConsistency:
    """验证不同数据源返回的统一格式一致性。"""

    @pytest.mark.asyncio
    async def test_realtime_format_consistency(self):
        """Sina 和 Tencent 实时行情应有相同的 key 结构。"""
        from app.services.sina import SinaClient
        from app.services.tencent import TencentClient

        sina = SinaClient(timeout=15)
        tencent = TencentClient(timeout=15)

        try:
            sq = await sina.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
            tq = await tencent.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)

            # Same key set
            assert set(sq.keys()) == set(tq.keys()), (
                f"Key 不一致: Sina={sorted(sq.keys())}, "
                f"Tencent={sorted(tq.keys())}"
            )

            # Both have same code
            assert sq["code"] == tq["code"] == TEST_STOCK_CODE

            # Prices should be in the same ballpark (within 5%)
            price_diff = abs(sq["price"] - tq["price"])
            avg_price = (sq["price"] + tq["price"]) / 2
            assert price_diff / avg_price < 0.05, (
                f"价格差异过大: Sina={sq['price']}, Tencent={tq['price']}"
            )
        finally:
            await sina.close()
            await tencent.close()

    @pytest.mark.asyncio
    async def test_realtime_types_consistent(self):
        """两个数据源的字段值类型应一致。"""
        from app.services.sina import SinaClient
        from app.services.tencent import TencentClient

        sina = SinaClient(timeout=15)
        tencent = TencentClient(timeout=15)

        try:
            sq = await sina.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)
            tq = await tencent.get_realtime_quote(TEST_STOCK_CODE, TEST_STOCK_MARKET)

            # Numeric fields should be the same type
            for key in ("price", "open", "high", "low", "close", "prev_close",
                        "amount", "change_pct", "turnover_rate"):
                assert isinstance(sq[key], (int, float)), f"Sina {key} type: {type(sq[key])}"
                assert isinstance(tq[key], (int, float)), f"Tencent {key} type: {type(tq[key])}"

            assert isinstance(sq["volume"], int), f"Sina volume type: {type(sq['volume'])}"
            assert isinstance(tq["volume"], int), f"Tencent volume type: {type(tq['volume'])}"
            assert isinstance(sq["timestamp"], datetime)
            assert isinstance(tq["timestamp"], datetime)
        finally:
            await sina.close()
            await tencent.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Sync 接口测试
# ═══════════════════════════════════════════════════════════════════════════════


@requires_network
class TestAggregatorSync:
    """测试 Aggregator 同步接口 (供 Celery 使用)。"""

    def test_sync_realtime(self):
        """同步实时行情应返回有效数据。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="eastmoney,sina,tencent")
        quote = agg.get_realtime_quote_sync(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        assert quote["price"] > 0
        assert agg.last_source in ("eastmoney", "sina", "tencent")
        missing = REALTIME_REQUIRED_KEYS - set(quote.keys())
        assert not missing, f"统一格式缺少字段: {missing}"

    def test_sync_realtime_fallback(self):
        """同步接口 fallback: 当 EastMoney 不可用时应回退。"""
        from app.services.aggregator import StockDataAggregator
        agg = StockDataAggregator(priority="eastmoney,sina,tencent")
        quote = agg.get_realtime_quote_sync(TEST_STOCK_CODE, TEST_STOCK_MARKET)
        assert quote is not None
        assert quote["price"] > 0
