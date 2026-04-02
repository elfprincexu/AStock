"""
Stock Screening Strategy Engine

Supports:
- Fundamental filters: industry, market_cap range, PE range, PB range
- Technical analysis: price percentile, volume surge, volume contraction
- Weekly aggregation from daily klines for technical signals
- Complex pattern: volume surge → pullback → volume contraction detection
"""

from datetime import date, timedelta
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict
import math

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ScreenerCriteria:
    """Screening filter criteria."""
    # Fundamental filters
    industries: list[str] = field(default_factory=list)  # empty = all
    exclude_industries: list[str] = field(default_factory=list)  # industries to exclude
    min_market_cap: Optional[float] = None  # 亿元
    max_market_cap: Optional[float] = None
    min_pe: Optional[float] = None
    max_pe: Optional[float] = None
    min_pb: Optional[float] = None
    max_pb: Optional[float] = None

    # Technical filters (computed from daily klines)
    # Price percentile: current price position in N-day range (0=lowest, 100=highest)
    price_percentile_days: int = 250  # lookback period (trading days)
    min_price_percentile: Optional[float] = None  # e.g. 0 = at 1yr low
    max_price_percentile: Optional[float] = None  # e.g. 30 = below 30th percentile

    # Volume surge: recent avg volume vs historical avg (ratio)
    volume_surge_recent_days: int = 5   # recent period
    volume_surge_base_days: int = 60    # base period
    min_volume_surge_ratio: Optional[float] = None  # e.g. 2.0 = 2x volume

    # Volume contraction: low recent volume vs historical
    max_volume_contraction_ratio: Optional[float] = None  # e.g. 0.5 = half volume

    # Complex pattern: volume-up-then-contraction
    enable_volume_pattern: bool = False  # enable the 3-week-surge + contraction detection

    # Volume pattern customizable parameters
    vp_lookback_months: int = 4           # lookback period in months (default 4 → ~80 trading days)
    vp_min_surge_weeks: int = 3           # minimum consecutive surge weeks required
    vp_surge_vol_ratio: float = 1.4       # weekly volume must be >= this × base avg
    vp_min_surge_gain: float = 10.0       # min cumulative price gain % during surge
    vp_max_surge_gain: float = 30.0       # max cumulative price gain % during surge
    vp_min_pullback_pct: float = 10.0     # min pullback % from peak required
    vp_min_pullback_weeks: int = 2        # min weeks of pullback/contraction after surge
    vp_recent_vol_days: int = 5           # recent N days total volume vs min weekly vol
    vp_recent_5d_vs_minweek_max: float = 1.1  # max ratio: last-5-day total vol / min weekly vol in lookback
    vp_latest_vs_minday_max: float = 1.1       # max ratio: latest 1-day vol / min daily vol in lookback; <1 ok
    vp_min_score: float = 30.0           # minimum pattern score to qualify

    # Sort
    sort_by: str = "market_cap_desc"  # market_cap_desc, pe_asc, pb_asc, price_pct_asc, volume_surge_desc, pattern_score_desc

    # Pagination
    limit: int = 50
    offset: int = 0


@dataclass
class ScreenerResult:
    """Single stock screening result."""
    stock_id: int
    code: str
    name: str
    industry: Optional[str]
    total_market_cap: Optional[float]
    circ_market_cap: Optional[float]
    pe_ttm: Optional[float]
    pb: Optional[float]
    # Technical indicators
    latest_close: Optional[float] = None
    price_percentile: Optional[float] = None  # 0-100
    volume_surge_ratio: Optional[float] = None
    avg_turnover_rate: Optional[float] = None
    weekly_change_pct: Optional[float] = None
    # Volume pattern detection results
    volume_pattern_score: Optional[float] = None  # 0-100 pattern match score
    surge_weeks_count: Optional[int] = None  # number of qualifying surge weeks found
    surge_price_gain: Optional[float] = None  # % price gain during surge phase
    pullback_weeks: Optional[int] = None  # weeks of pullback after surge
    pullback_pct: Optional[float] = None  # % pullback from peak to current price
    recent_vol_percentile: Optional[float] = None  # recent volume percentile (0=lowest)
    # Reference metrics
    base_weekly_vol: Optional[float] = None  # baseline weekly volume (4-week avg before surge)
    max_surge_vol_ratio: Optional[float] = None  # max weekly volume / base during surge
    pullback_min_weekly_vol: Optional[float] = None  # lowest weekly volume during pullback
    min_daily_vol: Optional[float] = None  # min daily volume in lookback window
    latest_daily_vol: Optional[float] = None  # most recent day's volume
    recent_5d_total_vol: Optional[float] = None  # sum of last 5 days volume
    recent_5d_vs_minweek: Optional[float] = None  # last-5-day total / min weekly vol ratio
    latest_vs_minday: Optional[float] = None  # latest day vol / min daily vol ratio


