"""
Quant Auto-Iteration Service

Handles the periodic rebalancing cycle for quantitative strategies:
1. Calculate actual returns of the current portfolio over the rebalance period
2. Re-run cross-sectional analysis with the same filters to get a new portfolio
3. Record iteration details (period return, cumulative return, portfolio changes)
"""

import logging
import uuid
from datetime import date, datetime, timedelta
from collections import defaultdict

import pandas as pd
from sqlalchemy import select, text, create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Stock, DailyKline, StockProfile, QuantBacktestResult, QuantIteration,
)

logger = logging.getLogger(__name__)

# Sync engine for use in Celery tasks
_sync_engine = None


def _get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(settings.DATABASE_URL_SYNC, echo=False)
    return _sync_engine


def _estimate_next_date(from_date: date, rebalance_freq: int) -> date:
    """Estimate the next iteration date (calendar days from trading days)."""
    calendar_days = int(rebalance_freq * 7 / 5) + 1  # trading -> calendar approx
    return from_date + timedelta(days=calendar_days)


def calculate_period_returns(
    db: Session,
    portfolio: list,
    start_date: date,
    end_date: date,
) -> dict:
    """
    Calculate equal-weighted period returns for a portfolio.

    Returns dict with:
      - period_return_pct: float (equal-weighted avg return %)
      - stock_returns: list of {code, name, start_price, end_price, return_pct}
    """
    if not portfolio:
        return {"period_return_pct": 0.0, "stock_returns": []}

    codes = [s.get("stock_code") or s.get("code", "") for s in portfolio]
    if not codes:
        return {"period_return_pct": 0.0, "stock_returns": []}

    # Get stock_id -> code mapping
    rows = db.execute(
        select(Stock.id, Stock.code, Stock.name)
        .where(Stock.code.in_(codes))
    ).all()
    code_to_id = {r[1]: r[0] for r in rows}
    code_to_name = {r[1]: r[2] for r in rows}

    stock_ids = list(code_to_id.values())
    if not stock_ids:
        return {"period_return_pct": 0.0, "stock_returns": []}

    # Get closing prices at start_date and end_date (or nearest available)
    kline_rows = db.execute(
        select(DailyKline.stock_id, DailyKline.date, DailyKline.close)
        .where(
            DailyKline.stock_id.in_(stock_ids),
            DailyKline.date >= start_date - timedelta(days=5),
            DailyKline.date <= end_date + timedelta(days=5),
        )
        .order_by(DailyKline.stock_id, DailyKline.date)
    ).all()

    # Group by stock_id
    klines_by_stock = defaultdict(list)
    for sid, d, close in kline_rows:
        klines_by_stock[sid].append((d, close))

    stock_returns = []
    total_return = 0.0
    valid_count = 0

    id_to_code = {v: k for k, v in code_to_id.items()}

    for code in codes:
        sid = code_to_id.get(code)
        if not sid or sid not in klines_by_stock:
            stock_returns.append({
                "code": code, "name": code_to_name.get(code, ""),
                "start_price": None, "end_price": None, "return_pct": None,
            })
            continue

        klines = klines_by_stock[sid]
        # Find closest price to start_date (on or before)
        start_price = None
        for d, c in klines:
            if d <= start_date:
                start_price = c
            elif start_price is None:
                start_price = c
                break

        # Find closest price to end_date (on or before)
        end_price = None
        for d, c in klines:
            if d <= end_date:
                end_price = c

        if start_price and end_price and start_price > 0:
            ret = (end_price - start_price) / start_price * 100
            stock_returns.append({
                "code": code, "name": code_to_name.get(code, ""),
                "start_price": round(start_price, 2),
                "end_price": round(end_price, 2),
                "return_pct": round(ret, 2),
            })
            total_return += ret
            valid_count += 1
        else:
            stock_returns.append({
                "code": code, "name": code_to_name.get(code, ""),
                "start_price": start_price, "end_price": end_price,
                "return_pct": None,
            })

    avg_return = total_return / valid_count if valid_count > 0 else 0.0
    return {
        "period_return_pct": round(avg_return, 4),
        "stock_returns": stock_returns,
    }


