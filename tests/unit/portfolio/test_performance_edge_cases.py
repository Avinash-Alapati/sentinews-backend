"""
Unit tests for Portfolio edge cases: zero-quantity holdings, same-day buy+sell, single cash flow XIRR.
"""

from datetime import datetime, timezone
import pytest

from app.modules.portfolio.domain.entities import Holding, Transaction, TransactionType
from app.modules.portfolio.domain.services.cost_basis import calculate_fifo_cost_basis_for_symbol
from app.modules.portfolio.domain.services.performance import (
    calculate_portfolio_weights,
    calculate_xirr,
)


def test_zero_quantity_holdings_weights():
    """Holdings with zero quantity should receive 0.0 weight without ZeroDivisionError."""
    holdings = [
        Holding(symbol="INFY", quantity=0.0, avg_buy_price=1500.0, current_price=1600.0),
        Holding(symbol="TCS", quantity=10.0, avg_buy_price=3000.0, current_price=3200.0),
    ]
    weighted = calculate_portfolio_weights(holdings)
    weights = {h.symbol: h.weight_pct for h in weighted}

    assert weights["INFY"] == 0.0
    assert pytest.approx(weights["TCS"], rel=1e-4) == 1.0


def test_all_zero_quantity_holdings():
    """If total portfolio value is 0, allocation weights are distributed uniformly without error."""
    holdings = [
        Holding(symbol="INFY", quantity=0.0, avg_buy_price=0.0, current_price=0.0),
        Holding(symbol="TCS", quantity=0.0, avg_buy_price=0.0, current_price=0.0),
    ]
    weighted = calculate_portfolio_weights(holdings)
    assert len(weighted) == 2
    assert weighted[0].weight_pct == 0.5
    assert weighted[1].weight_pct == 0.5


def test_same_day_buy_and_sell_fifo():
    """
    Same-day day trade:
    Buy 10 @ 100 at 09:30
    Buy 10 @ 110 at 10:15
    Sell 10 @ 120 at 14:00
    FIFO should consume the 10 @ 100 first.
    Realized PnL: 10 * (120 - 100) = 200.
    Remaining: 10 @ 110.
    """
    t0 = datetime(2026, 9, 14, 9, 30, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 14, 10, 15, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 14, 14, 0, 0, tzinfo=timezone.utc)

    txs = [
        Transaction(symbol="TATASTEEL", transaction_type=TransactionType.BUY, quantity=10.0, price=100.0, timestamp=t0),
        Transaction(symbol="TATASTEEL", transaction_type=TransactionType.BUY, quantity=10.0, price=110.0, timestamp=t1),
        Transaction(symbol="TATASTEEL", transaction_type=TransactionType.SELL, quantity=10.0, price=120.0, timestamp=t2),
    ]

    basis = calculate_fifo_cost_basis_for_symbol(txs)
    assert basis.remaining_quantity == 10.0
    assert basis.avg_buy_price == 110.0
    assert basis.total_cost_basis == 1100.0
    assert basis.realized_pnl == 200.0


def test_xirr_single_cash_flow_returns_none():
    """Single cash flow cannot compute internal rate of return and returns None cleanly."""
    t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    result = calculate_xirr([(t0, -50000.0)])
    assert result is None
