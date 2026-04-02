"""
新浪财经 (Sina Finance) API client.

Provides async access to real-time quotes and historical K-line data
via Sina's public HTTP endpoints.  Search is **not** supported by Sina;
the aggregator delegates search to EastMoney.

Encoding note: Sina responds in GB18030 for realtime quotes.
"""

import logging
import re
from datetime import datetime, date
from typing import Any

import httpx

from app.services.base import DataSourceClient, DataSourceError, safe_int, safe_float

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_REALTIME_URL = "https://hq.sinajs.cn/list={symbol}"
_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/CN_MarketData.getKLineData"
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn",
}

# Regex to extract the JS variable content:
#   var hq_str_sh600519="field0,field1,...";
_REALTIME_RE = re.compile(r'var hq_str_\w+="(.*)";')


class SinaError(DataSourceError):
    """Raised when a Sina Finance API call fails."""


class SinaClient(DataSourceClient):
    """Async client for 新浪财经 public APIs.

    Unified output format matches :class:`DataSourceClient` specification.
    """

    def __init__(self, timeout: int = 10) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers=_HEADERS,
        )

    @property
    def source_name(self) -> str:
        return "sina"

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _symbol(code: str, market: str) -> str:
        """Build Sina symbol like ``sh600519`` or ``sz000001``."""
        return f"{market.lower()}{code}"

    # ------------------------------------------------------------------
    # Real-time quote
    # ------------------------------------------------------------------
    async def get_realtime_quote(self, code: str, market: str) -> dict[str, Any]:
        """Fetch latest realtime snapshot from ``hq.sinajs.cn``.

        Sina returns a JS variable assignment in GB18030 encoding::

            var hq_str_sh600519="贵州茅台,open,prev_close,price,...";

        Fields (comma-separated, 0-indexed):
            [0] name, [1] open, [2] prev_close, [3] price,
            [4] high, [5] low, [6] bid, [7] ask,
            [8] volume (股), [9] amount (元),
            [30] date, [31] time
        """
        symbol = self._symbol(code, market)
        url = _REALTIME_URL.format(symbol=symbol)

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            # Sina sends GB18030; httpx may not auto-detect it
            text = resp.content.decode("gb18030", errors="replace")
        except httpx.HTTPStatusError as exc:
            raise SinaError(f"HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise SinaError(f"Request failed: {exc}") from exc

        m = _REALTIME_RE.search(text)
        if not m or not m.group(1):
            raise SinaError(f"Empty or unparseable response for {symbol}")

        fields = m.group(1).split(",")
        if len(fields) < 32:
            raise SinaError(
                f"Expected >=32 fields, got {len(fields)} for {symbol}"
            )

        try:
            name = fields[0]
            price = float(fields[3])
            open_ = float(fields[1])
            prev_close = float(fields[2])
            high = float(fields[4])
            low = float(fields[5])
            volume = safe_int(fields[8])      # already in 股
            amount = safe_float(fields[9])
        except (ValueError, IndexError) as exc:
            raise SinaError(f"Failed to parse realtime fields: {exc}") from exc

        if price <= 0:
            raise SinaError(f"Invalid price {price} for {symbol}")

        change_pct = (
            round((price - prev_close) / prev_close * 100, 2)
            if prev_close > 0
            else 0.0
        )

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
            "turnover_rate": 0.0,   # not available from Sina
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
        """Fetch daily K-line data from Sina.

        URL returns a JSON array::

            [{"day":"2024-06-14","open":"1523.000","high":"1535.000",
              "low":"1518.000","close":"1530.500","volume":"3031859"}, ...]

        Volume is in 股 (shares).  ``amount``, ``change_pct``, and
        ``turnover_rate`` are **not** available from this endpoint.
        """
        symbol = self._symbol(code, market)
        params = {
            "symbol": symbol,
            "scale": "240",      # 240-minute = daily
            "ma": "no",
            "datalen": str(limit),
        }

        try:
            resp = await self._client.get(_KLINE_URL, params=params)
            resp.raise_for_status()
            raw_list = resp.json()
        except httpx.HTTPStatusError as exc:
            raise SinaError(f"HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise SinaError(f"Request failed: {exc}") from exc
        except Exception as exc:
            raise SinaError(f"Failed to parse kline JSON: {exc}") from exc

        if not isinstance(raw_list, list):
            raise SinaError(f"Expected list, got {type(raw_list)}")

        results: list[dict[str, Any]] = []
        prev_close: float | None = None

        for item in raw_list:
            try:
                kline_date = datetime.strptime(item["day"], "%Y-%m-%d").date()
                open_ = float(item["open"])
                high = float(item["high"])
                low = float(item["low"])
                close = float(item["close"])
                volume = safe_int(item["volume"])

                # Calculate change_pct from consecutive closes
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
            except (KeyError, ValueError) as exc:
                logger.debug("Skipping malformed Sina kline: %s", exc)
                continue

        logger.info(
            "[sina] Fetched %d daily klines for %s", len(results), symbol
        )
        return results

    # ------------------------------------------------------------------
    # Minute-level K-lines (intraday)
    # ------------------------------------------------------------------
    async def get_minute_klines(
        self,
        code: str,
        market: str,
        scale: int = 5,
        limit: int = 240,
    ) -> list[dict[str, Any]]:
        """Fetch minute-level K-line data from Sina.

        Same endpoint as daily, but with a different ``scale`` parameter.
        Supported scales: 1, 5, 15, 30, 60 (minutes).

        Returns intraday bars with timestamps like ``"2024-06-14 09:35:00"``.
        """
        if scale not in (5, 15, 30, 60):
            raise SinaError(f"Unsupported minute scale {scale}, must be 5/15/30/60")

        symbol = self._symbol(code, market)
        params = {
            "symbol": symbol,
            "scale": str(scale),
            "ma": "no",
            "datalen": str(limit),
        }

        try:
            resp = await self._client.get(_KLINE_URL, params=params)
            resp.raise_for_status()
            raw_list = resp.json()
        except httpx.HTTPStatusError as exc:
            raise SinaError(f"HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise SinaError(f"Request failed: {exc}") from exc
        except Exception as exc:
            raise SinaError(f"Failed to parse minute kline JSON: {exc}") from exc

        if not isinstance(raw_list, list):
            raise SinaError(f"Expected list, got {type(raw_list)}")

        results: list[dict[str, Any]] = []
        prev_close: float | None = None

        for item in raw_list:
            try:
                ts_str = item["day"]  # "2024-06-14 09:35:00"
                open_ = float(item["open"])
                high = float(item["high"])
                low = float(item["low"])
                close = float(item["close"])
                volume = safe_int(item["volume"])

                change_pct = (
                    round((close - prev_close) / prev_close * 100, 2)
                    if prev_close and prev_close > 0
                    else 0.0
                )
                prev_close = close

                results.append({
                    "time": ts_str,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "change_pct": change_pct,
                })
            except (KeyError, ValueError) as exc:
                logger.debug("Skipping malformed Sina minute kline: %s", exc)
                continue

        logger.info(
            "[sina] Fetched %d %d-min klines for %s",
            len(results), scale, symbol,
        )
        return results

    # ------------------------------------------------------------------
    # Search (not supported)
    # ------------------------------------------------------------------
    async def search_stock(self, keyword: str) -> list[dict[str, str]]:
        """Sina does not provide a stock search API."""
        raise SinaError("Sina does not support stock search")
