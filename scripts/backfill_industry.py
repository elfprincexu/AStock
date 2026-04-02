#!/usr/bin/env python3
"""
Backfill industry data for stocks with NULL industry in stock_profiles.

Strategy:
1. Deactivate obviously delisted stocks (name contains "退")
2. For remaining active stocks with NULL industry, fetch from EastMoney F10 API
   (CompanySurvey endpoint) and extract EM2016 first-level industry category.
3. Update stock_profiles.industry with the fetched data.

Usage:
    python scripts/backfill_industry.py [--dry-run]
"""

import argparse
import re
import sys
import time

import httpx
from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://astock:astock123@localhost:5432/astock"
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://emweb.securities.eastmoney.com/",
}


def fetch_industry_f10(code: str, market: str) -> dict:
    """Fetch stock info from EastMoney F10 CompanySurvey API.

    Returns dict with keys: industry, security_type, name, em2016_full, is_delisted
    """
    prefix = "SH" if market == "SH" or code.startswith("6") else "SZ"
    if code.startswith(("4", "8", "92")):
        prefix = "BJ"

    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={prefix}{code}"
    r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    data = r.json()

    jbzl_list = data.get("jbzl", [])
    if not jbzl_list:
        return {"industry": None, "is_delisted": True, "em2016_full": None, "security_type": None, "name": None}

    jbzl = jbzl_list[0]
    em2016 = jbzl.get("EM2016", "")
    security_type = jbzl.get("SECURITY_TYPE", "")
    name = jbzl.get("SECURITY_NAME_ABBR", "")

    # Detect delisted / risk warning stocks
    is_delisted = False
    if "退" in (name or ""):
        is_delisted = True
    if "风险警示" in (security_type or "") or "退市" in (security_type or ""):
        is_delisted = True

    # Extract first-level industry from EM2016 (e.g., "食品饮料-饮料-白酒" -> "食品饮料")
    industry = None
    if em2016 and CJK_RE.search(em2016):
        parts = em2016.split("-")
        industry = parts[0].strip()
        # Clean up overly long category names
        if "、" in industry and len(industry) > 6:
            # e.g., "休闲、生活及专业服务" -> take first part
            industry = industry.split("、")[0] + "服务"

    return {
        "industry": industry,
        "is_delisted": is_delisted,
        "em2016_full": em2016,
        "security_type": security_type,
        "name": name,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    args = parser.parse_args()

    engine = create_engine(DATABASE_URL)

    with engine.connect() as db:
        # ── Step 1: Deactivate obviously delisted stocks ──
        print("=" * 60)
        print("Step 1: Deactivating delisted stocks (name contains '退')")
        print("=" * 60)

        result = db.execute(text(
            "SELECT id, code, name FROM stocks WHERE is_active = TRUE AND name LIKE '%退%'"
        ))
        delisted = result.all()
        print(f"  Found {len(delisted)} active stocks with '退' in name")

        if delisted and not args.dry_run:
            db.execute(text(
                "UPDATE stocks SET is_active = FALSE WHERE is_active = TRUE AND name LIKE '%退%'"
            ))
            db.commit()
            print(f"  Deactivated {len(delisted)} stocks")
        elif args.dry_run:
            print("  [dry-run] Would deactivate these stocks")
            for s in delisted[:10]:
                print(f"    {s[1]} {s[2]}")
            if len(delisted) > 10:
                print(f"    ... and {len(delisted) - 10} more")

        # ── Step 2: Fetch industry for NULL-industry active stocks ──
        print()
        print("=" * 60)
        print("Step 2: Fetching industry for NULL-industry active stocks")
        print("=" * 60)

        result = db.execute(text("""
            SELECT s.id, s.code, s.name, s.market
            FROM stock_profiles sp
            JOIN stocks s ON s.id = sp.stock_id
            WHERE sp.industry IS NULL AND s.is_active = TRUE
            ORDER BY s.code
        """))
        stocks = result.all()
        print(f"  {len(stocks)} stocks need industry data")

        if not stocks:
            print("  Nothing to do!")
            return

        success = 0
        failed = 0
        deactivated = 0
        no_data = 0
        consecutive_errors = 0

        for i, (stock_id, code, name, market) in enumerate(stocks, 1):
            print(f"\r  [{i}/{len(stocks)}] {code} {name} ... ", end="", flush=True)

            try:
                info = fetch_industry_f10(code, market or "")
                consecutive_errors = 0

                if info["is_delisted"]:
                    # Deactivate this stock
                    if not args.dry_run:
                        db.execute(text(
                            "UPDATE stocks SET is_active = FALSE WHERE id = :id"
                        ), {"id": stock_id})
                        db.commit()
                    deactivated += 1
                    print(f"退市 -> 停用 (type={info['security_type']})")
                    continue

                industry = info["industry"]
                if industry and CJK_RE.search(industry):
                    if not args.dry_run:
                        db.execute(text(
                            "UPDATE stock_profiles SET industry = :ind, updated_at = NOW() WHERE stock_id = :sid"
                        ), {"ind": industry, "sid": stock_id})
                        db.commit()
                    success += 1
                    print(f"行业={industry} (EM2016={info['em2016_full']})")
                else:
                    no_data += 1
                    print(f"无行业数据 (EM2016={info['em2016_full']})")

            except Exception as e:
                err = str(e)[:80]
                print(f"ERROR: {err}")
                failed += 1
                consecutive_errors += 1

                if consecutive_errors >= 10:
                    print(f"\n  *** Too many consecutive errors ({consecutive_errors}), stopping ***")
                    break

            # Rate limiting: 1.5s between requests
            time.sleep(1.5)

        print()
        print("=" * 60)
        print(f"Done! Success={success}, NoData={no_data}, Deactivated={deactivated}, Failed={failed}")
        print("=" * 60)


if __name__ == "__main__":
    main()
