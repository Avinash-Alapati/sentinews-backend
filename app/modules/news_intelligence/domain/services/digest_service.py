"""
Domain service for Digest Batching and Notification routing.

Articles below the high-relevance / concentration threshold are batched into
a daily digest queue rather than triggering an immediate alert notification.
"""

from typing import Tuple
from app.modules.news_intelligence.domain.services.relevance import (
    should_trigger_notification,
)

ACTION_IMMEDIATE_ALERT = "IMMEDIATE_ALERT"
ACTION_DAILY_DIGEST = "DAILY_DIGEST"
ACTION_IGNORE = "IGNORE"

DEFAULT_DIGEST_MIN_SCORE = 0.20


def route_article_notification(
    relevance_score: float,
    portfolio_weight_pct: float,
    notification_threshold: float = 0.70,
    concentration_threshold: float = 0.15,
    digest_min_threshold: float = DEFAULT_DIGEST_MIN_SCORE,
) -> Tuple[str, str]:
    """
    Determines whether an article triggers an immediate alert or is queued for daily digest.

    Args:
        relevance_score: Calculated relevance score.
        portfolio_weight_pct: Weight of the holding in portfolio.
        notification_threshold: Threshold for immediate notification (default 0.70).
        concentration_threshold: Concentration weight threshold (default 0.15).
        digest_min_threshold: Minimum score for daily digest inclusion (default 0.20).

    Returns:
        Tuple[str, str]: (action, reason)
            - action: 'IMMEDIATE_ALERT', 'DAILY_DIGEST', or 'IGNORE'
            - reason: Human-readable rationale description
    """
    if should_trigger_notification(
        relevance_score=relevance_score,
        portfolio_weight_pct=portfolio_weight_pct,
        notification_threshold=notification_threshold,
        concentration_threshold=concentration_threshold,
    ):
        return ACTION_IMMEDIATE_ALERT, "Concentrated holding with high relevance score."

    if relevance_score >= digest_min_threshold:
        return ACTION_DAILY_DIGEST, "Moderate relevance item queued for daily digest."

    return ACTION_IGNORE, "Below relevance floor."
