"""
Multi-source stock data aggregator with automatic fallback.

``StockDataAggregator`` wraps multiple :class:`DataSourceClient`
implementations and tries them in priority order.  If the primary source
fails (network error, rate-limit, empty data), the next source is
attempted automatically.

Default priority (configurable via ``DATA_SOURCE_PRIORITY``):

  akshare → tushare → baostock → eastmoney → sina → tencent

Behaviour by method
-------------------
* **realtime quote** – skips sources that don't support realtime
  (baostock/tushare) and reorders so fast HTTP scrapers (sina, tencent,
  eastmoney) are tried before the heavy akshare library call.
* **daily klines** – tries *all* sources with complete field coverage
  (akshare, tushare, baostock, eastmoney).  Falls back to sina/tencent
  only if all primary sources fail (those lack ``amount`` / ``turnover_rate``).
* **search** – EastMoney only (others don't offer search).

The attribute :pyattr:`last_source` records which provider fulfilled the
most recent successful request.
"""

import asyncio
import logging
from typing import Any

import httpx
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from app.services.base import DataSourceClient, DataSourceError, safe_int, safe_float
from app.services.akshare_client import AKShareClient
from app.services.baostock_client import BaoStockClient
from app.services.tushare_client import TushareClient
from app.services.eastmoney import EastMoneyClient
from app.services.sina import SinaClient
from app.services.tencent import TencentClient

logger = logging.getLogger(__name__)

# Default priority: library sources first, then web-scraping sources
_DEFAULT_PRIORITY = "akshare,tushare,baostock,eastmoney,sina,tencent"

# Sources whose kline data includes amount + turnover_rate
_KLINE_COMPLETE_SOURCES = {"akshare", "tushare", "baostock", "eastmoney"}


def _build_sources(
    priority_csv: str = _DEFAULT_PRIORITY,
    timeout: int = 10,
    tushare_token: str = "",
) -> list[DataSourceClient]:
    """Instantiate clients in the order given by *priority_csv*."""
    sources: list[DataSourceClient] = []
    for name in priority_csv.split(","):
        name = name.strip().lower()
        if name == "akshare":
            sources.append(AKShareClient(timeout=timeout))
        elif name == "tushare":
            sources.append(TushareClient(timeout=timeout, token=tushare_token))
        elif name == "baostock":
            sources.append(BaoStockClient(timeout=timeout))
        elif name == "eastmoney":
            sources.append(EastMoneyClient(timeout=timeout))
        elif name == "sina":
            sources.append(SinaClient(timeout=timeout))
        elif name == "tencent":
            sources.append(TencentClient(timeout=timeout))
        else:
            logger.warning("Unknown data source '%s', skipping", name)
    if not sources:
        raise ValueError(f"No valid sources in priority list: {priority_csv!r}")
    return sources


