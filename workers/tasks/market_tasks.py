"""
Market Intelligence Celery Worker Tasks.

Provides background execution for market data ingestion and cache maintenance.
Invoked by APScheduler enqueuer or single-flight trigger dispatches.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.modules.market_intelligence.infrastructure.fetcher import market_fetcher
from workers.celery_app import celery_app

logger = logging.getLogger("sentinews.workers.market_tasks")


def _run_coroutine_sync(coro):
    """Helper to run async coroutines reliably within Celery synchronous worker threads."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=30)
    else:
        return asyncio.run(coro)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    name="workers.tasks.market_tasks.warm_market_overview_cache_task",
)
def warm_market_overview_cache_task(self, filter_name: str = "all", limit: int = 20) -> Dict[str, Any]:
    """Refreshes market overview in cache via background worker."""
    logger.info("Celery task: refreshing market overview (%s, limit=%d)...", filter_name, limit)
    try:
        success = _run_coroutine_sync(market_fetcher.refresh_market_overview(filter_name, limit=limit))
        return {"status": "success" if success else "empty"}
    except Exception as exc:
        logger.error("Error in warm_market_overview_cache_task: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    name="workers.tasks.market_tasks.refresh_market_indices_task",
)
def refresh_market_indices_task(self) -> Dict[str, Any]:
    """Refreshes benchmark indices in cache."""
    try:
        success = _run_coroutine_sync(market_fetcher.refresh_market_indices())
        return {"status": "success" if success else "empty"}
    except Exception as exc:
        logger.error("Error in refresh_market_indices_task: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    name="workers.tasks.market_tasks.refresh_quote_universe_task",
)
def refresh_quote_universe_task(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Refreshes active quote universe."""
    try:
        count = _run_coroutine_sync(market_fetcher.refresh_quote_universe(symbols))
        return {"status": "success", "updated_count": count}
    except Exception as exc:
        logger.error("Error in refresh_quote_universe_task: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    max_retries=1,
    name="workers.tasks.market_tasks.refresh_stock_candles_task",
)
def refresh_stock_candles_task(self, symbol: str, interval: str = "1d", range_period: str = "1mo") -> Dict[str, Any]:
    """Refreshes historical candles for a symbol."""
    try:
        success = _run_coroutine_sync(market_fetcher.refresh_stock_candles(symbol, interval, range_period))
        return {"status": "success" if success else "failed"}
    except Exception as exc:
        logger.error("Error in refresh_stock_candles_task (%s): %s", symbol, exc)
        return {"status": "error", "error": str(exc)}


@celery_app.task(
    bind=True,
    max_retries=2,
    name="workers.tasks.market_tasks.run_full_market_refresh_cycle_task",
)
def run_full_market_refresh_cycle_task(self) -> Dict[str, Any]:
    """Executes a full market refresh cycle."""
    try:
        _run_coroutine_sync(market_fetcher.run_full_market_fetch_cycle())
        return {"status": "success"}
    except Exception as exc:
        logger.error("Error in run_full_market_refresh_cycle_task: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


async def _run_async_warm_market_overview() -> bool:
    """Helper coroutine for tests and legacy callers."""
    return await market_fetcher.refresh_market_overview("all", limit=20)
