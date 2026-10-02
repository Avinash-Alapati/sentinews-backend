"""
News Ingestion and Cleanup Background Tasks.

Follows the standard Celery pattern (bind=True, max_retries, self.retry with exponential backoff).
Runs scheduled RSS feed ingestion every 10-15 minutes and periodic expired article cleanup.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict
from sqlalchemy import delete, or_

from app.db.models.news import NewsArticleORM
from app.db.session import AsyncSessionLocal
from app.integrations.news.rss.fetcher import RSSFeedFetcher
from app.modules.news_intelligence.application.use_cases.ingest_news_from_feeds import (
    IngestNewsFromFeedsUseCase,
)
from app.modules.news_intelligence.infrastructure.adapters.asset_reader import (
    DatabaseAssetReferenceReader,
)
from app.modules.news_intelligence.infrastructure.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingProvider,
)
from app.modules.news_intelligence.infrastructure.repositories.news_repository import (
    SQLAlchemyNewsRepository,
)
from workers.celery_app import celery_app

logger = logging.getLogger("sentinews.workers.news_tasks")


async def _run_async_ingestion() -> Dict[str, int]:
    """Helper coroutine executing ingestion use case with an async database session."""
    async with AsyncSessionLocal() as session:
        news_repo = SQLAlchemyNewsRepository(session=session)
        asset_reader = DatabaseAssetReferenceReader(session=session)
        rss_fetcher = RSSFeedFetcher()
        embedding_provider = SentenceTransformerEmbeddingProvider()

        use_case = IngestNewsFromFeedsUseCase(
            news_repo=news_repo,
            rss_fetcher=rss_fetcher,
            embedding_provider=embedding_provider,
            asset_reader=asset_reader,
        )

        stats = await use_case.execute()
        await session.commit()
        return stats


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="workers.tasks.news_tasks.ingest_rss_news_task",
)
def ingest_rss_news_task(self) -> Dict[str, Any]:
    """
    Scheduled periodic task fetching allowlisted financial RSS feeds,
    deduplicating entries, tagging tickers/sectors, classifying tone and market context,
    and persisting compliant article metadata.
    """
    logger.info(
        "Starting scheduled RSS news ingestion task (attempt %d/%d)...",
        self.request.retries + 1,
        self.max_retries + 1,
    )
    try:
        stats = asyncio.run(_run_async_ingestion())
        logger.info("RSS news ingestion task succeeded: %s", stats)
        return {
            "status": "success",
            "stats": stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("RSS news ingestion task failed: %s. Retrying...", exc)
        countdown = (2 ** self.request.retries) * 15
        raise self.retry(exc=exc, countdown=countdown)


async def _run_async_cleanup() -> int:
    """Helper coroutine pruning non-trending expired articles."""
    async with AsyncSessionLocal() as session:
        now = datetime.now(timezone.utc)
        stmt = delete(NewsArticleORM).where(
            NewsArticleORM.is_trending.is_(False),
            NewsArticleORM.expires_at.is_not(None),
            NewsArticleORM.expires_at <= now,
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="workers.tasks.news_tasks.cleanup_expired_articles_task",
)
def cleanup_expired_articles_task(self) -> Dict[str, Any]:
    """
    Periodic task pruning expired non-trending articles older than 24h.
    """
    logger.info("Starting expired news cleanup task...")
    try:
        pruned_count = asyncio.run(_run_async_cleanup())
        logger.info("Cleanup task finished. Pruned %d expired articles.", pruned_count)
        return {
            "status": "success",
            "pruned_count": pruned_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("Cleanup task failed: %s", exc)
        raise self.retry(exc=exc, countdown=30)
