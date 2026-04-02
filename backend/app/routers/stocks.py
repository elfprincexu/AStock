from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.database import get_db
from app.models import Stock, QuoteSnapshot, DailyKline, FetchLog
from app.schemas import StockCreate, StockOut, StockUpdate, StockEnrichedOut, StockListResponse
from app.services.aggregator import StockDataAggregator
from app.services.base import safe_int, safe_float
from app.config import settings
from datetime import datetime, date
from app.utils import latest_expected_trading_date, is_valid_industry
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


async def _is_duplicate_snapshot(db: AsyncSession, stock_id: int, price: float, volume: int) -> bool:
    """Check if the latest snapshot for this stock already has the same price+volume."""
    row = (await db.execute(text(
        "SELECT price, volume FROM quote_snapshots "
        "WHERE stock_id = :sid ORDER BY created_at DESC LIMIT 1"
    ), {"sid": stock_id})).first()
    return row is not None and row[0] == price and row[1] == volume


def _make_aggregator() -> StockDataAggregator:
    """Create an aggregator configured from application settings."""
    return StockDataAggregator(
        priority=settings.DATA_SOURCE_PRIORITY,
        timeout=settings.DATA_SOURCE_TIMEOUT,
        tushare_token=settings.TUSHARE_TOKEN,
    )


def _code_to_market_prefix(code: str, market: str = "") -> str:
    """Convert stock code + market to market prefix for Tencent API."""
    m = market.upper()
    if m == "SH":
        return "sh"
    if m == "BJ":
        return "bj"
    if m == "SZ":
        return "sz"
    # Fallback: infer from code pattern
    if code.startswith(("60", "68")):
        return "sh"
    if code.startswith(("4", "8", "92")):
        return "bj"
    return "sz"


async def _fetch_tencent_fundamentals(code: str, market: str = "") -> dict | None:
    """Fetch PE/PB/market cap from Tencent Finance HTTP API."""
    prefix = _code_to_market_prefix(code, market)
    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.qq.com/",
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10)
        text_data = resp.content.decode("gbk", errors="replace")
        for line in text_data.strip().split("\n"):
            if "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) < 50:
                continue
            name = parts[1].strip()
            pe = float(parts[39]) if parts[39].strip() else None
            pb = float(parts[46]) if parts[46].strip() else None
            total_mcap = float(parts[45]) if parts[45].strip() else None
            circ_mcap = float(parts[44]) if parts[44].strip() else None
            return {
                "name": name, "pe_ttm": pe, "pb": pb,
                "total_market_cap": total_mcap, "circ_market_cap": circ_mcap,
            }
    except Exception as e:
        logger.warning("Tencent fundamental fetch failed for %s: %s", code, e)
    return None


async def _upsert_profile(db: AsyncSession, stock_id: int, data: dict):
    """Upsert stock_profiles row with fundamental data."""
    await db.execute(text("""
        INSERT INTO stock_profiles (stock_id, pe_ttm, pb, total_market_cap, circ_market_cap, updated_at)
        VALUES (:sid, :pe, :pb, :tmcap, :cmcap, NOW())
        ON CONFLICT (stock_id) DO UPDATE SET
            pe_ttm = EXCLUDED.pe_ttm,
            pb = EXCLUDED.pb,
            total_market_cap = EXCLUDED.total_market_cap,
            circ_market_cap = EXCLUDED.circ_market_cap,
            updated_at = NOW()
    """), {
        "sid": stock_id,
        "pe": data.get("pe_ttm"),
        "pb": data.get("pb"),
        "tmcap": data.get("total_market_cap"),
        "cmcap": data.get("circ_market_cap"),
    })


async def _fetch_industry_em(code: str) -> str | None:
    """Try to get industry from EastMoney individual stock info (may fail if rate-limited)."""
    try:
        import akshare as ak
        import asyncio
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(None, lambda: ak.stock_individual_info_em(symbol=code))
        if df is not None and not df.empty:
            info = dict(zip(df["item"], df["value"]))
            industry = info.get("行业", "")
            if is_valid_industry(industry):
                return industry
            return None
    except Exception:
        pass
    return None


# ── Static paths first (before /{stock_id} to avoid path conflicts) ──────────


