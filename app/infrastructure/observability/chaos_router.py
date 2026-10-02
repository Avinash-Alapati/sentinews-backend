"""
Chaos Engineering Endpoints Router.

Provides controlled fault injection endpoints to simulate CPU spikes, memory leaks,
database pool exhaustion, artificial latency, unhandled exceptions, and upstream timeouts.
Disabled in production environments.
"""

import asyncio
import time
from typing import List
from fastapi import APIRouter, HTTPException, Query, status
import httpx

from app.core.config import settings

router = APIRouter(prefix="/internal/chaos", tags=["Chaos Engineering"])

# In-memory leak bucket for simulation
_leaked_memory_blocks: List[bytearray] = []


@router.post("/cpu-burn", summary="Simulate CPU Burn Spike")
async def chaos_cpu_burn(seconds: float = Query(1.0, ge=0.1, le=10.0)):
    """Burns CPU cycles on a worker thread for the specified duration."""
    if settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=403, detail="Chaos endpoints disabled in production")

    def _burn():
        end_time = time.perf_counter() + seconds
        x = 0.0
        while time.perf_counter() < end_time:
            x += 1.000001 * 1.000002

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _burn)
    return {"message": f"Burned CPU for {seconds}s"}


@router.post("/memory-leak", summary="Simulate Memory Leak")
async def chaos_memory_leak(mb: int = Query(10, ge=1, le=100)):
    """Allocates bytearrays in memory to test memory RSS threshold alerts."""
    if settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=403, detail="Chaos endpoints disabled in production")

    block = bytearray(mb * 1024 * 1024)
    _leaked_memory_blocks.append(block)
    return {
        "message": f"Allocated {mb}MB of memory",
        "total_leaked_blocks": len(_leaked_memory_blocks),
    }


@router.post("/db-pool-exhaust", summary="Simulate DB Pool Exhaustion")
async def chaos_db_pool_exhaust(hold_seconds: float = Query(2.0, ge=0.5, le=15.0)):
    """Acquires a database connection and holds it sleeping."""
    if settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=403, detail="Chaos endpoints disabled in production")

    from app.db.session import engine
    from sqlalchemy import text

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        await asyncio.sleep(hold_seconds)

    return {"message": f"Held DB connection for {hold_seconds}s"}


@router.post("/sleep", summary="Simulate Artificial Latency")
async def chaos_sleep(seconds: float = Query(1.0, ge=0.1, le=10.0)):
    """Simulates slow endpoint latency."""
    if settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=403, detail="Chaos endpoints disabled in production")

    await asyncio.sleep(seconds)
    return {"message": f"Slept for {seconds}s"}


@router.post("/unhandled-exception", summary="Simulate Unhandled Exception Crash")
async def chaos_unhandled_exception(exception_type: str = Query("ValueError")):
    """Throws an unhandled exception to test crash signature hashing and error metrics."""
    if settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=403, detail="Chaos endpoints disabled in production")

    if exception_type == "ZeroDivisionError":
        _ = 1 / 0
    elif exception_type == "KeyError":
        d = {}
        _ = d["non_existent_key"]
    elif exception_type == "MemoryError":
        raise MemoryError("Simulated Out of Memory exception")
    else:
        raise ValueError("Simulated unhandled ValueError in chaos router")


@router.post("/upstream-timeout", summary="Simulate Upstream HTTP Timeout")
async def chaos_upstream_timeout(timeout_seconds: float = Query(0.01, ge=0.001, le=5.0)):
    """Makes an outbound HTTP request with an unrealistically short timeout."""
    if settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=403, detail="Chaos endpoints disabled in production")

    from app.infrastructure.observability.http_tracer import create_traced_async_client

    async with create_traced_async_client(provider="yahoo_finance", timeout=timeout_seconds) as client:
        try:
            # Hit an address that won't respond in 10ms
            await client.get("https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI")
        except httpx.TimeoutException:
            return {"message": "Upstream timeout successfully triggered and recorded in APM"}

    return {"message": "Request completed"}
