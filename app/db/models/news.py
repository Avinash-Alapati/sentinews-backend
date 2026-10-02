"""
SQLAlchemy ORM models for News Intelligence.
"""

from typing import List, Optional
from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

try:
    from pgvector.sqlalchemy import Vector
    PGVECTOR_AVAILABLE = True
except ImportError:
    Vector = None  # type: ignore
    PGVECTOR_AVAILABLE = False


class NewsArticleORM(Base):
    """
    SQLAlchemy ORM model for news articles and embeddings.
    """
    __tablename__ = "news_articles"

    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), default="Unknown", nullable=False)
    
    # Symbols and sectors stored as JSON list of strings
    symbols: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    sectors: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)

    # Compliance and categorization fields
    article_tone: Mapped[str] = mapped_column(String(20), default="neutral", nullable=False, index=True)
    market_context: Mapped[str] = mapped_column(String(20), default="market_hours", nullable=False, index=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sentiment_magnitude: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    click_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    is_trending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    published_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[Optional[DateTime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # Embedding vector: pgvector Vector(384) when supported, JSON fallback
    if PGVECTOR_AVAILABLE:
        embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(384), nullable=True)
    else:
        embedding: Mapped[Optional[List[float]]] = mapped_column(JSON, nullable=True)
