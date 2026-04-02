#!/usr/bin/env python3
"""
End-to-end walk-forward iteration simulation.

Simulates the quant auto-iteration feature as if it had been running from
2025-03-25, rebalancing every 10 trading days with the csi300_like preset,
top_n=10, industry_neutral=True.

Each iteration:
  1. Truncate kline data to only the dates visible at that point.
  2. Run run_cross_sectional_analysis() to get a new portfolio.
  3. On subsequent iterations compute the actual period returns using
     the *real* kline data for the 10-day holding window.
  4. Write QuantBacktestResult + QuantIteration rows to the database
     so the result appears in the frontend's history tab.

Usage:
    cd backend
    python ../scripts/simulate_iteration_history.py
"""

import sys, os, uuid, time, logging
from datetime import date, datetime, timedelta
from collections import defaultdict
from pathlib import Path

# Make sure backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import pandas as pd
from sqlalchemy import create_engine, select, func, text
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
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sim")

# ── Parameters ────────────────────────────────────────────────────────
START_DATE = date(2025, 3, 25)
TOP_N = 10
REBALANCE_FREQ = 10
INDUSTRY_NEUTRAL = True
PRESET = "csi300_like"
FILTERS = {"market_cap_min": 500, "pe_min": 0.01}  # same as csi300_like

# ── DB ────────────────────────────────────────────────────────────────
engine = create_engine(settings.DATABASE_URL_SYNC, echo=False)
Base.metadata.create_all(engine)          # ensure new tables exist


# ── Helpers ───────────────────────────────────────────────────────────

def load_all_data(db: Session):
    """Load filtered stocks + ALL kline data (we'll truncate per-iteration)."""
    log.info("Loading stock list with CSI300-like filters …")

    rows = db.execute(
        select(Stock, StockProfile)
        .join(StockProfile, Stock.id == StockProfile.stock_id)
        .where(
            Stock.is_active == True,
            StockProfile.total_market_cap >= FILTERS["market_cap_min"],
            StockProfile.pe_ttm >= FILTERS["pe_min"],
        )
    ).all()

    stock_info = {}
    stock_ids = []
    for s, p in rows:
        stock_ids.append(s.id)
        stock_info[s.id] = {
            "code": s.code, "name": s.name, "market": s.market,
            "industry": p.industry, "pe_ttm": p.pe_ttm,
            "pb": p.pb, "total_market_cap": p.total_market_cap,
        }

    log.info("  %d stocks pass filter", len(stock_ids))

    # Load ALL klines (no cutoff -- we truncate ourselves)
    log.info("Loading kline data …")
    kline_rows = db.execute(
        select(DailyKline)
        .where(DailyKline.stock_id.in_(stock_ids))
        .order_by(DailyKline.stock_id, DailyKline.date)
    ).scalars().all()

    grouped: dict[int, list] = defaultdict(list)
    for r in kline_rows:
        grouped[r.stock_id].append({
            "date": r.date, "open": r.open, "high": r.high, "low": r.low,
            "close": r.close, "volume": r.volume, "amount": r.amount,
            "change_pct": r.change_pct, "turnover_rate": r.turnover_rate or 0,
        })

    all_klines = {}
    for sid, data in grouped.items():
        if len(data) >= 120:
            all_klines[sid] = pd.DataFrame(data)
    log.info("  %d stocks have ≥120 rows of kline data", len(all_klines))
    return all_klines, stock_info


def truncate_klines(all_klines: dict, cutoff: date) -> dict:
    """Return a *new* dict with DataFrames trimmed to rows ≤ cutoff."""
    out = {}
    for sid, df in all_klines.items():
        sub = df[df["date"] <= cutoff]
        if len(sub) >= 120:
            out[sid] = sub.copy()
    return out


def calc_period_return(all_klines: dict, stock_info: dict,
                       portfolio: list, start: date, end: date):
    """
    Equal-weighted period return from *full* kline data.
    Returns (avg_return_pct, stock_returns_list).
    """
    code_to_sid = {v["code"]: k for k, v in stock_info.items()}
    stock_rets = []
    total_ret, n = 0.0, 0
    for s in portfolio:
        code = s.get("stock_code", "")
        name = s.get("stock_name", "")
        sid = code_to_sid.get(code)
        if sid is None or sid not in all_klines:
            stock_rets.append({"code": code, "name": name,
                               "start_price": None, "end_price": None,
                               "return_pct": None})
            continue
        df = all_klines[sid]
        # closest price on or before start / end
        before_start = df[df["date"] <= start]
        before_end   = df[df["date"] <= end]
        if before_start.empty or before_end.empty:
            stock_rets.append({"code": code, "name": name,
                               "start_price": None, "end_price": None,
                               "return_pct": None})
            continue
        sp = float(before_start.iloc[-1]["close"])
        ep = float(before_end.iloc[-1]["close"])
        if sp <= 0:
            stock_rets.append({"code": code, "name": name,
                               "start_price": sp, "end_price": ep,
                               "return_pct": None})
            continue
        ret = (ep - sp) / sp * 100
        stock_rets.append({"code": code, "name": name,
                           "start_price": round(sp, 2),
                           "end_price": round(ep, 2),
                           "return_pct": round(ret, 2)})
        total_ret += ret
        n += 1
    avg = total_ret / n if n else 0.0
    return round(avg, 4), stock_rets


