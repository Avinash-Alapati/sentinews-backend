"""
Active Quote Universe Aggregator & Token Bucket Rate Limiter.

Aggregates:
1. Top Benchmark Indian Stocks (NIFTY 50 & popular equities)
2. Distinct symbols in user watchlists
3. Distinct symbols in user portfolios

Deduplicates, caps to MAX_QUOTE_UNIVERSE_SIZE (default 300),
and provides token bucket rate limiting for upstream provider calls.
"""

import asyncio
import logging
import time
from typing import List, Optional, Set
from sqlalchemy import select, func

from app.core.config import settings
from app.db.session import async_session_factory
from app.integrations.market.index_constituents import NIFTY_50_SYMBOLS
from app.integrations.market.indian_market_provider import IndianMarketProvider

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """
    Asynchronous Token Bucket Rate Limiter.
    Ensures upstream market provider queries never exceed rate limits.
    """

    def __init__(self, rate: float = 5.0, capacity: float = 10.0):
        self.rate = max(0.1, float(rate))  # tokens per second
        self.capacity = max(1.0, float(capacity))
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop = None

    def _get_lock(self) -> asyncio.Lock:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if self._lock is None or self._lock_loop != current_loop:
            self._lock = asyncio.Lock()
            self._lock_loop = current_loop
        return self._lock

    async def acquire(self, tokens: float = 1.0) -> None:
        """Blocks until required tokens are available in bucket."""
        async with self._get_lock():
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_refill = now

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return

                needed = tokens - self._tokens
                wait_time = needed / self.rate
                await asyncio.sleep(min(wait_time, 1.0))


# Global provider token bucket (5 req/sec, burst up to 10)
provider_rate_limiter = TokenBucketRateLimiter(rate=5.0, capacity=10.0)


async def get_active_quote_universe() -> List[str]:
    """
    Returns deduplicated, capped symbol universe for background quote refresh:
    1. Core benchmark stocks (NIFTY 50 + Top 50 popular Indian stocks)
    2. Active symbols from watchlists (if DB is accessible)
    3. Active symbols from portfolios (if DB is accessible)
    """
    universe: Set[str] = set()

    # 1. Add popular Indian equities
    for s in IndianMarketProvider.POPULAR_INDIAN_STOCKS:
        clean = s.get("clean") or s.get("symbol", "").replace(".NS", "").replace(".BO", "")
        if clean:
            universe.add(clean.strip().upper())

    # 2. Add NIFTY 50 symbols
    for s in NIFTY_50_SYMBOLS:
        if s:
            universe.add(s.strip().upper())

    # 3. Query distinct active watchlist symbols from database
    try:
        from app.db.models.watchlist import WatchlistItem
        async with async_session_factory() as session:
            stmt = select(WatchlistItem.symbol).distinct().limit(settings.MAX_QUOTE_UNIVERSE_SIZE)
            res = await session.execute(stmt)
            for row in res.scalars().all():
                if row:
                    clean = str(row).strip().upper().replace(".NS", "").replace(".BO", "")
                    universe.add(clean)
    except Exception as e:
        logger.debug("Could not query watchlist symbols for quote universe: %s", e)

    # 4. Query distinct active portfolio symbols from database
    try:
        from app.db.models.portfolio import Holding
        async with async_session_factory() as session:
            stmt = select(Holding.symbol).distinct().limit(settings.MAX_QUOTE_UNIVERSE_SIZE)
            res = await session.execute(stmt)
            for row in res.scalars().all():
                if row:
                    clean = str(row).strip().upper().replace(".NS", "").replace(".BO", "")
                    universe.add(clean)
    except Exception as e:
        logger.debug("Could not query portfolio holdings for quote universe: %s", e)

    # Cap to MAX_QUOTE_UNIVERSE_SIZE
    capped_list = sorted(list(universe))[: settings.MAX_QUOTE_UNIVERSE_SIZE]
    return capped_list
