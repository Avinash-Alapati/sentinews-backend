from typing import List, TYPE_CHECKING
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base_class import Base

if TYPE_CHECKING:
    from app.db.models.watchlist import Watchlist
    from app.db.models.portfolio import PortfolioORM


class User(Base):
    """
    User Account model.
    """
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    watchlists: Mapped[List["Watchlist"]] = relationship(
        "Watchlist",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    portfolios: Mapped[List["PortfolioORM"]] = relationship(
        "PortfolioORM",
        back_populates="user",
        cascade="all, delete-orphan",
    )
