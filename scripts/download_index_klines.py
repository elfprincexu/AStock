"""
Download 5-year daily K-line data for all constituent stocks of
沪深300 (CSI 300) and 中证500 (CSI 500) indices, then store them
in the project PostgreSQL database (stocks + daily_klines tables).

Uses akshare's ``stock_zh_a_daily`` (Sina backend) which is more
reliable than the EastMoney-based APIs that tend to rate-limit.

Anti-rate-limiting strategies:
  - Sequential requests (no concurrency) to look like a normal user
  - Random delay 0.5~1.5s between requests with jitter
  - Batch cooldown: pause 10~15s every 50 stocks
  - Resume support: skip stocks that already have ≥1000 kline rows
  - Save-as-you-go: each stock committed immediately, safe to interrupt

Usage:
    cd /home/cyc/xun3/AStock
    python scripts/download_index_klines.py

    # To force re-download everything (ignore existing data):
    python scripts/download_index_klines.py --force
"""

import logging
import random
import sys
import time
from datetime import datetime

# Make the backend package importable
sys.path.insert(0, "backend")

import akshare as ak
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.models import Stock, DailyKline, FetchLog
from app.database import Base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---- Configuration --------------------------------------------------------
END_DATE = datetime.now().strftime("%Y%m%d")
START_DATE = str(int(END_DATE[:4]) - 5) + END_DATE[4:]

KLINE_LIMIT = 2000             # 5yr ≈ 1250 trading days
MAX_RETRIES = 3                # retries per stock
MIN_DELAY = 0.5                # base delay between requests (seconds)
MAX_DELAY = 1.5                # max normal delay
BATCH_COOLDOWN_EVERY = 50      # long pause after this many requests
BATCH_COOLDOWN_MIN = 10        # min cooldown (seconds)
BATCH_COOLDOWN_MAX = 15        # max cooldown (seconds)
SKIP_THRESHOLD = 1000          # skip stock if it already has ≥ this many rows
FORCE_MODE = "--force" in sys.argv

engine = create_engine(settings.DATABASE_URL_SYNC, echo=False, pool_size=3)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def code_to_market(code: str) -> str:
    if code.startswith(("60", "68")):
        return "SH"
    elif code.startswith(("00", "30")):
        return "SZ"
    raise ValueError(f"Unknown stock code prefix: {code}")


def get_constituent_stocks() -> list[dict]:
    """Fetch constituent lists for CSI300 + CSI500 via akshare, de-duplicate."""
    logger.info("Fetching index constituent lists via akshare ...")

    df300 = ak.index_stock_cons(symbol="000300")
    logger.info("  沪深300: %d constituents", len(df300))

    df500 = ak.index_stock_cons(symbol="000905")
    logger.info("  中证500: %d constituents", len(df500))

    seen = set()
    stocks = []
    for _, row in df300.iterrows():
        code = str(row["品种代码"]).zfill(6)
        if code not in seen:
            seen.add(code)
            stocks.append({
                "code": code,
                "name": str(row["品种名称"]),
                "market": code_to_market(code),
            })
    for _, row in df500.iterrows():
        code = str(row["品种代码"]).zfill(6)
        if code not in seen:
            seen.add(code)
            stocks.append({
                "code": code,
                "name": str(row["品种名称"]),
                "market": code_to_market(code),
            })

    logger.info("Total unique stocks: %d", len(stocks))
    return stocks


def ensure_tables():
    Base.metadata.create_all(engine)


def upsert_stock_record(db: Session, code: str, name: str, market: str) -> int:
    stock = db.execute(
        select(Stock).where(Stock.code == code)
    ).scalar_one_or_none()
    if stock is None:
        stock = Stock(code=code, name=name, market=market)
        db.add(stock)
        db.flush()
    return stock.id


def get_existing_kline_count(db: Session, stock_id: int) -> int:
    return db.execute(
        text("SELECT COUNT(*) FROM daily_klines WHERE stock_id = :sid"),
        {"sid": stock_id},
    ).scalar() or 0


def upsert_klines(db: Session, stock_id: int, klines: list[dict]) -> int:
    if not klines:
        return 0
    records = [{"stock_id": stock_id, **k} for k in klines]
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
    db.execute(stmt)
    return len(records)


