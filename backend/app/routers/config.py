"""
Configuration management router.

Provides GET/PUT endpoints for all system settings (LLM, service ports,
data sources) and a POST endpoint to test LLM connectivity.

Settings are persisted to the .env file and applied to the running
process immediately.
"""

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

router = APIRouter()

# Path to .env file (next to the backend dir)
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


# ── Schemas ──────────────────────────────────────────────────────────────────

class ServicePortsOut(BaseModel):
    backend: int = 8000
    frontend: int = 5174
    postgres: int = 5432
    redis: int = 6379
    grafana: int = 3000


class LLMSettingsOut(BaseModel):
    model: str = ""
    api_key_masked: str = ""           # Only first/last 4 chars shown
    api_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 8192
    request_timeout: int = 120
    ssl_verify: bool = True


class DataSourceSettingsOut(BaseModel):
    priority: str = "akshare,tushare,baostock,eastmoney,sina,tencent"
    timeout: int = 10
    tushare_token_set: bool = False    # Whether a token is configured


class ConfigSettingsOut(BaseModel):
    service_ports: ServicePortsOut
    llm: LLMSettingsOut
    data_source: DataSourceSettingsOut
    broker: "BrokerSettingsOut"
    display_timezone: str = "Asia/Shanghai"


class BrokerSettingsOut(BaseModel):
    account_masked: str = ""       # Only first 4 + last 4 chars shown
    password_set: bool = False     # Whether a password is configured
    qmt_path: str = ""
    xtquant_installed: bool = False


class LLMSettingsUpdate(BaseModel):
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_url: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=256, le=128000)
    request_timeout: Optional[int] = Field(None, ge=10, le=600)
    ssl_verify: Optional[bool] = None


class DataSourceSettingsUpdate(BaseModel):
    priority: Optional[str] = None
    timeout: Optional[int] = Field(None, ge=1, le=120)
    tushare_token: Optional[str] = None


class ServicePortsUpdate(BaseModel):
    backend: Optional[int] = Field(None, ge=1, le=65535)
    frontend: Optional[int] = Field(None, ge=1, le=65535)
    postgres: Optional[int] = Field(None, ge=1, le=65535)
    redis: Optional[int] = Field(None, ge=1, le=65535)
    grafana: Optional[int] = Field(None, ge=1, le=65535)


class BrokerSettingsUpdate(BaseModel):
    account: Optional[str] = None
    password: Optional[str] = None
    qmt_path: Optional[str] = None


class ConfigSettingsUpdate(BaseModel):
    llm: Optional[LLMSettingsUpdate] = None
    data_source: Optional[DataSourceSettingsUpdate] = None
    service_ports: Optional[ServicePortsUpdate] = None
    broker: Optional[BrokerSettingsUpdate] = None
    display_timezone: Optional[str] = None


class LLMTestResult(BaseModel):
    success: bool
    message: str
    model_used: Optional[str] = None
    response_preview: Optional[str] = None


# ── .env helpers ─────────────────────────────────────────────────────────────

def _read_env_dict() -> dict[str, str]:
    """Read current .env file into a dict."""
    env = {}
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _write_env_dict(env: dict[str, str]):
    """Write dict back to .env, preserving comments."""
    lines: list[str] = []
    existing_keys: set[str] = set()

    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in env:
                    lines.append(f"{key}={env[key]}")
                    existing_keys.add(key)
                    continue
            lines.append(line)

    # Append new keys
    for k, v in env.items():
        if k not in existing_keys:
            lines.append(f"{k}={v}")

    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _apply_setting(attr: str, value):
    """Update the in-memory settings object."""
    object.__setattr__(settings, attr, value)


def _mask_key(key: str) -> str:
    """Mask an API key for display: show first 4 and last 4 chars."""
    if not key or len(key) <= 8:
        return "****" if key else ""
    return key[:4] + "****" + key[-4:]


def _extract_port(url: str, default: int) -> int:
    """Extract port number from a URL string."""
    m = re.search(r":(\d+)", url)
    return int(m.group(1)) if m else default


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/settings", response_model=ConfigSettingsOut, summary="Get all config settings")
async def get_config_settings():
    """Return current configuration grouped by category."""
    # Service ports (derived from current config URLs)
    db_port = _extract_port(settings.DATABASE_URL, 5432)
    redis_port = _extract_port(settings.REDIS_URL, 6379)

    env = _read_env_dict()
    backend_port = int(env.get("BACKEND_PORT", "8000"))
    frontend_port = int(env.get("FRONTEND_PORT", "5174"))
    grafana_port = int(env.get("GRAFANA_PORT", "3000"))

    # LLM settings
    llm = LLMSettingsOut(
        model=settings.LITELLM_MODEL,
        api_key_masked=_mask_key(settings.OPENAI_API_KEY),
        api_url=settings.OPENAI_BASE_URL,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
        request_timeout=settings.LLM_REQUEST_TIMEOUT,
        ssl_verify=settings.LLM_SSL_VERIFY,
    )

    # Data source settings
    ds = DataSourceSettingsOut(
        priority=settings.DATA_SOURCE_PRIORITY,
        timeout=settings.DATA_SOURCE_TIMEOUT,
        tushare_token_set=bool(settings.TUSHARE_TOKEN),
    )

    # Broker settings
    xtquant_installed = False
    try:
        from app.services.brokers.pingan import _xt_available
        xtquant_installed = _xt_available
    except ImportError:
        pass

    broker = BrokerSettingsOut(
        account_masked=_mask_key(settings.BROKER_ACCOUNT),
        password_set=bool(settings.BROKER_PASSWORD),
        qmt_path=settings.BROKER_QMT_PATH,
        xtquant_installed=xtquant_installed,
    )

    return ConfigSettingsOut(
        service_ports=ServicePortsOut(
            backend=backend_port,
            frontend=frontend_port,
            postgres=db_port,
            redis=redis_port,
            grafana=grafana_port,
        ),
        llm=llm,
        data_source=ds,
        broker=broker,
        display_timezone=settings.DISPLAY_TIMEZONE,
    )


