"""
API routes for cross-sectional quantitative factor analysis.

Endpoints:
  POST /api/quant/analyze              — Start async analysis (returns task_id)
  GET  /api/quant/status/{task_id}     — Poll task progress
  GET  /api/quant/history              — List saved analysis results
  GET  /api/quant/result/{run_id}      — Get a specific saved result
  PUT  /api/quant/result/{run_id}      — Update name/notes of a saved result
  DELETE /api/quant/result/{run_id}    — Delete a saved result
  GET  /api/quant/factors              — List available factor definitions
  GET  /api/quant/factor-status        — Check factor computation status
  GET  /api/quant/stock-profile/{id}   — Single stock factor profile
  GET  /api/quant/industries           — List all available industries
  GET  /api/quant/presets              — List preset universe strategies
"""

import logging
import asyncio
import uuid
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, desc
import pandas as pd

from app.database import get_db
from app.models import Stock, DailyKline, StockProfile, QuantBacktestResult, QuantIteration

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── In-memory task management (same pattern as AI analysis) ─────────

_quant_tasks: Dict[str, Dict[str, Any]] = {}


# ─── Preset Strategies ───────────────────────────────────────────────

PRESET_STRATEGIES = {
    "all": {
        "label": "全部A股",
        "description": "沪深全部股票(排除ST/退市/北交所)",
        "filters": {},
    },
    "csi300_like": {
        "label": "沪深300风格",
        "description": "大盘蓝筹 (市值>500亿, PE>0)",
        "filters": {
            "market_cap_min": 500,
            "pe_min": 0.01,
        },
    },
    "csi500_like": {
        "label": "中证500风格",
        "description": "中盘成长 (100-800亿, PE>0, PB>0)",
        "filters": {
            "market_cap_min": 100,
            "market_cap_max": 800,
            "pe_min": 0.01,
            "pb_min": 0.01,
        },
    },
    "small_cap": {
        "label": "小盘股",
        "description": "小盘股 (市值<100亿)",
        "filters": {
            "market_cap_max": 100,
        },
    },
    "value": {
        "label": "价值股",
        "description": "低PE低PB (PE<20, PB<2, 市值>50亿)",
        "filters": {
            "pe_max": 20,
            "pb_max": 2,
            "market_cap_min": 50,
        },
    },
    "growth": {
        "label": "成长股",
        "description": "高估值成长 (PE 15-80, 市值>100亿)",
        "filters": {
            "pe_min": 15,
            "pe_max": 80,
            "market_cap_min": 100,
        },
    },
    "high_dividend": {
        "label": "高股息",
        "description": "高股息蓝筹 (PE<15, PB<3, 市值>200亿)",
        "filters": {
            "pe_min": 0.01,
            "pe_max": 15,
            "pb_max": 3,
            "market_cap_min": 200,
        },
    },
    "baijiu": {
        "label": "白酒板块",
        "description": "酿酒行业相关",
        "filters": {
            "industries": ["酿酒行业"],
        },
    },
    "bank": {
        "label": "银行金融",
        "description": "银行/金融/券商/保险 (PB<1.5)",
        "filters": {
            "industries": ["银行", "金融行业", "非银金融", "多元金融", "券商信托", "金融"],
            "pb_max": 1.5,
        },
    },
    "tech_hardware": {
        "label": "科技硬件",
        "description": "半导体/电子/通信设备/计算机设备",
        "filters": {
            "industries": [
                "半导体", "电子元件", "通信设备", "光学光电子", "消费电子",
                "电子信息", "电子器件", "电子设备", "计算机设备", "电子化学品",
                "安防设备",
            ],
        },
    },
    "new_energy": {
        "label": "新能源",
        "description": "电力设备/电池/光伏/风电/新能源汽车",
        "filters": {
            "industries": [
                "电力设备", "电网设备", "输配电气", "电源设备", "电气设备",
                "电机", "汽车零部件", "电池", "光伏设备", "风电设备",
                "能源金属",
            ],
        },
    },
    "pharma": {
        "label": "医药生物",
        "description": "医药制造/中药/生物制品/医疗器械/医疗服务",
        "filters": {
            "industries": [
                "中药", "化学制药", "生物制品", "医疗器械", "医疗服务",
                "医药商业", "医药制造", "生物制药", "医药生物", "医疗行业",
            ],
        },
    },
}


# ─── Data Loading ────────────────────────────────────────────────────

