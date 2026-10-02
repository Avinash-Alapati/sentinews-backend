"""
Unit tests for Portfolio pagination and lightweight metadata queries (Batch 4).
"""

from datetime import datetime, timezone, timedelta
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.v1.auth.dependencies import get_current_active_user, get_user_repository
from app.api.v1.portfolio.dependencies import get_portfolio_repository
from app.db.base import Base
from app.main import app
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.modules.portfolio.domain.entities import Holding, Portfolio, Transaction, TransactionType
from app.modules.portfolio.infrastructure.repositories.portfolio_repository import (
    SQLAlchemyPortfolioRepository,
)


@pytest.fixture
async def async_portfolio_db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_portfolio_metadata_query_isolation(async_portfolio_db: AsyncSession):
    """Test that get_portfolio_metadata returns lightweight model without eager loading relations."""
    repo = SQLAlchemyPortfolioRepository(session=async_portfolio_db)

    # 1. Create portfolio
    portfolio = await repo.save_portfolio(
        Portfolio(user_id=1, name="Retirement Fund", cash_balance=10000.0)
    )
    assert portfolio.id is not None

    # 2. Add holding and transaction
    await repo.add_holding(
        portfolio.id,
        Holding(symbol="RELIANCE", name="Reliance Industries", sector="Energy", quantity=50, avg_buy_price=2500.0),
    )
    await repo.add_transaction(
        portfolio.id,
        Transaction(
            symbol="RELIANCE",
            transaction_type=TransactionType.BUY,
            quantity=50,
            price=2500.0,
        ),
    )
    await async_portfolio_db.commit()

    # 3. Test get_portfolio_metadata
    meta = await repo.get_portfolio_metadata(portfolio.id)
    assert meta is not None
    assert meta.id == portfolio.id
    assert meta.name == "Retirement Fund"
    assert meta.cash_balance == 10000.0
    assert len(meta.holdings) == 0
    assert len(meta.transactions) == 0

    # 4. Test get_portfolio_by_user_metadata
    user_meta = await repo.get_portfolio_by_user_metadata(1)
    assert user_meta is not None
    assert user_meta.id == portfolio.id
    assert len(user_meta.holdings) == 0
    assert len(user_meta.transactions) == 0

    # 5. Full get_portfolio still eager loads relations
    full = await repo.get_portfolio(portfolio.id)
    assert full is not None
    assert len(full.holdings) == 1
    assert len(full.transactions) == 1


@pytest.mark.asyncio
async def test_transactions_sql_pagination(async_portfolio_db: AsyncSession):
    """Test SQL-level limit and offset on get_transactions."""
    repo = SQLAlchemyPortfolioRepository(session=async_portfolio_db)

    portfolio = await repo.save_portfolio(
        Portfolio(user_id=2, name="Day Trading", cash_balance=50000.0)
    )
    assert portfolio.id is not None

    base_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    # Insert 25 transactions with timestamps 1 hour apart
    for i in range(25):
        tx = Transaction(
            symbol="INFY" if i % 2 == 0 else "TCS",
            transaction_type=TransactionType.BUY if i % 3 != 0 else TransactionType.SELL,
            quantity=10.0 + i,
            price=1500.0 + (i * 10),
            timestamp=base_time + timedelta(hours=i),
        )
        await repo.add_transaction(portfolio.id, tx)

    await async_portfolio_db.commit()

    # 1. Query all transactions
    all_txs = await repo.get_transactions(portfolio.id)
    assert len(all_txs) == 25
    # Should be ordered descending by timestamp (latest first)
    first_ts = all_txs[0].timestamp.replace(tzinfo=timezone.utc) if all_txs[0].timestamp.tzinfo is None else all_txs[0].timestamp
    last_ts = all_txs[-1].timestamp.replace(tzinfo=timezone.utc) if all_txs[-1].timestamp.tzinfo is None else all_txs[-1].timestamp
    assert first_ts == base_time + timedelta(hours=24)
    assert last_ts == base_time

    # 2. Query Page 1 (limit=10, offset=0)
    page1 = await repo.get_transactions(portfolio.id, limit=10, offset=0)
    assert len(page1) == 10
    p1_first = page1[0].timestamp.replace(tzinfo=timezone.utc) if page1[0].timestamp.tzinfo is None else page1[0].timestamp
    p1_last = page1[-1].timestamp.replace(tzinfo=timezone.utc) if page1[-1].timestamp.tzinfo is None else page1[-1].timestamp
    assert p1_first == base_time + timedelta(hours=24)
    assert p1_last == base_time + timedelta(hours=15)

    # 3. Query Page 2 (limit=10, offset=10)
    page2 = await repo.get_transactions(portfolio.id, limit=10, offset=10)
    assert len(page2) == 10
    p2_first = page2[0].timestamp.replace(tzinfo=timezone.utc) if page2[0].timestamp.tzinfo is None else page2[0].timestamp
    p2_last = page2[-1].timestamp.replace(tzinfo=timezone.utc) if page2[-1].timestamp.tzinfo is None else page2[-1].timestamp
    assert p2_first == base_time + timedelta(hours=14)
    assert p2_last == base_time + timedelta(hours=5)

    # 4. Query Page 3 (limit=10, offset=20) -> should have remaining 5
    page3 = await repo.get_transactions(portfolio.id, limit=10, offset=20)
    assert len(page3) == 5
    p3_first = page3[0].timestamp.replace(tzinfo=timezone.utc) if page3[0].timestamp.tzinfo is None else page3[0].timestamp
    p3_last = page3[-1].timestamp.replace(tzinfo=timezone.utc) if page3[-1].timestamp.tzinfo is None else page3[-1].timestamp
    assert p3_first == base_time + timedelta(hours=4)
    assert p3_last == base_time


