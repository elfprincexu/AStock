import logging
import time
import random
import json
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings, SCHEDULE_DEFAULTS
from app.models import Stock, QuoteSnapshot, DailyKline, FetchLog, StockProfile, AppSetting, TradeStrategy
from app.database import Base
from app.services.aggregator import StockDataAggregator
from app.services.base import safe_int, safe_float
from app.services.trade_engine import trade_engine
from app.utils import latest_expected_trading_date, is_valid_industry
from celery_app import celery

logger = logging.getLogger(__name__)

# Sync engine for Celery
sync_engine = create_engine(settings.DATABASE_URL_SYNC, echo=False)


def _make_aggregator() -> StockDataAggregator:
    """Create an aggregator configured from application settings."""
    return StockDataAggregator(
        priority=settings.DATA_SOURCE_PRIORITY,
        timeout=settings.DATA_SOURCE_TIMEOUT,
        tushare_token=settings.TUSHARE_TOKEN,
    )


# ---------------------------------------------------------------------------
#  Helper: read/write app_settings from DB
# ---------------------------------------------------------------------------

def _get_setting(db: Session, key: str) -> str:
    """Read a setting from app_settings, falling back to SCHEDULE_DEFAULTS."""
    row = db.execute(
        select(AppSetting.value).where(AppSetting.key == key)
    ).scalar_one_or_none()
    if row is not None:
        return row
    return SCHEDULE_DEFAULTS.get(key, "")


def _set_setting(db: Session, key: str, value: str):
    """Upsert a setting into app_settings."""
    stmt = pg_insert(AppSetting).values(key=key, value=value, updated_at=datetime.utcnow())
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
    )
    db.execute(stmt)


# ---------------------------------------------------------------------------
#  Helper: Tencent batch fundamentals (sync, batches of 40)
# ---------------------------------------------------------------------------

def _code_to_market_prefix(code: str, market: str) -> str:
    """Map stock code + market to Tencent API prefix."""
    m = market.lower()
    if m == "sh":
        return "sh"
    if m == "bj":
        return "bj"
    return "sz"


def _fetch_tencent_fundamentals_batch(stocks: list[dict]) -> dict[str, dict]:
    """
    Fetch PE/PB/market cap for a batch of stocks from Tencent.
    stocks: list of {"code": "000001", "market": "sz"}
    Returns: {code: {pe_ttm, pb, total_market_cap, circ_market_cap, name}}
    """
    codes_str = ",".join(
        f"{_code_to_market_prefix(s['code'], s['market'])}{s['code']}"
        for s in stocks
    )
    url = f"http://qt.gtimg.cn/q={codes_str}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.qq.com/",
    }
    result = {}
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=headers)
        text_data = resp.content.decode("gbk", errors="replace")
        for line in text_data.strip().split("\n"):
            if "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) < 50:
                continue
            code = parts[2].strip()
            name = parts[1].strip()
            pe = float(parts[39]) if parts[39].strip() else None
            pb = float(parts[46]) if parts[46].strip() else None
            total_mcap = float(parts[45]) if parts[45].strip() else None
            circ_mcap = float(parts[44]) if parts[44].strip() else None
            result[code] = {
                "name": name,
                "pe_ttm": pe,
                "pb": pb,
                "total_market_cap": total_mcap,
                "circ_market_cap": circ_mcap,
            }
    except Exception as e:
        logger.warning("Tencent batch fundamental fetch failed: %s", e)
    return result


# ---------------------------------------------------------------------------
#  Unified per-stock full fetch (shared by manual & scheduled)
# ---------------------------------------------------------------------------

def _fetch_tencent_fundamentals_single_sync(code: str, market: str) -> dict | None:
    """Fetch PE/PB/market cap for a single stock from Tencent (sync)."""
    prefix = _code_to_market_prefix(code, market)
    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.qq.com/",
    }
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, headers=headers)
        text_data = resp.content.decode("gbk", errors="replace")
        for line in text_data.strip().split("\n"):
            if "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) < 50:
                continue
            return {
                "name": parts[1].strip(),
                "pe_ttm": float(parts[39]) if parts[39].strip() else None,
                "pb": float(parts[46]) if parts[46].strip() else None,
                "total_market_cap": float(parts[45]) if parts[45].strip() else None,
                "circ_market_cap": float(parts[44]) if parts[44].strip() else None,
            }
    except Exception as e:
        logger.warning("Tencent fundamental fetch failed for %s: %s", code, e)
    return None


