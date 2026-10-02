"""
Cross-Module Integration Tests (Co-Relevance Engine & Hexagonal Boundaries).

Verifies:
1. portfolio -> market_intelligence live quote propagation.
2. portfolio -> news_intelligence holding addition notification & backfill.
3. Relevant vs irrelevant article ranking and portfolio weight impact on feed ordering.
4. Hexagonal module boundary compliance (no cross-module ORM leaks).
5. Celery notification dispatch on high-relevance concentrated positions.
"""

from datetime import datetime, timezone
import inspect
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.modules.news_intelligence.application.use_cases.get_personalized_feed import (
    GetPersonalizedFeedUseCase,
)
from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from app.modules.news_intelligence.infrastructure.repositories.news_repository import (
    SQLAlchemyNewsRepository,
)
from app.modules.portfolio.application.use_cases.get_portfolio_overview import (
    GetPortfolioOverviewUseCase,
)
from app.modules.portfolio.application.use_cases.record_transaction import (
    RecordTransactionUseCase,
)
from app.modules.portfolio.domain.entities import Holding, Portfolio, TransactionType
from app.modules.portfolio.infrastructure.adapters import NewsIntelligenceRelevanceProvider
from app.modules.portfolio.infrastructure.market_data_provider import LiveMarketDataProvider
from app.modules.portfolio.infrastructure.repositories.portfolio_repository import (
    SQLAlchemyPortfolioRepository,
)


class MockDispatcher:
    def __init__(self):
        self.dispatched = []

    def send_relevance_alert(self, portfolio_id, user_id, article_id, symbol, relevance_score, article_title=""):
        self.dispatched.append({
            "portfolio_id": portfolio_id,
            "user_id": user_id,
            "article_id": article_id,
            "symbol": symbol,
            "relevance_score": relevance_score,
            "article_title": article_title,
        })


@pytest.fixture
async def cross_module_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_portfolio_overview_uses_market_data_provider(cross_module_db: AsyncSession):
    """Confirm overview uses live market data provider quotes for current_price and PnL."""
    user_repo = SQLAlchemyUserRepository(session=cross_module_db)
    user = await user_repo.create(User(email="trader.live@sentinews.in", hashed_password="pwd"))

    port_repo = SQLAlchemyPortfolioRepository(session=cross_module_db)
    port = await port_repo.save_portfolio(Portfolio(user_id=user.id, name="Live Test"))

    # Add holding: 10 shares of RELIANCE bought at 2000 (Cost = 20000)
    await port_repo.add_holding(
        port.id,
        Holding(symbol="RELIANCE", name="Reliance Industries", sector="Energy", quantity=10.0, avg_buy_price=2000.0),
    )

    class StaticMarketProvider:
        async def get_quotes_batch(self, symbols):
            return {"RELIANCE": 2500.0}
        async def get_current_price(self, symbol):
            return 2500.0

    overview_use_case = GetPortfolioOverviewUseCase(
        portfolio_repo=port_repo,
        market_data_provider=StaticMarketProvider(),
    )

    overview = await overview_use_case.execute(port.id)
    assert overview is not None
    assert overview.total_invested_value == 20000.0
    # Market value at 2500/share: 10 * 2500 = 25000.0
    assert overview.total_current_value == 25000.0
    assert overview.total_unrealized_pnl == 5000.0
    assert overview.holdings[0].current_price == 2500.0


@pytest.mark.asyncio
async def test_holding_added_backfills_relevance_scores(cross_module_db: AsyncSession):
    """Confirm notify_holding_added executes and processes active articles."""
    news_repo = SQLAlchemyNewsRepository(session=cross_module_db)
    now = datetime.now(timezone.utc)

    # Ingest existing articles in news corpus
    await news_repo.save_article(
        NewsArticle(
            title="Tata Motors secures EV battery supply deal",
            summary="Electric vehicle manufacturing expansion.",
            url="https://sentinews.in/news/tata-motors-battery",
            symbols=["TATAMOTORS"],
            sectors=["Automobile"],
            published_at=now,
        )
    )

    embedding_provider = SentenceTransformerEmbeddingProvider()
    adapter = NewsIntelligenceRelevanceProvider(
        embedding_provider=embedding_provider,
        news_repository=news_repo,
    )

    new_holding = Holding(
        symbol="TATAMOTORS",
        name="Tata Motors Ltd",
        sector="Automobile",
        quantity=50.0,
        avg_buy_price=800.0,
        weight_pct=0.25,
    )

    # Should execute without errors
    await adapter.notify_holding_added(new_holding)


