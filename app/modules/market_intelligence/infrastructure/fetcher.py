"""
Background Market Intelligence Fetcher & Cache Warmer.

Executes asynchronous market data retrieval without blocking user HTTP requests.
Supports:
1. In-Process Fetcher Loop with distributed leader election (for single-instance Render).
2. Celery Worker Task execution (for multi-worker deployments).
3. Market-hours aware dynamic cadences (30s in-hours, 5m off-hours, 15m weekends).
4. Token-bucket rate-limited batch quote retrieval.
5. Non-blocking startup cold-start warm-up.
"""

import asyncio
import logging
import time
from typing import List, Optional

from app.cache.market_cache import market_cache
from app.core.config import settings
from app.infrastructure.observability.metrics import (
    market_fetcher_duration_seconds,
    market_fetcher_operations_total,
)
from app.integrations.market.base import BaseMarketProvider
from app.integrations.market.indian_market_provider import IndianMarketProvider
from app.integrations.market.resilient_provider import ResilientMarketProvider
from app.integrations.market.upstox_provider import UpstoxMarketProvider
from app.modules.market_intelligence.domain.market_schedule import (
    get_current_market_cadence,
    is_indian_market_open,
)
from app.modules.market_intelligence.infrastructure.universe_aggregator import (
    get_active_quote_universe,
    provider_rate_limiter,
)
from app.modules.market_reports.infrastructure.adapters.nse_market_data_adapter import (
    NSEMarketDataProvider,
)

logger = logging.getLogger("sentinews.market_intelligence.fetcher")


def _record_fetcher_metric(job: str, status: str, duration: float) -> None:
    """Helper to safely record fetcher metrics (fail-open)."""
    try:
        market_fetcher_operations_total.labels(job=job, status=status).inc()
        market_fetcher_duration_seconds.labels(job=job).observe(duration)
    except Exception:
        pass


