"""
BaoStock data source client.

Uses the ``baostock`` library (free, no registration) to provide
historical K-line data and basic quote information.

BaoStock requires a login/logout lifecycle and is synchronous,
so we wrap calls with ``asyncio.to_thread``.
"""

import asyncio
import logging
from datetime import datetime, date
from typing import Any

from app.services.base import DataSourceClient, DataSourceError, safe_int, safe_float

logger = logging.getLogger(__name__)


class BaoStockError(DataSourceError):
    """Raised when a BaoStock API call fails."""


def _code_to_baostock(code: str, market: str) -> str:
    """Convert to BaoStock format, e.g. 'sh.600519' or 'sz.000001'."""
    return f"{market.lower()}.{code}"


class BaoStockClient(DataSourceClient):
    """Async wrapper around the ``baostock`` library."""

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    @property
    def source_name(self) -> str:
        return "baostock"

    async def close(self) -> None:
        pass  # login/logout handled per-call

    # ------------------------------------------------------------------
    # Realtime quote — BaoStock doesn't support true realtime;
    # we use the latest day's kline as a rough proxy.
    # ------------------------------------------------------------------
    async def get_realtime_quote(self, code: str, market: str) -> dict[str, Any]:
        raise BaoStockError("BaoStock does not support realtime quotes")

    # ------------------------------------------------------------------
    # Daily klines
    # ------------------------------------------------------------------
    async def get_daily_klines(
        self,
        code: str,
        market: str,
        limit: int = 120,
        start_date: str = "20200101",
        end_date: str = "20500101",
    ) -> list[dict[str, Any]]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._daily_klines_sync, code, market, limit, start_date, end_date
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise BaoStockError(f"baostock klines timed out after {self._timeout}s")
        except BaoStockError:
            raise
        except Exception as exc:
            raise BaoStockError(f"baostock klines failed: {exc}") from exc

    @staticmethod
    def _daily_klines_sync(
        code: str, market: str, limit: int,
        start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        import baostock as bs

        bs_code = _code_to_baostock(code, market)
        sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        if end_date == "20500101":
            ed = datetime.now().strftime("%Y-%m-%d")
        else:
            ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"

        lg = bs.login()
        if lg.error_code != "0":
            raise BaoStockError(f"baostock login failed: {lg.error_msg}")

        try:
            rs = bs.query_history_k_data_plus(
                code=bs_code,
                fields="date,open,high,low,close,volume,amount,pctChg,turn",
                start_date=sd,
                end_date=ed,
                frequency="d",
                adjustflag="2",  # forward-adjusted (前复权)
            )
            if rs.error_code != "0":
                raise BaoStockError(
                    f"baostock query failed for {bs_code}: {rs.error_msg}"
                )

            results: list[dict[str, Any]] = []
            while rs.next():
                row = rs.get_row_data()
                try:
                    d = datetime.strptime(row[0], "%Y-%m-%d").date()
                    open_ = float(row[1]) if row[1] else 0.0
                    high = float(row[2]) if row[2] else 0.0
                    low = float(row[3]) if row[3] else 0.0
                    close = float(row[4]) if row[4] else 0.0
                    volume = safe_int(row[5])
                    amount = float(row[6]) if row[6] else 0.0
                    change_pct = float(row[7]) if row[7] else 0.0
                    turnover_rate = float(row[8]) if row[8] else 0.0

                    if close <= 0:
                        continue

                    results.append({
                        "date": d,
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
                    logger.debug("[baostock] Skip bad row: %s", exc)
                    continue

            if limit and len(results) > limit:
                results = results[-limit:]

            logger.info("[baostock] Fetched %d klines for %s", len(results), bs_code)
            return results

        finally:
            bs.logout()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    async def search_stock(self, keyword: str) -> list[dict[str, str]]:
        raise BaoStockError("BaoStock does not support stock search")