def _aggregate_daily_to_weekly(daily_klines: list) -> list:
    """
    Convert daily klines to weekly klines (Mon-Fri aggregation).

    daily_klines: list of tuples (date, open, close, high, low, volume)
                  sorted by date ascending.

    Returns list of dicts:
      {date_start, date_end, open, close, high, low, volume}
    sorted by date ascending.
    """
    if not daily_klines:
        return []

    weeks = []
    current_week = None
    current_year_week = None

    for d, o, c, h, l, v in daily_klines:
        # isocalendar gives (year, week_number, weekday)
        yw = d.isocalendar()[:2]  # (year, week)

        if yw != current_year_week:
            # Start a new week
            if current_week is not None:
                weeks.append(current_week)
            current_year_week = yw
            current_week = {
                "date_start": d,
                "date_end": d,
                "open": o,
                "close": c,
                "high": h if h is not None else c,
                "low": l if l is not None else c,
                "volume": v if v else 0,
            }
        else:
            # Continue current week
            current_week["date_end"] = d
            current_week["close"] = c
            if h is not None and (current_week["high"] is None or h > current_week["high"]):
                current_week["high"] = h
            if l is not None and (current_week["low"] is None or l < current_week["low"]):
                current_week["low"] = l
            current_week["volume"] += (v if v else 0)

    if current_week is not None:
        weeks.append(current_week)

    return weeks


