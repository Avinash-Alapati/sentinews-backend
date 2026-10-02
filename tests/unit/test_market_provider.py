import pytest
from app.integrations.market.indian_market_provider import IndianMarketProvider
from app.cache.market_cache import MarketCache


@pytest.fixture
def provider():
    return IndianMarketProvider()


def test_normalize_ticker(provider):
    assert provider._normalize_ticker("RELIANCE") == "RELIANCE.NS"
    assert provider._normalize_ticker("TCS.NS") == "TCS.NS"
    assert provider._normalize_ticker("500325") == "500325.BO"
    assert provider._normalize_ticker("^NSEI") == "^NSEI"
    assert provider._normalize_ticker("NSE:INFY") == "INFY.NS"
    assert provider._normalize_ticker("BSE:TATASTEEL") == "TATASTEEL.BO"


def test_is_indian_market_open_returns_boolean(provider):
    is_open = provider._is_indian_market_open()
    assert isinstance(is_open, bool)


@pytest.mark.asyncio
async def test_search_symbols_popular_stocks(provider):
    results = await provider.search_symbols("reliance")
    assert len(results) > 0
    symbols = [r.symbol for r in results]
    assert "RELIANCE" in symbols


@pytest.mark.asyncio
async def test_connection_pool_reuse(provider):
    client1 = await provider._get_client()
    client2 = await provider._get_client()
    assert client1 is client2  # Reuses same client instance
    assert not client1.is_closed
    await provider.close()
    assert client1.is_closed


@pytest.mark.asyncio
async def test_cache_capacity_bounding():
    cache = MarketCache()
    cache.MAX_MEMORY_CACHE_SIZE = 10  # Test threshold
    for i in range(15):
        await cache.set(f"key_{i}", f"value_{i}", ttl_seconds=60)

    # Size should be pruned and capped at or below threshold
    assert len(cache._memory_cache) <= 10
    # Latest item is preserved
    assert await cache.get("key_14") == "value_14"