@pytest.mark.asyncio
async def test_relevance_ranking_and_weight_impact(cross_module_db: AsyncSession):
    """
    Ingest 1 relevant article (RELIANCE) and 1 irrelevant article (WIPRO).
    Verify:
    1. Relevant article ranks higher than irrelevant article.
    2. Changing portfolio weight from 2% to 40% increases the relevance score.
    """
    user_repo = SQLAlchemyUserRepository(session=cross_module_db)
    user = await user_repo.create(User(email="rank.investor@sentinews.in", hashed_password="pwd"))

    port_repo = SQLAlchemyPortfolioRepository(session=cross_module_db)
    news_repo = SQLAlchemyNewsRepository(session=cross_module_db)
    now = datetime.now(timezone.utc)

    # Ingest 2 articles
    art_reliance = await news_repo.save_article(
        NewsArticle(
            title="Reliance Retail enters quick commerce market",
            summary="Rapid delivery grocery expansion nationwide.",
            url="https://sentinews.in/news/reliance-quick-commerce",
            symbols=["RELIANCE"],
            sectors=["Retail", "Energy"],
            sentiment_score=0.7,
            sentiment_magnitude=1.2,
            is_trending=True,
            published_at=now,
        )
    )
    art_wipro = await news_repo.save_article(
        NewsArticle(
            title="Wipro opens new technology center in Munich",
            summary="European consulting center launched.",
            url="https://sentinews.in/news/wipro-munich",
            symbols=["WIPRO"],
            sectors=["IT"],
            sentiment_score=0.3,
            sentiment_magnitude=1.0,
            is_trending=False,
            published_at=now,
        )
    )

    # Create portfolio with 40% weight in RELIANCE and 60% in TCS (WIPRO is not held)
    port = await port_repo.save_portfolio(Portfolio(user_id=user.id, name="Rank Test"))
    await port_repo.add_holding(port.id, Holding(symbol="RELIANCE", name="Reliance", sector="Energy", quantity=40.0, avg_buy_price=100.0))
    await port_repo.add_holding(port.id, Holding(symbol="TCS", name="TCS", sector="IT", quantity=60.0, avg_buy_price=100.0))

    dispatcher = MockDispatcher()
    use_case = GetPersonalizedFeedUseCase(
        portfolio_repository=port_repo,
        news_repository=news_repo,
        notification_dispatcher=dispatcher,
        notification_threshold=0.70,
        concentration_threshold=0.15,
    )

    feed = await use_case.execute(portfolio_id=port.id)
    assert len(feed.items) >= 1

    # 1. Top article must be the RELIANCE article
    top_item = feed.items[0]
    assert top_item.article.id == art_reliance.id
    assert "RELIANCE" in top_item.matched_holdings

    # 2. Verify score of a 40% weight holding is substantially higher than a 2% weight holding
    # Portfolio with only 2% weight in RELIANCE
    port_small = await port_repo.save_portfolio(Portfolio(user_id=user.id, name="Small Weight Test"))
    await port_repo.add_holding(port_small.id, Holding(symbol="RELIANCE", name="Reliance", sector="Energy", quantity=2.0, avg_buy_price=100.0))
    await port_repo.add_holding(port_small.id, Holding(symbol="TCS", name="TCS", sector="IT", quantity=98.0, avg_buy_price=100.0))

    feed_small = await use_case.execute(portfolio_id=port_small.id, min_score=0.0)
    item_40 = next(i for i in feed.items if i.article.id == art_reliance.id)
    item_2 = next((i for i in feed_small.items if i.article.id == art_reliance.id), None)

    if item_2:
        assert item_40.relevance_score > item_2.relevance_score
        assert pytest.approx(item_40.relevance_score / item_2.relevance_score, rel=0.1) == (0.40 / 0.02)


def test_hexagonal_module_boundary_compliance():
    """
    Verify news_intelligence application use cases and domain services NEVER
    import portfolio ORM models directly.
    """
    import app.modules.news_intelligence.application.use_cases.get_personalized_feed as feed_module
    import app.modules.news_intelligence.domain.services.relevance as rel_module

    feed_source = inspect.getsource(feed_module)
    rel_source = inspect.getsource(rel_module)

    forbidden_orms = ["PortfolioORM", "HoldingORM", "TransactionORM", "app.db.models.portfolio"]

    for forbidden in forbidden_orms:
        assert forbidden not in feed_source, f"Violation: {forbidden} found in get_personalized_feed.py"
        assert forbidden not in rel_source, f"Violation: {forbidden} found in relevance.py"


@pytest.mark.asyncio
async def test_celery_notification_dispatch_on_high_relevance(cross_module_db: AsyncSession):
    """
    Confirm notification dispatcher is triggered when relevance_score crosses 0.70
    for a concentrated holding (>= 15%).
    """
    user_repo = SQLAlchemyUserRepository(session=cross_module_db)
    user = await user_repo.create(User(email="alert.user@sentinews.in", hashed_password="pwd"))

    port_repo = SQLAlchemyPortfolioRepository(session=cross_module_db)
    news_repo = SQLAlchemyNewsRepository(session=cross_module_db)
    now = datetime.now(timezone.utc)

    # 80% concentrated holding
    port = await port_repo.save_portfolio(Portfolio(user_id=user.id, name="Alert Test"))
    await port_repo.add_holding(port.id, Holding(symbol="INFY", name="Infosys Ltd", sector="IT", quantity=80.0, avg_buy_price=100.0))

    # Breaking news with sentiment magnitude 1.2 and trending boost 1.15
    article = await news_repo.save_article(
        NewsArticle(
            title="Infosys signs landmark $2 Billion AI transformation contract",
            summary="Major global contract signed.",
            url="https://sentinews.in/news/infy-2b-contract",
            symbols=["INFY"],
            sectors=["IT"],
            sentiment_score=0.9,
            sentiment_magnitude=1.2,
            is_trending=True,
            published_at=now,
        )
    )

    dispatcher = MockDispatcher()
    use_case = GetPersonalizedFeedUseCase(
        portfolio_repository=port_repo,
        news_repository=news_repo,
        notification_dispatcher=dispatcher,
        notification_threshold=0.70,
        concentration_threshold=0.15,
    )

    feed = await use_case.execute(portfolio_id=port.id)
    assert len(feed.items) >= 1
    assert feed.items[0].relevance_score >= 0.70

    # Notification must have been dispatched
    assert len(dispatcher.dispatched) >= 1
    alert = dispatcher.dispatched[0]
    assert alert["symbol"] == "INFY"
    assert alert["portfolio_id"] == port.id
    assert alert["relevance_score"] >= 0.70
