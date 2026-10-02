"""
Production Mode Security & Route Lockdown Integration Tests.

Validates:
1. /internal/chaos/* endpoints are NEVER registered when ENVIRONMENT=production (asserts 404).
2. /docs, /redoc, /openapi.json and API V1 docs are disabled in production (asserts 404).
3. /internal/market-reports/generate enforces strong X-Internal-Token / superuser auth (asserts 403 without token).
"""

from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from app.core.config import Settings
from app.infrastructure.observability import setup_observability
from app.api.v1.router import api_router
from app.api.v1.market_reports.router import internal_router as internal_market_reports_router


@pytest.fixture
def prod_app():
    """Constructs a test FastAPI application booted in production mode."""
    prod_settings = Settings(
        ENVIRONMENT="production",
        SECRET_KEY="production_super_secure_key_1234567890123456",
        METRICS_TOKEN="production_metrics_token_1234567890123456",
        INTERNAL_API_SECRET="production_internal_secret_key_123456789012",
        CORS_ORIGINS=["https://sentinews.com"],
        DATABASE_URL="postgresql+asyncpg://sentinews_user:strong_pass@rds-postgres.internal:5432/sentinews_prod",
        SYNC_DATABASE_URL="postgresql://sentinews_user:strong_pass@rds-postgres.internal:5432/sentinews_prod",
        REDIS_URL="redis://elasticache-redis.internal:6379/0",
        ENABLE_DOCS=False,
        DEBUG=False,
    )

    with patch("app.core.config.settings", prod_settings), \
         patch("app.infrastructure.observability.setup.settings", prod_settings), \
         patch("app.api.v1.market_reports.router.settings", prod_settings):

        docs_enabled = (prod_settings.ENVIRONMENT.lower() != "production") and prod_settings.ENABLE_DOCS

        app = FastAPI(
            title="SentiNews Prod Test",
            openapi_url=f"{prod_settings.API_V1_STR}/openapi.json" if docs_enabled else None,
            docs_url=f"{prod_settings.API_V1_STR}/docs" if docs_enabled else None,
            redoc_url=f"{prod_settings.API_V1_STR}/redoc" if docs_enabled else None,
        )

        setup_observability(app)
        app.include_router(api_router, prefix=prod_settings.API_V1_STR)
        app.include_router(internal_market_reports_router)

        yield app, prod_settings


@pytest.mark.asyncio
async def test_chaos_endpoints_not_registered_in_production(prod_app):
    """
    Asserts all /internal/chaos/* endpoints return 404 Not Found in production mode.
    """
    app, prod_settings = prod_app
    chaos_routes = [
        "/internal/chaos/cpu-burn",
        "/internal/chaos/memory-leak",
        "/internal/chaos/db-pool-exhaust",
        "/internal/chaos/sleep",
        "/internal/chaos/unhandled-exception",
        "/internal/chaos/upstream-timeout",
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for route in chaos_routes:
            resp = await client.get(route)
            assert resp.status_code == 404, f"Route {route} was accessible in production (expected 404, got {resp.status_code})"


@pytest.mark.asyncio
async def test_api_docs_and_openapi_disabled_in_production(prod_app):
    """
    Asserts /docs, /redoc, and /openapi.json (both root and api/v1 prefixed) return 404 in production.
    """
    app, prod_settings = prod_app
    doc_routes = [
        "/docs",
        "/redoc",
        "/openapi.json",
        f"{prod_settings.API_V1_STR}/docs",
        f"{prod_settings.API_V1_STR}/redoc",
        f"{prod_settings.API_V1_STR}/openapi.json",
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for route in doc_routes:
            resp = await client.get(route)
            assert resp.status_code == 404, f"Docs route {route} returned {resp.status_code} in production (expected 404)"


@pytest.mark.asyncio
async def test_internal_market_reports_strong_token_enforcement(prod_app):
    """
    Asserts /internal/market-reports/generate denies unauthenticated callers with 403 Forbidden,
    rejects invalid tokens, and authorizes requests providing the strong internal token.
    """
    app, prod_settings = prod_app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. No auth headers -> 403 Forbidden
        resp_no_auth = await client.post(
            "/internal/market-reports/generate",
            json={"report_type": "PRE_MARKET"},
        )
        assert resp_no_auth.status_code == 403

        # 2. Invalid internal token -> 403 Forbidden
        resp_bad_token = await client.post(
            "/internal/market-reports/generate",
            headers={"X-Internal-Secret": "invalid_wrong_token_123"},
            json={"report_type": "PRE_MARKET"},
        )
        assert resp_bad_token.status_code == 403

        # 3. Valid strong internal token -> Authorized (passes auth check)
        from app.api.v1.market_reports.dependencies import get_generate_pre_market_use_case
        from app.modules.market_reports.domain.entities import MarketReport
        from app.modules.market_reports.domain.enums import ReportStatus, ReportType
        from datetime import date, datetime, timezone

        mock_use_case = AsyncMock()
        mock_use_case.execute.return_value = MarketReport(
            id=1,
            report_type=ReportType.PRE_MARKET,
            report_date=date(2026, 10, 1),
            status=ReportStatus.PUBLISHED,
            generated_at=datetime.now(timezone.utc),
            sections={},
            disclaimer="SEBI Disclaimer",
            source_providers=["finnhub"],
            is_partial=False,
            error_details=None,
        )
        app.dependency_overrides[get_generate_pre_market_use_case] = lambda: mock_use_case

        resp_valid = await client.post(
            "/internal/market-reports/generate",
            headers={"X-Internal-Secret": prod_settings.INTERNAL_API_SECRET},
            json={"report_type": "PRE_MARKET"},
        )
        assert resp_valid.status_code == 200, f"Expected 200 OK with valid X-Internal-Secret, got {resp_valid.status_code}: {resp_valid.text}"
        data = resp_valid.json()
        assert data["id"] == 1
        assert data["status"] == "PUBLISHED"
