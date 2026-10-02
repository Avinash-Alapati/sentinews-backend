"""
Unit tests for Rate Limiting Middleware, multi-tier resolution,
in-memory sliding window, and Redis failure resilience.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.core.config import settings
from app.core.middleware.rate_limit import (
    InMemoryRateLimiter,
    RateLimitMiddleware,
    resolve_rate_limit_tier,
)


# ============================================================================
# 1. Tier Resolution Tests
# ============================================================================

def test_rate_limit_tier_resolution():
    """Verify paths resolve to the correct rate limiting tiers and limits."""
    # Auth endpoints
    tier, limit = resolve_rate_limit_tier("/api/v1/auth/login")
    assert tier == "auth"
    assert limit == settings.RATE_LIMIT_AUTH_PER_MINUTE

    tier, limit = resolve_rate_limit_tier("/api/v1/auth/register")
    assert tier == "auth"

    # OAuth endpoints
    tier, limit = resolve_rate_limit_tier("/api/v1/auth/oauth/google")
    assert tier == "oauth"
    assert limit == settings.RATE_LIMIT_OAUTH_PER_MINUTE

    # News click
    tier, limit = resolve_rate_limit_tier("/api/v1/news/42/click")
    assert tier == "news_click"
    assert limit == settings.RATE_LIMIT_NEWS_CLICK_PER_MINUTE

    # Heavy portfolio recomputations
    tier, limit = resolve_rate_limit_tier("/api/v1/portfolio/10/performance")
    assert tier == "heavy_compute"
    assert limit == settings.RATE_LIMIT_HEAVY_COMPUTE_PER_MINUTE

    # Default / General reads
    tier, limit = resolve_rate_limit_tier("/api/v1/market/quote/RELIANCE")
    assert tier == "default"
    assert limit == settings.RATE_LIMIT_DEFAULT_PER_MINUTE


# ============================================================================
# 2. InMemoryRateLimiter Sliding Window Tests
# ============================================================================

def test_in_memory_limiter_burst_and_exhaustion():
    """Verify in-memory limiter enforces limit within rolling window and calculates reset time."""
    limiter = InMemoryRateLimiter()
    key = "test_client_1"
    limit = 5
    window_sec = 60

    # 1. First 5 requests must succeed
    for i in range(5):
        allowed, remaining, reset_after = limiter.check_and_increment(key, limit=limit, window_sec=window_sec)
        assert allowed is True
        assert remaining == (limit - 1 - i)
        assert 1 <= reset_after <= 61

    # 2. 6th request must be rejected (limit exhausted)
    allowed, remaining, reset_after = limiter.check_and_increment(key, limit=limit, window_sec=window_sec)
    assert allowed is False
    assert remaining == 0
    assert 1 <= reset_after <= 61


def test_in_memory_limiter_rolling_window_expiry():
    """Verify expired timestamps outside the rolling window are pruned."""
    limiter = InMemoryRateLimiter()
    key = "test_client_expire"
    limit = 2
    window_sec = 10

    # Manually populate store with expired timestamps (15s ago)
    now = time.time()
    limiter._store[key] = [now - 15.0, now - 12.0]

    # New request should be accepted because old timestamps are > 10s old
    allowed, remaining, reset_after = limiter.check_and_increment(key, limit=limit, window_sec=window_sec)
    assert allowed is True
    assert remaining == 1
    # Store should now only contain the single current timestamp
    assert len(limiter._store[key]) == 1


# ============================================================================
# 3. Client Key Extraction Tests
# ============================================================================

def test_client_key_extraction_variations():
    """Verify client key extraction prioritizes Token > User ID > X-Forwarded-For > Client IP."""
    dummy_app = AsyncMock()
    middleware = RateLimitMiddleware(app=dummy_app)

    # 1. Bearer token
    scope_token = {
        "headers": [(b"authorization", b"Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz")]
    }
    key_token = middleware._extract_client_key(scope_token)
    assert key_token.startswith("tok:")

    # 2. X-User-ID header
    scope_user = {
        "headers": [(b"x-user-id", b"usr-4491-alpha")]
    }
    key_user = middleware._extract_client_key(scope_user)
    assert key_user == "usr:usr-4491-alpha"

    # 3. X-Forwarded-For (ALB appends real client IP to rightmost position)
    scope_xff = {
        "headers": [(b"x-forwarded-for", b"spoofed.1.2.3.4, spoofed.5.6.7.8, 150.172.238.178")]
    }
    key_xff = middleware._extract_client_key(scope_xff)
    assert key_xff == "ip:150.172.238.178"

    # 4. X-Real-IP
    scope_real_ip = {
        "headers": [(b"x-real-ip", b"198.51.100.22")]
    }
    key_real_ip = middleware._extract_client_key(scope_real_ip)
    assert key_real_ip == "ip:198.51.100.22"

    # 5. Fallback ASGI client tuple
    scope_client = {
        "headers": [],
        "client": ("192.0.2.1", 54321),
    }
    key_client = middleware._extract_client_key(scope_client)
    assert key_client == "ip:192.0.2.1"


def test_x_forwarded_for_spoofing_defense_behind_alb():
    """
    Verifies that an attacker sending rotating spoofed X-Forwarded-For headers
    cannot bypass rate limiting, because the middleware extracts the true connecting IP
    appended by AWS ALB at the rightmost position.
    """
    dummy_app = AsyncMock()
    middleware = RateLimitMiddleware(app=dummy_app)

    # Attacker tries to spoof client IP by changing the front of X-Forwarded-For
    scope_spoof_1 = {
        "headers": [(b"x-forwarded-for", b"10.0.0.1, 198.51.100.77")]
    }
    scope_spoof_2 = {
        "headers": [(b"x-forwarded-for", b"10.0.0.2, 198.51.100.77")]
    }

    ip_1 = middleware._extract_ip(scope_spoof_1)
    ip_2 = middleware._extract_ip(scope_spoof_2)

    assert ip_1 == "198.51.100.77"
    assert ip_2 == "198.51.100.77"
    # Both resolve to the same real connecting IP, preventing rate limit evasion
    assert ip_1 == ip_2


# ============================================================================
# 4. Redis Fallback & Middleware Execution Tests
# ============================================================================

@pytest.mark.asyncio
async def test_middleware_redis_failure_falls_back_to_in_memory():
    """Verify that when Redis raises an exception or times out, the middleware gracefully uses in-memory limiter."""
    mock_redis = MagicMock()
    # Simulate Redis connection failure
    mock_redis.incr = AsyncMock(side_effect=ConnectionError("Redis connection refused"))
    mock_redis.ping = AsyncMock(return_value=True)

    dummy_app = AsyncMock()
    middleware = RateLimitMiddleware(app=dummy_app, redis_getter=lambda: mock_redis)

    # Check rate limit (limit=3)
    for i in range(3):
        allowed, remaining, reset_after = await middleware._check_rate_limit(
            tier="auth",
            client_key="usr:fallback_user",
            limit=3,
            window_sec=60,
        )
        assert allowed is True

    # 4th request must fail via in-memory fallback
    allowed, remaining, reset_after = await middleware._check_rate_limit(
        tier="auth",
        client_key="usr:fallback_user",
        limit=3,
        window_sec=60,
    )
    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio
async def test_middleware_http_429_response_headers(monkeypatch):
    """Verify that 429 response produces correct RFC headers and JSON body."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_PER_MINUTE", 2)

    dummy_app = AsyncMock()
    middleware = RateLimitMiddleware(app=dummy_app)

    client_ip = "198.51.100.77"

    scope = {
        "type": "http",
        "path": "/api/v1/auth/login",
        "headers": [(b"x-real-ip", client_ip.encode("ascii"))],
    }

    sent_messages = []

    async def mock_receive():
        return {"type": "http.request"}

    async def mock_send(msg):
        sent_messages.append(msg)

    # 2 requests pass
    await middleware(scope, mock_receive, mock_send)
    await middleware(scope, mock_receive, mock_send)

    # 3rd request triggers 429
    sent_messages.clear()
    await middleware(scope, mock_receive, mock_send)

    assert len(sent_messages) == 2
    start_msg = sent_messages[0]
    body_msg = sent_messages[1]

    assert start_msg["type"] == "http.response.start"
    assert start_msg["status"] == 429

    headers_dict = {k.decode("latin1"): v.decode("latin1") for k, v in start_msg["headers"]}
    assert "retry-after" in headers_dict
    assert "x-ratelimit-limit" in headers_dict
    assert headers_dict["x-ratelimit-remaining"] == "0"

    body = json.loads(body_msg["body"].decode("utf-8"))
    assert body["error"] == "rate_limit_exceeded"
    assert body["tier"] == "auth"


