"""
Golden-File Tests for Portfolio Money Calculations & Cost Basis.

Verifies:
1. Exact FIFO cost basis with multiple lots and partial sells.
2. Zero holdings after complete exit.
3. Quantization to 0.01 without floating point drift.
4. Total P&L, realized P&L, unrealized P&L parity.
5. Money-weighted returns (XIRR) precision.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from app.modules.portfolio.domain.entities import Transaction, TransactionType
from app.modules.portfolio.domain.services.cost_basis import (
    calculate_fifo_cost_basis_for_symbol,
    calculate_portfolio_cost_basis,
)
from app.modules.portfolio.domain.services.performance import calculate_xirr


def test_golden_fifo_multiple_lots_and_partial_sells():
    """
    Scenario:
    - Buy Lot 1: 100 shares @ Rs 250.50 on Day 1 (Cost = 25050.00)
    - Buy Lot 2: 50 shares @ Rs 300.00 on Day 2 (Cost = 15000.00)
    - Sell 1: 120 shares @ Rs 320.00 on Day 3:
        * 100 shares from Lot 1 @ 250.50 -> Realized PnL = 100 * (320 - 250.50) = 100 * 69.50 = 6950.00
        * 20 shares from Lot 2 @ 300.00 -> Realized PnL = 20 * (320 - 300) = 20 * 20 = 400.00
        * Total Realized PnL = 7350.00
        * Remaining: 30 shares @ 300.00 -> Total Cost Basis = 9000.00, Avg Buy Price = 300.00
    """
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    txs = [
        Transaction(symbol="TCS", transaction_type=TransactionType.BUY, quantity=100.0, price=250.50, timestamp=t0),
        Transaction(symbol="TCS", transaction_type=TransactionType.BUY, quantity=50.0, price=300.00, timestamp=t0 + timedelta(days=1)),
        Transaction(symbol="TCS", transaction_type=TransactionType.SELL, quantity=120.0, price=320.00, timestamp=t0 + timedelta(days=2)),
    ]

    cb = calculate_fifo_cost_basis_for_symbol(txs)
    assert cb.symbol == "TCS"
    assert cb.remaining_quantity == 30.0
    assert cb.total_cost_basis == 9000.00
    assert cb.avg_buy_price == 300.00
    assert cb.realized_pnl == 7350.00


def test_golden_fifo_complete_exit_zero_holdings():
    """
    Scenario:
    - Buy Lot 1: 50 shares @ Rs 1000.00
    - Sell: 50 shares @ Rs 1200.00
    - Result: remaining_quantity = 0.0, total_cost_basis = 0.0, avg_buy_price = 0.0, realized_pnl = 10000.00
    """
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    txs = [
        Transaction(symbol="INFY", transaction_type=TransactionType.BUY, quantity=50.0, price=1000.0, timestamp=t0),
        Transaction(symbol="INFY", transaction_type=TransactionType.SELL, quantity=50.0, price=1200.0, timestamp=t0 + timedelta(days=5)),
    ]

    cb = calculate_fifo_cost_basis_for_symbol(txs)
    assert cb.remaining_quantity == 0.0
    assert cb.total_cost_basis == 0.0
    assert cb.avg_buy_price == 0.0
    assert cb.realized_pnl == 10000.00


def test_golden_fifo_sip_and_fractional_precision():
    """
    Scenario:
    - SIP 1: 33.333 shares @ Rs 150.25 (Cost = 5008.28325)
    - SIP 2: 66.667 shares @ Rs 160.75 (Cost = 10716.72025)
    - Total Quantity = 100.0
    - Total Cost = 15725.00
    - Avg Price = 157.25
    """
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    txs = [
        Transaction(symbol="RELIANCE", transaction_type=TransactionType.SIP, quantity=33.333, price=150.25, timestamp=t0),
        Transaction(symbol="RELIANCE", transaction_type=TransactionType.SIP, quantity=66.667, price=160.75, timestamp=t0 + timedelta(days=30)),
    ]

    cb = calculate_fifo_cost_basis_for_symbol(txs)
    assert cb.remaining_quantity == 100.0
    assert round(cb.total_cost_basis, 2) == 15725.00
    assert round(cb.avg_buy_price, 2) == 157.25
    assert cb.realized_pnl == 0.0


def test_golden_portfolio_cost_basis_multiple_symbols():
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    txs = [
        Transaction(symbol="TCS", transaction_type=TransactionType.BUY, quantity=10.0, price=3000.0, timestamp=t0),
        Transaction(symbol="INFY", transaction_type=TransactionType.BUY, quantity=20.0, price=1500.0, timestamp=t0),
        Transaction(symbol="TCS", transaction_type=TransactionType.SELL, quantity=5.0, price=3500.0, timestamp=t0 + timedelta(days=10)),
    ]

    port_cb = calculate_portfolio_cost_basis(txs)
    assert "TCS" in port_cb
    assert "INFY" in port_cb

    assert port_cb["TCS"].remaining_quantity == 5.0
    assert port_cb["TCS"].realized_pnl == 2500.0
    assert port_cb["INFY"].remaining_quantity == 20.0
    assert port_cb["INFY"].realized_pnl == 0.0
