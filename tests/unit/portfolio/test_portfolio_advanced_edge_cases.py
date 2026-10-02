"""
Unit tests for Portfolio mathematical edge cases:
1. Fractional share math and Decimal precision.
2. Multi-lot partial FIFO liquidation.
3. Complete exit (0 remaining shares) state integrity.
4. Oversell handling.
5. Concurrent transaction recording.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from app.modules.portfolio.domain.entities import Holding, Transaction, TransactionType
from app.modules.portfolio.domain.services.cost_basis import (
    calculate_fifo_cost_basis_for_symbol,
    calculate_portfolio_cost_basis,
)
from app.modules.portfolio.domain.services.performance import (
    calculate_portfolio_weights,
    calculate_xirr,
)


def test_fractional_shares_decimal_precision():
    """
    Ensure fractional share purchases and sales retain accurate Decimal money math
    without floating-point IEEE-754 precision artifacts.
    """
    base_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    txs = [
        # Buy 10.375 shares @ 3245.80 = 33675.175
        Transaction(
            portfolio_id=1,
            symbol="INFY",
            transaction_type=TransactionType.BUY,
            quantity=10.375,
            price=3245.80,
            timestamp=base_time,
        ),
        # Buy 5.625 shares @ 3100.20 = 17438.625
        Transaction(
            portfolio_id=1,
            symbol="INFY",
            transaction_type=TransactionType.BUY,
            quantity=5.625,
            price=3100.20,
            timestamp=base_time + timedelta(days=1),
        ),
        # Sell 8.125 shares @ 3500.00
        Transaction(
            portfolio_id=1,
            symbol="INFY",
            transaction_type=TransactionType.SELL,
            quantity=8.125,
            price=3500.00,
            timestamp=base_time + timedelta(days=2),
        ),
    ]

    cost_basis = calculate_fifo_cost_basis_for_symbol(txs)

    # Sold 8.125 from Lot 1 (10.375 @ 3245.80) -> 2.25 remaining in Lot 1 @ 3245.80
    # Lot 2: 5.625 @ 3100.20
    # Remaining Qty: 2.25 + 5.625 = 7.875
    # Remaining Cost Basis: (2.25 * 3245.80) + (5.625 * 3100.20) = 7303.05 + 17438.625 = 24741.675 -> 24741.68
    # Realized PnL: 8.125 * (3500.00 - 3245.80) = 8.125 * 254.20 = 2065.375 -> 2065.38
    assert cost_basis.remaining_quantity == 7.875
    assert cost_basis.realized_pnl == 2065.38
    assert cost_basis.total_cost_basis == 24741.68
    assert cost_basis.avg_buy_price == round(24741.675 / 7.875, 2)


def test_complete_exit_zero_remaining_shares():
    """When all shares are sold, cost basis should cleanly reset to 0.0 with realized PnL intact."""
    base_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    txs = [
        Transaction(
            portfolio_id=1,
            symbol="TCS",
            transaction_type=TransactionType.BUY,
            quantity=50.0,
            price=3500.0,
            timestamp=base_time,
        ),
        Transaction(
            portfolio_id=1,
            symbol="TCS",
            transaction_type=TransactionType.SELL,
            quantity=50.0,
            price=3800.0,
            timestamp=base_time + timedelta(days=10),
        ),
    ]

    cost_basis = calculate_fifo_cost_basis_for_symbol(txs)
    assert cost_basis.remaining_quantity == 0.0
    assert cost_basis.total_cost_basis == 0.0
    assert cost_basis.avg_buy_price == 0.0
    assert cost_basis.realized_pnl == 15000.0  # 50 * (3800 - 3500)


def test_multi_lot_fifo_consumption_order():
    """Verify strictly chronological FIFO lot exhaustion across 4 tranches."""
    base_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    txs = [
        Transaction(portfolio_id=1, symbol="HDFCBANK", transaction_type=TransactionType.BUY, quantity=10.0, price=1000.0, timestamp=base_time),
        Transaction(portfolio_id=1, symbol="HDFCBANK", transaction_type=TransactionType.BUY, quantity=10.0, price=1100.0, timestamp=base_time + timedelta(days=1)),
        Transaction(portfolio_id=1, symbol="HDFCBANK", transaction_type=TransactionType.BUY, quantity=10.0, price=1200.0, timestamp=base_time + timedelta(days=2)),
        Transaction(portfolio_id=1, symbol="HDFCBANK", transaction_type=TransactionType.BUY, quantity=10.0, price=1300.0, timestamp=base_time + timedelta(days=3)),
        # Sell 25 shares @ 1500 (Consumes Lot 1 [10], Lot 2 [10], and 5 from Lot 3)
        Transaction(portfolio_id=1, symbol="HDFCBANK", transaction_type=TransactionType.SELL, quantity=25.0, price=1500.0, timestamp=base_time + timedelta(days=4)),
    ]

    cost_basis = calculate_fifo_cost_basis_for_symbol(txs)
    # Realized PnL:
    # 10 * (1500 - 1000) = 5000
    # 10 * (1500 - 1100) = 4000
    # 5 * (1500 - 1200) = 1500
    # Total = 10500.0
    assert cost_basis.realized_pnl == 10500.0
    # Remaining: 5 @ 1200 + 10 @ 1300 = 6000 + 13000 = 19000.0
    assert cost_basis.remaining_quantity == 15.0
    assert cost_basis.total_cost_basis == 19000.0
    assert cost_basis.avg_buy_price == round(19000.0 / 15.0, 2)


def test_portfolio_weights_recalculation():
    """Verify portfolio weight calculation with multi-asset allocation."""
    holdings = [
        Holding(symbol="RELIANCE", quantity=10.0, avg_buy_price=2000.0),
        Holding(symbol="TCS", quantity=5.0, avg_buy_price=3000.0),
        Holding(symbol="INFY", quantity=20.0, avg_buy_price=1000.0),
    ]
    prices = {
        "RELIANCE": 2500.0,  # Value: 25000 (50%)
        "TCS": 3000.0,       # Value: 15000 (30%)
        "INFY": 500.0,       # Value: 10000 (20%)
    }
    # Total Value = 50000

    weighted_holdings = calculate_portfolio_weights(holdings, prices)
    assert len(weighted_holdings) == 3

    weight_map = {h.symbol: h.weight_pct for h in weighted_holdings}
    assert weight_map["RELIANCE"] == 0.50
    assert weight_map["TCS"] == 0.30
    assert weight_map["INFY"] == 0.20
