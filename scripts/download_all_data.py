#!/usr/bin/env python3
"""
AStock Data Download Script — Full A-share Market Coverage
Downloads ALL A-share stocks (SH/SZ/BJ), 10-year daily K-lines, and fundamental data.

Usage:
    python scripts/download_all_data.py                       # Download everything
    python scripts/download_all_data.py --klines-only         # Only K-lines
    python scripts/download_all_data.py --fundamentals-only   # Only PE/PB/market cap
    python scripts/download_all_data.py --industry-only       # Only industry data
    python scripts/download_all_data.py --skip-klines         # Everything except K-lines
    python scripts/download_all_data.py --force               # Force re-download
"""

import os
import sys
import re
import time
import random
import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import akshare as ak

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Database config
DATABASE_URL = "postgresql+asyncpg://astock:astock123@localhost:5432/astock"
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

KLINE_YEARS = 10               # 10-year history
SKIP_THRESHOLD = 2000           # Skip stock if already has this many kline rows (10yr ~ 2500 trading days)


# ======================================================================
# Step 1: Get ALL A-share stock codes (SH + SZ + BJ)
# ======================================================================

def get_all_a_shares() -> list[tuple[str, str]]:
    """Fetch all A-share stock codes from multiple exchanges.

    Returns list of (code, name) tuples, also saves to CSV.
    Uses:
      - akshare for Shanghai (SSE) and Beijing (BSE)
      - Tencent Finance API for Shenzhen (SZSE) when szse.cn is unavailable
    """
    print("\n" + "=" * 60)
    print("Step 1: Fetching ALL A-share stock list (SH + SZ + BJ)")
    print("=" * 60)

    all_stocks: dict[str, str] = {}  # code -> name

    # --- Shanghai Main Board ---
    print("\n  [SH] Fetching Shanghai Main Board (主板A股)...")
    try:
        df = ak.stock_info_sh_name_code(symbol="主板A股")
        for _, row in df.iterrows():
            code = str(row["证券代码"]).zfill(6)
            all_stocks[code] = str(row["证券简称"])
        print(f"       Got {len(df)} stocks")
    except Exception as e:
        print(f"       ERROR: {e}")

    # --- Shanghai STAR Market ---
    print("  [SH] Fetching Shanghai STAR Market (科创板)...")
    try:
        df2 = ak.stock_info_sh_name_code(symbol="科创板")
        before = len(all_stocks)
        for _, row in df2.iterrows():
            code = str(row["证券代码"]).zfill(6)
            all_stocks[code] = str(row["证券简称"])
        print(f"       Got {len(all_stocks) - before} stocks")
    except Exception as e:
        print(f"       ERROR: {e}")

    time.sleep(0.5)

    # --- Shenzhen (via Tencent batch discovery) ---
    print("  [SZ] Fetching Shenzhen stocks (via Tencent API)...")
    sz_count = 0
    session = httpx.Client(timeout=10, headers={"User-Agent": "Mozilla/5.0"})

    # Shenzhen code ranges: 000001-003999 (Main), 300001-301999 (ChiNext)
    code_ranges = [(0, 4000), (300000, 302000)]
    for start, end in code_ranges:
        batch = []
        for i in range(start, end):
            code = str(i).zfill(6)
            batch.append(f"sz{code}")
            if len(batch) >= 50:
                sz_count += _tencent_discover_batch(session, batch, all_stocks)
                batch = []
                time.sleep(0.15)
        if batch:
            sz_count += _tencent_discover_batch(session, batch, all_stocks)

    print(f"       Discovered {sz_count} Shenzhen stocks")

    # --- Beijing Exchange ---
    print("  [BJ] Fetching Beijing Exchange (北交所)...")
    try:
        df3 = ak.stock_info_bj_name_code()
        bj_count = 0
        for _, row in df3.iterrows():
            code = str(row["证券代码"]).zfill(6)
            if code not in all_stocks:
                all_stocks[code] = str(row["证券简称"])
                bj_count += 1
        print(f"       Got {bj_count} stocks")
    except Exception as e:
        print(f"       ERROR: {e}")

    session.close()

    # --- Summary ---
    codes = sorted(all_stocks.keys())
    sh_main = [c for c in codes if c.startswith("60")]
    sh_star = [c for c in codes if c.startswith("68")]
    sz_main = [c for c in codes if c.startswith("00")]
    sz_gem  = [c for c in codes if c.startswith("30")]
    bj_all  = [c for c in codes if c.startswith(("43", "83", "87", "92"))]

    print(f"\n  {'='*50}")
    print(f"  TOTAL: {len(codes)} unique A-share stocks")
    print(f"    SH Main (60xxxx):    {len(sh_main)}")
    print(f"    SH STAR (68xxxx):    {len(sh_star)}")
    print(f"    SZ Main (00xxxx):    {len(sz_main)}")
    print(f"    SZ ChiNext (30xxxx): {len(sz_gem)}")
    print(f"    BJ Exchange:         {len(bj_all)}")

    # Save to CSV
    pd.DataFrame(
        [{"code": c, "name": all_stocks[c]} for c in codes]
    ).to_csv(DATA_DIR / "all_a_shares.csv", index=False, encoding="utf-8-sig")
    print(f"  Saved to data/all_a_shares.csv")

    # Also update all_constituents.csv for backward compatibility
    pd.DataFrame({"code": codes}).to_csv(
        DATA_DIR / "all_constituents.csv", index=False, encoding="utf-8-sig"
    )

    return [(c, all_stocks[c]) for c in codes]