class MarketDataFetcher:
    """
    Orchestrates background data ingestion and dual-tier cache population.
    """

    def __init__(self):
        primary = IndianMarketProvider()
        fallback = UpstoxMarketProvider()
        self.provider: BaseMarketProvider = ResilientMarketProvider(
            primary=primary,
            fallback=fallback,
            latency_threshold=settings.MARKET_LATENCY_THRESHOLD_SECONDS,
            primary_name="Yahoo Finance",
            fallback_name="Upstox",
            per_call_timeout=6.0,
        )
        self.nse_adapter = NSEMarketDataProvider()
        self._running = False

    async def close(self):
        if hasattr(self.provider, "close"):
            try:
                await self.provider.close()
            except Exception:
                pass

    # =========================================================================
    # INDIVIDUAL REFRESH TASKS
    # =========================================================================

    async def refresh_market_overview(self, index_filter: str = "all", limit: int = 20) -> bool:
        """Fetches market overview snapshot and stores in envelope cache."""
        norm_filter = (index_filter or "all").lower().strip()
        cache_key = f"mkt:overview:{norm_filter}:{limit}"
        start_time = time.perf_counter()

        # Acquire single-flight lock
        if not await market_cache.acquire_single_flight_lock(cache_key, lock_ttl=20):
            logger.debug("Skipping overview refresh for %s; lock already held.", cache_key)
            _record_fetcher_metric("overview", "skipped", 0.0)
            return False

        try:
            overview = await self.provider.get_market_overview(index_filter=norm_filter, limit=limit)
            if overview and bool(getattr(overview, "major_indices", None)):
                await market_cache.set_envelope(
                    key=cache_key,
                    data=overview,
                    soft_ttl=settings.MARKET_OVERVIEW_SOFT_TTL,
                    hard_ttl=settings.MARKET_OVERVIEW_HARD_TTL,
                    source="fetcher:overview",
                )
                # Also populate alias for default requests
                if norm_filter == "all" and limit == 20:
                    await market_cache.set_envelope(
                        key="mkt:overview:all",
                        data=overview,
                        soft_ttl=settings.MARKET_OVERVIEW_SOFT_TTL,
                        hard_ttl=settings.MARKET_OVERVIEW_HARD_TTL,
                        source="fetcher:overview",
                    )
                logger.info("Refreshed market overview (%s, limit=%d) in cache.", norm_filter, limit)
                _record_fetcher_metric("overview", "success", time.perf_counter() - start_time)
                return True
            else:
                logger.warning("Market overview fetch returned empty or invalid data; cache untouched.")
                _record_fetcher_metric("overview", "empty", time.perf_counter() - start_time)
                return False
        except Exception as e:
            logger.error("Error fetching market overview (%s): %s", norm_filter, e)
            _record_fetcher_metric("overview", "failure", time.perf_counter() - start_time)
            return False
        finally:
            await market_cache.release_single_flight_lock(cache_key)

    async def refresh_market_indices(self) -> bool:
        """Fetches benchmark indices and stores in envelope cache."""
        cache_key = "mkt:indices"
        start_time = time.perf_counter()
        if not await market_cache.acquire_single_flight_lock(cache_key, lock_ttl=20):
            _record_fetcher_metric("indices", "skipped", 0.0)
            return False

        try:
            indices = await self.provider.get_indices()
            if indices and len(indices) >= 3:
                await market_cache.set_envelope(
                    key=cache_key,
                    data=indices,
                    soft_ttl=settings.INDICES_SOFT_TTL,
                    hard_ttl=settings.INDICES_HARD_TTL,
                    source="fetcher:indices",
                )
                logger.info("Refreshed %d market indices in cache.", len(indices))
                _record_fetcher_metric("indices", "success", time.perf_counter() - start_time)
                return True
            _record_fetcher_metric("indices", "empty", time.perf_counter() - start_time)
            return False
        except Exception as e:
            logger.error("Error fetching market indices: %s", e)
            _record_fetcher_metric("indices", "failure", time.perf_counter() - start_time)
            return False
        finally:
            await market_cache.release_single_flight_lock(cache_key)

    async def refresh_quote_universe(self, symbols: Optional[List[str]] = None) -> int:
        """
        Batches and fetches live quotes for active universe and writes to mkt:quote:{symbol}.
        """
        start_time = time.perf_counter()
        target_symbols = symbols or await get_active_quote_universe()
        if not target_symbols:
            _record_fetcher_metric("quotes", "empty", 0.0)
            return 0

        batch_size = max(10, settings.QUOTE_BATCH_SIZE)
        updated_count = 0

        for i in range(0, len(target_symbols), batch_size):
            chunk = target_symbols[i : i + batch_size]
            try:
                await provider_rate_limiter.acquire(1.0)
                quotes = await self.provider.get_quotes(chunk)
                for q in quotes:
                    if q and getattr(q, "current_price", 0) > 0:
                        sym_clean = q.symbol.upper().replace(".NS", "").replace(".BO", "")
                        cache_key = f"mkt:quote:{sym_clean}"
                        await market_cache.set_envelope(
                            key=cache_key,
                            data=q,
                            soft_ttl=settings.QUOTE_SOFT_TTL,
                            hard_ttl=settings.QUOTE_HARD_TTL,
                            source="fetcher:quotes",
                        )
                        updated_count += 1
            except Exception as e:
                logger.warning("Error refreshing batch quotes %s: %s", chunk[:3], e)

        logger.info("Refreshed %d/%d active quote universe symbols in cache.", updated_count, len(target_symbols))
        _record_fetcher_metric("quotes", "success" if updated_count > 0 else "empty", time.perf_counter() - start_time)
        return updated_count

    async def refresh_stock_candles(self, symbol: str, interval: str = "1d", range_period: str = "1mo") -> bool:
        """Fetches candle data on-demand and caches."""
        clean_sym = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        cache_key = f"mkt:candles:{clean_sym}:{interval}:{range_period}"
        start_time = time.perf_counter()

        if not await market_cache.acquire_single_flight_lock(cache_key, lock_ttl=20):
            _record_fetcher_metric("candles", "skipped", 0.0)
            return False

        try:
            history = await self.provider.get_historical_candles(clean_sym, interval=interval, range_period=range_period)
            if history and bool(getattr(history, "candles", None)):
                await market_cache.set_envelope(
                    key=cache_key,
                    data=history,
                    soft_ttl=settings.CANDLES_SOFT_TTL,
                    hard_ttl=settings.CANDLES_HARD_TTL,
                    source="fetcher:candles",
                )
                _record_fetcher_metric("candles", "success", time.perf_counter() - start_time)
                return True
            _record_fetcher_metric("candles", "empty", time.perf_counter() - start_time)
            return False
        except Exception as e:
            logger.debug("Error fetching candles for %s: %s", clean_sym, e)
            _record_fetcher_metric("candles", "failure", time.perf_counter() - start_time)
            return False
        finally:
            await market_cache.release_single_flight_lock(cache_key)

    async def refresh_etfs(self) -> bool:
        """Fetches ETF catalog and caches."""
        cache_key = "mkt:etfs:all"
        start_time = time.perf_counter()
        if not await market_cache.acquire_single_flight_lock(cache_key, lock_ttl=60):
            _record_fetcher_metric("etfs", "skipped", 0.0)
            return False

        try:
            raw_items = await self.nse_adapter.get_etfs()
            if raw_items:
                await market_cache.set_envelope(
                    key=cache_key,
                    data=raw_items,
                    soft_ttl=settings.ETFS_SOFT_TTL,
                    hard_ttl=settings.ETFS_HARD_TTL,
                    source="fetcher:etfs",
                )
                logger.info("Refreshed %d ETFs in cache.", len(raw_items))
                _record_fetcher_metric("etfs", "success", time.perf_counter() - start_time)
                return True
            _record_fetcher_metric("etfs", "empty", time.perf_counter() - start_time)
            return False
        except Exception as e:
            logger.debug("Error fetching ETFs: %s", e)
            _record_fetcher_metric("etfs", "failure", time.perf_counter() - start_time)
            return False
        finally:
            await market_cache.release_single_flight_lock(cache_key)

    # =========================================================================
    # FULL CYCLE & LIFESPAN LOOPS
    # =========================================================================

    async def run_full_market_fetch_cycle(self) -> None:
        """Executes one complete market refresh cycle across all datasets."""
        start = time.perf_counter()
        logger.info("Starting background market refresh cycle...")

        filters = ["all", "nifty50", "nifty500", "midcap100", "smallcap100"]
        tasks = [self.refresh_market_overview(f, limit=20) for f in filters]
        tasks.append(self.refresh_market_indices())
        tasks.append(self.refresh_quote_universe())
        tasks.append(self.refresh_etfs())

        results = await asyncio.gather(*tasks, return_exceptions=True)
        duration = time.perf_counter() - start
        _record_fetcher_metric("full_cycle", "success", duration)
        logger.info("Market refresh cycle completed in %.2fs. Results: %s", duration, results)

    async def run_in_process_fetcher_loop(self) -> None:
        """
        Continuous background fetch loop started in FastAPI lifespan.
        Guarded by distributed Redis leader lock so only 1 instance fetches.
        """
        self._running = True
        logger.info("In-process market fetcher loop initialized.")

        # Brief delay to allow app startup to finish cleanly
        await asyncio.sleep(2.0)

        while self._running:
            try:
                # Attempt leader election
                is_leader = await market_cache.acquire_leader_lock("market_fetcher_leader", lock_ttl=60)
                if not is_leader:
                    is_leader = await market_cache.renew_leader_lock("market_fetcher_leader", lock_ttl=60)

                if is_leader:
                    logger.debug("Instance is the active market fetcher leader. Executing cycle...")
                    await self.run_full_market_fetch_cycle()
                    # Renew leader lock post cycle
                    await market_cache.renew_leader_lock("market_fetcher_leader", lock_ttl=60)

                    cadence = get_current_market_cadence()
                    logger.debug("Sleeping for dynamic market cadence: %.1fs", cadence)
                    await asyncio.sleep(cadence)
                else:
                    logger.debug("Instance is a follower. Waiting 15s before next leader check...")
                    await asyncio.sleep(15.0)

            except asyncio.CancelledError:
                logger.info("In-process market fetcher loop cancelled.")
                self._running = False
                break
            except Exception as exc:
                logger.error("Error in in-process market fetcher loop: %s", exc, exc_info=True)
                await asyncio.sleep(10.0)

    async def run_cold_start_warmup(self) -> None:
        """
        Non-blocking startup warmup task.
        Seeds fast baseline values if cache is empty, then triggers immediate refresh.
        """
        try:
            logger.info("Starting non-blocking cold-start cache warmup...")
            # Check if overview already exists
            _, status, _ = await market_cache.get_envelope("mkt:overview:all:20")
            if status == "MISS":
                logger.info("Cold start detected: initiating immediate baseline refresh...")
                # Run initial cycle
                await self.refresh_market_indices()
                await self.refresh_market_overview("all", limit=20)
                await self.refresh_quote_universe()
            logger.info("Cold-start cache warmup complete.")
        except Exception as e:
            logger.warning("Cold-start cache warmup encountered non-fatal error: %s", e)


# Global singleton instance
market_fetcher = MarketDataFetcher()