def smart_delay(consecutive_ok: int) -> float:
    """Return a human-like random delay. Gets slightly longer over time."""
    base = random.uniform(MIN_DELAY, MAX_DELAY)
    # Every 100 consecutive successes, add a tiny drift so pattern isn't too regular
    drift = random.uniform(0, 0.5) if consecutive_ok % 100 < 5 else 0
    return base + drift


def fetch_klines_sina(code: str, market: str) -> list[dict]:
    """Fetch daily klines using akshare's stock_zh_a_daily (Sina backend).

    Returns list of dicts matching the DailyKline model columns.
    """
    prefix = "sh" if market == "SH" else "sz"
    symbol = f"{prefix}{code}"

    df = ak.stock_zh_a_daily(
        symbol=symbol,
        start_date=START_DATE,
        end_date=END_DATE,
        adjust="qfq",
    )

    if df is None or df.empty:
        return []

    results = []
    for _, r in df.iterrows():
        dt = r["date"]
        if hasattr(dt, "date"):
            dt = dt.date()

        vol = int(float(r.get("volume", 0) or 0))
        amt = float(r.get("amount", 0) or 0)
        turnover = float(r.get("turnover", 0) or 0)
        turnover_pct = turnover * 100.0  # ratio → percentage

        results.append({
            "date": dt,
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": vol,
            "amount": amt,
            "change_pct": 0.0,
            "turnover_rate": turnover_pct,
        })

    # Compute change_pct from consecutive closes
    for i in range(len(results)):
        if i == 0:
            results[i]["change_pct"] = 0.0
        else:
            prev = results[i - 1]["close"]
            if prev:
                results[i]["change_pct"] = round(
                    (results[i]["close"] - prev) / prev * 100, 4
                )

    if len(results) > KLINE_LIMIT:
        results = results[-KLINE_LIMIT:]

    return results


