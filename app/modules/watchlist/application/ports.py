"""
Application Ports and Domain Exceptions for the Watchlist module.
"""

from typing import List, Optional, Protocol, Union
from app.modules.watchlist.domain.entities import Watchlist, WatchlistItem


class WatchlistNotFoundError(Exception):
    """Raised when a watchlist is not found or user lacks access."""
    pass


class DuplicateStockError(Exception):
    """Raised when attempting to add a duplicate stock symbol to a watchlist."""
    pass


class DuplicateWatchlistError(Exception):
    """Raised when attempting to create or rename a watchlist with a duplicate name for the same user."""
    pass


class StockNotFoundError(Exception):
    """Raised when a stock symbol or item is not found in a watchlist."""
    pass


class InvalidWatchlistDataError(Exception):
    """Raised when provided watchlist data fails validation."""
    pass


class WatchlistRepository(Protocol):
    """
    Interface for Watchlist persistence and querying.
    """
    async def get_by_id(self, watchlist_id: int) -> Optional[Watchlist]:
        """Loads a watchlist by ID with its items."""
        ...

    async def get_by_user_id(self, user_id: int) -> List[Watchlist]:
        """Loads all watchlists belonging to a specific user."""
        ...

    async def get_by_user_and_name(self, user_id: int, name: str) -> Optional[Watchlist]:
        """Loads a watchlist belonging to a user by name."""
        ...

    async def get_user_watchlists_summary(self, user_id: int) -> List[Watchlist]:
        """Loads all watchlists belonging to a specific user with aggregated stock count, without loading items."""
        ...

    async def save_watchlist(self, watchlist: Watchlist) -> Watchlist:
        """Persists or updates a watchlist."""
        ...

    async def delete_watchlist(self, watchlist_id: int) -> bool:
        """Deletes a watchlist and all contained stock items."""
        ...

    async def add_item(self, watchlist_id: int, item: WatchlistItem) -> WatchlistItem:
        """Adds a stock item to a watchlist."""
        ...

    async def remove_item(self, watchlist_id: int, symbol_or_item_id: Union[str, int]) -> bool:
        """Removes a stock item from a watchlist by symbol or item ID."""
        ...

    async def get_item_by_symbol(self, watchlist_id: int, symbol: str) -> Optional[WatchlistItem]:
        """Checks if a stock symbol already exists in a watchlist."""
        ...
