"""
FastAPI 后端 API 端点测试。

通过 HTTP 请求验证已启动的后端 API 的行为。

依赖: Backend 运行中 (uvicorn at localhost:8000) + PostgreSQL
"""
import requests
import pytest

from conftest import (
    requires_backend,
    requires_postgres,
    BACKEND_URL,
    DATABASE_URL_SYNC,
    TEST_STOCK_CODE,
    TEST_STOCK_NAME,
    TEST_STOCK_MARKET,
)


def _api(method: str, path: str, **kwargs) -> requests.Response:
    """Send an HTTP request to the backend API."""
    url = f"{BACKEND_URL}{path}"
    return getattr(requests, method)(url, timeout=10, **kwargs)


def _hard_delete_stock(code: str):
    """Hard-delete a test stock directly from DB."""
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


@requires_backend
@requires_postgres
class TestBackendAPI:
    """测试 FastAPI 端点。"""

    # ── Health ────────────────────────────────────────────────────────────────

    def test_health(self):
        """GET /api/health 应返回 200。"""
        resp = _api("get", "/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    # ── Stock CRUD ────────────────────────────────────────────────────────────

    def test_list_stocks(self):
        """GET /api/stocks/ 应返回列表。"""
        resp = _api("get", "/api/stocks/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_add_and_get_stock(self):
        """POST -> GET -> PUT -> DELETE 完整生命周期。"""
        test_code = "888801"
        _hard_delete_stock(test_code)

        # Create
        resp = _api("post", "/api/stocks/", json={
            "code": test_code, "name": "API测试股", "market": "SZ",
        })
        assert resp.status_code == 201, f"创建失败: {resp.text}"
        stock = resp.json()
        stock_id = stock["id"]
        assert stock["code"] == test_code
        assert stock["market"] == "SZ"

        # Get
        resp = _api("get", f"/api/stocks/{stock_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "API测试股"

        # Update
        resp = _api("put", f"/api/stocks/{stock_id}", json={"name": "更新后名称"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "更新后名称"

        # Delete (soft)
        resp = _api("delete", f"/api/stocks/{stock_id}")
        assert resp.status_code == 200

        # Verify inactive
        resp = _api("get", f"/api/stocks/{stock_id}")
        assert resp.json()["is_active"] is False

        # Final cleanup
        _hard_delete_stock(test_code)

    def test_add_duplicate_stock_returns_400(self):
        """添加重复代码应返回 400。"""
        test_code = "888802"
        _hard_delete_stock(test_code)

        _api("post", "/api/stocks/", json={
            "code": test_code, "name": "First", "market": "SH",
        })
        resp = _api("post", "/api/stocks/", json={
            "code": test_code, "name": "Duplicate", "market": "SH",
        })
        assert resp.status_code == 400

        _hard_delete_stock(test_code)

    def test_get_nonexistent_stock_returns_404(self):
        """查询不存在的 stock_id 应返回 404。"""
        resp = _api("get", "/api/stocks/999999")
        assert resp.status_code == 404

    # ── Quotes ────────────────────────────────────────────────────────────────

    def test_get_snapshots_empty(self):
        """查询快照, 无数据时应返回空列表。"""
        resp = _api("get", "/api/quotes/snapshots/999999")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_klines_empty(self):
        """查询 K 线, 无数据时应返回空列表。"""
        resp = _api("get", "/api/quotes/klines/999999")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_logs(self):
        """GET /api/quotes/logs 应返回列表。"""
        resp = _api("get", "/api/quotes/logs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    # ── Search ────────────────────────────────────────────────────────────────

    def test_search_missing_keyword_returns_422(self):
        """搜索不带 keyword 参数应返回 422。"""
        resp = _api("get", "/api/stocks/search")
        assert resp.status_code == 422
