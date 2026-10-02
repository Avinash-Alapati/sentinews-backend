"""
Celery Notification Dispatcher Adapter.

Implements NotificationDispatcher Protocol port using background Celery tasks.
"""

import logging
from app.modules.news_intelligence.application.ports import NotificationDispatcher
from workers.tasks.notifications import send_relevance_alert_notification

logger = logging.getLogger("sentinews.news_intelligence.celery_dispatcher")


class CeleryNotificationDispatcher(NotificationDispatcher):
    """
    Asynchronous notification dispatcher enqueuing Celery tasks.
    """

    def send_relevance_alert(
        self,
        portfolio_id: int,
        user_id: int,
        article_id: int,
        symbol: str,
        relevance_score: float,
        article_title: str = "",
    ) -> None:
        """
        Enqueues high-relevance alert task onto the Celery work queue.
        """
        try:
            send_relevance_alert_notification.apply_async(
                kwargs={
                    "portfolio_id": portfolio_id,
                    "user_id": user_id,
                    "article_id": article_id,
                    "symbol": symbol,
                    "relevance_score": relevance_score,
                    "article_title": article_title,
                },
                retry=False,
                ignore_result=True,
            )
            logger.info(
                "Enqueued Celery alert notification for %s (Score: %.4f)",
                symbol,
                relevance_score,
            )
        except Exception as e:
            logger.warning("Failed enqueuing Celery notification task: %s", str(e))
