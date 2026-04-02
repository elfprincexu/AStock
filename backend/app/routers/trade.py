"""
Trading Strategy API Router.

Provides endpoints for:
- Creating/editing/deleting strategies with steps and conditions
- Activating/pausing/cancelling strategies
- Querying strategy status and execution history
- Manual quote-injection for testing
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete, create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, Session as SyncSession

from app.config import settings
from app.database import get_db
from app.models import (
    TradeStrategy, TradeStep, TradeCondition, TradeExecution,
)

# Shared sync engine for auto-tick (avoid re-creating per request)
_sync_engine = create_engine(settings.DATABASE_URL_SYNC, echo=False, pool_size=2)

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Pydantic schemas
# ---------------------------------------------------------------------------

class ConditionIn(BaseModel):
    field: str  # price / open_price / change_pct / rise_pct / fall_pct / profit_pct / loss_pct / volume / amount / turnover_rate
    operator: str  # >= / <= / > / < / ==
    value: float


class StepIn(BaseModel):
    name: str = ""
    condition_logic: str = "AND"  # AND / OR
    conditions: list[ConditionIn] = []
    action_type: str  # buy / sell
    quantity: int
    price_type: str = "market"  # market / limit
    limit_price: Optional[float] = None


class StrategyCreate(BaseModel):
    name: str
    stock_code: str
    stock_name: str = ""
    market: str = "SH"
    mode: str = "simulated"  # simulated / live
    sim_initial_cash: float = 100000.0
    notes: Optional[str] = None
    steps: list[StepIn] = []


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None
    market: Optional[str] = None
    notes: Optional[str] = None
    mode: Optional[str] = None
    sim_initial_cash: Optional[float] = None
    steps: Optional[list[StepIn]] = None


class ConditionOut(BaseModel):
    id: int
    field: str
    operator: str
    value: float
    is_met: bool
    met_at: Optional[str] = None


class StepOut(BaseModel):
    id: int
    step_order: int
    name: str
    condition_logic: str
    conditions: list[ConditionOut] = []
    action_type: str
    quantity: int
    price_type: str
    limit_price: Optional[float] = None
    status: str
    fill_price: Optional[float] = None
    fill_quantity: Optional[int] = None
    filled_at: Optional[str] = None


class StrategyOut(BaseModel):
    id: int
    name: str
    stock_code: str
    stock_name: str
    market: str
    mode: str
    status: str
    current_step_order: int
    sim_initial_cash: float
    sim_cash: float
    sim_holdings: int
    sim_avg_cost: float
    notes: Optional[str] = None
    error_message: Optional[str] = None
    steps: list[StepOut] = []
    created_at: str
    updated_at: str


class ExecutionOut(BaseModel):
    id: int
    strategy_id: int
    step_id: Optional[int] = None
    event_type: str
    message: Optional[str] = None
    price_snapshot: Optional[float] = None
    created_at: str


class ManualQuoteIn(BaseModel):
    """For manual testing — inject a fake quote to evaluate conditions."""
    price: float
    change_pct: float = 0.0
    volume: int = 0
    amount: float = 0.0
    turnover_rate: float = 0.0
    open: float = 0.0
    prev_close: float = 0.0


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _strategy_to_out(s: TradeStrategy) -> dict:
    """Convert a TradeStrategy ORM object to response dict."""
    steps_out = []
    for step in (s.steps or []):
        conds_out = []
        for c in (step.conditions or []):
            conds_out.append({
                "id": c.id,
                "field": c.field,
                "operator": c.operator,
                "value": c.value,
                "is_met": c.is_met,
                "met_at": c.met_at.isoformat() if c.met_at else None,
            })
        steps_out.append({
            "id": step.id,
            "step_order": step.step_order,
            "name": step.name,
            "condition_logic": step.condition_logic,
            "conditions": conds_out,
            "action_type": step.action_type,
            "quantity": step.quantity,
            "price_type": step.price_type,
            "limit_price": step.limit_price,
            "status": step.status,
            "fill_price": step.fill_price,
            "fill_quantity": step.fill_quantity,
            "filled_at": step.filled_at.isoformat() if step.filled_at else None,
        })
    return {
        "id": s.id,
        "name": s.name,
        "stock_code": s.stock_code,
        "stock_name": s.stock_name,
        "market": s.market,
        "mode": s.mode,
        "status": s.status,
        "current_step_order": s.current_step_order,
        "sim_initial_cash": s.sim_initial_cash,
        "sim_cash": s.sim_cash,
        "sim_holdings": s.sim_holdings,
        "sim_avg_cost": s.sim_avg_cost,
        "notes": s.notes,
        "error_message": s.error_message,
        "steps": steps_out,
        "created_at": s.created_at.isoformat() if s.created_at else "",
        "updated_at": s.updated_at.isoformat() if s.updated_at else "",
    }


async def _load_strategy(db: AsyncSession, strategy_id: int) -> TradeStrategy:
    """Load a strategy with steps+conditions eagerly."""
    stmt = (
        select(TradeStrategy)
        .where(TradeStrategy.id == strategy_id)
        .options(
            selectinload(TradeStrategy.steps)
            .selectinload(TradeStep.conditions)
        )
    )
    result = await db.execute(stmt)
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    return strategy


# ---------------------------------------------------------------------------
#  Endpoints: CRUD
# ---------------------------------------------------------------------------

@router.get("/strategies")
async def list_strategies(
    status: Optional[str] = Query(None),
    stock_code: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List all strategies, optionally filtered by status or stock."""
    stmt = (
        select(TradeStrategy)
        .options(
            selectinload(TradeStrategy.steps)
            .selectinload(TradeStep.conditions)
        )
        .order_by(TradeStrategy.updated_at.desc())
    )
    if status:
        stmt = stmt.where(TradeStrategy.status == status)
    if stock_code:
        stmt = stmt.where(TradeStrategy.stock_code == stock_code)
    result = await db.execute(stmt)
    strategies = result.scalars().all()
    return [_strategy_to_out(s) for s in strategies]


