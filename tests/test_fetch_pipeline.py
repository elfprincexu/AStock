"""
端到端抓取流程测试。

完整流程: 添加股票 → 触发抓取 → 验证数据入库。

依赖: PostgreSQL + Backend 运行中 + 网络连接 (push2/push2his可达)
"""
import requests as http_requests
import pytest
from datetime import date

from conftest import (
    requires_postgres,
    requires_network,
    requires_backend,
    DATABASE_URL_SYNC,
    BACKEND_URL,
    TEST_STOCK_CODE,
    TEST_STOCK_NAME,
    TEST_STOCK_MARKET,
)


E2E_STOCK_CODE = "600519"
E2E_STOCK_NAME = "贵州茅台"
E2E_STOCK_MARKET = "SH"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}


def _push2_available() -> bool:
    """Check if push2 realtime API reliably returns data (2 consecutive OK)."""
    import time as _time
    for _ in range(2):
        try:
            resp = http_requests.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": "1.600519", "fields": "f57",
                        "ut": "fa5fd1943c7b386f172d6893dbbd1177"},
                headers=_HEADERS, timeout=10,
            )
            if resp.status_code != 200:
                return False
            _time.sleep(1)
        except Exception:
            return False
    return True


def _push2his_available() -> bool:
    """Check if push2his kline API reliably returns data (2 consecutive OK)."""
    import time as _time
    for _ in range(2):
        try:
            resp = http_requests.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params={"secid": "1.600519", "fields1": "f1", "fields2": "f51",
                        "klt": "101", "fqt": "1", "beg": "0", "end": "20500101",
                        "lmt": "1", "ut": "fa5fd1943c7b386f172d6893dbbd1177"},
                headers=_HEADERS, timeout=10,
            )
            if resp.status_code != 200:
                return False
            _time.sleep(1)
        except Exception:
            return False
    return True


_push2_ok = _push2_available()
_push2his_ok = _push2his_available()

requires_push2 = pytest.mark.skipif(
    not _push2_ok,
    reason="push2.eastmoney.com not returning data (IP/network restriction)",
)
requires_push2his = pytest.mark.skipif(
    not _push2his_ok,
    reason="push2his.eastmoney.com not returning data (IP/network restriction)",
)

# Full pipeline needs both push2 endpoints
requires_push2_all = pytest.mark.skipif(
    not (_push2_ok and _push2his_ok),
    reason="push2/push2his APIs not available (required for full pipeline)",
)


def _api(method: str, path: str, **kwargs) -> http_requests.Response:
    """Send request to running backend."""
    url = f"{BACKEND_URL}{path}"
    return getattr(http_requests, method)(url, timeout=30, **kwargs)


