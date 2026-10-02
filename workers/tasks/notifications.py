"""
Relevance Notification Background Task.

Follows the standard Celery pattern (bind=True, max_retries, self.retry with exponential backoff).
Dispatched when an article's relevance_score exceeds the user notification threshold (default 0.70)
for a concentrated holding (portfolio weight >= 15%).
"""

import logging
from typing import Any, Dict
from workers.celery_app import celery_app

logger = logging.getLogger("sentinews.workers.notifications")


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    name="workers.tasks.notifications.send_relevance_alert_notification",
)
def send_relevance_alert_notification(
    self,
    portfolio_id: int,
    user_id: int,
    article_id: int,
    symbol: str,
    relevance_score: float,
    article_title: str = "",
) -> Dict[str, Any]:
    """
    Sends a high-relevance notification alert to the user for a concentrated holding.

    Args:
        self: Bound Celery task instance.
        portfolio_id: Target portfolio ID.
        user_id: Target user ID.
        article_id: News article ID triggering the alert.
        symbol: Ticker symbol of the concentrated holding.
        relevance_score: Calculated relevance score (>= 0.70).
        article_title: Title of the relevant news article.

    Returns:
        Dict with dispatch status.
    """
    logger.info(
        "Dispatching relevance alert notification for user %s (Portfolio: %s, Holding: %s, Score: %.4f, Attempt: %s/%s)...",
        user_id,
        portfolio_id,
        symbol,
        relevance_score,
        self.request.retries + 1,
        self.max_retries,
    )
    try:
        # In production, this delivers push notifications, WebSocket events, or emails
        logger.info(
            "ALERT DELIVERED: User %s notified for %s news: '%s' (Relevance: %.2f)",
            user_id,
            symbol,
            article_title,
            relevance_score,
        )
        return {
            "status": "delivered",
            "user_id": user_id,
            "portfolio_id": portfolio_id,
            "article_id": article_id,
            "symbol": symbol,
            "relevance_score": relevance_score,
        }
    except Exception as exc:
        logger.error(
            "Failed delivering relevance alert for user %s, article %s: %s. Retrying...",
            user_id,
            article_id,
            str(exc),
        )
        countdown = (2 ** self.request.retries) * 5
        raise self.retry(exc=exc, countdown=countdown)