@router.get("/search", summary="Search stocks")
async def search_stocks(keyword: str = Query(..., min_length=1)):
    """Search stocks by keyword (code or name). Uses EastMoney search API."""
    agg = _make_aggregator()
    try:
        return await agg.search_stock(keyword)
    finally:
        await agg.close()


@router.post("/fetch-all", summary="Fetch data for all active stocks")
async def fetch_all_stocks(db: AsyncSession = Depends(get_db)):
    """Trigger full data fetch for every active stock in the watchlist.

    Each stock goes through the unified 4-step pipeline:
    1. Realtime quote → QuoteSnapshot
    2. Daily klines (gap-aware) → DailyKline
    3. Fundamentals (PE/PB/market cap) from Tencent → stock_profiles
    4. Industry from EastMoney (best effort) → stock_profiles
    """
    result = await db.execute(select(Stock).where(Stock.is_active == True))
    stocks = result.scalars().all()

    if not stocks:
        return {"ok": True, "message": "No active stocks to fetch"}

    agg = _make_aggregator()
    total_klines = 0
    total_quotes = 0
    errors: list[str] = []

    try:
        for stock in stocks:
            try:
                details = []
                quote_source = None
                kline_source = None
                fundamental_ok = False
                industry_ok = False

                # 1. Realtime quote
                try:
                    quote_data = await agg.get_realtime_quote(stock.code, stock.market)
                    quote_source = agg.last_source
                    if quote_data:
                        # ── Delisted stock detection ──
                        quote_name = quote_data.get("name", "")
                        if quote_name and "退" in quote_name and "退" not in (stock.name or ""):
                            stock.is_active = False
                            stock.name = quote_name
                            details.append(f"检测到退市({quote_name}) -> 已停用")
                            logger.info("Stock %s detected as delisted (%s), deactivated", stock.code, quote_name)

                        q_price = safe_float(quote_data.get("price", 0.0))
                        q_volume = safe_int(quote_data.get("volume", 0))
                        if not await _is_duplicate_snapshot(db, stock.id, q_price, q_volume):
                            snapshot = QuoteSnapshot(
                                stock_id=stock.id,
                                price=q_price,
                                open=safe_float(quote_data.get("open", 0.0)),
                                high=safe_float(quote_data.get("high", 0.0)),
                                low=safe_float(quote_data.get("low", 0.0)),
                                close=safe_float(quote_data.get("close", 0.0)),
                                volume=q_volume,
                                amount=safe_float(quote_data.get("amount", 0.0)),
                                change_pct=safe_float(quote_data.get("change_pct", 0.0)),
                                timestamp=quote_data.get("timestamp", datetime.utcnow()),
                            )
                            db.add(snapshot)
                            total_quotes += 1
                            details.append(f"行情({quote_source})")
                        else:
                            details.append(f"行情({quote_source},无变化跳过)")
                except Exception as e:
                    details.append(f"行情失败: {str(e)[:50]}")

                # 2. Daily klines (gap-aware)
                try:
                    row = (await db.execute(text(
                        "SELECT max(date) FROM daily_klines WHERE stock_id = :sid"
                    ), {"sid": stock.id})).scalar()
                    latest_kline_date = row
                    expected_date = latest_expected_trading_date()

                    if latest_kline_date and latest_kline_date >= expected_date:
                        details.append("K线已是最新")
                    else:
                        if latest_kline_date is None:
                            limit = settings.KLINE_INITIAL_LIMIT
                        else:
                            gap_days = (expected_date - latest_kline_date).days
                            limit = min(gap_days + 10, 500)

                        klines = await agg.get_daily_klines(stock.code, stock.market, limit=limit)
                        kline_source = agg.last_source if klines else None
                        if klines:
                            records = [
                                {
                                    "stock_id": stock.id,
                                    "date": k["date"],
                                    "open": safe_float(k["open"]),
                                    "high": safe_float(k["high"]),
                                    "low": safe_float(k["low"]),
                                    "close": safe_float(k["close"]),
                                    "volume": safe_int(k["volume"]),
                                    "amount": safe_float(k["amount"]),
                                    "change_pct": safe_float(k["change_pct"]),
                                    "turnover_rate": k.get("turnover_rate"),
                                }
                                for k in klines
                            ]
                            stmt = pg_insert(DailyKline).values(records)
                            stmt = stmt.on_conflict_do_update(
                                index_elements=["stock_id", "date"],
                                set_={
                                    col: stmt.excluded[col]
                                    for col in [
                                        "open", "high", "low", "close",
                                        "volume", "amount", "change_pct", "turnover_rate",
                                    ]
                                },
                            )
                            await db.execute(stmt)
                            total_klines += len(klines)
                            details.append(f"K线{len(klines)}条({kline_source})")
                        else:
                            details.append("K线无数据")
                except Exception as e:
                    details.append(f"K线失败: {str(e)[:50]}")

                # 3. Fundamentals from Tencent
                try:
                    fund_data = await _fetch_tencent_fundamentals(stock.code, stock.market)
                    if fund_data:
                        await _upsert_profile(db, stock.id, fund_data)
                        if fund_data.get("name") and stock.name in (stock.code, "", None):
                            stock.name = fund_data["name"]
                        fundamental_ok = True
                        details.append(f"基本面(tencent,PE={fund_data.get('pe_ttm')},PB={fund_data.get('pb')})")
                except Exception as e:
                    details.append(f"基本面失败: {str(e)[:50]}")

                # 4. Industry (best effort)
                try:
                    existing = await db.execute(text(
                        "SELECT industry FROM stock_profiles WHERE stock_id = :sid"
                    ), {"sid": stock.id})
                    row_ind = existing.first()
                    existing_industry = row_ind[0] if row_ind else None
                    if not is_valid_industry(existing_industry):
                        industry = await _fetch_industry_em(stock.code)
                        if industry:
                            await db.execute(text(
                                "UPDATE stock_profiles SET industry = :ind, updated_at = NOW() WHERE stock_id = :sid"
                            ), {"ind": industry, "sid": stock.id})
                            industry_ok = True
                            details.append(f"行业(eastmoney)={industry}")
                    else:
                        details.append(f"行业已有={existing_industry}")
                except Exception:
                    pass  # industry is best-effort

                # Log
                source_parts = []
                if quote_source:
                    source_parts.append(f"行情:{quote_source}")
                if kline_source:
                    source_parts.append(f"K线:{kline_source}")
                if fundamental_ok:
                    source_parts.append("基本面:tencent")
                if industry_ok:
                    source_parts.append("行业:eastmoney")
                log_entry = FetchLog(
                    stock_id=stock.id,
                    fetch_type="all",
                    source=",".join(source_parts) if source_parts else "unknown",
                    status="success",
                    message=" | ".join(details),
                )
                db.add(log_entry)

            except Exception as e:
                logger.error("Error fetching stock %s: %s", stock.code, e)
                errors.append(f"{stock.code}: {str(e)}")
                log_entry = FetchLog(
                    stock_id=stock.id,
                    fetch_type="all",
                    source=getattr(agg, "last_source", None) or None,
                    status="error",
                    message=str(e),
                )
                db.add(log_entry)
    finally:
        await agg.close()

    await db.commit()

    return {
        "ok": True,
        "message": (
            f"Fetched {total_klines} klines and {total_quotes} realtime quotes "
            f"for {len(stocks)} stocks"
        ),
        "errors": errors if errors else None,
    }


