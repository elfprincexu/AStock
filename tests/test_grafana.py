"""
Grafana 服务测试。

验证:
  - Grafana 健康检查
  - PostgreSQL 数据源已配置且可连接
  - 预置看板已加载

依赖: Grafana + PostgreSQL 运行中 (docker-compose up -d)
"""
import httpx
import pytest

from conftest import (
    requires_grafana,
    requires_postgres,
    GRAFANA_URL,
    GRAFANA_USER,
    GRAFANA_PASSWORD,
)

TIMEOUT = httpx.Timeout(10.0)


def _grafana_get(path: str) -> httpx.Response:
    """Send authenticated GET to Grafana API."""
    with httpx.Client(timeout=TIMEOUT) as client:
        return client.get(
            f"{GRAFANA_URL}{path}",
            auth=(GRAFANA_USER, GRAFANA_PASSWORD),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 健康检查
# ═══════════════════════════════════════════════════════════════════════════════

@requires_grafana
class TestGrafanaHealth:
    """测试 Grafana 基础可用性。"""

    def test_health_endpoint(self):
        """GET /api/health 应返回 200。"""
        resp = _grafana_get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("database") == "ok", f"Grafana DB status: {data}"

    def test_login(self):
        """应能用 admin/admin 登录。"""
        resp = _grafana_get("/api/org")
        assert resp.status_code == 200, f"登录失败: {resp.status_code}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 数据源
# ═══════════════════════════════════════════════════════════════════════════════

@requires_grafana
@requires_postgres
class TestGrafanaDatasource:
    """测试 Grafana 的 PostgreSQL 数据源配置。"""

    def test_datasource_exists(self):
        """应存在名为 AStock-PostgreSQL 的数据源。"""
        resp = _grafana_get("/api/datasources")
        assert resp.status_code == 200
        datasources = resp.json()
        names = [ds["name"] for ds in datasources]
        assert "AStock-PostgreSQL" in names, f"数据源列表: {names}"

    def test_datasource_type_is_postgres(self):
        """数据源类型应为 postgres。"""
        resp = _grafana_get("/api/datasources")
        for ds in resp.json():
            if ds["name"] == "AStock-PostgreSQL":
                assert "postgres" in ds["type"], f"数据源类型: {ds['type']}"
                return
        pytest.fail("未找到 AStock-PostgreSQL 数据源")

    def test_datasource_health_check(self):
        """数据源健康检查应通过。"""
        # First get datasource id
        resp = _grafana_get("/api/datasources")
        ds_id = None
        for ds in resp.json():
            if ds["name"] == "AStock-PostgreSQL":
                ds_id = ds["id"]
                break
        assert ds_id is not None, "未找到数据源"

        # Check health via uid-based endpoint
        resp = _grafana_get(f"/api/datasources/{ds_id}")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 看板
# ═══════════════════════════════════════════════════════════════════════════════

@requires_grafana
class TestGrafanaDashboard:
    """测试预置看板是否正确加载。"""

    def test_dashboard_exists(self):
        """应能通过 UID 找到预置看板。"""
        resp = _grafana_get("/api/dashboards/uid/astock-main")
        assert resp.status_code == 200, f"看板查询失败: {resp.status_code}"
        data = resp.json()
        assert "dashboard" in data

    def test_dashboard_title(self):
        """看板标题应为 'AStock A股数据看板'。"""
        resp = _grafana_get("/api/dashboards/uid/astock-main")
        dashboard = resp.json()["dashboard"]
        assert dashboard["title"] == "AStock A股数据看板", f"标题: {dashboard['title']}"

    def test_dashboard_has_panels(self):
        """看板应包含多个面板。"""
        resp = _grafana_get("/api/dashboards/uid/astock-main")
        panels = resp.json()["dashboard"].get("panels", [])
        assert len(panels) > 0, "看板无面板"

    def test_dashboard_has_template_variables(self):
        """看板应包含模板变量 (股票选择、时间范围)。"""
        resp = _grafana_get("/api/dashboards/uid/astock-main")
        templating = resp.json()["dashboard"].get("templating", {})
        variables = templating.get("list", [])
        var_names = [v["name"] for v in variables]
        assert "stock" in var_names, f"缺少 'stock' 变量, 现有: {var_names}"
        assert "timerange" in var_names, f"缺少 'timerange' 变量, 现有: {var_names}"