async def _load_filtered_klines(
    db: AsyncSession,
    min_days: int = 120,
    max_days: int = 500,
    industries: Optional[List[str]] = None,
    exclude_industries: Optional[List[str]] = None,
    market_cap_min: Optional[float] = None,
    market_cap_max: Optional[float] = None,
    pe_min: Optional[float] = None,
    pe_max: Optional[float] = None,
    pb_min: Optional[float] = None,
    pb_max: Optional[float] = None,
    markets: Optional[List[str]] = None,
    backtest_start: Optional[str] = None,
) -> tuple:
    """Load kline data for stocks matching filter criteria."""
    stock_query = select(Stock).where(Stock.is_active == True)

    need_profile_join = any([
        industries, exclude_industries,
        market_cap_min is not None, market_cap_max is not None,
        pe_min is not None, pe_max is not None,
        pb_min is not None, pb_max is not None,
    ])

    if need_profile_join:
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

    result = await db.execute(stock_query)

    stock_info = {}
    stock_ids = []

    if need_profile_join:
        rows = result.all()
        for row in rows:
            s = row[0]
            p = row[1]
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
            prof_result = await db.execute(
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

    filter_desc = []
    if industries:
        filter_desc.append(f"行业={industries}")
    if market_cap_min is not None:
        filter_desc.append(f"市值>={market_cap_min}亿")
    if market_cap_max is not None:
        filter_desc.append(f"市值<={market_cap_max}亿")
    if pe_min is not None:
        filter_desc.append(f"PE>={pe_min}")
    if pe_max is not None:
        filter_desc.append(f"PE<={pe_max}")
    if pb_min is not None:
        filter_desc.append(f"PB>={pb_min}")
    if pb_max is not None:
        filter_desc.append(f"PB<={pb_max}")
    if markets:
        filter_desc.append(f"市场={markets}")
    logger.info(f"Pre-filtered to {len(stock_ids)} stocks | filters: {', '.join(filter_desc) or 'none'}")

    # Determine how far back to load kline data.
    # If a backtest_start is specified, we need data from that date
    # (plus 120-day warmup for factor computation). Otherwise use
    # the default max_days window.
    if backtest_start:
        try:
            bs = date.fromisoformat(backtest_start)
            cutoff_date = bs - timedelta(days=180)  # 180 calendar-day warmup
        except ValueError:
            cutoff_date = date.today() - timedelta(days=int(max_days * 1.5))
    else:
        cutoff_date = date.today() - timedelta(days=int(max_days * 1.5))
    all_klines = {}

    query = (
        select(DailyKline)
        .where(DailyKline.stock_id.in_(stock_ids), DailyKline.date >= cutoff_date)
        .order_by(DailyKline.stock_id, DailyKline.date)
    )
    result = await db.execute(query)
    rows = result.scalars().all()

    grouped = defaultdict(list)
    for r in rows:
        grouped[r.stock_id].append({
            "date": r.date, "open": r.open, "high": r.high, "low": r.low,
            "close": r.close, "volume": r.volume, "amount": r.amount,
            "change_pct": r.change_pct, "turnover_rate": r.turnover_rate or 0,
        })

    for sid, data in grouped.items():
        if len(data) >= min_days:
            all_klines[sid] = pd.DataFrame(data)

    return all_klines, stock_info


# ─── Async Task Runner ───────────────────────────────────────────────

def _run_analysis_sync(
    all_klines, stock_info, portfolio_config, backtest_config
):
    """Synchronous wrapper for the CPU-bound analysis (runs in thread pool)."""
    from app.services.quant_engine import run_cross_sectional_analysis
    return run_cross_sectional_analysis(all_klines, stock_info, portfolio_config, backtest_config)


async def _run_and_save(
    task_id: str,
    all_klines: dict,
    stock_info: dict,
    portfolio_config,
    backtest_config,
    filters_applied: dict,
):
    """Run analysis in background, save result to DB when done."""
    from app.services.quant_engine import PortfolioConfig, BacktestConfig
    from app.database import async_session

    _quant_tasks[task_id]["status"] = "running"
    _quant_tasks[task_id]["message"] = f"正在分析 {len(all_klines)} 只股票..."

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, _run_analysis_sync,
            all_klines, stock_info, portfolio_config, backtest_config,
        )

        if not result.get("success"):
            _quant_tasks[task_id]["status"] = "error"
            _quant_tasks[task_id]["message"] = result.get("error", "分析失败")
            return

        result["filters_applied"] = filters_applied

        # Generate auto-name from filters
        auto_name = _generate_auto_name(filters_applied, portfolio_config)

        # Generate run_id
        run_id = str(uuid.uuid4())
        result["run_id"] = run_id

        # Save to DB
        backtest = result.get("backtest", {})
        async with async_session() as db:
            record = QuantBacktestResult(
                run_id=run_id,
                name=auto_name,
                analysis_date=result.get("analysis_date"),
                elapsed_seconds=result.get("elapsed_seconds"),
                filters_applied=filters_applied,
                universe_info=result.get("universe"),
                signal_info=result.get("signal"),
                ml_model_info=result.get("ml_model"),
                factors=result.get("factors"),
                config=result.get("portfolio", {}).get("config"),
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
                monthly_returns=backtest.get("period_returns"),
                current_portfolio=result.get("portfolio", {}).get("stocks"),
                data_splits=result.get("data_splits"),
            )
            db.add(record)
            await db.commit()
            logger.info(f"Saved analysis result run_id={run_id}")

        _quant_tasks[task_id]["status"] = "completed"
        _quant_tasks[task_id]["result"] = result
        _quant_tasks[task_id]["run_id"] = run_id
        _quant_tasks[task_id]["message"] = f"分析完成，耗时 {result.get('elapsed_seconds', 0):.0f}s"

    except Exception as e:
        logger.exception(f"Quant analysis task {task_id} failed")
        _quant_tasks[task_id]["status"] = "error"
        _quant_tasks[task_id]["message"] = str(e)


