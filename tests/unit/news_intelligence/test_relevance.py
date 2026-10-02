"""
Unit tests for the pure Relevance scoring service.

Zero DB, zero framework imports, testing exact mathematical formulas,
tone magnitude, trending boost, concentration floor, and hyper-personalized differentiation.
"""

from datetime import datetime, timedelta, timezone
import math
import pytest

from app.modules.news_intelligence.domain.enums import ArticleTone, RetentionTier
from app.modules.news_intelligence.domain.services.relevance import (
    calculate_recency_decay,
    compute_relevance_score,
    derive_sentiment_magnitude,
    derive_trending_boost,
    is_feed_eligible,
    should_trigger_notification,
)


def test_recency_decay_exact_half_life():
    """At delta_t = 12h with half_life = 12h, decay must be exactly 0.5."""
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    published_12h_ago = now - timedelta(hours=12)

    decay = calculate_recency_decay(published_12h_ago, now, half_life_hours=12.0)
    assert pytest.approx(decay, rel=1e-5) == 0.5


def test_recency_decay_fresh_article():
    """At delta_t = 0h, decay must be 1.0."""
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    published_now = now

    decay = calculate_recency_decay(published_now, now, half_life_hours=12.0)
    assert pytest.approx(decay, rel=1e-5) == 1.0


def test_recency_decay_stale_article():
    """At delta_t = 48h (4 half-lives), decay must be 0.5^4 = 0.0625, approaching zero."""
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    published_48h_ago = now - timedelta(hours=48)

    decay = calculate_recency_decay(published_48h_ago, now, half_life_hours=12.0)
    assert pytest.approx(decay, rel=1e-5) == 0.0625

    # At 120h (5 days), decay is negligible
    published_120h_ago = now - timedelta(hours=120)
    decay_120h = calculate_recency_decay(published_120h_ago, now, half_life_hours=12.0)
    assert decay_120h < 0.001


def test_derive_sentiment_magnitude_neutral_vs_polarized():
    """Neutral tone must yield lower magnitude (0.8) than positive/negative (1.2)."""
    mag_neutral = derive_sentiment_magnitude(ArticleTone.NEUTRAL)
    mag_positive = derive_sentiment_magnitude(ArticleTone.POSITIVE)
    mag_negative = derive_sentiment_magnitude(ArticleTone.NEGATIVE)

    assert mag_neutral == 0.8
    assert mag_positive == 1.2
    assert mag_negative == 1.2
    assert mag_neutral < mag_positive
    assert mag_neutral < mag_negative

    # String tone inputs
    assert derive_sentiment_magnitude("neutral") == 0.8
    assert derive_sentiment_magnitude("positive") == 1.2
    assert derive_sentiment_magnitude("negative") == 1.2


def test_derive_trending_boost_retention_tier():
    """RetentionTier.TRENDING or string 'TRENDING' applies 1.15x boost."""
    boost_trending = derive_trending_boost(RetentionTier.TRENDING)
    boost_trending_str = derive_trending_boost("TRENDING")
    boost_standard = derive_trending_boost(RetentionTier.STANDARD)
    boost_flag = derive_trending_boost(is_trending=True)

    assert boost_trending == 1.15
    assert boost_trending_str == 1.15
    assert boost_flag == 1.15
    assert boost_standard == 1.0


def test_compute_relevance_score_basic():
    """Verify base formula calculation."""
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    published_6h_ago = now - timedelta(hours=6)

    # similarity = 0.8, weight = 0.25 (25%), decay at 6h (12h half-life) = 2^(-0.5) ≈ 0.70710678
    # explicit magnitude = 1.2, is_trending = False
    expected_decay = math.pow(2.0, -6.0 / 12.0)
    expected_score = 0.8 * 0.25 * expected_decay * 1.2 * 1.0

    score = compute_relevance_score(
        semantic_similarity=0.8,
        portfolio_weight_pct=0.25,
        published_at=published_6h_ago,
        now=now,
        sentiment_magnitude=1.2,
        is_trending=False,
        half_life_hours=12.0,
    )

    assert pytest.approx(score, rel=1e-4) == expected_score


