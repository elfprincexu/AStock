"""
Stock market data services.

The primary entry point is :class:`StockDataAggregator` which wraps
AKShare, Tushare, BaoStock, EastMoney, Sina, and Tencent clients
with automatic fallback.
"""

from app.services.base import DataSourceClient, DataSourceError      # noqa: F401
from app.services.akshare_client import AKShareClient, AKShareError  # noqa: F401
from app.services.baostock_client import BaoStockClient, BaoStockError  # noqa: F401
from app.services.tushare_client import TushareClient, TushareError  # noqa: F401
from app.services.eastmoney import EastMoneyClient, EastMoneyError   # noqa: F401
from app.services.sina import SinaClient, SinaError                  # noqa: F401
from app.services.tencent import TencentClient, TencentError         # noqa: F401
from app.services.aggregator import StockDataAggregator              # noqa: F401