def detect_volume_pattern(
    daily_klines: list,
    lookback_days: int = 120,
    min_surge_weeks: int = 3,
    surge_vol_ratio: float = 1.5,
    min_surge_gain: float = 2.0,
    max_surge_gain: float = 30.0,
    min_pullback_pct: float = 1.0,
    min_pullback_weeks: int = 2,
    recent_vol_days: int = 5,
    recent_5d_vs_minweek_max: float = 1.5,
    latest_vs_minday_max: float = 1.5,
) -> dict:
    """
    Detect the "volume surge -> pullback -> contraction" pattern.

    daily_klines: list of (date, open, close, high, low, volume) tuples,
                  sorted by date ascending.

    Parameters:
      lookback_days: trading days to look back
      min_surge_weeks: minimum consecutive high-volume weeks
      surge_vol_ratio: weekly volume / base avg threshold
      min_surge_gain / max_surge_gain: cumulative price gain range during surge
      min_pullback_pct: minimum pullback from peak after surge
      min_pullback_weeks: minimum weeks elapsed after surge ended
      recent_vol_days: how many recent days to sum for volume comparison
      recent_5d_vs_minweek_max: max ratio of (sum last N days vol / min weekly vol in lookback)
      latest_vs_minday_max: max ratio of (latest 1-day vol / min daily vol in lookback); <1 ok

    Returns dict with pattern metrics and reference data.
    """
    result = {
        "score": 0.0,
        "surge_weeks": 0,
        "surge_gain": 0.0,
        "pullback_weeks": 0,
        "recent_vol_percentile": 100.0,
        "peak_price": None,
        "pullback_pct": 0.0,
        "base_weekly_vol": None,
        "max_surge_vol_ratio": None,
        "pullback_min_weekly_vol": None,
        "min_daily_vol": None,
        "latest_daily_vol": None,
        "recent_5d_total_vol": None,
        "recent_5d_vs_minweek": None,
        "latest_vs_minday": None,
    }

    if len(daily_klines) < 30:
        return result

    # ---- Step A: Trim to lookback window ----
    klines_window = daily_klines[-lookback_days:] if len(daily_klines) > lookback_days else daily_klines[:]

    # ---- Step B: Aggregate to weekly klines ----
    weekly = _aggregate_daily_to_weekly(klines_window)

    min_weekly_needed = 4 + min_surge_weeks
    if len(weekly) < min_weekly_needed:
        return result

    # ---- Step C: Scan for consecutive surge weeks ----
    best_surge = None  # will hold the best matching surge found

    for start_idx in range(4, len(weekly) - (min_surge_weeks - 1)):
        # Ensure at least 3 weeks from start_idx onwards
        # Base period: 4 weeks before start_idx
        base_start = start_idx - 4
        base_volumes = [weekly[j]["volume"] for j in range(base_start, start_idx)]
        avg_base_vol = sum(base_volumes) / len(base_volumes) if base_volumes else 0

        if avg_base_vol <= 0:
            continue

        # Try to extend consecutive surge weeks as far as possible
        consecutive = 0
        up_weeks = 0
        surge_start_price = weekly[start_idx]["open"]
        surge_end_idx = start_idx

        for j in range(start_idx, len(weekly)):
            w = weekly[j]
            vol_ratio = w["volume"] / avg_base_vol if avg_base_vol > 0 else 0

            if vol_ratio >= surge_vol_ratio:
                consecutive += 1
                surge_end_idx = j
                if w["close"] > w["open"]:
                    up_weeks += 1
            else:
                break  # streak broken

        if consecutive < min_surge_weeks:
            continue

        # Check up-week ratio: at least 2 out of first 3 must be up
        # Check across all consecutive weeks: at least 2/3 must be up
        up_count_first3 = 0
        for j in range(start_idx, min(start_idx + 3, start_idx + consecutive)):
            w = weekly[j]
            if w["close"] > w["open"]:
                up_count_first3 += 1

        if up_count_first3 < 2:
            continue

        # Cumulative price gain over the surge period
        surge_open = weekly[start_idx]["open"]
        surge_close = weekly[surge_end_idx]["close"]
        if surge_open <= 0:
            continue
        surge_gain_pct = (surge_close - surge_open) / surge_open * 100

        if surge_gain_pct < min_surge_gain or surge_gain_pct > max_surge_gain:
            continue

        # This is a valid surge. Pick the one with the most weeks / highest gain
        if best_surge is None or consecutive > best_surge["weeks"] or (
            consecutive == best_surge["weeks"] and surge_gain_pct > best_surge["gain"]
        ):
            # Compute max volume ratio during this surge
            max_vol_r = 0.0
            for jj in range(start_idx, surge_end_idx + 1):
                vr = weekly[jj]["volume"] / avg_base_vol if avg_base_vol > 0 else 0
                if vr > max_vol_r:
                    max_vol_r = vr
            best_surge = {
                "start_idx": start_idx,
                "end_idx": surge_end_idx,
                "weeks": consecutive,
                "up_weeks": up_weeks,
                "gain": surge_gain_pct,
                "surge_open": surge_open,
                "surge_close": surge_close,
                "avg_base_vol": avg_base_vol,
                "max_vol_ratio": max_vol_r,
            }

    if best_surge is None:
        return result

    # ---- Step D: Check pullback from peak + count pullback weeks ----
    # Count weeks after surge ended
    weeks_after_surge = len(weekly) - 1 - best_surge["end_idx"]
    if weeks_after_surge < min_pullback_weeks:
        return result  # not enough time has passed for pullback

    # Find the peak price from surge start to the end of available data
    peak_price = 0.0
    for j in range(best_surge["start_idx"], len(weekly)):
        w = weekly[j]
        if w["high"] is not None and w["high"] > peak_price:
            peak_price = w["high"]
        if w["close"] > peak_price:
            peak_price = w["close"]

    current_close = daily_klines[-1][2]  # latest daily close
    if peak_price <= 0:
        return result

    pullback_pct = (peak_price - current_close) / peak_price * 100

    # We require some pullback: current price must be below peak
    has_pullback = pullback_pct > min_pullback_pct

    # ---- Step E: Volume contraction checks ----
    daily_volumes = [k[5] for k in daily_klines if k[5] is not None and k[5] > 0]

    if len(daily_volumes) < 10:
        return result

    vol_window = daily_volumes[-lookback_days:] if len(daily_volumes) >= lookback_days else daily_volumes[:]
    recent_vols = daily_volumes[-recent_vol_days:] if len(daily_volumes) >= recent_vol_days else daily_volumes[:]
    min_daily_vol_val = min(vol_window)
    latest_daily_vol_val = daily_volumes[-1] if daily_volumes else 0
    recent_5d_total = sum(recent_vols)

    # Compute min weekly volume in the entire lookback window (all weeks)
    min_weekly_vol_val = min(w["volume"] for w in weekly) if weekly else 0

    # Check 1: last-5-day total volume vs min weekly volume in lookback
    ratio_5d_vs_minweek = recent_5d_total / min_weekly_vol_val if min_weekly_vol_val > 0 else float("inf")
    check1_ok = ratio_5d_vs_minweek <= recent_5d_vs_minweek_max

    # Check 2: latest single day volume vs min daily volume in lookback
    ratio_latest_vs_minday = latest_daily_vol_val / min_daily_vol_val if min_daily_vol_val > 0 else float("inf")
    check2_ok = ratio_latest_vs_minday <= latest_vs_minday_max

    is_low_volume = check1_ok and check2_ok

    # Also compute percentile for reference (kept for scoring)
    avg_recent = sum(recent_vols) / len(recent_vols)
    sorted_vols = sorted(vol_window)
    rank = 0
    for sv in sorted_vols:
        if sv <= avg_recent:
            rank += 1
        else:
            break
    vol_percentile = (rank / len(sorted_vols)) * 100 if sorted_vols else 100.0

    # ---- Compute pullback min weekly volume ----
    pullback_min_wvol = None
    if best_surge["end_idx"] + 1 < len(weekly):
        pullback_weeks_data = weekly[best_surge["end_idx"] + 1:]
        if pullback_weeks_data:
            pullback_min_wvol = min(w["volume"] for w in pullback_weeks_data)

    # ---- Step F: Compute composite score (0-100) ----
    score = 0.0

    # Sub-score 1: Surge quality (0-30 points)
    # More consecutive weeks = better, up to 5
    weeks_score = min(best_surge["weeks"], 5) / 5.0 * 15
    # Higher up-week ratio = better
    up_ratio = best_surge["up_weeks"] / best_surge["weeks"] if best_surge["weeks"] > 0 else 0
    up_score = up_ratio * 15
    score += weeks_score + up_score

    # Sub-score 2: Surge gain quality (0-20 points)
    # Moderate gain (10-20%) is ideal; too small or too large is less ideal
    gain = best_surge["gain"]
    if 8 <= gain <= 25:
        gain_score = 20.0
    elif 5 <= gain < 8:
        gain_score = 15.0
    elif 2 <= gain < 5:
        gain_score = 10.0
    elif 25 < gain <= 30:
        gain_score = 12.0
    else:
        gain_score = 5.0
    score += gain_score

    # Sub-score 3: Pullback quality (0-25 points)
    # Ideal pullback: 5%-20% from peak
    if has_pullback:
        if 5 <= pullback_pct <= 20:
            pullback_score = 25.0
        elif 3 <= pullback_pct < 5:
            pullback_score = 18.0
        elif 20 < pullback_pct <= 30:
            pullback_score = 15.0
        elif 1 < pullback_pct < 3:
            pullback_score = 10.0
        else:
            pullback_score = 5.0
    else:
        pullback_score = 0.0
    score += pullback_score

    # Sub-score 4: Volume contraction quality (0-25 points)
    if is_low_volume:
        # Lower percentile = better contraction
        if vol_percentile <= 5:
            vol_score = 25.0
        elif vol_percentile <= 10:
            vol_score = 22.0
        elif vol_percentile <= 15:
            vol_score = 18.0
        else:
            vol_score = 15.0
    else:
        # Partial credit if below 40th percentile
        if vol_percentile <= 30:
            vol_score = 10.0
        elif vol_percentile <= 40:
            vol_score = 5.0
        else:
            vol_score = 0.0
    score += vol_score

    result = {
        "score": round(min(score, 100.0), 1),
        "surge_weeks": best_surge["weeks"],
        "surge_gain": round(best_surge["gain"], 2),
        "pullback_weeks": weeks_after_surge,
        "recent_vol_percentile": round(vol_percentile, 1),
        "peak_price": round(peak_price, 2),
        "pullback_pct": round(pullback_pct, 2),
        "base_weekly_vol": round(best_surge["avg_base_vol"], 0),
        "max_surge_vol_ratio": round(best_surge["max_vol_ratio"], 2),
        "pullback_min_weekly_vol": round(pullback_min_wvol, 0) if pullback_min_wvol is not None else None,
        "min_daily_vol": round(min_daily_vol_val, 0),
        "latest_daily_vol": round(latest_daily_vol_val, 0),
        "recent_5d_total_vol": round(recent_5d_total, 0),
        "recent_5d_vs_minweek": round(ratio_5d_vs_minweek, 2),
        "latest_vs_minday": round(ratio_latest_vs_minday, 2),
    }

    return result


