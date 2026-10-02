"""
Cache-Reader Application Service for Market Intelligence.

Follows the strict SLA:
1. User HTTP request path reads ONLY from Redis/memory envelope cache (<2ms).
2. NEVER awaits external third-party API calls inline.
3. Implements Stale-While-Revalidate: serves stale data instantly while triggering
   single-flight background refreshes.
4. Performs stock search locally against the In-Memory Symbol Master (0ms).
"""

import asyncio
import logging
from typing import List, Optional, Tuple, Union

from app.cache.market_cache import market_cache
from app.core.config import settings
from app.infrastructure.observability.metrics import (
    market_cache_age_seconds,
    market_cache_requests_total,
)
from app.modules.market_intelligence.domain.market_schedule import is_indian_market_open
from app.modules.market_intelligence.domain.schemas import (
    StockQuote,
    IndexQuote,
    MarketOverview,
    MarketMover,
    StockHistoryResponse,
    StockSearchResult,
    ETFQuote,
    ETFListResponse,
)
from app.modules.market_intelligence.domain.symbol_master import symbol_master
from app.modules.market_intelligence.infrastructure.fetcher import market_fetcher

logger = logging.getLogger("sentinews.market_intelligence.service")


def _record_metrics(resource: str, status: str, age: float) -> None:
    """Helper to safely record cache lookups and age metrics (fail-open)."""
    try:
        market_cache_requests_total.labels(resource=resource, status=status).inc()
        market_cache_age_seconds.labels(resource=resource).set(age)
    except Exception:
        pass


