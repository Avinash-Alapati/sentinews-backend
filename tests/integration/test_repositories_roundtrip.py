"""
Repository integration tests verifying ORM <-> Domain entity roundtrips,
data fidelity, and decimal/float precision across all modules.
"""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.infrastructure.repositories.news_repository import (
    SQLAlchemyNewsRepository,
)
from app.modules.portfolio.domain.entities import Holding, Portfolio, Transaction, TransactionType
from app.modules.portfolio.infrastructure.repositories.portfolio_repository import (
    SQLAlchemyPortfolioRepository,
)


@pytest.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_user_repository_crud_roundtrip(async_db: AsyncSession):
    repo = SQLAlchemyUserRepository(session=async_db)

    # 1. Create
    user = User(
        email="trader@sentinews.in",
        hashed_password="hashed_secure_password_abc123",
        full_name="Rajesh Sharma",
        is_active=True,
    )
    created = await repo.create(user)
    assert created.id is not None
    assert created.email == "trader@sentinews.in"
    assert created.full_name == "Rajesh Sharma"

    # 2. Read by ID and by Email
    by_id = await repo.get_by_id(created.id)
    assert by_id is not None
    assert by_id.email == "trader@sentinews.in"

    by_email = await repo.get_by_email("trader@sentinews.in")
    assert by_email is not None
    assert by_email.id == created.id

    # 3. Update
    by_id.full_name = "Rajesh K. Sharma"
    updated = await repo.update(by_id)
    assert updated.full_name == "Rajesh K. Sharma"


@pytest.mark.asyncio
async def test_portfolio_repository_crud_and_precision(async_db: AsyncSession):
    user_repo = SQLAlchemyUserRepository(session=async_db)
    user = await user_repo.create(User(email="investor@sentinews.in", hashed_password="pwd"))

    port_repo = SQLAlchemyPortfolioRepository(session=async_db)

    # 1. Create Portfolio
    portfolio = Portfolio(user_id=user.id, name="Tech Long Term", cash_balance=50000.75)
    created_port = await port_repo.save_portfolio(portfolio)
    assert created_port.id is not None
    assert created_port.cash_balance == 50000.75

    # 2. Add Holdings with high precision prices and quantities
    holding = Holding(
        symbol="TCS",
        name="Tata Consultancy Services",
        sector="Information Technology",
        quantity=125.50,
        avg_buy_price=3456.85,
    )
    added_holding = await port_repo.add_holding(created_port.id, holding)
    assert added_holding.id is not None
    assert added_holding.quantity == 125.50
    assert added_holding.avg_buy_price == 3456.85

    # 3. Add Transaction
    tx = Transaction(
        symbol="TCS",
        transaction_type=TransactionType.BUY,
        quantity=125.50,
        price=3456.85,
        notes="Initial tranche",
    )
    added_tx = await port_repo.add_transaction(created_port.id, tx)
    assert added_tx.id is not None
    assert added_tx.quantity == 125.50
    assert added_tx.price == 3456.85

    # 4. Get Holdings with dynamic weights
    weighted = await port_repo.get_holdings_with_weights(created_port.id)
    assert len(weighted) == 1
    assert weighted[0].symbol == "TCS"
    assert weighted[0].weight_pct == 1.0


@pytest.mark.asyncio
async def test_news_repository_crud_and_trending_update(async_db: AsyncSession):
    repo = SQLAlchemyNewsRepository(session=async_db)

    now = datetime.now(timezone.utc)
    article = NewsArticle(
        title="RBI keeps repo rate unchanged at 6.5%",
        summary="Monetary policy committee maintains status quo.",
        content="Full text of RBI statement...",
        url="https://sentinews.in/news/rbi-rate-decision-2026",
        source="Economic Times",
        symbols=["^NSEI", "^NSEBANK", "SBIN"],
        sectors=["Banking", "Finance"],
        sentiment_score=0.45,
        sentiment_magnitude=1.2,
        published_at=now,
    )

    # 1. Save Article
    saved = await repo.save_article(article)
    assert saved.id is not None
    assert saved.is_trending is False
    assert saved.click_count == 0

    # 2. Query Active Articles
    active = await repo.get_active_articles()
    assert len(active) == 1
    assert active[0].id == saved.id
    assert "SBIN" in active[0].symbols

    # 3. Update Click Count & Trending
    new_expiry = now + timedelta(days=3)
    await repo.update_click_count_and_trending(
        article_id=saved.id,
        click_count=52,
        is_trending=True,
        expires_at=new_expiry,
    )

    # 4. Re-query and verify persisted changes
    active_after = await repo.get_active_articles()
    assert len(active_after) == 1
    assert active_after[0].click_count == 52
    assert active_after[0].is_trending is True
    assert active_after[0].expires_at is not None
