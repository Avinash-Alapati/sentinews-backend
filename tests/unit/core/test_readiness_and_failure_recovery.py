"""
Unit and integration tests for Deep Readiness Probe (/readyz), Liveness (/healthz),
Database/Redis Disconnect Recovery, and Graceful Lifespan Shutdown.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app, lifespan


@pytest.fixture
async def app_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_liveness_probe_always_succeeds(app_client: AsyncClient):
    """Liveness probe /healthz returns 200 OK indicating the process is running."""
    response = await app_client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"


@pytest.fixture
def mock_db_healthy():
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=True)
    mock_connect_ctx = MagicMock()
    mock_connect_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_connect_ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("sqlalchemy.ext.asyncio.AsyncEngine.connect", return_value=mock_connect_ctx):
        yield mock_conn


@pytest.mark.asyncio
async def test_readiness_probe_healthy_state(app_client: AsyncClient, mock_db_healthy):
    """Readiness probe /readyz returns 200 OK when both PostgreSQL and Redis are responsive."""
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)

    with patch("app.cache.market_cache.market_cache._get_redis", new_callable=AsyncMock, return_value=mock_redis):
        response = await app_client.get("/readyz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["database"] is True
        assert data["redis"] is True


@pytest.mark.asyncio
async def test_readiness_probe_database_down_yields_503(app_client: AsyncClient):
    """When PostgreSQL fails to connect or query times out, /readyz must return 503 Service Unavailable."""
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)

    with patch("sqlalchemy.ext.asyncio.AsyncEngine.connect", side_effect=TimeoutError("Postgres pool timeout")), \
         patch("app.cache.market_cache.market_cache._get_redis", new_callable=AsyncMock, return_value=mock_redis):
        response = await app_client.get("/readyz")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["database"] is False


@pytest.mark.asyncio
async def test_readiness_probe_redis_down_yields_503(app_client: AsyncClient, mock_db_healthy):
    """When Redis ping fails, /readyz must return 503 Service Unavailable if REDIS_ENABLED is True."""
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(side_effect=ConnectionError("Redis connection refused"))

    with patch("app.cache.market_cache.market_cache._get_redis", new_callable=AsyncMock, return_value=mock_redis):
        response = await app_client.get("/readyz")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["redis"] is False


@pytest.mark.asyncio
async def test_readiness_probe_auto_recovery(app_client: AsyncClient):
    """Verify that after transient dependency outage, /readyz automatically recovers to 200 OK."""
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)

    # 1. Transient failure
    with patch("sqlalchemy.ext.asyncio.AsyncEngine.connect", side_effect=ConnectionError("DB network drop")), \
         patch("app.cache.market_cache.market_cache._get_redis", new_callable=AsyncMock, return_value=mock_redis):
        response = await app_client.get("/readyz")
        assert response.status_code == 503

    # 2. Connection restored -> returns 200 immediately
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=True)
    mock_connect_ctx = MagicMock()
    mock_connect_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_connect_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("sqlalchemy.ext.asyncio.AsyncEngine.connect", return_value=mock_connect_ctx), \
         patch("app.cache.market_cache.market_cache._get_redis", new_callable=AsyncMock, return_value=mock_redis):
        response = await app_client.get("/readyz")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_lifespan_graceful_shutdown_cleanup():
    """Verify that lifespan teardown cancels background tasks and invokes close() on resources."""
    mock_app = MagicMock()
    
    with patch("app.cache.market_cache.market_cache.close", new_callable=AsyncMock) as mock_cache_close, \
         patch("app.modules.market_intelligence.infrastructure.fetcher.market_fetcher.close", new_callable=AsyncMock) as mock_fetcher_close:
        
        async with lifespan(mock_app):
            # Inside lifespan context
            pass

        # Verify close methods were cleanly awaited on shutdown
        mock_cache_close.assert_awaited_once()
        mock_fetcher_close.assert_awaited_once()