def _tencent_discover_batch(
    client: httpx.Client,
    symbols: list[str],
    out: dict[str, str],
) -> int:
    """Query Tencent Finance for a batch of stock symbols. Returns newly added count."""
    url = f"http://qt.gtimg.cn/q={','.join(symbols)}"
    added = 0
    try:
        resp = client.get(url)
        text_data = resp.content.decode("gbk", errors="replace")
        for line in text_data.strip().split("\n"):
            if "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) < 5:
                continue
            code = parts[2].strip()
            name = parts[1].strip()
            if name and code and len(code) == 6 and code not in out:
                out[code] = name
                added += 1
    except Exception:
        pass
    return added


def code_to_market(code: str) -> str:
    """Convert stock code to market prefix (sh/sz/bj)."""
    code = str(code).zfill(6)
    if code.startswith(("60", "68")):
        return "sh"
    elif code.startswith(("00", "30")):
        return "sz"
    elif code.startswith(("43", "83", "87", "92")):
        return "bj"
    return "sh"


# ======================================================================
# Database helpers
# ======================================================================

async def ensure_stock_exists(session: AsyncSession, code: str, name: str = ""):
    """Ensure stock exists in stocks table, return stock_id."""
    market = code_to_market(code)
    result = await session.execute(
        text("SELECT id FROM stocks WHERE code = :code"), {"code": code}
    )
    row = result.first()
    if row:
        return row[0]

    result = await session.execute(
        text(
            "INSERT INTO stocks (code, name, market, is_active, is_favorite, created_at) "
            "VALUES (:code, :name, :market, true, false, NOW()) "
            "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING id"
        ),
        {"code": code, "name": name or code, "market": market},
    )
    await session.commit()
    return result.scalar()


async def get_existing_kline_counts(session: AsyncSession):
    """Get count of klines per stock_id."""
    result = await session.execute(
        text("SELECT stock_id, COUNT(*) FROM daily_klines GROUP BY stock_id")
    )
    return dict(result.all())


async def insert_klines_batch(session: AsyncSession, stock_id: int, df: pd.DataFrame, source: str):
    """Insert klines using upsert. Includes change_pct and created_at."""
    if df.empty:
        return 0

    # Compute change_pct if not present
    if "change_pct" not in df.columns:
        df = df.copy()
        df["change_pct"] = df["close"].pct_change() * 100
        df.loc[df.index[0], "change_pct"] = 0.0

    count = 0
    for _, row in df.iterrows():
        try:
            cp = float(row.get("change_pct", 0))
            if pd.isna(cp):
                cp = 0.0
            await session.execute(
                text("""
                    INSERT INTO daily_klines (stock_id, date, open, high, low, close, volume, amount, change_pct, turnover_rate, created_at)
                    VALUES (:sid, :td, :o, :h, :l, :c, :v, :a, :cp, :tr, NOW())
                    ON CONFLICT (stock_id, date) DO UPDATE SET
                        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                        close = EXCLUDED.close, volume = EXCLUDED.volume, amount = EXCLUDED.amount,
                        change_pct = EXCLUDED.change_pct, turnover_rate = EXCLUDED.turnover_rate
                """),
                {
                    "sid": stock_id,
                    "td": row["date"],
                    "o": float(row["open"]),
                    "h": float(row["high"]),
                    "l": float(row["low"]),
                    "c": float(row["close"]),
                    "v": int(row.get("volume", 0)),
                    "a": float(row.get("amount", 0)) if pd.notna(row.get("amount")) else 0,
                    "cp": cp,
                    "tr": float(row.get("turnover_rate", 0)) if pd.notna(row.get("turnover_rate")) else None,
                },
            )
            count += 1
        except Exception:
            await session.rollback()
    await session.commit()
    return count