def _generate_auto_name(filters_applied: dict, portfolio_config) -> str:
    """Generate a human-readable name from analysis parameters."""
    parts = []

    preset = filters_applied.get("preset")
    if preset and preset in PRESET_STRATEGIES:
        parts.append(PRESET_STRATEGIES[preset]["label"])

    # Always include effective filter info (may differ from preset defaults)
    industries = filters_applied.get("industries")
    if industries and not preset:
        parts.append(",".join(industries[:2]) + ("..." if len(industries) > 2 else ""))
    mcap_min = filters_applied.get("market_cap_min")
    mcap_max = filters_applied.get("market_cap_max")
    if mcap_min is not None or mcap_max is not None:
        mc_parts = []
        if mcap_min is not None:
            mc_parts.append(f"≥{mcap_min}")
        if mcap_max is not None:
            mc_parts.append(f"≤{mcap_max}")
        parts.append(f"市值{''.join(mc_parts)}亿")
    pe_min = filters_applied.get("pe_min")
    pe_max = filters_applied.get("pe_max")
    if pe_max is not None and (pe_min is None or pe_min <= 0.01):
        parts.append(f"PE≤{pe_max}")
    elif pe_min is not None and pe_min > 0.01:
        if pe_max is not None:
            parts.append(f"PE {pe_min}-{pe_max}")
        else:
            parts.append(f"PE≥{pe_min}")

    if not parts:
        parts.append("全部A股")

    parts.append(f"Top{portfolio_config.top_n}")
    if portfolio_config.industry_neutral:
        parts.append("行业中性")

    return " ".join(parts)


# ─── API Endpoints ───────────────────────────────────────────────────

