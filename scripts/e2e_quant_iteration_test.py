#!/usr/bin/env python3
"""
End-to-end quantitative analysis iteration test.

Scenario:
  - Simulated start date: 2025-09-25
  - Universe preset: CSI500-like (market_cap 100-800亿, PE>0, PB>0)
  - Portfolio size: 10 (top_n=10)
  - Rebalance frequency: 10 trading days
  - Industry neutral: True
  - Backtest/training period: ~2 years of data before each analysis date

The script:
  1. Runs the REAL analysis engine on kline data truncated to 2025-09-25
  2. Creates the initial portfolio recommendation (iteration #0)
  3. Steps forward 10 trading days at a time
  4. At each step: calculates actual period returns, re-runs analysis
     with expanded data, creates new iteration record
  5. Continues until the present (2026-03-25)
  6. All records are written to the DB and visible in the GUI

Usage:
  cd /home/cyc/xun3/AStock
  python scripts/e2e_quant_iteration_test.py
"""

import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend'))

import uuid
import time
import logging
from datetime import date, datetime, timedelta
from collections import defaultdict

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Base, Stock, DailyKline, StockProfile,
    QuantBacktestResult, QuantIteration,
)
from app.services.quant_engine import (
    run_cross_sectional_analysis, PortfolioConfig, BacktestConfig,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
)
logger = logging.getLogger("e2e_test")

# ─── Test Configuration ──────────────────────────────────────────────

PRESET = "csi500_like"
TOP_N = 10
REBALANCE_FREQ = 10  # trading days
INDUSTRY_NEUTRAL = True
SIM_START_DATE = date(2025, 9, 25)
KLINE_LOOKBACK_DAYS = 750  # calendar days (~500 trading days, ~2 years)

FILTERS_APPLIED = {
    "preset": "csi500_like",
    "industries": None,
    "market_cap_min": 100,
    "market_cap_max": 800,
    "pe_min": 0.01,
    "pe_max": None,
    "pb_min": 0.01,
    "pb_max": None,
    "markets": None,
    "backtest_start": None,
    "backtest_end": None,
}

PORTFOLIO_CONFIG = PortfolioConfig(
    top_n=TOP_N,
    rebalance_freq=REBALANCE_FREQ,
    industry_neutral=INDUSTRY_NEUTRAL,
    buffer_in=max(TOP_N - 5, int(TOP_N * 0.8)),   # 8
    buffer_out=min(TOP_N + 10, int(TOP_N * 1.3)),  # 13
)


# ─── Helper Functions ────────────────────────────────────────────────

def get_trading_dates(db: Session, start: date, end: date) -> list:
    """Get distinct trading dates from kline data in [start, end]."""
    rows = db.execute(text("""
        SELECT DISTINCT date FROM daily_klines
        WHERE date >= :s AND date <= :e
        ORDER BY date
    """), {"s": start, "e": end}).all()
    return [r[0] for r in rows]


def load_stocks_csi500(db: Session) -> tuple:
    """Load stocks matching CSI500-like filter criteria.

    Returns: (stock_ids, stock_info)
    """
    rows = db.execute(
        select(Stock, StockProfile)
        .join(StockProfile, Stock.id == StockProfile.stock_id)
        .where(
            Stock.is_active == True,
            StockProfile.total_market_cap >= 100,
            StockProfile.total_market_cap <= 800,
            StockProfile.pe_ttm >= 0.01,
            StockProfile.pb >= 0.01,
        )
    ).all()

    stock_ids = []
    stock_info = {}
    for s, p in rows:
        stock_ids.append(s.id)
        stock_info[s.id] = {
            "code": s.code,
            "name": s.name,
            "market": s.market,
            "industry": p.industry,
            "pe_ttm": p.pe_ttm,
            "pb": p.pb,
            "total_market_cap": p.total_market_cap,
        }
    return stock_ids, stock_info


