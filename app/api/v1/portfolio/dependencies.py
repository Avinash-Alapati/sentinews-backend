"""
FastAPI Dependency Injection wiring for Portfolio and News Intelligence endpoints.
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.news_intelligence.application.use_cases.get_personalized_feed import (
    GetPersonalizedFeedUseCase,
)
from app.modules.news_intelligence.infrastructure.adapters.celery_dispatcher import (
    CeleryNotificationDispatcher,
)
from app.modules.news_intelligence.infrastructure.adapters.portfolio_holdings_reader import (
    PortfolioHoldingsReaderAdapter,
)
from app.modules.news_intelligence.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from app.modules.news_intelligence.application.ports import NewsRepository
from app.modules.news_intelligence.domain.services.live_news_service import (
    LiveNewsService,
    live_news_service,
)
from app.modules.portfolio.application.use_cases.create_portfolio import CreatePortfolioUseCase
from app.modules.portfolio.application.use_cases.get_portfolio_allocation import (
    GetPortfolioAllocationUseCase,
)
from app.modules.portfolio.application.use_cases.get_portfolio_overview import (
    GetPortfolioOverviewUseCase,
)
from app.modules.portfolio.application.use_cases.record_transaction import (
    RecordTransactionUseCase,
)
from app.modules.portfolio.infrastructure.adapters import NewsIntelligenceRelevanceProvider
from app.modules.portfolio.infrastructure.market_data_provider import LiveMarketDataProvider
from app.modules.portfolio.infrastructure.repositories.portfolio_repository import (
    SQLAlchemyPortfolioRepository,
)

# Singleton providers
_embedding_provider = None
_market_provider = None


def get_embedding_provider() -> SentenceTransformerEmbeddingProvider:
    global _embedding_provider
    if _embedding_provider is None:
        _embedding_provider = SentenceTransformerEmbeddingProvider()
    return _embedding_provider


def get_market_data_provider() -> LiveMarketDataProvider:
    global _market_provider
    if _market_provider is None:
        _market_provider = LiveMarketDataProvider()
    return _market_provider


def get_notification_dispatcher() -> CeleryNotificationDispatcher:
    return CeleryNotificationDispatcher()


def get_portfolio_repository(
    db: AsyncSession = Depends(get_db),
) -> SQLAlchemyPortfolioRepository:
    return SQLAlchemyPortfolioRepository(session=db)


def get_news_repository() -> NewsRepository:
    """Returns the production live RSS news repository."""
    return live_news_service


def get_relevance_adapter(
    embedding_provider: SentenceTransformerEmbeddingProvider = Depends(get_embedding_provider),
    news_repo: NewsRepository = Depends(get_news_repository),
) -> NewsIntelligenceRelevanceProvider:
    return NewsIntelligenceRelevanceProvider(
        embedding_provider=embedding_provider,
        news_repository=news_repo,
    )


def get_create_portfolio_use_case(
    portfolio_repo: SQLAlchemyPortfolioRepository = Depends(get_portfolio_repository),
) -> CreatePortfolioUseCase:
    return CreatePortfolioUseCase(portfolio_repo=portfolio_repo)


def get_record_transaction_use_case(
    portfolio_repo: SQLAlchemyPortfolioRepository = Depends(get_portfolio_repository),
    market_provider: LiveMarketDataProvider = Depends(get_market_data_provider),
    relevance_adapter: NewsIntelligenceRelevanceProvider = Depends(get_relevance_adapter),
) -> RecordTransactionUseCase:
    return RecordTransactionUseCase(
        portfolio_repo=portfolio_repo,
        market_data_provider=market_provider,
        relevance_port=relevance_adapter,
    )


def get_portfolio_overview_use_case(
    portfolio_repo: SQLAlchemyPortfolioRepository = Depends(get_portfolio_repository),
    market_provider: LiveMarketDataProvider = Depends(get_market_data_provider),
) -> GetPortfolioOverviewUseCase:
    return GetPortfolioOverviewUseCase(
        portfolio_repo=portfolio_repo,
        market_data_provider=market_provider,
    )


def get_portfolio_allocation_use_case(
    portfolio_repo: SQLAlchemyPortfolioRepository = Depends(get_portfolio_repository),
) -> GetPortfolioAllocationUseCase:
    return GetPortfolioAllocationUseCase(portfolio_repo=portfolio_repo)


def get_portfolio_holdings_reader(
    portfolio_overview_use_case: GetPortfolioOverviewUseCase = Depends(get_portfolio_overview_use_case),
    portfolio_repo: SQLAlchemyPortfolioRepository = Depends(get_portfolio_repository),
) -> PortfolioHoldingsReaderAdapter:
    return PortfolioHoldingsReaderAdapter(
        portfolio_overview_use_case=portfolio_overview_use_case,
        portfolio_repository=portfolio_repo,
    )


from app.modules.portfolio.application.use_cases.get_portfolio_performance import (
    GetPortfolioPerformanceUseCase,
)


def get_portfolio_performance_use_case(
    portfolio_repo: SQLAlchemyPortfolioRepository = Depends(get_portfolio_repository),
    market_provider: LiveMarketDataProvider = Depends(get_market_data_provider),
) -> GetPortfolioPerformanceUseCase:
    return GetPortfolioPerformanceUseCase(
        portfolio_repo=portfolio_repo,
        market_data_provider=market_provider,
    )


def get_personalized_feed_use_case(
    portfolio_holdings_reader: PortfolioHoldingsReaderAdapter = Depends(get_portfolio_holdings_reader),
    news_repo: NewsRepository = Depends(get_news_repository),
    embedding_provider: SentenceTransformerEmbeddingProvider = Depends(get_embedding_provider),
    notification_dispatcher: CeleryNotificationDispatcher = Depends(get_notification_dispatcher),
) -> GetPersonalizedFeedUseCase:
    return GetPersonalizedFeedUseCase(
        portfolio_holdings_reader=portfolio_holdings_reader,
        news_repository=news_repo,
        embedding_provider=embedding_provider,
        notification_dispatcher=notification_dispatcher,
        notification_threshold=0.70,
        concentration_threshold=0.15,
    )