@pytest.mark.asyncio
async def test_auth_stricter_fallback_and_metric_emission(monkeypatch):
    """Verify that auth tier enforces stricter fallback limit and emits rate_limit_fallback_active_total metric."""
    from app.infrastructure.observability.metrics import rate_limit_fallback_active_total

    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_FALLBACK_PER_MINUTE", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_DEFAULT_PER_MINUTE", 10)

    # In-memory limiter fallback (no redis getter)
    middleware = RateLimitMiddleware(app=AsyncMock(), redis_getter=lambda: None)

    client_key = "usr:test_stricter_auth"

    initial_auth_metric = rate_limit_fallback_active_total.labels(tier="auth")._value.get()
    initial_default_metric = rate_limit_fallback_active_total.labels(tier="default")._value.get()

    # 1. Auth requests: Exactly 3 allowed, 4th rejected
    for _ in range(3):
        allowed, remaining, _ = await middleware._check_rate_limit(
            tier="auth",
            client_key=client_key,
            limit=20,  # Redis limit was 20, but fallback should enforce 3
            window_sec=60,
        )
        assert allowed is True

    allowed, remaining, _ = await middleware._check_rate_limit(
        tier="auth",
        client_key=client_key,
        limit=20,
        window_sec=60,
    )
    assert allowed is False
    assert remaining == 0

    # 2. Standard Read requests: uses standard limit (10)
    read_client_key = "ip:203.0.113.50"
    for _ in range(10):
        allowed, _, _ = await middleware._check_rate_limit(
            tier="default",
            client_key=read_client_key,
            limit=10,
            window_sec=60,
        )
        assert allowed is True

    # 11th request rejected
    allowed, _, _ = await middleware._check_rate_limit(
        tier="default",
        client_key=read_client_key,
        limit=10,
        window_sec=60,
    )
    assert allowed is False

    # 3. Verify Prometheus metrics were incremented
    new_auth_metric = rate_limit_fallback_active_total.labels(tier="auth")._value.get()
    new_default_metric = rate_limit_fallback_active_total.labels(tier="default")._value.get()

    assert new_auth_metric - initial_auth_metric == 4
    assert new_default_metric - initial_default_metric == 11