@router.post(
    "/analyze",
    summary="Start async quantitative analysis",
)
async def start_analysis(
    top_n: int = Query(30, ge=10, le=100),
    rebalance_freq: int = Query(10, ge=5, le=30),
    industry_neutral: bool = Query(False),
    preset: Optional[str] = Query(None),
    industries: Optional[str] = Query(None),
    exclude_industries: Optional[str] = Query(None),
    market_cap_min: Optional[float] = Query(None, ge=0),
    market_cap_max: Optional[float] = Query(None, ge=0),
    pe_min: Optional[float] = Query(None),
    pe_max: Optional[float] = Query(None),
    pb_min: Optional[float] = Query(None),
    pb_max: Optional[float] = Query(None),
    markets: Optional[str] = Query(None),
    backtest_start: Optional[str] = Query(None, description="Backtest start date (YYYY-MM-DD)"),
    backtest_end: Optional[str] = Query(None, description="Backtest end date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Start an async cross-sectional quant analysis. Returns a task_id immediately.
    Poll GET /status/{task_id} for progress. Results are auto-saved to DB.
    """
    from app.services.quant_engine import PortfolioConfig, BacktestConfig

    # Resolve filters
    filter_industries = None
    filter_exclude_industries = None
    filter_mcap_min = market_cap_min
    filter_mcap_max = market_cap_max
    filter_pe_min = pe_min
    filter_pe_max = pe_max
    filter_pb_min = pb_min
    filter_pb_max = pb_max
    filter_markets_list = None

    if preset and preset in PRESET_STRATEGIES:
        pf = PRESET_STRATEGIES[preset]["filters"]
        filter_industries = pf.get("industries")
        if "market_cap_min" in pf and filter_mcap_min is None:
            filter_mcap_min = pf["market_cap_min"]
        if "market_cap_max" in pf and filter_mcap_max is None:
            filter_mcap_max = pf["market_cap_max"]
        if "pe_min" in pf and filter_pe_min is None:
            filter_pe_min = pf["pe_min"]
        if "pe_max" in pf and filter_pe_max is None:
            filter_pe_max = pf["pe_max"]
        if "pb_min" in pf and filter_pb_min is None:
            filter_pb_min = pf["pb_min"]
        if "pb_max" in pf and filter_pb_max is None:
            filter_pb_max = pf["pb_max"]
    elif preset and preset not in PRESET_STRATEGIES:
        raise HTTPException(status_code=400, detail=f"未知的预设策略: {preset}")

    if industries:
        parsed = [i.strip() for i in industries.split(",") if i.strip()]
        if parsed:
            filter_industries = (filter_industries or []) + parsed
    if exclude_industries:
        parsed = [i.strip() for i in exclude_industries.split(",") if i.strip()]
        if parsed:
            filter_exclude_industries = (filter_exclude_industries or []) + parsed
    if markets:
        filter_markets_list = [m.strip() for m in markets.split(",") if m.strip()]

    # Load data (async, fast)
    all_klines, stock_info = await _load_filtered_klines(
        db,
        industries=filter_industries,
        exclude_industries=filter_exclude_industries,
        market_cap_min=filter_mcap_min, market_cap_max=filter_mcap_max,
        pe_min=filter_pe_min, pe_max=filter_pe_max,
        pb_min=filter_pb_min, pb_max=filter_pb_max,
        markets=filter_markets_list,
        backtest_start=backtest_start,
    )

    if not all_klines:
        raise HTTPException(status_code=400, detail="没有符合筛选条件的K线数据，请调整筛选条件")
    if len(all_klines) < 30:
        raise HTTPException(
            status_code=400,
            detail=f"符合条件的股票数量不足 ({len(all_klines)}), 需要至少30只。请放宽筛选条件。",
        )

    portfolio_config = PortfolioConfig(
        top_n=top_n,
        rebalance_freq=rebalance_freq,
        industry_neutral=industry_neutral,
        buffer_in=max(top_n - 5, int(top_n * 0.8)),
        buffer_out=min(top_n + 10, int(top_n * 1.3)),
    )

    filters_applied = {
        "preset": preset,
        "industries": filter_industries,
        "exclude_industries": filter_exclude_industries,
        "market_cap_min": filter_mcap_min,
        "market_cap_max": filter_mcap_max,
        "pe_min": filter_pe_min,
        "pe_max": filter_pe_max,
        "pb_min": filter_pb_min,
        "pb_max": filter_pb_max,
        "markets": filter_markets_list,
        "backtest_start": backtest_start,
        "backtest_end": backtest_end,
    }

    bt_config = BacktestConfig(
        start_date=backtest_start,
        end_date=backtest_end,
    )

    # Build human-readable filter description for task message
    filter_parts = []
    if preset and preset in PRESET_STRATEGIES:
        filter_parts.append(PRESET_STRATEGIES[preset]["label"])
    if filter_mcap_min is not None:
        filter_parts.append(f"市值≥{filter_mcap_min}亿")
    if filter_mcap_max is not None:
        filter_parts.append(f"市值≤{filter_mcap_max}亿")
    if filter_pe_min is not None:
        filter_parts.append(f"PE≥{filter_pe_min}")
    if filter_pe_max is not None:
        filter_parts.append(f"PE≤{filter_pe_max}")
    if filter_pb_min is not None:
        filter_parts.append(f"PB≥{filter_pb_min}")
    if filter_pb_max is not None:
        filter_parts.append(f"PB≤{filter_pb_max}")
    if filter_industries:
        filter_parts.append(f"行业: {','.join(filter_industries[:3])}{'...' if len(filter_industries) > 3 else ''}")
    if filter_exclude_industries:
        filter_parts.append(f"排除行业: {','.join(filter_exclude_industries[:3])}{'...' if len(filter_exclude_industries) > 3 else ''}")
    filter_summary = " | ".join(filter_parts) if filter_parts else "全部A股"

    # Create task
    task_id = str(uuid.uuid4())
    _quant_tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "message": f"筛选条件: {filter_summary} → 共 {len(all_klines)} 只股票",
        "created_at": datetime.utcnow().isoformat(),
        "stock_count": len(all_klines),
        "result": None,
        "run_id": None,
    }

    # Launch background task
    asyncio.create_task(_run_and_save(
        task_id, all_klines, stock_info,
        portfolio_config, bt_config,
        filters_applied,
    ))

    return {
        "task_id": task_id,
        "status": "pending",
        "stock_count": len(all_klines),
        "message": f"分析任务已提交，共 {len(all_klines)} 只股票",
    }


@router.get(
    "/status/{task_id}",
    summary="Poll analysis task status",
)
async def get_task_status(task_id: str):
    """Check the status of a running analysis task."""
    task = _quant_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    resp = {
        "task_id": task_id,
        "status": task["status"],
        "message": task.get("message", ""),
        "stock_count": task.get("stock_count", 0),
    }

    if task["status"] == "completed":
        resp["result"] = task.get("result")
        resp["run_id"] = task.get("run_id")
        # Clean up old tasks (keep only last 20)
        if len(_quant_tasks) > 20:
            old_ids = sorted(_quant_tasks.keys(), key=lambda k: _quant_tasks[k].get("created_at", ""))
            for oid in old_ids[:len(_quant_tasks) - 20]:
                _quant_tasks.pop(oid, None)

    return resp


# ─── History CRUD ────────────────────────────────────────────────────

@router.get(
    "/history",
    summary="List saved analysis results",
)
async def list_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated list of saved analysis results (newest first)."""
    total_q = await db.execute(select(func.count(QuantBacktestResult.id)))
    total = total_q.scalar() or 0

    result = await db.execute(
        select(QuantBacktestResult)
        .order_by(desc(QuantBacktestResult.created_at))
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()

    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "run_id": r.run_id,
            "name": r.name,
            "notes": r.notes,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "analysis_date": r.analysis_date,
            "elapsed_seconds": r.elapsed_seconds,
            "filters_applied": r.filters_applied,
            "universe_info": r.universe_info,
            "config": r.config,
            "total_return": r.total_return,
            "annual_return": r.annual_return,
            "sharpe_ratio": r.sharpe_ratio,
            "max_drawdown": r.max_drawdown,
            "win_rate": r.win_rate,
            "portfolio_size": len(r.current_portfolio) if r.current_portfolio else 0,
            "auto_iterate": r.auto_iterate or False,
            "iterate_status": r.iterate_status,
            "next_iterate_date": str(r.next_iterate_date) if r.next_iterate_date else None,
            "total_iterations": r.total_iterations or 0,
            "live_nav": r.live_nav,
            "live_return_pct": r.live_return_pct,
        })

    return {"total": total, "items": items}


@router.get(
    "/result/{run_id}",
    summary="Get a specific saved analysis result",
)
async def get_result(run_id: str, db: AsyncSession = Depends(get_db)):
    """Return the full saved analysis result including portfolio, backtest, factors."""
    result = await db.execute(
        select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
    )
    r = result.scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="分析结果不存在")

    backtest = {
        "success": True,
        "total_return": r.total_return,
        "annual_return": r.annual_return,
        "annual_volatility": r.annual_volatility,
        "sharpe_ratio": r.sharpe_ratio,
        "max_drawdown": r.max_drawdown,
        "calmar_ratio": r.calmar_ratio,
        "win_rate": r.win_rate,
        "annual_turnover": r.avg_turnover,
        "total_cost_pct": r.total_cost,
        "equity_curve": r.equity_curve,
        "period_returns": r.monthly_returns,
    }

    return {
        "success": True,
        "run_id": r.run_id,
        "name": r.name,
        "notes": r.notes,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "analysis_date": r.analysis_date,
        "elapsed_seconds": r.elapsed_seconds,
        "filters_applied": r.filters_applied,
        "universe": r.universe_info,
        "signal": r.signal_info,
        "ml_model": r.ml_model_info,
        "factors": r.factors,
        "portfolio": {
            "stocks": r.current_portfolio or [],
            "size": len(r.current_portfolio) if r.current_portfolio else 0,
            "config": r.config,
        },
        "data_splits": r.data_splits,
        "backtest": backtest,
        "auto_iterate": r.auto_iterate or False,
        "iterate_status": r.iterate_status,
        "next_iterate_date": str(r.next_iterate_date) if r.next_iterate_date else None,
        "total_iterations": r.total_iterations or 0,
        "live_nav": r.live_nav,
        "live_return_pct": r.live_return_pct,
    }


