"""
Domain Enums for the News Intelligence module.
"""

from enum import Enum


class RetentionTier(str, Enum):
    """
    Retention tier for article lifecycle and storage duration.
    """
    STANDARD = "STANDARD"  # 24h retention
    TRENDING = "TRENDING"  # 72h extended retention for high-engagement articles
    EXPIRED = "EXPIRED"    # Soft-deleted / excluded from active queries


class ArticleTone(str, Enum):
    """
    SEBI-compliant article linguistic tone.
    Classifies the language tone used by the author, NOT a market recommendation.
    """
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class MarketContext(str, Enum):
    """
    Market session timing context when the article was published.
    """
    PRE_MARKET = "pre_market"
    MARKET_HOURS = "market_hours"
    POST_MARKET = "post_market"
