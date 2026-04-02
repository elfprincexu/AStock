from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, ConfigDict


class StockCreate(BaseModel):
    code: str
    name: str
    market: str = "SH"


class StockUpdate(BaseModel):
    name: Optional[str] = None
    market: Optional[str] = None
    is_active: Optional[bool] = None
    is_favorite: Optional[bool] = None


class StockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    market: str
    is_active: bool
    is_favorite: bool
    created_at: datetime


class QuoteSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    price: float
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    change_pct: float
    timestamp: datetime


class DailyKlineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    change_pct: float
    turnover_rate: Optional[float] = None


class FetchLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: Optional[int] = None
    fetch_type: str
    source: Optional[str] = None
    status: str
    message: Optional[str] = None
    created_at: datetime


class StockProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: int
    industry: Optional[str] = None
    sector: Optional[str] = None
    total_market_cap: Optional[float] = None
    circ_market_cap: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    total_shares: Optional[int] = None
    circ_shares: Optional[int] = None
    updated_at: datetime


class FetchLogDetailOut(BaseModel):
    """FetchLog with stock code and name for display."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: Optional[int] = None
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None
    fetch_type: str
    source: Optional[str] = None
    status: str
    message: Optional[str] = None
    created_at: datetime


class StockEnrichedOut(BaseModel):
    """Stock with profile data and latest kline date for management views."""
    id: int
    code: str
    name: str
    market: str
    is_active: bool
    is_favorite: bool
    created_at: datetime
    # Profile data (nullable)
    industry: Optional[str] = None
    total_market_cap: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    # Latest kline date
    latest_kline_date: Optional[date] = None


class StockListResponse(BaseModel):
    """Paginated stock list response."""
    total: int
    items: list[StockEnrichedOut]
