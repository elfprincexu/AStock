"""
Tushare data source client.

Uses the ``tushare`` pro API (requires a free registration token from
https://tushare.pro) to provide historical K-line data.

If no token is configured (``TUSHARE_TOKEN`` env var), the client will
skip silently when the aggregator tries it.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from app.services.base import DataSourceClient, DataSourceError, safe_int, safe_float

logger = logging.getLogger(__name__)


class TushareError(DataSourceError):
    """Raised when a Tushare API call fails."""


class TushareClient(DataSourceClient):
    """Async wrapper around the ``tushare`` pro API."""

    def __init__(self, timeout: int = 30, token: str = "") -> None:
        self._timeout = timeout
        self._token = token

    @property
    def source_name(self) -> str:
        return "tushare"

    async def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Realtime quote
    # ------------------------------------------------------------------
    async def get_realtime_quote(self, code: str, market: str) -> dict[str, Any]:
        raise TushareError("Tushare does not support realtime quotes in free tier")

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
        if not self._token:
            raise TushareError("No TUSHARE_TOKEN configured, skipping")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._daily_klines_sync, code, market, limit, start_date, end_date
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise TushareError(f"tushare klines timed out after {self._timeout}s")
        except TushareError:
            raise
        except Exception as exc:
            raise TushareError(f"tushare klines failed: {exc}") from exc

    def _daily_klines_sync(
        self,
        code: str, market: str, limit: int,
        start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        import tushare as ts

        # Tushare ts_code format: "600519.SH" or "000001.SZ"
        ts_code = f"{code}.{market.upper()}"
        ed = end_date if end_date != "20500101" else datetime.now().strftime("%Y%m%d")

        try:
            pro = ts.pro_api(self._token)
            df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=ed)
        except Exception as exc:
            raise TushareError(f"tushare daily query failed: {exc}") from exc

        if df is None or df.empty:
            raise TushareError(f"No tushare data for {ts_code}")

        # Try to get turnover rate from daily_basic
        turn_map: dict[str, float] = {}
        try:
            df_basic = pro.daily_basic(
                ts_code=ts_code, start_date=start_date, end_date=ed,
                fields="trade_date,turnover_rate",
            )
            if df_basic is not None and not df_basic.empty:
                for _, rb in df_basic.iterrows():
                    turn_map[str(rb["trade_date"])] = float(rb["turnover_rate"] or 0)
        except Exception:
            pass  # turnover_rate is nice-to-have

        results: list[dict[str, Any]] = []
        for _, r in df.iterrows():
            try:
                trade_date = str(r["trade_date"])
                d = datetime.strptime(trade_date, "%Y%m%d").date()
                results.append({
                    "date": d,
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": safe_int(r.get("vol", 0)) * 100,  # tushare vol is in 手
                    "amount": float(r.get("amount", 0) or 0) * 1000,   # tushare amount is in 千元
                    "change_pct": float(r.get("pct_chg", 0) or 0),
                    "turnover_rate": turn_map.get(trade_date, 0.0),
                })
            except (ValueError, KeyError) as exc:
                logger.debug("[tushare] Skip bad row: %s", exc)
                continue

        # Tushare returns newest-first; reverse to oldest-first
        results.sort(key=lambda x: x["date"])

        if limit and len(results) > limit:
            results = results[-limit:]

        logger.info("[tushare] Fetched %d klines for %s", len(results), ts_code)
        return results

    @staticmethod
    def _daily_klines_sync_static(
        code: str, market: str, limit: int, token: str,
    ) -> list[dict[str, Any]]:
        """Static sync klines for use from aggregator sync wrappers."""
        import tushare as ts

        if not token:
            raise TushareError("No TUSHARE_TOKEN configured, skipping")

        ts_code = f"{code}.{market.upper()}"
        ed = datetime.now().strftime("%Y%m%d")
        sd = str(int(ed[:4]) - 10) + ed[4:]

        try:
            pro = ts.pro_api(token)
            df = pro.daily(ts_code=ts_code, start_date=sd, end_date=ed)
        except Exception as exc:
            raise TushareError(f"tushare daily query failed: {exc}") from exc

        if df is None or df.empty:
            raise TushareError(f"No tushare data for {ts_code}")

        # Try to get turnover rate from daily_basic
        turn_map: dict[str, float] = {}
        try:
            df_basic = pro.daily_basic(
                ts_code=ts_code, start_date=sd, end_date=ed,
                fields="trade_date,turnover_rate",
            )
            if df_basic is not None and not df_basic.empty:
                for _, rb in df_basic.iterrows():
                    turn_map[str(rb["trade_date"])] = float(rb["turnover_rate"] or 0)
        except Exception:
            pass  # turnover_rate is nice-to-have

        results: list[dict[str, Any]] = []
        for _, r in df.iterrows():
            try:
                trade_date = str(r["trade_date"])
                d = datetime.strptime(trade_date, "%Y%m%d").date()
                results.append({
                    "date": d,
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": safe_int(r.get("vol", 0)) * 100,
                    "amount": float(r.get("amount", 0) or 0) * 1000,
                    "change_pct": float(r.get("pct_chg", 0) or 0),
                    "turnover_rate": turn_map.get(trade_date, 0.0),
                })
            except (ValueError, KeyError) as exc:
                logger.debug("[tushare] Skip bad row: %s", exc)
                continue

        results.sort(key=lambda x: x["date"])

        if limit and len(results) > limit:
            results = results[-limit:]

        logger.info("[tushare] Fetched %d klines (static) for %s", len(results), ts_code)
        return results

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    async def search_stock(self, keyword: str) -> list[dict[str, str]]:
        raise TushareError("Tushare search not implemented")
