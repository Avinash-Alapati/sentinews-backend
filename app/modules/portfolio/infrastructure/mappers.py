"""
Mappers translating between Portfolio SQLAlchemy ORM models and Domain Entities.
"""

from typing import List
from app.db.models.portfolio import HoldingORM, PortfolioORM, TransactionORM
from app.modules.portfolio.domain.entities import Holding, Portfolio, Transaction, TransactionType


def holding_orm_to_domain(orm: HoldingORM) -> Holding:
    """Converts a Holding ORM model to a Holding domain entity."""
    return Holding(
        id=orm.id,
        portfolio_id=orm.portfolio_id,
        symbol=orm.symbol,
        name=orm.name,
        sector=orm.sector,
        quantity=orm.quantity,
        avg_buy_price=orm.avg_buy_price,
        weight_pct=orm.weight_pct,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def holding_domain_to_orm(domain: Holding, portfolio_id: int) -> HoldingORM:
    """Converts a Holding domain entity to a Holding ORM model."""
    return HoldingORM(
        id=domain.id,
        portfolio_id=portfolio_id,
        symbol=domain.symbol,
        name=domain.name,
        sector=domain.sector,
        quantity=domain.quantity,
        avg_buy_price=domain.avg_buy_price,
        weight_pct=domain.weight_pct,
    )


def transaction_orm_to_domain(orm: TransactionORM) -> Transaction:
    """Converts a Transaction ORM model to a Transaction domain entity."""
    return Transaction(
        id=orm.id,
        portfolio_id=orm.portfolio_id,
        symbol=orm.symbol,
        transaction_type=TransactionType(orm.transaction_type),
        quantity=orm.quantity,
        price=orm.price,
        timestamp=orm.timestamp,
        notes=orm.notes,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def transaction_domain_to_orm(domain: Transaction, portfolio_id: int) -> TransactionORM:
    """Converts a Transaction domain entity to a Transaction ORM model."""
    return TransactionORM(
        id=domain.id,
        portfolio_id=portfolio_id,
        symbol=domain.symbol,
        transaction_type=domain.transaction_type.value,
        quantity=domain.quantity,
        price=domain.price,
        timestamp=domain.timestamp,
        notes=domain.notes,
    )


def portfolio_orm_to_domain(orm: PortfolioORM) -> Portfolio:
    """Converts a Portfolio ORM model to a Portfolio domain entity."""
    holdings: List[Holding] = [holding_orm_to_domain(h) for h in (orm.holdings or [])]
    transactions: List[Transaction] = [
        transaction_orm_to_domain(t) for t in (orm.transactions or [])
    ]
    return Portfolio(
        id=orm.id,
        user_id=orm.user_id,
        name=orm.name,
        cash_balance=orm.cash_balance,
        holdings=holdings,
        transactions=transactions,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )
