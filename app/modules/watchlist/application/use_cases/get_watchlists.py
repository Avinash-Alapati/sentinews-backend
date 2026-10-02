"""
Get User's Watchlists Use Case.
"""

from typing import List
from app.modules.watchlist.application.ports import WatchlistRepository
from app.modules.watchlist.domain.entities import Watchlist


class GetWatchlistsUseCase:
    def __init__(self, watchlist_repo: WatchlistRepository):
        self.watchlist_repo = watchlist_repo

    async def execute(self, user_id: int, summary: bool = False) -> List[Watchlist]:
        if summary and hasattr(self.watchlist_repo, "get_user_watchlists_summary"):
            return await self.watchlist_repo.get_user_watchlists_summary(user_id)
        return await self.watchlist_repo.get_by_user_id(user_id)
