"""
Abstract base class for stock data source clients.

All data source implementations (EastMoney, Sina, Tencent) inherit from
``DataSourceClient`` and normalize their responses to a unified format
so that downstream code (routers, tasks, DB models) never needs to know
which provider supplied the data.
"""

from abc import ABC, abstractmethod
from typing import Any
import math


def safe_int(value, default: int = 0) -> int:
    """Safely convert a value to int, handling NaN, None, empty strings."""
    if value is None or value == "" or value == "-":
        return default
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return int(f)
    except (ValueError, TypeError):
        return default


def safe_float(value, default: float = 0.0) -> float:
    """Safely convert a value to float, handling NaN, None, empty strings."""
    if value is None or value == "" or value == "-":
        return default
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


class DataSourceError(Exception):
    """Raised when a data source API call fails.

    All provider-specific error classes should inherit from this so that
    the aggregator can catch a single type.
    """


class DataSourceClient(ABC):
    """Async client interface for A-share market data providers.

    Every concrete implementation must produce the **same** dict schema
    from each method so that callers receive provider-agnostic data.

    Unified realtime quote dict keys::

        code, name, price, open, high, low, close, prev_close,
        volume (股), amount (元), change_pct (%), turnover_rate (%),
        timestamp (datetime)

    Unified daily kline dict keys::

        date (date), open, high, low, close,
        volume (股), amount (元), change_pct (%), turnover_rate (%)

    Unified search result dict keys::

        code, name, market ("SH" / "SZ")
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def close(self) -> None:
        """Release underlying HTTP resources."""

    # ------------------------------------------------------------------
    # Provider identity
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short lowercase identifier, e.g. ``"eastmoney"``, ``"sina"``."""

    # ------------------------------------------------------------------
    # Data methods
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_realtime_quote(self, code: str, market: str) -> dict[str, Any]:
        """Fetch latest realtime snapshot for a single stock.

        Parameters
        ----------
        code : str
            6-digit stock code, e.g. ``"600519"``.
        market : str
            ``"SH"`` or ``"SZ"``.

        Returns
        -------
        dict
            Unified quote dict (see class docstring).

        Raises
        ------
        DataSourceError
            On network / parsing / empty-data failures.
        """

    @abstractmethod
    async def get_daily_klines(
        self,
        code: str,
        market: str,
        limit: int = 120,
        start_date: str = "20200101",
        end_date: str = "20500101",
    ) -> list[dict[str, Any]]:
        """Fetch daily K-line (candlestick) history.

        Parameters
        ----------
        code : str
            6-digit stock code.
        market : str
            ``"SH"`` or ``"SZ"``.
        limit : int
            Maximum number of records to return.
        start_date / end_date : str
            ``YYYYMMDD`` date bounds.

        Returns
        -------
        list[dict]
            Unified kline dicts sorted by date ascending.

        Raises
        ------
        DataSourceError
            On network / parsing failures.
        """

    @abstractmethod
    async def search_stock(self, keyword: str) -> list[dict[str, str]]:
        """Search for stocks by code or name fragment.

        Parameters
        ----------
        keyword : str
            Partial code or Chinese name.

        Returns
        -------
        list[dict]
            Each dict: ``{"code": ..., "name": ..., "market": "SH"|"SZ"}``.

        Raises
        ------
        DataSourceError
            On network / parsing failures, or if the provider does not
            support search.
        """