# ---------------------------------------------------------------------------
# Main download loop — strictly sequential, synchronous
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    logger.info("=" * 70)
    logger.info("  Index Constituent K-line Downloader (Sina backend)")
    logger.info("  CSI300 + CSI500  |  %s -> %s  (5 years)", START_DATE, END_DATE)
    logger.info("  Force mode: %s", FORCE_MODE)
    logger.info("=" * 70)

    ensure_tables()
    stocks = get_constituent_stocks()

    # Register all stocks in DB
    logger.info("Registering stocks in database ...")
    stock_id_map: dict[str, int] = {}
    with Session(engine) as db:
        for s in stocks:
            sid = upsert_stock_record(db, s["code"], s["name"], s["market"])
            stock_id_map[s["code"]] = sid
        db.commit()
    logger.info("Registered %d stocks (IDs %d ~ %d)",
                len(stock_id_map), min(stock_id_map.values()), max(stock_id_map.values()))

    # Check which stocks already have data (for resume)
    skip_codes: set[str] = set()
    if not FORCE_MODE:
        with Session(engine) as db:
            for s in stocks:
                existing = get_existing_kline_count(db, stock_id_map[s["code"]])
                if existing >= SKIP_THRESHOLD:
                    skip_codes.add(s["code"])
        if skip_codes:
            logger.info("Skipping %d stocks that already have >= %d kline rows",
                        len(skip_codes), SKIP_THRESHOLD)

    to_download = [s for s in stocks if s["code"] not in skip_codes]
    logger.info("Will download: %d stocks", len(to_download))

    if not to_download:
        logger.info("Nothing to download -- all stocks already have data.")
        return

    # Sequential download with anti-rate-limiting
    total_klines = 0
    succeeded = 0
    failed = 0
    failed_codes: list[str] = []
    consecutive_ok = 0
    request_count = 0

    try:
        for i, s in enumerate(to_download):
            code, market = s["code"], s["market"]
            stock_id = stock_id_map[code]

            # ---- Batch cooldown ----
            if request_count > 0 and request_count % BATCH_COOLDOWN_EVERY == 0:
                cooldown = random.uniform(BATCH_COOLDOWN_MIN, BATCH_COOLDOWN_MAX)
                logger.info(
                    "  -- Batch cooldown: sleeping %.0fs after %d requests ...",
                    cooldown, request_count,
                )
                time.sleep(cooldown)

            # ---- Fetch with retry ----
            klines = None
            last_err = None
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    klines = fetch_klines_sina(code, market)
                    request_count += 1
                    break
                except Exception as e:
                    last_err = str(e)
                    request_count += 1
                    if attempt < MAX_RETRIES:
                        backoff = (2 ** attempt) + random.uniform(1, 3)
                        logger.warning(
                            "  ! %s attempt %d/%d failed: %s -- backoff %.1fs",
                            code, attempt, MAX_RETRIES, e, backoff,
                        )
                        time.sleep(backoff)

            # ---- Handle result ----
            if klines is not None and len(klines) > 0:
                with Session(engine) as db:
                    count = upsert_klines(db, stock_id, klines)
                    db.add(FetchLog(
                        stock_id=stock_id,
                        fetch_type="kline",
                        source="akshare_sina",
                        status="success",
                        message=f"{count} klines ({START_DATE}-{END_DATE})",
                    ))
                    db.commit()
                total_klines += count
                succeeded += 1
                consecutive_ok += 1

            elif klines is not None and len(klines) == 0:
                with Session(engine) as db:
                    db.add(FetchLog(
                        stock_id=stock_id,
                        fetch_type="kline",
                        source="akshare_sina",
                        status="success",
                        message="0 klines (empty response)",
                    ))
                    db.commit()
                succeeded += 1
                consecutive_ok += 1
                logger.warning("  ! %s returned 0 klines (may be delisted)", code)

            else:
                failed += 1
                failed_codes.append(code)
                consecutive_ok = 0
                with Session(engine) as db:
                    db.add(FetchLog(
                        stock_id=stock_id,
                        fetch_type="kline",
                        source="akshare_sina",
                        status="error",
                        message=f"Failed: {last_err}",
                    ))
                    db.commit()
                logger.error("  X %s FAILED: %s", code, last_err)
                # Longer pause after failure
                time.sleep(random.uniform(3, 6))

            # ---- Progress log ----
            done = i + 1
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            remaining = len(to_download) - done
            eta = remaining / rate if rate > 0 else 0
            if done % 10 == 0 or done == len(to_download):
                logger.info(
                    "  [%d/%d] %s %s -> %d klines  |  total=%dk  %.1f/s  ETA %dm%ds",
                    done, len(to_download), code, s["name"],
                    len(klines) if klines else 0,
                    total_klines // 1000,
                    rate, int(eta) // 60, int(eta) % 60,
                )

            # ---- Normal inter-request delay ----
            delay = smart_delay(consecutive_ok)
            time.sleep(delay)

    except KeyboardInterrupt:
        logger.info("\n  Interrupted by user. Data saved so far is safe.")

    # ---- Summary ----
    elapsed = time.time() - t0
    logger.info("=" * 70)
    logger.info("  COMPLETE -- %.0fs total  (%d requests)", elapsed, request_count)
    logger.info("  Downloaded: %d ok, %d failed, %d skipped",
                succeeded, failed, len(skip_codes))
    logger.info("  Kline records upserted: %d", total_klines)
    if failed_codes:
        logger.info("  Failed codes: %s", ", ".join(failed_codes[:20]))
    logger.info("=" * 70)

    with Session(engine) as db:
        stock_count = db.execute(
            text("SELECT COUNT(*) FROM stocks")
        ).scalar()
        kline_count = db.execute(
            text("SELECT COUNT(*) FROM daily_klines")
        ).scalar()
        date_range = db.execute(
            text("SELECT MIN(date), MAX(date) FROM daily_klines")
        ).one()
        stocks_with_data = db.execute(
            text("SELECT COUNT(DISTINCT stock_id) FROM daily_klines")
        ).scalar()
        logger.info("  DB totals: %d stocks (%d with klines), %d kline rows",
                     stock_count, stocks_with_data, kline_count)
        logger.info("  Date range: %s ~ %s", date_range[0], date_range[1])


if __name__ == "__main__":
    main()