@pytest.mark.asyncio
async def test_api_transactions_pagination_flow():
    """Test API endpoint GET /api/v1/portfolio/{id}/transactions with page and limit query params."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_port_repo():
        async with session_factory() as session:
            try:
                yield SQLAlchemyPortfolioRepository(session=session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_user_repo():
        async with session_factory() as session:
            try:
                yield SQLAlchemyUserRepository(session=session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # Create test user and portfolio with 15 transactions
    async with session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session=session)
        user = await user_repo.create(User(email="paginated.trader@sentinews.in", hashed_password="pwd"))
        
        port_repo = SQLAlchemyPortfolioRepository(session=session)
        port = await port_repo.save_portfolio(Portfolio(user_id=user.id, name="Test Algo Portfolio"))
        
        base_time = datetime(2026, 3, 1, 9, 30, 0, tzinfo=timezone.utc)
        for i in range(15):
            await port_repo.add_transaction(
                port.id,
                Transaction(
                    symbol="HDFCBANK",
                    transaction_type=TransactionType.BUY,
                    quantity=5.0 * (i + 1),
                    price=1600.0 + i,
                    timestamp=base_time + timedelta(minutes=i * 15),
                ),
            )
        await session.commit()
        user_id = user.id
        port_id = port.id

    app.dependency_overrides[get_portfolio_repository] = override_get_port_repo
    app.dependency_overrides[get_user_repository] = override_get_user_repo
    app.dependency_overrides[get_current_active_user] = lambda: user

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Test GET /portfolio/{id}?metadata_only=true
            meta_res = await client.get(f"/api/v1/portfolio/{port_id}?metadata_only=true")
            assert meta_res.status_code == 200
            assert meta_res.json()["name"] == "Test Algo Portfolio"

            # 2. Test GET /portfolio/{id}/transactions?page=1&limit=5
            res1 = await client.get(f"/api/v1/portfolio/{port_id}/transactions?page=1&limit=5")
            assert res1.status_code == 200
            txs1 = res1.json()
            assert len(txs1) == 5
            assert txs1[0]["quantity"] == 75.0  # 15th transaction (i=14 -> 5*15 = 75.0)

            # 3. Test GET /portfolio/{id}/transactions?page=2&limit=5
            res2 = await client.get(f"/api/v1/portfolio/{port_id}/transactions?page=2&limit=5")
            assert res2.status_code == 200
            txs2 = res2.json()
            assert len(txs2) == 5
            assert txs2[0]["quantity"] == 50.0  # 10th transaction (i=9 -> 5*10 = 50.0)

            # 4. Test GET /portfolio/{id}/transactions?page=3&limit=5
            res3 = await client.get(f"/api/v1/portfolio/{port_id}/transactions?page=3&limit=5")
            assert res3.status_code == 200
            txs3 = res3.json()
            assert len(txs3) == 5

            # 5. Test GET /portfolio/{id}/transactions?page=4&limit=5 (empty page)
            res4 = await client.get(f"/api/v1/portfolio/{port_id}/transactions?page=4&limit=5")
            assert res4.status_code == 200
            assert res4.json() == []
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
