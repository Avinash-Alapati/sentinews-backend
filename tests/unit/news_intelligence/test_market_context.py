"""
Unit tests for Market-Hours Context Tagging.
"""

from datetime import datetime, timezone
import pytest

from app.modules.news_intelligence.domain.services.market_context import (
    get_market_context,
    PRE_MARKET,
    MARKET_HOURS,
    POST_MARKET,
)


def test_pre_market_classification():
    """Weekdays before 09:15 IST (e.g. 08:00 IST = 02:30 UTC) must be pre_market."""
    # Wednesday 2026-09-16 02:30 UTC -> 08:00 IST
    dt = datetime(2026, 9, 16, 2, 30, 0, tzinfo=timezone.utc)
    assert get_market_context(dt) == PRE_MARKET


def test_market_hours_classification():
    """Weekdays between 09:15 and 15:30 IST (e.g. 11:30 IST = 06:00 UTC) must be market_hours."""
    # Wednesday 2026-09-16 06:00 UTC -> 11:30 IST
    dt = datetime(2026, 9, 16, 6, 0, 0, tzinfo=timezone.utc)
    assert get_market_context(dt) == MARKET_HOURS


def test_market_open_boundary():
    """Exactly 09:15:00 IST (03:45 UTC) must be market_hours."""
    # Wednesday 2026-09-16 03:45:00 UTC -> 09:15:00 IST
    dt = datetime(2026, 9, 16, 3, 45, 0, tzinfo=timezone.utc)
    assert get_market_context(dt) == MARKET_HOURS


def test_market_close_boundary():
    """Exactly 15:30:00 IST (10:00 UTC) must be market_hours."""
    # Wednesday 2026-09-16 10:00:00 UTC -> 15:30:00 IST
    dt = datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc)
    assert get_market_context(dt) == MARKET_HOURS


def test_post_market_classification():
    """Weekdays after 15:30 IST (e.g. 17:00 IST = 11:30 UTC) must be post_market."""
    # Wednesday 2026-09-16 11:30 UTC -> 17:00 IST
    dt = datetime(2026, 9, 16, 11, 30, 0, tzinfo=timezone.utc)
    assert get_market_context(dt) == POST_MARKET


def test_weekend_classification():
    """Saturday and Sunday timestamps must be classified as post_market."""
    # Saturday 2026-09-19 06:00 UTC -> 11:30 IST
    saturday_dt = datetime(2026, 9, 19, 6, 0, 0, tzinfo=timezone.utc)
    assert get_market_context(saturday_dt) == POST_MARKET

    # Sunday 2026-09-20 06:00 UTC -> 11:30 IST
    sunday_dt = datetime(2026, 9, 20, 6, 0, 0, tzinfo=timezone.utc)
    assert get_market_context(sunday_dt) == POST_MARKET


def test_naive_datetime_handling():
    """Naive datetimes are assumed UTC and classified correctly."""
    dt_naive = datetime(2026, 9, 16, 6, 0, 0)
    assert get_market_context(dt_naive) == MARKET_HOURS
