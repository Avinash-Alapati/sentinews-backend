"""
FastAPI Dependency Injection wiring for Watchlist endpoints.
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.watchlist.application.use_cases.add_stock_to_watchlist import (
    AddStockToWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.create_watchlist import (
    CreateWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.delete_watchlist import (
    DeleteWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.get_watchlist import (
    GetWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.get_watchlists import (
    GetWatchlistsUseCase,
)
from app.modules.watchlist.application.use_cases.remove_stock_from_watchlist import (
    RemoveStockFromWatchlistUseCase,
)
from app.modules.watchlist.application.use_cases.update_watchlist import (
    UpdateWatchlistUseCase,
)
from app.modules.watchlist.infrastructure.repositories.watchlist_repository import (
    SQLAlchemyWatchlistRepository,
)


def get_watchlist_repository(
    db: AsyncSession = Depends(get_db),
) -> SQLAlchemyWatchlistRepository:
    return SQLAlchemyWatchlistRepository(session=db)


def get_create_watchlist_use_case(
    repo: SQLAlchemyWatchlistRepository = Depends(get_watchlist_repository),
) -> CreateWatchlistUseCase:
    return CreateWatchlistUseCase(watchlist_repo=repo)


def get_get_watchlists_use_case(
    repo: SQLAlchemyWatchlistRepository = Depends(get_watchlist_repository),
) -> GetWatchlistsUseCase:
    return GetWatchlistsUseCase(watchlist_repo=repo)


def get_get_watchlist_use_case(
    repo: SQLAlchemyWatchlistRepository = Depends(get_watchlist_repository),
) -> GetWatchlistUseCase:
    return GetWatchlistUseCase(watchlist_repo=repo)


def get_update_watchlist_use_case(
    repo: SQLAlchemyWatchlistRepository = Depends(get_watchlist_repository),
) -> UpdateWatchlistUseCase:
    return UpdateWatchlistUseCase(watchlist_repo=repo)


def get_delete_watchlist_use_case(
    repo: SQLAlchemyWatchlistRepository = Depends(get_watchlist_repository),
) -> DeleteWatchlistUseCase:
    return DeleteWatchlistUseCase(watchlist_repo=repo)


def get_add_stock_to_watchlist_use_case(
    repo: SQLAlchemyWatchlistRepository = Depends(get_watchlist_repository),
) -> AddStockToWatchlistUseCase:
    return AddStockToWatchlistUseCase(watchlist_repo=repo)


def get_remove_stock_from_watchlist_use_case(
    repo: SQLAlchemyWatchlistRepository = Depends(get_watchlist_repository),
) -> RemoveStockFromWatchlistUseCase:
    return RemoveStockFromWatchlistUseCase(watchlist_repo=repo)
