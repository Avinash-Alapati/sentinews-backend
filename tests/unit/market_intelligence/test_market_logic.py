"""
Unit tests for Market Intelligence pure logic (ticker normalization, cache keys).
"""

import pytest
from app.integrations.market.indian_market_provider import IndianMarketProvider


@pytest.fixture
def provider():
    return IndianMarketProvider()


def test_ticker_normalization(provider):
    """Confirm normalization across exchanges and scrip codes."""
    assert provider._normalize_ticker("RELIANCE") == "RELIANCE.NS"
    assert provider._normalize_ticker("TCS.NS") == "TCS.NS"
    assert provider._normalize_ticker("500325") == "500325.BO"
    assert provider._normalize_ticker("^NSEI") == "^NSEI"
    assert provider._normalize_ticker("NSE:INFY") == "INFY.NS"
    assert provider._normalize_ticker("BSE:TATASTEEL") == "TATASTEEL.BO"


def test_market_open_indicator(provider):
    """Confirm market open function returns a boolean without throwing."""
    is_open = provider._is_indian_market_open()
    assert isinstance(is_open, bool)