# Async-only data sources for non-blocking fetch operations
_ASYNC_ONLY_PRIORITY = "eastmoney,sina,tencent"


@router.post("/{stock_id}/fetch-lite", summary="Quick fetch (async-only, non-blocking)")
async def fetch_stock_lite(stock_id: int, db: AsyncSession = Depends(get_db)):
    """
    Lightweight data fetch using only async data sources (eastmoney/sina/tencent).
    Fetches realtime quote + daily klines (gap-aware) only (no fundamentals, no industry).
    Designed for batch operations — does not block the event loop.
    """
    result = await db.execute(select(Stock).where(Stock.id == stock_id))
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    agg = StockDataAggregator(
        priority=_ASYNC_ONLY_PRIORITY,
        timeout=settings.DATA_SOURCE_TIMEOUT,
    )
    details = []
    quote_data = None
    quote_source = None
    kline_source = None
    kline_count = 0

    try:
        # 1. Realtime quote
        try:
            quote_data = await agg.get_realtime_quote(stock.code, stock.market)
            quote_source = agg.last_source
            if quote_data:
                # ── Delisted stock detection ──
                quote_name = quote_data.get("name", "")
                if quote_name and "退" in quote_name and "退" not in (stock.name or ""):
                    stock.is_active = False
                    stock.name = quote_name
                    details.append(f"检测到退市({quote_name}) -> 已停用")
                    logger.info("Stock %s detected as delisted (%s), deactivated", stock.code, quote_name)

                q_price = safe_float(quote_data.get("price", 0.0))
                q_volume = safe_int(quote_data.get("volume", 0))
                if await _is_duplicate_snapshot(db, stock.id, q_price, q_volume):
                    details.append(f"行情({quote_source},无变化跳过)")
                else:
                    snapshot = QuoteSnapshot(
                        stock_id=stock.id,
                        price=q_price,
                        open=safe_float(quote_data.get("open", 0.0)),
                        high=safe_float(quote_data.get("high", 0.0)),
                        low=safe_float(quote_data.get("low", 0.0)),
                        close=safe_float(quote_data.get("close", 0.0)),
                        volume=q_volume,
                        amount=safe_float(quote_data.get("amount", 0.0)),
                        change_pct=safe_float(quote_data.get("change_pct", 0.0)),
                        timestamp=quote_data.get("timestamp", datetime.utcnow()),
                    )
                    db.add(snapshot)
                    details.append(f"行情({quote_source})")
        except Exception as e:
            details.append(f"行情失败: {str(e)[:50]}")

        # 2. Daily klines (gap-aware, skip if already up-to-date)
        klines = None
        try:
            row = (await db.execute(text(
                "SELECT max(date) FROM daily_klines WHERE stock_id = :sid"
            ), {"sid": stock.id})).scalar()
            latest_kline_date = row  # date or None
            expected_date = latest_expected_trading_date()

            if latest_kline_date and latest_kline_date >= expected_date:
                details.append("K线已是最新")
            else:
                if latest_kline_date is None:
                    limit = settings.KLINE_INITIAL_LIMIT
                else:
                    gap_days = (expected_date - latest_kline_date).days
                    limit = min(gap_days + 10, 500)

                klines = await agg.get_daily_klines(stock.code, stock.market, limit=limit)
                kline_source = agg.last_source if klines else None
                if klines:
                    records = [
                        {
                            "stock_id": stock.id,
                            "date": k["date"],
                            "open": safe_float(k["open"]),
                            "high": safe_float(k["high"]),
                            "low": safe_float(k["low"]),
                            "close": safe_float(k["close"]),
                            "volume": safe_int(k["volume"]),
                            "amount": safe_float(k["amount"]),
                            "change_pct": safe_float(k["change_pct"]),
                            "turnover_rate": k.get("turnover_rate"),
                        }
                        for k in klines
                    ]
                    stmt = pg_insert(DailyKline).values(records)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["stock_id", "date"],
                        set_={
                            col: stmt.excluded[col]
                            for col in [
                                "open", "high", "low", "close",
                                "volume", "amount", "change_pct", "turnover_rate",
                            ]
                        },
                    )
                    await db.execute(stmt)
                    kline_count = len(klines)
                    details.append(f"K线{kline_count}条({kline_source})")
                else:
                    details.append("K线无数据")
        except Exception as e:
            details.append(f"K线失败: {str(e)[:50]}")

        # 3. Fundamentals from Tencent (PE/PB/market cap)
        fundamental_ok = False
        try:
            fund_data = await _fetch_tencent_fundamentals(stock.code, stock.market)
            if fund_data:
                await _upsert_profile(db, stock.id, fund_data)
                if fund_data.get("name") and stock.name in (stock.code, "", None):
                    stock.name = fund_data["name"]
                fundamental_ok = True
                details.append(f"基本面(tencent,PE={fund_data.get('pe_ttm')},PB={fund_data.get('pb')})")
        except Exception as e:
            details.append(f"基本面失败: {str(e)[:50]}")

        # 4. Industry (best effort, skip if already present)
        industry_ok = False
        try:
            existing = await db.execute(text(
                "SELECT industry FROM stock_profiles WHERE stock_id = :sid"
            ), {"sid": stock.id})
            row_ind = existing.first()
            existing_industry = row_ind[0] if row_ind else None
            if not is_valid_industry(existing_industry):
                industry = await _fetch_industry_em(stock.code)
                if industry:
                    await db.execute(text(
                        "UPDATE stock_profiles SET industry = :ind, updated_at = NOW() WHERE stock_id = :sid"
                    ), {"ind": industry, "sid": stock.id})
                    industry_ok = True
                    details.append(f"行业(eastmoney)={industry}")
            else:
                details.append(f"行业已有={existing_industry}")
        except Exception:
            pass  # industry is best-effort

        # Log (consistent format with manual_full_fetch)
        source_parts = []
        if quote_source:
            source_parts.append(f"行情:{quote_source}")
        if kline_source:
            source_parts.append(f"K线:{kline_source}")
        if fundamental_ok:
            source_parts.append("基本面:tencent")
        if industry_ok:
            source_parts.append("行业:eastmoney")
        if details:
            log_entry = FetchLog(
                stock_id=stock.id,
                fetch_type="batch_lite_fetch",
                source=",".join(source_parts) if source_parts else "unknown",
                status="success",
                message=" | ".join(details),
            )
            db.add(log_entry)

        await db.commit()
    except Exception as e:
        logger.error("Error in fetch-lite for %s: %s", stock.code, e)
        raise HTTPException(status_code=500, detail=f"Fetch failed: {str(e)}")
    finally:
        await agg.close()

    # Build enriched response so frontend can update the row immediately
    enriched = {"ok": True, "message": " | ".join(details)}
    if quote_data:
        enriched["quote"] = {
            "price": quote_data.get("price"),
            "change_pct": quote_data.get("change_pct"),
        }
    if kline_count > 0 and klines:
        enriched["latest_kline_date"] = str(klines[-1]["date"]) if klines[-1].get("date") else None
    return enriched


