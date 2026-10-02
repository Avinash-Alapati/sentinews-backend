"""
Record Article Click Use Case.

Implements trending policy: click counting in Redis (INCR), with a single Postgres UPDATE
only at the exact moment the threshold (default 50) is crossed, extending expiry to 72h
and marking TRENDING permanently.
"""

from datetime import datetime, timezone
import logging
from typing import Optional, Tuple
from app.modules.news_intelligence.application.ports import NewsRepository
from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.domain.services.retention import (
    calculate_trending_expiry,
    evaluate_click_threshold,
)

logger = logging.getLogger("sentinews.news_intelligence.click_service")

# In-memory Redis simulation fallback when Redis server is not running
_memory_click_counts = {}


class RecordArticleClickUseCase:
    def __init__(
        self,
        news_repo: NewsRepository,
        redis_client: Optional[object] = None,
        trending_threshold: int = 50,
    ):
        self.news_repo = news_repo
        self.redis_client = redis_client
        self.trending_threshold = trending_threshold

    async def execute(
        self,
        article_id: int,
        article: NewsArticle,
    ) -> Tuple[int, bool]:
        """
        Increments article click count and triggers trending expiry extension if threshold crossed.

        Returns:
            Tuple[int, bool]: (new_click_count, is_trending)
        """
        # 1. INCR in Redis (or in-memory cache)
        new_count = await self._increment_click_counter(article_id)

        # 2. Evaluate trending transition
        is_trending, threshold_just_crossed = evaluate_click_threshold(
            current_clicks=new_count,
            is_currently_trending=article.is_trending,
            threshold=self.trending_threshold,
        )

        # 3. Single Postgres UPDATE only when threshold is crossed
        if threshold_just_crossed:
            new_expiry = calculate_trending_expiry(article.published_at)
            await self.news_repo.update_click_count_and_trending(
                article_id=article_id,
                click_count=new_count,
                is_trending=True,
                expires_at=new_expiry,
            )
            logger.info(
                "Article %s reached trending threshold (%s clicks)! Expiry extended to %s (72h)",
                article_id,
                new_count,
                new_expiry,
            )

        return new_count, is_trending

    async def _increment_click_counter(self, article_id: int) -> int:
        key = f"news:click_count:{article_id}"
        if self.redis_client:
            try:
                # If redis_client is async
                if hasattr(self.redis_client, "incr"):
                    val = self.redis_client.incr(key)
                    if hasattr(val, "__await__"):
                        return int(await val)
                    return int(val)
            except Exception as exc:
                logger.debug("Redis INCR failed for %s (%s), using memory fallback", key, exc)

        global _memory_click_counts
        current = _memory_click_counts.get(article_id, 0) + 1
        _memory_click_counts[article_id] = current
        return current