def load_filtered_klines_sync(db: Session, filters: dict, as_of_date: date = None) -> tuple:
    """Sync version of _load_filtered_klines for Celery tasks.

    Args:
        as_of_date: If provided, only load klines up to this date (for historical simulation).
                    Defaults to None (load all available data).
    """
    industries = filters.get("industries")
    exclude_industries = filters.get("exclude_industries")
    market_cap_min = filters.get("market_cap_min")
    market_cap_max = filters.get("market_cap_max")
    pe_min = filters.get("pe_min")
    pe_max = filters.get("pe_max")
    pb_min = filters.get("pb_min")
    pb_max = filters.get("pb_max")
    markets = filters.get("markets")

    need_profile = any([
        industries, exclude_industries,
        market_cap_min is not None, market_cap_max is not None,
        pe_min is not None, pe_max is not None,
        pb_min is not None, pb_max is not None,
    ])

    stock_query = select(Stock).where(Stock.is_active == True)

    if need_profile:
        stock_query = (
            select(Stock, StockProfile)
            .join(StockProfile, Stock.id == StockProfile.stock_id)
            .where(Stock.is_active == True)
        )
        if industries:
            stock_query = stock_query.where(StockProfile.industry.in_(industries))
        if exclude_industries:
            stock_query = stock_query.where(~StockProfile.industry.in_(exclude_industries))
        if market_cap_min is not None:
            stock_query = stock_query.where(StockProfile.total_market_cap >= market_cap_min)
        if market_cap_max is not None:
            stock_query = stock_query.where(StockProfile.total_market_cap <= market_cap_max)
        if pe_min is not None:
            stock_query = stock_query.where(StockProfile.pe_ttm >= pe_min)
        if pe_max is not None:
            stock_query = stock_query.where(StockProfile.pe_ttm <= pe_max)
        if pb_min is not None:
            stock_query = stock_query.where(StockProfile.pb >= pb_min)
        if pb_max is not None:
            stock_query = stock_query.where(StockProfile.pb <= pb_max)

    if markets:
        stock_query = stock_query.where(Stock.market.in_(markets))

    result = db.execute(stock_query)

    stock_info = {}
    stock_ids = []

    if need_profile:
        rows = result.all()
        for row in rows:
            s, p = row[0], row[1]
            stock_ids.append(s.id)
            stock_info[s.id] = {
                "code": s.code, "name": s.name, "market": s.market,
                "industry": p.industry if p else None,
                "pe_ttm": p.pe_ttm if p else None,
                "pb": p.pb if p else None,
                "total_market_cap": p.total_market_cap if p else None,
            }
    else:
        stocks = result.scalars().all()
        stock_ids = [s.id for s in stocks]
        for s in stocks:
            stock_info[s.id] = {"code": s.code, "name": s.name, "market": s.market}
        if stock_ids:
            prof_result = db.execute(
                select(StockProfile).where(StockProfile.stock_id.in_(stock_ids))
            )
            for p in prof_result.scalars().all():
                if p.stock_id in stock_info:
                    stock_info[p.stock_id].update({
                        "industry": p.industry, "pe_ttm": p.pe_ttm,
                        "pb": p.pb, "total_market_cap": p.total_market_cap,
                    })

    if not stock_ids:
        return {}, {}

    ref_date = as_of_date or date.today()
    cutoff_date = ref_date - timedelta(days=750)  # ~500 trading days
    kline_query = (
        select(DailyKline)
        .where(DailyKline.stock_id.in_(stock_ids), DailyKline.date >= cutoff_date)
    )
    if as_of_date:
        kline_query = kline_query.where(DailyKline.date <= as_of_date)
    kline_rows = db.execute(
        kline_query.order_by(DailyKline.stock_id, DailyKline.date)
    ).scalars().all()

    grouped = defaultdict(list)
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

    return all_klines, stock_info


