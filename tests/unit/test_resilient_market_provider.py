import asyncio
import pytest
from app.core.circuit_breaker import circuit_breaker
from app.integrations.market.base import BaseMarketProvider
from app.integrations.market.resilient_provider import ResilientMarketProvider
from app.integrations.market.upstox_provider import UpstoxMarketProvider
from app.modules.market_intelligence.domain.schemas import StockQuote, IndexQuote, MarketOverview


class MockProvider(BaseMarketProvider):
    def __init__(self, delay: float = 0.0, should_fail: bool = False, return_val=None):
        self.delay = delay
        self.should_fail = should_fail
        self.return_val = return_val
        self.call_count = 0

    async def get_quote(self, symbol: str):
        self.call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Provider failed!")
        return self.return_val

    async def get_quotes(self, symbols):
        self.call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Provider failed!")
        return self.return_val or []

    async def get_indices(self):
        self.call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Provider failed!")
        return self.return_val or []

    async def get_market_overview(self, index_filter: str = "all", limit: int = 20):
        self.call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Provider failed!")
        return self.return_val

    async def get_historical_candles(self, symbol: str, interval: str = "1d", range_period: str = "1mo"):
        self.call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Provider failed!")
        return self.return_val

    async def search_symbols(self, query: str):
        self.call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError("Provider failed!")
        return self.return_val or []


@pytest.mark.asyncio
async def test_resilient_primary_fast_returns_primary():
    await circuit_breaker.reset("YahooTest")
    await circuit_breaker.reset("UpstoxTest")

    primary_quote = StockQuote(
        symbol="RELIANCE",
        company_name="Reliance Industries",
        exchange="NSE",
        currency="INR",
        current_price=2900.0,
        change=15.0,
        change_percent=0.52,
        provider="Primary",
    )
    fallback_quote = StockQuote(
        symbol="RELIANCE",
        company_name="Reliance Industries",
        exchange="NSE",
        currency="INR",
        current_price=2905.0,
        change=20.0,
        change_percent=0.69,
        provider="Upstox API",
    )

    primary = MockProvider(delay=0.01, return_val=primary_quote)
    fallback = MockProvider(delay=0.01, return_val=fallback_quote)

    resilient = ResilientMarketProvider(
        primary=primary,
        fallback=fallback,
        latency_threshold=0.2,
        primary_name="YahooTest",
        fallback_name="UpstoxTest",
    )

    result = await resilient.get_quote("RELIANCE")
    assert result is not None
    assert result.current_price == 2900.0
    assert result.provider == "Primary"


@pytest.mark.asyncio
async def test_resilient_primary_latency_falls_back_to_upstox():
    await circuit_breaker.reset("YahooTest")
    await circuit_breaker.reset("UpstoxTest")

    primary_quote = StockQuote(
        symbol="TCS",
        company_name="Tata Consultancy",
        exchange="NSE",
        currency="INR",
        current_price=3800.0,
        change=10.0,
        change_percent=0.26,
        provider="Primary",
    )
    fallback_quote = StockQuote(
        symbol="TCS",
        company_name="Tata Consultancy",
        exchange="NSE",
        currency="INR",
        current_price=3802.0,
        change=12.0,
        change_percent=0.31,
        provider="Upstox API",
    )

    primary = MockProvider(delay=0.3, return_val=primary_quote)
    fallback = MockProvider(delay=0.01, return_val=fallback_quote)

    resilient = ResilientMarketProvider(
        primary=primary,
        fallback=fallback,
        latency_threshold=0.05,
        primary_name="YahooTest",
        fallback_name="UpstoxTest",
    )

    result = await resilient.get_quote("TCS")
    assert result is not None
    assert result.current_price == 3802.0
    assert result.provider == "Upstox API"


@pytest.mark.asyncio
async def test_resilient_primary_error_falls_back_to_upstox():
    await circuit_breaker.reset("YahooTest")
    await circuit_breaker.reset("UpstoxTest")

    fallback_quote = StockQuote(
        symbol="INFY",
        company_name="Infosys",
        exchange="NSE",
        currency="INR",
        current_price=1600.0,
        change=5.0,
        change_percent=0.31,
        provider="Upstox API",
    )

    primary = MockProvider(delay=0.0, should_fail=True)
    fallback = MockProvider(delay=0.01, return_val=fallback_quote)

    resilient = ResilientMarketProvider(
        primary=primary,
        fallback=fallback,
        latency_threshold=0.2,
        primary_name="YahooTest",
        fallback_name="UpstoxTest",
    )

    result = await resilient.get_quote("INFY")
    assert result is not None
    assert result.current_price == 1600.0
    assert result.provider == "Upstox API"


@pytest.mark.asyncio
async def test_circuit_breaker_open_skips_primary_directly_to_fallback():
    await circuit_breaker.reset("YahooTripped")
    await circuit_breaker.reset("UpstoxRecovered")

    # Trip YahooTripped circuit breaker to OPEN
    for _ in range(5):
        await circuit_breaker.record_failure("YahooTripped", RuntimeError("429 Too Many Requests"))

    assert await circuit_breaker.can_execute("YahooTripped") is False

    fallback_quote = StockQuote(
        symbol="HDFCBANK",
        company_name="HDFC Bank",
        exchange="NSE",
        currency="INR",
        current_price=1650.0,
        change=10.0,
        change_percent=0.6,
        provider="Upstox API",
    )

    primary = MockProvider(delay=0.0, return_val=None)
    fallback = MockProvider(delay=0.01, return_val=fallback_quote)

    resilient = ResilientMarketProvider(
        primary=primary,
        fallback=fallback,
        latency_threshold=0.2,
        primary_name="YahooTripped",
        fallback_name="UpstoxRecovered",
    )

    result = await resilient.get_quote("HDFCBANK")
    assert result is not None
    assert result.current_price == 1650.0
    # Primary was completely skipped without incrementing call_count
    assert primary.call_count == 0
    assert fallback.call_count == 1


@pytest.mark.asyncio
async def test_no_redundant_fallback_calls_on_overview():
    await circuit_breaker.reset("PrimaryFail")
    await circuit_breaker.reset("FallbackFail")

    primary = MockProvider(delay=0.0, should_fail=True)
    fallback = MockProvider(delay=0.0, should_fail=True)

    resilient = ResilientMarketProvider(
        primary=primary,
        fallback=fallback,
        latency_threshold=0.05,
        primary_name="PrimaryFail",
        fallback_name="FallbackFail",
    )

    res = await resilient.get_market_overview()
    assert res is None
    # Both called at most once, zero redundant second fallback calls!
    assert primary.call_count == 1
    assert fallback.call_count == 1


def test_upstox_instrument_key_resolution():
    upstox = UpstoxMarketProvider()
    assert upstox._resolve_instrument_key("RELIANCE") == ("NSE_EQ|INE002A01018", "Reliance Industries Ltd")
    assert upstox._resolve_instrument_key("TCS.NS") == ("NSE_EQ|INE467B01029", "Tata Consultancy Services Ltd")
    assert upstox._resolve_instrument_key("NSE:INFY") == ("NSE_EQ|INE009A01021", "Infosys Ltd")
    assert upstox._resolve_instrument_key("NIFTY 50") == ("NSE_INDEX|Nifty 50", "NIFTY 50")
    assert upstox._resolve_instrument_key("^BSESN") == ("BSE_INDEX|SENSEX", "SENSEX")
    assert upstox._clean_symbol("NSE:TATASTEEL.NS") == "TATASTEEL"
