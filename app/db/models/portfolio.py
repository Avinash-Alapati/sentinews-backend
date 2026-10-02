"""
SQLAlchemy ORM models for the Portfolio module.
"""

from typing import List, TYPE_CHECKING
from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.modules.portfolio.domain.entities import TransactionType

if TYPE_CHECKING:
    from app.db.models.user import User


class PortfolioORM(Base):
    """
    SQLAlchemy ORM model for user portfolios.
    """
    __tablename__ = "portfolios"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), default="My Portfolio", nullable=False)
    cash_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="portfolios")
    holdings: Mapped[List["HoldingORM"]] = relationship(
        "HoldingORM",
        back_populates="portfolio",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    transactions: Mapped[List["TransactionORM"]] = relationship(
        "TransactionORM",
        back_populates="portfolio",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class HoldingORM(Base):
    """
    SQLAlchemy ORM model for portfolio holdings.
    """
    __tablename__ = "holdings"

    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    sector: Mapped[str] = mapped_column(String(100), default="General", nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_buy_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    weight_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Relationships
    portfolio: Mapped["PortfolioORM"] = relationship("PortfolioORM", back_populates="holdings")


class TransactionORM(Base):
    """
    SQLAlchemy ORM model for portfolio transactions.
    """
    __tablename__ = "transactions"

    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    transaction_type: Mapped[str] = mapped_column(
        String(10),
        default="BUY",
        nullable=False,
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    notes: Mapped[str] = mapped_column(String(255), nullable=True)

    # Relationships
    portfolio: Mapped["PortfolioORM"] = relationship("PortfolioORM", back_populates="transactions")
