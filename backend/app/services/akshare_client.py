"""
AKShare data source client.

Uses the ``akshare`` library (which scrapes various Chinese financial
data sites) to provide realtime quotes, historical K-line data, and
stock search.

AKShare calls are synchronous, so we wrap them with
``asyncio.to_thread`` to keep the async interface.
"""

import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import Any

from app.services.base import DataSourceClient, DataSourceError, safe_int, safe_float

logger = logging.getLogger(__name__)


class AKShareError(DataSourceError):
    """Raised when an AKShare API call fails."""


class AKShareClient(DataSourceClient):
    """Async wrapper around the ``akshare`` library."""

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    @property
    def source_name(self) -> str:
        return "akshare"

    async def close(self) -> None:
        pass  # no persistent connection

    # ------------------------------------------------------------------
    # Realtime quote
    # ------------------------------------------------------------------
    async def get_realtime_quote(self, code: str, market: str) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._realtime_quote_sync, code, market),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise AKShareError(
                f"akshare realtime timed out after {self._timeout}s"
            )
        except AKShareError:
            raise
        except Exception as exc:
            raise AKShareError(f"akshare realtime failed: {exc}") from exc

    @staticmethod
    def _realtime_quote_sync(code: str, market: str) -> dict[str, Any]:
        import akshare as ak

        try:
            df = ak.stock_zh_a_spot_em()
        except Exception as exc:
            raise AKShareError(f"ak.stock_zh_a_spot_em failed: {exc}") from exc

        row = df[df["代码"] == code]
        if row.empty:
            raise AKShareError(f"Stock {code} not found in akshare spot data")

        r = row.iloc[0]
        price = safe_float(r.get("最新价", 0))
        prev_close = safe_float(r.get("昨收", 0))
        return {
            "code": code,
            "name": str(r.get("名称", "")),
            "price": price,
            "open": safe_float(r.get("今开", 0)),
            "high": safe_float(r.get("最高", 0)),
            "low": safe_float(r.get("最低", 0)),
            "close": price,
            "prev_close": prev_close,
            "volume": safe_int(r.get("成交量", 0)),
            "amount": safe_float(r.get("成交额", 0)),
            "change_pct": safe_float(r.get("涨跌幅", 0)),
            "turnover_rate": safe_float(r.get("换手率", 0)),
            "timestamp": datetime.now(),
        }

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
            raise AKShareError(
                f"akshare klines timed out after {self._timeout}s"
            )
        except AKShareError:
            raise
        except Exception as exc:
            raise AKShareError(f"akshare klines failed: {exc}") from exc

    @staticmethod
    def _daily_klines_sync(
        code: str, market: str, limit: int,
        start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        import akshare as ak

        sd = f"{start_date[:4]}{start_date[4:6]}{start_date[6:8]}"
        ed = end_date
        if ed == "20500101":
            ed = datetime.now().strftime("%Y%m%d")

        # Build symbol in Sina format: "sh600519", "sz000001", or "bj830799"
        mkt = market.upper()
        if mkt == "SH":
            prefix = "sh"
        elif mkt == "BJ":
            prefix = "bj"
        else:
            prefix = "sz"
        sina_symbol = f"{prefix}{code}"

        try:
            # Use stock_zh_a_daily (Sina backend) – works even when
            # EastMoney is rate-limiting.  Provides volume, amount,
            # outstanding_share and turnover.
            df = ak.stock_zh_a_daily(
                symbol=sina_symbol,
                start_date=sd,
                end_date=ed,
                adjust="qfq",  # forward-adjusted
            )
        except Exception as exc:
            raise AKShareError(
                f"ak.stock_zh_a_daily failed for {sina_symbol}: {exc}"
            ) from exc

        if df is None or df.empty:
            logger.warning("[akshare] No kline data for %s", code)
            return []

        results: list[dict[str, Any]] = []
        for _, r in df.iterrows():
            try:
                dt = r["date"]
                if hasattr(dt, "date"):
                    dt = dt.date()
                elif isinstance(dt, str):
                    dt = datetime.strptime(dt[:10], "%Y-%m-%d").date()

                vol = safe_int(r.get("volume", 0))
                amt = safe_float(r.get("amount", 0))
                close_ = float(r["close"])
                open_ = float(r["open"])
                turnover = safe_float(r.get("turnover", 0))

                # turnover from Sina is a ratio (e.g. 0.002);
                # the unified format expects a percentage.
                turnover_pct = turnover * 100.0

                # Compute change_pct from previous close if available
                prev_close = float(r.get("close", 0) or 0)
                results.append({
                    "date": dt,
                    "open": open_,
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": close_,
                    "volume": vol,
                    "amount": amt,
                    "change_pct": 0.0,  # will be computed below
                    "turnover_rate": turnover_pct,
                })
            except (ValueError, KeyError) as exc:
                logger.debug("[akshare] Skip bad row: %s", exc)
                continue

        # Compute change_pct from consecutive closes
        for i in range(len(results)):
            if i == 0:
                results[i]["change_pct"] = 0.0
            else:
                prev = results[i - 1]["close"]
                if prev:
                    results[i]["change_pct"] = round(
                        (results[i]["close"] - prev) / prev * 100, 4
                    )

        if limit and len(results) > limit:
            results = results[-limit:]

        logger.info("[akshare] Fetched %d klines for %s", len(results), code)
        return results

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    async def search_stock(self, keyword: str) -> list[dict[str, str]]:
        raise AKShareError("AKShare does not support stock search")
