"""
Observability Context Variables.

Provides request-scoped context propagation across async tasks, ASGI middleware,
database queries, and outbound HTTP calls.
"""

import contextvars
from typing import Optional

from contextlib import asynccontextmanager
import time
from typing import Dict, Optional

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
endpoint_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("endpoint", default="unknown")
user_id_hash_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("user_id_hash", default=None)
request_start_time_ctx: contextvars.ContextVar[float] = contextvars.ContextVar("request_start_time", default=0.0)
upstream_in_flight_ctx: contextvars.ContextVar[int] = contextvars.ContextVar("upstream_in_flight", default=0)
stage_timings_ctx: contextvars.ContextVar[Optional[Dict[str, float]]] = contextvars.ContextVar("stage_timings", default=None)
cache_status_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("cache_status", default="")


def init_stage_timings() -> Dict[str, float]:
    """Initializes per-request stage timings dict in context."""
    timings = {"auth": 0.0, "db": 0.0, "redis": 0.0, "upstream": 0.0, "serialization": 0.0}
    stage_timings_ctx.set(timings)
    return timings


def get_stage_timings() -> Dict[str, float]:
    """Retrieves the current request stage timings dict."""
    timings = stage_timings_ctx.get()
    if timings is None:
        timings = init_stage_timings()
    return timings


def record_stage_timing(stage: str, duration_ms: float) -> None:
    """Records duration in milliseconds for a specific processing stage."""
    timings = get_stage_timings()
    timings[stage] = timings.get(stage, 0.0) + duration_ms


@asynccontextmanager
async def measure_stage(stage: str):
    """Context manager for asynchronous timing of a processing stage."""
    start = time.perf_counter()
    try:
        yield
    finally:
        dur_ms = (time.perf_counter() - start) * 1000.0
        record_stage_timing(stage, dur_ms)


def set_cache_status(status: str) -> None:
    """Sets the cache status header value for current request."""
    cache_status_ctx.set(status)


def get_cache_status() -> str:
    """Gets current cache status."""
    return cache_status_ctx.get()


def get_request_id() -> str:
    """Return the current request ID or an empty string."""
    return request_id_ctx.get()


def set_request_id(request_id: str) -> None:
    """Set the current request ID."""
    request_id_ctx.set(request_id)


def get_current_endpoint() -> str:
    """Return the normalized endpoint route template for the current request."""
    return endpoint_ctx.get()


def set_current_endpoint(endpoint: str) -> None:
    """Set the current endpoint route template."""
    endpoint_ctx.set(endpoint)
