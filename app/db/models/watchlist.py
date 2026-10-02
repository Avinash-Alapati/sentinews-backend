from typing import List, TYPE_CHECKING
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base_class import Base

if TYPE_CHECKING:
    from app.db.models.user import User


class Watchlist(Base):
    """
    Watchlist collection for an individual user.
    """
    __tablename__ = "watchlists"

    name: Mapped[str] = mapped_column(String(100), default="My Watchlist", nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="watchlists")
    items: Mapped[List["WatchlistItem"]] = relationship(
        "WatchlistItem",
        back_populates="watchlist",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),
    )


class WatchlistItem(Base):
    """
    Stock item within a user's Watchlist.
    """
    __tablename__ = "watchlist_items"

    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(20), default="NSE", nullable=False)
    notes: Mapped[str] = mapped_column(String(255), nullable=True)

    # Relationships
    watchlist: Mapped["Watchlist"] = relationship("Watchlist", back_populates="items")

    __table_args__ = (
        UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_symbol"),
    )
