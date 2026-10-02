"""
API flow tests for Portfolio endpoints:
Create portfolio -> Record buy/sell transactions -> Get overview -> Get allocation.
Validates FIFO P&L numbers against known mathematical calculations.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy.pool import StaticPool

from app.api.v1.auth.dependencies import get_current_active_user, get_user_repository
from app.api.v1.portfolio.dependencies import (
    get_market_data_provider,
    get_portfolio_repository,
    get_relevance_adapter,
)
from app.db.base import Base
from app.main import app
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.modules.portfolio.infrastructure.repositories.portfolio_repository import (
    SQLAlchemyPortfolioRepository,
)


class MockMarketDataProvider:
    async def get_current_price(self, symbol: str):
        prices = {"TCS": 220.0, "INFY": 1600.0}
        return prices.get(symbol.upper(), 100.0)

    async def get_quotes_batch(self, symbols):
        prices = {"TCS": 220.0, "INFY": 1600.0}
        return {s.upper(): prices.get(s.upper(), 100.0) for s in symbols}


class MockRelevancePort:
    async def notify_holding_added(self, holding):
        pass


@pytest.fixture
async def test_portfolio_client():
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

    # Pre-create a user
    async with session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session=session)
        user = await user_repo.create(User(email="test.trader@sentinews.in", hashed_password="pwd"))
        await session.commit()
        user_id = user.id

    app.dependency_overrides[get_portfolio_repository] = override_get_port_repo
    app.dependency_overrides[get_user_repository] = override_get_user_repo
    app.dependency_overrides[get_market_data_provider] = lambda: MockMarketDataProvider()
    app.dependency_overrides[get_relevance_adapter] = lambda: MockRelevancePort()
    app.dependency_overrides[get_current_active_user] = lambda: user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, user_id

    app.dependency_overrides.clear()
    await engine.dispose()



@pytest.mark.asyncio
async def test_portfolio_full_lifecycle_and_fifo_pnl(test_portfolio_client):
    client, user_id = test_portfolio_client

    # 1. Create Portfolio
    create_res = await client.post(
        "/api/v1/portfolio",
        json={"user_id": user_id, "name": "Growth Equities"},
    )
    assert create_res.status_code == 201
    port_id = create_res.json()["id"]

    # 2. Record transactions matching FIFO test fixture:
    # Buy 10 @ 100
    r1 = await client.post(
        f"/api/v1/portfolio/{port_id}/transactions",
        json={"symbol": "TCS", "transaction_type": "BUY", "quantity": 10.0, "price": 100.0, "sector": "IT"},
    )
    assert r1.status_code == 201

    # Buy 10 @ 150
    r2 = await client.post(
        f"/api/v1/portfolio/{port_id}/transactions",
        json={"symbol": "TCS", "transaction_type": "BUY", "quantity": 10.0, "price": 150.0, "sector": "IT"},
    )
    assert r2.status_code == 201

    # Sell 15 @ 200 -> Realized PnL: (10 * 100) + (5 * 50) = 1000 + 250 = 1250.
    # Remaining: 5 shares @ 150 avg cost = 750 total cost.
    r3 = await client.post(
        f"/api/v1/portfolio/{port_id}/transactions",
        json={"symbol": "TCS", "transaction_type": "SELL", "quantity": 15.0, "price": 200.0, "sector": "IT"},
    )
    assert r3.status_code == 201

    # 3. Get Portfolio Overview (Mock market price for TCS = 220.0)
    # Current value: 5 * 220 = 1100. Total Cost = 750.
    # Unrealized PnL = 1100 - 750 = 350.
    # Realized PnL = 1250.
    ov_res = await client.get(f"/api/v1/portfolio/{port_id}/overview")
    assert ov_res.status_code == 200
    ov = ov_res.json()

    assert ov["total_invested_value"] == 750.0
    assert ov["total_current_value"] == 1100.0
    assert ov["total_unrealized_pnl"] == 350.0
    assert ov["total_realized_pnl"] == 1250.0

    holding = ov["holdings"][0]
    assert holding["symbol"] == "TCS"
    assert holding["quantity"] == 5.0
    assert holding["avg_buy_price"] == 150.0
    assert holding["current_price"] == 220.0
    assert holding["unrealized_pnl"] == 350.0

    # 4. Get Portfolio Allocation
    alloc_res = await client.get(f"/api/v1/portfolio/{port_id}/allocation")
    assert alloc_res.status_code == 200
    alloc = alloc_res.json()

    assert len(alloc["holdings"]) == 1
    assert alloc["holdings"][0]["symbol"] == "TCS"
    assert alloc["holdings"][0]["weight_pct"] == 1.0
    assert len(alloc["sectors"]) == 1
    assert alloc["sectors"][0]["sector"] == "IT"
