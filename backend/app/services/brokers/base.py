"""
Abstract broker interface — all broker adapters implement this.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class BrokerError(Exception):
    """Raised when a broker operation fails."""
    pass


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class OrderResult:
    """Result of a broker order submission or query."""
    success: bool
    order_id: str = ""
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float = 0.0
    fill_quantity: int = 0
    message: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Position:
    """A single position in the broker account."""
    stock_code: str
    stock_name: str = ""
    market: str = ""
    quantity: int = 0
    available_quantity: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    profit: float = 0.0
    profit_pct: float = 0.0


@dataclass
class AccountInfo:
    """Broker account summary."""
    total_asset: float = 0.0
    cash: float = 0.0
    market_value: float = 0.0
    frozen: float = 0.0
    positions: list = field(default_factory=list)  # list[Position]


class BaseBroker(ABC):
    """
    Abstract broker interface.
    
    Lifecycle:
        broker = SomeBroker(config)
        broker.connect()
        ...
        broker.disconnect()
    """

    @abstractmethod
    def connect(self) -> bool:
        """
        Connect and authenticate with the broker.
        Returns True on success, raises BrokerError on failure.
        """
        ...

    @abstractmethod
    def disconnect(self):
        """Disconnect from the broker gracefully."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if broker connection is alive."""
        ...

    @abstractmethod
    def submit_order(
        self,
        stock_code: str,
        market: str,
        action: str,       # "buy" or "sell"
        quantity: int,
        price_type: str,    # "market" or "limit"
        price: float = 0.0,
    ) -> OrderResult:
        """
        Submit a buy/sell order to the broker.
        
        Returns an OrderResult immediately (order may still be pending).
        """
        ...

    @abstractmethod
    def query_order(self, order_id: str) -> OrderResult:
        """Query the status of a previously submitted order."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. Returns True if cancellation was accepted."""
        ...

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Get account summary and positions."""
        ...

    @abstractmethod
    def get_positions(self) -> list:
        """Get current positions. Returns list[Position]."""
        ...
