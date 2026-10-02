"""
Market Data Provider Adapter for Portfolio module.

Wraps the IndianMarketProvider / MarketIntelligenceService behind the
MarketDataProvider Protocol port with Redis caching.
"""

from typing import Dict, List, Optional
import logging
from app.modules.market_intelligence.application.service import market_service
from app.modules.portfolio.application.ports import MarketDataProvider

logger = logging.getLogger("sentinews.portfolio.market_provider")


class LiveMarketDataProvider(MarketDataProvider):
    """
    Adapter satisfying MarketDataProvider port by querying the market intelligence service.
    """

    async def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            quote, _, _ = await market_service.get_realtime_quote(symbol)
            if quote and quote.current_price > 0:
                return quote.current_price
        except Exception as e:
            logger.debug("Could not fetch real-time price for %s: %s", symbol, e)
        return None

    async def get_quotes_batch(self, symbols: List[str]) -> Dict[str, float]:
        price_map: Dict[str, float] = {}
        if not symbols:
            return price_map

        try:
            quotes, _, _ = await market_service.get_realtime_quotes(symbols)
            for q in quotes:
                if q.current_price > 0:
                    price_map[q.symbol.upper()] = q.current_price
        except Exception as e:
            logger.debug("Could not fetch batch quotes for %s: %s", symbols, e)
        return price_map
