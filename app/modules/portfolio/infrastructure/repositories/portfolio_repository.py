"""
SQLAlchemy Repository implementing the PortfolioRepository application port.
"""

from datetime import datetime, timezone
import logging
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.portfolio import HoldingORM, PortfolioORM, TransactionORM
from app.modules.portfolio.application.ports import PortfolioRepository
from app.modules.portfolio.domain.entities import Holding, Portfolio, Transaction
from app.modules.portfolio.domain.services.performance import calculate_portfolio_weights
from app.modules.portfolio.infrastructure.mappers import (
    holding_domain_to_orm,
    holding_orm_to_domain,
    portfolio_orm_to_domain,
    transaction_domain_to_orm,
    transaction_orm_to_domain,
)

logger = logging.getLogger("sentinews.portfolio.repository")


class SQLAlchemyPortfolioRepository(PortfolioRepository):
    """
    Asynchronous SQLAlchemy repository for Portfolio data access.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_portfolio(self, portfolio_id: int) -> Optional[Portfolio]:
        """Loads a portfolio with its holdings and transactions."""
        stmt = (
            select(PortfolioORM)
            .where(PortfolioORM.id == portfolio_id)
            .options(
                selectinload(PortfolioORM.holdings),
                selectinload(PortfolioORM.transactions),
            )
        )
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return portfolio_orm_to_domain(orm)

    async def get_portfolio_metadata(self, portfolio_id: int) -> Optional[Portfolio]:
        """Loads only portfolio metadata without eager loading holdings or transactions."""
        stmt = select(PortfolioORM).where(PortfolioORM.id == portfolio_id)
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return Portfolio(
            id=orm.id,
            user_id=orm.user_id,
            name=orm.name,
            cash_balance=orm.cash_balance,
            holdings=[],
            transactions=[],
        )

    async def get_portfolio_by_user(self, user_id: int) -> Optional[Portfolio]:
        """Loads a user's primary portfolio."""
        stmt = (
            select(PortfolioORM)
            .where(PortfolioORM.user_id == user_id)
            .options(
                selectinload(PortfolioORM.holdings),
                selectinload(PortfolioORM.transactions),
            )
        )
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return portfolio_orm_to_domain(orm)

    async def get_portfolio_by_user_metadata(self, user_id: int) -> Optional[Portfolio]:
        """Loads a user's primary portfolio metadata without eager loading holdings or transactions."""
        stmt = select(PortfolioORM).where(PortfolioORM.user_id == user_id)
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return Portfolio(
            id=orm.id,
            user_id=orm.user_id,
            name=orm.name,
            cash_balance=orm.cash_balance,
            holdings=[],
            transactions=[],
        )

    async def get_holdings_with_weights(self, portfolio_id: int) -> List[Holding]:
        """
        Retrieves portfolio holdings and computes current allocation weights.
        Respects module boundary by returning pure domain entities.
        """
        stmt = select(HoldingORM).where(HoldingORM.portfolio_id == portfolio_id)
        result = await self.session.execute(stmt)
        orms = result.scalars().all()

        holdings = [holding_orm_to_domain(h) for h in orms]
        # Calculate dynamic portfolio weights using performance domain service
        weighted_holdings = calculate_portfolio_weights(holdings)
        return weighted_holdings

    async def get_holdings(self, portfolio_id: int) -> List[Holding]:
        """Retrieves active holdings for a portfolio."""
        stmt = select(HoldingORM).where(
            HoldingORM.portfolio_id == portfolio_id,
            HoldingORM.quantity > 0,
        )
        result = await self.session.execute(stmt)
        orms = result.scalars().all()
        return [holding_orm_to_domain(h) for h in orms]

    async def get_transactions(
        self,
        portfolio_id: int,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Transaction]:
        """Retrieves transactions for a portfolio ordered chronologically with optional SQL LIMIT and OFFSET."""
        stmt = (
            select(TransactionORM)
            .where(TransactionORM.portfolio_id == portfolio_id)
            .order_by(TransactionORM.timestamp.desc())
        )
        if offset is not None and offset > 0:
            stmt = stmt.offset(offset)
        if limit is not None and limit > 0:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        orms = result.scalars().all()
        return [transaction_orm_to_domain(t) for t in orms]

    async def get_transactions_for_symbol(
        self,
        portfolio_id: int,
        symbol: str,
    ) -> List[Transaction]:
        """Retrieves chronological transactions for a specific symbol."""
        stmt = (
            select(TransactionORM)
            .where(
                TransactionORM.portfolio_id == portfolio_id,
                TransactionORM.symbol == symbol.strip().upper(),
            )
            .order_by(TransactionORM.timestamp.asc())
        )
        result = await self.session.execute(stmt)
        orms = result.scalars().all()
        return [transaction_orm_to_domain(t) for t in orms]

    async def get_holding_by_symbol(
        self,
        portfolio_id: int,
        symbol: str,
    ) -> Optional[Holding]:
        """Retrieves holding record for a specific symbol."""
        stmt = select(HoldingORM).where(
            HoldingORM.portfolio_id == portfolio_id,
            HoldingORM.symbol == symbol.strip().upper(),
        )
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        return holding_orm_to_domain(orm) if orm else None

    async def add_holding(self, portfolio_id: int, holding: Holding) -> Holding:
        """Adds or updates a holding in a portfolio and persists to DB."""
        stmt = select(HoldingORM).where(
            HoldingORM.portfolio_id == portfolio_id,
            HoldingORM.symbol == holding.symbol.upper(),
        )
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is not None:
            orm.quantity = holding.quantity
            orm.avg_buy_price = holding.avg_buy_price
            if holding.name:
                orm.name = holding.name
            if holding.sector:
                orm.sector = holding.sector
            orm.updated_at = datetime.now(timezone.utc)
            await self.session.flush()
            await self.session.refresh(orm)
            return holding_orm_to_domain(orm)
        else:
            orm = holding_domain_to_orm(holding, portfolio_id=portfolio_id)
            self.session.add(orm)
            await self.session.flush()
            await self.session.refresh(orm)
            return holding_orm_to_domain(orm)

    async def add_transaction(
        self,
        portfolio_id: int,
        transaction: Transaction,
    ) -> Transaction:
        """Records a new buy or sell transaction."""
        orm = transaction_domain_to_orm(transaction, portfolio_id=portfolio_id)
        self.session.add(orm)
        await self.session.flush()
        await self.session.refresh(orm)
        return transaction_orm_to_domain(orm)

    async def save_portfolio(self, portfolio: Portfolio) -> Portfolio:
        """Creates or updates a portfolio."""
        if portfolio.id is None:
            orm = PortfolioORM(
                user_id=portfolio.user_id,
                name=portfolio.name,
                cash_balance=portfolio.cash_balance,
            )
            self.session.add(orm)
            await self.session.flush()
            await self.session.refresh(orm)
            return portfolio_orm_to_domain(orm)
        else:
            stmt = select(PortfolioORM).where(PortfolioORM.id == portfolio.id)
            result = await self.session.execute(stmt)
            orm = result.scalar_one()
            orm.name = portfolio.name
            orm.cash_balance = portfolio.cash_balance
            await self.session.flush()
            return portfolio_orm_to_domain(orm)

    async def delete_portfolio(self, portfolio_id: int) -> bool:
        """Deletes a portfolio and its cascades."""
        stmt = select(PortfolioORM).where(PortfolioORM.id == portfolio_id)
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return False
        await self.session.delete(orm)
        await self.session.flush()
        return True
