"""
Pure domain service for computing hyper-personalized news relevance scores.

Compliance framing:
This service scores news articles that mention holdings owned in a user's portfolio.
It is an attention-ranking function and does NOT assess market impact, predict price
movement, or provide investment advice.

This module contains zero database or framework dependencies to ensure
complete unit testability and mathematical purity.
"""

from datetime import datetime, timezone
import math
from typing import Any, Optional, Union
from app.infrastructure.observability.decorators import track_compute
from app.modules.news_intelligence.domain.enums import ArticleTone, RetentionTier

# Default domain constants
DEFAULT_HALF_LIFE_HOURS = 12.0
DEFAULT_TRENDING_BOOST = 1.15
DEFAULT_CONCENTRATION_THRESHOLD = 0.15  # 15% holding weight
DEFAULT_NOTIFICATION_THRESHOLD = 0.70   # Immediate alert cutoff
DEFAULT_STANDARD_CUTOFF = 0.30          # Minimum relevance for standard holdings
DEFAULT_CONCENTRATED_CUTOFF = 0.15      # Minimum relevance for concentrated holdings


@track_compute("news_recency_decay")
def calculate_recency_decay(
    published_at: datetime,
    now: datetime,
    half_life_hours: float = DEFAULT_HALF_LIFE_HOURS,
) -> float:
    """
    Computes exponential recency decay based on a configurable half-life.

    Formula:
        decay = 2 ** (-delta_t_hours / half_life_hours)
              = exp(-ln(2) * delta_t_hours / half_life_hours)

    Args:
        published_at: Publication timestamp of the article.
        now: Current reference timestamp.
        half_life_hours: Duration in hours after which score decays by 50% (default 12h).

    Returns:
        float: Decay factor in the range (0.0, 1.0].
    """
    if half_life_hours <= 0:
        raise ValueError("half_life_hours must be strictly positive")

    # Normalize timezone awareness if needed
    if published_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    elif published_at.tzinfo is None and now.tzinfo is not None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    delta_seconds = (now - published_at).total_seconds()
    delta_hours = max(0.0, delta_seconds / 3600.0)

    # Exponential decay: 2^(-t / half_life)
    decay = math.pow(2.0, -delta_hours / half_life_hours)
    return max(0.0, min(1.0, decay))


def derive_sentiment_magnitude(
    article_tone: Optional[Union[str, ArticleTone]] = None,
    explicit_magnitude: Optional[float] = None,
    neutral_magnitude: float = 0.8,
    polarized_magnitude: float = 1.2,
) -> float:
    """
    Derives linguistic sentiment magnitude from article tone.

    Compliance Notice:
    Reflects the linguistic strength/expressiveness of the author's writing,
    NOT market direction or price signal. Neutral tone is less attention-grabbing,
    while strongly-worded positive or negative tone is more attention-worthy.

    Args:
        article_tone: Linguistic tone ('positive', 'neutral', 'negative' or ArticleTone).
        explicit_magnitude: Optional explicit magnitude override >= 0.0.
        neutral_magnitude: Multiplier for neutral tone (default 0.8).
        polarized_magnitude: Multiplier for positive/negative tone (default 1.2).

    Returns:
        float: Sentiment magnitude multiplier >= 0.0.
    """
    if explicit_magnitude is not None:
        return max(0.0, float(explicit_magnitude))

    if article_tone is None:
        return 1.0

    tone_str = article_tone.value.lower() if isinstance(article_tone, ArticleTone) else str(article_tone).lower().strip()

    if tone_str in ("positive", "negative"):
        return polarized_magnitude
    elif tone_str == "neutral":
        return neutral_magnitude

    return 1.0


def derive_trending_boost(
    retention_tier: Optional[Union[str, RetentionTier]] = None,
    is_trending: bool = False,
    trending_boost_factor: float = DEFAULT_TRENDING_BOOST,
) -> float:
    """
    Derives trending multiplier based on retention tier or trending flag.

    Args:
        retention_tier: RetentionTier enum or string ('TRENDING', 'STANDARD', etc.).
        is_trending: Boolean indicating whether article is trending.
        trending_boost_factor: Multiplier applied when trending (default 1.15).

    Returns:
        float: Trending boost multiplier (>= 1.0).
    """
    if is_trending:
        return float(trending_boost_factor)

    if retention_tier is not None:
        tier_str = retention_tier.value.upper() if isinstance(retention_tier, RetentionTier) else str(retention_tier).upper().strip()
        if tier_str == RetentionTier.TRENDING.value:
            return float(trending_boost_factor)

    return 1.0


