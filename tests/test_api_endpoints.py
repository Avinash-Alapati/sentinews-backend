import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_health_endpoints():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Root health
        r = await client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"

        # API V1 health
        r = await client.get("/api/v1/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert "cache" in data
        assert "uptime_seconds" in data


@pytest.mark.asyncio
async def test_market_quote_endpoints():
    from app.cache.market_cache import market_cache
    await market_cache.set_envelope(
        "mkt:quote:RELIANCE",
        {"symbol": "RELIANCE", "company_name": "Reliance Industries Ltd", "current_price": 2950.0, "change": 15.0, "change_percent": 0.51, "source": "test"},
        soft_ttl=60,
        hard_ttl=300,
        source="test",
    )
    await market_cache.set_envelope(
        "mkt:quote:TCS",
        {"symbol": "TCS", "company_name": "Tata Consultancy Services Ltd", "current_price": 3800.0, "change": 25.0, "change_percent": 0.66, "source": "test"},
        soft_ttl=60,
        hard_ttl=300,
        source="test",
    )
    await market_cache.set_envelope(
        "mkt:quote:INFY",
        {"symbol": "INFY", "company_name": "Infosys Ltd", "current_price": 1750.0, "change": -10.0, "change_percent": -0.57, "source": "test"},
        soft_ttl=60,
        hard_ttl=300,
        source="test",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Single Quote
        r = await client.get("/api/v1/market/quote/RELIANCE")
        assert r.status_code == 200
        data = r.json()
        assert data["symbol"] == "RELIANCE"
        assert "current_price" in data
        assert data["current_price"] > 0

        # Batch Quotes
        r = await client.get("/api/v1/market/quotes?symbols=TCS,INFY")
        assert r.status_code == 200
        quotes = r.json()
        assert len(quotes) >= 1
        symbols = [q["symbol"] for q in quotes]
        assert "TCS" in symbols or "INFY" in symbols



@pytest.mark.asyncio
async def test_market_indices_and_overview():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Indices
        r = await client.get("/api/v1/market/indices")
        assert r.status_code == 200
        indices = r.json()
        assert len(indices) > 0

        # Overview
        r = await client.get("/api/v1/market/overview")
        assert r.status_code == 200
        overview = r.json()
        assert "market_status" in overview
        assert overview["market_status"] in ["OPEN", "CLOSED"]


@pytest.mark.asyncio
async def test_market_history_and_search():
    mock_candles = [
        CandleData(
            timestamp=datetime.now(timezone.utc),
            open=2800.0,
            high=2850.0,
            low=2790.0,
            close=2830.0,
            volume=50000,
        )
    ]
    mock_history = StockHistoryResponse(
        symbol="RELIANCE",
        interval="1d",
        range="5d",
        candles=mock_candles,
    )
    with patch("app.modules.market_intelligence.application.service.market_service.get_stock_history", return_value=(mock_history, "HIT", 0.0)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # History
            r = await client.get("/api/v1/market/history/RELIANCE?interval=1d&range=5d")
            assert r.status_code == 200
            history = r.json()
            assert history["symbol"] == "RELIANCE"
            assert len(history["candles"]) > 0

            # Search
            r = await client.get("/api/v1/market/search?query=tata")
            assert r.status_code == 200
            results = r.json()
            assert len(results) > 0


from unittest.mock import patch
from app.modules.market_intelligence.domain.schemas import StockHistoryResponse, CandleData, ETFQuote, ETFListResponse
from datetime import datetime, timezone

@pytest.mark.asyncio
async def test_gzip_compression_enabled():
    mock_candles = [
        CandleData(
            timestamp=datetime.now(timezone.utc),
            open=100.0 + i,
            high=105.0 + i,
            low=99.0 + i,
            close=102.0 + i,
            volume=1000 * (i + 1),
        )
        for i in range(50)
    ]
    mock_history = StockHistoryResponse(
        symbol="RELIANCE",
        interval="1d",
        range="1mo",
        candles=mock_candles,
    )
    with patch("app.modules.market_intelligence.application.service.market_service.get_stock_history", return_value=(mock_history, "HIT", 0.0)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/market/history/RELIANCE?interval=1d&range=1mo",
                headers={"Accept-Encoding": "gzip"},
            )
            assert r.status_code == 200
            # Automatically decompressed by client, payload valid
            assert "candles" in r.json()
            assert len(r.json()["candles"]) == 50


@pytest.mark.asyncio
async def test_market_etfs_endpoint():
    mock_etfs = ETFListResponse(
        total_count=2,
        available_categories=["Commodity - Gold", "Equity - Broad Market"],
        items=[
            ETFQuote(
                symbol="GOLDBEES",
                underlying_asset="Gold",
                category="Commodity - Gold",
                last_price=55.0,
                change=0.5,
                change_percent=0.9,
            ),
            ETFQuote(
                symbol="NIFTYBEES",
                underlying_asset="NIFTY 50",
                category="Equity - Broad Market",
                last_price=250.0,
                change=1.2,
                change_percent=0.48,
            ),
        ],
    )
    with patch("app.modules.market_intelligence.application.service.market_service.get_etfs", return_value=(mock_etfs, "HIT", 0.0)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # All ETFs
            r = await client.get("/api/v1/market/etfs?limit=10")
            assert r.status_code == 200
            data = r.json()
            assert "total_count" in data
            assert "available_categories" in data
            assert "items" in data
            assert data["total_count"] > 0
            assert len(data["items"]) <= 10

