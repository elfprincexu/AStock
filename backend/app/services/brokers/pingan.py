"""
平安证券 (Ping An Securities) broker adapter via xtquant / QMT.

xtquant is the Python SDK for 迅投QMT, used by 平安证券 and other
Chinese brokers for programmatic trading.

Prerequisites:
    1. Install QMT client from 平安证券 (download from their website)
    2. pip install xtquant  (or copy xtquant from QMT installation)
    3. Start the QMT mini client (MiniQMT mode) before running
    4. Configure account credentials in AStock config

Usage:
    broker = PinganBroker(
        account="your_account_id",
        xt_mini_path="C:/国金QMT/bin.x64",  # Path to QMT mini
    )
    broker.connect()
    result = broker.submit_order("000001", "SZ", "buy", 100, "limit", 10.50)
"""

import logging
import time
import threading
from typing import Optional

from app.services.brokers.base import (
    BaseBroker, BrokerError, OrderResult, OrderStatus,
    AccountInfo, Position,
)

logger = logging.getLogger(__name__)

# xtquant is only available when QMT client is installed
_xt_available = False
try:
    from xtquant import xttype, xtdata
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    _xt_available = True
except ImportError:
    XtQuantTrader = None
    XtQuantTraderCallback = object
    logger.info("xtquant not installed — live trading unavailable. "
                "Install QMT client and xtquant to enable.")


def _to_xt_code(stock_code: str, market: str) -> str:
    """Convert AStock code+market to xtquant format (e.g. '000001.SZ')."""
    m = market.upper()
    if m == "SH":
        return f"{stock_code}.SH"
    elif m == "SZ":
        return f"{stock_code}.SZ"
    elif m == "BJ":
        return f"{stock_code}.BJ"
    return f"{stock_code}.{m}"


def _from_xt_order_status(xt_status: int) -> OrderStatus:
    """Map xtquant order status code to our OrderStatus enum."""
    # xtquant status codes:
    # xttype.ORDER_UNREPORTED = 0
    # xttype.ORDER_WAIT_REPORTING = 1
    # xttype.ORDER_REPORTED = 2
    # xttype.ORDER_REPORTED_CANCEL = 3
    # xttype.ORDER_PARTSUCC_CANCEL = 4
    # xttype.ORDER_PART_CANCEL = 5
    # xttype.ORDER_CANCELED = 6
    # xttype.ORDER_PART_SUCC = 7
    # xttype.ORDER_SUCCEEDED = 8
    # xttype.ORDER_JUNK = 9
    # xttype.ORDER_UNKNOWN = 255
    if not _xt_available:
        return OrderStatus.PENDING
    mapping = {
        0: OrderStatus.PENDING,       # UNREPORTED
        1: OrderStatus.PENDING,       # WAIT_REPORTING
        2: OrderStatus.SUBMITTED,     # REPORTED
        3: OrderStatus.SUBMITTED,     # REPORTED_CANCEL
        4: OrderStatus.PARTIAL_FILLED,  # PARTSUCC_CANCEL
        5: OrderStatus.CANCELLED,     # PART_CANCEL
        6: OrderStatus.CANCELLED,     # CANCELED
        7: OrderStatus.PARTIAL_FILLED,  # PART_SUCC
        8: OrderStatus.FILLED,        # SUCCEEDED
        9: OrderStatus.REJECTED,      # JUNK
        255: OrderStatus.PENDING,     # UNKNOWN
    }
    return mapping.get(xt_status, OrderStatus.PENDING)


class _OrderCallback(XtQuantTraderCallback):
    """Callback handler for xtquant trader events."""

    def __init__(self):
        super().__init__()
        self._order_events = {}   # order_id -> latest event
        self._deal_events = {}    # order_id -> list of deals
        self._lock = threading.Lock()

    def on_disconnected(self):
        logger.warning("[PinganBroker] Disconnected from QMT trader")

    def on_stock_order(self, order):
        """Called when order status changes."""
        with self._lock:
            self._order_events[str(order.order_id)] = order
        logger.info("[PinganBroker] Order update: id=%s status=%s stock=%s",
                    order.order_id, order.order_status, order.stock_code)

    def on_stock_trade(self, trade):
        """Called when a trade (fill) occurs."""
        with self._lock:
            oid = str(trade.order_id)
            if oid not in self._deal_events:
                self._deal_events[oid] = []
            self._deal_events[oid].append(trade)
        logger.info("[PinganBroker] Trade fill: id=%s stock=%s price=%.2f qty=%d",
                    trade.order_id, trade.stock_code, trade.traded_price,
                    trade.traded_quantity)

    def on_order_error(self, order_error):
        """Called when an order encounters an error."""
        logger.error("[PinganBroker] Order error: id=%s code=%d msg=%s",
                     order_error.order_id, order_error.error_id,
                     order_error.error_msg)

    def on_order_stock_async_response(self, response):
        """Called with async order response."""
        logger.info("[PinganBroker] Async response: order_id=%s, seq=%s",
                    response.order_id, response.seq)

    def get_order_event(self, order_id: str):
        with self._lock:
            return self._order_events.get(order_id)

    def get_deal_events(self, order_id: str) -> list:
        with self._lock:
            return list(self._deal_events.get(order_id, []))