def test_refresh_tier_resolution_and_limit():
    """Verify refresh endpoint resolves to dedicated 'refresh' tier with configured limit."""
    tier, limit = resolve_rate_limit_tier("/api/v1/auth/refresh")
    assert tier == "refresh"
    assert limit == getattr(settings, "RATE_LIMIT_REFRESH_PER_MINUTE", 30)


@pytest.mark.asyncio
async def test_multiple_accounts_behind_one_ip_isolation():
    """Verify multiple distinct accounts sharing the same client IP maintain independent rate limit quotas."""
    middleware = RateLimitMiddleware(app=AsyncMock(), redis_getter=lambda: None)
    shared_ip = "198.51.100.99"
    limit_per_account = 3

    # Account A consumes all 3 requests
    key_a = f"acct:user_alpha:{shared_ip}"
    for _ in range(limit_per_account):
        allowed, _, _ = await middleware._check_rate_limit(
            tier="auth",
            client_key=key_a,
            limit=limit_per_account,
            window_sec=60,
        )
        assert allowed is True

    # Account A 4th request must be rejected
    allowed_a, _, _ = await middleware._check_rate_limit(
        tier="auth",
        client_key=key_a,
        limit=limit_per_account,
        window_sec=60,
    )
    assert allowed_a is False

    # Account B behind the SAME IP must still have full quota available
    key_b = f"acct:user_bravo:{shared_ip}"
    for _ in range(limit_per_account):
        allowed_b, _, _ = await middleware._check_rate_limit(
            tier="auth",
            client_key=key_b,
            limit=limit_per_account,
            window_sec=60,
        )
        assert allowed_b is True


