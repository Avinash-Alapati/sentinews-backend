"""
Unit tests for the Portfolio FIFO cost basis engine.
"""

from datetime import datetime, timedelta, timezone
import pytest

from app.modules.portfolio.domain.entities import Transaction, TransactionType
from app.modules.portfolio.domain.services.cost_basis import (
    calculate_fifo_cost_basis_for_symbol,
    calculate_portfolio_cost_basis,
)


def test_fifo_single_buy():
    """Single BUY transaction."""
    now = datetime(2026, 9, 14, 10, 0, 0, tzinfo=timezone.utc)
    txs = [
        Transaction(
            symbol="INFY",
            transaction_type=TransactionType.BUY,
            quantity=10.0,
            price=1500.0,
            timestamp=now,
        )
    ]
    basis = calculate_fifo_cost_basis_for_symbol(txs)
    assert basis.symbol == "INFY"
    assert basis.remaining_quantity == 10.0
    assert basis.total_cost_basis == 15000.0
    assert basis.avg_buy_price == 1500.0
    assert basis.realized_pnl == 0.0


def test_fifo_multiple_buys_and_partial_sell():
    """
    Buy 10 @ 100, Buy 10 @ 150.
    Sell 15 @ 200.
    FIFO should consume:
    - 10 @ 100 -> PnL: 10 * (200 - 100) = 1000
    - 5 @ 150  -> PnL: 5 * (200 - 150) = 250
    Total Realized PnL: 1250
    Remaining: 5 @ 150 = 750 (avg: 150)
    """
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    txs = [
        Transaction(symbol="TCS", transaction_type=TransactionType.BUY, quantity=10, price=100, timestamp=t0),
        Transaction(symbol="TCS", transaction_type=TransactionType.BUY, quantity=10, price=150, timestamp=t0 + timedelta(days=1)),
        Transaction(symbol="TCS", transaction_type=TransactionType.SELL, quantity=15, price=200, timestamp=t0 + timedelta(days=2)),
    ]

    basis = calculate_fifo_cost_basis_for_symbol(txs)
    assert basis.symbol == "TCS"
    assert basis.remaining_quantity == 5.0
    assert basis.total_cost_basis == 750.0
    assert basis.avg_buy_price == 150.0
    assert basis.realized_pnl == 1250.0


def test_fifo_complete_liquidation():
    """All bought shares are sold."""
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    txs = [
        Transaction(symbol="RELIANCE", transaction_type=TransactionType.BUY, quantity=20, price=2500, timestamp=t0),
        Transaction(symbol="RELIANCE", transaction_type=TransactionType.SELL, quantity=20, price=2700, timestamp=t0 + timedelta(days=5)),
    ]
    basis = calculate_fifo_cost_basis_for_symbol(txs)
    assert basis.remaining_quantity == 0.0
    assert basis.total_cost_basis == 0.0
    assert basis.avg_buy_price == 0.0
    assert basis.realized_pnl == 4000.0  # 20 * 200
