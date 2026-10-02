"""
Resilient Dual-Tier Envelope Cache for High-Frequency Financial Intelligence.

Features:
1. Envelope Architecture: {data, fetched_at, source, soft_ttl, hard_ttl}.
2. Soft TTL (freshness) with +-15% Jitter & Hard TTL (stale-while-revalidate serving).
3. Cache Status Tagging: HIT, STALE, MISS with exact Data Age calculation.
4. Distributed Single-Flight Lock helper to prevent upstream dogpiling.
5. Leader Election / Lock for in-process background fetchers.
6. Graceful Degradation: Thread-safe in-memory cache with LRU auto-eviction if Redis is unavailable.
"""

import asyncio
from datetime import datetime
from decimal import Decimal
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

from app.core.circuit_breaker import circuit_breaker
from app.core.config import settings

logger = logging.getLogger(__name__)


class CustomJSONEncoder(json.JSONEncoder):
    """Encodes datetimes, decimals, and Pydantic models."""
    def default(self, obj):
        if isinstance(obj, BaseModel):
            return obj.model_dump(mode="json")
        if isinstance(obj, (datetime,)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


class MarketCache:
    """
    Dual-Tier Envelope Cache:
    1. Primary: Distributed Redis cache (async) with hard expiry.
    2. Fallback: Thread-safe in-memory cache with bounded LRU-like pruning.
    """

    MAX_MEMORY_CACHE_SIZE = 5000

    def __init__(self):
        self._memory_cache: Dict[str, Tuple[Any, float]] = {}  # key -> (envelope_dict, expire_time)
        self._memory_locks: Dict[str, float] = {}  # local single-flight / leader locks
        self._memory_lock: Optional[asyncio.Lock] = None
        self._lock_loop = None
        self._redis: Optional[Any] = None
        self._redis_loop = None
        self._redis_available: bool = False
        self._init_attempted: bool = False

        # Link circuit breaker to this cache's redis client
        circuit_breaker._redis_getter = self.get_redis_client

    def _get_lock(self) -> asyncio.Lock:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if self._memory_lock is None or self._lock_loop != current_loop:
            self._memory_lock = asyncio.Lock()
            self._lock_loop = current_loop
        return self._memory_lock

    async def _get_redis(self):
        if not settings.REDIS_ENABLED or aioredis is None:
            return None

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._redis is not None and getattr(self, "_redis_loop", None) != current_loop:
            # Event loop changed (e.g. across pytest test cases); reset client for current loop
            self._redis = None
            self._init_attempted = False

        if self._redis is None and not self._init_attempted:
            self._init_attempted = True
            self._redis_loop = current_loop
            try:
                conn_kwargs = {
                    "encoding": "utf-8",
                    "decode_responses": True,
                    "socket_connect_timeout": settings.REDIS_TIMEOUT,
                    "socket_timeout": settings.REDIS_TIMEOUT,
                    "health_check_interval": settings.REDIS_HEALTH_CHECK_INTERVAL,
                    "socket_keepalive": settings.REDIS_SOCKET_KEEPALIVE,
                    "retry_on_timeout": settings.REDIS_RETRY_ON_TIMEOUT,
                    "max_connections": settings.REDIS_MAX_CONNECTIONS,
                }

                # Handle rediss:// and In-Transit TLS Encryption (e.g. AWS ElastiCache)
                is_tls = settings.REDIS_URL.startswith("rediss://") or settings.REDIS_TLS_ENABLED
                if is_tls:
                    import ssl
                    cert_map = {
                        "none": ssl.CERT_NONE,
                        "optional": ssl.CERT_OPTIONAL,
                        "required": ssl.CERT_REQUIRED,
                    }
                    req_val = (settings.REDIS_SSL_CERT_REQS or "none").lower()
                    conn_kwargs["ssl_cert_reqs"] = cert_map.get(req_val, ssl.CERT_NONE)

                client = aioredis.from_url(settings.REDIS_URL, **conn_kwargs)
                await asyncio.wait_for(client.ping(), timeout=settings.REDIS_TIMEOUT)
                self._redis = client
                self._redis_available = True
                logger.info("Connected to Redis cache at %s (TLS: %s)", settings.REDIS_URL, is_tls)
            except Exception as e:
                self._redis_available = False
                logger.warning("Redis not available (%s). Using in-memory fallback cache.", str(e))
        return self._redis if self._redis_available else None

    def get_redis_client(self) -> Optional[Any]:
        """Returns active Redis client only if successfully connected and available on current loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if self._redis_loop != current_loop:
            return None
        return self._redis if self._redis_available else None

    async def close(self):
        """Gracefully closes Redis connection pool on server shutdown."""
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
            self._redis_available = False

    def _serialize(self, value: Any) -> str:
        """Safely serializes Pydantic models, lists, and dicts containing datetimes."""
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        return json.dumps(value, cls=CustomJSONEncoder)

    def _apply_jitter(self, base_ttl: int) -> float:
        """Applies configured +-15% random jitter to base TTL to prevent stampedes."""
        pct = max(0.0, float(settings.CACHE_SOFT_TTL_JITTER_PCT))
        if pct == 0.0:
            return float(base_ttl)
        delta = base_ttl * pct
        return round(float(base_ttl) + random.uniform(-delta, delta), 2)

    # =========================================================================
    # ENVELOPE GET & SET (Primary Interface for Request Path & Fetchers)
    # =========================================================================

    async def get_envelope(
        self,
        key: str,
        soft_ttl_override: Optional[int] = None,
    ) -> Tuple[Optional[Any], str, float]:
        """
        Retrieves data envelope for given key and resolves freshness status.

        Returns:
            Tuple of:
            1. data: Deserialized object/dict/list (or None if MISS).
            2. status: "HIT" (fresh), "STALE" (stale-while-revalidate), or "MISS" (cold/empty).
            3. age: Elapsed seconds since data was fetched from upstream.
        """
        now = time.time()
        raw_envelope: Optional[Dict[str, Any]] = None

        # 1. Try Redis first
        redis_client = await self._get_redis()
        if redis_client:
            try:
                raw = await asyncio.wait_for(redis_client.get(key), timeout=settings.REDIS_TIMEOUT)
                if raw:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and "fetched_at" in parsed and "data" in parsed:
                        raw_envelope = parsed
                    else:
                        # Legacy un-enveloped raw payload
                        raw_envelope = {
                            "data": parsed,
                            "fetched_at": now - 5.0,
                            "source": "legacy",
                            "soft_ttl": soft_ttl_override or settings.QUOTE_SOFT_TTL,
                            "hard_ttl": settings.QUOTE_HARD_TTL,
                        }
            except Exception as e:
                logger.debug("Redis get_envelope failed for key %s: %s; falling back to memory.", key, e)

        # 2. In-Memory Cache Lookup (if Redis missed or errored)
        if raw_envelope is None:
            async with self._get_lock():
                item = self._memory_cache.get(key)
                if item:
                    val, expiry = item
                    if now <= expiry:
                        if isinstance(val, dict) and "fetched_at" in val and "data" in val:
                            raw_envelope = val
                        else:
                            raw_envelope = {
                                "data": val,
                                "fetched_at": now - 5.0,
                                "source": "memory",
                                "soft_ttl": soft_ttl_override or settings.QUOTE_SOFT_TTL,
                                "hard_ttl": settings.QUOTE_HARD_TTL,
                            }
                    else:
                        del self._memory_cache[key]

        # 3. Resolve status and age
        if raw_envelope is None:
            return None, "MISS", 0.0

        fetched_at = float(raw_envelope.get("fetched_at", now))
        age = max(0.0, now - fetched_at)
        soft_ttl = float(soft_ttl_override or raw_envelope.get("soft_ttl", settings.QUOTE_SOFT_TTL))

        if age <= soft_ttl:
            return raw_envelope["data"], "HIT", age
        else:
            return raw_envelope["data"], "STALE", age

    async def set_envelope(
        self,
        key: str,
        data: Any,
        soft_ttl: int,
        hard_ttl: int,
        source: str = "upstream",
    ) -> None:
        """
        Stores data wrapped in standard Envelope with soft TTL (with jitter) and hard TTL.
        Never overwrites if data is None or empty.
        """
        if data is None:
            logger.warning("Attempted to set empty data in envelope for key %s. Skipped.", key)
            return

        now = time.time()
        effective_soft_ttl = self._apply_jitter(soft_ttl)
        effective_hard_ttl = max(int(hard_ttl), int(soft_ttl) * 2)

        # Serialize inner data if it's a Pydantic model
        if isinstance(data, BaseModel):
            data_payload = json.loads(data.model_dump_json())
        elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], BaseModel):
            data_payload = [json.loads(item.model_dump_json()) for item in data]
        else:
            data_payload = data

        envelope = {
            "data": data_payload,
            "fetched_at": now,
            "source": source,
            "soft_ttl": effective_soft_ttl,
            "hard_ttl": effective_hard_ttl,
        }

        serialized = json.dumps(envelope, cls=CustomJSONEncoder)

        # 1. Write to Redis
        redis_client = await self._get_redis()
        if redis_client:
            try:
                await asyncio.wait_for(
                    redis_client.set(key, serialized, ex=effective_hard_ttl),
                    timeout=settings.REDIS_TIMEOUT,
                )
            except Exception as e:
                logger.debug("Redis set_envelope failed for key %s: %s; falling back to memory.", key, e)

        # 2. Write to bounded in-memory cache
        async with self._get_lock():
            if len(self._memory_cache) >= self.MAX_MEMORY_CACHE_SIZE:
                expired = [k for k, (_, exp) in self._memory_cache.items() if now > exp]
                for k in expired:
                    del self._memory_cache[k]
                if len(self._memory_cache) >= self.MAX_MEMORY_CACHE_SIZE:
                    sorted_keys = sorted(self._memory_cache.keys(), key=lambda k: self._memory_cache[k][1])
                    for k in sorted_keys[: max(1, self.MAX_MEMORY_CACHE_SIZE // 10)]:
                        del self._memory_cache[k]

            self._memory_cache[key] = (envelope, now + effective_hard_ttl)

    # =========================================================================
    # SINGLE-FLIGHT LOCKING (Dogpile / Stampede Protection)
    # =========================================================================

    async def acquire_single_flight_lock(self, key: str, lock_ttl: int = 15) -> bool:
        """
        Acquires single-flight lock (`lock:fetch:{key}`) so only ONE background refresh
        runs concurrently for a given resource.
        """
        lock_key = f"lock:fetch:{key}"
        now = time.time()
        redis_client = await self._get_redis()

        if redis_client:
            try:
                # SET key "1" NX EX lock_ttl
                acquired = await asyncio.wait_for(
                    redis_client.set(lock_key, "1", nx=True, ex=lock_ttl),
                    timeout=settings.REDIS_TIMEOUT,
                )
                return bool(acquired)
            except Exception as e:
                logger.debug("Redis single-flight lock failed for %s: %s", lock_key, e)

        # In-Memory fallback lock
        async with self._get_lock():
            current_expiry = self._memory_locks.get(lock_key, 0.0)
            if now < current_expiry:
                return False
            self._memory_locks[lock_key] = now + lock_ttl
            return True

    async def release_single_flight_lock(self, key: str) -> None:
        """Releases the single-flight fetch lock."""
        lock_key = f"lock:fetch:{key}"
        redis_client = await self._get_redis()
        if redis_client:
            try:
                await asyncio.wait_for(redis_client.delete(lock_key), timeout=settings.REDIS_TIMEOUT)
            except Exception:
                pass
        async with self._get_lock():
            self._memory_locks.pop(lock_key, None)

    # =========================================================================
    # LEADER ELECTION (For In-Process Lifespan Fetcher on Render)
    # =========================================================================

    async def acquire_leader_lock(self, leader_name: str = "market_fetcher", lock_ttl: int = 45) -> bool:
        """
        Acquires distributed leader lock so only ONE application instance runs the in-process fetcher.
        """
        leader_key = f"leader:{leader_name}"
        now = time.time()
        redis_client = await self._get_redis()

        if redis_client:
            try:
                acquired = await asyncio.wait_for(
                    redis_client.set(leader_key, "1", nx=True, ex=lock_ttl),
                    timeout=settings.REDIS_TIMEOUT,
                )
                return bool(acquired)
            except Exception as e:
                logger.debug("Redis leader lock acquisition failed: %s. Using memory leader.", e)

        # In-memory single instance leader
        async with self._get_lock():
            current_exp = self._memory_locks.get(leader_key, 0.0)
            if now < current_exp:
                return False
            self._memory_locks[leader_key] = now + lock_ttl
            return True

    async def renew_leader_lock(self, leader_name: str = "market_fetcher", lock_ttl: int = 45) -> bool:
        """Renews heartbeat on the active leader lock."""
        leader_key = f"leader:{leader_name}"
        now = time.time()
        redis_client = await self._get_redis()

        if redis_client:
            try:
                renewed = await asyncio.wait_for(
                    redis_client.set(leader_key, "1", xx=True, ex=lock_ttl),
                    timeout=settings.REDIS_TIMEOUT,
                )
                return bool(renewed)
            except Exception:
                return False

        async with self._get_lock():
            self._memory_locks[leader_key] = now + lock_ttl
            return True

    # =========================================================================
    # GENERIC JSON GET & SET (Backward-Compatibility)
    # =========================================================================

    async def get_json(self, key: str) -> Optional[Any]:
        """Convenience method returning unpacked data from Redis or in-memory cache."""
        redis_client = await self._get_redis()
        if redis_client:
            try:
                raw = await asyncio.wait_for(redis_client.get(key), timeout=settings.REDIS_TIMEOUT)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        async with self._get_lock():
            item = self._memory_cache.get(key)
            if item and time.time() <= item[1]:
                return item[0]
        return None

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        """Stores arbitrary JSON data with TTL."""
        serialized = self._serialize(value)
        redis_client = await self._get_redis()
        if redis_client:
            try:
                await asyncio.wait_for(
                    redis_client.set(key, serialized, ex=ttl_seconds),
                    timeout=settings.REDIS_TIMEOUT,
                )
            except Exception:
                pass
        async with self._get_lock():
            self._memory_cache[key] = (value, time.time() + ttl_seconds)

    async def get(self, key: str) -> Optional[Any]:
        """Legacy get returning inner data or raw value."""
        data, status, _ = await self.get_envelope(key)
        if status in ("HIT", "STALE"):
            return data
        return None

    async def set(self, key: str, value: Any, ttl_seconds: int = 30) -> None:
        """Legacy set storing as envelope with standard hard TTL."""
        await self.set_envelope(
            key=key,
            data=value,
            soft_ttl=ttl_seconds,
            hard_ttl=ttl_seconds * 10,
            source="legacy_set",
        )

    async def delete(self, key: str) -> None:
        """Deletes key from both Redis and in-memory cache."""
        redis_client = await self._get_redis()
        if redis_client:
            try:
                await asyncio.wait_for(redis_client.delete(key), timeout=settings.REDIS_TIMEOUT)
            except Exception as e:
                logger.debug("Redis delete failed for key %s: %s", key, e)
        async with self._get_lock():
            self._memory_cache.pop(key, None)

    async def delete_pattern(self, pattern: str) -> None:
        """Deletes all keys matching wildcard pattern from both Redis and in-memory cache."""
        import fnmatch
        redis_client = await self._get_redis()
        if redis_client:
            try:
                keys = []
                async for k in redis_client.scan_iter(match=pattern):
                    keys.append(k)
                if keys:
                    await asyncio.wait_for(redis_client.delete(*keys), timeout=settings.REDIS_TIMEOUT)
            except Exception as e:
                logger.debug("Redis delete_pattern failed for %s: %s", pattern, e)
        async with self._get_lock():
            matching_keys = [k for k in self._memory_cache.keys() if fnmatch.fnmatch(k, pattern)]
            for k in matching_keys:
                self._memory_cache.pop(k, None)

    async def health_check(self) -> Dict[str, Any]:
        redis_status = "disabled"
        if settings.REDIS_ENABLED:
            redis_status = "connected" if self._redis_available else "disconnected (using memory fallback)"
        async with self._get_lock():
            memory_keys_count = len(self._memory_cache)
        return {
            "tier": "redis" if self._redis_available else "in_memory",
            "redis_status": redis_status,
            "memory_cache_entries": memory_keys_count,
            "max_memory_capacity": self.MAX_MEMORY_CACHE_SIZE,
        }


# Global singleton instance
market_cache = MarketCache()
