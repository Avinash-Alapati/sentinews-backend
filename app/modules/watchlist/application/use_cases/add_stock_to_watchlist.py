"""
Add Stock to Watchlist Use Case.
"""

from typing import Optional
from app.modules.watchlist.application.ports import (
    DuplicateStockError,
    InvalidWatchlistDataError,
    WatchlistNotFoundError,
    WatchlistRepository,
)
from app.modules.watchlist.domain.entities import WatchlistItem


class AddStockToWatchlistUseCase:
    def __init__(self, watchlist_repo: WatchlistRepository):
        self.watchlist_repo = watchlist_repo

    async def execute(
        self,
        user_id: int,
        watchlist_id: int,
        symbol: str,
        exchange: str = "NSE",
        notes: Optional[str] = None,
    ) -> WatchlistItem:
        clean_symbol = (symbol or "").strip().upper()
        if not clean_symbol:
            raise InvalidWatchlistDataError("Stock symbol cannot be empty")

        clean_exchange = (exchange or "NSE").strip().upper()

        watchlist = await self.watchlist_repo.get_by_id(watchlist_id)
        if watchlist is None or watchlist.user_id != user_id:
            raise WatchlistNotFoundError(f"Watchlist with id {watchlist_id} not found")

        # Check for duplicates
        existing = await self.watchlist_repo.get_item_by_symbol(watchlist_id, clean_symbol)
        if existing is not None:
            raise DuplicateStockError(
                f"Stock symbol '{clean_symbol}' is already present in watchlist {watchlist_id}"
            )

        new_item = WatchlistItem(
            watchlist_id=watchlist_id,
            symbol=clean_symbol,
            exchange=clean_exchange,
            notes=notes.strip() if notes else None,
        )
        return await self.watchlist_repo.add_item(watchlist_id, new_item)