# ── Main simulation ──────────────────────────────────────────────────

def main():
    t0 = time.time()
    with Session(engine) as db:
        all_klines, stock_info = load_all_data(db)

        # Collect every distinct trading date across all stocks
        all_dates_set = set()
        for df in all_klines.values():
            all_dates_set.update(df["date"].tolist())
        trading_dates = sorted(d for d in all_dates_set if d >= START_DATE)
        log.info("Trading days from %s: %d  (last: %s)",
                 START_DATE, len(trading_dates), trading_dates[-1])

        # Build rebalance schedule: every REBALANCE_FREQ trading days
        rebalance_dates = [trading_dates[i]
                           for i in range(0, len(trading_dates), REBALANCE_FREQ)]
        # The final rebalance creates an "active" iteration that doesn't close.
        # We keep it so the user can trigger the next iteration manually.
        log.info("Rebalance dates (%d): %s … %s",
                 len(rebalance_dates),
                 [str(d) for d in rebalance_dates[:3]],
                 [str(d) for d in rebalance_dates[-3:]])

        # ---- Create parent QuantBacktestResult ----
        run_id = str(uuid.uuid4())
        portfolio_cfg = PortfolioConfig(
            top_n=TOP_N, rebalance_freq=REBALANCE_FREQ,
            buffer_in=max(TOP_N - 3, int(TOP_N * 0.7)),
            buffer_out=min(TOP_N + 5, int(TOP_N * 1.5)),
            industry_neutral=INDUSTRY_NEUTRAL,
        )

        # Run initial analysis to populate backtest fields
        log.info("Running initial cross-sectional analysis (cutoff=%s) …", START_DATE)
        trunc0 = truncate_klines(all_klines, START_DATE)
        log.info("  %d stocks after truncation", len(trunc0))
        bt_cfg = BacktestConfig(
            end_date=str(START_DATE),
        )
        initial_result = run_cross_sectional_analysis(
            trunc0, stock_info, portfolio_cfg, bt_cfg,
        )
        if not initial_result.get("success"):
            log.error("Initial analysis failed: %s", initial_result.get("error"))
            return
        log.info("  Initial portfolio: %d stocks, elapsed %.1fs",
                 len(initial_result["portfolio"]["stocks"]),
                 initial_result.get("elapsed_seconds", 0))

        backtest = initial_result.get("backtest", {})
        record = QuantBacktestResult(
            run_id=run_id,
            name=f"沪深300风格 Top{TOP_N} 自动迭代模拟 (从{START_DATE})",
            notes=(
                f"端到端walk-forward模拟: csi300_like, top_n={TOP_N}, "
                f"rebalance_freq={REBALANCE_FREQ}, industry_neutral={INDUSTRY_NEUTRAL}. "
                f"从 {START_DATE} 每10个交易日重新选股并计算实际收益。"
            ),
            analysis_date=str(START_DATE),
            elapsed_seconds=0,
            filters_applied=FILTERS,
            universe_info=initial_result.get("universe"),
            signal_info=initial_result.get("signal"),
            ml_model_info=initial_result.get("ml_model"),
            factors=initial_result.get("factors"),
            config={
                "top_n": TOP_N, "rebalance_freq": REBALANCE_FREQ,
                "buffer_in": portfolio_cfg.buffer_in,
                "buffer_out": portfolio_cfg.buffer_out,
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
            current_portfolio=initial_result["portfolio"]["stocks"],
            data_splits=initial_result.get("data_splits"),
            auto_iterate=True,
            iterate_status="idle",
            total_iterations=0,
            live_nav=1.0,
            live_return_pct=0.0,
        )
        db.add(record)
        db.flush()
        log.info("Created QuantBacktestResult run_id=%s", run_id)

        # ---- Walk-forward iteration loop ----
        nav = 1.0
        prev_portfolio = None
        prev_start_date = None

        for i, reb_date in enumerate(rebalance_dates):
            iter_t0 = time.time()
            is_last = (i == len(rebalance_dates) - 1)

            # 1) Close previous iteration (compute returns) if exists
            if prev_portfolio is not None and prev_start_date is not None:
                period_ret, stock_rets = calc_period_return(
                    all_klines, stock_info,
                    prev_portfolio, prev_start_date, reb_date,
                )
                nav *= (1 + period_ret / 100)
                cum_ret = (nav - 1) * 100

                # Update the previous iteration record
                prev_iter = db.execute(
                    select(QuantIteration)
                    .where(QuantIteration.run_id == run_id,
                           QuantIteration.status == "active")
                    .order_by(QuantIteration.iteration_num.desc())
                ).scalar_one_or_none()
                if prev_iter:
                    prev_iter.end_date = reb_date
                    prev_iter.period_return_pct = round(period_ret, 4)
                    prev_iter.cumulative_return_pct = round(cum_ret, 4)
                    prev_iter.nav = round(nav, 6)
                    prev_iter.stock_returns = stock_rets
                    prev_iter.status = "completed"
                    prev_iter.completed_at = datetime.utcnow()
                    prev_iter.message = (
                        f"期间收益 {period_ret:+.2f}%, 累计收益 {cum_ret:+.2f}%, "
                        f"NAV {nav:.4f}"
                    )
                    db.flush()

                log.info("  Period %d closed: ret=%+.2f%%, cum=%+.2f%%, NAV=%.4f",
                         i - 1, period_ret, cum_ret, nav)

            # 2) Run analysis with data visible up to reb_date
            log.info("[Iter %d] Rebalance date %s — running analysis …", i, reb_date)
            trunc = truncate_klines(all_klines, reb_date)
            if len(trunc) < 30:
                log.warning("  Only %d stocks after truncation, skipping", len(trunc))
                continue

            bt_cfg_iter = BacktestConfig(end_date=str(reb_date))
            result = run_cross_sectional_analysis(
                trunc, stock_info, portfolio_cfg, bt_cfg_iter,
            )
            if not result.get("success"):
                log.warning("  Analysis failed: %s", result.get("error"))
                continue

            new_portfolio = result["portfolio"]["stocks"]
            simplified = [
                {
                    "stock_code": s.get("stock_code", ""),
                    "stock_name": s.get("stock_name", ""),
                    "industry": s.get("industry", ""),
                    "score": round(s.get("score", 0), 2),
                    "rank": s.get("rank", 0),
                }
                for s in new_portfolio
            ]

            # Compute buys / sells
            prev_codes = set()
            if prev_portfolio:
                prev_codes = {s["stock_code"] for s in prev_portfolio}
            new_codes = {s["stock_code"] for s in simplified}
            buys = sorted(new_codes - prev_codes)
            sells = sorted(prev_codes - new_codes)

            # 3) Create new iteration record
            status = "active" if is_last else "active"  # will be closed next loop
            new_iter = QuantIteration(
                run_id=run_id,
                iteration_num=i,
                start_date=reb_date,
                portfolio=simplified,
                new_buys=buys,
                new_sells=sells,
                nav=round(nav, 6),
                status="active",
                message=(
                    f"第{i}期 | 持仓{len(simplified)}只 | "
                    f"买入{len(buys)} 卖出{len(sells)}"
                ),
            )
            db.add(new_iter)
            db.flush()

            prev_portfolio = simplified
            prev_start_date = reb_date

            elapsed = time.time() - iter_t0
            log.info("  Portfolio: %d stocks | +%d -%d | %.1fs",
                     len(simplified), len(buys), len(sells), elapsed)

        # ---- Close the LAST iteration with returns up to the latest date ----
        latest_date = trading_dates[-1]
        if prev_portfolio and prev_start_date and prev_start_date < latest_date:
            period_ret, stock_rets = calc_period_return(
                all_klines, stock_info,
                prev_portfolio, prev_start_date, latest_date,
            )
            nav *= (1 + period_ret / 100)
            cum_ret = (nav - 1) * 100

            last_iter = db.execute(
                select(QuantIteration)
                .where(QuantIteration.run_id == run_id,
                       QuantIteration.status == "active")
                .order_by(QuantIteration.iteration_num.desc())
            ).scalar_one_or_none()
            if last_iter:
                last_iter.end_date = latest_date
                last_iter.period_return_pct = round(period_ret, 4)
                last_iter.cumulative_return_pct = round(cum_ret, 4)
                last_iter.nav = round(nav, 6)
                last_iter.stock_returns = stock_rets
                last_iter.status = "completed"
                last_iter.completed_at = datetime.utcnow()
                last_iter.message = (
                    f"期间收益 {period_ret:+.2f}%, 累计收益 {cum_ret:+.2f}%, "
                    f"NAV {nav:.4f} (最后一期截止 {latest_date})"
                )
            log.info("  Final period closed: ret=%+.2f%%, cum=%+.2f%%, NAV=%.4f",
                     period_ret, cum_ret, nav)

        # ---- Update parent record ----
        record.current_portfolio = new_portfolio if 'new_portfolio' in dir() else []
        record.total_iterations = len(rebalance_dates)
        record.live_nav = round(nav, 6)
        record.live_return_pct = round((nav - 1) * 100, 4)
        record.iterate_status = "idle"
        record.elapsed_seconds = round(time.time() - t0, 1)

        # Set next_iterate_date to a future date
        cal_days = int(REBALANCE_FREQ * 7 / 5) + 1
        record.next_iterate_date = latest_date + timedelta(days=cal_days)

        db.commit()

        log.info("=" * 60)
        log.info("DONE  run_id=%s", run_id)
        log.info("  Iterations: %d", len(rebalance_dates))
        log.info("  Final NAV:  %.4f", nav)
        log.info("  Return:     %+.2f%%", (nav - 1) * 100)
        log.info("  Elapsed:    %.1fs", time.time() - t0)
        log.info("=" * 60)


if __name__ == "__main__":
    main()
