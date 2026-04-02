"""
Trade Condition Engine — evaluates conditions, advances steps, executes orders.

This is the core "brain" of the strategy trading system.  It runs inside the
Celery worker (sync SQLAlchemy session) and does:

1. Load all active strategies
2. For each strategy, find the current watching step
3. Evaluate all conditions of that step against real-time quote data
4. If conditions are met (AND/OR), trigger the action (buy/sell)
5. After fill, advance to the next step (or complete the strategy)
"""

import logging
import operator as op
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    TradeStrategy, TradeStep, TradeCondition, TradeExecution,
)

logger = logging.getLogger(__name__)

# Comparison operators
OPERATORS = {
    ">=": op.ge,
    "<=": op.le,
    ">": op.gt,
    "<": op.lt,
    "==": op.eq,
}


class TradeEngine:
    """Stateless engine — operates on a sync DB session provided per call."""

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def tick(self, db: Session, quotes: dict[str, dict[str, Any]]) -> list[dict]:
        """
        Main entry point — called every few seconds by the Celery worker.

        Parameters
        ----------
        db : Session
            Sync SQLAlchemy session.
        quotes : dict
            {stock_code: {price, open, high, low, close, volume, amount,
                          change_pct, turnover_rate, ...}}

        Returns
        -------
        list[dict]
            List of events that happened during this tick.
        """
        events = []
        strategies = self._load_active_strategies(db)

        for strategy in strategies:
            code = strategy.stock_code
            quote = quotes.get(code)
            if not quote:
                continue

            try:
                step_events = self._process_strategy(db, strategy, quote)
                events.extend(step_events)
            except Exception as e:
                logger.exception("Error processing strategy %d: %s", strategy.id, e)
                strategy.status = "error"
                strategy.error_message = str(e)[:500]
                self._log_event(db, strategy, None, "error",
                                f"引擎异常: {e}", quote.get("price"))
                db.commit()

        return events

    def activate_strategy(self, db: Session, strategy_id: int) -> TradeStrategy:
        """Start a strategy — set first step to watching."""
        strategy = db.get(TradeStrategy, strategy_id)
        if not strategy:
            raise ValueError(f"Strategy {strategy_id} not found")
        if strategy.status not in ("draft", "paused"):
            raise ValueError(f"Cannot activate strategy in status '{strategy.status}'")

        strategy.status = "active"
        strategy.error_message = None

        # Find the first non-completed step and set it to watching
        for step in strategy.steps:
            if step.status in ("waiting", "cancelled"):
                step.status = "watching"
                strategy.current_step_order = step.step_order
                # Reset conditions
                for cond in step.conditions:
                    cond.is_met = False
                    cond.met_at = None
                break

        self._log_event(db, strategy, None, "strategy_started",
                        f"策略已启动 (模式: {strategy.mode})")
        db.commit()
        return strategy

    def pause_strategy(self, db: Session, strategy_id: int) -> TradeStrategy:
        """Pause a running strategy."""
        strategy = db.get(TradeStrategy, strategy_id)
        if not strategy:
            raise ValueError(f"Strategy {strategy_id} not found")
        if strategy.status != "active":
            raise ValueError(f"Cannot pause strategy in status '{strategy.status}'")

        strategy.status = "paused"
        self._log_event(db, strategy, None, "strategy_paused", "策略已暂停")
        db.commit()
        return strategy

    def cancel_strategy(self, db: Session, strategy_id: int) -> TradeStrategy:
        """Cancel a strategy."""
        strategy = db.get(TradeStrategy, strategy_id)
        if not strategy:
            raise ValueError(f"Strategy {strategy_id} not found")
        if strategy.status in ("completed", "cancelled"):
            raise ValueError(f"Strategy already {strategy.status}")

        strategy.status = "cancelled"
        for step in strategy.steps:
            if step.status in ("waiting", "watching"):
                step.status = "cancelled"
        self._log_event(db, strategy, None, "strategy_cancelled", "策略已取消")
        db.commit()
        return strategy

    # ------------------------------------------------------------------
    #  Internal: strategy processing
    # ------------------------------------------------------------------

    def _load_active_strategies(self, db: Session) -> list[TradeStrategy]:
        """Load all strategies with status='active', eagerly loading steps+conditions."""
        stmt = (
            select(TradeStrategy)
            .where(TradeStrategy.status == "active")
            .options(
                selectinload(TradeStrategy.steps)
                .selectinload(TradeStep.conditions)
            )
        )
        return list(db.execute(stmt).scalars().all())

    def _process_strategy(
        self, db: Session, strategy: TradeStrategy, quote: dict
    ) -> list[dict]:
        """Process a single strategy against current quote data."""
        events = []
        current_step = self._get_current_step(strategy)
        if not current_step:
            # No more steps — strategy is done
            if strategy.status == "active":
                strategy.status = "completed"
                self._log_event(db, strategy, None, "strategy_completed",
                                "所有步骤已完成")
                db.commit()
                events.append({"strategy_id": strategy.id, "event": "completed"})
            return events

        if current_step.status != "watching":
            return events

        # Evaluate conditions
        all_met = self._evaluate_step_conditions(db, strategy, current_step, quote)

        if all_met:
            # Conditions met — trigger order
            self._log_event(db, strategy, current_step, "step_triggered",
                            f"步骤{current_step.step_order}条件满足, 触发{current_step.action_type}",
                            quote.get("price"))
            events.append({
                "strategy_id": strategy.id,
                "step_id": current_step.id,
                "event": "triggered",
                "action": current_step.action_type,
            })

            # Execute order
            fill_result = self._execute_order(db, strategy, current_step, quote)
            if fill_result["success"]:
                events.append({
                    "strategy_id": strategy.id,
                    "step_id": current_step.id,
                    "event": "filled",
                    "fill_price": fill_result["fill_price"],
                    "fill_quantity": fill_result["fill_quantity"],
                })

                # Advance to next step
                self._advance_to_next_step(db, strategy, current_step)
            else:
                events.append({
                    "strategy_id": strategy.id,
                    "step_id": current_step.id,
                    "event": "fill_failed",
                    "reason": fill_result.get("reason", "unknown"),
                })

            db.commit()

        return events

    def _get_current_step(self, strategy: TradeStrategy) -> TradeStep | None:
        """Find the current active step for the strategy."""
        for step in strategy.steps:
            if step.step_order == strategy.current_step_order:
                return step
        # Fallback: find first non-completed step
        for step in strategy.steps:
            if step.status in ("waiting", "watching"):
                return step
        return None

    # ------------------------------------------------------------------
    #  Internal: condition evaluation
    # ------------------------------------------------------------------

    def _evaluate_step_conditions(
        self, db: Session, strategy: TradeStrategy,
        step: TradeStep, quote: dict
    ) -> bool:
        """
        Evaluate all conditions for a step. Returns True if the step's
        conditions are met (according to AND/OR logic).
        """
        if not step.conditions:
            return True  # No conditions = always met

        results = []
        for cond in step.conditions:
            met = self._evaluate_single_condition(cond, strategy, step, quote)
            if met and not cond.is_met:
                cond.is_met = True
                cond.met_at = datetime.utcnow()
                self._log_event(db, strategy, step, "condition_met",
                                f"{cond.field} {cond.operator} {cond.value} 已满足 "
                                f"(当前值: {self._get_field_value(cond.field, strategy, quote):.4f})",
                                quote.get("price"))
            elif not met and cond.is_met:
                # Condition was met but no longer — reset for AND logic
                if step.condition_logic == "AND":
                    cond.is_met = False
                    cond.met_at = None
            results.append(met)

        if step.condition_logic == "AND":
            return all(results)
        else:  # OR
            return any(results)

    def _evaluate_single_condition(
        self, cond: TradeCondition, strategy: TradeStrategy,
        step: TradeStep, quote: dict
    ) -> bool:
        """Evaluate a single condition against current data."""
        current_value = self._get_field_value(cond.field, strategy, quote)
        if current_value is None:
            return False

        cmp_func = OPERATORS.get(cond.operator)
        if not cmp_func:
            logger.warning("Unknown operator: %s", cond.operator)
            return False

        return cmp_func(current_value, cond.value)

    def _get_field_value(
        self, field: str, strategy: TradeStrategy, quote: dict
    ) -> float | None:
        """Extract the value of a condition field from quote + strategy state."""
        if field == "price":
            return quote.get("price")
        elif field == "change_pct":
            return quote.get("change_pct")
        elif field == "volume":
            return quote.get("volume")
        elif field == "amount":
            return quote.get("amount")
        elif field == "turnover_rate":
            return quote.get("turnover_rate")
        elif field == "open_price":
            return quote.get("open")
        elif field == "rise_pct":
            # 涨幅: (当前价 - 昨收) / 昨收 * 100, 只返回正值(上涨时)
            price = quote.get("price")
            prev_close = quote.get("prev_close") or quote.get("yesterday_close")
            if price and prev_close and prev_close > 0:
                pct = (price - prev_close) / prev_close * 100
                return pct if pct >= 0 else 0.0
            return None
        elif field == "fall_pct":
            # 跌幅: (昨收 - 当前价) / 昨收 * 100, 只返回正值(下跌时)
            price = quote.get("price")
            prev_close = quote.get("prev_close") or quote.get("yesterday_close")
            if price and prev_close and prev_close > 0:
                pct = (prev_close - price) / prev_close * 100
                return pct if pct >= 0 else 0.0
            return None
        elif field == "profit_pct":
            # Unrealized profit percentage based on avg cost
            price = quote.get("price")
            avg_cost = strategy.sim_avg_cost if strategy.mode == "simulated" else self._get_live_avg_cost(strategy)
            if price and avg_cost and avg_cost > 0:
                return (price - avg_cost) / avg_cost * 100
            return None
        elif field == "loss_pct":
            # Unrealized loss percentage (positive value means loss)
            price = quote.get("price")
            avg_cost = strategy.sim_avg_cost if strategy.mode == "simulated" else self._get_live_avg_cost(strategy)
            if price and avg_cost and avg_cost > 0:
                return (avg_cost - price) / avg_cost * 100
            return None
        else:
            logger.warning("Unknown condition field: %s", field)
            return None

    def _get_live_avg_cost(self, strategy: TradeStrategy) -> float:
        """Get average cost for live mode from broker positions."""
        try:
            broker = self._get_broker()
            if broker and broker.is_connected():
                positions = broker.get_positions()
                for pos in positions:
                    if pos.stock_code == strategy.stock_code:
                        return pos.avg_cost
        except Exception as e:
            logger.warning("Failed to get live avg cost: %s", e)
        return strategy.sim_avg_cost  # fallback

    # ------------------------------------------------------------------
    #  Internal: order execution (simulated)
    # ------------------------------------------------------------------

    def _execute_order(
        self, db: Session, strategy: TradeStrategy,
        step: TradeStep, quote: dict
    ) -> dict:
        """
        Execute a buy/sell order.  For simulated mode, fills immediately
        at the current market price.
        """
        current_price = quote.get("price", 0)
        if current_price <= 0:
            step.status = "failed"
            self._log_event(db, strategy, step, "order_failed",
                            f"无效价格: {current_price}", current_price)
            return {"success": False, "reason": "invalid_price"}

        # Determine fill price
        if step.price_type == "limit" and step.limit_price:
            if step.action_type == "buy" and current_price > step.limit_price:
                return {"success": False, "reason": "price_above_limit"}
            if step.action_type == "sell" and current_price < step.limit_price:
                return {"success": False, "reason": "price_below_limit"}
            fill_price = step.limit_price
        else:
            fill_price = current_price

        quantity = step.quantity

        if strategy.mode == "simulated":
            result = self._execute_simulated(db, strategy, step, fill_price, quantity)
        else:
            # Live mode — placeholder for future broker integration
            result = self._execute_live(db, strategy, step, fill_price, quantity)

        return result

    def _execute_simulated(
        self, db: Session, strategy: TradeStrategy,
        step: TradeStep, fill_price: float, quantity: int
    ) -> dict:
        """Simulate a fill: update cash, holdings, avg cost."""
        cost = fill_price * quantity

        if step.action_type == "buy":
            if cost > strategy.sim_cash:
                step.status = "failed"
                self._log_event(db, strategy, step, "order_failed",
                                f"资金不足: 需要 {cost:.2f}, 可用 {strategy.sim_cash:.2f}",
                                fill_price)
                return {"success": False, "reason": "insufficient_cash"}

            # Update holdings
            total_cost = strategy.sim_avg_cost * strategy.sim_holdings + cost
            strategy.sim_holdings += quantity
            strategy.sim_avg_cost = total_cost / strategy.sim_holdings if strategy.sim_holdings > 0 else 0
            strategy.sim_cash -= cost

        elif step.action_type == "sell":
            if quantity > strategy.sim_holdings:
                step.status = "failed"
                self._log_event(db, strategy, step, "order_failed",
                                f"持仓不足: 需要 {quantity}, 持有 {strategy.sim_holdings}",
                                fill_price)
                return {"success": False, "reason": "insufficient_holdings"}

            # Update holdings
            strategy.sim_holdings -= quantity
            strategy.sim_cash += cost
            if strategy.sim_holdings == 0:
                strategy.sim_avg_cost = 0

        # Mark step as filled
        step.status = "filled"
        step.fill_price = fill_price
        step.fill_quantity = quantity
        step.filled_at = datetime.utcnow()

        action_label = "买入" if step.action_type == "buy" else "卖出"
        self._log_event(
            db, strategy, step, "order_filled",
            f"模拟{action_label} {quantity}股 @ {fill_price:.2f}, "
            f"现金: {strategy.sim_cash:.2f}, 持仓: {strategy.sim_holdings}股",
            fill_price,
        )

        return {
            "success": True,
            "fill_price": fill_price,
            "fill_quantity": quantity,
            "mode": "simulated",
        }

    def _execute_live(
        self, db: Session, strategy: TradeStrategy,
        step: TradeStep, fill_price: float, quantity: int
    ) -> dict:
        """
        Live order execution via broker adapter (平安证券 QMT).
        Submits the order to the real broker and records the result.
        """
        broker = self._get_broker()
        if not broker:
            step.status = "failed"
            self._log_event(db, strategy, step, "order_failed",
                            "实盘券商未配置。请在配置管理中设置券商账号和QMT路径。", fill_price)
            return {"success": False, "reason": "broker_not_configured"}

        if not broker.is_connected():
            try:
                broker.connect()
            except Exception as e:
                step.status = "failed"
                self._log_event(db, strategy, step, "order_failed",
                                f"连接券商失败: {e}", fill_price)
                return {"success": False, "reason": f"broker_connect_failed: {e}"}

        try:
            result = broker.submit_order(
                stock_code=strategy.stock_code,
                market=strategy.market,
                action=step.action_type,
                quantity=quantity,
                price_type=step.price_type,
                price=fill_price,
            )

            if result.success:
                # For live mode, use actual fill price from broker if available
                actual_price = result.fill_price if result.fill_price > 0 else fill_price
                actual_qty = result.fill_quantity if result.fill_quantity > 0 else quantity

                step.status = "filled"
                step.fill_price = actual_price
                step.fill_quantity = actual_qty
                step.filled_at = datetime.utcnow()

                action_label = "买入" if step.action_type == "buy" else "卖出"
                self._log_event(
                    db, strategy, step, "order_filled",
                    f"实盘{action_label} {actual_qty}股 @ {actual_price:.2f} "
                    f"(订单号: {result.order_id})",
                    actual_price,
                )
                return {
                    "success": True,
                    "fill_price": actual_price,
                    "fill_quantity": actual_qty,
                    "mode": "live",
                    "order_id": result.order_id,
                }
            else:
                step.status = "failed"
                self._log_event(db, strategy, step, "order_failed",
                                f"实盘下单失败: {result.message}", fill_price)
                return {"success": False, "reason": result.message}

        except Exception as e:
            logger.exception("Live order execution error")
            step.status = "failed"
            self._log_event(db, strategy, step, "order_failed",
                            f"实盘交易异常: {e}", fill_price)
            return {"success": False, "reason": str(e)}

    def _get_broker(self):
        """Get or create the broker instance (singleton per engine)."""
        if not hasattr(self, '_broker') or self._broker is None:
            from app.config import settings
            if not settings.BROKER_ACCOUNT:
                return None
            try:
                from app.services.brokers.pingan import PinganBroker
                self._broker = PinganBroker(
                    account=settings.BROKER_ACCOUNT,
                    xt_mini_path=settings.BROKER_QMT_PATH,
                )
            except Exception as e:
                logger.error("Failed to create broker: %s", e)
                return None
        return self._broker

    # ------------------------------------------------------------------
    #  Internal: step advancement
    # ------------------------------------------------------------------

    def _advance_to_next_step(
        self, db: Session, strategy: TradeStrategy, completed_step: TradeStep
    ):
        """After a step fills, advance to the next step or complete the strategy."""
        next_order = completed_step.step_order + 1
        next_step = None
        for step in strategy.steps:
            if step.step_order == next_order:
                next_step = step
                break

        if next_step:
            next_step.status = "watching"
            strategy.current_step_order = next_order
            # Reset conditions for the new step
            for cond in next_step.conditions:
                cond.is_met = False
                cond.met_at = None
            self._log_event(
                db, strategy, next_step, "step_advanced",
                f"推进到步骤{next_order}: {next_step.name or next_step.action_type}",
            )
        else:
            # No more steps
            strategy.status = "completed"
            self._log_event(db, strategy, None, "strategy_completed",
                            "所有步骤执行完成")

    # ------------------------------------------------------------------
    #  Internal: logging
    # ------------------------------------------------------------------

    def _log_event(
        self, db: Session, strategy: TradeStrategy,
        step: TradeStep | None, event_type: str,
        message: str = "", price_snapshot: float | None = None,
    ):
        """Write an execution log entry."""
        execution = TradeExecution(
            strategy_id=strategy.id,
            step_id=step.id if step else None,
            event_type=event_type,
            message=message,
            price_snapshot=price_snapshot,
        )
        db.add(execution)
        logger.info("[trade] strategy=%d event=%s: %s",
                    strategy.id, event_type, message)


# Singleton instance
trade_engine = TradeEngine()
