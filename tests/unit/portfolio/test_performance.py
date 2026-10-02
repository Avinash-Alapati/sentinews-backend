"""
Unit tests for Portfolio performance (weights and XIRR).
"""

from datetime import datetime, timedelta, timezone
import pytest

from app.modules.portfolio.domain.entities import Holding
from app.modules.portfolio.domain.services.performance import (
    calculate_portfolio_weights,
    calculate_xirr,
)


def test_calculate_portfolio_weights():
    """Calculates weights proportionally based on current prices."""
    holdings = [
        Holding(symbol="RELIANCE", quantity=10, avg_buy_price=2000, current_price=2500),  # Value: 25,000 (50%)
        Holding(symbol="TCS", quantity=5, avg_buy_price=3000, current_price=3000),        # Value: 15,000 (30%)
        Holding(symbol="INFY", quantity=10, avg_buy_price=1000, current_price=1000),      # Value: 10,000 (20%)
    ]
    # Total Value: 50,000

    weighted = calculate_portfolio_weights(holdings)
    weights = {h.symbol: h.weight_pct for h in weighted}

    assert pytest.approx(weights["RELIANCE"], rel=1e-4) == 0.50
    assert pytest.approx(weights["TCS"], rel=1e-4) == 0.30
    assert pytest.approx(weights["INFY"], rel=1e-4) == 0.20
    assert pytest.approx(sum(weights.values()), rel=1e-4) == 1.0


def test_calculate_xirr_known_case():
    """
    Test XIRR on standard cash flow:
    - 2025-01-01: -100,000 (investment)
    - 2026-01-01: +110,000 (value 1 year later)
    Expected XIRR: exactly 0.10 (10%).
    """
    t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    cash_flows = [
        (t0, -100000.0),
        (t1, 110000.0),
    ]

    rate = calculate_xirr(cash_flows)
    assert rate is not None
    assert pytest.approx(rate, rel=1e-3) == 0.10


def test_calculate_xirr_insufficient_flows():
    """Single cash flow cannot compute XIRR and returns None."""
    t0 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert calculate_xirr([(t0, -100000.0)]) is None