async def run_screen(db: AsyncSession, criteria: ScreenerCriteria) -> tuple[list[ScreenerResult], int]:
    """
    Execute stock screening with given criteria.
    Returns (results, total_count).
    """
    # Step 1: Fundamental filter — get candidate stock IDs
    conditions = []
    params = {}

    if criteria.industries:
        conditions.append("sp.industry = ANY(:industries)")
        params["industries"] = criteria.industries

    if criteria.exclude_industries:
        conditions.append("NOT (sp.industry = ANY(:exclude_industries))")
        params["exclude_industries"] = criteria.exclude_industries

    if criteria.min_market_cap is not None:
        conditions.append("sp.total_market_cap >= :min_mcap")
        params["min_mcap"] = criteria.min_market_cap

    if criteria.max_market_cap is not None:
        conditions.append("sp.total_market_cap <= :max_mcap")
        params["max_mcap"] = criteria.max_market_cap

    if criteria.min_pe is not None:
        conditions.append("sp.pe_ttm >= :min_pe")
        params["min_pe"] = criteria.min_pe

    if criteria.max_pe is not None:
        conditions.append("sp.pe_ttm <= :max_pe")
        params["max_pe"] = criteria.max_pe

    if criteria.min_pb is not None:
        conditions.append("sp.pb >= :min_pb")
        params["min_pb"] = criteria.min_pb

    if criteria.max_pb is not None:
        conditions.append("sp.pb <= :max_pb")
        params["max_pb"] = criteria.max_pb

    where_clause = " AND ".join(conditions) if conditions else "TRUE"

    # Count total matching
    count_sql = f"""
        SELECT COUNT(*) FROM stocks s
        JOIN stock_profiles sp ON s.id = sp.stock_id
        WHERE {where_clause}
    """
    count_result = await db.execute(text(count_sql), params)
    total_count = count_result.scalar()

    # Get candidates with profile data
    candidates_sql = f"""
        SELECT s.id, s.code, s.name, sp.industry,
               sp.total_market_cap, sp.circ_market_cap, sp.pe_ttm, sp.pb
        FROM stocks s
        JOIN stock_profiles sp ON s.id = sp.stock_id
        WHERE {where_clause}
        ORDER BY sp.total_market_cap DESC NULLS LAST
    """
    candidates_result = await db.execute(text(candidates_sql), params)
    candidates = candidates_result.all()

    if not candidates:
        return [], 0

    # Step 2: Compute technical indicators for candidates
    results = []
    stock_ids = [c[0] for c in candidates]

    # Determine lookback: need enough data for volume pattern (120 days) or
    # other technical indicators
    lookback = max(
        criteria.price_percentile_days,
        criteria.volume_surge_base_days + criteria.volume_surge_recent_days,
        int(criteria.vp_lookback_months * 20) if criteria.enable_volume_pattern else 0,
    )
    cutoff_date = date.today() - timedelta(days=int(lookback * 1.6))  # ~1.6x for weekends/holidays

    # Batch-fetch recent klines for all candidates
    # Include open, high, low for weekly aggregation in volume pattern
    klines_sql = """
        SELECT stock_id, date, open, close, high, low, volume, turnover_rate
        FROM daily_klines
        WHERE stock_id = ANY(:sids) AND date >= :cutoff
        ORDER BY stock_id, date
    """
    klines_result = await db.execute(
        text(klines_sql), {"sids": stock_ids, "cutoff": cutoff_date}
    )
    klines_rows = klines_result.all()

    # Group by stock_id
    stock_klines = defaultdict(list)
    for row in klines_rows:
        stock_klines[row[0]].append(row)

    # Compute indicators per stock
    tech_data = {}
    pattern_data = {}
    for stock_id, klines in stock_klines.items():
        if len(klines) < 20:
            continue

        closes = [k[3] for k in klines]       # index 3 = close
        volumes = [k[6] for k in klines]       # index 6 = volume
        turnover_rates = [k[7] for k in klines if k[7] is not None]

        latest_close = closes[-1]

        # Price percentile (in lookback window)
        lookback_closes = closes[-criteria.price_percentile_days:]
        if lookback_closes:
            min_p = min(lookback_closes)
            max_p = max(lookback_closes)
            if max_p > min_p:
                pct = (latest_close - min_p) / (max_p - min_p) * 100
            else:
                pct = 50.0
        else:
            pct = None

        # Volume surge ratio
        recent_vols = volumes[-criteria.volume_surge_recent_days:]
        base_vols = volumes[-(criteria.volume_surge_base_days + criteria.volume_surge_recent_days):-criteria.volume_surge_recent_days]
        if recent_vols and base_vols:
            avg_recent = sum(recent_vols) / len(recent_vols)
            avg_base = sum(base_vols) / len(base_vols)
            vol_ratio = avg_recent / avg_base if avg_base > 0 else None
        else:
            vol_ratio = None

        # Average turnover rate (recent 5 days)
        recent_tr = turnover_rates[-5:] if turnover_rates else []
        avg_tr = sum(recent_tr) / len(recent_tr) if recent_tr else None

        # Weekly change percent (last 5 trading days)
        if len(closes) >= 6:
            weekly_chg = (closes[-1] - closes[-6]) / closes[-6] * 100
        else:
            weekly_chg = None

        tech_data[stock_id] = {
            "latest_close": latest_close,
            "price_percentile": pct,
            "volume_surge_ratio": vol_ratio,
            "avg_turnover_rate": avg_tr,
            "weekly_change_pct": weekly_chg,
        }

        # Volume pattern detection
        if criteria.enable_volume_pattern:
            # Build tuples: (date, open, close, high, low, volume)
            daily_tuples = [
                (k[1], k[2], k[3], k[4], k[5], k[6]) for k in klines
            ]
            vp_lookback = int(criteria.vp_lookback_months * 20)  # ~20 trading days per month
            pat = detect_volume_pattern(
                daily_tuples,
                lookback_days=vp_lookback,
                min_surge_weeks=criteria.vp_min_surge_weeks,
                surge_vol_ratio=criteria.vp_surge_vol_ratio,
                min_surge_gain=criteria.vp_min_surge_gain,
                max_surge_gain=criteria.vp_max_surge_gain,
                min_pullback_pct=criteria.vp_min_pullback_pct,
                min_pullback_weeks=criteria.vp_min_pullback_weeks,
                recent_vol_days=criteria.vp_recent_vol_days,
                recent_5d_vs_minweek_max=criteria.vp_recent_5d_vs_minweek_max,
                latest_vs_minday_max=criteria.vp_latest_vs_minday_max,
            )
            pattern_data[stock_id] = pat

    # Step 3: Apply technical filters and build results
    for c in candidates:
        stock_id = c[0]
        tech = tech_data.get(stock_id, {})

        # Apply technical filters
        if criteria.min_price_percentile is not None:
            pct = tech.get("price_percentile")
            if pct is None or pct < criteria.min_price_percentile:
                continue

        if criteria.max_price_percentile is not None:
            pct = tech.get("price_percentile")
            if pct is None or pct > criteria.max_price_percentile:
                continue

        if criteria.min_volume_surge_ratio is not None:
            ratio = tech.get("volume_surge_ratio")
            if ratio is None or ratio < criteria.min_volume_surge_ratio:
                continue

        if criteria.max_volume_contraction_ratio is not None:
            ratio = tech.get("volume_surge_ratio")
            if ratio is None or ratio > criteria.max_volume_contraction_ratio:
                continue

        # Volume pattern filter: require score >= min_score when enabled
        pat = pattern_data.get(stock_id) if criteria.enable_volume_pattern else None
        if criteria.enable_volume_pattern:
            if pat is None or pat["score"] < criteria.vp_min_score:
                continue
            # Hard filter: latest_vs_minday must be within user-specified max
            if pat.get("latest_vs_minday") is not None and pat["latest_vs_minday"] > criteria.vp_latest_vs_minday_max:
                continue
            # Hard filter: recent_5d_vs_minweek must be within user-specified max
            if pat.get("recent_5d_vs_minweek") is not None and pat["recent_5d_vs_minweek"] > criteria.vp_recent_5d_vs_minweek_max:
                continue

        results.append(ScreenerResult(
            stock_id=c[0],
            code=c[1],
            name=c[2],
            industry=c[3],
            total_market_cap=c[4],
            circ_market_cap=c[5],
            pe_ttm=c[6],
            pb=c[7],
            latest_close=tech.get("latest_close"),
            price_percentile=tech.get("price_percentile"),
            volume_surge_ratio=tech.get("volume_surge_ratio"),
            avg_turnover_rate=tech.get("avg_turnover_rate"),
            weekly_change_pct=tech.get("weekly_change_pct"),
            volume_pattern_score=pat["score"] if pat else None,
            surge_weeks_count=pat["surge_weeks"] if pat else None,
            surge_price_gain=pat["surge_gain"] if pat else None,
            pullback_weeks=pat["pullback_weeks"] if pat else None,
            pullback_pct=pat["pullback_pct"] if pat else None,
            recent_vol_percentile=pat["recent_vol_percentile"] if pat else None,
            base_weekly_vol=pat["base_weekly_vol"] if pat else None,
            max_surge_vol_ratio=pat["max_surge_vol_ratio"] if pat else None,
            pullback_min_weekly_vol=pat["pullback_min_weekly_vol"] if pat else None,
            min_daily_vol=pat["min_daily_vol"] if pat else None,
            latest_daily_vol=pat["latest_daily_vol"] if pat else None,
            recent_5d_total_vol=pat["recent_5d_total_vol"] if pat else None,
            recent_5d_vs_minweek=pat["recent_5d_vs_minweek"] if pat else None,
            latest_vs_minday=pat["latest_vs_minday"] if pat else None,
        ))

    # Step 4: Sort
    sort_key_map = {
        "market_cap_desc": lambda r: -(r.total_market_cap or 0),
        "market_cap_asc": lambda r: (r.total_market_cap or float("inf")),
        "pe_asc": lambda r: (r.pe_ttm or float("inf")),
        "pe_desc": lambda r: -(r.pe_ttm or 0),
        "pb_asc": lambda r: (r.pb or float("inf")),
        "pb_desc": lambda r: -(r.pb or 0),
        "price_pct_asc": lambda r: (r.price_percentile if r.price_percentile is not None else float("inf")),
        "price_pct_desc": lambda r: -(r.price_percentile if r.price_percentile is not None else 0),
        "volume_surge_desc": lambda r: -(r.volume_surge_ratio or 0),
        "pattern_score_desc": lambda r: -(r.volume_pattern_score or 0),
    }
    sort_fn = sort_key_map.get(criteria.sort_by, sort_key_map["market_cap_desc"])
    results.sort(key=sort_fn)

    # Paginate
    total_filtered = len(results)
    paginated = results[criteria.offset:criteria.offset + criteria.limit]

    return paginated, total_filtered