@track_compute("news_relevance_score")
def compute_relevance_score(
    semantic_similarity: float,
    portfolio_weight_pct: float,
    published_at: datetime,
    now: datetime,
    article_tone: Optional[Union[str, ArticleTone]] = None,
    sentiment_magnitude: Optional[float] = None,
    retention_tier: Optional[Union[str, RetentionTier]] = None,
    is_trending: bool = False,
    half_life_hours: float = DEFAULT_HALF_LIFE_HOURS,
    trending_boost_factor: float = DEFAULT_TRENDING_BOOST,
) -> float:
    """
    Pure unit-testable scoring function for news that mentions holdings you own.

    Formula:
        relevance_score = semantic_similarity
                        × portfolio_weight_pct
                        × recency_decay(published_at, now)
                        × sentiment_magnitude(article_tone)
                        × trending_boost(retention_tier)

    Args:
        semantic_similarity: Upstream pgvector / embedding cosine similarity [0.0, 1.0].
        portfolio_weight_pct: Holding's weight in the portfolio (accepts 0.0-1.0 or 0-100%).
        published_at: Article publication timestamp.
        now: Current reference timestamp.
        article_tone: Linguistic tone of the article (positive/neutral/negative).
        sentiment_magnitude: Optional explicit sentiment magnitude multiplier.
        retention_tier: Article retention tier enum or string.
        is_trending: Boolean flag for trending status.
        half_life_hours: Half-life in hours for recency decay (default 12.0).
        trending_boost_factor: Multiplier applied when article is trending (default 1.15).

    Returns:
        float: Calculated relevance score (clamped >= 0.0).
    """
    # 1. Normalize semantic similarity [0.0, 1.0]
    similarity = max(0.0, min(1.0, float(semantic_similarity)))

    # 2. Normalize portfolio weight to fraction [0.0, 1.0]
    weight = float(portfolio_weight_pct)
    if weight > 1.0:
        weight = weight / 100.0
    weight = max(0.0, min(1.0, weight))

    # 3. Compute exponential recency decay
    recency_factor = calculate_recency_decay(
        published_at=published_at,
        now=now,
        half_life_hours=half_life_hours,
    )

    # 4. Derive sentiment magnitude from article tone or explicit magnitude
    magnitude_factor = derive_sentiment_magnitude(
        article_tone=article_tone,
        explicit_magnitude=sentiment_magnitude,
    )

    # 5. Derive trending boost from retention tier or trending flag
    boost_factor = derive_trending_boost(
        retention_tier=retention_tier,
        is_trending=is_trending,
        trending_boost_factor=trending_boost_factor,
    )

    score = similarity * weight * recency_factor * magnitude_factor * boost_factor
    return max(0.0, score)


def is_feed_eligible(
    relevance_score: float,
    portfolio_weight_pct: float,
    concentration_threshold: float = DEFAULT_CONCENTRATION_THRESHOLD,
    standard_cutoff: float = DEFAULT_STANDARD_CUTOFF,
    concentrated_cutoff: float = DEFAULT_CONCENTRATED_CUTOFF,
) -> bool:
    """
    Evaluates feed inclusion using a concentration-aware floor.

    Holdings above the concentration threshold (default >= 15% portfolio weight)
    receive a lower minimum relevance_score cutoff (default 0.15) for feed inclusion,
    ensuring material portfolio exposures are not omitted due to moderate similarity.

    Args:
        relevance_score: The computed relevance score for the article.
        portfolio_weight_pct: Holding weight (fraction [0, 1] or percentage [0, 100]).
        concentration_threshold: Portfolio weight threshold for concentration (default 0.15).
        standard_cutoff: Minimum score required for regular holdings (default 0.30).
        concentrated_cutoff: Lower minimum score required for concentrated holdings (default 0.15).

    Returns:
        bool: True if the article meets the cutoff criteria, False otherwise.
    """
    weight = float(portfolio_weight_pct)
    if weight > 1.0:
        weight = weight / 100.0

    conc_thresh = float(concentration_threshold)
    if conc_thresh > 1.0:
        conc_thresh = conc_thresh / 100.0

    effective_cutoff = concentrated_cutoff if weight >= conc_thresh else standard_cutoff
    return relevance_score >= effective_cutoff


def should_trigger_notification(
    relevance_score: float,
    portfolio_weight_pct: float,
    notification_threshold: float = DEFAULT_NOTIFICATION_THRESHOLD,
    concentration_threshold: float = DEFAULT_CONCENTRATION_THRESHOLD,
) -> bool:
    """
    Determines if a high-relevance alert notification task should be enqueued.

    Triggers when relevance_score exceeds the notification threshold (default 0.70)
    for a concentrated holding (portfolio weight >= concentration threshold, default 15%).

    Args:
        relevance_score: The computed relevance score.
        portfolio_weight_pct: Holding weight (fraction or percentage).
        notification_threshold: Score threshold for alert trigger (default 0.70).
        concentration_threshold: Weight threshold for concentrated holding (default 0.15).

    Returns:
        bool: True if immediate notification Celery task should be enqueued.
    """
    weight = float(portfolio_weight_pct)
    if weight > 1.0:
        weight = weight / 100.0

    conc_thresh = float(concentration_threshold)
    if conc_thresh > 1.0:
        conc_thresh = conc_thresh / 100.0

    is_concentrated = weight >= conc_thresh
    is_high_relevance = relevance_score >= float(notification_threshold)

    return is_concentrated and is_high_relevance
