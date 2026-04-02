"""
Shared fixtures and helpers for the AStock test suite.

pytest auto-loads this file. The pyproject.toml [tool.pytest.ini_options]
adds both ``backend/`` and ``tests/`` to ``pythonpath`` so that:
  - ``from app.xxx import ...`` works  (backend code)
  - ``from conftest import ...`` works  (test helpers / markers)
"""
import os

import pytest


# ── Configuration constants ───────────────────────────────────────────────────

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "astock")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "astock123")
POSTGRES_DB = os.getenv("POSTGRES_DB", "astock")

DATABASE_URL_SYNC = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)
DATABASE_URL_ASYNC = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000")
GRAFANA_USER = os.getenv("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.getenv("GRAFANA_PASSWORD", "admin")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# A well-known stock for testing
TEST_STOCK_CODE = "600519"
TEST_STOCK_NAME = "贵州茅台"
TEST_STOCK_MARKET = "SH"


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is accepting connections."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


# ── Conditional skip markers ──────────────────────────────────────────────────

requires_postgres = pytest.mark.skipif(
    not is_port_open(POSTGRES_HOST, POSTGRES_PORT),
    reason=f"PostgreSQL not reachable at {POSTGRES_HOST}:{POSTGRES_PORT}",
)

requires_redis = pytest.mark.skipif(
    not is_port_open(REDIS_HOST, REDIS_PORT),
    reason=f"Redis not reachable at {REDIS_HOST}:{REDIS_PORT}",
)

requires_grafana = pytest.mark.skipif(
    not is_port_open(GRAFANA_URL.split("://")[1].split(":")[0],
                     int(GRAFANA_URL.rsplit(":", 1)[1])),
    reason=f"Grafana not reachable at {GRAFANA_URL}",
)

requires_backend = pytest.mark.skipif(
    not is_port_open("localhost", 8000),
    reason="Backend API not reachable at localhost:8000",
)

requires_network = pytest.mark.skipif(
    not is_port_open("push2.eastmoney.com", 443),
    reason="Cannot reach push2.eastmoney.com (no network?)",
)
