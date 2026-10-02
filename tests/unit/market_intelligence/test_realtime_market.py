"""
Comprehensive unit & integration tests for Market Intelligence:
1. Real-time quote retrieval (single & batch) with accurate calculations.
2. Market Overview with true top gainers, top losers, and volume leaders.
3. Major Indian Benchmark Indices (NIFTY 50, SENSEX, BANK NIFTY, NIFTY IT, MIDCAP 100).
4. Historical and intraday candle charts.
5. Symbol search across NSE and BSE.
"""

import pytest
from app.integrations.market.indian_market_provider import IndianMarketProvider
from app.modules.market_intelligence.application.service import MarketIntelligenceService
from app.modules.market_intelligence.domain.schemas import (
    StockQuote,
    IndexQuote,
    MarketOverview,
    MarketMover,
)


@pytest.fixture
async def market_provider():
    provider = IndianMarketProvider()
    yield provider
    await provider.close()


@pytest.mark.asyncio
async def test_indian_provider_get_quote_live(market_provider: IndianMarketProvider):
    """Test live single quote retrieval for large-cap Indian equity."""
    quote = await market_provider.get_quote("RELIANCE")
    assert quote is not None
    assert quote.symbol == "RELIANCE"
    assert quote.current_price > 0
    assert quote.exchange in ["NSE", "BSE"]
    assert quote.currency == "INR"
    assert quote.company_name is not None and len(quote.company_name) > 0


@pytest.mark.asyncio
async def test_indian_provider_get_quotes_batch(market_provider: IndianMarketProvider):
    """Test batch quote retrieval in parallel."""
    symbols = ["TCS", "INFY", "HDFCBANK", "ICICIBANK"]
    quotes = await market_provider.get_quotes(symbols)
    assert len(quotes) >= 3
    fetched_symbols = {q.symbol.upper() for q in quotes}
    assert "TCS" in fetched_symbols or "INFY" in fetched_symbols
    for q in quotes:
        assert q.current_price > 0


@pytest.mark.asyncio
async def test_indian_provider_get_indices(market_provider: IndianMarketProvider):
    """Test retrieval of all 5 major Indian benchmark indices."""
    indices = await market_provider.get_indices()
    assert len(indices) >= 4
    names = [idx.name for idx in indices]
    assert "NIFTY 50" in names
    assert "SENSEX" in names
    assert "NIFTY BANK" in names
    for idx in indices:
        assert idx.current_value > 0
        assert isinstance(idx.change_percent, float)


@pytest.mark.asyncio
async def test_indian_provider_market_overview_gainers_losers(market_provider: IndianMarketProvider):
    """
    Test Market Overview produces valid top gainers, top losers, and volume leaders
    from either live NSE variations or the comprehensive constituent universe.
    """
    overview = await market_provider.get_market_overview()
    assert overview is not None
    assert overview.market_status in ["OPEN", "CLOSED"]
    assert len(overview.major_indices) >= 4
    
    # Gainers validation
    assert len(overview.top_gainers) >= 1
    for g in overview.top_gainers:
        assert isinstance(g, MarketMover)
        assert g.current_price > 0
        assert g.change_percent >= 0  # Gainers must have positive or zero change
        assert len(g.symbol) > 0

    # Losers validation
    assert len(overview.top_losers) >= 1
    for l in overview.top_losers:
        assert isinstance(l, MarketMover)
        assert l.current_price > 0
        assert l.change_percent <= 0  # Losers must have negative or zero change
        assert len(l.symbol) > 0

    # Most active volume validation
    assert len(overview.most_active) >= 1
    for m in overview.most_active:
        assert isinstance(m, MarketMover)
        assert m.current_price > 0


@pytest.mark.asyncio
async def test_indian_provider_historical_candles(market_provider: IndianMarketProvider):
    """Test OHLCV candle charting data."""
    history = await market_provider.get_historical_candles("TCS", interval="1d", range_period="1mo")
    assert history is not None
    assert history.symbol == "TCS"
    assert len(history.candles) > 0
    first_candle = history.candles[0]
    assert first_candle.open > 0
    assert first_candle.high >= first_candle.low
    assert first_candle.close > 0


@pytest.mark.asyncio
async def test_indian_provider_search_symbols(market_provider: IndianMarketProvider):
    """Test symbol search for Indian equities."""
    results = await market_provider.search_symbols("tata")
    assert len(results) > 0
    symbols = [r.symbol for r in results]
    assert any("TATA" in s for s in symbols)