class PinganBroker(BaseBroker):
    """
    平安证券 live broker via xtquant (QMT/MiniQMT).
    
    Parameters
    ----------
    account : str
        Trading account ID (e.g. your stock account number at 平安证券).
    xt_mini_path : str
        Path to the QMT Mini client directory.
        E.g. "C:/国金QMT/bin.x64" or "D:/平安证券/QMT/bin.x64"
    session_id : int
        Session ID for xtquant connection (default: auto-generated).
    """

    def __init__(
        self,
        account: str,
        xt_mini_path: str = "",
        session_id: int = 0,
    ):
        self._account_id = account
        self._xt_mini_path = xt_mini_path
        self._session_id = session_id or int(time.time())
        self._trader: Optional[XtQuantTrader] = None
        self._callback = None
        self._xt_account = None
        self._connected = False

    def connect(self) -> bool:
        if not _xt_available:
            raise BrokerError(
                "xtquant 未安装。请先安装平安证券QMT客户端并安装xtquant: "
                "pip install xtquant 或从QMT安装目录复制xtquant包。"
            )

        if not self._xt_mini_path:
            raise BrokerError(
                "未配置QMT路径 (BROKER_QMT_PATH)。"
                "请在配置管理中设置QMT Mini客户端的路径。"
            )

        if not self._account_id:
            raise BrokerError(
                "未配置交易账号 (BROKER_ACCOUNT)。"
                "请在配置管理中设置您的平安证券资金账号。"
            )

        try:
            self._callback = _OrderCallback()
            self._trader = XtQuantTrader(
                self._xt_mini_path, self._session_id
            )
            self._trader.register_callback(self._callback)
            self._trader.start()

            # Create stock account
            from xtquant.xttype import StockAccount
            self._xt_account = StockAccount(self._account_id)

            # Connect and subscribe
            connect_result = self._trader.connect()
            if connect_result != 0:
                raise BrokerError(f"连接QMT失败, 错误码: {connect_result}。"
                                  "请确认QMT Mini客户端已启动。")

            subscribe_result = self._trader.subscribe(self._xt_account)
            if subscribe_result != 0:
                raise BrokerError(f"订阅账户失败, 错误码: {subscribe_result}。"
                                  "请确认账号 {self._account_id} 已在QMT中登录。")

            self._connected = True
            logger.info("[PinganBroker] Connected: account=%s", self._account_id)
            return True

        except BrokerError:
            raise
        except Exception as e:
            raise BrokerError(f"连接平安证券QMT失败: {e}")

    def disconnect(self):
        if self._trader:
            try:
                self._trader.stop()
            except Exception as e:
                logger.warning("[PinganBroker] Error stopping trader: %s", e)
            self._trader = None
        self._connected = False
        logger.info("[PinganBroker] Disconnected")

    def is_connected(self) -> bool:
        return self._connected and self._trader is not None

    def submit_order(
        self,
        stock_code: str,
        market: str,
        action: str,
        quantity: int,
        price_type: str,
        price: float = 0.0,
    ) -> OrderResult:
        if not self.is_connected():
            raise BrokerError("未连接到券商, 请先连接")

        xt_code = _to_xt_code(stock_code, market)

        # Map action to xtquant order type
        if action == "buy":
            xt_direction = xttype.STOCK_BUY
        elif action == "sell":
            xt_direction = xttype.STOCK_SELL
        else:
            raise BrokerError(f"未知操作类型: {action}")

        # Map price type
        if price_type == "limit":
            xt_price_type = xttype.FIX_PRICE
            order_price = price
        else:
            # Market order — use latest price or 5-level optimal
            xt_price_type = xttype.LATEST_PRICE
            order_price = 0

        logger.info("[PinganBroker] Submitting order: %s %s %d shares @ %s %.2f",
                    action, xt_code, quantity, price_type, order_price)

        try:
            order_id = self._trader.order_stock(
                self._xt_account,
                xt_code,
                xt_direction,
                quantity,
                xt_price_type,
                order_price,
                strategy_name="AStock",
                order_remark="AStock策略交易",
            )

            if order_id == -1:
                return OrderResult(
                    success=False,
                    status=OrderStatus.REJECTED,
                    message="下单被拒绝 (order_id=-1)",
                )

            # Wait briefly for order confirmation
            time.sleep(0.3)
            
            # Check for immediate callback
            order_event = self._callback.get_order_event(str(order_id))
            if order_event:
                status = _from_xt_order_status(order_event.order_status)
                return OrderResult(
                    success=status not in (OrderStatus.REJECTED, OrderStatus.FAILED),
                    order_id=str(order_id),
                    status=status,
                    fill_price=order_event.traded_price if hasattr(order_event, 'traded_price') else 0,
                    fill_quantity=order_event.traded_quantity if hasattr(order_event, 'traded_quantity') else 0,
                    message=f"订单已提交 (id={order_id})",
                )

            return OrderResult(
                success=True,
                order_id=str(order_id),
                status=OrderStatus.SUBMITTED,
                message=f"订单已提交, 等待确认 (id={order_id})",
            )

        except Exception as e:
            logger.exception("[PinganBroker] Order submission failed")
            return OrderResult(
                success=False,
                status=OrderStatus.FAILED,
                message=f"下单失败: {e}",
            )

    def query_order(self, order_id: str) -> OrderResult:
        if not self.is_connected():
            raise BrokerError("未连接到券商")

        # First check callback cache
        order_event = self._callback.get_order_event(order_id)
        deal_events = self._callback.get_deal_events(order_id)

        if order_event:
            status = _from_xt_order_status(order_event.order_status)
            total_filled = sum(d.traded_quantity for d in deal_events) if deal_events else 0
            avg_price = 0
            if deal_events:
                total_amount = sum(d.traded_price * d.traded_quantity for d in deal_events)
                avg_price = total_amount / total_filled if total_filled > 0 else 0

            return OrderResult(
                success=status in (OrderStatus.FILLED, OrderStatus.SUBMITTED,
                                   OrderStatus.PARTIAL_FILLED),
                order_id=order_id,
                status=status,
                fill_price=avg_price,
                fill_quantity=total_filled,
                message=f"状态: {status.value}",
            )

        return OrderResult(
            success=False,
            order_id=order_id,
            status=OrderStatus.PENDING,
            message="未找到订单状态",
        )

    def cancel_order(self, order_id: str) -> bool:
        if not self.is_connected():
            raise BrokerError("未连接到券商")
        try:
            result = self._trader.cancel_order_stock(
                self._xt_account, int(order_id)
            )
            return result == 0
        except Exception as e:
            logger.error("[PinganBroker] Cancel order failed: %s", e)
            return False

    def get_account(self) -> AccountInfo:
        if not self.is_connected():
            raise BrokerError("未连接到券商")

        try:
            asset = self._trader.query_stock_asset(self._xt_account)
            positions = self.get_positions()

            return AccountInfo(
                total_asset=asset.total_asset if asset else 0,
                cash=asset.cash if asset else 0,
                market_value=asset.market_value if asset else 0,
                frozen=asset.frozen_cash if asset else 0,
                positions=positions,
            )
        except Exception as e:
            raise BrokerError(f"查询账户失败: {e}")

    def get_positions(self) -> list:
        if not self.is_connected():
            raise BrokerError("未连接到券商")

        try:
            xt_positions = self._trader.query_stock_positions(self._xt_account)
            result = []
            for p in (xt_positions or []):
                if p.volume <= 0:
                    continue
                # Parse code: "000001.SZ" -> ("000001", "SZ")
                parts = p.stock_code.split(".")
                code = parts[0] if parts else p.stock_code
                mkt = parts[1] if len(parts) > 1 else ""

                result.append(Position(
                    stock_code=code,
                    stock_name=getattr(p, 'stock_name', ''),
                    market=mkt,
                    quantity=p.volume,
                    available_quantity=p.can_use_volume,
                    avg_cost=p.avg_price,
                    current_price=getattr(p, 'market_price', 0),
                    market_value=p.market_value,
                    profit=getattr(p, 'profit', 0),
                ))
            return result
        except Exception as e:
            raise BrokerError(f"查询持仓失败: {e}")
