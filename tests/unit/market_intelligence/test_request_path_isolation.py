"""
Strict Isolation Verification Test: Request Path vs Background Fetchers.

Guarantees that NO user-facing HTTP request to /api/v1/market/* endpoints
ever executes an external API call inline or awaits a live third-party provider.
"""

import pytest
from unittest.mock import AsyncMock, patch
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.cache.market_cache import market_cache
from app.infrastructure.observability.metrics import external_calls_in_request_path_total


def forbidden_call(*args, **kwargs):
    raise RuntimeError("CRITICAL SLA VIOLATION: Live external API call attempted inside request path!")


@pytest.fixture(autouse=True)
def poison_all_external_providers(monkeypatch):
    """
    Poisons all external provider methods to immediately raise if any user
    request attempts to make an outbound external network call inline.
    """
    provider_targets = [
        "app.integrations.market.indian_market_provider.IndianMarketProvider.get_market_overview",
        "app.integrations.market.indian_market_provider.IndianMarketProvider.get_indices",
        "app.integrations.market.indian_market_provider.IndianMarketProvider.get_quotes",
        "app.integrations.market.indian_market_provider.IndianMarketProvider.get_historical_candles",
        "app.integrations.market.upstox_provider.UpstoxMarketProvider.get_market_overview",
        "app.integrations.market.upstox_provider.UpstoxMarketProvider.get_indices",
        "app.integrations.market.upstox_provider.UpstoxMarketProvider.get_quotes",
        "app.integrations.market.upstox_provider.UpstoxMarketProvider.get_historical_candles",
        "app.modules.market_reports.infrastructure.adapters.nse_market_data_adapter.NSEMarketDataProvider.get_etfs",
    ]
    for target in provider_targets:
        monkeypatch.setattr(target, AsyncMock(side_effect=forbidden_call))


@pytest.mark.asyncio
async def test_all_market_endpoints_zero_external_calls():
    """
    Tests every /api/v1/market/* endpoint to verify zero external calls
    and proper cache responses with headers.
    """
    # 1. Warm some test cache data
    overview_data = {
        "market_status": "OPEN",
        "status_message": "Normal Trading",
        "major_indices": [{"symbol": "^NSEI", "name": "NIFTY 50", "current_value": 25100.0, "change": 120.0, "change_percent": 0.48}],
        "top_gainers": [{"symbol": "INFY", "company_name": "Infosys", "current_price": 1900.0, "change": 40.0, "change_percent": 2.1}],
        "top_losers": [],
        "most_active": [],
    }
    await market_cache.set_envelope("mkt:overview:all:20", overview_data, soft_ttl=60, hard_ttl=1800, source="test")
    await market_cache.set_envelope("mkt:quote:INFY", {
        "symbol": "INFY",
        "company_name": "Infosys Ltd",
        "exchange": "NSE",
        "currency": "INR",
        "current_price": 1900.0,
        "change": 40.0,
        "change_percent": 2.1,
    }, soft_ttl=60, hard_ttl=1800, source="test")
    await market_cache.set_envelope("mkt:candles:INFY:1d:1mo", {
        "symbol": "INFY",
        "interval": "1d",
        "range": "1mo",
        "candles": [{"timestamp": 1700000000, "open": 1850.0, "high": 1910.0, "low": 1840.0, "close": 1900.0, "volume": 500000}],
    }, soft_ttl=60, hard_ttl=1800, source="test")
    await market_cache.set_envelope("mkt:etfs:all", [
        {"symbol": "NIFTYBEES", "underlying_asset": "NIFTY 50", "category": "Equity - Broad Market", "last_price": 270.0, "change": 1.2, "change_percent": 0.45}
    ], soft_ttl=60, hard_ttl=1800, source="test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /api/v1/market/indices (Cache HIT or Baseline Cold-Start fallback)
        res_indices = await client.get("/api/v1/market/indices")
        assert res_indices.status_code == 200
        assert "x-cache" in res_indices.headers

        # GET /api/v1/market/overview
        res_overview = await client.get("/api/v1/market/overview?filter=all&limit=20")
        assert res_overview.status_code == 200
        assert res_overview.headers["x-cache"] in ("HIT", "STALE")

        # GET /api/v1/market/quote/INFY
        res_quote = await client.get("/api/v1/market/quote/INFY")
        assert res_quote.status_code == 200
        assert res_quote.json()["symbol"] == "INFY"

        # GET /api/v1/market/quotes?symbols=INFY
        res_quotes = await client.get("/api/v1/market/quotes?symbols=INFY")
        assert res_quotes.status_code == 200
        assert len(res_quotes.json()) == 1

        # GET /api/v1/market/history/INFY
        res_history = await client.get("/api/v1/market/history/INFY?interval=1d&range_period=1mo")
        assert res_history.status_code == 200
        assert res_history.json()["symbol"] == "INFY"

        # GET /api/v1/market/search?query=INFY
        res_search = await client.get("/api/v1/market/search?query=INFY")
        assert res_search.status_code == 200
        assert res_search.headers["x-cache"] == "HIT"

        # GET /api/v1/market/etfs
        res_etfs = await client.get("/api/v1/market/etfs")
        assert res_etfs.status_code == 200
        assert res_etfs.headers["x-cache"] in ("HIT", "STALE")


@pytest.mark.asyncio
async def test_cold_start_unwarmed_endpoints_never_block_on_provider():
    """
    Verifies that on an empty cache (cold start), endpoints return immediate
    baseline/MISS responses without making blocking provider calls.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Indices on cold start returns instant baseline
        res_indices = await client.get("/api/v1/market/indices")
        assert res_indices.status_code == 200
        assert res_indices.headers["x-cache"] in ("HIT", "STALE", "MISS")

        # Unknown quote on cold start returns 404 MISS immediately
        res_unknown = await client.get("/api/v1/market/quote/UNWARMED_STOCK")
        assert res_unknown.status_code == 404
        assert res_unknown.headers["x-cache"] == "MISS"
