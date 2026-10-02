"""
Unit tests for Market Intelligence Cache-Reader endpoints & X-Cache / X-Data-Age response headers.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.cache.market_cache import market_cache


@pytest.mark.asyncio
async def test_indices_endpoint_returns_cache_headers():
    # Pre-populate fresh envelope cache
    indices_data = [
        {"symbol": "^NSEI", "name": "NIFTY 50", "current_value": 25000.0, "change": 100.0, "change_percent": 0.4},
        {"symbol": "^BSESN", "name": "SENSEX", "current_value": 82000.0, "change": 200.0, "change_percent": 0.25},
    ]
    await market_cache.set_envelope("mkt:indices", indices_data, soft_ttl=60, hard_ttl=1800, source="test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/market/indices")
        assert res.status_code == 200
        assert "x-cache" in res.headers
        assert res.headers["x-cache"] in ("HIT", "STALE")
        assert "x-data-age" in res.headers
        data = res.json()
        assert len(data) >= 2


@pytest.mark.asyncio
async def test_overview_endpoint_returns_cache_headers():
    overview_data = {
        "market_status": "OPEN",
        "status_message": "Normal",
        "major_indices": [{"symbol": "^NSEI", "name": "NIFTY 50", "current_value": 25000.0, "change": 100.0, "change_percent": 0.4}],
        "top_gainers": [{"symbol": "RELIANCE", "company_name": "Reliance", "current_price": 3000.0, "change": 50.0, "change_percent": 1.7}],
        "top_losers": [],
        "most_active": [],
    }
    await market_cache.set_envelope("mkt:overview:all:20", overview_data, soft_ttl=60, hard_ttl=1800, source="test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/market/overview?filter=all&limit=20")
        assert res.status_code == 200
        assert res.headers["x-cache"] in ("HIT", "STALE")
        assert "x-data-age" in res.headers
        data = res.json()
        assert data["market_status"] in ("OPEN", "CLOSED")


@pytest.mark.asyncio
async def test_quote_endpoint_returns_hit_and_miss_headers():
    quote_data = {
        "symbol": "TCS",
        "company_name": "Tata Consultancy Services Ltd",
        "exchange": "NSE",
        "currency": "INR",
        "current_price": 3850.0,
        "change": 25.0,
        "change_percent": 0.65,
    }
    await market_cache.set_envelope("mkt:quote:TCS", quote_data, soft_ttl=60, hard_ttl=1800, source="test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Existing cached quote -> 200 HIT
        res_tcs = await client.get("/api/v1/market/quote/TCS")
        assert res_tcs.status_code == 200
        assert res_tcs.headers["x-cache"] in ("HIT", "STALE")
        assert res_tcs.json()["current_price"] == 3850.0

        # 2. Missing quote -> 404 MISS
        res_missing = await client.get("/api/v1/market/quote/NONEXISTENT999")
        assert res_missing.status_code == 404
        assert res_missing.headers["x-cache"] == "MISS"
        assert "x-data-age" in res_missing.headers


@pytest.mark.asyncio
async def test_search_endpoint_local_symbol_master():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/market/search?query=RELIANCE")
        assert res.status_code == 200
        assert res.headers["x-cache"] == "HIT"
        data = res.json()
        assert len(data) >= 1
        assert data[0]["symbol"] == "RELIANCE"