def run_single_iteration(run_id: str) -> dict:
    """
    Execute one iteration cycle for a given run_id.

    Steps:
    1. Load the QuantBacktestResult record
    2. Find the active iteration -> calculate its period return -> mark completed
    3. Re-run analysis with same filters -> get new portfolio
    4. Create new iteration with the new portfolio
    5. Update parent record (nav, cumulative return, next date)

    Returns dict with status info.
    """
    from app.services.quant_engine import run_cross_sectional_analysis, PortfolioConfig, BacktestConfig

    engine = _get_sync_engine()
    with Session(engine) as db:
        # 1. Load parent record
        record = db.execute(
            select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
        ).scalar_one_or_none()
        if not record:
            return {"ok": False, "error": f"Run {run_id} not found"}
        if not record.auto_iterate:
            return {"ok": False, "error": "Auto-iterate is not enabled"}

        record.iterate_status = "running"
        db.commit()

        try:
            config = record.config or {}
            rebalance_freq = config.get("rebalance_freq", 10)
            top_n = config.get("top_n", 30)
            industry_neutral = config.get("industry_neutral", False)

            # 2. Find the latest active iteration
            active_iter = db.execute(
                select(QuantIteration)
                .where(QuantIteration.run_id == run_id, QuantIteration.status == "active")
                .order_by(QuantIteration.iteration_num.desc())
            ).scalar_one_or_none()

            today = date.today()
            prev_nav = record.live_nav or 1.0

            if active_iter:
                # Calculate period returns
                end_date = today
                ret_info = calculate_period_returns(
                    db, active_iter.portfolio or [], active_iter.start_date, end_date,
                )
                period_ret = ret_info["period_return_pct"]
                new_nav = prev_nav * (1 + period_ret / 100)
                cumulative_ret = (new_nav - 1.0) * 100

                active_iter.end_date = end_date
                active_iter.period_return_pct = round(period_ret, 4)
                active_iter.cumulative_return_pct = round(cumulative_ret, 4)
                active_iter.nav = round(new_nav, 6)
                active_iter.stock_returns = ret_info["stock_returns"]
                active_iter.status = "completed"
                active_iter.completed_at = datetime.utcnow()
                active_iter.message = (
                    f"期间收益 {period_ret:+.2f}%, 累计收益 {cumulative_ret:+.2f}%, "
                    f"NAV {new_nav:.4f}"
                )
                prev_nav = new_nav
                next_num = active_iter.iteration_num + 1
                db.flush()
            else:
                next_num = 1
                new_nav = prev_nav

            # 3. Re-run analysis with same filters
            filters = record.filters_applied or {}
            logger.info(f"[Iteration] run_id={run_id} iter#{next_num}: loading data with filters={filters}")

            all_klines, stock_info = load_filtered_klines_sync(db, filters)
            if len(all_klines) < 30:
                raise ValueError(f"Not enough stocks ({len(all_klines)}) for analysis, need >= 30")

            portfolio_config = PortfolioConfig(
                top_n=top_n,
                rebalance_freq=rebalance_freq,
                industry_neutral=industry_neutral,
                buffer_in=max(top_n - 5, int(top_n * 0.8)),
                buffer_out=min(top_n + 10, int(top_n * 1.3)),
            )
            bt_config = BacktestConfig()  # No backtest needed for iteration

            result = run_cross_sectional_analysis(
                all_klines, stock_info, portfolio_config, bt_config,
            )

            if not result.get("success"):
                raise ValueError(result.get("error", "Analysis failed"))

            new_portfolio = result.get("portfolio", {}).get("stocks", [])
            # Simplify portfolio for storage
            portfolio_simplified = [
                {
                    "stock_code": s.get("stock_code", ""),
                    "stock_name": s.get("stock_name", ""),
                    "industry": s.get("industry", ""),
                    "score": s.get("score", 0),
                    "rank": s.get("rank", 0),
                }
                for s in new_portfolio
            ]

            # Compute buy/sell changes vs previous portfolio
            prev_codes = set()
            if active_iter and active_iter.portfolio:
                prev_codes = {s.get("stock_code", "") for s in active_iter.portfolio}
            new_codes = {s.get("stock_code", "") for s in portfolio_simplified}
            buys = sorted(new_codes - prev_codes)
            sells = sorted(prev_codes - new_codes)

            # 4. Create new iteration
            new_iter = QuantIteration(
                run_id=run_id,
                iteration_num=next_num,
                start_date=today,
                portfolio=portfolio_simplified,
                new_buys=buys,
                new_sells=sells,
                nav=round(prev_nav, 6),
                status="active",
                message=f"第{next_num}期 | 持仓{len(portfolio_simplified)}只 | 买入{len(buys)} 卖出{len(sells)}",
            )
            db.add(new_iter)

            # 5. Update parent record
            record.current_portfolio = new_portfolio
            record.iterate_status = "idle"
            record.total_iterations = next_num
            record.live_nav = round(prev_nav, 6)
            record.live_return_pct = round((prev_nav - 1.0) * 100, 4)
            record.next_iterate_date = _estimate_next_date(today, rebalance_freq)

            db.commit()
            logger.info(
                f"[Iteration] run_id={run_id} iter#{next_num} completed: "
                f"portfolio={len(portfolio_simplified)}, buys={len(buys)}, sells={len(sells)}"
            )
            return {
                "ok": True,
                "iteration_num": next_num,
                "portfolio_size": len(portfolio_simplified),
                "buys": len(buys),
                "sells": len(sells),
                "nav": round(prev_nav, 6),
                "cumulative_return_pct": round((prev_nav - 1.0) * 100, 4),
            }

        except Exception as e:
            logger.error(f"[Iteration] run_id={run_id} error: {e}", exc_info=True)
            db.rollback()
            # Update status to error
            record = db.execute(
                select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
            ).scalar_one_or_none()
            if record:
                record.iterate_status = "error"
                db.commit()
            return {"ok": False, "error": str(e)}


