"""
Broker adapter layer for live trading.

Provides an abstract interface and concrete implementations for
connecting to securities brokers (e.g. 平安证券 via xtquant/QMT).
"""

from app.services.brokers.base import BaseBroker, BrokerError, OrderResult
from app.services.brokers.pingan import PinganBroker

__all__ = ["BaseBroker", "BrokerError", "OrderResult", "PinganBroker"]
