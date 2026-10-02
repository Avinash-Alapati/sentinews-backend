"""
Computational Function Tracking Decorator.

Instruments heavy financial and domain algorithms (XIRR, FIFO cost basis, portfolio weights,
clustering, relevance scoring) across both sync and async functions.
"""

import asyncio
import functools
import time
from typing import Any, Callable
from app.infrastructure.observability.metrics import (
    compute_duration_seconds,
    compute_failures_total,
    compute_in_progress,
)


def track_compute(function_name: str) -> Callable:
    """
    Decorator to track execution duration, concurrency, and error rates of heavy compute algorithms.
    Supports both synchronous and asynchronous functions.

    Args:
        function_name: Identifier for the compute task (e.g. 'portfolio_xirr', 'fifo_cost_basis').
    """

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    compute_in_progress.labels(function=function_name).inc()
                except Exception:
                    pass

                start_time = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as exc:
                    try:
                        compute_failures_total.labels(function=function_name).inc()
                    except Exception:
                        pass
                    raise exc
                finally:
                    duration = time.perf_counter() - start_time
                    try:
                        compute_in_progress.labels(function=function_name).dec()
                        compute_duration_seconds.labels(function=function_name).observe(duration)
                    except Exception:
                        pass

            return async_wrapper

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    compute_in_progress.labels(function=function_name).inc()
                except Exception:
                    pass

                start_time = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as exc:
                    try:
                        compute_failures_total.labels(function=function_name).inc()
                    except Exception:
                        pass
                    raise exc
                finally:
                    duration = time.perf_counter() - start_time
                    try:
                        compute_in_progress.labels(function=function_name).dec()
                        compute_duration_seconds.labels(function=function_name).observe(duration)
                    except Exception:
                        pass

            return sync_wrapper

    return decorator