class StockDataAggregator:
    """Tries data sources in priority order until one succeeds.

    Parameters
    ----------
    priority : str
        Comma-separated source names.
    timeout : int
        Per-source HTTP timeout in seconds.
    tushare_token : str
        Optional Tushare Pro API token.
    """

    def __init__(
        self,
        priority: str = _DEFAULT_PRIORITY,
        timeout: int = 10,
        tushare_token: str = "",
    ) -> None:
        self._sources = _build_sources(priority, timeout, tushare_token)
        self._timeout = timeout
        self.last_source: str = ""

    async def close(self) -> None:
        """Close all underlying HTTP clients."""
        for src in self._sources:
            try:
                await src.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Realtime quote – fast sources first, skip non-realtime providers
    # ------------------------------------------------------------------

    # Sources that don't support realtime quotes at all
    _NO_REALTIME = {"tushare", "baostock"}

    # Fast native-async HTTP sources tried first; akshare is slow
    # (downloads ALL stocks via sync library) so it goes last.
    _REALTIME_ORDER = {
        "sina": 0,
        "tencent": 1,
        "eastmoney": 2,
        "akshare": 3,
    }

    async def get_realtime_quote(
        self, code: str, market: str
    ) -> dict[str, Any]:
        """Try fast sources first; return the first successful result.

        Sources that don't support realtime (tushare, baostock) are
        skipped entirely.  The remaining sources are sorted so that
        lightweight HTTP scrapers (sina, tencent) run before heavy
        library calls (akshare) to keep total latency low.
        """
        realtime_srcs = [
            src for src in self._sources
            if src.source_name not in self._NO_REALTIME
        ]
        realtime_srcs.sort(
            key=lambda s: self._REALTIME_ORDER.get(s.source_name, 99)
        )

        errors: list[str] = []
        for src in realtime_srcs:
            try:
                result = await src.get_realtime_quote(code, market)
                self.last_source = src.source_name
                logger.info(
                    "[aggregator] Realtime %s.%s from %s",
                    market, code, src.source_name,
                )
                return result
            except DataSourceError as exc:
                errors.append(f"{src.source_name}: {exc}")
                logger.debug(
                    "[aggregator] %s realtime failed: %s", src.source_name, exc
                )

        raise DataSourceError(
            f"All sources failed for realtime {market}.{code}: "
            + "; ".join(errors)
        )

    # ------------------------------------------------------------------
    # Daily klines – try complete sources first, then incomplete fallback
    # ------------------------------------------------------------------
    async def get_daily_klines(
        self,
        code: str,
        market: str,
        limit: int = 120,
        start_date: str = "20200101",
        end_date: str = "20500101",
    ) -> list[dict[str, Any]]:
        """Fetch klines from sources with complete field coverage first.

        If all complete sources fail, falls back to incomplete sources
        (sina/tencent) which lack ``amount`` and ``turnover_rate``.
        Returns an empty list only if absolutely everything fails.
        """
        errors: list[str] = []

        # Phase 1: Try complete sources (akshare, tushare, baostock, eastmoney)
        for src in self._sources:
            if src.source_name not in _KLINE_COMPLETE_SOURCES:
                continue
            try:
                result = await src.get_daily_klines(
                    code, market, limit, start_date, end_date
                )
                if result:  # non-empty
                    self.last_source = src.source_name
                    logger.info(
                        "[aggregator] Klines %s.%s (%d rows) from %s",
                        market, code, len(result), src.source_name,
                    )
                    return result
                else:
                    errors.append(f"{src.source_name}: empty result")
            except DataSourceError as exc:
                errors.append(f"{src.source_name}: {exc}")
                logger.warning(
                    "[aggregator] %s klines failed: %s", src.source_name, exc
                )

        # Phase 2: Fallback to incomplete sources (sina, tencent)
        logger.warning(
            "[aggregator] All complete kline sources failed for %s.%s, "
            "trying incomplete sources (missing amount/turnover_rate)",
            market, code,
        )
        for src in self._sources:
            if src.source_name in _KLINE_COMPLETE_SOURCES:
                continue
            try:
                result = await src.get_daily_klines(
                    code, market, limit, start_date, end_date
                )
                if result:
                    self.last_source = src.source_name
                    logger.info(
                        "[aggregator] Klines %s.%s (%d rows) from %s (INCOMPLETE)",
                        market, code, len(result), src.source_name,
                    )
                    return result
            except DataSourceError as exc:
                errors.append(f"{src.source_name}: {exc}")
                logger.warning(
                    "[aggregator] %s klines failed: %s", src.source_name, exc
                )

        logger.error(
            "[aggregator] ALL kline sources failed for %s.%s: %s",
            market, code, "; ".join(errors),
        )
        return []

    # ------------------------------------------------------------------
    # Search – EastMoney only
    # ------------------------------------------------------------------
    async def search_stock(self, keyword: str) -> list[dict[str, str]]:
        """Delegate search to the first source that supports it."""
        errors: list[str] = []
        for src in self._sources:
            try:
                result = await src.search_stock(keyword)
                self.last_source = src.source_name
                return result
            except DataSourceError as exc:
                errors.append(f"{src.source_name}: {exc}")

        raise DataSourceError(
            f"All sources failed for search '{keyword}': "
            + "; ".join(errors)
        )

    # ==================================================================
    # Synchronous wrappers for Celery / non-async contexts
    # ==================================================================

    def get_realtime_quote_sync(
        self, code: str, market: str
    ) -> dict[str, Any]:
        """Synchronous version using ``httpx.Client`` with fallback.

        Mirrors the async version: skips non-realtime providers and
        tries fast HTTP scrapers before heavy library calls.
        """
        realtime_srcs = [
            src for src in self._sources
            if src.source_name not in self._NO_REALTIME
        ]
        realtime_srcs.sort(
            key=lambda s: self._REALTIME_ORDER.get(s.source_name, 99)
        )

        errors: list[str] = []
        for src in realtime_srcs:
            try:
                result = _sync_realtime(src.source_name, code, market, timeout=self._timeout)
                self.last_source = src.source_name
                logger.info(
                    "[aggregator-sync] Realtime %s.%s from %s",
                    market, code, src.source_name,
                )
                return result
            except DataSourceError as exc:
                errors.append(f"{src.source_name}: {exc}")
                logger.warning(
                    "[aggregator-sync] %s realtime failed: %s",
                    src.source_name, exc,
                )

        raise DataSourceError(
            f"All sources failed (sync) for realtime {market}.{code}: "
            + "; ".join(errors)
        )

    def get_daily_klines_sync(
        self,
        code: str,
        market: str,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        """Synchronous klines — tries complete sources first, then incomplete."""
        errors: list[str] = []

        # Phase 1: complete sources
        for src in self._sources:
            if src.source_name not in _KLINE_COMPLETE_SOURCES:
                continue
            try:
                result = _sync_klines(src.source_name, code, market, limit, timeout=self._timeout)
                if result:
                    self.last_source = src.source_name
                    return result
            except DataSourceError as exc:
                errors.append(f"{src.source_name}: {exc}")
                logger.warning(
                    "[aggregator-sync] %s klines failed: %s",
                    src.source_name, exc,
                )

        # Phase 2: incomplete sources
        for src in self._sources:
            if src.source_name in _KLINE_COMPLETE_SOURCES:
                continue
            try:
                result = _sync_klines(src.source_name, code, market, limit, timeout=self._timeout)
                if result:
                    self.last_source = src.source_name
                    return result
            except DataSourceError as exc:
                errors.append(f"{src.source_name}: {exc}")

        return []


# ======================================================================
# Synchronous fetch helpers (for Celery tasks)
# ======================================================================

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}


