"""
Unit tests for Route Template Resolver (RouteMatcher).
"""

from fastapi import FastAPI
from app.infrastructure.observability.route_matcher import RouteMatcher


def test_route_matcher_normalization():
    matcher = RouteMatcher()
    app = FastAPI()

    @app.get("/api/v1/portfolio/{id}")
    async def get_portfolio(id: int):
        return {"id": id}

    @app.post("/api/v1/portfolio/{id}/transactions")
    async def create_tx(id: int):
        return {"id": id}

    @app.get("/api/v1/market/quote/{symbol}")
    async def get_quote(symbol: str):
        return {"symbol": symbol}

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    matcher.initialize_from_app(app)

    # Exact match
    assert matcher.match("/api/v1/health", "GET") == "/api/v1/health"
    assert matcher.match("/api/v1/health/", "GET") == "/api/v1/health"

    # Dynamic single parameter
    assert matcher.match("/api/v1/portfolio/42", "GET") == "/api/v1/portfolio/{id}"
    assert matcher.match("/api/v1/portfolio/999/", "GET") == "/api/v1/portfolio/{id}"

    # Dynamic parameter with subpath
    assert (
        matcher.match("/api/v1/portfolio/10/transactions", "POST")
        == "/api/v1/portfolio/{id}/transactions"
    )

    # String parameter
    assert matcher.match("/api/v1/market/quote/RELIANCE", "GET") == "/api/v1/market/quote/{symbol}"
    assert matcher.match("/api/v1/market/quote/TCS.NS", "GET") == "/api/v1/market/quote/{symbol}"

    # Unmatched / 404 paths
    assert matcher.match("/random/nonexistent/path", "GET") == "unmatched"
    assert matcher.match("/api/v1/unknown", "GET") == "unmatched"
