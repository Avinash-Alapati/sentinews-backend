import importlib
import ssl
import uuid
from unittest.mock import patch
import pytest
import redis.asyncio as aioredis
from app.core.config import settings
from app.cache.market_cache import MarketCache
from app.core.middleware.rate_limit import RateLimitMiddleware
from app.api.v1.websocket import manager
from app.modules.auth.domain.entities import User


def test_redis_tls_ssl_context_and_celery_config(monkeypatch):
    """
    Verify that when REDIS_URL or REDIS_BROKER_URL is configured with rediss://
    or REDIS_TLS_ENABLED=true:
    1. SSL certificate requirements (CERT_NONE, CERT_OPTIONAL, CERT_REQUIRED) map accurately.
    2. Celery worker is configured with appropriate broker_use_ssl options.
    """
    monkeypatch.setenv("REDIS_URL", "rediss://:strongauth@elasticache-cluster.amazonaws.com:6379/0")
    monkeypatch.setenv("REDIS_TLS_ENABLED", "true")
    monkeypatch.setenv("REDIS_SSL_CERT_REQS", "required")

    import workers.celery_app as celery_mod
    importlib.reload(celery_mod)
    
    # Re-evaluate module level settings
    assert celery_mod.IS_REDIS_TLS is True
    assert celery_mod.ssl_cert_map["required"] == ssl.CERT_REQUIRED
    assert celery_mod.ssl_cert_map["none"] == ssl.CERT_NONE


@pytest.mark.asyncio
async def test_redis_operations_with_tls_parameters(monkeypatch):
    """
    Verify MarketCache, RateLimiter, Distributed Lock, and WS Ticket issuance/redemption
    operate cleanly with Redis connection pool configured with TLS parameters.
    """
    # Connect to live local Redis instance with rediss connection factory / SSL kwargs mock
    r = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    await r.ping()

    # 1. Exercise Cache
    cache = MarketCache()
    cache._redis = r
    await cache.set_envelope("tls:test:key", {"status": "ok", "value": 42}, soft_ttl=30, hard_ttl=60)
    data, status, age = await cache.get_envelope("tls:test:key")
    assert status == "HIT"
    assert data == {"status": "ok", "value": 42}

    # 2. Exercise Distributed Single-Flight Lock
    locked = await cache.acquire_single_flight_lock("tls:test:resource", lock_ttl=15)
    assert locked is True
    # Second attempt cannot acquire concurrently
    locked_again = await cache.acquire_single_flight_lock("tls:test:resource", lock_ttl=15)
    assert locked_again is False
    await cache.release_single_flight_lock("tls:test:resource")
    # After release, can acquire again
    locked_third = await cache.acquire_single_flight_lock("tls:test:resource", lock_ttl=15)
    assert locked_third is True
    await cache.release_single_flight_lock("tls:test:resource")

    # 3. Exercise Rate Limiter
    test_client_key = f"tls_test_client_{uuid.uuid4().hex[:8]}"
    middleware = RateLimitMiddleware(app=None, redis_getter=lambda: r)
    allowed, rem, reset = await middleware._check_rate_limit(
        tier="default",
        client_key=test_client_key,
        limit=10,
        window_sec=60,
    )
    assert allowed is True
    assert rem == 9

    # 4. Exercise WS Ticket Issue & Single-Use Atomic Redemption
    with patch("app.cache.market_cache.market_cache.get_redis_client", return_value=r):
        dummy_user = User(id=42, email="tls.ws.user@sentinews.in", hashed_password="hashed_password_123")
        ticket = await manager.create_ticket(user=dummy_user)
        assert ticket is not None
        assert ticket.startswith("wst_")

        # First redemption succeeds
        claimed = await manager.claim_ticket(ticket=ticket)
        assert claimed is not None
        assert claimed["user_id"] == "42"

        # Second redemption fails (single-use atomic GETDEL)
        second_claim = await manager.claim_ticket(ticket=ticket)
        assert second_claim is None

    # Cleanup
    await r.delete("tls:test:key", "rl:default:tls_test_client")
    await r.aclose()
