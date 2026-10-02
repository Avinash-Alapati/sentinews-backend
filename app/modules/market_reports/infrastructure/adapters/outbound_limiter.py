"""
Outbound Rate Limiter and Request Pacer for External Market Data Adapters.

Enforces strict pacing and concurrency bounds on outbound calls to unofficial
or scraped endpoints (e.g. NSE JSON endpoints, Yahoo Finance, Stooq, RBI)
to prevent IP throttling, 429 Too Many Requests, and upstream overload.
"""

import asyncio
import logging
import time
from typing import Dict

logger = logging.getLogger("sentinews.market_reports.outbound_limiter")


class AsyncTokenBucket:
    """
    Asynchronous Token Bucket rate limiter with concurrency limits.
    """

    def __init__(self, rate_per_second: float = 4.0, capacity: float = 8.0, max_concurrency: int = 3):
        self.rate = rate_per_second
        self.capacity = capacity
        self.tokens = capacity
        self.last_updated = time.monotonic()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def acquire(self) -> None:
        """Acquires a token and semaphore slot, pausing if bucket is exhausted."""
        await self._semaphore.acquire()
        try:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_updated
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_updated = now

                if self.tokens < 1.0:
                    sleep_time = (1.0 - self.tokens) / self.rate
                    await asyncio.sleep(sleep_time)
                    self.tokens = 0.0
                    self.last_updated = time.monotonic()
                else:
                    self.tokens -= 1.0
        except Exception:
            self._semaphore.release()
            raise

    def release(self) -> None:
        """Releases the concurrency semaphore slot."""
        self._semaphore.release()


class OutboundRateLimiter:
    """
    Registry of token buckets partitioned by domain/provider.
    """

    def __init__(self):
        self._limiters: Dict[str, AsyncTokenBucket] = {
            "nse": AsyncTokenBucket(rate_per_second=2.5, capacity=5.0, max_concurrency=2),
            "yfinance": AsyncTokenBucket(rate_per_second=5.0, capacity=10.0, max_concurrency=4),
            "rbi": AsyncTokenBucket(rate_per_second=2.0, capacity=4.0, max_concurrency=2),
            "stooq": AsyncTokenBucket(rate_per_second=1.5, capacity=3.0, max_concurrency=1),
            "default": AsyncTokenBucket(rate_per_second=5.0, capacity=10.0, max_concurrency=3),
        }

    def _get_bucket(self, provider: str) -> AsyncTokenBucket:
        return self._limiters.get(provider.lower(), self._limiters["default"])

    class _Context:
        def __init__(self, bucket: AsyncTokenBucket):
            self.bucket = bucket

        async def __aenter__(self):
            await self.bucket.acquire()
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            self.bucket.release()

    def pace(self, provider: str):
        """Async context manager to pace requests for a specific provider."""
        bucket = self._get_bucket(provider)
        return self._Context(bucket)


# Global singleton instance for outbound pacing across market report adapters
outbound_limiter = OutboundRateLimiter()
