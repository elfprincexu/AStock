"""Stock Screener API Router."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.services.screener import (
    ScreenerCriteria,
    ScreenerResult,
    run_screen,
    get_preset_strategies,
    criteria_from_dict,
    detect_volume_pattern,
)

router = APIRouter()


class ScreenerRequest(BaseModel):
    """Request body for custom screening."""
    industries: list[str] = []
    exclude_industries: list[str] = []
    min_market_cap: Optional[float] = None
    max_market_cap: Optional[float] = None
    min_pe: Optional[float] = None
    max_pe: Optional[float] = None
    min_pb: Optional[float] = None
    max_pb: Optional[float] = None
    price_percentile_days: int = 250
    min_price_percentile: Optional[float] = None
    max_price_percentile: Optional[float] = None
    volume_surge_recent_days: int = 5
    volume_surge_base_days: int = 60
    min_volume_surge_ratio: Optional[float] = None
    max_volume_contraction_ratio: Optional[float] = None
    enable_volume_pattern: bool = False
    # Volume pattern customizable parameters
    vp_lookback_months: int = 4
    vp_min_surge_weeks: int = 3
    vp_surge_vol_ratio: float = 1.4
    vp_min_surge_gain: float = 10.0
    vp_max_surge_gain: float = 30.0
    vp_min_pullback_pct: float = 10.0
    vp_min_pullback_weeks: int = 2
    vp_recent_vol_days: int = 5
    vp_recent_5d_vs_minweek_max: float = 1.1
    vp_latest_vs_minday_max: float = 1.1
    vp_min_score: float = 30.0
    sort_by: str = "market_cap_desc"
    limit: int = 50
    offset: int = 0


class ScreenerResultOut(BaseModel):
    stock_id: int
    code: str
    name: str
    industry: Optional[str] = None
    total_market_cap: Optional[float] = None
    circ_market_cap: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    latest_close: Optional[float] = None
    price_percentile: Optional[float] = None
    volume_surge_ratio: Optional[float] = None
    avg_turnover_rate: Optional[float] = None
    weekly_change_pct: Optional[float] = None
    volume_pattern_score: Optional[float] = None
    surge_weeks_count: Optional[int] = None
    surge_price_gain: Optional[float] = None
    pullback_weeks: Optional[int] = None
    pullback_pct: Optional[float] = None
    recent_vol_percentile: Optional[float] = None
    # Reference metrics
    base_weekly_vol: Optional[float] = None
    max_surge_vol_ratio: Optional[float] = None
    pullback_min_weekly_vol: Optional[float] = None
    min_daily_vol: Optional[float] = None
    latest_daily_vol: Optional[float] = None
    recent_5d_total_vol: Optional[float] = None
    recent_5d_vs_minweek: Optional[float] = None
    latest_vs_minday: Optional[float] = None


class ScreenerResponse(BaseModel):
    total: int
    results: list[ScreenerResultOut]


class PresetStrategyOut(BaseModel):
    key: str
    name: str
    description: str


def _result_to_out(r: ScreenerResult) -> ScreenerResultOut:
    """Convert a ScreenerResult dataclass to ScreenerResultOut pydantic model."""
    return ScreenerResultOut(
        stock_id=r.stock_id,
        code=r.code,
        name=r.name,
        industry=r.industry,
        total_market_cap=r.total_market_cap,
        circ_market_cap=r.circ_market_cap,
        pe_ttm=r.pe_ttm,
        pb=r.pb,
        latest_close=r.latest_close,
        price_percentile=r.price_percentile,
        volume_surge_ratio=r.volume_surge_ratio,
        avg_turnover_rate=r.avg_turnover_rate,
        weekly_change_pct=r.weekly_change_pct,
        volume_pattern_score=r.volume_pattern_score,
        surge_weeks_count=r.surge_weeks_count,
        surge_price_gain=r.surge_price_gain,
        pullback_weeks=r.pullback_weeks,
        pullback_pct=r.pullback_pct,
        recent_vol_percentile=r.recent_vol_percentile,
        base_weekly_vol=r.base_weekly_vol,
        max_surge_vol_ratio=r.max_surge_vol_ratio,
        pullback_min_weekly_vol=r.pullback_min_weekly_vol,
        min_daily_vol=r.min_daily_vol,
        latest_daily_vol=r.latest_daily_vol,
        recent_5d_total_vol=r.recent_5d_total_vol,
        recent_5d_vs_minweek=r.recent_5d_vs_minweek,
        latest_vs_minday=r.latest_vs_minday,
    )


@router.post(
    "/screen",
    response_model=ScreenerResponse,
    summary="Run stock screening",
)
async def screen_stocks(
    req: ScreenerRequest,
    db: AsyncSession = Depends(get_db),
):
    """Screen stocks based on fundamental and technical criteria."""
    criteria = ScreenerCriteria(
        industries=req.industries,
        exclude_industries=req.exclude_industries,
        min_market_cap=req.min_market_cap,
        max_market_cap=req.max_market_cap,
        min_pe=req.min_pe,
        max_pe=req.max_pe,
        min_pb=req.min_pb,
        max_pb=req.max_pb,
        price_percentile_days=req.price_percentile_days,
        min_price_percentile=req.min_price_percentile,
        max_price_percentile=req.max_price_percentile,
        volume_surge_recent_days=req.volume_surge_recent_days,
        volume_surge_base_days=req.volume_surge_base_days,
        min_volume_surge_ratio=req.min_volume_surge_ratio,
        max_volume_contraction_ratio=req.max_volume_contraction_ratio,
        enable_volume_pattern=req.enable_volume_pattern,
        vp_lookback_months=req.vp_lookback_months,
        vp_min_surge_weeks=req.vp_min_surge_weeks,
        vp_surge_vol_ratio=req.vp_surge_vol_ratio,
        vp_min_surge_gain=req.vp_min_surge_gain,
        vp_max_surge_gain=req.vp_max_surge_gain,
        vp_min_pullback_pct=req.vp_min_pullback_pct,
        vp_min_pullback_weeks=req.vp_min_pullback_weeks,
        vp_recent_vol_days=req.vp_recent_vol_days,
        vp_recent_5d_vs_minweek_max=req.vp_recent_5d_vs_minweek_max,
        vp_latest_vs_minday_max=req.vp_latest_vs_minday_max,
        vp_min_score=req.vp_min_score,
        sort_by=req.sort_by,
        limit=req.limit,
        offset=req.offset,
    )
    results, total = await run_screen(db, criteria)
    return ScreenerResponse(
        total=total,
        results=[_result_to_out(r) for r in results],
    )


@router.get(
    "/presets",
    response_model=list[PresetStrategyOut],
    summary="Get preset screening strategies",
)
async def list_presets():
    """Return available preset screening strategies."""
    presets = get_preset_strategies()
    return [
        PresetStrategyOut(key=k, name=v["name"], description=v["description"])
        for k, v in presets.items()
    ]


@router.post(
    "/preset/{strategy_key}",
    response_model=ScreenerResponse,
    summary="Run a preset screening strategy",
)
async def run_preset(
    strategy_key: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Run a preset screening strategy by key."""
    presets = get_preset_strategies()
    if strategy_key not in presets:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Preset '{strategy_key}' not found")

    preset = presets[strategy_key]
    criteria_dict = {**preset["criteria"], "limit": limit, "offset": offset}
    criteria = criteria_from_dict(criteria_dict)
    results, total = await run_screen(db, criteria)
    return ScreenerResponse(
        total=total,
        results=[_result_to_out(r) for r in results],
    )


@router.get(
    "/industries",
    summary="Get available industry list",
)
async def list_industries(db: AsyncSession = Depends(get_db)):
    """Return distinct industries from stock profiles."""
    from sqlalchemy import text
    result = await db.execute(
        text("SELECT DISTINCT industry FROM stock_profiles WHERE industry IS NOT NULL AND industry != '' ORDER BY industry")
    )
    return [r[0] for r in result.all()]
