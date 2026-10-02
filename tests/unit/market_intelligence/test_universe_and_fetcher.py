"""
Unit tests for Active Universe Aggregator and Market Fetcher.
"""

from unittest.mock import AsyncMock, patch
import pytest

from app.cache.market_cache import market_cache
from app.modules.market_intelligence.domain.schemas import IndexQuote, MarketMover, MarketOverview, StockQuote
from app.modules.market_intelligence.infrastructure.fetcher import MarketDataFetcher
from app.modules.market_intelligence.infrastructure.universe_aggregator import (
    TokenBucketRateLimiter,
    get_active_quote_universe,
)


@pytest.mark.asyncio
async def test_token_bucket_rate_limiter():
    limiter = TokenBucketRateLimiter(rate=100.0, capacity=2.0)
    await limiter.acquire(1.0)
    assert limiter._tokens <= 1.0


@pytest.mark.asyncio
async def test_get_active_quote_universe():
    universe = await get_active_quote_universe()
    assert len(universe) >= 20
    assert "RELIANCE" in universe
    assert "TCS" in universe


@pytest.mark.asyncio
async def test_fetcher_refresh_overview_populates_envelope():
    mock_provider = AsyncMock()
    fake_overview = MarketOverview(
        market_status="OPEN",
        status_message="Normal trading",
        major_indices=[IndexQuote(symbol="^NSEI", name="NIFTY 50", current_value=25000.0, change=100.0, change_percent=0.4)],
        top_gainers=[MarketMover(symbol="RELIANCE", company_name="Reliance Industries", current_price=3000.0, change=50.0, change_percent=1.7)],
        top_losers=[],
        most_active=[],
    )
    mock_provider.get_market_overview = AsyncMock(return_value=fake_overview)

    fetcher = MarketDataFetcher()
    fetcher.provider = mock_provider

    success = await fetcher.refresh_market_overview("all", limit=20)
    assert success is True

    # Verify envelope exists in cache
    data, status, age = await market_cache.get_envelope("mkt:overview:all:20")
    assert status == "HIT"
    assert data["market_status"] == "OPEN"
    assert data["major_indices"][0]["symbol"] == "^NSEI"


@pytest.mark.asyncio
async def test_fetcher_refresh_indices_populates_envelope():
    mock_provider = AsyncMock()
    fake_indices = [
        IndexQuote(symbol="^NSEI", name="NIFTY 50", current_value=25000.0, change=100.0, change_percent=0.4),
        IndexQuote(symbol="^BSESN", name="SENSEX", current_value=82000.0, change=200.0, change_percent=0.25),
        IndexQuote(symbol="^NSEBANK", name="NIFTY BANK", current_value=53000.0, change=150.0, change_percent=0.3),
    ]
    mock_provider.get_indices = AsyncMock(return_value=fake_indices)

    fetcher = MarketDataFetcher()
    fetcher.provider = mock_provider

    success = await fetcher.refresh_market_indices()
    assert success is True

    data, status, age = await market_cache.get_envelope("mkt:indices")
    assert status == "HIT"
    assert len(data) == 3
    assert data[0]["symbol"] == "^NSEI"
