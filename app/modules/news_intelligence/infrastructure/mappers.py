"""
Mappers translating between News Intelligence SQLAlchemy ORM models and Domain Entities.
"""

from app.db.models.news import NewsArticleORM
from app.modules.news_intelligence.domain.entities import NewsArticle


def news_article_orm_to_domain(orm: NewsArticleORM) -> NewsArticle:
    """Converts a NewsArticleORM model to a NewsArticle domain entity."""
    return NewsArticle(
        id=orm.id,
        title=orm.title,
        summary=orm.summary,
        content=orm.content,
        url=orm.url,
        source=orm.source,
        symbols=list(orm.symbols or []),
        sectors=list(orm.sectors or []),
        article_tone=getattr(orm, "article_tone", "neutral") or "neutral",
        market_context=getattr(orm, "market_context", "market_hours") or "market_hours",
        content_hash=getattr(orm, "content_hash", None),
        sentiment_score=orm.sentiment_score,
        sentiment_magnitude=orm.sentiment_magnitude,
        click_count=orm.click_count,
        is_trending=orm.is_trending,
        published_at=orm.published_at,
        expires_at=orm.expires_at,
        embedding=list(orm.embedding) if orm.embedding is not None else None,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def news_article_domain_to_orm(domain: NewsArticle) -> NewsArticleORM:
    """Converts a NewsArticle domain entity to a NewsArticleORM model."""
    return NewsArticleORM(
        id=domain.id,
        title=domain.title,
        summary=domain.summary,
        content=domain.content,
        url=domain.url,
        source=domain.source,
        symbols=domain.symbols,
        sectors=domain.sectors,
        article_tone=domain.article_tone,
        market_context=domain.market_context,
        content_hash=domain.content_hash,
        sentiment_score=domain.sentiment_score,
        sentiment_magnitude=domain.sentiment_magnitude,
        click_count=domain.click_count,
        is_trending=domain.is_trending,
        published_at=domain.published_at,
        expires_at=domain.expires_at,
        embedding=domain.embedding,
    )