# --- Preset Strategies ---

PRESET_STRATEGIES = {
    "value_largecap": {
        "name": "大盘价值股",
        "description": "大市值 + 低PE + 低PB，适合稳健投资",
        "criteria": {
            "min_market_cap": 500,
            "max_pe": 15,
            "max_pb": 2,
            "sort_by": "market_cap_desc",
        },
    },
    "growth_midcap": {
        "name": "中盘成长股",
        "description": "中等市值 + 合理估值，寻找成长空间",
        "criteria": {
            "min_market_cap": 100,
            "max_market_cap": 1000,
            "min_pe": 10,
            "max_pe": 40,
            "sort_by": "market_cap_desc",
        },
    },
    "low_position": {
        "name": "底部区域股",
        "description": "价格处于一年内低位(30%以下)，潜在底部反转",
        "criteria": {
            "min_market_cap": 50,
            "max_price_percentile": 30,
            "price_percentile_days": 250,
            "sort_by": "price_pct_asc",
        },
    },
    "volume_breakout": {
        "name": "放量突破",
        "description": "近5日成交量是过去60日的2倍以上，可能突破信号",
        "criteria": {
            "min_market_cap": 50,
            "min_volume_surge_ratio": 2.0,
            "volume_surge_recent_days": 5,
            "volume_surge_base_days": 60,
            "sort_by": "volume_surge_desc",
        },
    },
    "quiet_accumulation": {
        "name": "缩量蓄势",
        "description": "成交量萎缩至历史低位，可能在主力吸筹阶段",
        "criteria": {
            "min_market_cap": 50,
            "max_volume_contraction_ratio": 0.5,
            "sort_by": "market_cap_desc",
        },
    },
    "baijiu_sector": {
        "name": "白酒板块",
        "description": "酿酒行业全部股票",
        "criteria": {
            "industries": ["酿酒行业"],
            "sort_by": "market_cap_desc",
        },
    },
    "bank_sector": {
        "name": "银行金融",
        "description": "银行/金融/券商/保险 低PB",
        "criteria": {
            "industries": ["银行", "金融行业", "非银金融", "多元金融", "券商信托", "金融"],
            "max_pb": 1.5,
            "sort_by": "pb_asc",
        },
    },
    "tech_sector": {
        "name": "科技硬件",
        "description": "半导体/电子/通信设备/计算机设备",
        "criteria": {
            "industries": [
                "半导体", "电子元件", "通信设备", "光学光电子", "消费电子",
                "电子信息", "电子器件", "电子设备", "计算机设备", "电子化学品",
                "安防设备",
            ],
            "sort_by": "market_cap_desc",
        },
    },
    "new_energy_sector": {
        "name": "新能源",
        "description": "电力设备/电池/光伏/风电/新能源汽车",
        "criteria": {
            "industries": [
                "电力设备", "电网设备", "输配电气", "电源设备", "电气设备",
                "电机", "汽车零部件", "电池", "光伏设备", "风电设备",
                "能源金属",
            ],
            "sort_by": "market_cap_desc",
        },
    },
    "pharma_sector": {
        "name": "医药生物",
        "description": "医药制造/中药/生物制品/医疗器械/医疗服务",
        "criteria": {
            "industries": [
                "中药", "化学制药", "生物制品", "医疗器械", "医疗服务",
                "医药商业", "医药制造", "生物制药", "医药生物", "医疗行业",
            ],
            "sort_by": "market_cap_desc",
        },
    },
}


def get_preset_strategies() -> dict:
    """Return all preset strategies."""
    return PRESET_STRATEGIES


def criteria_from_dict(d: dict) -> ScreenerCriteria:
    """Create ScreenerCriteria from a dict (e.g., from API request)."""
    return ScreenerCriteria(**{k: v for k, v in d.items() if k in ScreenerCriteria.__dataclass_fields__})
