"""
Unit and integration tests for NSE Market Calendar, Timezone boundaries,
and Resilient Market Provider Latency Hedging / Circuit Breaker fallback sequence.
"""

import asyncio
from datetime import date, datetime, time as dtime, timezone
from typing import List, Optional
from unittest.mock import AsyncMock, patch
import pytest
import pytz

from app.core.circuit_breaker import circuit_breaker
from app.integrations.market.base import BaseMarketProvider
from app.integrations.market.resilient_provider import ResilientMarketProvider
from app.modules.market_intelligence.domain.market_schedule import (
    get_current_ist_time,
    is_indian_market_open,
    get_current_market_cadence,
)
from app.modules.market_intelligence.domain.schemas import (
    IndexQuote,
    MarketOverview,
    StockHistoryResponse,
    StockQuote,
    StockSearchResult,
)
from app.modules.market_reports.domain.services.trading_calendar import (
    NSE_HOLIDAYS,
    NSETradingCalendar,
)

KOLKATA_TZ = pytz.timezone("Asia/Kolkata")


# ============================================================================
# 1. NSE Trading Calendar & Timezone Tests
# ============================================================================

def test_nse_calendar_weekend_and_holiday_logic():
    """Verify weekend detection and official holiday handling."""
    # Weekend dates
    saturday = date(2026, 9, 26)
    sunday = date(2026, 9, 27)
    monday = date(2026, 9, 28)

    assert NSETradingCalendar.is_weekend(saturday) is True
    assert NSETradingCalendar.is_weekend(sunday) is True
    assert NSETradingCalendar.is_weekend(monday) is False

    # Holidays in 2026
    republic_day = date(2026, 1, 26)
    independence_day = date(2026, 8, 15)
    gandhi_jayanti = date(2026, 10, 2)

    assert NSETradingCalendar.is_trading_day(republic_day) is False
    assert NSETradingCalendar.get_holiday_name(republic_day) == "Republic Day"

    assert NSETradingCalendar.is_trading_day(independence_day) is False
    assert NSETradingCalendar.is_trading_day(gandhi_jayanti) is False

    # Normal trading day
    normal_tuesday = date(2026, 9, 29)
    assert NSETradingCalendar.is_trading_day(normal_tuesday) is True
    assert NSETradingCalendar.get_holiday_name(normal_tuesday) is None


def test_nse_calendar_next_and_previous_trading_day():
    """Verify next/prev trading day traversal skips weekends and holidays."""
    # Good Friday 2026 is April 3, 2026 (Friday holiday)
    # Thursday April 2 is trading day -> next trading day should be Monday April 6
    thursday = date(2026, 4, 2)
    next_day = NSETradingCalendar.get_next_trading_day(thursday)
    assert next_day == date(2026, 4, 6)

    # From Monday April 6, previous trading day should be Thursday April 2
    monday = date(2026, 4, 6)
    prev_day = NSETradingCalendar.get_previous_trading_day(monday)
    assert prev_day == date(2026, 4, 2)


def test_market_schedule_utc_input_conversion():
    """Verify market open check handles UTC datetime input correctly."""
    # 09:15 IST is 03:45 UTC
    # 15:30 IST is 10:00 UTC
    # Monday Sep 28, 2026 at 03:45 UTC (09:15 IST) -> Market OPEN
    open_utc = datetime(2026, 9, 28, 3, 45, 0, tzinfo=timezone.utc)
    assert is_indian_market_open(open_utc) is True

    # Monday Sep 28, 2026 at 03:44 UTC (09:14 IST) -> Market CLOSED
    pre_utc = datetime(2026, 9, 28, 3, 44, 59, tzinfo=timezone.utc)
    assert is_indian_market_open(pre_utc) is False

    # Monday Sep 28, 2026 at 10:00 UTC (15:30 IST) -> Market OPEN (closing bell)
    close_utc = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    assert is_indian_market_open(close_utc) is True

    # Monday Sep 28, 2026 at 10:01 UTC (15:31 IST) -> Market CLOSED
    post_utc = datetime(2026, 9, 28, 10, 1, 0, tzinfo=timezone.utc)
    assert is_indian_market_open(post_utc) is False


# ============================================================================
# 2. Resilient Market Provider Fallback & Latency-Hedging Tests
# ============================================================================

class MockMarketProvider(BaseMarketProvider):
    def __init__(self, name: str = "Mock"):
        self.name = name
        self.delay = 0.0
        self.should_fail = False
        self.quote_data: Optional[StockQuote] = None
        self.quotes_data: List[StockQuote] = []

    async def get_quote(self, symbol: str) -> Optional[StockQuote]:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError(f"{self.name} connection failed")
        return self.quote_data

    async def get_quotes(self, symbols: List[str]) -> List[StockQuote]:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError(f"{self.name} batch quote failed")
        return self.quotes_data

    async def get_indices(self) -> List[IndexQuote]:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError(f"{self.name} indices failed")
        return []

    async def get_market_overview(self, index_filter: Optional[str] = None, limit: int = 20) -> Optional[MarketOverview]:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError(f"{self.name} overview failed")
        return None

    async def get_historical_candles(self, symbol: str, interval: str = "1d", range_period: str = "1mo") -> Optional[StockHistoryResponse]:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError(f"{self.name} candles failed")
        return None

    async def search_symbols(self, query: str) -> List[StockSearchResult]:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.should_fail:
            raise RuntimeError(f"{self.name} search failed")
        return []


