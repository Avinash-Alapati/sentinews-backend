"""
Unit tests for @track_compute decorator (sync & async) and metrics.
"""

import asyncio
import pytest
from app.infrastructure.observability.decorators import track_compute
from app.infrastructure.observability.metrics import (
    compute_duration_seconds,
    compute_failures_total,
    compute_in_progress,
)


def test_track_compute_sync_success():
    @track_compute("test_sync_fn")
    def compute_squares(n: int) -> int:
        """Calculate sum of squares."""
        return sum(i * i for i in range(n))

    assert compute_squares.__doc__ == "Calculate sum of squares."
    assert compute_squares.__name__ == "compute_squares"

    res = compute_squares(100)
    assert res == 328350


def test_track_compute_sync_failure():
    @track_compute("test_sync_fail")
    def failing_fn():
        raise ValueError("Intentional sync failure")

    with pytest.raises(ValueError, match="Intentional sync failure"):
        failing_fn()


@pytest.mark.asyncio
async def test_track_compute_async_success():
    @track_compute("test_async_fn")
    async def async_compute(val: int) -> int:
        """Async multiplier."""
        await asyncio.sleep(0.01)
        return val * 2

    assert async_compute.__doc__ == "Async multiplier."
    assert async_compute.__name__ == "async_compute"

    res = await async_compute(21)
    assert res == 42


@pytest.mark.asyncio
async def test_track_compute_async_failure():
    @track_compute("test_async_fail")
    async def async_fail():
        await asyncio.sleep(0.01)
        raise RuntimeError("Intentional async failure")

    with pytest.raises(RuntimeError, match="Intentional async failure"):
        await async_fail()
