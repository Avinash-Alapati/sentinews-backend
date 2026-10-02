"""
Domain service for article retention, trending threshold evaluation, and expiry policy.

Articles expire 24h after ingestion unless click_count crosses a threshold
(default 50), in which case expiry extends to 72h and the article is marked
TRENDING permanently.
"""

from datetime import datetime, timedelta, timezone
from typing import Tuple


DEFAULT_RETENTION_HOURS = 24
TRENDING_RETENTION_HOURS = 72
DEFAULT_TRENDING_CLICK_THRESHOLD = 50


def calculate_initial_expiry(ingested_at: datetime) -> datetime:
    """
    Calculates the initial expiration timestamp for a newly ingested article (24h).
    """
    return ingested_at + timedelta(hours=DEFAULT_RETENTION_HOURS)


def calculate_trending_expiry(ingested_at: datetime) -> datetime:
    """
    Calculates the extended expiration timestamp for a trending article (72h from ingestion).
    """
    return ingested_at + timedelta(hours=TRENDING_RETENTION_HOURS)


def evaluate_click_threshold(
    current_clicks: int,
    is_currently_trending: bool,
    threshold: int = DEFAULT_TRENDING_CLICK_THRESHOLD,
) -> Tuple[bool, bool]:
    """
    Evaluates whether a click count crosses the trending threshold.

    Args:
        current_clicks: The new click count (e.g. from Redis INCR).
        is_currently_trending: Whether the article is already marked trending.
        threshold: The threshold to cross (default 50).

    Returns:
        Tuple[bool, bool]: (is_trending, threshold_just_crossed)
        - is_trending: True if article meets trending criteria.
        - threshold_just_crossed: True only at the exact moment the threshold is crossed,
          indicating that a single Postgres UPDATE should be performed.
    """
    if is_currently_trending:
        return True, False

    if current_clicks >= threshold:
        return True, True

    return False, False


def is_article_expired(expires_at: datetime, now: datetime) -> bool:
    """
    Checks if an article has expired relative to the given reference time.
    """
    if expires_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    elif expires_at.tzinfo is None and now.tzinfo is not None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return now >= expires_at