async def write_fetch_log(session: AsyncSession, stock_id: int, fetch_type: str, source: str, status: str, message: str = ""):
    """Write a fetch log entry."""
    try:
        await session.execute(
            text("""
                INSERT INTO fetch_logs (stock_id, fetch_type, source, status, message, created_at)
                VALUES (:sid, :ft, :src, :st, :msg, NOW())
            """),
            {"sid": stock_id, "ft": fetch_type, "src": source, "st": status, "msg": message},
        )
        await session.commit()
    except Exception:
        await session.rollback()


# ======================================================================
# Step 2: Download K-line data (10-year history)
# ======================================================================

async def download_klines(stocks: list[tuple[str, str]], force: bool = False):
    """Download 10-year daily K-line data for all stocks via akshare (Sina backend)."""
    print("\n" + "=" * 60)
    print(f"Step 2: Downloading {KLINE_YEARS}-year daily K-lines (akshare/Sina)")
    print("=" * 60)

    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=KLINE_YEARS * 365)).strftime("%Y%m%d")
    print(f"  Date range: {start_date} ~ {end_date}")

    # Pre-populate stock_id map
    async with async_session() as session:
        existing_counts = await get_existing_kline_counts(session)
        stock_id_map = {}
        for code, name in stocks:
            sid = await ensure_stock_exists(session, code, name)
            stock_id_map[code] = sid

    success = 0
    skipped = 0
    failed = 0
    total = len(stocks)

    for i, (code, name) in enumerate(stocks, 1):
        stock_id = stock_id_map[code]

        # Check if we can skip
        existing = existing_counts.get(stock_id, 0)
        if not force and existing >= SKIP_THRESHOLD:
            skipped += 1
            continue

        market = code_to_market(code)
        symbol = f"{market}{code}"
        print(f"\r  [{i}/{total}] {code} {name} ({symbol}) ... ", end="", flush=True)

        try:
            df = ak.stock_zh_a_daily(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )

            if df is not None and not df.empty:
                # Normalize columns
                df = df.rename(columns={"turnover": "turnover_rate"})
                if "outstanding_share" in df.columns:
                    df = df.drop(columns=["outstanding_share"], errors="ignore")

                async with async_session() as session:
                    count = await insert_klines_batch(session, stock_id, df, "akshare_sina")
                    await write_fetch_log(
                        session, stock_id, "kline", "akshare_sina", "success",
                        f"Downloaded {count} klines ({start_date}-{end_date})",
                    )
                success += 1
                print(f"{len(df)} rows", flush=True)
            else:
                async with async_session() as session:
                    await write_fetch_log(session, stock_id, "kline", "akshare_sina", "error", "Empty response")
                failed += 1
                print("empty", flush=True)

        except Exception as e:
            err_msg = str(e)[:200]
            async with async_session() as session:
                await write_fetch_log(session, stock_id, "kline", "akshare_sina", "error", err_msg)
            failed += 1
            print(f"ERROR: {err_msg[:60]}", flush=True)

        # Rate limiting
        delay = random.uniform(0.3, 0.8)
        if i % 50 == 0:
            delay = random.uniform(5, 10)
            print(f"\n  --- Batch cooldown ({delay:.1f}s) after {i} stocks ---")
            print(f"  --- Progress: success={success}, skipped={skipped}, failed={failed} ---")
        time.sleep(delay)

    await engine.dispose()

    print(f"\n\nK-line download complete:")
    print(f"  Success: {success}, Skipped: {skipped}, Failed: {failed}")
    return success