@pytest.mark.asyncio
async def test_credential_stuffing_defense_one_ip_across_50_accounts():
    """
    Credential Stuffing Defense Test:
    Simulates an attacker using 1 IP attempting login across 50 different user accounts.
    Asserts that although each account is well under the 5 req/min limit,
    the aggregate IP limit (30 req/min) triggers at request 31 and returns HTTP 429.
    """
    mock_app = AsyncMock()
    middleware = RateLimitMiddleware(app=mock_app, redis_getter=lambda: None)

    attacker_ip = "198.51.100.77"
    ip_agg_limit = settings.RATE_LIMIT_AUTH_IP_AGGREGATE_PER_MINUTE  # 30

    # 1. First 30 requests across 30 different accounts succeed
    for i in range(1, ip_agg_limit + 1):
        account_id = f"target_user_{i}@example.com"
        scope = {
            "type": "http",
            "path": "/api/v1/auth/login",
            "headers": [
                (b"x-account-id", account_id.encode("latin1")),
                (b"x-forwarded-for", attacker_ip.encode("latin1")),
            ],
            "client": ("198.51.100.77", 12345),
        }
        sent_messages = []
        async def mock_send(msg):
            sent_messages.append(msg)

        await middleware(scope, AsyncMock(), mock_send)
        # Mock app should have been called (request allowed)
        assert mock_app.call_count == i

    # 2. Requests 31 to 50 from the SAME IP across new accounts must be rejected with 429
    for i in range(ip_agg_limit + 1, 51):
        account_id = f"target_user_{i}@example.com"
        scope = {
            "type": "http",
            "path": "/api/v1/auth/login",
            "headers": [
                (b"x-account-id", account_id.encode("latin1")),
                (b"x-forwarded-for", attacker_ip.encode("latin1")),
            ],
            "client": ("198.51.100.77", 12345),
        }
        sent_messages = []
        async def mock_send(msg):
            sent_messages.append(msg)

        await middleware(scope, AsyncMock(), mock_send)
        # Verify 429 was returned
        start_msg = [m for m in sent_messages if m.get("type") == "http.response.start"][0]
        assert start_msg["status"] == 429


@pytest.mark.asyncio
async def test_distributed_brute_force_defense_one_account_across_distributed_ips():
    """
    Distributed Password Spraying / Brute-Force Defense Test:
    Simulates an attacker using 25 distinct botnet IPs targeting 1 single victim account.
    Asserts that although each IP makes only 1 request, the aggregate Account limit
    (15 req/min) triggers at request 16 and returns HTTP 429.
    """
    mock_app = AsyncMock()
    middleware = RateLimitMiddleware(app=mock_app, redis_getter=lambda: None)

    victim_account = "ceo_account@sentinews.com"
    acct_agg_limit = settings.RATE_LIMIT_AUTH_ACCOUNT_AGGREGATE_PER_MINUTE  # 15

    # 1. First 15 requests from 15 distinct IPs targeting the victim account succeed
    for i in range(1, acct_agg_limit + 1):
        bot_ip = f"198.51.100.{i}"
        scope = {
            "type": "http",
            "path": "/api/v1/auth/login",
            "headers": [
                (b"x-account-id", victim_account.encode("latin1")),
                (b"x-forwarded-for", bot_ip.encode("latin1")),
            ],
            "client": (bot_ip, 12345),
        }
        sent_messages = []
        async def mock_send(msg):
            sent_messages.append(msg)

        await middleware(scope, AsyncMock(), mock_send)
        assert mock_app.call_count == i

    # 2. Requests 16 to 25 from NEW bot IPs targeting the same victim account must be blocked with 429
    for i in range(acct_agg_limit + 1, 26):
        bot_ip = f"198.51.100.{i}"
        scope = {
            "type": "http",
            "path": "/api/v1/auth/login",
            "headers": [
                (b"x-account-id", victim_account.encode("latin1")),
                (b"x-forwarded-for", bot_ip.encode("latin1")),
            ],
            "client": (bot_ip, 12345),
        }
        sent_messages = []
        async def mock_send(msg):
            sent_messages.append(msg)

        await middleware(scope, AsyncMock(), mock_send)
        start_msg = [m for m in sent_messages if m.get("type") == "http.response.start"][0]
        assert start_msg["status"] == 429



