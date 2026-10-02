"""
Unit tests for Celery Workers, Task Idempotency, Retry Policies, and Resilience.
"""

from unittest.mock import MagicMock, patch
import pytest
from celery.exceptions import Retry

from workers.celery_app import celery_app
from workers.tasks.market_tasks import (
    refresh_market_indices_task,
    warm_market_overview_cache_task,
)
from workers.tasks.news_tasks import (
    cleanup_expired_articles_task,
    ingest_rss_news_task,
)
from workers.tasks.notifications import send_relevance_alert_notification


def test_celery_configuration_security_and_resilience():
    """Verify Celery is configured securely with strict JSON serialization, UTC, and socket timeouts."""
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.enable_utc is True
    assert celery_app.conf.task_time_limit == 300
    assert celery_app.conf.broker_connection_retry_on_startup is True

    # Check beat schedule tasks are defined
    beat_schedule = celery_app.conf.beat_schedule
    assert "periodic-rss-news-ingestion" in beat_schedule
    assert "daily-expired-articles-cleanup" in beat_schedule
    assert "proactive-market-overview-cache-warming" in beat_schedule


def test_send_relevance_alert_notification_success():
    """Verify notification task executes and returns expected status."""
    res = send_relevance_alert_notification(
        portfolio_id=1,
        user_id=10,
        article_id=100,
        symbol="RELIANCE",
        relevance_score=0.88,
        article_title="Reliance Q3 profit rises 10%",
    )
    assert res["status"] == "delivered"
    assert res["user_id"] == 10
    assert res["symbol"] == "RELIANCE"
    assert res["relevance_score"] == 0.88


def test_notification_task_retry_on_transient_failure():
    """Verify notification task invokes self.retry with exponential backoff on failure."""
    task = send_relevance_alert_notification

    with patch.object(task, "retry", side_effect=Retry("Simulated task retry")) as mock_retry:
        task.request.retries = 1
        
        def mock_logger_info(msg, *args):
            if "ALERT DELIVERED" in msg:
                raise Exception("Delivery broker dropped")

        with patch("workers.tasks.notifications.logger.info", side_effect=mock_logger_info):
            with pytest.raises(Retry):
                task(
                    portfolio_id=1,
                    user_id=10,
                    article_id=100,
                    symbol="TCS",
                    relevance_score=0.75,
                    article_title="TCS results",
                )
            mock_retry.assert_called_once()
            call_kwargs = mock_retry.call_args[1]
            assert call_kwargs["countdown"] == 10


def test_news_ingestion_task_retry_on_error():
    """Verify news ingestion task retries on database / network failure."""
    task = ingest_rss_news_task

    with patch.object(task, "retry", side_effect=Retry("Simulated retry")) as mock_retry:
        task.request.retries = 0
        with patch("workers.tasks.news_tasks.asyncio.run", side_effect=ConnectionError("Database offline")):
            with pytest.raises(Retry):
                task()
            mock_retry.assert_called_once()
            assert mock_retry.call_args[1]["countdown"] == 15  # 2^0 * 15 = 15s


def test_market_overview_warm_task_retry():
    """Verify market overview warm task retries on fetcher exception."""
    task = warm_market_overview_cache_task

    with patch.object(task, "retry", side_effect=Retry("Market fetcher retry")) as mock_retry:
        task.request.retries = 0
        with patch("workers.tasks.market_tasks._run_coroutine_sync", side_effect=RuntimeError("Redis timeout")):
            with pytest.raises(Retry):
                task(filter_name="all", limit=20)
            mock_retry.assert_called_once()
