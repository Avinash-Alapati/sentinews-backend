"""
Update Watchlist Use Case.
"""

from app.modules.watchlist.application.ports import (
    DuplicateWatchlistError,
    InvalidWatchlistDataError,
    WatchlistNotFoundError,
    WatchlistRepository,
)
from app.modules.watchlist.domain.entities import Watchlist


class UpdateWatchlistUseCase:
    def __init__(self, watchlist_repo: WatchlistRepository):
        self.watchlist_repo = watchlist_repo

    async def execute(self, user_id: int, watchlist_id: int, name: str) -> Watchlist:
        clean_name = (name or "").strip()
        if not clean_name:
            raise InvalidWatchlistDataError("Watchlist name cannot be empty")

        watchlist = await self.watchlist_repo.get_by_id(watchlist_id)
        if watchlist is None or watchlist.user_id != user_id:
            raise WatchlistNotFoundError(f"Watchlist with id {watchlist_id} not found")

        existing = await self.watchlist_repo.get_by_user_and_name(user_id, clean_name)
        if existing is not None and existing.id != watchlist_id:
            raise DuplicateWatchlistError(
                f"Watchlist with name '{clean_name}' already exists"
            )

        watchlist.name = clean_name
        return await self.watchlist_repo.save_watchlist(watchlist)