# ─── Historical Iteration (batch simulation from backtest_start to today) ───


def _get_trading_dates(db: Session, start: date, end: date) -> list:
    """Get actual trading dates from DailyKline table."""
    rows = db.execute(
        text(
            "SELECT DISTINCT date FROM daily_klines "
            "WHERE date >= :s AND date <= :e ORDER BY date"
        ),
        {"s": start, "e": end},
    ).all()
    return [r[0] for r in rows]


def run_historical_iterations(run_id: str, progress_cb=None) -> dict:
    """
    Create historical iteration records from backtest_start to today.

    For each rebalance period:
    1. Re-run factor analysis with data up to that date (as_of_date)
    2. Calculate actual period returns from DB kline data
    3. Create QuantIteration records

    This is CPU-intensive (~90s per period). Should be called from async task.

    Args:
        progress_cb: optional callback(message_str) for status updates
    """
    from app.services.quant_engine import (
        run_cross_sectional_analysis, PortfolioConfig, BacktestConfig,
    )

    engine = _get_sync_engine()
    with Session(engine) as db:
        record = db.execute(
            select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
        ).scalar_one_or_none()
        if not record:
            return {"ok": False, "error": f"Run {run_id} not found"}

        record.iterate_status = "running"
        db.commit()

        try:
            config = record.config or {}
            filters = record.filters_applied or {}
            rebalance_freq = config.get("rebalance_freq", 10)
            top_n = config.get("top_n", 30)
            industry_neutral = config.get("industry_neutral", False)

            backtest_start_str = filters.get("backtest_start")
            if not backtest_start_str:
                return {"ok": False, "error": "No backtest_start in filters_applied"}

            sim_start = date.fromisoformat(backtest_start_str)
            today = date.today()

            # Get actual trading dates to build rebalance schedule
            trading_dates = _get_trading_dates(db, sim_start, today)
            if len(trading_dates) < rebalance_freq:
                return {"ok": False, "error": f"Not enough trading days ({len(trading_dates)})"}

            # Build rebalance dates: every rebalance_freq trading days
            rebalance_dates = [trading_dates[i] for i in range(0, len(trading_dates), rebalance_freq)]
            # Always include the last trading day if not already there
            if rebalance_dates[-1] != trading_dates[-1]:
                rebalance_dates.append(trading_dates[-1])

            logger.info(
                f"[HistIter] run_id={run_id}: {len(rebalance_dates)} rebalance dates "
                f"from {rebalance_dates[0]} to {rebalance_dates[-1]}"
            )

            # Delete any existing iterations for clean re-creation
            db.execute(
                text("DELETE FROM quant_iterations WHERE run_id = :rid"),
                {"rid": run_id},
            )
            db.flush()

            portfolio_config = PortfolioConfig(
                top_n=top_n,
                rebalance_freq=rebalance_freq,
                industry_neutral=industry_neutral,
                buffer_in=max(top_n - 5, int(top_n * 0.8)),
                buffer_out=min(top_n + 10, int(top_n * 1.3)),
            )
            bt_config = BacktestConfig()  # No backtest needed

            nav = 1.0
            prev_portfolio = None
            total_periods = len(rebalance_dates) - 1

            for i in range(total_periods):
                period_start = rebalance_dates[i]
                period_end = rebalance_dates[i + 1]
                iter_num = i

                if progress_cb:
                    progress_cb(
                        f"历史回测迭代 {i+1}/{total_periods}: "
                        f"{period_start} ~ {period_end} ..."
                    )

                # Run analysis with data up to period_start
                all_klines, stock_info = load_filtered_klines_sync(
                    db, filters, as_of_date=period_start,
                )
                if len(all_klines) < 30:
                    logger.warning(
                        f"[HistIter] iter#{iter_num} only {len(all_klines)} stocks, skipping"
                    )
                    continue

                result = run_cross_sectional_analysis(
                    all_klines, stock_info, portfolio_config, bt_config,
                )
                if not result.get("success"):
                    logger.warning(f"[HistIter] iter#{iter_num} analysis failed, skipping")
                    continue

                new_portfolio = [
                    {
                        "stock_code": s.get("stock_code", ""),
                        "stock_name": s.get("stock_name", ""),
                        "industry": s.get("industry", ""),
                        "score": s.get("score", 0),
                        "rank": s.get("rank", 0),
                    }
                    for s in result.get("portfolio", {}).get("stocks", [])
                ]

                # Calculate buys/sells vs previous
                prev_codes = set()
                if prev_portfolio:
                    prev_codes = {s["stock_code"] for s in prev_portfolio}
                new_codes = {s["stock_code"] for s in new_portfolio}
                buys = sorted(new_codes - prev_codes)
                sells = sorted(prev_codes - new_codes)

                # Calculate actual returns for this period
                ret_info = calculate_period_returns(
                    db, new_portfolio, period_start, period_end,
                )
                period_ret = ret_info["period_return_pct"]
                nav *= (1 + period_ret / 100)
                cumul_ret = (nav - 1.0) * 100

                iteration = QuantIteration(
                    run_id=run_id,
                    iteration_num=iter_num,
                    start_date=period_start,
                    end_date=period_end,
                    portfolio=new_portfolio,
                    new_buys=buys,
                    new_sells=sells,
                    period_return_pct=round(period_ret, 4),
                    cumulative_return_pct=round(cumul_ret, 4),
                    nav=round(nav, 6),
                    stock_returns=ret_info["stock_returns"],
                    status="completed",
                    completed_at=datetime.utcnow(),
                    message=(
                        f"历史回测 第{iter_num}期 | "
                        f"{period_start}~{period_end} | "
                        f"收益 {period_ret:+.2f}% | NAV {nav:.4f}"
                    ),
                )
                db.add(iteration)
                prev_portfolio = new_portfolio

                logger.info(
                    f"[HistIter] run_id={run_id} iter#{iter_num}: "
                    f"{period_start}~{period_end} ret={period_ret:+.2f}% nav={nav:.4f}"
                )

            # Create final active iteration (from last rebalance to now, watching)
            final_num = total_periods
            if prev_portfolio:
                # Run latest analysis for the active iteration
                if progress_cb:
                    progress_cb(f"生成当前持仓 (第{final_num}期)...")
                all_klines, stock_info = load_filtered_klines_sync(db, filters)
                if len(all_klines) >= 30:
                    result = run_cross_sectional_analysis(
                        all_klines, stock_info, portfolio_config, bt_config,
                    )
                    if result.get("success"):
                        latest_portfolio = [
                            {
                                "stock_code": s.get("stock_code", ""),
                                "stock_name": s.get("stock_name", ""),
                                "industry": s.get("industry", ""),
                                "score": s.get("score", 0),
                                "rank": s.get("rank", 0),
                            }
                            for s in result.get("portfolio", {}).get("stocks", [])
                        ]
                        prev_codes = {s["stock_code"] for s in prev_portfolio}
                        new_codes = {s["stock_code"] for s in latest_portfolio}
                        buys = sorted(new_codes - prev_codes)
                        sells = sorted(prev_codes - new_codes)
                        prev_portfolio = latest_portfolio
                    else:
                        buys, sells = [], []
                        latest_portfolio = prev_portfolio
                else:
                    buys, sells = [], []
                    latest_portfolio = prev_portfolio

                active_iter = QuantIteration(
                    run_id=run_id,
                    iteration_num=final_num,
                    start_date=rebalance_dates[-1],
                    portfolio=latest_portfolio,
                    new_buys=buys,
                    new_sells=sells,
                    nav=round(nav, 6),
                    status="active",
                    message=f"当前持仓 | 第{final_num}期 | {len(latest_portfolio)}只",
                )
                db.add(active_iter)
                record.current_portfolio = latest_portfolio

            # Update parent record
            record.iterate_status = "idle"
            record.total_iterations = final_num
            record.live_nav = round(nav, 6)
            record.live_return_pct = round((nav - 1.0) * 100, 4)
            record.next_iterate_date = _estimate_next_date(today, rebalance_freq)
            record.auto_iterate = True

            db.commit()
            logger.info(
                f"[HistIter] run_id={run_id} completed: "
                f"{final_num} periods, NAV={nav:.4f}, return={(nav-1)*100:.2f}%"
            )
            return {
                "ok": True,
                "total_periods": final_num,
                "nav": round(nav, 6),
                "return_pct": round((nav - 1.0) * 100, 4),
            }

        except Exception as e:
            logger.error(f"[HistIter] run_id={run_id} error: {e}", exc_info=True)
            db.rollback()
            record = db.execute(
                select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
            ).scalar_one_or_none()
            if record:
                record.iterate_status = "error"
                db.commit()
            return {"ok": False, "error": str(e)}
