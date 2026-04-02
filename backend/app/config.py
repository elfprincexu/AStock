from pathlib import Path
from pydantic_settings import BaseSettings

# Determine the project root (parent of backend/)
_project_root = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://astock:astock123@localhost:5432/astock"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://astock:astock123@localhost:5432/astock"
    REDIS_URL: str = "redis://localhost:6379/0"
    EASTMONEY_TIMEOUT: int = 10
    DATA_SOURCE_PRIORITY: str = "akshare,tushare,baostock,eastmoney,sina,tencent"
    DATA_SOURCE_TIMEOUT: int = 10
    TUSHARE_TOKEN: str = ""  # Optional: register at https://tushare.pro for a token
    KLINE_INITIAL_LIMIT: int = 2500  # First-time fetch: ~10 years of trading days

    # AI Analysis (LLM) configuration
    LITELLM_MODEL: str = ""  # e.g. "gemini/gemini-2.5-flash", "anthropic/claude-3-5-sonnet-20241022"
    GEMINI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""  # For compatible providers (e.g. DeepSeek)
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 8192
    LLM_REQUEST_TIMEOUT: int = 120  # Timeout in seconds for LLM API calls
    LLM_SSL_VERIFY: bool = True  # Set False for corporate/self-signed certs

    # JWT Authentication
    JWT_SECRET_KEY: str = "astock-jwt-secret-change-in-production-2026"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 hours

    # Display timezone (used by frontend for all datetime formatting)
    DISPLAY_TIMEZONE: str = "Asia/Shanghai"

    # Broker (live trading) configuration
    BROKER_ACCOUNT: str = ""          # 券商资金账号 (e.g. 平安证券账号)
    BROKER_PASSWORD: str = ""         # 券商密码 (stored encrypted in .env)
    BROKER_QMT_PATH: str = ""         # QMT Mini客户端路径 (e.g. "C:/平安证券/QMT/bin.x64")

    # Daily update schedule defaults (can be overridden via DB app_settings)
    DAILY_UPDATE_HOUR: int = 16        # 16:00 (4 PM)
    DAILY_UPDATE_MINUTE: int = 0
    DAILY_UPDATE_TIMEZONE: str = "Asia/Shanghai"
    DAILY_UPDATE_ENABLED: bool = True

    class Config:
        env_file = _project_root / ".env"
        extra = "ignore"


settings = Settings()

# Default schedule settings (used when DB has no overrides)
SCHEDULE_DEFAULTS = {
    "daily_update_hour": str(settings.DAILY_UPDATE_HOUR),
    "daily_update_minute": str(settings.DAILY_UPDATE_MINUTE),
    "daily_update_timezone": settings.DAILY_UPDATE_TIMEZONE,
    "daily_update_enabled": str(settings.DAILY_UPDATE_ENABLED).lower(),
    "daily_update_last_run": "",
    "daily_update_last_status": "",
    "daily_update_last_message": "",
}
