"""
News Intelligence API Dependencies.
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.market_cache import market_cache
from app.modules.news_intelligence.application.ports import NewsRepository
from app.modules.news_intelligence.domain.services.live_news_service import (
    LiveNewsService,
    live_news_service,
)
from app.modules.news_intelligence.infrastructure.adapters.asset_reader import (
    DatabaseAssetReferenceReader,
)
from app.modules.news_intelligence.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)

_embedding_provider = None


def get_embedding_provider() -> SentenceTransformerEmbeddingProvider:
    global _embedding_provider
    if _embedding_provider is None:
        _embedding_provider = SentenceTransformerEmbeddingProvider()
    return _embedding_provider


def get_live_news_service() -> LiveNewsService:
    """Returns the live RSS news service and in-memory repository."""
    return live_news_service


def get_news_repository() -> NewsRepository:
    """Returns the production in-memory live RSS news repository."""
    return live_news_service


def get_asset_reference_reader(db=None) -> DatabaseAssetReferenceReader:
    return DatabaseAssetReferenceReader(session=db)


async def get_redis_client():
    """Returns the async Redis client if active, or None for memory fallback."""
    return await market_cache._get_redis()

