"""
SQLAlchemy Repository implementing the NewsRepository application port.
"""

from datetime import datetime, timezone
import logging
from typing import List, Optional, Tuple
import numpy as np
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.news import NewsArticleORM
from app.modules.news_intelligence.application.ports import NewsRepository
from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.infrastructure.mappers import (
    news_article_domain_to_orm,
    news_article_orm_to_domain,
)

logger = logging.getLogger("sentinews.news_intelligence.repository")


class SQLAlchemyNewsRepository(NewsRepository):
    """
    Asynchronous SQLAlchemy repository for News Article data access with vector support.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active_articles(self, limit: int = 50) -> List[NewsArticle]:
        """Fetches active, non-expired articles ordered by publication date."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(NewsArticleORM)
            .where(
                or_(
                    NewsArticleORM.expires_at.is_(None),
                    NewsArticleORM.expires_at > now,
                )
            )
            .order_by(NewsArticleORM.published_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        orms = result.scalars().all()
        return [news_article_orm_to_domain(orm) for orm in orms]

    async def get_latest_articles(
        self,
        limit: int = 50,
        offset: int = 0,
        sector: Optional[str] = None,
        symbol: Optional[str] = None,
        tone: Optional[str] = None,
    ) -> List[NewsArticle]:
        """
        Fetches non-expired articles with pagination and optional sector, symbol, tone filters.
        Strictly excludes expired articles at the query level (expires_at > now).
        """
        now = datetime.now(timezone.utc)
        conditions = [
            or_(
                NewsArticleORM.expires_at.is_(None),
                NewsArticleORM.expires_at > now,
            )
        ]

        if tone:
            conditions.append(NewsArticleORM.article_tone == tone.lower())

        stmt = (
            select(NewsArticleORM)
            .where(*conditions)
            .order_by(NewsArticleORM.published_at.desc())
            .offset(offset)
            .limit(limit * 3 if (sector or symbol) else limit)
        )
        result = await self.session.execute(stmt)
        orms = result.scalars().all()
        articles = [news_article_orm_to_domain(orm) for orm in orms]

        # In-memory filter for JSON symbols and sectors if specified
        if sector or symbol:
            filtered = []
            target_sector = sector.upper() if sector else None
            target_symbol = symbol.upper() if symbol else None

            for a in articles:
                match_sec = True if not target_sector else any(s.upper() == target_sector for s in a.sectors)
                match_sym = True if not target_symbol else any(s.upper() == target_symbol for s in a.symbols)
                if match_sec and match_sym:
                    filtered.append(a)
                if len(filtered) >= limit:
                    break
            return filtered

        return articles[:limit]

    async def get_trending_articles(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> List[NewsArticle]:
        """
        Fetches active articles marked as trending (is_trending=True).
        Strictly excludes expired articles at the query level.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(NewsArticleORM)
            .where(
                NewsArticleORM.is_trending.is_(True),
                or_(
                    NewsArticleORM.expires_at.is_(None),
                    NewsArticleORM.expires_at > now,
                ),
            )
            .order_by(NewsArticleORM.click_count.desc(), NewsArticleORM.published_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        orms = result.scalars().all()
        return [news_article_orm_to_domain(orm) for orm in orms]

    async def get_article_by_id(self, article_id: int) -> Optional[NewsArticle]:
        """Fetches an article by its primary key ID."""
        stmt = select(NewsArticleORM).where(NewsArticleORM.id == article_id)
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return news_article_orm_to_domain(orm)

    async def get_article_by_url(self, url: str) -> Optional[NewsArticle]:
        """Fetches an article by its canonical URL."""
        stmt = select(NewsArticleORM).where(NewsArticleORM.url == url)
        result = await self.session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return news_article_orm_to_domain(orm)

    async def get_existing_urls_and_hashes(
        self,
        urls: List[str],
        hashes: List[str],
    ) -> Tuple[set, set]:
        """
        Batch query to check which URLs or content hashes already exist in the database.
        """
        existing_urls = set()
        existing_hashes = set()

        if urls:
            stmt = select(NewsArticleORM.url).where(NewsArticleORM.url.in_(urls))
            result = await self.session.execute(stmt)
            existing_urls = set(result.scalars().all())

        if hashes:
            stmt = select(NewsArticleORM.content_hash).where(NewsArticleORM.content_hash.in_(hashes))
            result = await self.session.execute(stmt)
            existing_hashes = set(result.scalars().all())

        return existing_urls, existing_hashes

    async def get_articles_by_symbols_or_sectors(
        self,
        symbols: List[str],
        sectors: List[str],
        limit: int = 50,
    ) -> List[NewsArticle]:
        """Fetches active articles matching specified symbols or sectors."""
        active_articles = await self.get_active_articles(limit=limit * 2)
        upper_symbols = set(s.upper() for s in symbols)
        upper_sectors = set(s.upper() for s in sectors)

        matched: List[NewsArticle] = []
        for article in active_articles:
            art_symbols = set(s.upper() for s in article.symbols)
            art_sectors = set(s.upper() for s in article.sectors)

            if (art_symbols & upper_symbols) or (art_sectors & upper_sectors):
                matched.append(article)
            elif not symbols and not sectors:
                matched.append(article)

            if len(matched) >= limit:
                break

        return matched

    async def search_by_vector(
        self,
        query_vector: List[float],
        limit: int = 20,
    ) -> List[Tuple[NewsArticle, float]]:
        """
        Performs cosine similarity search against article embeddings.
        Returns list of (NewsArticle, similarity_score).
        """
        active_articles = await self.get_active_articles(limit=100)
        if not query_vector:
            return [(a, 0.5) for a in active_articles[:limit]]

        q_vec = np.array(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return [(a, 0.0) for a in active_articles[:limit]]

        scored_articles: List[Tuple[NewsArticle, float]] = []
        for article in active_articles:
            if article.embedding is not None and len(article.embedding) == len(query_vector):
                art_vec = np.array(article.embedding, dtype=np.float32)
                art_norm = np.linalg.norm(art_vec)
                if art_norm > 0:
                    sim = float(np.dot(q_vec, art_vec) / (q_norm * art_norm))
                    sim = max(0.0, min(1.0, sim))
                else:
                    sim = 0.0
            else:
                sim = 0.3

            scored_articles.append((article, sim))

        scored_articles.sort(key=lambda x: x[1], reverse=True)
        return scored_articles[:limit]

    async def save_article(self, article: NewsArticle) -> NewsArticle:
        """Saves or updates a single news article."""
        orm = news_article_domain_to_orm(article)
        self.session.add(orm)
        await self.session.flush()
        await self.session.refresh(orm)
        return news_article_orm_to_domain(orm)

    async def save_articles_batch(self, articles: List[NewsArticle]) -> List[NewsArticle]:
        """Saves a batch of news articles."""
        saved: List[NewsArticle] = []
        for article in articles:
            orm = news_article_domain_to_orm(article)
            self.session.add(orm)
            await self.session.flush()
            await self.session.refresh(orm)
            saved.append(news_article_orm_to_domain(orm))
        return saved

    async def update_click_count_and_trending(
        self,
        article_id: int,
        click_count: int,
        is_trending: bool,
        expires_at: datetime,
    ) -> None:
        """Updates article click count, trending status, and expiry timestamp."""
        stmt = (
            update(NewsArticleORM)
            .where(NewsArticleORM.id == article_id)
            .values(
                click_count=click_count,
                is_trending=is_trending,
                expires_at=expires_at,
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()