class ResultUpdateIn(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


@router.put(
    "/result/{run_id}",
    summary="Update name/notes of a saved result",
)
async def update_result(
    run_id: str,
    body: ResultUpdateIn,
    db: AsyncSession = Depends(get_db),
):
    """Update the name and/or notes of a saved analysis result."""
    result = await db.execute(
        select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
    )
    r = result.scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="分析结果不存在")

    if body.name is not None:
        r.name = body.name
    if body.notes is not None:
        r.notes = body.notes
    await db.commit()
    return {"ok": True, "run_id": run_id}


@router.delete(
    "/result/{run_id}",
    summary="Delete a saved analysis result",
)
async def delete_result(run_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a saved analysis result."""
    result = await db.execute(
        select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
    )
    r = result.scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="分析结果不存在")

    await db.delete(r)
    await db.commit()
    return {"ok": True}


# ─── Iteration Endpoints ─────────────────────────────────────────────


class IterateToggleIn(BaseModel):
    auto_iterate: bool


@router.post(
    "/result/{run_id}/iterate",
    summary="Enable or disable auto-iteration for a saved result",
)
async def toggle_auto_iterate(
    run_id: str,
    body: IterateToggleIn,
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable auto-iteration. When enabling, sets next_iterate_date.
    If backtest_start is in the past, launches async historical simulation."""
    result = await db.execute(
        select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
    )
    r = result.scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="分析结果不存在")

    r.auto_iterate = body.auto_iterate

    if body.auto_iterate:
        config = r.config or {}
        filters = r.filters_applied or {}
        rebalance_freq = config.get("rebalance_freq", 10)
        calendar_days = int(rebalance_freq * 7 / 5) + 1
        r.next_iterate_date = date.today() + timedelta(days=calendar_days)
        r.iterate_status = "idle"
        if r.live_nav is None:
            r.live_nav = 1.0
        if r.live_return_pct is None:
            r.live_return_pct = 0.0
        if r.total_iterations is None:
            r.total_iterations = 0

        # Check if backtest_start is in the past → historical simulation
        backtest_start_str = filters.get("backtest_start")
        needs_historical = False
        if backtest_start_str:
            try:
                bs_date = date.fromisoformat(backtest_start_str)
                if bs_date < date.today() - timedelta(days=5):
                    needs_historical = True
            except ValueError:
                pass

        if needs_historical:
            # Delete existing iterations and launch async historical simulation
            from sqlalchemy import delete as sa_delete
            await db.execute(
                sa_delete(QuantIteration).where(QuantIteration.run_id == run_id)
            )
            r.iterate_status = "running"
            await db.commit()

            # Launch async task
            from app.services.quant_iterate import run_historical_iterations
            task_id = f"histiter_{run_id}_{uuid.uuid4().hex[:6]}"

            def _progress_cb(msg):
                _quant_tasks[task_id]["message"] = msg

            _quant_tasks[task_id] = {
                "status": "running",
                "message": "正在创建历史迭代记录...",
                "run_id": run_id,
            }

            async def _run_hist():
                loop = asyncio.get_event_loop()
                try:
                    res = await loop.run_in_executor(
                        None, run_historical_iterations, run_id, _progress_cb,
                    )
                    if res.get("ok"):
                        _quant_tasks[task_id] = {
                            "status": "completed",
                            "message": (
                                f"历史迭代完成: {res['total_periods']}期, "
                                f"NAV={res['nav']:.4f}, 收益{res['return_pct']:+.2f}%"
                            ),
                            "run_id": run_id,
                            "result": res,
                        }
                    else:
                        _quant_tasks[task_id] = {
                            "status": "error",
                            "message": res.get("error", "历史迭代失败"),
                            "run_id": run_id,
                        }
                except Exception as e:
                    logger.error(f"Historical iteration error: {e}", exc_info=True)
                    _quant_tasks[task_id] = {
                        "status": "error", "message": str(e), "run_id": run_id,
                    }

            asyncio.create_task(_run_hist())
            return {
                "ok": True,
                "auto_iterate": True,
                "task_id": task_id,
                "historical": True,
                "message": f"历史迭代任务已提交 (从 {backtest_start_str} 开始模拟)",
                "next_iterate_date": str(r.next_iterate_date),
            }
        else:
            # Normal mode: create initial iteration from today
            existing = await db.execute(
                select(QuantIteration).where(QuantIteration.run_id == run_id).limit(1)
            )
            if existing.scalar_one_or_none() is None:
                portfolio = r.current_portfolio or []
                simplified = [
                    {
                        "stock_code": s.get("stock_code", ""),
                        "stock_name": s.get("stock_name", ""),
                        "industry": s.get("industry", ""),
                        "score": s.get("score", 0),
                        "rank": s.get("rank", 0),
                    }
                    for s in portfolio
                ]
                initial_iter = QuantIteration(
                    run_id=run_id,
                    iteration_num=0,
                    start_date=date.today(),
                    portfolio=simplified,
                    new_buys=[s["stock_code"] for s in simplified],
                    new_sells=[],
                    nav=1.0,
                    status="active",
                    message=f"初始持仓 | {len(simplified)}只股票",
                )
                db.add(initial_iter)
    else:
        r.iterate_status = None
        r.next_iterate_date = None

    await db.commit()
    return {
        "ok": True,
        "auto_iterate": r.auto_iterate,
        "next_iterate_date": str(r.next_iterate_date) if r.next_iterate_date else None,
    }


