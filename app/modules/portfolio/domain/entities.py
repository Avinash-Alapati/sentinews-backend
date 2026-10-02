"""
Domain entities for the Portfolio module.

Domain entities are separate plain dataclasses, never ORM classes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


class TransactionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    SIP = "SIP"
    DIVIDEND = "DIVIDEND"
    TRANSFER = "TRANSFER"


@dataclass
class Holding:
    """
    Plain domain entity representing a portfolio holding.
    """
    symbol: str
    quantity: float
    avg_buy_price: float
    name: str = ""
    sector: str = "General"
    current_price: Optional[float] = None
    weight_pct: float = 0.0  # Fraction (0.0 to 1.0) or percentage (0 to 100)
    id: Optional[int] = None
    portfolio_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def total_cost(self) -> float:
        return self.quantity * self.avg_buy_price

    @property
    def current_value(self) -> float:
        price = self.current_price if self.current_price is not None else self.avg_buy_price
        return self.quantity * price


@dataclass
class Transaction:
    """
    Plain domain entity representing a portfolio transaction.
    """
    symbol: str
    transaction_type: TransactionType
    quantity: float
    price: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: Optional[int] = None
    portfolio_id: Optional[int] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Portfolio:
    """
    Plain domain entity representing a user's portfolio.
    """
    user_id: int
    name: str = "My Portfolio"
    id: Optional[int] = None
    cash_balance: float = 0.0
    holdings: List[Holding] = field(default_factory=list)
    transactions: List[Transaction] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def total_invested_value(self) -> float:
        return sum(h.total_cost for h in self.holdings)

    @property
    def total_current_value(self) -> float:
        return sum(h.current_value for h in self.holdings) + self.cash_balance
