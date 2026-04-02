from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from zoneinfo import ZoneInfo

from app.database import get_db
from app.models import AppSetting
from app.config import SCHEDULE_DEFAULTS

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────────────

class ScheduleSettingsOut(BaseModel):
    daily_update_hour: int = 16
    daily_update_minute: int = 0
    daily_update_timezone: str = "Asia/Shanghai"
    daily_update_enabled: bool = True
    daily_update_last_run: Optional[str] = None
    daily_update_last_status: Optional[str] = None
    daily_update_last_message: Optional[str] = None


class ScheduleSettingsUpdate(BaseModel):
    daily_update_hour: Optional[int] = Field(None, ge=0, le=23)
    daily_update_minute: Optional[int] = Field(None, ge=0, le=59)
    daily_update_timezone: Optional[str] = None
    daily_update_enabled: Optional[bool] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _get_setting(db: AsyncSession, key: str) -> str:
    """Read a setting, falling back to SCHEDULE_DEFAULTS."""
    result = await db.execute(
        select(AppSetting.value).where(AppSetting.key == key)
    )
    val = result.scalar_one_or_none()
    if val is not None:
        return val
    return SCHEDULE_DEFAULTS.get(key, "")


async def _set_setting(db: AsyncSession, key: str, value: str):
    """Upsert a setting."""
    await db.execute(text("""
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (:key, :val, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    """), {"key": key, "val": value})


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/settings", response_model=ScheduleSettingsOut, summary="Get schedule settings")
async def get_schedule_settings(db: AsyncSession = Depends(get_db)):
    """Return current schedule configuration."""
    # Ensure table exists (auto-created by init_db, but be safe)
    hour = await _get_setting(db, "daily_update_hour")
    minute = await _get_setting(db, "daily_update_minute")
    timezone = await _get_setting(db, "daily_update_timezone")
    enabled = await _get_setting(db, "daily_update_enabled")
    last_run = await _get_setting(db, "daily_update_last_run")
    last_status = await _get_setting(db, "daily_update_last_status")
    last_message = await _get_setting(db, "daily_update_last_message")

    return ScheduleSettingsOut(
        daily_update_hour=int(hour) if hour else 16,
        daily_update_minute=int(minute) if minute else 0,
        daily_update_timezone=timezone or "Asia/Shanghai",
        daily_update_enabled=enabled == "true",
        daily_update_last_run=last_run or None,
        daily_update_last_status=last_status or None,
        daily_update_last_message=last_message or None,
    )


@router.put("/settings", response_model=ScheduleSettingsOut, summary="Update schedule settings")
async def update_schedule_settings(
    payload: ScheduleSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update schedule configuration. Only provided fields are updated."""
    # Validate timezone if provided
    if payload.daily_update_timezone is not None:
        try:
            ZoneInfo(payload.daily_update_timezone)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid timezone: {payload.daily_update_timezone}"
            )

    if payload.daily_update_hour is not None:
        await _set_setting(db, "daily_update_hour", str(payload.daily_update_hour))
    if payload.daily_update_minute is not None:
        await _set_setting(db, "daily_update_minute", str(payload.daily_update_minute))
    if payload.daily_update_timezone is not None:
        await _set_setting(db, "daily_update_timezone", payload.daily_update_timezone)
    if payload.daily_update_enabled is not None:
        await _set_setting(db, "daily_update_enabled", str(payload.daily_update_enabled).lower())

    await db.commit()

    # Return updated settings
    return await get_schedule_settings(db)


@router.get("/status", summary="Get last update status")
async def get_update_status(db: AsyncSession = Depends(get_db)):
    """Return the last daily update run status with summary stats."""
    last_run = await _get_setting(db, "daily_update_last_run")
    last_status = await _get_setting(db, "daily_update_last_status")
    last_message = await _get_setting(db, "daily_update_last_message")

    # Get some aggregate stats
    result = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM stocks WHERE is_active = TRUE) as total_stocks,
            (SELECT COUNT(DISTINCT stock_id) FROM daily_klines) as stocks_with_klines,
            (SELECT COUNT(*) FROM daily_klines) as total_klines,
            (SELECT MAX(date) FROM daily_klines) as latest_kline_date,
            (SELECT COUNT(*) FROM stock_profiles WHERE pe_ttm IS NOT NULL) as stocks_with_pe
    """))
    row = result.one()

    return {
        "last_run": last_run or None,
        "last_status": last_status or None,
        "last_message": last_message or None,
        "stats": {
            "total_active_stocks": row[0],
            "stocks_with_klines": row[1],
            "total_kline_rows": row[2],
            "latest_kline_date": str(row[3]) if row[3] else None,
            "stocks_with_pe": row[4],
        },
    }


@router.post("/trigger", summary="Manually trigger daily update")
async def trigger_daily_update(db: AsyncSession = Depends(get_db)):
    """Manually trigger the daily update task now (ignores schedule time)."""
    from app.tasks import daily_update_task

    # Use timezone-aware Beijing time (consistent with check_scheduled_update)
    tz_name = await _get_setting(db, "daily_update_timezone") or "Asia/Shanghai"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz)

    # Update status
    await _set_setting(db, "daily_update_last_status", "running")
    await _set_setting(db, "daily_update_last_message", "Manual trigger")
    await _set_setting(db, "daily_update_last_run", now.isoformat())
    await db.commit()

    # Dispatch Celery task
    result = daily_update_task.delay()
    return {
        "ok": True,
        "message": "Daily update task dispatched",
        "task_id": result.id,
    }