@router.get(
    "/result/{run_id}/iterations",
    summary="Get iteration timeline for a saved result",
)
async def get_iterations(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return all iterations for a given run, newest first."""
    result = await db.execute(
        select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
    )
    r = result.scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="分析结果不存在")

    iter_result = await db.execute(
        select(QuantIteration)
        .where(QuantIteration.run_id == run_id)
        .order_by(desc(QuantIteration.iteration_num))
    )
    iterations = iter_result.scalars().all()

    items = []
    for it in iterations:
        items.append({
            "id": it.id,
            "iteration_num": it.iteration_num,
            "start_date": str(it.start_date) if it.start_date else None,
            "end_date": str(it.end_date) if it.end_date else None,
            "portfolio": it.portfolio or [],
            "portfolio_size": len(it.portfolio) if it.portfolio else 0,
            "new_buys": it.new_buys or [],
            "new_sells": it.new_sells or [],
            "period_return_pct": it.period_return_pct,
            "cumulative_return_pct": it.cumulative_return_pct,
            "nav": it.nav,
            "stock_returns": it.stock_returns,
            "status": it.status,
            "message": it.message,
            "created_at": it.created_at.isoformat() if it.created_at else None,
            "completed_at": it.completed_at.isoformat() if it.completed_at else None,
        })

    return {
        "run_id": run_id,
        "auto_iterate": r.auto_iterate or False,
        "iterate_status": r.iterate_status,
        "next_iterate_date": str(r.next_iterate_date) if r.next_iterate_date else None,
        "total_iterations": r.total_iterations or 0,
        "live_nav": r.live_nav,
        "live_return_pct": r.live_return_pct,
        "iterations": items,
    }


@router.post(
    "/result/{run_id}/iterate-now",
    summary="Manually trigger one iteration cycle",
)
async def trigger_iteration(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Manually run one iteration cycle in a background thread."""
    result = await db.execute(
        select(QuantBacktestResult).where(QuantBacktestResult.run_id == run_id)
    )
    r = result.scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="分析结果不存在")
    if not r.auto_iterate:
        raise HTTPException(status_code=400, detail="自动迭代未启用，请先启用")
    if r.iterate_status == "running":
        raise HTTPException(status_code=400, detail="迭代正在运行中，请稍后再试")

    # Run in background thread pool
    from app.services.quant_iterate import run_single_iteration

    task_id = f"iter_{run_id}_{uuid.uuid4().hex[:6]}"
    _quant_tasks[task_id] = {"status": "running", "message": "正在执行迭代...", "run_id": run_id}

    async def _run():
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, run_single_iteration, run_id)
            if result.get("ok"):
                _quant_tasks[task_id] = {
                    "status": "completed",
                    "message": (
                        f"迭代完成: 第{result['iteration_num']}期, "
                        f"持仓{result['portfolio_size']}只, "
                        f"买入{result['buys']}只 卖出{result['sells']}只"
                    ),
                    "run_id": run_id,
                    "result": result,
                }
            else:
                _quant_tasks[task_id] = {
                    "status": "error",
                    "message": result.get("error", "未知错误"),
                    "run_id": run_id,
                }
        except Exception as e:
            logger.error(f"Iteration task error: {e}", exc_info=True)
            _quant_tasks[task_id] = {
                "status": "error", "message": str(e), "run_id": run_id,
            }

    asyncio.create_task(_run())
    return {"ok": True, "task_id": task_id, "message": "迭代任务已提交"}