def _fetch_industry_em_sync(code: str) -> str | None:
    """Fetch industry from EastMoney via akshare (sync, best-effort)."""
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=code)
        if df is not None and not df.empty:
            info = dict(zip(df["item"], df["value"]))
            industry = info.get("行业", "")
            if is_valid_industry(industry):
                return industry
            return None
    except Exception:
        pass
    return None


def _upsert_profile_sync(db: Session, stock_id: int, data: dict):
    """Upsert stock_profiles row with fundamental data (sync)."""
    db.execute(text("""
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


def _is_duplicate_snapshot_sync(db: Session, stock_id: int, price: float, volume: int) -> bool:
    """Check if the latest snapshot for this stock already has the same price+volume."""
    row = db.execute(text(
        "SELECT price, volume FROM quote_snapshots "
        "WHERE stock_id = :sid ORDER BY created_at DESC LIMIT 1"
    ), {"sid": stock_id}).first()
    return row is not None and row[0] == price and row[1] == volume


def _full_fetch_one_stock_sync(
    db: Session,
    stock_id: int,
    code: str,
    market: str,
    latest_kline_date,
    fetch_type: str,
    agg: StockDataAggregator,
) -> dict:
    """
    Full data fetch for one stock (unified logic for manual & scheduled):
      1. Realtime quote → QuoteSnapshot
      2. Daily klines (gap-aware) → upsert DailyKline
      3. Fundamentals (PE/PB/market cap) from Tencent → stock_profiles
      4. Industry from EastMoney (best effort, skip if already present)
    Writes a FetchLog entry and commits. Returns stats dict.
    """
    details = []
    kline_count = 0
    quote_source = None
    kline_source = None

    # 1. Realtime quote
    try:
        quote_data = agg.get_realtime_quote_sync(code, market)
        quote_source = agg.last_source
        if quote_data:
            # ── Delisted stock detection ──
            quote_name = quote_data.get("name", "")
            if quote_name and "退" in quote_name:
                db.execute(text(
                    "UPDATE stocks SET is_active = FALSE, name = :name WHERE id = :id AND name NOT LIKE :pat"
                ), {"id": stock_id, "name": quote_name, "pat": "%退%"})
                details.append(f"检测到退市({quote_name}) -> 已停用")
                logger.info("Stock %s detected as delisted (%s), deactivated", code, quote_name)

            q_price = safe_float(quote_data.get("price", 0.0))
            q_volume = safe_int(quote_data.get("volume", 0))
            if _is_duplicate_snapshot_sync(db, stock_id, q_price, q_volume):
                details.append(f"实时行情({quote_source},无变化跳过)")
            else:
                db.add(QuoteSnapshot(
                    stock_id=stock_id,
                    price=q_price,
                    open=safe_float(quote_data.get("open", 0.0)),
                    high=safe_float(quote_data.get("high", 0.0)),
                    low=safe_float(quote_data.get("low", 0.0)),
                    close=safe_float(quote_data.get("close", 0.0)),
                    volume=q_volume,
                    amount=safe_float(quote_data.get("amount", 0.0)),
                    change_pct=safe_float(quote_data.get("change_pct", 0.0)),
                    timestamp=quote_data.get("timestamp", datetime.utcnow()),
                ))
                details.append(f"实时行情({quote_source})")
    except Exception as e:
        details.append(f"实时行情失败: {str(e)[:50]}")

    # 2. Daily klines (gap-aware)
    try:
        expected_date = latest_expected_trading_date()
        if latest_kline_date and latest_kline_date >= expected_date:
            details.append("K线已是最新")
        else:
            if latest_kline_date is None:
                limit = settings.KLINE_INITIAL_LIMIT
            else:
                gap_days = (expected_date - latest_kline_date).days
                limit = min(gap_days + 10, 500)

            klines = agg.get_daily_klines_sync(code, market, limit=limit)
            kline_source = agg.last_source if klines else None
            if klines:
                records = [
                    {
                        "stock_id": stock_id,
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
                        c: stmt.excluded[c]
                        for c in [
                            "open", "high", "low", "close",
                            "volume", "amount", "change_pct", "turnover_rate",
                        ]
                    },
                )
                db.execute(stmt)
                kline_count = len(klines)
                details.append(f"K线{kline_count}条({kline_source})")
            else:
                details.append("K线无数据")
    except Exception as e:
        details.append(f"K线失败: {str(e)[:50]}")

    # 3. Fundamentals from Tencent (PE/PB/market cap)
    try:
        fund_data = _fetch_tencent_fundamentals_single_sync(code, market)
        if fund_data:
            _upsert_profile_sync(db, stock_id, fund_data)
            stock_obj = db.get(Stock, stock_id)
            if stock_obj and fund_data.get("name") and stock_obj.name in (stock_obj.code, "", None):
                stock_obj.name = fund_data["name"]
            details.append(f"基本面(tencent,PE={fund_data.get('pe_ttm')},PB={fund_data.get('pb')})")
    except Exception as e:
        details.append(f"基本面失败: {str(e)[:50]}")

    # 4. Industry (best effort, skip if already present)
    industry_fetched = False
    try:
        existing = db.execute(text(
            "SELECT industry FROM stock_profiles WHERE stock_id = :sid"
        ), {"sid": stock_id}).first()
        existing_industry = existing[0] if existing else None
        if not is_valid_industry(existing_industry):
            industry = _fetch_industry_em_sync(code)
            if industry:
                db.execute(text(
                    "UPDATE stock_profiles SET industry = :ind, updated_at = NOW() WHERE stock_id = :sid"
                ), {"ind": industry, "sid": stock_id})
                industry_fetched = True
                details.append(f"行业(eastmoney)={industry}")
        else:
            details.append(f"行业已有={existing_industry}")
    except Exception:
        pass  # industry is best-effort

    # Write FetchLog
    source_parts = []
    if quote_source:
        source_parts.append(f"行情:{quote_source}")
    if kline_source:
        source_parts.append(f"K线:{kline_source}")
    source_parts.append("基本面:tencent")
    if industry_fetched:
        source_parts.append("行业:eastmoney")
    db.add(FetchLog(
        stock_id=stock_id,
        fetch_type=fetch_type,
        source=",".join(source_parts),
        status="success",
        message=" | ".join(details),
    ))
    db.commit()

    return {"kline_count": kline_count, "details": details}


# ---------------------------------------------------------------------------
#  Existing single-stock task (kept for backward compatibility)
# ---------------------------------------------------------------------------

@celery.task(name="app.tasks.fetch_stock_task")
def fetch_stock_task(stock_id: int, fetch_type: str = "all"):
    """Fetch data for a single stock using the unified full pipeline.

    Delegates to ``_full_fetch_one_stock_sync`` for full 4-step coverage:
    realtime quote, gap-aware klines, fundamentals, industry.
    """
    agg = _make_aggregator()

    with Session(sync_engine) as db:
        stock = db.get(Stock, stock_id)
        if not stock:
            logger.error(f"Stock {stock_id} not found")
            return {"error": "Stock not found"}

        # Get latest kline date for gap-aware logic
        from sqlalchemy import func as sa_func
        latest_kline_date = db.execute(
            select(sa_func.max(DailyKline.date)).where(DailyKline.stock_id == stock_id)
        ).scalar()

        try:
            stats = _full_fetch_one_stock_sync(
                db, stock.id, stock.code, stock.market,
                latest_kline_date, fetch_type, agg,
            )
            db.commit()
            return {"stock_id": stock_id, "code": stock.code, **stats}
        except Exception as e:
            db.rollback()
            db.add(FetchLog(
                stock_id=stock.id, fetch_type=fetch_type,
                source=agg.last_source or None, status="error",
                message=str(e),
            ))
            db.commit()
            logger.exception(f"Error fetching {stock.code}")
            return {"stock_id": stock_id, "error": str(e)}


@celery.task(name="app.tasks.fetch_all_stocks_task")
def fetch_all_stocks_task(fetch_type: str = "all"):
    """Fetch data for all active stocks by dispatching individual tasks."""
    with Session(sync_engine) as db:
        stocks = (
            db.execute(select(Stock).where(Stock.is_active == True)).scalars().all()
        )

    results = []
    for stock in stocks:
        r = fetch_stock_task.delay(stock.id, fetch_type)
        results.append({"stock_id": stock.id, "code": stock.code, "task_id": r.id})

    return {"dispatched": len(results), "tasks": results}


# ---------------------------------------------------------------------------
#  Scheduler check task — runs every 10 minutes, dispatches daily_update
#  if the configured time has arrived and hasn't run today yet.
# ---------------------------------------------------------------------------

@celery.task(name="app.tasks.check_scheduled_update")
def check_scheduled_update():
    """
    Lightweight check (runs every 10 min via beat).
    Reads schedule settings from DB and dispatches daily_update_task
    if the configured time has passed and it hasn't run today yet.
    """
    with Session(sync_engine) as db:
        # Ensure app_settings table exists
        AppSetting.__table__.create(sync_engine, checkfirst=True)

        enabled = _get_setting(db, "daily_update_enabled")
        if enabled != "true":
            return {"skipped": "disabled"}

        tz_name = _get_setting(db, "daily_update_timezone") or "Asia/Shanghai"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")

        now = datetime.now(tz)
        # Skip weekends (Saturday=5, Sunday=6)
        if now.weekday() >= 5:
            return {"skipped": "weekend"}

        hour = int(_get_setting(db, "daily_update_hour") or 16)
        minute = int(_get_setting(db, "daily_update_minute") or 0)
        scheduled_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # Only trigger if we're past the scheduled time
        if now < scheduled_time:
            return {"skipped": "not_yet", "scheduled": scheduled_time.isoformat()}

        # Check if already ran today
        last_run_str = _get_setting(db, "daily_update_last_run")
        if last_run_str:
            try:
                last_run = datetime.fromisoformat(last_run_str)
                if last_run.astimezone(tz).date() == now.date():
                    return {"skipped": "already_ran_today", "last_run": last_run_str}
            except Exception:
                pass

        # Mark as running to prevent duplicate dispatch
        _set_setting(db, "daily_update_last_run", now.isoformat())
        _set_setting(db, "daily_update_last_status", "running")
        _set_setting(db, "daily_update_last_message", "Dispatched by scheduler")
        db.commit()

    # Dispatch the actual daily update
    daily_update_task.delay()
    return {"dispatched": True, "time": now.isoformat()}


# ---------------------------------------------------------------------------
#  Core daily update task — unified full fetch per stock
# ---------------------------------------------------------------------------

@celery.task(name="app.tasks.daily_update_task", bind=True)
def daily_update_task(self):
    """
    Comprehensive daily update using unified per-stock fetch:
      For each active stock: quote + klines + fundamentals + industry.
    Same logic as manual fetch, differentiated by fetch_type in logs.
    """
    start_time = time.time()
    logger.info("[daily_update] Starting comprehensive daily update")

    stats = {
        "total_stocks": 0,
        "success": 0,
        "failed": 0,
        "kline_total_rows": 0,
        "failed_codes": [],
    }

    agg = _make_aggregator()

    with Session(sync_engine) as db:
        AppSetting.__table__.create(sync_engine, checkfirst=True)
        _set_setting(db, "daily_update_last_status", "running")
        db.commit()

        # Load all active stocks with their latest kline date
        rows = db.execute(text("""
            SELECT s.id, s.code, s.market,
                   (SELECT MAX(dk.date) FROM daily_klines dk WHERE dk.stock_id = s.id) as latest_date
            FROM stocks s
            WHERE s.is_active = TRUE
            ORDER BY s.id
        """)).all()

        stocks_info = [
            {"id": r[0], "code": r[1], "market": r[2], "latest_date": r[3]}
            for r in rows
        ]
        stats["total_stocks"] = len(stocks_info)
        logger.info("[daily_update] Processing %d active stocks", len(stocks_info))

        for i, s in enumerate(stocks_info):
            try:
                result = _full_fetch_one_stock_sync(
                    db=db,
                    stock_id=s["id"],
                    code=s["code"],
                    market=s["market"],
                    latest_kline_date=s["latest_date"],
                    fetch_type="scheduled_full_fetch",
                    agg=agg,
                )
                stats["success"] += 1
                stats["kline_total_rows"] += result["kline_count"]
            except Exception as e:
                db.rollback()
                stats["failed"] += 1
                stats["failed_codes"].append(s["code"])
                logger.warning("[daily_update] Failed for %s: %s", s["code"], e)
                try:
                    db.add(FetchLog(
                        stock_id=s["id"],
                        fetch_type="scheduled_full_fetch",
                        source=None,
                        status="error",
                        message=str(e)[:200],
                    ))
                    db.commit()
                except Exception:
                    db.rollback()

            # Rate limiting
            if (i + 1) % 50 == 0:
                logger.info(
                    "[daily_update] Progress: %d/%d (ok=%d, fail=%d)",
                    i + 1, len(stocks_info),
                    stats["success"], stats["failed"],
                )
                time.sleep(random.uniform(2, 5))
            else:
                time.sleep(random.uniform(0.1, 0.3))

        # Finalize
        elapsed = time.time() - start_time
        stats["elapsed_seconds"] = round(elapsed, 1)
        if len(stats["failed_codes"]) > 50:
            stats["failed_codes"] = stats["failed_codes"][:50] + ["..."]

        status = "success" if stats["failed"] == 0 else "partial"
        message = (
            f"总计{stats['total_stocks']}只: "
            f"{stats['success']}成功/{stats['failed']}失败, "
            f"{stats['kline_total_rows']}条K线; "
            f"耗时{stats['elapsed_seconds']}秒"
        )

        tz_name = _get_setting(db, "daily_update_timezone") or "Asia/Shanghai"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")

        _set_setting(db, "daily_update_last_status", status)
        _set_setting(db, "daily_update_last_message", message)
        _set_setting(db, "daily_update_last_run", datetime.now(tz).isoformat())

        db.add(FetchLog(
            stock_id=None,
            fetch_type="daily_update_summary",
            source="scheduler",
            status=status,
            message=message,
        ))
        db.commit()

        logger.info("[daily_update] Completed: %s", message)
        return stats


# ---------------------------------------------------------------------------
#  Trade condition monitoring task
# ---------------------------------------------------------------------------

@celery.task(name="app.tasks.trade_monitor_tick")
def trade_monitor_tick():
    """
    Periodic task: fetch real-time quotes for all active strategies,
    then run the trade engine to evaluate conditions and execute orders.
    Runs every 5 seconds during trading hours.
    """
    with Session(sync_engine) as db:
        # Find all distinct stock codes from active strategies
        rows = db.execute(
            select(TradeStrategy.stock_code, TradeStrategy.market)
            .where(TradeStrategy.status == "active")
            .distinct()
        ).all()

        if not rows:
            return {"skipped": "no_active_strategies"}

        # Fetch real-time quotes for all watched stocks
        agg = _make_aggregator()
        quotes = {}
        for code, market in rows:
            try:
                quote = agg.get_realtime_quote_sync(code, market)
                if quote and quote.get("price", 0) > 0:
                    quotes[code] = quote
            except Exception as e:
                logger.debug("[trade_monitor] Quote fetch failed for %s: %s", code, e)

        if not quotes:
            return {"skipped": "no_quotes", "stocks": len(rows)}

        # Run the trade engine
        events = trade_engine.tick(db, quotes)

        return {
            "stocks_monitored": len(quotes),
            "events": len(events),
            "details": events[:10],  # Limit for task result size
        }


# ─── Quant Auto-Iteration Task ──────────────────────────────────────

@celery.task(name="app.tasks.check_quant_iterations")
def check_quant_iterations():
    """Check for quant strategies that need iteration today and run them."""
    from app.models import QuantBacktestResult
    from app.services.quant_iterate import run_single_iteration

    with Session(sync_engine) as db:
        today = date.today()
        # Find all strategies due for iteration
        rows = db.execute(
            select(QuantBacktestResult.run_id, QuantBacktestResult.name)
            .where(
                QuantBacktestResult.auto_iterate == True,
                QuantBacktestResult.iterate_status != "running",
                QuantBacktestResult.next_iterate_date <= today,
            )
        ).all()

        if not rows:
            return {"status": "no_pending", "checked_date": str(today)}

        results = []
        for run_id, name in rows:
            logger.info("[quant_iterate] Running iteration for %s (%s)", run_id, name)
            try:
                result = run_single_iteration(run_id)
                results.append({"run_id": run_id, "name": name, **result})
            except Exception as e:
                logger.error("[quant_iterate] Error for %s: %s", run_id, e, exc_info=True)
                results.append({"run_id": run_id, "name": name, "ok": False, "error": str(e)})

        return {
            "status": "completed",
            "checked_date": str(today),
            "total": len(rows),
            "results": results,
        }
