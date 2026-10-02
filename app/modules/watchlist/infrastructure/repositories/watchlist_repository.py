"""
SQLAlchemy Repository implementing the WatchlistRepository application port.
"""

from datetime import datetime, timezone
import logging
from typing import List, Optional, Union
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.watchlist import Watchlist as WatchlistORM, WatchlistItem as WatchlistItemORM
from app.modules.watchlist.application.ports import (
    DuplicateStockError,
    DuplicateWatchlistError,
    WatchlistRepository,
)
from app.modules.watchlist.domain.entities import Watchlist, WatchlistItem
from app.modules.watchlist.infrastructure.mappers import (
    watchlist_item_domain_to_orm,
    watchlist_item_orm_to_domain,
    watchlist_orm_to_domain,
)

logger = logging.getLogger("sentinews.watchlist.repository")


class SQLAlchemyWatchlistRepository(WatchlistRepository):
    """
    Asynchronous SQLAlchemy repository for Watchlist and WatchlistItem data access.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, watchlist_id: int) -> Optional[Watchlist]:
        """Loads a watchlist by ID with all its items."""
        stmt = (
            select(WatchlistORM)
            .where(WatchlistORM.id == watchlist_id)
            .options(selectinload(WatchlistORM.items))
        )
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return watchlist_orm_to_domain(orm)

    async def get_by_user_id(self, user_id: int) -> List[Watchlist]:
        """Loads all watchlists belonging to a user."""
        stmt = (
            select(WatchlistORM)
            .where(WatchlistORM.user_id == user_id)
            .options(selectinload(WatchlistORM.items))
            .order_by(WatchlistORM.created_at.asc())
        )
        result = await self.session.execute(stmt)
        orms = result.scalars().all()
        return [watchlist_orm_to_domain(orm) for orm in orms]

    async def get_by_user_and_name(self, user_id: int, name: str) -> Optional[Watchlist]:
        """Loads a watchlist belonging to a user by name."""
        clean_name = (name or "").strip()
        stmt = (
            select(WatchlistORM)
            .where(
                WatchlistORM.user_id == user_id,
                WatchlistORM.name == clean_name,
            )
            .options(selectinload(WatchlistORM.items))
        )
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return watchlist_orm_to_domain(orm)

    async def get_user_watchlists_summary(self, user_id: int) -> List[Watchlist]:
        """Loads all watchlists belonging to a user with stock_count computed at the database level without loading stock items."""
        stmt = (
            select(
                WatchlistORM,
                func.count(WatchlistItemORM.id).label("stock_count"),
            )
            .outerjoin(WatchlistItemORM, WatchlistORM.id == WatchlistItemORM.watchlist_id)
            .where(WatchlistORM.user_id == user_id)
            .group_by(WatchlistORM.id)
            .order_by(WatchlistORM.created_at.asc())
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        return [
            Watchlist(
                id=orm.id,
                user_id=orm.user_id,
                name=orm.name,
                items=[],
                created_at=orm.created_at,
                updated_at=orm.updated_at,
                _stock_count=stock_count,
            )
            for orm, stock_count in rows
        ]

    async def save_watchlist(self, watchlist: Watchlist) -> Watchlist:
        """Creates or updates a watchlist with concurrency-safe duplicate name protection."""
        if watchlist.id is None:
            orm = WatchlistORM(
                user_id=watchlist.user_id,
                name=watchlist.name,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(orm)
                    await self.session.flush()
            except IntegrityError as e:
                err_str = str(e).lower()
                orig = getattr(e, "orig", None)
                pgcode = getattr(orig, "pgcode", None)
                is_unique_violation = (
                    "uq_watchlist_user_name" in str(e)
                    or "unique constraint" in err_str
                    or "duplicate key" in err_str
                    or pgcode == "23505"
                )
                if is_unique_violation:
                    raise DuplicateWatchlistError(
                        f"Watchlist with name '{watchlist.name}' already exists"
                    ) from e
                raise

            # Eagerly populate the items relationship to avoid a synchronous
            # lazy-load when watchlist_orm_to_domain accesses orm.items.
            # A freshly-created watchlist always has zero items, but the ORM
            # attribute is still in the "expired / unloaded" state after flush.
            await self.session.refresh(orm, ["items"])
            return watchlist_orm_to_domain(orm)
        else:
            stmt = select(WatchlistORM).where(WatchlistORM.id == watchlist.id).options(selectinload(WatchlistORM.items))
            result = await self.session.execute(stmt)
            orm = result.scalar_one()
            orm.name = watchlist.name
            orm.updated_at = datetime.now(timezone.utc)
            try:
                async with self.session.begin_nested():
                    await self.session.flush()
            except IntegrityError as e:
                err_str = str(e).lower()
                orig = getattr(e, "orig", None)
                pgcode = getattr(orig, "pgcode", None)
                is_unique_violation = (
                    "uq_watchlist_user_name" in str(e)
                    or "unique constraint" in err_str
                    or "duplicate key" in err_str
                    or pgcode == "23505"
                )
                if is_unique_violation:
                    raise DuplicateWatchlistError(
                        f"Watchlist with name '{watchlist.name}' already exists"
                    ) from e
                raise

            return watchlist_orm_to_domain(orm)

    async def delete_watchlist(self, watchlist_id: int) -> bool:
        """Deletes a watchlist and all its items (cascading)."""
        stmt = select(WatchlistORM).where(WatchlistORM.id == watchlist_id)
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self.session.delete(orm)
        await self.session.flush()
        return True

    async def add_item(self, watchlist_id: int, item: WatchlistItem) -> WatchlistItem:
        """Adds a stock item to a watchlist with concurrency-safe unique constraint handling."""
        orm = watchlist_item_domain_to_orm(item, watchlist_id=watchlist_id)
        try:
            async with self.session.begin_nested():
                self.session.add(orm)
                await self.session.flush()
        except IntegrityError as e:
            err_str = str(e).lower()
            orig = getattr(e, "orig", None)
            pgcode = getattr(orig, "pgcode", None)
            is_unique_violation = (
                "uq_watchlist_symbol" in str(e)
                or "unique constraint" in err_str
                or "duplicate key" in err_str
                or pgcode == "23505"
            )
            if is_unique_violation:
                raise DuplicateStockError(
                    f"Stock symbol '{item.symbol}' is already present in watchlist {watchlist_id}"
                ) from e
            raise

        return watchlist_item_orm_to_domain(orm)

    async def remove_item(self, watchlist_id: int, symbol_or_item_id: Union[str, int]) -> bool:
        """Removes a stock item from a watchlist by symbol or item ID."""
        if isinstance(symbol_or_item_id, int) or (isinstance(symbol_or_item_id, str) and symbol_or_item_id.isdigit()):
            item_id = int(symbol_or_item_id)
            stmt = select(WatchlistItemORM).where(
                WatchlistItemORM.watchlist_id == watchlist_id,
                WatchlistItemORM.id == item_id,
            )
        else:
            symbol = str(symbol_or_item_id).strip().upper()
            stmt = select(WatchlistItemORM).where(
                WatchlistItemORM.watchlist_id == watchlist_id,
                WatchlistItemORM.symbol == symbol,
            )

        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self.session.delete(orm)
        await self.session.flush()
        return True

    async def get_item_by_symbol(self, watchlist_id: int, symbol: str) -> Optional[WatchlistItem]:
        """Checks if a stock symbol already exists in a watchlist."""
        clean_symbol = symbol.strip().upper()
        stmt = select(WatchlistItemORM).where(
            WatchlistItemORM.watchlist_id == watchlist_id,
            WatchlistItemORM.symbol == clean_symbol,
        )
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return watchlist_item_orm_to_domain(orm)