def load_klines_truncated(
    db: Session,
    stock_ids: list,
    as_of_date: date,
    lookback_days: int = KLINE_LOOKBACK_DAYS,
    min_rows: int = 120,
) -> dict:
    """Load kline data for given stocks, truncated to [as_of_date - lookback, as_of_date].

    This simulates only seeing data available up to `as_of_date`.
    Returns dict of {stock_id: pd.DataFrame}.
    """
    cutoff_start = as_of_date - timedelta(days=lookback_days)

    # Batch query
    kline_rows = db.execute(
        select(DailyKline)
        .where(
            DailyKline.stock_id.in_(stock_ids),
            DailyKline.date >= cutoff_start,
            DailyKline.date <= as_of_date,
        )
        .order_by(DailyKline.stock_id, DailyKline.date)
    ).scalars().all()

    grouped = defaultdict(list)
    for r in kline_rows:
        grouped[r.stock_id].append({
            "date": r.date,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "amount": r.amount,
            "change_pct": r.change_pct,
            "turnover_rate": r.turnover_rate or 0,
        })

    all_klines = {}
    for sid, data in grouped.items():
        if len(data) >= min_rows:
            all_klines[sid] = pd.DataFrame(data)

    return all_klines


def calculate_period_returns(
    db: Session,
    portfolio: list,
    start_date: date,
    end_date: date,
) -> dict:
    """Calculate equal-weighted period returns for a portfolio.

    Same logic as quant_iterate.calculate_period_returns but standalone.
    """
    if not portfolio:
        return {"period_return_pct": 0.0, "stock_returns": []}

    codes = [s.get("stock_code") or s.get("code", "") for s in portfolio]
    if not codes:
        return {"period_return_pct": 0.0, "stock_returns": []}

    rows = db.execute(
        select(Stock.id, Stock.code, Stock.name).where(Stock.code.in_(codes))
    ).all()
    code_to_id = {r[1]: r[0] for r in rows}
    code_to_name = {r[1]: r[2] for r in rows}

    stock_ids = list(code_to_id.values())
    if not stock_ids:
        return {"period_return_pct": 0.0, "stock_returns": []}

    kline_rows = db.execute(
        select(DailyKline.stock_id, DailyKline.date, DailyKline.close)
        .where(
            DailyKline.stock_id.in_(stock_ids),
            DailyKline.date >= start_date - timedelta(days=5),
            DailyKline.date <= end_date + timedelta(days=5),
        )
        .order_by(DailyKline.stock_id, DailyKline.date)
    ).all()

    klines_by_stock = defaultdict(list)
    for sid, d, close in kline_rows:
        klines_by_stock[sid].append((d, close))

    stock_returns = []
    total_return = 0.0
    valid_count = 0

    for code in codes:
        sid = code_to_id.get(code)
        if not sid or sid not in klines_by_stock:
            stock_returns.append({
                "code": code, "name": code_to_name.get(code, ""),
                "start_price": None, "end_price": None, "return_pct": None,
            })
            continue

        klines = klines_by_stock[sid]
        start_price = None
        for d, c in klines:
            if d <= start_date:
                start_price = c
            elif start_price is None:
                start_price = c
                break

        end_price = None
        for d, c in klines:
            if d <= end_date:
                end_price = c

        if start_price and end_price and start_price > 0:
            ret = (end_price - start_price) / start_price * 100
            stock_returns.append({
                "code": code, "name": code_to_name.get(code, ""),
                "start_price": round(float(start_price), 2),
                "end_price": round(float(end_price), 2),
                "return_pct": round(ret, 2),
            })
            total_return += ret
            valid_count += 1
        else:
            stock_returns.append({
                "code": code, "name": code_to_name.get(code, ""),
                "start_price": float(start_price) if start_price else None,
                "end_price": float(end_price) if end_price else None,
                "return_pct": None,
            })

    avg_return = total_return / valid_count if valid_count > 0 else 0.0
    return {
        "period_return_pct": round(avg_return, 4),
        "stock_returns": stock_returns,
    }


def simplify_portfolio(stocks: list) -> list:
    """Simplify portfolio for QuantIteration storage."""
    return [
        {
            "stock_code": s.get("stock_code", ""),
            "stock_name": s.get("stock_name", ""),
            "industry": s.get("industry", ""),
            "score": s.get("score", 0),
            "rank": s.get("rank", 0),
        }
        for s in stocks
    ]


# ─── Main Test Runner ────────────────────────────────────────────────

