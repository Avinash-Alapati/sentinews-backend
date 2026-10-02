"""
Distributed Locking and Concurrency Control for Market Report Generation.
"""

import asyncio
from datetime import date
import logging
import time
from typing import Dict, Optional, Tuple

from app.cache.market_cache import market_cache
from app.modules.market_reports.application.ports import ReportLockPort
from app.modules.market_reports.domain.enums import ReportType

logger = logging.getLogger("sentinews.market_reports.locks")


class RedisReportLock(ReportLockPort):
    """
    Distributed Redis lock for preventing concurrent duplicate generation of market reports.
    Includes in-memory fallback for local execution and offline testing.
    """

    def __init__(self):
        self._memory_locks: Dict[str, Tuple[str, float]] = {}
        self._async_lock = asyncio.Lock()

    def _format_lock_key(self, report_type: ReportType, report_date: date) -> str:
        type_str = report_type.value if hasattr(report_type, "value") else str(report_type)
        return f"market_reports:lock:{type_str.lower()}:{report_date.isoformat()}"

    async def acquire_lock(
        self, report_type: ReportType, report_date: date, ttl_seconds: int = 120
    ) -> bool:
        key = self._format_lock_key(report_type, report_date)
        redis_client = await market_cache._get_redis()

        if redis_client:
            try:
                # Redis SET key value NX EX ttl_seconds
                acquired = await redis_client.set(key, "locked", ex=ttl_seconds, nx=True)
                return bool(acquired)
            except Exception as e:
                logger.debug("Redis acquire_lock failed for %s (%s); falling back to memory.", key, e)

        # In-memory lock fallback
        async with self._async_lock:
            now = time.time()
            if key in self._memory_locks:
                _, expiry = self._memory_locks[key]
                if now < expiry:
                    return False  # Still locked
            self._memory_locks[key] = ("locked", now + ttl_seconds)
            return True

    async def release_lock(self, report_type: ReportType, report_date: date) -> None:
        key = self._format_lock_key(report_type, report_date)
        redis_client = await market_cache._get_redis()

        if redis_client:
            try:
                await redis_client.delete(key)
            except Exception as e:
                logger.debug("Redis release_lock failed for %s: %s", key, e)

        async with self._async_lock:
            self._memory_locks.pop(key, None)
