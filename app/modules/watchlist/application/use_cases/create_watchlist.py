"""
Create Watchlist Use Case.
"""

from app.modules.watchlist.application.ports import (
    DuplicateWatchlistError,
    InvalidWatchlistDataError,
    WatchlistRepository,
)
from app.modules.watchlist.domain.entities import Watchlist


class CreateWatchlistUseCase:
    def __init__(self, watchlist_repo: WatchlistRepository):
        self.watchlist_repo = watchlist_repo

    async def execute(self, user_id: int, name: str) -> Watchlist:
        clean_name = (name or "").strip()
        if not clean_name:
            raise InvalidWatchlistDataError("Watchlist name cannot be empty")

        existing = await self.watchlist_repo.get_by_user_and_name(user_id, clean_name)
        if existing is not None:
            raise DuplicateWatchlistError(
                f"Watchlist with name '{clean_name}' already exists"
            )

        new_watchlist = Watchlist(
            user_id=user_id,
            name=clean_name,
        )
        return await self.watchlist_repo.save_watchlist(new_watchlist)
