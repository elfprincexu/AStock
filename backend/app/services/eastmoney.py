"""
东方财富 (East Money) API client.

Provides async access to real-time quotes, historical K-line data,
and stock search via East Money's public push/search APIs.
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
_UT_TOKEN = "fa5fd1943c7b386f172d6893dbbd1177"
_SEARCH_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"

_REALTIME_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"

_REALTIME_FIELDS = (
    "f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f59,f60,"
    "f116,f117,f168,f170"
)

_KLINE_FIELDS1 = "f1,f2,f3,f4,f5,f6"
_KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

# Price fields that need decimal-precision adjustment in real-time quotes
_PRICE_FIELDS = ("f43", "f44", "f45", "f46", "f60")


class EastMoneyError(DataSourceError):
    """Raised when an East Money API call fails."""


class EastMoneyClient(DataSourceClient):
    """Async client for the 东方财富 public APIs.

    Usage::

        async with httpx.AsyncClient() as _:
            client = EastMoneyClient(timeout=10)
            quote = await client.get_realtime_quote("600519", "SH")
            await client.close()
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def __init__(self, timeout: int = 10) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Referer": "https://quote.eastmoney.com/",
            },
        )

    @property
    def source_name(self) -> str:
        return "eastmoney"

    async def close(self) -> None:
        """Shut down the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_secid(code: str, market: str) -> str:
        """Return the *secid* parameter expected by East Money.

        Returns ``"1.<code>"`` for SH, ``"0.<code>"`` for SZ and BJ.
        """
        market_upper = market.upper()
        if market_upper == "SH":
            return f"1.{code}"
        elif market_upper in ("SZ", "BJ"):
            return f"0.{code}"
        else:
            raise EastMoneyError(f"Unsupported market '{market}', expected 'SH', 'SZ' or 'BJ'")

    # ------------------------------------------------------------------
    # Real-time quote
    # ------------------------------------------------------------------
    async def get_realtime_quote(self, code: str, market: str) -> dict[str, Any]:
        """Fetch the latest real-time snapshot for a single stock.

        Parameters
        ----------
        code : str
            Stock code, e.g. ``"600519"``.
        market : str
            ``"SH"`` or ``"SZ"``.

        Returns
        -------
        dict
            Keys: ``price``, ``open``, ``high``, ``low``, ``close`` (=price),
            ``volume`` (shares), ``amount`` (元), ``change_pct``,
            ``turnover_rate``, ``prev_close``, ``name``, ``code``,
            ``timestamp``.

        Raises
        ------
        EastMoneyError
            On network / API failures.
        """
        secid = self._build_secid(code, market)
        params = {
            "secid": secid,
            "fields": _REALTIME_FIELDS,
            "ut": _UT_TOKEN,
        }

        try:
            resp = await self._client.get(_REALTIME_URL, params=params)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("East Money realtime API HTTP %s for %s", exc.response.status_code, secid)
            raise EastMoneyError(f"HTTP {exc.response.status_code} from realtime API") from exc
        except httpx.RequestError as exc:
            logger.error("East Money realtime API request failed for %s: %s", secid, exc)
            raise EastMoneyError(f"Request failed: {exc}") from exc
        except Exception as exc:
            logger.error("East Money realtime API unexpected error for %s: %s", secid, exc)
            raise EastMoneyError(f"Unexpected error: {exc}") from exc

        data = body.get("data")
        if not data:
            logger.warning("Empty data in realtime response for %s", secid)
            raise EastMoneyError(f"No data returned for secid={secid}")

        # Decimal precision – prices are raw integers scaled by 10^f59
        decimal_places = int(data.get("f59", 2))
        divisor = 10 ** decimal_places

        def _price(field: str) -> float:
            raw = data.get(field)
            if raw is None or raw == "-":
                return 0.0
            return round(int(raw) / divisor, decimal_places)

        price = _price("f43")
        volume_hands = data.get("f47", 0)
        # Volume from API is in 手 (lots of 100 shares)
        volume_shares = safe_int(volume_hands) * 100 if volume_hands and volume_hands != "-" else 0
        amount = float(data.get("f48", 0)) if data.get("f48") not in (None, "-") else 0.0
        change_pct_raw = data.get("f170")
        change_pct = round(int(change_pct_raw) / 100, 2) if change_pct_raw not in (None, "-") else 0.0
        turnover_raw = data.get("f168")
        turnover_rate = round(int(turnover_raw) / 100, 2) if turnover_raw not in (None, "-") else 0.0

        return {
            "code": str(data.get("f57", code)),
            "name": data.get("f58", ""),
            "price": price,
            "open": _price("f46"),
            "high": _price("f44"),
            "low": _price("f45"),
            "close": price,
            "prev_close": _price("f60"),
            "volume": volume_shares,
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
        """Fetch daily K-line (candlestick) history.

        Parameters
        ----------
        code : str
            Stock code.
        market : str
            ``"SH"`` or ``"SZ"``.
        limit : int
            Maximum number of records to return (most-recent first from
            the API perspective).
        start_date : str
            Start date in ``YYYYMMDD`` format.
        end_date : str
            End date in ``YYYYMMDD`` format.

        Returns
        -------
        list[dict]
            Each dict contains: ``date``, ``open``, ``high``, ``low``,
            ``close``, ``volume`` (shares), ``amount`` (元),
            ``change_pct``, ``turnover_rate``.
        """
        secid = self._build_secid(code, market)
        params = {
            "secid": secid,
            "fields1": _KLINE_FIELDS1,
            "fields2": _KLINE_FIELDS2,
            "klt": "101",       # daily
            "fqt": "1",         # 前复权 (forward-adjusted)
            "beg": start_date,
            "end": end_date,
            "lmt": str(limit),
            "ut": _UT_TOKEN,
        }

        try:
            resp = await self._client.get(_KLINE_URL, params=params)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("East Money kline API HTTP %s for %s", exc.response.status_code, secid)
            raise EastMoneyError(f"HTTP {exc.response.status_code} from kline API") from exc
        except httpx.RequestError as exc:
            logger.error("East Money kline API request failed for %s: %s", secid, exc)
            raise EastMoneyError(f"Request failed: {exc}") from exc
        except Exception as exc:
            logger.error("East Money kline API unexpected error for %s: %s", secid, exc)
            raise EastMoneyError(f"Unexpected error: {exc}") from exc

        data = body.get("data")
        if not data:
            logger.warning("Empty data in kline response for %s", secid)
            return []

        raw_klines: list[str] = data.get("klines", [])
        results: list[dict[str, Any]] = []

        # Each kline is a comma-separated string:
        # date, open, close, high, low, volume, amount,
        # amplitude, change_pct, change_amount, turnover_rate
        for line in raw_klines:
            parts = line.split(",")
            if len(parts) < 11:
                logger.debug("Skipping malformed kline line: %s", line)
                continue
            try:
                kline_date_str = parts[0]       # e.g. "2024-06-17"
                open_ = float(parts[1])
                close = float(parts[2])
                high = float(parts[3])
                low = float(parts[4])
                volume = safe_int(parts[5])       # shares (already in shares)
                amount = float(parts[6])
                # parts[7] = amplitude (振幅 %)
                change_pct = float(parts[8])     # 涨跌幅 %
                # parts[9] = change_amount (涨跌额)
                turnover_rate = float(parts[10]) # 换手率 %

                results.append({
                    "date": datetime.strptime(kline_date_str, "%Y-%m-%d").date(),
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": amount,
                    "change_pct": change_pct,
                    "turnover_rate": turnover_rate,
                })
            except (ValueError, IndexError) as exc:
                logger.debug("Failed to parse kline line '%s': %s", line, exc)
                continue

        logger.info("Fetched %d daily klines for %s", len(results), secid)
        return results

    # ------------------------------------------------------------------
    # Stock search / validation
    # ------------------------------------------------------------------
    async def search_stock(self, keyword: str) -> list[dict[str, str]]:
        """Search for stocks by code or name fragment.

        Parameters
        ----------
        keyword : str
            Partial stock code or Chinese name to search for.

        Returns
        -------
        list[dict]
            Each dict contains ``code``, ``name``, ``market``
            (``"SH"`` / ``"SZ"``).
        """
        params = {
            "input": keyword,
            "type": "14",
            "token": _SEARCH_TOKEN,
            "count": "5",
        }

        try:
            resp = await self._client.get(_SEARCH_URL, params=params)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("East Money search API HTTP %s for '%s'", exc.response.status_code, keyword)
            raise EastMoneyError(f"HTTP {exc.response.status_code} from search API") from exc
        except httpx.RequestError as exc:
            logger.error("East Money search API request failed for '%s': %s", keyword, exc)
            raise EastMoneyError(f"Request failed: {exc}") from exc
        except Exception as exc:
            logger.error("East Money search API unexpected error for '%s': %s", keyword, exc)
            raise EastMoneyError(f"Unexpected error: {exc}") from exc

        table = body.get("QuotationCodeTable") or {}
        items: list[dict[str, Any]] = table.get("Data") or []

        results: list[dict[str, str]] = []
        for item in items:
            mkt_num = str(item.get("MktNum", ""))
            code_val = item.get("Code", "")
            if mkt_num == "0":
                # BJ stocks (codes starting with 4/8/92) share MktNum 0 with SZ
                if code_val.startswith(("4", "8", "92")):
                    market_label = "BJ"
                else:
                    market_label = "SZ"
            elif mkt_num == "1":
                market_label = "SH"
            elif mkt_num == "2":
                market_label = "BJ"
            else:
                # Skip non-A-share entries (e.g. HK, US)
                continue

            results.append({
                "code": code_val,
                "name": item.get("Name", ""),
                "market": market_label,
            })

        logger.info("Search '%s' returned %d results", keyword, len(results))
        return results
