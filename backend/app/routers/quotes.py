from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, delete, func
from app.database import get_db
from app.models import Stock, QuoteSnapshot, DailyKline, FetchLog, StockProfile
from app.schemas import QuoteSnapshotOut, DailyKlineOut, FetchLogOut, StockProfileOut
from app.config import settings
from datetime import date
from typing import Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
#  Helper: resolve stock by id or code
# ---------------------------------------------------------------------------

async def _resolve_stock(db: AsyncSession, stock_id: Optional[int] = None, code: Optional[str] = None):
    """Resolve a Stock record by id or code. Returns Stock or raises 404."""
    if stock_id:
        result = await db.execute(select(Stock).where(Stock.id == stock_id))
    elif code:
        result = await db.execute(select(Stock).where(Stock.code == code))
    else:
        raise HTTPException(status_code=400, detail="stock_id or code required")
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return stock


# ---------------------------------------------------------------------------
#  Unified realtime quote (transient, not stored)
# ---------------------------------------------------------------------------

@router.get(
    "/realtime/{stock_id}",
    summary="Fetch realtime quote for a stock (transient, not stored)",
)
async def get_realtime_quote(
    stock_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch a live realtime quote for any stock via the aggregator.
    Returns transient data — nothing is written to the database.
    Used by both the quotes page and the strategy trading page.
    """
    stock = await _resolve_stock(db, stock_id=stock_id)
    return await _fetch_realtime(stock, db)


@router.get(
    "/realtime-by-code/{code}",
    summary="Fetch realtime quote by stock code",
)
async def get_realtime_quote_by_code(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """Fetch realtime quote by stock code (e.g. 600519). Same as /realtime/{id} but by code."""
    stock = await _resolve_stock(db, code=code)
    return await _fetch_realtime(stock, db)


async def _fetch_realtime(stock: Stock, db: AsyncSession):
    from app.services.aggregator import StockDataAggregator

    agg = StockDataAggregator(
        priority=settings.DATA_SOURCE_PRIORITY,
        timeout=settings.DATA_SOURCE_TIMEOUT,
        tushare_token=settings.TUSHARE_TOKEN,
    )
    try:
        quote = await agg.get_realtime_quote(stock.code, stock.market)
    except Exception as e:
        logger.warning("Realtime quote failed for %s: %s", stock.code, e)
        return {"error": f"行情获取失败: {e}", "source": None}
    finally:
        await agg.close()

    if not quote or quote.get("price", 0) <= 0:
        return {"error": "行情数据为空", "source": agg.last_source}

    return {
        "stock_id": stock.id,
        "code": stock.code,
        "name": quote.get("name") or stock.name,
        "price": quote.get("price"),
        "open": quote.get("open"),
        "high": quote.get("high"),
        "low": quote.get("low"),
        "close": quote.get("close"),
        "prev_close": quote.get("prev_close") or quote.get("yesterday_close"),
        "volume": quote.get("volume"),
        "amount": quote.get("amount"),
        "change_pct": quote.get("change_pct"),
        "turnover_rate": quote.get("turnover_rate"),
        "timestamp": quote.get("timestamp"),
        "source": agg.last_source,
    }


@router.get(
    "/intraday/{stock_id}",
    summary="Fetch intraday minute klines for a stock",
)
async def get_intraday_klines(
    stock_id: int,
    scale: int = Query(default=5, ge=1, le=60),
    limit: int = Query(default=240, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch intraday minute-level K-line data for any stock via Sina.
    Unified endpoint used by both quotes page and strategy page.
    """
    stock = await _resolve_stock(db, stock_id=stock_id)
    return await _fetch_intraday(stock, scale, limit)


@router.get(
    "/intraday-by-code/{code}",
    summary="Fetch intraday minute klines by stock code",
)
async def get_intraday_klines_by_code(
    code: str,
    scale: int = Query(default=5, ge=1, le=60),
    limit: int = Query(default=240, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Fetch intraday klines by stock code. Same as /intraday/{id} but by code."""
    stock = await _resolve_stock(db, code=code)
    return await _fetch_intraday(stock, scale, limit)


async def _fetch_intraday(stock: Stock, scale: int, limit: int):
    from app.services.sina import SinaClient

    client = SinaClient(timeout=settings.DATA_SOURCE_TIMEOUT)
    try:
        klines = await client.get_minute_klines(
            stock.code, stock.market, scale=scale, limit=limit,
        )
    except Exception as e:
        logger.warning("Intraday klines failed for %s: %s", stock.code, e)
        return {"klines": [], "error": str(e)}
    finally:
        await client.close()

    return {
        "stock_id": stock.id,
        "stock_code": stock.code,
        "stock_name": stock.name,
        "market": stock.market,
        "scale": scale,
        "klines": klines,
    }


@router.get(
    "/snapshots/{stock_id}",
    response_model=list[QuoteSnapshotOut],
    summary="Get recent quote snapshots",
)
async def get_snapshots(
    stock_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent quote snapshots for a given stock, ordered by timestamp descending."""
    query = (
        select(QuoteSnapshot)
        .where(QuoteSnapshot.stock_id == stock_id)
        .order_by(desc(QuoteSnapshot.timestamp))
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/klines/{stock_id}",
    response_model=list[DailyKlineOut],
    summary="Get daily klines",
)
async def get_klines(
    stock_id: int,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    limit: int = Query(default=2000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Return daily kline records for a given stock, with optional date range filtering."""
    query = select(DailyKline).where(DailyKline.stock_id == stock_id)

    if start_date is not None:
        query = query.where(DailyKline.date >= start_date)
    if end_date is not None:
        query = query.where(DailyKline.date <= end_date)

    query = query.order_by(desc(DailyKline.date)).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/profile/{stock_id}",
    response_model=StockProfileOut,
    summary="Get stock profile (fundamentals)",
)
async def get_stock_profile(
    stock_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return fundamental data (industry, PE, PB, market cap) for a stock."""
    result = await db.execute(
        select(StockProfile).where(StockProfile.stock_id == stock_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found for this stock")
    return profile


@router.get(
    "/logs",
    summary="Get fetch logs",
)
async def get_fetch_logs(
    stock_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Return fetch log entries with stock code/name, optionally filtered by stock_id."""
    query = (
        select(
            FetchLog.id,
            FetchLog.stock_id,
            Stock.code.label("stock_code"),
            Stock.name.label("stock_name"),
            FetchLog.fetch_type,
            FetchLog.source,
            FetchLog.status,
            FetchLog.message,
            FetchLog.created_at,
        )
        .outerjoin(Stock, FetchLog.stock_id == Stock.id)
    )

    if stock_id is not None:
        query = query.where(FetchLog.stock_id == stock_id)

    query = query.order_by(desc(FetchLog.created_at)).limit(limit)

    result = await db.execute(query)
    rows = result.all()
    return [
        {
            "id": r.id,
            "stock_id": r.stock_id,
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "fetch_type": r.fetch_type,
            "source": r.source,
            "status": r.status,
            "message": r.message,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.delete(
    "/logs/errors",
    summary="Delete all error fetch logs",
)
async def delete_error_logs(
    db: AsyncSession = Depends(get_db),
):
    """Delete all fetch log entries with status='error'."""
    result = await db.execute(
        delete(FetchLog).where(FetchLog.status == "error")
    )
    await db.commit()
    count = result.rowcount
    return {"ok": True, "message": f"已删除 {count} 条错误日志", "deleted": count}


@router.delete(
    "/logs/all",
    summary="Delete all fetch logs",
)
async def delete_all_logs(
    db: AsyncSession = Depends(get_db),
):
    """Delete ALL fetch log entries."""
    result = await db.execute(delete(FetchLog))
    await db.commit()
    count = result.rowcount
    return {"ok": True, "message": f"已删除全部 {count} 条日志", "deleted": count}