_SYNC_TIMEOUT = 15  # seconds, per-source timeout for sync calls


def _sync_realtime(source: str, code: str, market: str, timeout: int = _SYNC_TIMEOUT) -> dict[str, Any]:
    """Dispatch synchronous realtime fetch to the right provider with timeout."""
    if source == "eastmoney":
        return _sync_realtime_eastmoney(code, market, timeout)
    elif source == "sina":
        return _sync_realtime_sina(code, market, timeout)
    elif source == "tencent":
        return _sync_realtime_tencent(code, market, timeout)
    elif source == "akshare":
        # akshare sync calls can hang — wrap with thread timeout
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(AKShareClient._realtime_quote_sync, code, market)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeoutError:
                raise DataSourceError(f"akshare sync realtime timed out after {timeout}s")
    # baostock / tushare don't support realtime
    raise DataSourceError(f"Realtime not supported from {source}")


def _sync_klines(
    source: str, code: str, market: str, limit: int, timeout: int = _SYNC_TIMEOUT
) -> list[dict[str, Any]]:
    """Dispatch synchronous kline fetch to the right provider with timeout."""
    if source == "eastmoney":
        return _sync_klines_eastmoney(code, market, limit, timeout)
    elif source == "akshare":
        from datetime import datetime
        end = datetime.now().strftime("%Y%m%d")
        start = str(int(end[:4]) - 10) + end[4:]
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                AKShareClient._daily_klines_sync, code, market, limit, start, end
            )
            try:
                return future.result(timeout=timeout)
            except FuturesTimeoutError:
                raise DataSourceError(f"akshare sync klines timed out after {timeout}s")
    elif source == "baostock":
        from datetime import datetime
        end = datetime.now().strftime("%Y%m%d")
        start = str(int(end[:4]) - 10) + end[4:]
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                BaoStockClient._daily_klines_sync, code, market, limit, start, end
            )
            try:
                return future.result(timeout=timeout)
            except FuturesTimeoutError:
                raise DataSourceError(f"baostock sync klines timed out after {timeout}s")
    elif source == "tushare":
        from app.config import settings
        if not settings.TUSHARE_TOKEN:
            raise DataSourceError("No TUSHARE_TOKEN configured, skipping tushare")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                TushareClient._daily_klines_sync_static, code, market, limit, settings.TUSHARE_TOKEN
            )
            try:
                return future.result(timeout=timeout)
            except FuturesTimeoutError:
                raise DataSourceError(f"tushare sync klines timed out after {timeout}s")
    raise DataSourceError(f"Klines not supported from {source}")


