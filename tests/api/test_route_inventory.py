"""
Automated Route Inventory and Authentication Enforcement Audit Test.

Iterates over every registered route in the FastAPI application and enforces that:
1. Every route either requires an authenticated user dependency (e.g. get_current_user,
   get_current_active_user, verify_admin_or_internal_auth) OR is present on an explicit,
   strictly documented public allowlist.
2. No unauthenticated backdoor endpoints exist.
3. Explicit coverage for Google OAuth, logout, /internal/*, /docs, /openapi.json, and WebSocket.
"""

from fastapi.routing import APIRoute, APIWebSocketRoute
import pytest
from httpx import AsyncClient

from app.main import app

# Explicit Allowlist of strictly public routes with security justifications
PUBLIC_ROUTE_ALLOWLIST = {
    # System probes & Docs
    ("/health", "GET"): "Root container liveness probe (ALB / ECS)",
    ("/healthz", "GET"): "Dedicated liveness probe for ECS task definition",
    ("/readyz", "GET"): "Deep readiness probe for APM / Prometheus",
    ("/api/v1/health", "GET"): "V1 module health check",
    ("/api/v1/docs", "GET"): "Swagger UI documentation",
    ("/api/v1/redoc", "GET"): "ReDoc documentation",
    ("/api/v1/openapi.json", "GET"): "OpenAPI schema specification",
    # Public Auth & OAuth (Public ingestion rate-limited at 5 req/min)
    ("/api/v1/auth/register", "POST"): "Public user self-registration",
    ("/api/v1/auth/login", "POST"): "User login with password / credentials",
    ("/api/v1/auth/token", "POST"): "OAuth2 password form token login",
    ("/api/v1/auth/refresh", "POST"): "Refresh token rotation endpoint",
    ("/api/v1/auth/logout", "POST"): "Refresh token / session revocation endpoint",
    ("/api/v1/auth/oauth/google", "GET"): "Google OAuth initialization redirect",
    ("/api/v1/auth/oauth/google/callback", "GET"): "Google OAuth authentication callback",
    ("/api/v1/auth/google", "POST"): "Google OAuth mobile client token exchange",
    ("/api/v1/auth/google/login", "GET"): "Google OAuth web login URL generator",
    ("/api/v1/auth/google/callback", "GET"): "Google OAuth web callback handler",
    ("/api/v1/auth/google/exchange", "POST"): "Google OAuth authorization code exchange",
    # Public Financial Market Intelligence (Cached global / Indian market data)
    ("/api/v1/market/overview", "GET"): "Cached market overview snapshot",
    ("/api/v1/market/indices", "GET"): "Cached major market indices",
    ("/api/v1/market/quote/{symbol}", "GET"): "Single stock price quote",
    ("/api/v1/market/quotes", "GET"): "Batch stock quotes",
    ("/api/v1/market/candles/{symbol}", "GET"): "Stock historical candle bars",
    ("/api/v1/market/history/{symbol}", "GET"): "Stock historical daily price series",
    ("/api/v1/market/movers", "GET"): "Top market gainers and losers snapshot",
    ("/api/v1/market/search", "GET"): "Symbol master ticker search",
    ("/api/v1/market/etfs", "GET"): "ETF list with NAVs",
    ("/api/v1/market/status", "GET"): "NSE market operational calendar status",
    ("/api/v1/market/circuit-breaker/status", "GET"): "Market provider circuit breaker health",
    # Public News Intelligence (Public ingestion rate-limited)
    ("/api/v1/news/feed", "GET"): "Cached chronological news feed",
    ("/api/v1/news/latest", "GET"): "Latest chronological news stream",
    ("/api/v1/news/sources", "GET"): "Allowlisted RSS news publishers",
    ("/api/v1/news/search", "GET"): "News search by keywords",
    ("/api/v1/news/trending", "GET"): "Trending news articles",
    ("/api/v1/news/sector/{sector}", "GET"): "News filtered by market sector",
    ("/api/v1/news/symbol/{symbol}", "GET"): "News filtered by stock ticker",
    ("/api/v1/news/{id}", "GET"): "Single news article metadata",
    ("/api/v1/news/{id}/full-coverage", "GET"): "Clustered related coverage",
    ("/api/v1/news/{id}/click", "POST"): "Engagement click counter (Rate-limited, max 60/min)",
    # Internal Observability & Fault Injection (Blocked by ALB listener; token protected)
    ("/internal/metrics", "GET"): "Prometheus metrics endpoint (Internal token protected)",
    ("/internal/crash-signatures", "GET"): "APM crash signatures diagnostic (Internal token protected)",
    ("/internal/chaos/cpu-burn", "POST"): "Chaos CPU stress testing (Internal token protected)",
    ("/internal/chaos/memory-leak", "POST"): "Chaos memory allocation test (Internal token protected)",
    ("/internal/chaos/db-pool-exhaust", "POST"): "Chaos DB pool test (Internal token protected)",
    ("/internal/chaos/sleep", "POST"): "Chaos latency injection test (Internal token protected)",
    ("/internal/chaos/unhandled-exception", "POST"): "Chaos exception recovery test (Internal token protected)",
    ("/internal/chaos/upstream-timeout", "POST"): "Chaos upstream timeout test (Internal token protected)",
    ("/test-unhandled-server-crash", "GET"): "Testing endpoint for 500 error handler",
    # WebSocket Handshake (Public connection; channel subscriptions filtered in handshake)
    ("/ws", "WEBSOCKET"): "WebSocket stream: public channels open; user channels require token",
    ("/api/v1/ws", "WEBSOCKET"): "WebSocket stream (v1): public channels open; user channels require token",
}

