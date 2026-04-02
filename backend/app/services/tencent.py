"""
腾讯财经 (Tencent Finance) API client.

Provides async access to real-time quotes and historical K-line data
via Tencent's public HTTP endpoints.  Search is **not** supported;
the aggregator delegates search to EastMoney.

Encoding note: Tencent responds in GBK for realtime quotes.
"""

import logging
from datetime import datetime, date
from typing import Any

import httpx

from app.services.base import DataSourceClient, DataSourceError, safe_int, safe_float

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_REALTIME_URL = "http://qt.gtimg.cn/q={symbol}"
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}


class TencentError(DataSourceError):
    """Raised when a Tencent Finance API call fails."""


class TencentClient(DataSourceClient):
    """Async client for 腾讯财经 public APIs.

    Unified output format matches :class:`DataSourceClient` specification.
    """

    def __init__(self, timeout: int = 10) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers=_HEADERS,
        )

    @property
    def source_name(self) -> str:
        return "tencent"

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _symbol(code: str, market: str) -> str:
        """Build Tencent symbol like ``sh600519`` or ``sz000001``."""
        return f"{market.lower()}{code}"

    # ------------------------------------------------------------------
    # Real-time quote
    # ------------------------------------------------------------------
    async def get_realtime_quote(self, code: str, market: str) -> dict[str, Any]:
        """Fetch latest realtime snapshot from ``qt.gtimg.cn``.

        Response is a GBK-encoded JS variable with ``~``-separated fields::

            v_sh600519="1~贵州茅台~600519~1444.42~1452.87~..."

        Key field positions (0-indexed, split by ``~``):
            [1]  name
            [2]  code
            [3]  current price
            [4]  prev_close
            [5]  open
            [6]  volume (手)
            [30] datetime (YYYYMMDDHHmmss)
            [31] change_amount
            [32] change_pct (%)
            [33] high
            [34] low
            [37] amount (万元)
            [38] turnover_rate (%)
        """
        symbol = self._symbol(code, market)
        url = _REALTIME_URL.format(symbol=symbol)

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="replace")
        except httpx.HTTPStatusError as exc:
            raise TencentError(f"HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise TencentError(f"Request failed: {exc}") from exc

        # Extract content between quotes
        try:
            start = text.index('"') + 1
            end = text.rindex('"')
            content = text[start:end]
        except ValueError:
            raise TencentError(f"Cannot parse response for {symbol}")

        fields = content.split("~")
        if len(fields) < 50:
            raise TencentError(
                f"Expected >=50 fields, got {len(fields)} for {symbol}"
            )

        try:
            name = fields[1]
            price = float(fields[3])
            prev_close = float(fields[4])
            open_ = float(fields[5])
            volume_lots = safe_int(fields[6])       # 手 (lots)
            volume = volume_lots * 100             # 股 (shares)
            change_pct = float(fields[32])
            high = float(fields[33])
            low = float(fields[34])
            amount_wan = float(fields[37])         # 万元
            amount = amount_wan * 10000            # 元
            turnover_raw = fields[38]
            turnover_rate = float(turnover_raw) if turnover_raw else 0.0
        except (ValueError, IndexError) as exc:
            raise TencentError(f"Failed to parse realtime fields: {exc}") from exc

        if price <= 0:
            raise TencentError(f"Invalid price {price} for {symbol}")

        return {
            "code": code,
            "name": name,
            "price": price,
            "open": open_,
            "high": high,
            "low": low,
            "close": price,
            "prev_close": prev_close,
            "volume": volume,
            "amount": amount,
            "change_pct": change_pct,
            "turnover_rate": turnover_rate,
            "timestamp": datetime.now(),
        }

    # ------------------------------------------------------------------
    # Historical K-lines
    # ------------------------------------------------------------------
    async def get_daily_klines(
        self,
        code: str,
        market: str,
        limit: int = 120,
        start_date: str = "20200101",
        end_date: str = "20500101",
    ) -> list[dict[str, Any]]:
        """Fetch daily K-line data from Tencent (前复权).

        URL returns JSON with structure::

            {"code": 0, "data": {"sh600519": {"qfqday": [
                ["2024-06-14", "1523.00", "1530.50", "1535.00", "1518.00", "25631.00"],
                ...
            ]}}}

        Array positions: [date, open, close, high, low, volume].
        Volume is in 手 (lots of 100 shares).
        ``amount``, ``change_pct``, and ``turnover_rate`` are **not**
        available.
        """
        symbol = self._symbol(code, market)
        params = {
            "param": f"{symbol},day,,,{limit},qfq",
        }

        try:
            resp = await self._client.get(_KLINE_URL, params=params)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            raise TencentError(f"HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise TencentError(f"Request failed: {exc}") from exc
        except Exception as exc:
            raise TencentError(f"Failed to parse kline JSON: {exc}") from exc

        # Navigate nested structure
        data = body.get("data", {})
        stock_data = data.get(symbol, {})
        raw_klines = stock_data.get("qfqday") or stock_data.get("day") or []

        if not raw_klines:
            logger.warning("[tencent] No kline data for %s", symbol)
            return []

        results: list[dict[str, Any]] = []
        prev_close: float | None = None

        for row in raw_klines:
            if len(row) < 6:
                logger.debug("Skipping short kline row: %s", row)
                continue
            try:
                kline_date = datetime.strptime(row[0], "%Y-%m-%d").date()
                open_ = float(row[1])
                close = float(row[2])
                high = float(row[3])
                low = float(row[4])
                volume_lots = safe_int(row[5])
                volume = volume_lots * 100  # 手 → 股

                change_pct = (
                    round((close - prev_close) / prev_close * 100, 2)
                    if prev_close and prev_close > 0
                    else 0.0
                )
                prev_close = close

                results.append({
                    "date": kline_date,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": 0.0,          # not available
                    "change_pct": change_pct,
                    "turnover_rate": None,   # not available
                })
            except (ValueError, IndexError) as exc:
                logger.debug("Skipping malformed Tencent kline row: %s", exc)
                continue

        logger.info(
            "[tencent] Fetched %d daily klines for %s", len(results), symbol
        )
        return results

    # ------------------------------------------------------------------
    # Search (not supported)
    # ------------------------------------------------------------------
    async def search_stock(self, keyword: str) -> list[dict[str, str]]:
        """Tencent does not provide a stock search API."""
        raise TencentError("Tencent does not support stock search")