def main():
    logger.info("=" * 70)
    logger.info("E2E Quant Iteration Test: CSI500-like, Top10, 10-day rebalance")
    logger.info(f"Simulated start: {SIM_START_DATE}")
    logger.info("=" * 70)

    engine = create_engine(settings.DATABASE_URL_SYNC, echo=False)

    with Session(engine) as db:
        # Step 1: Load stock universe
        logger.info("Loading CSI500-like stock universe...")
        stock_ids, stock_info = load_stocks_csi500(db)
        logger.info(f"Found {len(stock_ids)} stocks matching CSI500-like filter")

        # Step 2: Get trading dates from sim start to today
        today = date.today()
        trading_dates = get_trading_dates(db, SIM_START_DATE, today)
        logger.info(f"Trading dates from {SIM_START_DATE} to {today}: {len(trading_dates)} days")

        if len(trading_dates) < REBALANCE_FREQ:
            logger.error("Not enough trading dates for even one rebalance period!")
            return

        # Build iteration schedule: every REBALANCE_FREQ trading days
        iter_dates = []
        i = 0
        while i < len(trading_dates):
            iter_dates.append(trading_dates[i])
            i += REBALANCE_FREQ

        logger.info(f"Iteration schedule ({len(iter_dates)} iterations):")
        for idx, d in enumerate(iter_dates):
            logger.info(f"  Iteration #{idx}: {d}")

        # Step 3: Generate unique run_id for this test run
        run_id = str(uuid.uuid4())
        logger.info(f"Run ID: {run_id}")

        # Step 4: Run analysis for each iteration date
        nav = 1.0
        cumulative_return = 0.0
        prev_portfolio_simplified = None
        all_iterations = []
        initial_result = None  # Store first analysis result for QuantBacktestResult

        for iter_idx, iter_date in enumerate(iter_dates):
            is_last = (iter_idx == len(iter_dates) - 1)

            logger.info("-" * 60)
            logger.info(f"ITERATION #{iter_idx} | Date: {iter_date} | NAV: {nav:.4f}")

            # 4a. If not the first iteration, calculate returns for previous period
            if iter_idx > 0 and prev_portfolio_simplified:
                prev_start = iter_dates[iter_idx - 1]
                prev_end = iter_date

                logger.info(f"  Calculating period returns: {prev_start} -> {prev_end}")
                ret_info = calculate_period_returns(
                    db, prev_portfolio_simplified, prev_start, prev_end,
                )
                period_ret = ret_info["period_return_pct"]
                nav = nav * (1 + period_ret / 100)
                cumulative_return = (nav - 1.0) * 100

                # Update previous iteration as completed
                prev_iter = all_iterations[-1]
                prev_iter["end_date"] = prev_end
                prev_iter["period_return_pct"] = round(period_ret, 4)
                prev_iter["cumulative_return_pct"] = round(cumulative_return, 4)
                prev_iter["nav_at_end"] = round(nav, 6)
                prev_iter["stock_returns"] = ret_info["stock_returns"]
                prev_iter["status"] = "completed"
                prev_iter["message"] = (
                    f"期间收益 {period_ret:+.2f}%, 累计收益 {cumulative_return:+.2f}%, "
                    f"NAV {nav:.4f}"
                )

                logger.info(f"  Period return: {period_ret:+.2f}% | Cumulative: {cumulative_return:+.2f}% | NAV: {nav:.4f}")
                for sr in ret_info["stock_returns"]:
                    rp = sr["return_pct"]
                    rp_str = f"{rp:+.2f}%" if rp is not None else "N/A"
                    logger.info(f"    {sr['code']} {sr['name']}: {sr.get('start_price', '?')} -> {sr.get('end_price', '?')} ({rp_str})")

            # 4b. Load klines truncated to this date and run analysis
            logger.info(f"  Loading klines truncated to {iter_date}...")
            t0 = time.time()
            all_klines = load_klines_truncated(db, stock_ids, iter_date)
            load_time = time.time() - t0
            logger.info(f"  Loaded klines for {len(all_klines)} stocks in {load_time:.1f}s")

            if len(all_klines) < 30:
                logger.error(f"  Not enough stocks with kline data ({len(all_klines)}), skipping")
                continue

            bt_config = BacktestConfig(
                start_date=str(iter_date - timedelta(days=KLINE_LOOKBACK_DAYS)),
                end_date=str(iter_date),
            )

            logger.info(f"  Running cross-sectional analysis...")
            t0 = time.time()
            result = run_cross_sectional_analysis(
                all_klines, stock_info, PORTFOLIO_CONFIG, bt_config,
            )
            analysis_time = time.time() - t0
            logger.info(f"  Analysis completed in {analysis_time:.1f}s | success={result.get('success')}")

            if not result.get("success"):
                logger.error(f"  Analysis failed: {result.get('error')}")
                continue

            # Store first result for the main record
            if initial_result is None:
                initial_result = result

            portfolio_stocks = result.get("portfolio", {}).get("stocks", [])
            new_portfolio = simplify_portfolio(portfolio_stocks)

            # Compute buys/sells
            prev_codes = set()
            if prev_portfolio_simplified:
                prev_codes = {s["stock_code"] for s in prev_portfolio_simplified}
            new_codes = {s["stock_code"] for s in new_portfolio}
            buys = sorted(new_codes - prev_codes)
            sells = sorted(prev_codes - new_codes)

            logger.info(f"  Portfolio: {len(new_portfolio)} stocks | Buys: {len(buys)} | Sells: {len(sells)}")
            for s in new_portfolio:
                logger.info(f"    #{s['rank']} {s['stock_code']} {s['stock_name']} ({s['industry']}) score={s['score']}")

            # 4c. Record this iteration
            iter_record = {
                "iteration_num": iter_idx,
                "start_date": iter_date,
                "end_date": None,  # Will be set by next iteration
                "portfolio": new_portfolio,
                "full_portfolio": portfolio_stocks,  # Full version for current_portfolio
                "new_buys": buys,
                "new_sells": sells,
                "period_return_pct": None,
                "cumulative_return_pct": None,
                "nav_at_start": round(nav, 6),
                "nav_at_end": None,
                "stock_returns": None,
                "status": "active",
                "message": f"第{iter_idx}期 | 持仓{len(new_portfolio)}只 | 买入{len(buys)} 卖出{len(sells)}",
                "analysis_result": result,
                "analysis_time": analysis_time,
            }

            if iter_idx == 0:
                iter_record["message"] = f"初始持仓 | {len(new_portfolio)}只股票"
                iter_record["new_buys"] = [s["stock_code"] for s in new_portfolio]
                iter_record["new_sells"] = []

            all_iterations.append(iter_record)
            prev_portfolio_simplified = new_portfolio

        # Step 5: Write all records to DB
        logger.info("=" * 60)
        logger.info("Writing records to database...")

        if not initial_result:
            logger.error("No successful analysis results, cannot create DB records")
            return

        # Determine the last completed iteration for live_nav
        last_completed_idx = -1
        for i, it in enumerate(all_iterations):
            if it["status"] == "completed":
                last_completed_idx = i

        final_nav = nav
        final_return = cumulative_return

        # Use the last iteration's analysis result for backtest metrics
        last_result = all_iterations[-1]["analysis_result"] if all_iterations else initial_result
        backtest = last_result.get("backtest", {})

        # Create the main QuantBacktestResult record
        record = QuantBacktestResult(
            run_id=run_id,
            created_at=datetime(2025, 9, 25, 16, 30, 0),  # Simulated creation time
            name=f"[E2E测试] 中证500风格 Top{TOP_N} 行业中性 (2025-09-25起)",
            notes=(
                f"端到端验证测试：从{SIM_START_DATE}开始，每{REBALANCE_FREQ}个交易日换仓一次，"
                f"共{len(all_iterations)}期迭代。使用真实K线数据和分析引擎。"
            ),
            analysis_date=str(SIM_START_DATE),
            elapsed_seconds=initial_result.get("elapsed_seconds"),
            filters_applied=FILTERS_APPLIED,
            universe_info=initial_result.get("universe"),
            signal_info=initial_result.get("signal"),
            ml_model_info=initial_result.get("ml_model"),
            factors=initial_result.get("factors"),
            config={
                "top_n": TOP_N,
                "rebalance_freq": REBALANCE_FREQ,
                "buffer_in": PORTFOLIO_CONFIG.buffer_in,
                "buffer_out": PORTFOLIO_CONFIG.buffer_out,
                "industry_neutral": INDUSTRY_NEUTRAL,
            },
            total_return=backtest.get("total_return"),
            annual_return=backtest.get("annual_return"),
            annual_volatility=backtest.get("annual_volatility"),
            sharpe_ratio=backtest.get("sharpe_ratio"),
            max_drawdown=backtest.get("max_drawdown"),
            calmar_ratio=backtest.get("calmar_ratio"),
            win_rate=backtest.get("win_rate"),
            avg_turnover=backtest.get("annual_turnover"),
            total_cost=backtest.get("total_cost_pct"),
            equity_curve=backtest.get("equity_curve"),
            monthly_returns=backtest.get("monthly_returns"),
            current_portfolio=all_iterations[-1]["full_portfolio"] if all_iterations else None,
            data_splits=initial_result.get("data_splits"),
            # Auto-iteration fields
            auto_iterate=True,
            iterate_status="idle",
            next_iterate_date=all_iterations[-1]["start_date"] + timedelta(days=int(REBALANCE_FREQ * 7 / 5) + 1) if all_iterations else None,
            total_iterations=len(all_iterations) - 1,  # Completed iterations (last one is active)
            live_nav=round(final_nav, 6),
            live_return_pct=round(final_return, 4),
        )
        db.add(record)
        db.flush()

        logger.info(f"Created QuantBacktestResult: run_id={run_id}")

        # Create QuantIteration records
        for it in all_iterations:
            iter_nav = it["nav_at_end"] if it["status"] == "completed" else it["nav_at_start"]

            qi = QuantIteration(
                run_id=run_id,
                iteration_num=it["iteration_num"],
                start_date=it["start_date"],
                end_date=it["end_date"],
                portfolio=it["portfolio"],
                new_buys=it["new_buys"],
                new_sells=it["new_sells"],
                period_return_pct=it["period_return_pct"],
                cumulative_return_pct=it["cumulative_return_pct"],
                nav=round(float(iter_nav or 1.0), 6),
                stock_returns=it["stock_returns"],
                status=it["status"],
                message=it["message"],
                created_at=datetime.combine(it["start_date"], datetime.min.time().replace(hour=16, minute=30)),
                completed_at=(
                    datetime.combine(it["end_date"], datetime.min.time().replace(hour=16, minute=30))
                    if it["status"] == "completed" and it["end_date"]
                    else None
                ),
            )
            db.add(qi)
            logger.info(
                f"  Iteration #{it['iteration_num']}: {it['start_date']} -> {it.get('end_date', '(active)')} "
                f"| status={it['status']} | nav={iter_nav}"
            )

        db.commit()
        logger.info(f"All records committed to database!")

        # Step 6: Summary
        logger.info("=" * 60)
        logger.info("TEST SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Run ID: {run_id}")
        logger.info(f"Name: [E2E测试] 中证500风格 Top{TOP_N} 行业中性")
        logger.info(f"Start date: {SIM_START_DATE}")
        logger.info(f"Total iterations: {len(all_iterations)}")
        completed = sum(1 for it in all_iterations if it["status"] == "completed")
        active = sum(1 for it in all_iterations if it["status"] == "active")
        logger.info(f"  Completed: {completed}")
        logger.info(f"  Active: {active}")
        logger.info(f"Final NAV: {final_nav:.4f}")
        logger.info(f"Cumulative return: {final_return:+.2f}%")
        logger.info(f"Next iterate date: {record.next_iterate_date}")
        logger.info("")
        logger.info("Iteration timeline:")
        for it in all_iterations:
            ret_str = f"{it['period_return_pct']:+.2f}%" if it['period_return_pct'] is not None else "pending"
            cum_str = f"{it['cumulative_return_pct']:+.2f}%" if it['cumulative_return_pct'] is not None else "pending"
            logger.info(
                f"  #{it['iteration_num']:2d} | {it['start_date']} -> "
                f"{str(it.get('end_date') or '(active)'):>12s} | "
                f"ret={ret_str:>8s} | cum={cum_str:>8s} | "
                f"stocks={len(it['portfolio']):2d} | {it['status']}"
            )
        logger.info("")
        logger.info("You can now view this record in the GUI:")
        logger.info("  1. Open the frontend (http://localhost:5174)")
        logger.info("  2. Go to '量化选股' section")
        logger.info("  3. Click '分析历史' tab")
        logger.info(f"  4. Look for '[E2E测试] 中证500风格 Top{TOP_N} 行业中性'")
        logger.info("  5. Click the iteration button to see the full timeline")
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