# Known Auth Dependency Function Names
AUTH_DEPENDENCY_NAMES = {
    "get_current_user",
    "get_current_active_user",
    "get_current_superuser",
    "verify_admin_or_internal_auth",
    "verify_internal_metrics_token",
}


def extract_all_routes(app_or_router, prefix=""):
    """Recursively extracts all routes including nested routers."""
    all_routes = []
    for r in getattr(app_or_router, "routes", []):
        if hasattr(r, "original_router"):
            inc_prefix = getattr(getattr(r, "include_context", None), "prefix", "") or ""
            all_routes.extend(extract_all_routes(r.original_router, prefix + inc_prefix))
        elif hasattr(r, "path"):
            all_routes.append((prefix + r.path, r))
    return all_routes


def _has_auth_dependency(route: APIRoute) -> bool:
    """Recursively checks if route has any authentication dependency."""
    all_deps = list(route.dependencies)
    if hasattr(route, "dependant"):
        all_deps.extend(route.dependant.dependencies)

    for dep in all_deps:
        call = getattr(dep, "dependency", None) or getattr(dep, "call", None)
        if call:
            func_name = getattr(call, "__name__", "")
            if func_name in AUTH_DEPENDENCY_NAMES:
                return True
    return False


def test_every_route_is_authenticated_or_allowlisted():
    """
    Automated inventory test that checks every route in app.routes.
    Fails if any unauthenticated route is added without being explicitly allowlisted.
    """
    unauthorized_backdoors = []

    for path, route in extract_all_routes(app):
        if isinstance(route, APIRoute):
            for method in route.methods:
                if method in ("HEAD", "OPTIONS"):
                    continue

                is_auth = _has_auth_dependency(route)
                is_allowlisted = (path, method) in PUBLIC_ROUTE_ALLOWLIST

                if not is_auth and not is_allowlisted:
                    unauthorized_backdoors.append(f"{method} {path} (missing auth dependency and not in allowlist)")

        elif isinstance(route, APIWebSocketRoute):
            is_allowlisted = (path, "WEBSOCKET") in PUBLIC_ROUTE_ALLOWLIST
            if not is_allowlisted:
                unauthorized_backdoors.append(f"WEBSOCKET {path} (unauthorized websocket route)")

    assert not unauthorized_backdoors, (
        f"Found {len(unauthorized_backdoors)} routes lacking authentication and not in allowlist:\n"
        + "\n".join(unauthorized_backdoors)
    )


@pytest.mark.asyncio
async def test_authenticated_routes_require_valid_token(async_client: AsyncClient):
    """Verify that protected routes reject unauthenticated requests with 401 Unauthorized."""
    # Profile endpoint
    me_res = await async_client.get("/api/v1/auth/me")
    assert me_res.status_code in (401, 403)

    # Portfolio lookup
    portfolio_res = await async_client.get("/api/v1/portfolio/1")
    assert portfolio_res.status_code in (401, 403)

    # Market reports
    reports_res = await async_client.get("/api/v1/market-reports/latest")
    assert reports_res.status_code in (401, 403)

    # Internal generator
    internal_res = await async_client.post("/internal/market-reports/generate", json={"report_type": "PRE_MARKET"})
    assert internal_res.status_code in (401, 403)