# ======================================================================
# Step 3: Download fundamentals (PE/PB/market cap from Tencent)
# ======================================================================

async def download_fundamentals(stocks: list[tuple[str, str]]):
    """Download PE/PB/market cap from Tencent Finance API (batch, fast)."""
    print("\n" + "=" * 60)
    print("Step 3: Downloading fundamentals (Tencent Finance)")
    print("=" * 60)

    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.qq.com/",
    }

    codes = [s[0] for s in stocks]

    # Build stock_id map
    async with async_session() as session:
        stock_id_map = {}
        for code, name in stocks:
            sid = await ensure_stock_exists(session, code, name)
            stock_id_map[code] = sid

    # Tencent supports batch queries (up to ~50 per request)
    batch_size = 40
    success = 0
    failed = 0

    for batch_start in range(0, len(codes), batch_size):
        batch = codes[batch_start:batch_start + batch_size]
        symbols = [f"{code_to_market(c)}{c}" for c in batch]
        url = f"http://qt.gtimg.cn/q={','.join(symbols)}"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=15)
            text_data = resp.content.decode("gbk", errors="replace")

            for line in text_data.strip().split("\n"):
                if "~" not in line:
                    continue
                parts = line.split("~")
                if len(parts) < 50:
                    continue

                code = parts[2].strip()
                name = parts[1].strip()
                stock_id = stock_id_map.get(code)
                if not stock_id:
                    continue

                try:
                    pe = float(parts[39]) if parts[39].strip() else None
                    pb = float(parts[46]) if parts[46].strip() else None
                    total_mcap = float(parts[45]) if parts[45].strip() else None  # 亿元
                    circ_mcap = float(parts[44]) if parts[44].strip() else None   # 亿元
                except (ValueError, IndexError):
                    pe = pb = total_mcap = circ_mcap = None

                # Update stock name
                async with async_session() as session:
                    await session.execute(
                        text("UPDATE stocks SET name = :name WHERE id = :sid AND (name = '' OR name = code)"),
                        {"name": name, "sid": stock_id},
                    )

                    # Upsert stock_profiles
                    await session.execute(
                        text("""
                            INSERT INTO stock_profiles (stock_id, pe_ttm, pb, total_market_cap, circ_market_cap, updated_at)
                            VALUES (:sid, :pe, :pb, :tmcap, :cmcap, NOW())
                            ON CONFLICT (stock_id) DO UPDATE SET
                                pe_ttm = EXCLUDED.pe_ttm,
                                pb = EXCLUDED.pb,
                                total_market_cap = EXCLUDED.total_market_cap,
                                circ_market_cap = EXCLUDED.circ_market_cap,
                                updated_at = NOW()
                        """),
                        {"sid": stock_id, "pe": pe, "pb": pb, "tmcap": total_mcap, "cmcap": circ_mcap},
                    )
                    await session.commit()

                    await write_fetch_log(
                        session, stock_id, "fundamental", "tencent", "success",
                        f"PE={pe}, PB={pb}, MCap={total_mcap}亿",
                    )

                success += 1

        except Exception as e:
            print(f"\n  Batch error at {batch_start}: {e}")
            failed += len(batch)

        progress = min(batch_start + batch_size, len(codes))
        print(f"\r  Progress: {progress}/{len(codes)} stocks processed ({success} ok)", end="", flush=True)
        time.sleep(random.uniform(0.3, 0.6))

    await engine.dispose()

    print(f"\n\nFundamentals download complete:")
    print(f"  Success: {success}, Failed: {failed}")
    return success


# ======================================================================
# Step 4: Download industry data (EastMoney individual info)
# ======================================================================

