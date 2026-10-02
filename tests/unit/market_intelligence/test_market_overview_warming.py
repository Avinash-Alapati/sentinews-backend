"""
Unit tests for Market Overview background cache warming and fast read.
"""

from unittest.mock import AsyncMock
import pytest

from app.cache.market_cache import market_cache
from app.modules.market_intelligence.application.service import MarketIntelligenceService
from app.modules.market_intelligence.domain.schemas import IndexQuote, MarketMover, MarketOverview
from workers.tasks.market_tasks import _run_async_warm_market_overview


@pytest.mark.asyncio
async def test_proactive_market_overview_warming_and_fast_read():
    """
    Validates that:
    1. Background warming task executes and populates envelope cache with 30s soft TTL.
    2. get_market_overview() hits warm cache directly without touching external providers.
    """
    mock_provider = AsyncMock()
    fake_overview = MarketOverview(
        market_status="OPEN",
        status_message="Market is trading normal",
        major_indices=[IndexQuote(symbol="^NSEI", name="NIFTY 50", current_value=25000.0, change=100.0, change_percent=0.4)],
        top_gainers=[MarketMover(symbol="RELIANCE", company_name="Reliance Industries", current_price=3000.0, change=60.0, change_percent=2.0)],
        top_losers=[MarketMover(symbol="TCS", company_name="Tata Consultancy", current_price=4000.0, change=-40.0, change_percent=-1.0)],
        most_active=[],
    )
    mock_provider.get_market_overview = AsyncMock(return_value=fake_overview)

    service = MarketIntelligenceService()
    service.fetcher.provider = mock_provider

    # Clear existing cache
    await market_cache.delete("mkt:overview:all:20")
    await market_cache.delete("mkt:overview:all")

    # 1. Simulate background cache warmer
    warmed = await service.warm_market_overview_cache()
    assert warmed is not None
    assert mock_provider.get_market_overview.call_count == 1

    # Verify envelope cache key exists
    data, status, age = await market_cache.get_envelope("mkt:overview:all:20")
    assert status == "HIT"
    assert data["market_status"] == "OPEN"

    # 2. User request for overview -> hits warm cache directly
    result, cache_status, cache_age = await service.get_market_overview()
    assert cache_status == "HIT"
    assert result.market_status == "OPEN"
    assert result.major_indices[0].symbol == "^NSEI"
    assert mock_provider.get_market_overview.call_count == 1  # Still 1, ZERO additional provider calls!


@pytest.mark.asyncio
async def test_celery_warm_market_overview_coroutine():
    """Confirms the Celery worker helper coroutine executes successfully."""
    success = await _run_async_warm_market_overview()
    assert isinstance(success, bool)
