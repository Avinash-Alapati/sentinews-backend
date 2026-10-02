"""
Remove Stock from Watchlist Use Case.
"""

from typing import Union
from app.modules.watchlist.application.ports import (
    StockNotFoundError,
    WatchlistNotFoundError,
    WatchlistRepository,
)


class RemoveStockFromWatchlistUseCase:
    def __init__(self, watchlist_repo: WatchlistRepository):
        self.watchlist_repo = watchlist_repo

    async def execute(
        self,
        user_id: int,
        watchlist_id: int,
        symbol_or_item_id: Union[str, int],
    ) -> bool:
        watchlist = await self.watchlist_repo.get_by_id(watchlist_id)
        if watchlist is None or watchlist.user_id != user_id:
            raise WatchlistNotFoundError(f"Watchlist with id {watchlist_id} not found")

        removed = await self.watchlist_repo.remove_item(watchlist_id, symbol_or_item_id)
        if not removed:
            raise StockNotFoundError(
                f"Stock '{symbol_or_item_id}' not found in watchlist {watchlist_id}"
            )
        return True
