"""
Unit tests for internal APM endpoints and chaos fault injection.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_internal_metrics_auth():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing Authorization header -> 401
        res_no_auth = await client.get("/internal/metrics")
        assert res_no_auth.status_code == 401

        # 2. Invalid Token -> 401
        res_bad_auth = await client.get(
            "/internal/metrics",
            headers={"Authorization": "Bearer invalid_secret_token"},
        )
        assert res_bad_auth.status_code == 401

        # 3. Valid Token -> 200
        res_valid = await client.get(
            "/internal/metrics",
            headers={"Authorization": f"Bearer {settings.METRICS_TOKEN}"},
        )
        assert res_valid.status_code == 200
        assert "http_requests_total" in res_valid.text


@pytest.mark.asyncio
async def test_internal_crash_signatures_auth():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing Auth -> 401
        res_no_auth = await client.get("/internal/crash-signatures")
        assert res_no_auth.status_code == 401

        # 2. Valid Auth -> 200
        res_valid = await client.get(
            "/internal/crash-signatures",
            headers={"Authorization": f"Bearer {settings.METRICS_TOKEN}"},
        )
        assert res_valid.status_code == 200
        data = res_valid.json()
        assert "signatures" in data
        assert "count" in data


@pytest.mark.asyncio
async def test_healthz_and_readyz():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_healthz = await client.get("/healthz")
        assert res_healthz.status_code == 200
        assert res_healthz.json().get("status") == "alive"

        res_readyz = await client.get("/readyz")
        assert res_readyz.status_code in (200, 503)


@pytest.mark.asyncio
async def test_chaos_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test CPU burn
        res_cpu = await client.post("/internal/chaos/cpu-burn?seconds=0.1")
        assert res_cpu.status_code == 200

        # Test Memory allocation
        res_mem = await client.post("/internal/chaos/memory-leak?mb=1")
        assert res_mem.status_code == 200

        # Test Latency sleep
        res_sleep = await client.post("/internal/chaos/sleep?seconds=0.1")
        assert res_sleep.status_code == 200