@requires_postgres
@requires_network
class TestFetchPipeline:
    """端到端抓取流程测试。"""

    def _cleanup(self, code: str):
        """Remove test stock via API."""
        resp = _api("get", "/api/stocks/")
        if resp.status_code == 200:
            for s in resp.json():
                if s["code"] == code:
                    _api("delete", f"/api/stocks/{s['id']}")

    def _cleanup_db(self, code: str):
        """Direct DB cleanup for thorough removal."""
        from sqlalchemy import create_engine, text
        engine = create_engine(DATABASE_URL_SYNC)
        with engine.connect() as conn:
            conn.execute(text(
                "DELETE FROM fetch_logs WHERE stock_id IN "
                "(SELECT id FROM stocks WHERE code = :code)"
            ), {"code": code})
            conn.execute(text(
                "DELETE FROM quote_snapshots WHERE stock_id IN "
                "(SELECT id FROM stocks WHERE code = :code)"
            ), {"code": code})
            conn.execute(text(
                "DELETE FROM daily_klines WHERE stock_id IN "
                "(SELECT id FROM stocks WHERE code = :code)"
            ), {"code": code})
            conn.execute(text("DELETE FROM stocks WHERE code = :code"), {"code": code})
            conn.commit()
        engine.dispose()

    # ── 1. EastMoneyClient 直接调用测试 ───────────────────────────────────────

    @requires_push2
    @pytest.mark.asyncio
    async def test_eastmoney_client_realtime(self):
        """EastMoneyClient.get_realtime_quote 应返回有效数据。"""
        from app.services.eastmoney import EastMoneyClient
        client = EastMoneyClient(timeout=15)
        try:
            quote = await client.get_realtime_quote(E2E_STOCK_CODE, E2E_STOCK_MARKET)
            assert "price" in quote
            assert "volume" in quote
            assert "change_pct" in quote
            assert quote["price"] > 0, f"价格应 > 0, 实际: {quote['price']}"
        finally:
            await client.close()

    @requires_push2his
    @pytest.mark.asyncio
    async def test_eastmoney_client_klines(self):
        """EastMoneyClient.get_daily_klines 应返回非空列表。"""
        from app.services.eastmoney import EastMoneyClient
        client = EastMoneyClient(timeout=15)
        try:
            klines = await client.get_daily_klines(
                E2E_STOCK_CODE, E2E_STOCK_MARKET, limit=5
            )
            assert len(klines) > 0, "K 线数据为空"
            k = klines[0]
            assert "date" in k
            assert "open" in k
            assert "close" in k
            assert "volume" in k
            assert isinstance(k["date"], date), f"date 类型应为 date, 实际: {type(k['date'])}"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_eastmoney_client_search(self):
        """EastMoneyClient.search_stock 应返回匹配结果。"""
        from app.services.eastmoney import EastMoneyClient
        client = EastMoneyClient(timeout=15)
        try:
            results = await client.search_stock("茅台")
            assert len(results) > 0, "搜索 '茅台' 无结果"
            found_codes = [r["code"] for r in results]
            assert E2E_STOCK_CODE in found_codes, f"未找到 {E2E_STOCK_CODE}, 结果: {found_codes}"
        finally:
            await client.close()

    # ── 2. 完整入库流程 (通过 running backend) ─────────────────────────────────

    @requires_push2_all
    @requires_backend
    def test_full_fetch_pipeline(self):
        """添加股票 → 触发抓取 → 验证快照和K线入库。"""
        pipeline_code = "601398"  # 工商银行
        self._cleanup_db(pipeline_code)

        # Step 1: 添加股票
        resp = _api("post", "/api/stocks/", json={
            "code": pipeline_code, "name": "工商银行", "market": "SH",
        })
        assert resp.status_code == 201, f"添加失败: {resp.text}"
        stock_id = resp.json()["id"]

        # Step 2: 触发抓取
        resp = _api("post", f"/api/stocks/{stock_id}/fetch")
        assert resp.status_code == 200, f"抓取失败: {resp.text}"
        result = resp.json()
        assert result["ok"] is True

        # Step 3: 验证快照入库
        resp = _api("get", f"/api/quotes/snapshots/{stock_id}?limit=5")
        assert resp.status_code == 200
        snapshots = resp.json()
        assert len(snapshots) > 0, "抓取后应有快照数据"
        s = snapshots[0]
        assert s["price"] > 0, f"快照价格应 > 0: {s}"
        assert s["volume"] >= 0

        # Step 4: 验证 K 线入库
        resp = _api("get", f"/api/quotes/klines/{stock_id}?limit=10")
        assert resp.status_code == 200
        klines = resp.json()
        assert len(klines) > 0, "抓取后应有 K 线数据"
        k = klines[0]
        assert k["close"] > 0, f"K 线收盘价应 > 0: {k}"
        assert k["date"] is not None

        # Step 5: 验证抓取日志
        resp = _api("get", f"/api/quotes/logs?stock_id={stock_id}&limit=5")
        assert resp.status_code == 200
        logs = resp.json()
        assert len(logs) > 0, "应有抓取日志"
        assert logs[0]["status"] == "success"

        self._cleanup_db(pipeline_code)

    # ── 3. 重复抓取不应产生重复 K 线 (upsert 验证) ────────────────────────────

    @requires_push2_all
    @requires_backend
    def test_fetch_twice_no_duplicate_klines(self):
        """抓取两次同一只股票, K 线数应保持不变 (upsert)。"""
        pipeline_code = "601288"  # 农业银行
        self._cleanup_db(pipeline_code)

        # Add
        resp = _api("post", "/api/stocks/", json={
            "code": pipeline_code, "name": "农业银行", "market": "SH",
        })
        stock_id = resp.json()["id"]

        # Fetch #1
        _api("post", f"/api/stocks/{stock_id}/fetch")
        resp = _api("get", f"/api/quotes/klines/{stock_id}?limit=1000")
        count_first = len(resp.json())

        # Fetch #2
        _api("post", f"/api/stocks/{stock_id}/fetch")
        resp = _api("get", f"/api/quotes/klines/{stock_id}?limit=1000")
        count_second = len(resp.json())

        assert count_second == count_first, (
            f"第二次抓取后 K 线数变化: {count_first} -> {count_second}, "
            "upsert 可能未生效"
        )

        self._cleanup_db(pipeline_code)
