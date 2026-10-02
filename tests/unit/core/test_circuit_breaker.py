"""
Unit tests for Distributed & In-Memory Circuit Breaker.
"""

import asyncio
import time
import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitState


@pytest.mark.asyncio
async def test_circuit_breaker_closed_by_default():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.05, half_open_success_threshold=2)
    can_run = await cb.can_execute("test_provider")
    state, remaining = await cb.get_state("test_provider")

    assert can_run is True
    assert state == CircuitState.CLOSED
    assert remaining == 0.0


@pytest.mark.asyncio
async def test_circuit_breaker_trips_to_open_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.05, half_open_success_threshold=2)

    # 1st failure
    await cb.record_failure("yahoo", RuntimeError("timeout"))
    assert await cb.can_execute("yahoo") is True

    # 2nd failure
    await cb.record_failure("yahoo", RuntimeError("503 Service Unavailable"))
    assert await cb.can_execute("yahoo") is True

    # 3rd failure -> Trip to OPEN
    await cb.record_failure("yahoo", RuntimeError("429 Too Many Requests"))
    assert await cb.can_execute("yahoo") is False

    state, remaining = await cb.get_state("yahoo")
    assert state == CircuitState.OPEN
    assert remaining > 0.0


@pytest.mark.asyncio
async def test_circuit_breaker_transitions_to_half_open_and_recovers():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05, half_open_success_threshold=2)

    await cb.record_failure("upstox", RuntimeError("network error"))
    await cb.record_failure("upstox", RuntimeError("network error"))
    assert await cb.can_execute("upstox") is False

    # Wait for recovery timeout
    await asyncio.sleep(0.1)

    # First probe should be allowed (HALF_OPEN)
    assert await cb.can_execute("upstox") is True
    state, _ = await cb.get_state("upstox")
    assert state == CircuitState.HALF_OPEN

    # 1st success in half-open
    await cb.record_success("upstox")
    assert await cb.can_execute("upstox") is True

    # 2nd success in half-open -> Circuit closes
    await cb.record_success("upstox")
    state, _ = await cb.get_state("upstox")
    assert state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_probe_failure_reopens_circuit():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05, half_open_success_threshold=2)

    await cb.record_failure("nse", RuntimeError("500 error"))
    await cb.record_failure("nse", RuntimeError("500 error"))
    assert await cb.can_execute("nse") is False

    # Wait for recovery timeout
    await asyncio.sleep(0.1)

    # Probe allowed
    assert await cb.can_execute("nse") is True

    # Probe fails -> Trips back to OPEN immediately
    await cb.record_failure("nse", RuntimeError("probe failed"))
    assert await cb.can_execute("nse") is False
    state, _ = await cb.get_state("nse")
    assert state == CircuitState.OPEN