@router.post("/strategies", status_code=201)
async def create_strategy(
    req: StrategyCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new strategy with steps and conditions."""
    strategy = TradeStrategy(
        name=req.name,
        stock_code=req.stock_code,
        stock_name=req.stock_name,
        market=req.market.upper(),
        mode=req.mode,
        sim_initial_cash=req.sim_initial_cash,
        sim_cash=req.sim_initial_cash,
        notes=req.notes,
    )
    db.add(strategy)
    await db.flush()  # get strategy.id

    for i, step_in in enumerate(req.steps):
        step = TradeStep(
            strategy_id=strategy.id,
            step_order=i + 1,
            name=step_in.name,
            condition_logic=step_in.condition_logic,
            action_type=step_in.action_type,
            quantity=step_in.quantity,
            price_type=step_in.price_type,
            limit_price=step_in.limit_price,
        )
        db.add(step)
        await db.flush()
        for cond_in in step_in.conditions:
            cond = TradeCondition(
                step_id=step.id,
                field=cond_in.field,
                operator=cond_in.operator,
                value=cond_in.value,
            )
            db.add(cond)

    await db.commit()
    # Reload with relationships
    strategy = await _load_strategy(db, strategy.id)
    return _strategy_to_out(strategy)


@router.get("/strategies/{strategy_id}")
async def get_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a single strategy with full details."""
    strategy = await _load_strategy(db, strategy_id)
    return _strategy_to_out(strategy)


@router.put("/strategies/{strategy_id}")
async def update_strategy(
    strategy_id: int,
    req: StrategyUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update strategy metadata and/or replace steps."""
    strategy = await _load_strategy(db, strategy_id)
    if strategy.status not in ("draft", "paused", "error", "completed", "cancelled"):
        raise HTTPException(400, "无法编辑运行中的策略, 请先暂停")

    if req.name is not None:
        strategy.name = req.name
    if req.stock_code is not None:
        strategy.stock_code = req.stock_code
    if req.stock_name is not None:
        strategy.stock_name = req.stock_name
    if req.market is not None:
        strategy.market = req.market.upper()
    if req.notes is not None:
        strategy.notes = req.notes
    if req.mode is not None:
        strategy.mode = req.mode
    if req.sim_initial_cash is not None:
        strategy.sim_initial_cash = req.sim_initial_cash

    if req.steps is not None:
        # Use ORM-level deletion to keep session state consistent
        # (bulk SQL delete can desync the identity map in async sessions)
        strategy.steps.clear()
        await db.flush()

        # Also clear stale execution logs when steps are replaced
        await db.execute(
            delete(TradeExecution)
            .where(TradeExecution.strategy_id == strategy.id)
            .execution_options(synchronize_session=False)
        )

        # Reset simulation state
        strategy.sim_cash = strategy.sim_initial_cash
        strategy.sim_holdings = 0
        strategy.sim_avg_cost = 0
        strategy.current_step_order = 1
        strategy.status = "draft"
        strategy.error_message = None

        for i, step_in in enumerate(req.steps):
            step = TradeStep(
                strategy_id=strategy.id,
                step_order=i + 1,
                name=step_in.name,
                condition_logic=step_in.condition_logic,
                action_type=step_in.action_type,
                quantity=step_in.quantity,
                price_type=step_in.price_type,
                limit_price=step_in.limit_price,
            )
            db.add(step)
            await db.flush()
            for cond_in in step_in.conditions:
                cond = TradeCondition(
                    step_id=step.id,
                    field=cond_in.field,
                    operator=cond_in.operator,
                    value=cond_in.value,
                )
                db.add(cond)

    await db.commit()
    strategy = await _load_strategy(db, strategy.id)
    return _strategy_to_out(strategy)


@router.delete("/strategies/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete a strategy and all related data."""
    strategy = await _load_strategy(db, strategy_id)
    if strategy.status == "active":
        raise HTTPException(400, "无法删除运行中的策略, 请先暂停或取消")
    await db.delete(strategy)
    await db.commit()


# ---------------------------------------------------------------------------
#  Endpoints: Control
# ---------------------------------------------------------------------------

@router.post("/strategies/{strategy_id}/activate")
async def activate_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Activate a strategy — start monitoring conditions."""
    strategy = await _load_strategy(db, strategy_id)
    if strategy.status not in ("draft", "paused"):
        raise HTTPException(400, f"当前状态 '{strategy.status}' 无法启动")
    if not strategy.steps:
        raise HTTPException(400, "策略没有步骤, 无法启动")

    strategy.status = "active"
    strategy.error_message = None

    # Find the first non-completed step
    activated = False
    for step in strategy.steps:
        if step.status in ("waiting", "cancelled"):
            step.status = "watching"
            strategy.current_step_order = step.step_order
            for cond in step.conditions:
                cond.is_met = False
                cond.met_at = None
            activated = True
            break
        elif step.status == "watching":
            activated = True
            break

    if not activated:
        strategy.status = "completed"

    # Log event
    db.add(TradeExecution(
        strategy_id=strategy.id,
        event_type="strategy_started",
        message=f"策略已启动 (模式: {strategy.mode})",
    ))
    await db.commit()
    strategy = await _load_strategy(db, strategy.id)
    return _strategy_to_out(strategy)


@router.post("/strategies/{strategy_id}/pause")
async def pause_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Pause a running strategy."""
    strategy = await _load_strategy(db, strategy_id)
    if strategy.status != "active":
        raise HTTPException(400, f"当前状态 '{strategy.status}' 无法暂停")

    strategy.status = "paused"
    db.add(TradeExecution(
        strategy_id=strategy.id,
        event_type="strategy_paused",
        message="策略已暂停",
    ))
    await db.commit()
    strategy = await _load_strategy(db, strategy.id)
    return _strategy_to_out(strategy)


@router.post("/strategies/{strategy_id}/cancel")
async def cancel_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a strategy."""
    strategy = await _load_strategy(db, strategy_id)
    if strategy.status in ("completed", "cancelled"):
        raise HTTPException(400, f"策略已经{strategy.status}")

    strategy.status = "cancelled"
    for step in strategy.steps:
        if step.status in ("waiting", "watching"):
            step.status = "cancelled"

    db.add(TradeExecution(
        strategy_id=strategy.id,
        event_type="strategy_cancelled",
        message="策略已取消",
    ))
    await db.commit()
    strategy = await _load_strategy(db, strategy.id)
    return _strategy_to_out(strategy)


@router.post("/strategies/{strategy_id}/reset")
async def reset_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Reset a strategy to draft state, clearing all fills and simulation data."""
    strategy = await _load_strategy(db, strategy_id)
    if strategy.status == "active":
        raise HTTPException(400, "请先暂停策略")

    strategy.status = "draft"
    strategy.current_step_order = 1
    strategy.sim_cash = strategy.sim_initial_cash
    strategy.sim_holdings = 0
    strategy.sim_avg_cost = 0
    strategy.error_message = None

    for step in strategy.steps:
        step.status = "waiting"
        step.fill_price = None
        step.fill_quantity = None
        step.filled_at = None
        for cond in step.conditions:
            cond.is_met = False
            cond.met_at = None

    # Clear execution logs — use synchronize_session=False for async safety
    await db.execute(
        delete(TradeExecution)
        .where(TradeExecution.strategy_id == strategy.id)
        .execution_options(synchronize_session=False)
    )
    await db.flush()

    db.add(TradeExecution(
        strategy_id=strategy.id,
        event_type="strategy_reset",
        message="策略已重置",
    ))
    await db.commit()
    strategy = await _load_strategy(db, strategy.id)
    return _strategy_to_out(strategy)


# ---------------------------------------------------------------------------
#  Endpoints: Execution log
# ---------------------------------------------------------------------------

@router.get("/strategies/{strategy_id}/executions")
async def get_executions(
    strategy_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get execution log for a strategy."""
    stmt = (
        select(TradeExecution)
        .where(TradeExecution.strategy_id == strategy_id)
        .order_by(TradeExecution.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    execs = result.scalars().all()
    return [
        {
            "id": e.id,
            "strategy_id": e.strategy_id,
            "step_id": e.step_id,
            "event_type": e.event_type,
            "message": e.message,
            "price_snapshot": e.price_snapshot,
            "created_at": e.created_at.isoformat() if e.created_at else "",
        }
        for e in execs
    ]


# ---------------------------------------------------------------------------
#  Endpoints: Manual test (inject quote)
# ---------------------------------------------------------------------------

@router.post("/strategies/{strategy_id}/test-tick")
async def test_tick(
    strategy_id: int,
    quote: ManualQuoteIn,
    db: AsyncSession = Depends(get_db),
):
    """
    Manually inject a quote to test condition evaluation.
    Uses sync engine internally to run the trade engine.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SyncSession
    from app.config import settings
    from app.services.trade_engine import trade_engine

    strategy = await _load_strategy(db, strategy_id)
    if strategy.status != "active":
        raise HTTPException(400, "策略未激活, 请先启动")

    # Run engine with sync session
    sync_engine = create_engine(settings.DATABASE_URL_SYNC, echo=False)
    with SyncSession(sync_engine) as sync_db:
        quotes = {
            strategy.stock_code: {
                "price": quote.price,
                "change_pct": quote.change_pct,
                "volume": quote.volume,
                "amount": quote.amount,
                "turnover_rate": quote.turnover_rate,
                "open": quote.open,
                "prev_close": quote.prev_close,
            }
        }
        events = trade_engine.tick(sync_db, quotes)
    sync_engine.dispose()

    # Expire stale ORM objects so _load_strategy fetches fresh data
    db.expire_all()
    strategy = await _load_strategy(db, strategy.id)
    return {
        "events": events,
        "strategy": _strategy_to_out(strategy),
    }


# ---------------------------------------------------------------------------
#  Endpoints: Summary stats
# ---------------------------------------------------------------------------

@router.get("/summary")
async def trade_summary(db: AsyncSession = Depends(get_db)):
    """Overview statistics for the trading module."""
    total = (await db.execute(select(func.count(TradeStrategy.id)))).scalar() or 0
    active = (await db.execute(
        select(func.count(TradeStrategy.id)).where(TradeStrategy.status == "active")
    )).scalar() or 0
    completed = (await db.execute(
        select(func.count(TradeStrategy.id)).where(TradeStrategy.status == "completed")
    )).scalar() or 0
    return {
        "total_strategies": total,
        "active_strategies": active,
        "completed_strategies": completed,
    }


# ---------------------------------------------------------------------------
#  Endpoints: Auto-tick (real-time quote-driven execution)
# ---------------------------------------------------------------------------

@router.post("/strategies/{strategy_id}/auto-tick")
async def auto_tick(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch a real-time quote for the strategy's stock and process it
    through the trade engine.  This provides real-time monitoring
    without requiring a Celery worker.
    """
    from app.services.trade_engine import trade_engine
    from app.services.aggregator import StockDataAggregator

    strategy = await _load_strategy(db, strategy_id)
    if strategy.status != "active":
        raise HTTPException(400, "策略未激活")

    # Capture values before any expire — avoid MissingGreenlet
    stock_code = strategy.stock_code
    stock_market = strategy.market

    # Fetch real-time quote via aggregator
    agg = StockDataAggregator(
        priority=settings.DATA_SOURCE_PRIORITY,
        timeout=settings.DATA_SOURCE_TIMEOUT,
    )
    try:
        quote = await agg.get_realtime_quote(stock_code, stock_market)
    except Exception as e:
        logger.warning("[auto-tick] Quote fetch failed for %s: %s",
                       stock_code, e)
        return {
            "events": [],
            "strategy": _strategy_to_out(strategy),
            "quote": None,
            "error": f"行情获取失败: {e}",
        }
    finally:
        await agg.close()

    if not quote or quote.get("price", 0) <= 0:
        return {
            "events": [],
            "strategy": _strategy_to_out(strategy),
            "quote": quote,
        }

    # Run engine with sync session
    with SyncSession(_sync_engine) as sync_db:
        events = trade_engine.tick(sync_db, {stock_code: quote})

    # Expire stale ORM objects and reload (use strategy_id param, not strategy.id)
    db.expire_all()
    strategy = await _load_strategy(db, strategy_id)
    return {
        "events": events,
        "strategy": _strategy_to_out(strategy),
        "quote": {
            "price": quote.get("price"),
            "change_pct": quote.get("change_pct"),
            "volume": quote.get("volume"),
            "amount": quote.get("amount"),
            "turnover_rate": quote.get("turnover_rate"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "open": quote.get("open"),
            "prev_close": quote.get("prev_close") or quote.get("yesterday_close"),
            "name": quote.get("name", ""),
        },
    }


# ---------------------------------------------------------------------------
#  Endpoints: Broker status and account (live trading)
# ---------------------------------------------------------------------------

@router.get("/broker/status")
async def broker_status():
    """Check broker connection status and configuration."""
    from app.config import settings
    configured = bool(settings.BROKER_ACCOUNT and settings.BROKER_QMT_PATH)

    result = {
        "configured": configured,
        "account": settings.BROKER_ACCOUNT[:4] + "****" if settings.BROKER_ACCOUNT else "",
        "qmt_path": settings.BROKER_QMT_PATH or "",
        "connected": False,
        "xtquant_installed": False,
    }

    try:
        from app.services.brokers.pingan import _xt_available
        result["xtquant_installed"] = _xt_available
    except ImportError:
        pass

    if configured:
        try:
            from app.services.trade_engine import trade_engine
            broker = trade_engine._get_broker()
            if broker:
                result["connected"] = broker.is_connected()
        except Exception:
            pass

    return result


@router.post("/broker/connect")
async def broker_connect():
    """Attempt to connect to the broker."""
    from app.services.trade_engine import trade_engine
    broker = trade_engine._get_broker()
    if not broker:
        raise HTTPException(400, "券商未配置, 请在配置管理中设置账号和QMT路径")
    try:
        broker.connect()
        return {"success": True, "message": "已连接到券商"}
    except Exception as e:
        raise HTTPException(400, f"连接失败: {e}")


@router.post("/broker/disconnect")
async def broker_disconnect():
    """Disconnect from the broker."""
    from app.services.trade_engine import trade_engine
    broker = trade_engine._get_broker()
    if broker:
        broker.disconnect()
    return {"success": True, "message": "已断开券商连接"}


@router.get("/broker/account")
async def broker_account():
    """Get live broker account info and positions."""
    from app.services.trade_engine import trade_engine
    broker = trade_engine._get_broker()
    if not broker or not broker.is_connected():
        raise HTTPException(400, "券商未连接")
    try:
        account = broker.get_account()
        return {
            "total_asset": account.total_asset,
            "cash": account.cash,
            "market_value": account.market_value,
            "frozen": account.frozen,
            "positions": [
                {
                    "stock_code": p.stock_code,
                    "stock_name": p.stock_name,
                    "market": p.market,
                    "quantity": p.quantity,
                    "available_quantity": p.available_quantity,
                    "avg_cost": p.avg_cost,
                    "current_price": p.current_price,
                    "market_value": p.market_value,
                    "profit": p.profit,
                }
                for p in account.positions
            ],
        }
    except Exception as e:
        raise HTTPException(400, f"查询账户失败: {e}")


# ---------------------------------------------------------------------------
#  Endpoints: Intraday minute klines for strategy chart
# ---------------------------------------------------------------------------

@router.get("/strategies/{strategy_id}/intraday")
async def get_intraday(
    strategy_id: int,
    scale: int = Query(default=5, ge=5, le=60),
    limit: int = Query(default=240, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch intraday minute-level K-line data for the strategy's stock.
    Uses Sina Finance as the data source.
    """
    from app.services.sina import SinaClient

    strategy = await _load_strategy(db, strategy_id)
    stock_code = strategy.stock_code
    stock_market = strategy.market

    client = SinaClient(timeout=settings.DATA_SOURCE_TIMEOUT)
    try:
        klines = await client.get_minute_klines(
            stock_code, stock_market, scale=scale, limit=limit,
        )
    except Exception as e:
        logger.warning("[intraday] Failed for %s: %s", stock_code, e)
        return {"klines": [], "error": str(e)}
    finally:
        await client.close()

    return {
        "stock_code": stock_code,
        "stock_name": strategy.stock_name,
        "market": stock_market,
        "scale": scale,
        "klines": klines,
    }