@pytest.mark.asyncio
async def test_resilient_provider_primary_fast_success():
    """Primary completes well within latency threshold; fallback is never invoked."""
    primary = MockMarketProvider("Primary")
    fallback = MockMarketProvider("Fallback")

    expected_quote = StockQuote(
        symbol="RELIANCE",
        current_price=2950.0,
        change=15.0,
        change_percent=0.51,
        company_name="Reliance Industries Ltd",
        currency="INR",
        exchange="NSE",
    )
    primary.quote_data = expected_quote

    resilient = ResilientMarketProvider(
        primary=primary,
        fallback=fallback,
        latency_threshold=0.5,
        primary_name="TestPrimary1",
        fallback_name="TestFallback1",
    )

    quote = await resilient.get_quote("RELIANCE")
    assert quote is not None
    assert quote.symbol == "RELIANCE"
    assert quote.current_price == 2950.0


@pytest.mark.asyncio
async def test_resilient_provider_primary_failure_recovers_via_fallback():
    """Primary throws exception; provider seamlessly recovers via fallback."""
    primary = MockMarketProvider("Primary")
    fallback = MockMarketProvider("Fallback")

    primary.should_fail = True
    fallback_quote = StockQuote(
        symbol="TCS",
        current_price=4120.0,
        change=-20.0,
        change_percent=-0.48,
        company_name="Tata Consultancy Services",
        currency="INR",
        exchange="NSE",
    )
    fallback.quote_data = fallback_quote

    resilient = ResilientMarketProvider(
        primary=primary,
        fallback=fallback,
        latency_threshold=0.5,
        primary_name="TestPrimary2",
        fallback_name="TestFallback2",
    )

    quote = await resilient.get_quote("TCS")
    assert quote is not None
    assert quote.symbol == "TCS"
    assert quote.current_price == 4120.0


@pytest.mark.asyncio
async def test_resilient_provider_latency_hedging_fallback_race():
    """
    Primary is sluggish (> latency_threshold).
    Resilient provider launches fallback concurrently; fallback wins the race.
    """
    primary = MockMarketProvider("Primary")
    fallback = MockMarketProvider("Fallback")

    primary.delay = 1.0  # Takes 1 second
    primary.quote_data = StockQuote(
        symbol="INFY",
        current_price=1850.0,
        change=10.0,
        change_percent=0.54,
        company_name="Infosys Ltd",
        currency="INR",
        exchange="NSE",
    )

    fallback.delay = 0.05  # Responds in 50ms
    fallback.quote_data = StockQuote(
        symbol="INFY",
        current_price=1852.0,
        change=12.0,
        change_percent=0.65,
        company_name="Infosys Ltd",
        currency="INR",
        exchange="NSE",
    )

    resilient = ResilientMarketProvider(
        primary=primary,
        fallback=fallback,
        latency_threshold=0.1,  # Trigger fallback after 100ms
        primary_name="TestPrimary3",
        fallback_name="TestFallback3",
    )

    quote = await resilient.get_quote("INFY")
    assert quote is not None
    assert quote.symbol == "INFY"
    # Fallback won because primary had 1.0s delay
    assert quote.current_price == 1852.0


@pytest.mark.asyncio
async def test_resilient_provider_batch_quotes_partial_fallback_fill():
    """Verify batch quotes fetch missing symbols from fallback when primary only returns a subset."""
    primary = MockMarketProvider("Primary")
    fallback = MockMarketProvider("Fallback")

    q_rel = StockQuote(symbol="RELIANCE", current_price=2950.0, change=15.0, change_percent=0.51, company_name="Reliance", currency="INR", exchange="NSE")
    q_tcs = StockQuote(symbol="TCS", current_price=4100.0, change=-20.0, change_percent=-0.48, company_name="TCS", currency="INR", exchange="NSE")

    # Primary only returned RELIANCE
    primary.quotes_data = [q_rel]
    # Fallback returns TCS
    fallback.quotes_data = [q_tcs]

    resilient = ResilientMarketProvider(
        primary=primary,
        fallback=fallback,
        latency_threshold=0.5,
        primary_name="TestPrimary4",
        fallback_name="TestFallback4",
    )

    quotes = await resilient.get_quotes(["RELIANCE", "TCS"])
    assert len(quotes) == 2
    symbols = {q.symbol for q in quotes}
    assert "RELIANCE" in symbols
    assert "TCS" in symbols