# ─── Factor / Utility Endpoints ──────────────────────────────────────

@router.get("/factors", summary="List all available quantitative factors")
async def list_factors():
    from app.services.quant_engine import FACTOR_DEFS, CATEGORY_LABELS
    return {"total": len(FACTOR_DEFS), "categories": CATEGORY_LABELS, "factors": FACTOR_DEFS}


@router.get("/stock-profile/{stock_id}", summary="Get single stock factor profile")
async def get_stock_profile(stock_id: int, db: AsyncSession = Depends(get_db)):
    from app.services.quant_engine import get_stock_factor_profile

    result = await db.execute(select(Stock).where(Stock.id == stock_id))
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    kline_result = await db.execute(
        select(DailyKline).where(DailyKline.stock_id == stock_id).order_by(DailyKline.date)
    )
    rows = kline_result.scalars().all()
    if not rows:
        raise HTTPException(status_code=400, detail=f"股票 {stock.code} 没有K线数据")

    kline_df = pd.DataFrame([{
        "date": r.date, "open": r.open, "high": r.high, "low": r.low,
        "close": r.close, "volume": r.volume, "amount": r.amount,
        "change_pct": r.change_pct, "turnover_rate": r.turnover_rate or 0,
    } for r in rows])

    prof_result = await db.execute(select(StockProfile).where(StockProfile.stock_id == stock_id))
    profile = prof_result.scalar_one_or_none()

    stock_info_dict = {
        stock_id: {
            "code": stock.code, "name": stock.name, "market": stock.market,
            "industry": profile.industry if profile else None,
            "pe_ttm": profile.pe_ttm if profile else None,
            "pb": profile.pb if profile else None,
            "total_market_cap": profile.total_market_cap if profile else None,
        }
    }

    analysis = get_stock_factor_profile(stock_id, {stock_id: kline_df}, stock_info_dict)
    if not analysis.get("success"):
        raise HTTPException(status_code=400, detail=analysis.get("error", "分析失败"))
    return analysis


