"""
FIFO Cost Basis Engine for Portfolio Transactions.

Pure domain service to compute accurate FIFO average buy price,
realized gains, and current cost basis without external dependencies.
"""

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, NamedTuple, Tuple

from app.infrastructure.observability.decorators import track_compute
from app.modules.portfolio.domain.entities import Transaction, TransactionType


from decimal import Decimal, ROUND_HALF_UP

class Lot(NamedTuple):
    quantity: Decimal
    price: Decimal


@dataclass
class HoldingCostBasis:
    symbol: str
    remaining_quantity: float
    total_cost_basis: float
    avg_buy_price: float
    realized_pnl: float


def _to_decimal(val: float | int | str | Decimal) -> Decimal:
    return Decimal(str(val))


def _round_money(val: Decimal) -> float:
    return float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@track_compute("fifo_cost_basis_symbol")
def calculate_fifo_cost_basis_for_symbol(transactions: List[Transaction]) -> HoldingCostBasis:
    """
    Computes FIFO cost basis for a single symbol from its transaction history using Decimal precision.

    Args:
        transactions: List of transactions for a specific symbol.

    Returns:
        HoldingCostBasis with remaining quantity, average buy price, and realized P&L.
    """
    if not transactions:
        return HoldingCostBasis(
            symbol="",
            remaining_quantity=0.0,
            total_cost_basis=0.0,
            avg_buy_price=0.0,
            realized_pnl=0.0,
        )

    # Sort transactions chronologically
    sorted_txs = sorted(transactions, key=lambda tx: tx.timestamp)
    symbol = sorted_txs[0].symbol

    buy_queue: Deque[Lot] = deque()
    realized_pnl = Decimal("0.0")

    for tx in sorted_txs:
        tx_qty = _to_decimal(tx.quantity)
        tx_price = _to_decimal(tx.price)

        if tx.transaction_type in (TransactionType.BUY, TransactionType.SIP):
            buy_queue.append(Lot(quantity=tx_qty, price=tx_price))
        elif tx.transaction_type == TransactionType.SELL:
            remaining_to_sell = tx_qty

            while remaining_to_sell > Decimal("0.0") and buy_queue:
                oldest_lot = buy_queue.popleft()
                if oldest_lot.quantity <= remaining_to_sell:
                    # Fully consume this lot
                    realized_pnl += oldest_lot.quantity * (tx_price - oldest_lot.price)
                    remaining_to_sell -= oldest_lot.quantity
                else:
                    # Partially consume this lot
                    realized_pnl += remaining_to_sell * (tx_price - oldest_lot.price)
                    buy_queue.appendleft(
                        Lot(
                            quantity=oldest_lot.quantity - remaining_to_sell,
                            price=oldest_lot.price,
                        )
                    )
                    remaining_to_sell = Decimal("0.0")

    remaining_qty = sum((lot.quantity for lot in buy_queue), Decimal("0.0"))
    total_cost = sum((lot.quantity * lot.price for lot in buy_queue), Decimal("0.0"))
    avg_price = (total_cost / remaining_qty) if remaining_qty > Decimal("0.0") else Decimal("0.0")

    return HoldingCostBasis(
        symbol=symbol,
        remaining_quantity=float(remaining_qty),
        total_cost_basis=_round_money(total_cost),
        avg_buy_price=_round_money(avg_price),
        realized_pnl=_round_money(realized_pnl),
    )


@track_compute("fifo_cost_basis_portfolio")
def calculate_portfolio_cost_basis(
    transactions: List[Transaction],
) -> Dict[str, HoldingCostBasis]:
    """
    Computes FIFO cost basis across all symbols present in transaction history.
    """
    grouped_txs: Dict[str, List[Transaction]] = {}
    for tx in transactions:
        grouped_txs.setdefault(tx.symbol, []).append(tx)

    return {
        symbol: calculate_fifo_cost_basis_for_symbol(tx_list)
        for symbol, tx_list in grouped_txs.items()
    }