async def download_industry_data(stocks: list[tuple[str, str]]):
    """Try to get industry classification from akshare (EastMoney individual info)."""
    print("\n" + "=" * 60)
    print("Step 4: Downloading industry data (EastMoney individual)")
    print("=" * 60)

    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    codes = [s[0] for s in stocks]

    async with async_session() as session:
        stock_id_map = {}
        for code, name in stocks:
            sid = await ensure_stock_exists(session, code, name)
            stock_id_map[code] = sid

        # Check which stocks already have industry data
        result = await session.execute(
            text("SELECT stock_id FROM stock_profiles WHERE industry IS NOT NULL AND industry ~ '[\u4e00-\u9fff]'")
        )
        has_industry = {r[0] for r in result.all()}

    codes_needing_industry = [c for c in codes if stock_id_map.get(c) not in has_industry]
    print(f"  {len(codes_needing_industry)} stocks need industry data ({len(has_industry)} already have it)")

    if not codes_needing_industry:
        print("  All stocks already have industry data. Skipping.")
        await engine.dispose()
        return 0

    success = 0
    failed = 0
    consecutive_errors = 0

    for i, code in enumerate(codes_needing_industry, 1):
        stock_id = stock_id_map[code]

        print(f"\r  [{i}/{len(codes_needing_industry)}] {code} ... ", end="", flush=True)

        try:
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                info = dict(zip(df["item"], df["value"]))
                industry = info.get("行业", "")

                if not industry or not any('\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf' for c in industry):
                    failed += 1
                    print(f"行业=无效({industry})", flush=True)
                    continue

                async with async_session() as session:
                    await session.execute(
                        text("""
                            INSERT INTO stock_profiles (stock_id, industry, updated_at)
                            VALUES (:sid, :ind, NOW())
                            ON CONFLICT (stock_id) DO UPDATE SET
                                industry = COALESCE(EXCLUDED.industry, stock_profiles.industry),
                                updated_at = NOW()
                        """),
                        {"sid": stock_id, "ind": industry},
                    )
                    await session.commit()

                success += 1
                consecutive_errors = 0
                print(f"行业={industry}", flush=True)
            else:
                failed += 1

        except Exception as e:
            err = str(e)[:80]
            print(f"ERROR: {err}", flush=True)
            failed += 1
            consecutive_errors += 1

            if consecutive_errors >= 10:
                print(f"\n  *** Too many consecutive errors ({consecutive_errors}), stopping industry download ***")
                print(f"  *** EastMoney may be rate-limiting. Try again later with --industry-only ***")
                break

        # Aggressive rate limiting for EastMoney
        delay = random.uniform(1.0, 2.0)
        if i % 20 == 0:
            delay = random.uniform(8, 15)
            print(f"\n  --- Cooldown ({delay:.1f}s) after {i} stocks ---")
        time.sleep(delay)

    await engine.dispose()

    print(f"\n\nIndustry download complete:")
    print(f"  Success: {success}, Failed: {failed}")
    return success


# ======================================================================
# Main
# ======================================================================

async def main():
    parser = argparse.ArgumentParser(description="AStock Full A-Share Data Downloader")
    parser.add_argument("--klines-only", action="store_true", help="Only download K-lines")
    parser.add_argument("--fundamentals-only", action="store_true", help="Only download PE/PB/market cap")
    parser.add_argument("--industry-only", action="store_true", help="Only download industry data")
    parser.add_argument("--force", action="store_true", help="Force re-download even if data exists")
    parser.add_argument("--skip-klines", action="store_true", help="Skip K-line download")
    args = parser.parse_args()

    print("AStock Full A-Share Data Downloader")
    print(f"  Coverage: ALL A-shares (SH + SZ + BJ)")
    print(f"  K-line history: {KLINE_YEARS} years")
    print(f"  Data directory: {DATA_DIR}")
    print(f"  Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Step 1: Get full A-share stock list
    stocks = get_all_a_shares()
    if not stocks:
        # Fallback: load from cache
        cache_path = DATA_DIR / "all_a_shares.csv"
        if cache_path.exists():
            df = pd.read_csv(cache_path, dtype=str)
            stocks = [(row["code"].zfill(6), row.get("name", "")) for _, row in df.iterrows()]
            print(f"  Loaded {len(stocks)} stocks from cache")
        else:
            print("ERROR: No stock codes found. Exiting.")
            return

    # Determine what to run
    run_all = not (args.klines_only or args.fundamentals_only or args.industry_only)

    # Step 2: K-lines
    if (run_all and not args.skip_klines) or args.klines_only:
        await download_klines(stocks, force=args.force)

    # Step 3: Fundamentals (PE/PB/market cap from Tencent — fast, batch)
    if run_all or args.fundamentals_only:
        await download_fundamentals(stocks)

    # Step 4: Industry (from EastMoney, slow, may hit rate limits)
    if run_all or args.industry_only:
        await download_industry_data(stocks)

    print(f"\nAll done! End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    asyncio.run(main())
