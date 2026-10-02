from datetime import datetime
from typing import List, Optional
import pytz
from pydantic import BaseModel, Field

IST = pytz.timezone("Asia/Kolkata")


def get_ist_now() -> datetime:
    return datetime.now(IST)


class StockQuote(BaseModel):
    symbol: str
    company_name: str
    exchange: str = "NSE"
    currency: str = "INR"
    current_price: float
    change: float
    change_percent: float
    open_price: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    previous_close: Optional[float] = None
    volume: Optional[int] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    market_cap: Optional[float] = None
    pe_ratio: Optional[float] = None
    timestamp: datetime = Field(default_factory=get_ist_now)
    provider: str = "Live"
    source: str = "cache"
    is_stale: bool = False


class IndexQuote(BaseModel):
    symbol: str
    name: str
    current_value: float
    change: float
    change_percent: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    previous_close: Optional[float] = None
    is_market_open: bool = False
    timestamp: datetime = Field(default_factory=get_ist_now)
    source: str = "cache"
    is_stale: bool = False


class MarketMover(BaseModel):
    symbol: str
    company_name: str
    exchange: str = "NSE"
    current_price: float
    change: float
    change_percent: float
    volume: Optional[int] = None


class MarketOverview(BaseModel):
    market_status: str  # "OPEN" | "CLOSED"
    status_message: str
    timestamp: datetime = Field(default_factory=get_ist_now)
    major_indices: List[IndexQuote] = []
    top_gainers: List[MarketMover] = []
    top_losers: List[MarketMover] = []
    most_active: List[MarketMover] = []
    source: str = "cache"
    is_stale: bool = False


class CandleData(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = 0


class StockHistoryResponse(BaseModel):
    symbol: str
    interval: str
    range: str
    candles: List[CandleData]
    source: str = "cache"
    is_stale: bool = False


class StockSearchResult(BaseModel):
    symbol: str
    name: str
    exchange: str
    instrument_type: str = "EQUITY"


class ETFQuote(BaseModel):
    symbol: str
    underlying_asset: str
    category: str
    last_price: float
    change: float
    change_percent: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    previous_close: Optional[float] = None
    volume: Optional[int] = None
    traded_value: Optional[float] = None
    nav: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    source: str = "cache"
    is_stale: bool = False


class ETFListResponse(BaseModel):
    total_count: int
    available_categories: List[str]
    items: List[ETFQuote]
    source: str = "cache"
    is_stale: bool = False
