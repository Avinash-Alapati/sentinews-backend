from abc import ABC, abstractmethod
from typing import List, Optional
from app.modules.market_intelligence.domain.schemas import (
    StockQuote,
    IndexQuote,
    MarketOverview,
    StockHistoryResponse,
    StockSearchResult,
)


class BaseMarketProvider(ABC):
    """
    Abstract interface for Market Data Providers.
    """

    @abstractmethod
    async def get_quote(self, symbol: str) -> Optional[StockQuote]:
        """Fetch real-time quote for a given symbol (NSE/BSE)."""
        pass

    @abstractmethod
    async def get_quotes(self, symbols: List[str]) -> List[StockQuote]:
        """Fetch real-time quotes for multiple symbols."""
        pass

    @abstractmethod
    async def get_indices(self) -> List[IndexQuote]:
        """Fetch real-time quotes for major Indian benchmark indices."""
        pass

    @abstractmethod
    async def get_market_overview(
        self, index_filter: Optional[str] = None, limit: int = 20
    ) -> MarketOverview:
        """Fetch market status, major indices, and top movers filtered by index (nifty50, nifty500, midcap100, smallcap100, total_market)."""
        pass

    @abstractmethod
    async def get_historical_candles(
        self, symbol: str, interval: str = "1d", range_period: str = "1mo"
    ) -> Optional[StockHistoryResponse]:
        """Fetch historical or intraday candles for charts."""
        pass

    @abstractmethod
    async def search_symbols(self, query: str) -> List[StockSearchResult]:
        """Search Indian stock symbols across exchanges."""
        pass
