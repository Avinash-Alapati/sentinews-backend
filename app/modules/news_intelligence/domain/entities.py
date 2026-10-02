"""
Domain entities for the News Intelligence module.

Domain entities are separate plain dataclasses, never ORM classes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class NewsArticle:
    """
    Plain domain entity representing a news article.
    """
    id: Optional[int] = None
    title: str = ""
    summary: str = ""
    content: str = ""  # Left empty per SEBI compliance (no full body scraping)
    url: str = ""
    source: str = "Unknown"
    symbols: List[str] = field(default_factory=list)
    sectors: List[str] = field(default_factory=list)
    article_tone: str = "neutral"  # 'positive', 'neutral', 'negative' (article language tone)
    market_context: str = "market_hours"  # 'pre_market', 'market_hours', 'post_market'
    content_hash: Optional[str] = None
    sentiment_score: float = 0.0  # Range -1.0 to 1.0
    sentiment_magnitude: float = 1.0  # Intensity multiplier >= 0.0
    click_count: int = 0
    is_trending: bool = False
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    embedding: Optional[List[float]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class RelatedSourceLink:
    """
    Domain entity representing another publisher's link covering the same clustered event.
    """
    article_id: Optional[int]
    title: str
    source: str
    url: str
    published_at: datetime


@dataclass
class FullCoverageCluster:
    """
    Domain entity representing clustered coverage of the same event across multiple publishers.
    """
    primary_article: NewsArticle
    related_sources: List[RelatedSourceLink] = field(default_factory=list)
    cluster_size: int = 1


@dataclass
class PersonalizedFeedItem:
    """
    Domain entity representing an article within a user's personalized news feed.
    """
    article: NewsArticle
    relevance_score: float
    matched_holdings: List[str] = field(default_factory=list)
    is_concentrated_holding_match: bool = False
    notification_triggered: bool = False


@dataclass
class PersonalizedFeed:
    """
    Domain entity representing a complete ranked personalized news feed.
    """
    portfolio_id: int
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    items: List[PersonalizedFeedItem] = field(default_factory=list)
    total_count: int = 0
