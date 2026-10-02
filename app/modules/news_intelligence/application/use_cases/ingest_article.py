"""
Ingest News Article Use Case.
"""

from datetime import datetime, timezone
from typing import List, Optional
from app.modules.news_intelligence.application.ports import EmbeddingProvider, NewsRepository
from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.domain.services.retention import calculate_initial_expiry


class IngestArticleUseCase:
    def __init__(
        self,
        news_repo: NewsRepository,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ):
        self.news_repo = news_repo
        self.embedding_provider = embedding_provider

    async def execute(
        self,
        title: str,
        summary: str,
        content: str,
        url: str,
        source: str = "Unknown",
        symbols: Optional[List[str]] = None,
        sectors: Optional[List[str]] = None,
        sentiment_score: float = 0.0,
        sentiment_magnitude: float = 1.0,
        published_at: Optional[datetime] = None,
    ) -> NewsArticle:
        now = datetime.now(timezone.utc)
        pub_date = published_at or now
        initial_expiry = calculate_initial_expiry(pub_date)

        # Generate embedding locally if provider present
        embedding: Optional[List[float]] = None
        if self.embedding_provider:
            text_to_embed = f"{title}. {summary}"
            embedding = self.embedding_provider.embed_text(text_to_embed)

        article = NewsArticle(
            title=title,
            summary=summary,
            content=content,
            url=url,
            source=source,
            symbols=symbols or [],
            sectors=sectors or [],
            sentiment_score=sentiment_score,
            sentiment_magnitude=sentiment_magnitude,
            click_count=0,
            is_trending=False,
            published_at=pub_date,
            expires_at=initial_expiry,
            embedding=embedding,
        )

        return await self.news_repo.save_article(article)
