"""
Mappers translating between Watchlist SQLAlchemy ORM models and Domain Entities.
"""

from typing import List
from app.db.models.watchlist import Watchlist as WatchlistORM, WatchlistItem as WatchlistItemORM
from app.modules.watchlist.domain.entities import Watchlist, WatchlistItem


def watchlist_item_orm_to_domain(orm: WatchlistItemORM) -> WatchlistItem:
    """Converts a WatchlistItem ORM model to a WatchlistItem domain entity."""
    return WatchlistItem(
        id=orm.id,
        watchlist_id=orm.watchlist_id,
        symbol=orm.symbol,
        exchange=orm.exchange,
        notes=orm.notes,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def watchlist_item_domain_to_orm(domain: WatchlistItem, watchlist_id: int) -> WatchlistItemORM:
    """Converts a WatchlistItem domain entity to a WatchlistItem ORM model."""
    return WatchlistItemORM(
        id=domain.id,
        watchlist_id=watchlist_id,
        symbol=domain.symbol,
        exchange=domain.exchange,
        notes=domain.notes,
    )


def watchlist_orm_to_domain(orm: WatchlistORM) -> Watchlist:
    """Converts a Watchlist ORM model to a Watchlist domain entity."""
    items: List[WatchlistItem] = [
        watchlist_item_orm_to_domain(item) for item in (orm.items or [])
    ]
    return Watchlist(
        id=orm.id,
        user_id=orm.user_id,
        name=orm.name,
        items=items,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def watchlist_domain_to_orm(domain: Watchlist) -> WatchlistORM:
    """Converts a Watchlist domain entity to a Watchlist ORM model."""
    return WatchlistORM(
        id=domain.id,
        user_id=domain.user_id,
        name=domain.name,
    )