class MarketIntelligenceService:
    """
    Read-Only Application Service orchestrating envelope cache reads,
    stale-while-revalidate background dispatching, and response metadata.
    """

    def __init__(self):
        # Background fetcher reference (used exclusively for async non-blocking tasks)
        self.fetcher = market_fetcher
        self.provider = market_fetcher.provider

    def _get_baseline_indices(self) -> List[IndexQuote]:
        """Provides instant static baseline indices during cold start."""
        return [
            IndexQuote(symbol="^NSEI", name="NIFTY 50", current_value=25000.0, change=0.0, change_percent=0.0, source="seed_baseline", is_stale=True),
            IndexQuote(symbol="^BSESN", name="SENSEX", current_value=82000.0, change=0.0, change_percent=0.0, source="seed_baseline", is_stale=True),
            IndexQuote(symbol="^NSEBANK", name="NIFTY BANK", current_value=53000.0, change=0.0, change_percent=0.0, source="seed_baseline", is_stale=True),
            IndexQuote(symbol="^CNXIT", name="NIFTY IT", current_value=41000.0, change=0.0, change_percent=0.0, source="seed_baseline", is_stale=True),
        ]

    def _get_baseline_overview(self) -> MarketOverview:
        """Provides instant baseline overview structure during cold start."""
        status = "OPEN" if is_indian_market_open() else "CLOSED"
        return MarketOverview(
            market_status=status,
            status_message="Market intelligence data warming up",
            major_indices=self._get_baseline_indices(),
            top_gainers=[],
            top_losers=[],
            most_active=[],
            source="seed_baseline",
            is_stale=True,
        )

    # =========================================================================
    # INDICES
    # =========================================================================

    async def get_indian_indices(self) -> Tuple[List[IndexQuote], str, float]:
        """
        Retrieves benchmark indices from envelope cache.
        Returns (indices_list, cache_status, cache_age).
        """
        cache_key = "mkt:indices"
        data, status, age = await market_cache.get_envelope(cache_key, soft_ttl_override=settings.INDICES_SOFT_TTL)

        if status == "STALE" or status == "MISS":
            # Trigger non-blocking background refresh
            asyncio.create_task(self.fetcher.refresh_market_indices())

        _record_metrics("indices", status, age)

        if status in ("HIT", "STALE") and data:
            if isinstance(data, list):
                items = []
                for x in data:
                    item = IndexQuote.model_validate(x) if isinstance(x, dict) else x
                    item.is_stale = (status == "STALE")
                    item.source = "cache"
                    items.append(item)
                return items, status, age
            return [data], status, age

        # Cold start fallback
        return self._get_baseline_indices(), "MISS", 0.0

    # =========================================================================
    # OVERVIEW & MOVERS
    # =========================================================================

    async def get_market_overview(
        self, index_filter: Optional[str] = None, limit: int = 20
    ) -> Tuple[MarketOverview, str, float]:
        """
        Retrieves market overview from envelope cache.
        Returns (MarketOverview, cache_status, cache_age).
        """
        norm_filter = (index_filter or "all").lower().strip()
        cache_key = f"mkt:overview:{norm_filter}:{limit}"

        data, status, age = await market_cache.get_envelope(
            cache_key, soft_ttl_override=settings.MARKET_OVERVIEW_SOFT_TTL
        )

        # If filtered overview missed, try default 'all'
        if status == "MISS" and norm_filter != "all":
            fallback_data, fallback_status, fallback_age = await market_cache.get_envelope(
                "mkt:overview:all:20", soft_ttl_override=settings.MARKET_OVERVIEW_SOFT_TTL
            )
            if fallback_data:
                data, status, age = fallback_data, "STALE", fallback_age

        if status == "STALE" or status == "MISS":
            # Trigger background refresh
            asyncio.create_task(self.fetcher.refresh_market_overview(norm_filter, limit=limit))

        _record_metrics("overview", status, age)

        if status in ("HIT", "STALE") and data:
            overview = MarketOverview.model_validate(data) if isinstance(data, dict) else data
            overview.is_stale = (status == "STALE")
            overview.source = "cache"
            return overview, status, age

        # Cold start fallback
        return self._get_baseline_overview(), "MISS", 0.0

    # =========================================================================
    # REAL-TIME QUOTES
    # =========================================================================

    async def get_realtime_quote(self, symbol: str) -> Tuple[Optional[StockQuote], str, float]:
        """
        Retrieves real-time stock quote from envelope cache.
        Returns (StockQuote or None, cache_status, cache_age).
        """
        clean_sym = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        cache_key = f"mkt:quote:{clean_sym}"

        data, status, age = await market_cache.get_envelope(cache_key, soft_ttl_override=settings.QUOTE_SOFT_TTL)

        if status == "STALE" or status == "MISS":
            # Trigger single-flight background refresh
            asyncio.create_task(self.fetcher.refresh_quote_universe([clean_sym]))

        _record_metrics("quote", status, age)

        if status in ("HIT", "STALE") and data:
            if isinstance(data, dict) and data.get("not_found"):
                return None, status, age
            try:
                quote = StockQuote.model_validate(data) if isinstance(data, dict) else data
                quote.is_stale = (status == "STALE")
                quote.source = "cache"
                return quote, status, age
            except Exception as e:
                logger.warning("Cached quote data for %s failed schema validation: %s", clean_sym, e)

        return None, "MISS", 0.0

    async def get_realtime_quotes(self, symbols: List[str]) -> Tuple[List[StockQuote], str, float]:
        """
        Batch retrieves quotes from cache.
        Returns (quotes_list, aggregate_cache_status, average_cache_age).
        """
        if not symbols:
            _record_metrics("quotes", "HIT", 0.0)
            return [], "HIT", 0.0

        results: List[StockQuote] = []
        missing_symbols: List[str] = []
        statuses: List[str] = []
        ages: List[float] = []

        clean_symbols = [s.strip().upper().replace(".NS", "").replace(".BO", "") for s in symbols if s.strip()]

        for sym in clean_symbols:
            cache_key = f"mkt:quote:{sym}"
            data, status, age = await market_cache.get_envelope(cache_key, soft_ttl_override=settings.QUOTE_SOFT_TTL)
            statuses.append(status)
            ages.append(age)

            if status in ("HIT", "STALE") and data:
                if not (isinstance(data, dict) and data.get("not_found")):
                    try:
                        quote = StockQuote.model_validate(data) if isinstance(data, dict) else data
                        quote.is_stale = (status == "STALE")
                        quote.source = "cache"
                        results.append(quote)
                    except Exception:
                        missing_symbols.append(sym)
                else:
                    pass
            else:
                missing_symbols.append(sym)

        if missing_symbols:
            # Trigger background batch refresh for missing symbols
            asyncio.create_task(self.fetcher.refresh_quote_universe(missing_symbols))

        overall_status = "HIT" if all(s == "HIT" for s in statuses) else ("STALE" if any(s == "STALE" for s in statuses) else "MISS")
        avg_age = sum(ages) / len(ages) if ages else 0.0

        _record_metrics("quotes", overall_status, avg_age)
        return results, overall_status, avg_age

    # =========================================================================
    # CANDLE CHARTS & HISTORY
    # =========================================================================

    async def get_stock_history(
        self, symbol: str, interval: str = "1d", range_period: str = "1mo"
    ) -> Tuple[Optional[StockHistoryResponse], str, float]:
        """
        Retrieves historical candle data from envelope cache.
        Returns (StockHistoryResponse or None, cache_status, cache_age).
        """
        clean_sym = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        cache_key = f"mkt:candles:{clean_sym}:{interval}:{range_period}"

        data, status, age = await market_cache.get_envelope(cache_key, soft_ttl_override=settings.CANDLES_SOFT_TTL)

        if status == "STALE" or status == "MISS":
            # Trigger single-flight background refresh
            asyncio.create_task(self.fetcher.refresh_stock_candles(clean_sym, interval, range_period))

        _record_metrics("candles", status, age)

        if status in ("HIT", "STALE") and data:
            try:
                history = StockHistoryResponse.model_validate(data) if isinstance(data, dict) else data
                history.is_stale = (status == "STALE")
                history.source = "cache"
                return history, status, age
            except Exception as e:
                logger.warning("Cached candles for %s failed schema validation: %s", clean_sym, e)

        return None, "MISS", 0.0

    # =========================================================================
    # SEARCH (In-Memory Local Search - 0ms External Latency)
    # =========================================================================

    async def search_stocks(self, query: str) -> Tuple[List[StockSearchResult], str, float]:
        """
        Searches Indian stocks locally against the In-Memory Symbol Master.
        Zero upstream HTTP calls.
        """
        _record_metrics("search", "HIT", 0.0)
        results = symbol_master.search(query, limit=20)
        return results, "HIT", 0.0

    # =========================================================================
    # ETFS
    # =========================================================================

    async def get_etfs(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[ETFListResponse, str, float]:
        """
        Retrieves real-time Indian ETFs from cache with local filtering.
        """
        standard_categories = [
            "Equity - Broad Market",
            "Equity - Sectoral & Thematic",
            "Equity - Factor & Smart Beta",
            "Commodity - Gold",
            "Commodity - Silver",
            "Debt & Liquid",
            "Global / International",
            "Other",
        ]

        cache_key = "mkt:etfs:all"
        data, status, age = await market_cache.get_envelope(cache_key, soft_ttl_override=settings.ETFS_SOFT_TTL)

        if status == "STALE" or status == "MISS":
            asyncio.create_task(self.fetcher.refresh_etfs())

        _record_metrics("etfs", status, age)

        raw_items = data or []
        if isinstance(raw_items, dict):
            raw_items = raw_items.get("items", [])

        # Local filtering by category or search
        filtered = []
        cat_lower = category.lower().strip() if category else None
        search_lower = search.lower().strip() if search else None

        for item in raw_items:
            item_sym = (item.get("symbol") or getattr(item, "symbol", "")).lower()
            item_name = (item.get("company_name") or getattr(item, "company_name", "")).lower()
            item_cat = (item.get("category") or getattr(item, "category", "")).lower()

            if cat_lower and cat_lower not in item_cat:
                continue
            if search_lower and (search_lower not in item_sym and search_lower not in item_name):
                continue
            filtered.append(item)

        total_count = len(filtered)
        paged = filtered[offset : offset + limit] if limit else filtered[offset:]
        items = []
        for x in paged:
            try:
                item = ETFQuote.model_validate(x) if isinstance(x, dict) else x
                item.is_stale = (status == "STALE")
                item.source = "cache"
                items.append(item)
            except Exception as e:
                logger.warning("Cached ETF item failed schema validation: %s", e)

        res = ETFListResponse(
            total_count=total_count,
            available_categories=standard_categories,
            items=items,
            source="cache",
            is_stale=(status == "STALE"),
        )
        return res, status, age

    async def warm_market_overview_cache(self) -> Optional[MarketOverview]:
        """Convenience method for background warmers and tests."""
        success = await self.fetcher.refresh_market_overview("all", limit=20)
        if success:
            data, _, _ = await market_cache.get_envelope("mkt:overview:all:20")
            if data:
                return MarketOverview.model_validate(data) if isinstance(data, dict) else data
        return None


# Global singleton instance
market_service = MarketIntelligenceService()
