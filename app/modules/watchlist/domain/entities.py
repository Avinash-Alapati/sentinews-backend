"""
Domain entities for the Watchlist module.

Domain entities are separate plain dataclasses, never ORM classes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class WatchlistItem:
    """
    Plain domain entity representing a stock item within a user's Watchlist.
    """
    symbol: str
    exchange: str = "NSE"
    notes: Optional[str] = None
    id: Optional[int] = None
    watchlist_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Watchlist:
    """
    Plain domain entity representing a user's Watchlist collection.
    """
    user_id: int
    name: str = "My Watchlist"
    id: Optional[int] = None
    items: List[WatchlistItem] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    _stock_count: Optional[int] = None

    @property
    def stock_count(self) -> int:
        if self._stock_count is not None:
            return self._stock_count
        return len(self.items)