@router.get("/factor-status", summary="Check factor computation status")
async def get_factor_status(db: AsyncSession = Depends(get_db)):
    try:
        from app.models import QuantFactorDaily
        total = await db.execute(select(func.count(QuantFactorDaily.id)))
        total_count = total.scalar() or 0
        latest = await db.execute(select(func.max(QuantFactorDaily.date)))
        latest_date = latest.scalar()
        universe_count = 0
        if latest_date:
            uc = await db.execute(
                select(func.count(QuantFactorDaily.id))
                .where(QuantFactorDaily.date == latest_date, QuantFactorDaily.in_universe == True)
            )
            universe_count = uc.scalar() or 0
        from app.services.quant_engine import FACTOR_DEFS
        return {
            "total_records": total_count, "latest_date": str(latest_date) if latest_date else None,
            "universe_size": universe_count, "factor_count": len(FACTOR_DEFS),
        }
    except Exception:
        from app.services.quant_engine import FACTOR_DEFS
        return {"total_records": 0, "latest_date": None, "universe_size": 0, "factor_count": len(FACTOR_DEFS), "note": "Factor table not yet created"}


@router.get("/industries", summary="List available industries for filtering")
async def list_industries(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StockProfile.industry, func.count(StockProfile.id))
        .where(StockProfile.industry.isnot(None), StockProfile.industry != "")
        .group_by(StockProfile.industry)
        .order_by(func.count(StockProfile.id).desc())
    )
    rows = result.all()
    return {"total": len(rows), "industries": [{"name": row[0], "count": row[1]} for row in rows]}


@router.get("/presets", summary="List preset universe strategies")
async def list_presets():
    return {
        "presets": [
            {"name": k, "label": v["label"], "description": v["description"], "filters": v.get("filters", {})}
            for k, v in PRESET_STRATEGIES.items()
        ],
    }