# --- EastMoney sync ---------------------------------------------------

def _sync_realtime_eastmoney(code: str, market: str, timeout: int = 10) -> dict[str, Any]:
    from datetime import datetime

    mkt = market.upper()
    if mkt == "SH":
        secid = f"1.{code}"
    elif mkt == "BJ":
        secid = f"0.{code}"
    else:
        secid = f"0.{code}"
    headers = {**_BROWSER_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    params = {
        "secid": secid,
        "fields": "f43,f44,f45,f46,f47,f48,f55,f57,f58,f59,f60,f168,f170",
        "ut": "fa5fd1943c7b386f172d6893dbbd1177",
    }
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            resp = client.get(
                "https://push2.eastmoney.com/api/qt/stock/get", params=params
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
    except Exception as exc:
        raise DataSourceError(f"eastmoney sync realtime: {exc}") from exc

    if not data:
        raise DataSourceError(f"No realtime data from eastmoney for {code}")

    precision = 10 ** data.get("f59", 2)
    return {
        "code": str(data.get("f57", code)),
        "name": data.get("f58", ""),
        "price": data.get("f43", 0) / precision,
        "open": data.get("f46", 0) / precision,
        "high": data.get("f44", 0) / precision,
        "low": data.get("f45", 0) / precision,
        "close": data.get("f43", 0) / precision,
        "prev_close": data.get("f60", 0) / precision,
        "volume": data.get("f47", 0) * 100,
        "amount": data.get("f48", 0),
        "change_pct": data.get("f170", 0) / 100,
        "turnover_rate": data.get("f168", 0) / 100,
        "timestamp": datetime.now(),
    }


def _sync_klines_eastmoney(
    code: str, market: str, limit: int = 120, timeout: int = 10
) -> list[dict[str, Any]]:
    from datetime import datetime

    mkt = market.upper()
    if mkt == "SH":
        secid = f"1.{code}"
    elif mkt == "BJ":
        secid = f"0.{code}"
    else:
        secid = f"0.{code}"
    headers = {**_BROWSER_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "beg": "0",
        "end": "20500101",
        "lmt": str(limit),
        "ut": "fa5fd1943c7b386f172d6893dbbd1177",
    }
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            resp = client.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params=params,
            )
            resp.raise_for_status()
            raw = resp.json().get("data", {})
    except Exception as exc:
        raise DataSourceError(f"eastmoney sync klines: {exc}") from exc

    klines = raw.get("klines", [])
    results: list[dict[str, Any]] = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        try:
            results.append({
                "date": datetime.strptime(parts[0], "%Y-%m-%d").date(),
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": safe_int(parts[5]),
                "amount": float(parts[6]),
                "change_pct": float(parts[8]),
                "turnover_rate": (
                    float(parts[10]) if parts[10] != "-" else None
                ),
            })
        except (ValueError, IndexError):
            continue
    return results


# --- Sina sync --------------------------------------------------------

def _sync_realtime_sina(code: str, market: str, timeout: int = 10) -> dict[str, Any]:
    import re
    from datetime import datetime

    symbol = f"{market.lower()}{code}"
    headers = {**_BROWSER_HEADERS, "Referer": "https://finance.sina.com.cn"}
    try:
        with httpx.Client(timeout=timeout, headers=headers) as client:
            resp = client.get(f"https://hq.sinajs.cn/list={symbol}")
            resp.raise_for_status()
            text = resp.content.decode("gb18030", errors="replace")
    except Exception as exc:
        raise DataSourceError(f"sina sync realtime: {exc}") from exc

    m = re.search(r'var hq_str_\w+="(.*)";', text)
    if not m or not m.group(1):
        raise DataSourceError(f"Empty sina response for {symbol}")

    fields = m.group(1).split(",")
    if len(fields) < 32:
        raise DataSourceError(f"Too few fields ({len(fields)}) from sina")

    try:
        price = float(fields[3])
        prev_close = float(fields[2])
        if price <= 0:
            raise DataSourceError(f"Invalid sina price {price}")
        change_pct = (
            round((price - prev_close) / prev_close * 100, 2)
            if prev_close > 0
            else 0.0
        )
        return {
            "code": code,
            "name": fields[0],
            "price": price,
            "open": float(fields[1]),
            "high": float(fields[4]),
            "low": float(fields[5]),
            "close": price,
            "prev_close": prev_close,
            "volume": safe_int(fields[8]),
            "amount": float(fields[9]),
            "change_pct": change_pct,
            "turnover_rate": 0.0,
            "timestamp": datetime.now(),
        }
    except (ValueError, IndexError) as exc:
        raise DataSourceError(f"sina parse error: {exc}") from exc


# --- Tencent sync -----------------------------------------------------

def _sync_realtime_tencent(code: str, market: str, timeout: int = 10) -> dict[str, Any]:
    from datetime import datetime

    symbol = f"{market.lower()}{code}"
    try:
        with httpx.Client(timeout=timeout, headers=_BROWSER_HEADERS) as client:
            resp = client.get(f"http://qt.gtimg.cn/q={symbol}")
            resp.raise_for_status()
            text = resp.content.decode("gbk", errors="replace")
    except Exception as exc:
        raise DataSourceError(f"tencent sync realtime: {exc}") from exc

    try:
        start = text.index('"') + 1
        end = text.rindex('"')
        content = text[start:end]
    except ValueError:
        raise DataSourceError(f"Cannot parse tencent response for {symbol}")

    fields = content.split("~")
    if len(fields) < 50:
        raise DataSourceError(f"Too few fields ({len(fields)}) from tencent")

    try:
        price = float(fields[3])
        if price <= 0:
            raise DataSourceError(f"Invalid tencent price {price}")
        return {
            "code": code,
            "name": fields[1],
            "price": price,
            "open": float(fields[5]),
            "high": float(fields[33]),
            "low": float(fields[34]),
            "close": price,
            "prev_close": float(fields[4]),
            "volume": safe_int(fields[6]) * 100,
            "amount": float(fields[37]) * 10000,
            "change_pct": float(fields[32]),
            "turnover_rate": float(fields[38]) if fields[38] else 0.0,
            "timestamp": datetime.now(),
        }
    except (ValueError, IndexError) as exc:
        raise DataSourceError(f"tencent parse error: {exc}") from exc
