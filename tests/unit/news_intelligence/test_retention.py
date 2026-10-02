"""
Unit tests for News Retention and Trending policy domain service (no DB, no network).
"""

from datetime import datetime, timedelta, timezone
import pytest

from app.modules.news_intelligence.domain.services.retention import (
    calculate_initial_expiry,
    calculate_trending_expiry,
    evaluate_click_threshold,
    is_article_expired,
)


def test_calculate_initial_expiry_boundary():
    """Initial expiry must be exactly 24 hours after ingestion timestamp."""
    ingested_at = datetime(2026, 9, 14, 10, 0, 0, tzinfo=timezone.utc)
    expiry = calculate_initial_expiry(ingested_at)
    assert expiry == ingested_at + timedelta(hours=24)
    assert (expiry - ingested_at).total_seconds() == 24 * 3600


def test_calculate_trending_expiry_boundary():
    """Trending expiry must be exactly 72 hours after ingestion timestamp."""
    ingested_at = datetime(2026, 9, 14, 10, 0, 0, tzinfo=timezone.utc)
    expiry = calculate_trending_expiry(ingested_at)
    assert expiry == ingested_at + timedelta(hours=72)
    assert (expiry - ingested_at).total_seconds() == 72 * 3600


def test_evaluate_click_threshold_transition_at_50():
    """
    Article transitions NORMAL -> TRENDING once when crossing threshold 50.
    threshold_just_crossed is True ONLY at the crossing instant.
    """
    threshold = 50

    # 1. Below threshold (49 clicks)
    is_trending, just_crossed = evaluate_click_threshold(
        current_clicks=49,
        is_currently_trending=False,
        threshold=threshold,
    )
    assert is_trending is False
    assert just_crossed is False

    # 2. Reaching threshold (50 clicks) -> Transitions and triggers DB update flag
    is_trending, just_crossed = evaluate_click_threshold(
        current_clicks=50,
        is_currently_trending=False,
        threshold=threshold,
    )
    assert is_trending is True
    assert just_crossed is True

    # 3. Subsequent clicks (51 clicks) -> Remains trending, but does NOT trigger another update
    is_trending, just_crossed = evaluate_click_threshold(
        current_clicks=51,
        is_currently_trending=True,
        threshold=threshold,
    )
    assert is_trending is True
    assert just_crossed is False

    # 4. Never reverts even if check is called with lower count
    is_trending, just_crossed = evaluate_click_threshold(
        current_clicks=10,
        is_currently_trending=True,
        threshold=threshold,
    )
    assert is_trending is True
    assert just_crossed is False


def test_is_article_expired():
    """Validates expiration reference checks."""
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    future_expiry = now + timedelta(hours=1)
    past_expiry = now - timedelta(hours=1)

    assert is_article_expired(future_expiry, now) is False
    assert is_article_expired(past_expiry, now) is True
    assert is_article_expired(now, now) is True
