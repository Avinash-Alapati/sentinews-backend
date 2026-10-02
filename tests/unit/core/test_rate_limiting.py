"""
Unit and Integration Tests for Rate Limiting Middleware.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.middleware.rate_limit import InMemoryRateLimiter, resolve_rate_limit_tier


def test_resolve_rate_limit_tiers():
    assert resolve_rate_limit_tier("/api/v1/auth/login")[0] == "auth"
    assert resolve_rate_limit_tier("/api/v1/auth/register")[0] == "auth"
    assert resolve_rate_limit_tier("/api/v1/auth/google/callback")[0] == "oauth"
    assert resolve_rate_limit_tier("/api/v1/portfolio/oauth/upstox")[0] == "oauth"
    assert resolve_rate_limit_tier("/api/v1/news/123/click")[0] == "news_click"
    assert resolve_rate_limit_tier("/api/v1/portfolio/1/performance")[0] == "heavy_compute"
    assert resolve_rate_limit_tier("/api/v1/market/quote/RELIANCE.NS")[0] == "default"


def test_in_memory_rate_limiter():
    limiter = InMemoryRateLimiter()
    key = "test_client_1"
    limit = 3

    allowed, remaining, reset = limiter.check_and_increment(key, limit=limit, window_sec=60)
    assert allowed is True
    assert remaining == 2

    allowed, remaining, reset = limiter.check_and_increment(key, limit=limit, window_sec=60)
    assert allowed is True
    assert remaining == 1

    allowed, remaining, reset = limiter.check_and_increment(key, limit=limit, window_sec=60)
    assert allowed is True
    assert remaining == 0

    # 4th request should be rejected
    allowed, remaining, reset = limiter.check_and_increment(key, limit=limit, window_sec=60)
    assert allowed is False
    assert remaining == 0
    assert reset > 0


from unittest.mock import AsyncMock, patch
from app.modules.auth.application.use_cases.login import InvalidCredentialsError


@pytest.mark.asyncio
async def test_rate_limit_middleware_headers_and_rejection():
    with patch(
        "app.modules.auth.application.use_cases.login.LoginUseCase.execute",
        AsyncMock(side_effect=InvalidCredentialsError()),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Health endpoint should be excluded
            res = await client.get("/health")
            assert res.status_code == 200

            # Unique IP for testing rate limit rejection
            test_ip = "198.51.100.42"
            headers = {"x-forwarded-for": test_ip}

            # Auth endpoint has 5 req/min limit
            for i in range(5):
                res = await client.post("/api/v1/auth/login", json={"email": "bad@example.com", "password": "pass"}, headers=headers)
                # Response may be 401 or 422, but not 429
                assert res.status_code != 429
                assert "x-ratelimit-limit" in res.headers
                assert "x-ratelimit-remaining" in res.headers

            # 6th request should hit rate limit (429)
            res_429 = await client.post("/api/v1/auth/login", json={"email": "bad@example.com", "password": "pass"}, headers=headers)
            assert res_429.status_code == 429
            assert "retry-after" in res_429.headers
            assert res_429.json()["error"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_rate_limit_loadtest_header_bypass():
    from starlette.responses import JSONResponse
    from app.core.middleware.rate_limit import RateLimitMiddleware

    async def dummy_app(scope, receive, send):
        response = JSONResponse({"status": "ok"})
        await response(scope, receive, send)

    middleware = RateLimitMiddleware(app=dummy_app)
    transport = ASGITransport(app=middleware)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        test_ip = "198.51.100.99"
        headers = {"x-forwarded-for": test_ip, "x-loadtest": "true"}

        # Even with 10 consecutive requests on an auth endpoint (limit: 5), x-loadtest bypasses
        for _ in range(10):
            res = await client.post("/api/v1/auth/login", headers=headers)
            assert res.status_code == 200
            assert res.json() == {"status": "ok"}
