"""
Benchmark Middleware Latency Overhead.

Measures latency of API request processing with vs without the Pure ASGI APM middleware.
"""

import asyncio
import time
import numpy as np
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.infrastructure.observability.asgi_middleware import PureASGIObservabilityMiddleware
from app.infrastructure.observability.route_matcher import route_matcher


async def create_apps():
    # Base App without APM
    base_app = FastAPI()

    @base_app.get("/api/v1/benchmark/{item_id}")
    async def base_endpoint(item_id: int):
        return {"item_id": item_id, "status": "ok"}

    # Instrumented App with Pure ASGI APM Middleware
    apm_app = FastAPI()

    @apm_app.get("/api/v1/benchmark/{item_id}")
    async def apm_endpoint(item_id: int):
        return {"item_id": item_id, "status": "ok"}

    apm_app.add_middleware(PureASGIObservabilityMiddleware)
    route_matcher.initialize_from_app(apm_app)

    return base_app, apm_app


async def run_benchmark(n_iterations: int = 1000):
    base_app, apm_app = await create_apps()

    # Warmup
    async with AsyncClient(transport=ASGITransport(app=base_app), base_url="http://test") as client:
        for i in range(50):
            await client.get(f"/api/v1/benchmark/{i}")

    async with AsyncClient(transport=ASGITransport(app=apm_app), base_url="http://test") as client:
        for i in range(50):
            await client.get(f"/api/v1/benchmark/{i}")

    # Benchmark Base
    base_latencies = []
    async with AsyncClient(transport=ASGITransport(app=base_app), base_url="http://test") as client:
        for i in range(n_iterations):
            t0 = time.perf_counter()
            resp = await client.get(f"/api/v1/benchmark/{i}")
            t1 = time.perf_counter()
            assert resp.status_code == 200
            base_latencies.append((t1 - t0) * 1000.0)  # ms

    # Benchmark APM
    apm_latencies = []
    async with AsyncClient(transport=ASGITransport(app=apm_app), base_url="http://test") as client:
        for i in range(n_iterations):
            t0 = time.perf_counter()
            resp = await client.get(f"/api/v1/benchmark/{i}")
            t1 = time.perf_counter()
            assert resp.status_code == 200
            apm_latencies.append((t1 - t0) * 1000.0)  # ms

    base_mean = np.mean(base_latencies)
    apm_mean = np.mean(apm_latencies)
    delta_mean = apm_mean - base_mean
    pct_overhead = (delta_mean / base_mean) * 100.0 if base_mean > 0 else 0.0

    print("=========================================================")
    print(f"MIDDLEWARE OVERHEAD BENCHMARK RESULTS ({n_iterations} requests)")
    print("=========================================================")
    print(f"Base App Mean Latency:  {base_mean:.4f} ms | p50: {np.percentile(base_latencies, 50):.4f} ms | p95: {np.percentile(base_latencies, 95):.4f} ms | p99: {np.percentile(base_latencies, 99):.4f} ms")
    print(f"APM App Mean Latency:   {apm_mean:.4f} ms | p50: {np.percentile(apm_latencies, 50):.4f} ms | p95: {np.percentile(apm_latencies, 95):.4f} ms | p99: {np.percentile(apm_latencies, 99):.4f} ms")
    print(f"Absolute Latency Delta: {delta_mean:.4f} ms ({delta_mean*1000:.2f} microseconds)")
    print(f"Calculated APM Overhead: {pct_overhead:.2f}%")
    print("=========================================================")


if __name__ == "__main__":
    asyncio.run(run_benchmark(1000))