@router.put("/settings", response_model=ConfigSettingsOut, summary="Update config settings")
async def update_config_settings(payload: ConfigSettingsUpdate):
    """
    Update configuration. Changes are written to .env and applied to the
    running process immediately. Service port changes require manual restart.
    """
    env = _read_env_dict()
    restart_needed = False

    # ── LLM settings ──
    if payload.llm:
        llm = payload.llm
        if llm.model is not None:
            env["LITELLM_MODEL"] = llm.model
            _apply_setting("LITELLM_MODEL", llm.model)
        if llm.api_key is not None:
            env["OPENAI_API_KEY"] = llm.api_key
            _apply_setting("OPENAI_API_KEY", llm.api_key)
            # Set env vars for litellm — both the OpenAI key and
            # provider-specific keys so native prefixes work immediately
            os.environ["OPENAI_API_KEY"] = llm.api_key
            for _ek in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY",
                        "MOONSHOT_API_KEY", "VOLCENGINE_API_KEY"):
                os.environ[_ek] = llm.api_key
        if llm.api_url is not None:
            env["OPENAI_BASE_URL"] = llm.api_url
            _apply_setting("OPENAI_BASE_URL", llm.api_url)
            os.environ["OPENAI_API_BASE"] = llm.api_url
        if llm.temperature is not None:
            env["LLM_TEMPERATURE"] = str(llm.temperature)
            _apply_setting("LLM_TEMPERATURE", llm.temperature)
        if llm.max_tokens is not None:
            env["LLM_MAX_TOKENS"] = str(llm.max_tokens)
            _apply_setting("LLM_MAX_TOKENS", llm.max_tokens)
        if llm.request_timeout is not None:
            env["LLM_REQUEST_TIMEOUT"] = str(llm.request_timeout)
            _apply_setting("LLM_REQUEST_TIMEOUT", llm.request_timeout)
        if llm.ssl_verify is not None:
            env["LLM_SSL_VERIFY"] = str(llm.ssl_verify).lower()
            _apply_setting("LLM_SSL_VERIFY", llm.ssl_verify)

    # ── Data source settings ──
    if payload.data_source:
        ds = payload.data_source
        if ds.priority is not None:
            env["DATA_SOURCE_PRIORITY"] = ds.priority
            _apply_setting("DATA_SOURCE_PRIORITY", ds.priority)
        if ds.timeout is not None:
            env["DATA_SOURCE_TIMEOUT"] = str(ds.timeout)
            _apply_setting("DATA_SOURCE_TIMEOUT", ds.timeout)
        if ds.tushare_token is not None:
            env["TUSHARE_TOKEN"] = ds.tushare_token
            _apply_setting("TUSHARE_TOKEN", ds.tushare_token)

    # ── Service ports (informational, saved for reference) ──
    if payload.service_ports:
        sp = payload.service_ports
        if sp.backend is not None:
            env["BACKEND_PORT"] = str(sp.backend)
            restart_needed = True
        if sp.frontend is not None:
            env["FRONTEND_PORT"] = str(sp.frontend)
            restart_needed = True
        if sp.postgres is not None:
            env["POSTGRES_PORT"] = str(sp.postgres)
            restart_needed = True
        if sp.redis is not None:
            env["REDIS_PORT"] = str(sp.redis)
            restart_needed = True
        if sp.grafana is not None:
            env["GRAFANA_PORT"] = str(sp.grafana)
            restart_needed = True

    # ── Broker settings ──
    if payload.broker:
        br = payload.broker
        if br.account is not None:
            env["BROKER_ACCOUNT"] = br.account
            _apply_setting("BROKER_ACCOUNT", br.account)
        if br.password is not None:
            env["BROKER_PASSWORD"] = br.password
            _apply_setting("BROKER_PASSWORD", br.password)
        if br.qmt_path is not None:
            env["BROKER_QMT_PATH"] = br.qmt_path
            _apply_setting("BROKER_QMT_PATH", br.qmt_path)

    # ── Display timezone ──
    if payload.display_timezone is not None:
        # Validate timezone
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(payload.display_timezone)
        except (ZoneInfoNotFoundError, KeyError):
            raise HTTPException(status_code=422, detail=f"Invalid timezone: {payload.display_timezone}")
        env["DISPLAY_TIMEZONE"] = payload.display_timezone
        _apply_setting("DISPLAY_TIMEZONE", payload.display_timezone)

    _write_env_dict(env)

    return await get_config_settings()


@router.post("/test-llm", response_model=LLMTestResult, summary="Test LLM connectivity")
async def test_llm_connection():
    """Send a short test prompt to the configured LLM and return the result."""
    if not settings.LITELLM_MODEL:
        return LLMTestResult(
            success=False,
            message="LITELLM_MODEL is not configured",
        )

    try:
        from app.services.ai_analysis import call_llm
        # Very short test prompt
        text, model_used = await call_llm(
            "Respond with exactly: OK. Nothing else."
        )
        return LLMTestResult(
            success=True,
            message="LLM connection successful",
            model_used=model_used,
            response_preview=text[:200] if text else "",
        )
    except Exception as e:
        return LLMTestResult(
            success=False,
            message=f"LLM connection failed: {str(e)[:300]}",
        )
