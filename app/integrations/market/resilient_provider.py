"""
Latency-Hedging & High-Availability Market Provider for Background Fetchers.

Orchestrates Primary Provider (e.g. Yahoo / IndianMarketProvider)
and Fallback Provider (e.g. UpstoxMarketProvider) with:
1. Distributed Circuit Breaker checks on both primary and fallback.
2. Latency Hedging: triggers fallback if primary takes longer than latency_threshold.
3. Clean Coroutine Lifecycle: cancels AND awaits losing tasks to eliminate task leaks.
4. Redundant fallback elimination: never calls fallback a second time if already failed.
5. Strict per-call timeouts on all upstream branches.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, List, Optional, Set, TypeVar

from app.core.circuit_breaker import circuit_breaker
from app.integrations.market.base import BaseMarketProvider
from app.modules.market_intelligence.domain.schemas import (
    StockQuote,
    IndexQuote,
    MarketOverview,
    StockHistoryResponse,
    StockSearchResult,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ResilientMarketProvider(BaseMarketProvider):
    """
    Latency-Hedging Market Provider with Circuit Breaker Integration.
    Designed exclusively for background worker jobs and cache fetchers.
    """

    def __init__(
        self,
        primary: BaseMarketProvider,
        fallback: BaseMarketProvider,
        latency_threshold: float = 4.0,
        primary_name: str = "Primary",
        fallback_name: str = "Upstox",
        per_call_timeout: float = 6.0,
    ):
        self.primary = primary
        self.fallback = fallback
        self.latency_threshold = max(0.05, float(latency_threshold))
        self.primary_name = primary_name
        self.fallback_name = fallback_name
        self.per_call_timeout = max(1.0, float(per_call_timeout))

    async def close(self):
        """Closes connection pools for both underlying providers."""
        if hasattr(self.primary, "close"):
            try:
                await self.primary.close()
            except Exception:
                pass
        if hasattr(self.fallback, "close"):
            try:
                await self.fallback.close()
            except Exception:
                pass

    async def _safe_cancel_tasks(self, tasks: Set[asyncio.Task]) -> None:
        """Properly cancels and awaits pending asyncio tasks to prevent leaks."""
        if not tasks:
            return
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute_with_latency_fallback(
        self,
        operation_name: str,
        primary_coro_fn: Callable[[], Coroutine[Any, Any, T]],
        fallback_coro_fn: Callable[[], Coroutine[Any, Any, T]],
        is_valid_result: Callable[[T], bool] = lambda res: res is not None,
        default_value: Any = None,
    ) -> T:
        """
        Executes primary provider with circuit breaker check.
        If latency exceeds threshold or an error occurs, triggers fallback provider.
        Cancels and awaits losing tasks to guarantee zero leaked coroutines.
        """
        start_time = time.monotonic()

        primary_can_run = await circuit_breaker.can_execute(self.primary_name)
        fallback_can_run = await circuit_breaker.can_execute(self.fallback_name)

        # If primary is OPEN in circuit breaker, jump directly to fallback if available
        if not primary_can_run:
            if fallback_can_run:
                logger.info("[%s] Circuit OPEN for %s. Skipping to fallback %s...",
                            self.primary_name, operation_name, self.fallback_name)
                try:
                    res = await asyncio.wait_for(fallback_coro_fn(), timeout=self.per_call_timeout)
                    if is_valid_result(res):
                        await circuit_breaker.record_success(self.fallback_name)
                        return res
                except Exception as fb_err:
                    await circuit_breaker.record_failure(self.fallback_name, fb_err)
                    logger.warning("[%s] Direct fallback failed for %s: %s", self.fallback_name, operation_name, fb_err)
            return default_value

        # Wrap primary in per-call timeout
        async def run_primary_bounded():
            return await asyncio.wait_for(primary_coro_fn(), timeout=self.per_call_timeout)

        primary_task = asyncio.create_task(run_primary_bounded())

        try:
            done, _ = await asyncio.wait({primary_task}, timeout=self.latency_threshold)
            if primary_task in done:
                # Primary completed within threshold
                try:
                    res = primary_task.result()
                    if is_valid_result(res):
                        await circuit_breaker.record_success(self.primary_name)
                        return res
                    logger.warning(
                        "[%s] Primary returned empty/invalid result for %s in %.2fs. Invoking fallback %s...",
                        self.primary_name,
                        operation_name,
                        time.monotonic() - start_time,
                        self.fallback_name,
                    )
                except Exception as exc:
                    await circuit_breaker.record_failure(self.primary_name, exc)
                    logger.warning(
                        "[%s] Primary error during %s in %.2fs: %s. Invoking fallback %s...",
                        self.primary_name,
                        operation_name,
                        time.monotonic() - start_time,
                        exc,
                        self.fallback_name,
                    )

                # Primary failed/invalid, try fallback sequentially if fallback circuit allows
                if fallback_can_run:
                    try:
                        fallback_res = await asyncio.wait_for(fallback_coro_fn(), timeout=self.per_call_timeout)
                        if is_valid_result(fallback_res):
                            await circuit_breaker.record_success(self.fallback_name)
                            logger.info(
                                "[%s] Fallback recovered %s successfully in %.2fs",
                                self.fallback_name,
                                operation_name,
                                time.monotonic() - start_time,
                            )
                            return fallback_res
                    except Exception as fb_exc:
                        await circuit_breaker.record_failure(self.fallback_name, fb_exc)
                        logger.error("[%s] Fallback also failed for %s: %s", self.fallback_name, operation_name, fb_exc)
                return default_value
        except Exception as wait_exc:
            logger.debug("Exception waiting for primary task: %s", wait_exc)

        # Primary exceeded latency threshold -> trigger fallback in parallel (Latency Hedging)
        elapsed = time.monotonic() - start_time
        logger.warning(
            "Latency occurrence: %s took > %.2fs (elapsed: %.2fs) for %s. Triggering %s fallback...",
            self.primary_name,
            self.latency_threshold,
            elapsed,
            operation_name,
            self.fallback_name,
        )

        async def run_fallback_bounded():
            return await asyncio.wait_for(fallback_coro_fn(), timeout=self.per_call_timeout)

        fallback_task = asyncio.create_task(run_fallback_bounded()) if fallback_can_run else None
        pending: Set[asyncio.Task] = {primary_task}
        if fallback_task:
            pending.add(fallback_task)

        winner_result = default_value
        while pending:
            done_now, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done_now:
                try:
                    res = task.result()
                    if is_valid_result(res):
                        winner = self.fallback_name if task is fallback_task else self.primary_name
                        await circuit_breaker.record_success(winner)
                        logger.info(
                            "Resolved %s via %s in %.2fs total",
                            operation_name,
                            winner,
                            time.monotonic() - start_time,
                        )
                        winner_result = res
                        # Cancel and await any remaining pending tasks
                        await self._safe_cancel_tasks(pending)
                        return winner_result
                    else:
                        loser = self.fallback_name if task is fallback_task else self.primary_name
                        logger.debug("Provider %s returned invalid result during race for %s", loser, operation_name)
                except Exception as e:
                    loser = self.fallback_name if task is fallback_task else self.primary_name
                    await circuit_breaker.record_failure(loser, e)
                    logger.debug("Provider %s threw during race for %s: %s", loser, operation_name, e)

        # Clean up any leftover tasks
        await self._safe_cancel_tasks(pending)
        return winner_result

    async def get_quote(self, symbol: str) -> Optional[StockQuote]:
        return await self._execute_with_latency_fallback(
            f"quote:{symbol}",
            lambda: self.primary.get_quote(symbol),
            lambda: self.fallback.get_quote(symbol),
            is_valid_result=lambda res: res is not None and getattr(res, "current_price", 0) > 0,
            default_value=None,
        )

    async def get_quotes(self, symbols: List[str]) -> List[StockQuote]:
        if not symbols:
            return []

        async def fetch_primary():
            return await self.primary.get_quotes(symbols)

        async def fetch_fallback():
            return await self.fallback.get_quotes(symbols)

        quotes = await self._execute_with_latency_fallback(
            f"quotes_batch({len(symbols)} items)",
            fetch_primary,
            fetch_fallback,
            is_valid_result=lambda res: bool(res and len(res) > 0),
            default_value=[],
        )

        if quotes is None:
            quotes = []

        # If some symbols were missing and fallback circuit allows, attempt to fill
        found_syms = {q.symbol.upper() for q in quotes}
        missing = [
            s for s in symbols
            if s.strip().upper().replace(".NS", "").replace(".BO", "") not in found_syms
            and s.strip().upper() not in found_syms
        ]

        if missing and await circuit_breaker.can_execute(self.fallback_name):
            try:
                fallback_missing = await asyncio.wait_for(
                    self.fallback.get_quotes(missing), timeout=self.per_call_timeout
                )
                if fallback_missing:
                    quotes.extend(fallback_missing)
            except Exception as e:
                logger.debug("Could not fetch missing symbols from fallback: %s", e)

        return quotes

    async def get_indices(self) -> List[IndexQuote]:
        indices = await self._execute_with_latency_fallback(
            "indices",
            self.primary.get_indices,
            self.fallback.get_indices,
            is_valid_result=lambda res: bool(res and len(res) >= 3),
            default_value=[],
        )
        return indices or []

    async def get_market_overview(
        self, index_filter: Optional[str] = None, limit: int = 20
    ) -> Optional[MarketOverview]:
        """
        Retrieves market overview without redundant second fallback call.
        """
        res = await self._execute_with_latency_fallback(
            f"market_overview({index_filter or 'all'})",
            lambda: self.primary.get_market_overview(index_filter=index_filter, limit=limit),
            lambda: self.fallback.get_market_overview(index_filter=index_filter, limit=limit),
            is_valid_result=lambda res: res is not None and bool(getattr(res, "major_indices", None)),
            default_value=None,
        )
        return res

    async def get_historical_candles(
        self, symbol: str, interval: str = "1d", range_period: str = "1mo"
    ) -> Optional[StockHistoryResponse]:
        return await self._execute_with_latency_fallback(
            f"candles:{symbol}:{interval}:{range_period}",
            lambda: self.primary.get_historical_candles(symbol, interval, range_period),
            lambda: self.fallback.get_historical_candles(symbol, interval, range_period),
            is_valid_result=lambda res: res is not None and bool(getattr(res, "candles", None)),
            default_value=None,
        )

    async def search_symbols(self, query: str) -> List[StockSearchResult]:
        results = await self._execute_with_latency_fallback(
            f"search:{query}",
            lambda: self.primary.search_symbols(query),
            lambda: self.fallback.search_symbols(query),
            is_valid_result=lambda res: bool(res and len(res) > 0),
            default_value=[],
        )
        return results or []