def test_compute_relevance_score_zero_weight():
    """Holding with 0 weight must result in relevance score of exactly 0.0."""
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    published_now = now

    score = compute_relevance_score(
        semantic_similarity=1.0,
        portfolio_weight_pct=0.0,
        published_at=published_now,
        now=now,
        sentiment_magnitude=1.5,
        is_trending=True,
    )
    assert score == 0.0


def test_hyper_personalized_weight_differentiation():
    """
    Direct test of hyper-personalization:
    Identical article relevance differs meaningfully between a 2%-weight holding
    and a 40%-weight holding (score scales 20x).
    """
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    published_now = now

    score_2pct = compute_relevance_score(
        semantic_similarity=0.90,
        portfolio_weight_pct=2.0,  # 2% weight
        published_at=published_now,
        now=now,
        article_tone="positive",
        is_trending=True,
    )

    score_40pct = compute_relevance_score(
        semantic_similarity=0.90,
        portfolio_weight_pct=40.0,  # 40% weight
        published_at=published_now,
        now=now,
        article_tone="positive",
        is_trending=True,
    )

    # 40% weight should yield exactly 20x the relevance score of 2% weight
    assert pytest.approx(score_40pct, rel=1e-5) == score_2pct * 20.0
    assert score_2pct < 0.05
    assert score_40pct > 0.40


def test_compute_relevance_score_percentage_input():
    """Accepts weight as percentage (e.g. 25.0) and normalizes identically to 0.25 fraction."""
    now = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    score_pct = compute_relevance_score(
        semantic_similarity=1.0,
        portfolio_weight_pct=25.0,  # 25%
        published_at=now,
        now=now,
        sentiment_magnitude=1.0,
        is_trending=False,
    )
    score_fraction = compute_relevance_score(
        semantic_similarity=1.0,
        portfolio_weight_pct=0.25,  # 0.25
        published_at=now,
        now=now,
        sentiment_magnitude=1.0,
        is_trending=False,
    )
    assert pytest.approx(score_pct, rel=1e-6) == score_fraction
    assert pytest.approx(score_pct, rel=1e-6) == 0.25


def test_is_feed_eligible_concentration_floor():
    """
    Concentrated holdings (>= 15% weight) qualify at lower cutoff (0.15)
    while normal holdings require standard cutoff (0.30).
    """
    score = 0.20

    # Holding A: 20% weight (concentrated >= 15%) -> eligible at 0.15 cutoff
    assert is_feed_eligible(
        relevance_score=score,
        portfolio_weight_pct=0.20,
        concentration_threshold=0.15,
        standard_cutoff=0.30,
        concentrated_cutoff=0.15,
    ) is True

    # Holding B: 5% weight (not concentrated) -> ineligible since score (0.20) < 0.30 standard cutoff
    assert is_feed_eligible(
        relevance_score=score,
        portfolio_weight_pct=0.05,
        concentration_threshold=0.15,
        standard_cutoff=0.30,
        concentrated_cutoff=0.15,
    ) is False


def test_should_trigger_notification():
    """
    Notification is triggered ONLY when relevance_score >= 0.70 AND
    portfolio_weight_pct >= 15%.
    """
    # 1. Concentrated holding (20%) with high relevance (0.75) -> True
    assert should_trigger_notification(
        relevance_score=0.75,
        portfolio_weight_pct=0.20,
        notification_threshold=0.70,
        concentration_threshold=0.15,
    ) is True

    # 2. Concentrated holding (20%) with low relevance (0.50) -> False
    assert should_trigger_notification(
        relevance_score=0.50,
        portfolio_weight_pct=0.20,
        notification_threshold=0.70,
        concentration_threshold=0.15,
    ) is False

    # 3. Non-concentrated holding (5%) with high relevance (0.95) -> False
    assert should_trigger_notification(
        relevance_score=0.95,
        portfolio_weight_pct=0.05,
        notification_threshold=0.70,
        concentration_threshold=0.15,
    ) is False
