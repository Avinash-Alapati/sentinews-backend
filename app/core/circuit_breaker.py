"""
Distributed & In-Memory Resilient Circuit Breaker.

Provides failure isolation and self-healing for external financial providers
(Yahoo Finance, Upstox, NSE, Finnhub, Google OAuth).
Shared across instances via Redis with in-process memory fallback.
"""

import asyncio
from enum import Enum
import logging
import time
from typing import Any, Dict, Optional, Tuple

from app.infrastructure.observability.metrics import (
    circuit_breaker_state,
    circuit_breaker_trips_total,
)

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(Exception):
    """Raised when an operation is attempted while the circuit breaker is OPEN."""
    def __init__(self, provider: str, time_remaining: float):
        self.provider = provider
        self.time_remaining = max(0.0, time_remaining)
        super().__init__(
            f"Circuit breaker for provider '{provider}' is OPEN. "
            f"Failing fast for next {self.time_remaining:.1f}s."
        )


class CircuitBreaker:
    """
    Dual-Tier Distributed & In-Memory Circuit Breaker:
    1. Primary: Redis-backed distributed state (multi-instance synchronization).
    2. Fallback: Thread-safe in-process memory state.

    Thresholds:
    - failure_threshold (default 5): Consecutive failures to trip CLOSED -> OPEN.
    - minimum_sample_size (default 5): Minimum total calls in evaluation window before tripping.
    - recovery_timeout (default 60s): Time in OPEN before transitioning to HALF_OPEN probe.
    - half_open_success_threshold (default 2): Consecutive successful probes to transition HALF_OPEN -> CLOSED.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_success_threshold: int = 2,
        minimum_sample_size: int = 5,
        redis_getter: Optional[Any] = None,
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(0.01, float(recovery_timeout))
        self.half_open_success_threshold = max(1, half_open_success_threshold)
        self.minimum_sample_size = max(1, minimum_sample_size)
        self._redis_getter = redis_getter
        self._memory_state: Dict[str, Dict[str, Any]] = {}
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

    def _get_redis_client(self):
        if callable(self._redis_getter):
            try:
                return self._redis_getter()
            except Exception:
                return None
        return None

    def _init_memory_state(self, provider: str) -> Dict[str, Any]:
        if provider not in self._memory_state:
            self._memory_state[provider] = {
                "state": CircuitState.CLOSED.value,
                "failure_count": 0,
                "success_count": 0,
                "opened_at": 0.0,
                "last_failure_at": 0.0,
            }
        return self._memory_state[provider]

    def _update_metrics(self, provider: str, state: CircuitState) -> None:
        try:
            val = 0.0 if state == CircuitState.CLOSED else (1.0 if state == CircuitState.HALF_OPEN else 2.0)
            circuit_breaker_state.labels(provider=provider).set(val)
        except Exception:
            pass

    async def get_state(self, provider: str) -> Tuple[CircuitState, float]:
        """
        Returns (state, time_remaining_in_open).
        Checks if an OPEN circuit has expired its recovery_timeout and should transition to HALF_OPEN.
        """
        now = time.monotonic()
        redis_client = self._get_redis_client()

        if redis_client:
            try:
                state_key = f"cb:state:{provider}"
                opened_key = f"cb:opened_at:{provider}"
                raw_state = await redis_client.get(state_key)
                if raw_state:
                    current_state = CircuitState(raw_state)
                    if current_state == CircuitState.OPEN:
                        opened_at = float(await redis_client.get(opened_key) or 0.0)
                        elapsed = time.time() - opened_at
                        if elapsed >= self.recovery_timeout:
                            # Transition to HALF_OPEN
                            await redis_client.set(state_key, CircuitState.HALF_OPEN.value)
                            await redis_client.set(f"cb:successes:{provider}", 0)
                            self._update_metrics(provider, CircuitState.HALF_OPEN)
                            return CircuitState.HALF_OPEN, 0.0
                        self._update_metrics(provider, CircuitState.OPEN)
                        return CircuitState.OPEN, self.recovery_timeout - elapsed
                    self._update_metrics(provider, current_state)
                    return current_state, 0.0
            except Exception as e:
                logger.debug("Redis circuit breaker read failed for %s: %s; falling back to memory.", provider, e)

        # In-Memory Fallback
        async with self._get_lock():
            st = self._init_memory_state(provider)
            current_state = CircuitState(st["state"])
            if current_state == CircuitState.OPEN:
                elapsed = now - st["opened_at"]
                if elapsed >= self.recovery_timeout:
                    st["state"] = CircuitState.HALF_OPEN.value
                    st["success_count"] = 0
                    self._update_metrics(provider, CircuitState.HALF_OPEN)
                    return CircuitState.HALF_OPEN, 0.0
                self._update_metrics(provider, CircuitState.OPEN)
                return CircuitState.OPEN, max(0.0, self.recovery_timeout - elapsed)
            self._update_metrics(provider, current_state)
            return current_state, 0.0

    async def can_execute(self, provider: str) -> bool:
        """
        Returns True if the circuit allows requests (CLOSED or HALF_OPEN).
        Returns False if OPEN.
        """
        state, _ = await self.get_state(provider)
        return state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    async def record_success(self, provider: str) -> None:
        """
        Records a successful upstream call.
        In HALF_OPEN, increments success count and closes the circuit when threshold is reached.
        In CLOSED, resets failure count.
        """
        redis_client = self._get_redis_client()

        if redis_client:
            try:
                state_key = f"cb:state:{provider}"
                raw_state = await redis_client.get(state_key)
                current_state = CircuitState(raw_state) if raw_state else CircuitState.CLOSED

                if current_state == CircuitState.HALF_OPEN:
                    succ_key = f"cb:successes:{provider}"
                    succ_count = await redis_client.incr(succ_key)
                    if succ_count >= self.half_open_success_threshold:
                        # Recovery successful! Close circuit.
                        await redis_client.set(state_key, CircuitState.CLOSED.value)
                        await redis_client.set(f"cb:failures:{provider}", 0)
                        self._update_metrics(provider, CircuitState.CLOSED)
                        logger.info("Circuit breaker for '%s' recovered and transitioned to CLOSED.", provider)
                else:
                    await redis_client.set(f"cb:failures:{provider}", 0)
            except Exception as e:
                logger.debug("Redis circuit breaker record_success failed for %s: %s", provider, e)

        # In-Memory Fallback
        async with self._get_lock():
            st = self._init_memory_state(provider)
            current_state = CircuitState(st["state"])
            if current_state == CircuitState.HALF_OPEN:
                st["success_count"] += 1
                if st["success_count"] >= self.half_open_success_threshold:
                    st["state"] = CircuitState.CLOSED.value
                    st["failure_count"] = 0
                    st["success_count"] = 0
                    self._update_metrics(provider, CircuitState.CLOSED)
                    logger.info("In-memory circuit breaker for '%s' recovered to CLOSED.", provider)
            else:
                st["failure_count"] = 0

    async def record_failure(self, provider: str, exc: Optional[Exception] = None) -> None:
        """
        Records an upstream error or timeout.
        If in HALF_OPEN or failure count >= threshold, trips the circuit to OPEN.
        """
        mono_now = time.monotonic()
        wall_now = time.time()
        redis_client = self._get_redis_client()

        if redis_client:
            try:
                state_key = f"cb:state:{provider}"
                fail_key = f"cb:failures:{provider}"
                opened_key = f"cb:opened_at:{provider}"

                raw_state = await redis_client.get(state_key)
                current_state = CircuitState(raw_state) if raw_state else CircuitState.CLOSED

                if current_state == CircuitState.HALF_OPEN:
                    # Probe failed in half-open -> Trip immediately back to OPEN
                    await redis_client.set(state_key, CircuitState.OPEN.value)
                    await redis_client.set(opened_key, str(wall_now))
                    self._update_metrics(provider, CircuitState.OPEN)
                    try:
                        circuit_breaker_trips_total.labels(provider=provider).inc()
                    except Exception:
                        pass
                    logger.warning("Circuit breaker probe for '%s' failed in HALF_OPEN. Re-opened for %.1fs. Error: %s",
                                   provider, self.recovery_timeout, exc)
                else:
                    failures = await redis_client.incr(fail_key)
                    if failures >= self.failure_threshold:
                        await redis_client.set(state_key, CircuitState.OPEN.value)
                        await redis_client.set(opened_key, str(wall_now))
                        self._update_metrics(provider, CircuitState.OPEN)
                        try:
                            circuit_breaker_trips_total.labels(provider=provider).inc()
                        except Exception:
                            pass
                        logger.warning("Circuit breaker for '%s' TRIPPED to OPEN after %d consecutive failures. Error: %s",
                                       provider, failures, exc)
            except Exception as e:
                logger.debug("Redis circuit breaker record_failure failed for %s: %s", provider, e)

        # In-Memory Fallback
        async with self._get_lock():
            st = self._init_memory_state(provider)
            current_state = CircuitState(st["state"])
            st["last_failure_at"] = mono_now

            if current_state == CircuitState.HALF_OPEN:
                st["state"] = CircuitState.OPEN.value
                st["opened_at"] = mono_now
                self._update_metrics(provider, CircuitState.OPEN)
                try:
                    circuit_breaker_trips_total.labels(provider=provider).inc()
                except Exception:
                    pass
                logger.warning("In-memory circuit breaker probe for '%s' failed. Re-opened for %.1fs.",
                               provider, self.recovery_timeout)
            else:
                st["failure_count"] += 1
                if st["failure_count"] >= self.failure_threshold:
                    st["state"] = CircuitState.OPEN.value
                    st["opened_at"] = mono_now
                    self._update_metrics(provider, CircuitState.OPEN)
                    try:
                        circuit_breaker_trips_total.labels(provider=provider).inc()
                    except Exception:
                        pass
                    logger.warning("In-memory circuit breaker for '%s' TRIPPED to OPEN after %d failures.",
                                   provider, st["failure_count"])

    async def reset(self, provider: str) -> None:
        """Manually resets circuit breaker for a provider to CLOSED."""
        redis_client = self._get_redis_client()
        if redis_client:
            try:
                await redis_client.set(f"cb:state:{provider}", CircuitState.CLOSED.value)
                await redis_client.set(f"cb:failures:{provider}", 0)
                await redis_client.set(f"cb:successes:{provider}", 0)
            except Exception:
                pass
        async with self._get_lock():
            st = self._init_memory_state(provider)
            st["state"] = CircuitState.CLOSED.value
            st["failure_count"] = 0
            st["success_count"] = 0
            st["opened_at"] = 0.0
        self._update_metrics(provider, CircuitState.CLOSED)


# Global singleton instance (wires to Redis client once initialized)
circuit_breaker = CircuitBreaker()
