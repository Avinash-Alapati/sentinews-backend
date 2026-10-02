"""
Delete Watchlist Use Case.
"""

from app.modules.watchlist.application.ports import (
    WatchlistNotFoundError,
    WatchlistRepository,
)


class DeleteWatchlistUseCase:
    def __init__(self, watchlist_repo: WatchlistRepository):
        self.watchlist_repo = watchlist_repo

    async def execute(self, user_id: int, watchlist_id: int) -> bool:
        watchlist = await self.watchlist_repo.get_by_id(watchlist_id)
        if watchlist is None or watchlist.user_id != user_id:
            raise WatchlistNotFoundError(f"Watchlist with id {watchlist_id} not found")

        return await self.watchlist_repo.delete_watchlist(watchlist_id)