# ── CRUD endpoints ───────────────────────────────────────────────────────────


@router.get("/enriched", response_model=StockListResponse, summary="List stocks with profile data (paginated)")
async def list_stocks_enriched(
    active_only: bool = False,
    favorites_only: bool = False,
    keyword: str = Query(default="", description="Filter by code or name"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Return stocks with profile data (market cap, PE, PB, industry) and latest kline date, paginated."""
    conditions = ["TRUE"]
    params = {}

    if active_only:
        conditions.append("s.is_active = TRUE")
    if favorites_only:
        conditions.append("s.is_favorite = TRUE")
    if keyword.strip():
        conditions.append("(LOWER(s.code) LIKE :kw OR LOWER(s.name) LIKE :kw)")
        params["kw"] = f"%{keyword.strip().lower()}%"

    where_clause = " AND ".join(conditions)

    count_sql = f"SELECT COUNT(*) FROM stocks s WHERE {where_clause}"
    count_result = await db.execute(text(count_sql), params)
    total = count_result.scalar()

    offset = (page - 1) * page_size
    data_sql = f"""
        SELECT s.id, s.code, s.name, s.market, s.is_active, s.is_favorite, s.created_at,
               sp.industry, sp.total_market_cap, sp.pe_ttm, sp.pb,
               (SELECT MAX(dk.date) FROM daily_klines dk WHERE dk.stock_id = s.id) as latest_kline_date
        FROM stocks s
        LEFT JOIN stock_profiles sp ON s.id = sp.stock_id
        WHERE {where_clause}
        ORDER BY s.id
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = page_size
    params["offset"] = offset
    result = await db.execute(text(data_sql), params)
    rows = result.all()

    items = []
    for r in rows:
        items.append(StockEnrichedOut(
            id=r[0], code=r[1], name=r[2], market=r[3],
            is_active=r[4], is_favorite=r[5], created_at=r[6],
            industry=r[7], total_market_cap=r[8], pe_ttm=r[9], pb=r[10],
            latest_kline_date=r[11],
        ))

    return StockListResponse(total=total, items=items)


@router.get("/", response_model=list[StockOut], summary="List all stocks")
async def list_stocks(
    active_only: bool = False,
    favorites_only: bool = False,
    keyword: str = Query(default="", description="Filter by code or name (server-side)"),
    limit: int = Query(default=0, ge=0, le=10000, description="Max results (0=all)"),
    db: AsyncSession = Depends(get_db),
):
    """Return stocks in the watchlist, optionally filtered by keyword, active, or favorites."""
    query = select(Stock)
    if active_only:
        query = query.where(Stock.is_active == True)
    if favorites_only:
        query = query.where(Stock.is_favorite == True)
    if keyword.strip():
        kw = f"%{keyword.strip().lower()}%"
        query = query.where(
            (Stock.code.ilike(kw)) | (Stock.name.ilike(kw))
        )
    query = query.order_by(Stock.id)
    if limit > 0:
        query = query.limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/", response_model=StockOut, status_code=201, summary="Add a stock")
async def add_stock(
    payload: StockCreate,
    db: AsyncSession = Depends(get_db),
):
    """Add a new stock to the watchlist. Validates via search first."""
    # Optional: validate the stock code
    try:
        agg = _make_aggregator()
        try:
            search_results = await agg.search_stock(payload.code)
            if not search_results:
                logger.warning("Stock code %s not found via search, adding anyway", payload.code)
        finally:
            await agg.close()
    except Exception as e:
        logger.warning("Search validation failed: %s, adding stock anyway", e)

    # Check for duplicate code
    existing = await db.execute(select(Stock).where(Stock.code == payload.code))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail=f"Stock with code {payload.code} already exists")

    stock = Stock(
        code=payload.code,
        name=payload.name,
        market=payload.market,
    )
    db.add(stock)
    await db.commit()
    await db.refresh(stock)
    return stock


@router.get("/{stock_id}", response_model=StockOut, summary="Get a stock by ID")
async def get_stock(stock_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single stock by its database ID."""
    result = await db.execute(select(Stock).where(Stock.id == stock_id))
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return stock


@router.put("/{stock_id}", response_model=StockOut, summary="Update a stock")
async def update_stock(
    stock_id: int,
    payload: StockUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing stock's name, market, or active status."""
    result = await db.execute(select(Stock).where(Stock.id == stock_id))
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(stock, key, value)

    await db.commit()
    await db.refresh(stock)
    return stock


@router.delete("/{stock_id}", summary="Delete a stock")
async def delete_stock(stock_id: int, db: AsyncSession = Depends(get_db)):
    """Soft-delete a stock by setting is_active=False."""
    result = await db.execute(select(Stock).where(Stock.id == stock_id))
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    stock.is_active = False
    await db.commit()
    return {"ok": True}


@router.post("/{stock_id}/fetch", summary="Manually fetch all data for a stock")
async def fetch_stock(stock_id: int, db: AsyncSession = Depends(get_db)):
    """
    One-click data update for a single stock:
    1. Realtime quote → QuoteSnapshot
    2. Daily klines (120 days) → upsert DailyKline
    3. Fundamentals (PE/PB/market cap) from Tencent → stock_profiles
    4. Industry from EastMoney (best effort) → stock_profiles
    """
    result = await db.execute(select(Stock).where(Stock.id == stock_id))
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    agg = _make_aggregator()
    kline_count = 0
    quote_count = 0
    quote_source = None
    kline_source = None
    fundamental_ok = False
    industry_ok = False
    details = []

    try:
        # 1. Realtime quote
        try:
            quote_data = await agg.get_realtime_quote(stock.code, stock.market)
            quote_source = agg.last_source
            if quote_data:
                # ── Delisted stock detection ──
                quote_name = quote_data.get("name", "")
                if quote_name and "退" in quote_name and "退" not in (stock.name or ""):
                    stock.is_active = False
                    stock.name = quote_name
                    details.append(f"检测到退市({quote_name}) -> 已停用")
                    logger.info("Stock %s detected as delisted (%s), deactivated", stock.code, quote_name)

                q_price = safe_float(quote_data.get("price", 0.0))
                q_volume = safe_int(quote_data.get("volume", 0))
                if await _is_duplicate_snapshot(db, stock.id, q_price, q_volume):
                    details.append(f"实时行情({quote_source},无变化跳过)")
                else:
                    snapshot = QuoteSnapshot(
                        stock_id=stock.id,
                        price=q_price,
                        open=safe_float(quote_data.get("open", 0.0)),
                        high=safe_float(quote_data.get("high", 0.0)),
                        low=safe_float(quote_data.get("low", 0.0)),
                        close=safe_float(quote_data.get("close", 0.0)),
                        volume=q_volume,
                        amount=safe_float(quote_data.get("amount", 0.0)),
                        change_pct=safe_float(quote_data.get("change_pct", 0.0)),
                        timestamp=quote_data.get("timestamp", datetime.utcnow()),
                    )
                    db.add(snapshot)
                    quote_count = 1
                    details.append(f"实时行情({quote_source})")
        except Exception as e:
            details.append(f"实时行情失败: {str(e)[:50]}")

        # 2. Daily klines (gap-aware, skip if already up-to-date)
        try:
            row = (await db.execute(text(
                "SELECT max(date) FROM daily_klines WHERE stock_id = :sid"
            ), {"sid": stock.id})).scalar()
            latest_kline_date = row  # date or None
            expected_date = latest_expected_trading_date()

            if latest_kline_date and latest_kline_date >= expected_date:
                details.append("K线已是最新")
            else:
                if latest_kline_date is None:
                    limit = settings.KLINE_INITIAL_LIMIT
                else:
                    gap_days = (expected_date - latest_kline_date).days
                    limit = min(gap_days + 10, 500)

                klines = await agg.get_daily_klines(stock.code, stock.market, limit=limit)
                kline_source = agg.last_source if klines else None
                if klines:
                    records = [
                        {
                            "stock_id": stock.id,
                            "date": k["date"],
                            "open": safe_float(k["open"]),
                            "high": safe_float(k["high"]),
                            "low": safe_float(k["low"]),
                            "close": safe_float(k["close"]),
                            "volume": safe_int(k["volume"]),
                            "amount": safe_float(k["amount"]),
                            "change_pct": safe_float(k["change_pct"]),
                            "turnover_rate": k.get("turnover_rate"),
                        }
                        for k in klines
                    ]
                    stmt = pg_insert(DailyKline).values(records)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["stock_id", "date"],
                        set_={
                            col: stmt.excluded[col]
                            for col in [
                                "open", "high", "low", "close",
                                "volume", "amount", "change_pct", "turnover_rate",
                            ]
                        },
                    )
                    await db.execute(stmt)
                    kline_count = len(klines)
                    details.append(f"K线{kline_count}条({kline_source})")
                else:
                    details.append("K线无数据")
        except Exception as e:
            details.append(f"K线失败: {str(e)[:50]}")

        # 3. Fundamentals from Tencent (PE/PB/market cap)
        try:
            fund_data = await _fetch_tencent_fundamentals(stock.code, stock.market)
            if fund_data:
                await _upsert_profile(db, stock.id, fund_data)
                # Also update stock name if better
                if fund_data.get("name") and stock.name in (stock.code, "", None):
                    stock.name = fund_data["name"]
                fundamental_ok = True
                details.append(f"基本面(tencent,PE={fund_data.get('pe_ttm')},PB={fund_data.get('pb')})")
        except Exception as e:
            details.append(f"基本面失败: {str(e)[:50]}")

        # 4. Industry (best effort, EastMoney may be rate-limited)
        try:
            # Check if we already have industry
            existing = await db.execute(text(
                "SELECT industry FROM stock_profiles WHERE stock_id = :sid"
            ), {"sid": stock.id})
            row = existing.first()
            existing_industry = row[0] if row else None
            if not is_valid_industry(existing_industry):
                industry = await _fetch_industry_em(stock.code)
                if industry:
                    await db.execute(text(
                        "UPDATE stock_profiles SET industry = :ind, updated_at = NOW() WHERE stock_id = :sid"
                    ), {"ind": industry, "sid": stock.id})
                    industry_ok = True
                    details.append(f"行业(eastmoney)={industry}")
            else:
                details.append(f"行业已有={existing_industry}")
        except Exception:
            pass  # industry is best-effort

        # Log
        source_parts = []
        if quote_source:
            source_parts.append(f"行情:{quote_source}")
        if kline_source:
            source_parts.append(f"K线:{kline_source}")
        source_parts.append("基本面:tencent")
        if industry_ok:
            source_parts.append("行业:eastmoney")
        log_entry = FetchLog(
            stock_id=stock.id,
            fetch_type="manual_full_fetch",
            source=",".join(source_parts),
            status="success",
            message=" | ".join(details),
        )
        db.add(log_entry)
        await db.commit()

    except Exception as e:
        logger.error("Error fetching stock %s: %s", stock.code, e)
        log_entry = FetchLog(
            stock_id=stock.id,
            fetch_type="manual_full_fetch",
            source=getattr(agg, "last_source", None) or None,
            status="error",
            message=str(e),
        )
        db.add(log_entry)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Fetch failed: {str(e)}")
    finally:
        await agg.close()

    return {
        "ok": True,
        "message": " | ".join(details),
    }
