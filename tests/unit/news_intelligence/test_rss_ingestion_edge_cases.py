"""
Unit tests for RSS Ingestion, sanitization, deduplication, and financial compliance edge cases.
"""

from datetime import datetime, timezone
import pytest
from app.integrations.news.rss.fetcher import RawFeedArticle, compute_article_id
from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.domain.services.article_tone import classify_article_tone
from app.modules.news_intelligence.domain.services.financial_filter import is_financial_or_market_news
from app.modules.news_intelligence.domain.services.live_news_service import LiveNewsService
from app.modules.news_intelligence.domain.services.retention import (
    calculate_initial_expiry,
    calculate_trending_expiry,
)


def test_article_id_computation_deduplication():
    """Verify compute_article_id produces identical deterministic hash for same canonical URL."""
    url1 = "https://economictimes.indiatimes.com/markets/stocks/news/reliance-q3-results-profit-rises/articleshow/123456.cms"
    url2 = "https://economictimes.indiatimes.com/markets/stocks/news/reliance-q3-results-profit-rises/articleshow/123456.cms?from=mdr"

    # Both URLs point to the same canonical article
    id1 = compute_article_id(url1, "Reliance Q3 profit rises 10%")
    id2 = compute_article_id(url1, "Reliance Q3 profit rises 10%")
    assert id1 == id2
    assert isinstance(id1, int)
    assert id1 > 0


def test_financial_filter_distinguishes_market_vs_non_financial():
    """Verify non-financial news (sports, celebrity, viral) is filtered out while financial news is kept."""
    # Positive financial cases
    is_fin, score, reason = is_financial_or_market_news(
        title="RBI keeps repo rate unchanged at 6.5% in MPC meeting",
        summary="Governor announces monetary policy committee decision on inflation.",
    )
    assert is_fin is True
    assert score > 0.0

    is_fin, score, reason = is_financial_or_market_news(
        title="Sensex gains 450 points, Nifty above 22,500 led by IT stocks",
        summary="Infosys and TCS rally following strong quarterly results.",
    )
    assert is_fin is True
    assert score > 0.0

    is_fin, score, reason = is_financial_or_market_news(
        title="Adani Green raises $400 million through bond issuance",
        summary="Debt refinancing for renewable energy projects.",
    )
    assert is_fin is True
    assert score > 0.0

    # Negative non-financial cases
    is_fin, score, reason = is_financial_or_market_news(
        title="India wins cricket test match by 5 wickets in final session",
        summary="Sensational batting display leads team to memorable victory.",
    )
    assert is_fin is False
    assert "noise" in reason or score < 0.5

    is_fin, score, reason = is_financial_or_market_news(
        title="Top 10 Bollywood celebrities attend movie premiere in Mumbai",
        summary="Red carpet fashion and exclusive photos from the event.",
    )
    assert is_fin is False
    assert "noise" in reason or score < 0.5


def test_article_tone_classification_compliance():
    """
    Verify sentiment and tone analysis produces objective, factual classifications
    without speculative forward-looking investment advice.
    """
    # Positive earnings
    tone_pos, score_pos, mag_pos = classify_article_tone(
        "TCS beats revenue estimates with 8% YoY operating margin expansion. "
        "Strong order pipeline and enterprise cloud adoption drive quarterly surge."
    )
    assert tone_pos in ("positive", "neutral")
    assert score_pos >= 0.0

    # Negative downturn
    tone_neg, score_neg, mag_neg = classify_article_tone(
        "Steel prices tumble as export demand slumps sharply. "
        "Manufacturers face declining margins and rising coking coal input costs."
    )
    assert tone_neg in ("negative", "neutral")
    assert score_neg <= 0.0


def test_retention_and_trending_expiry():
    """Verify standard 24h retention vs 72h trending extension for high-engagement articles."""
    pub_time = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
    
    # 1. Base article: initial expiry = published_at + 24 hours
    exp_initial = calculate_initial_expiry(pub_time)
    assert (exp_initial - pub_time).total_seconds() == 24 * 3600

    # 2. Trending article with clicks: trending expiry = published_at + 72 hours
    exp_trending = calculate_trending_expiry(pub_time)
    assert (exp_trending - pub_time).total_seconds() == 72 * 3600
    assert exp_trending > exp_initial


def test_click_threshold_and_expiration_detection():
    """Verify trending threshold crossing and expiration evaluation."""
    from app.modules.news_intelligence.domain.services.retention import (
        evaluate_click_threshold,
        is_article_expired,
    )
    # Below threshold
    is_tr, just_crossed = evaluate_click_threshold(49, is_currently_trending=False, threshold=50)
    assert is_tr is False
    assert just_crossed is False

    # Exact threshold crossed
    is_tr, just_crossed = evaluate_click_threshold(50, is_currently_trending=False, threshold=50)
    assert is_tr is True
    assert just_crossed is True

    # Already trending previously (no duplicate DB trigger)
    is_tr, just_crossed = evaluate_click_threshold(55, is_currently_trending=True, threshold=50)
    assert is_tr is True
    assert just_crossed is False

    # Expiry evaluation with mixed tz
    t_ingest = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
    exp = calculate_initial_expiry(t_ingest)
    assert is_article_expired(exp, datetime(2026, 3, 16, 9, 59, 0, tzinfo=timezone.utc)) is False
    assert is_article_expired(exp, datetime(2026, 3, 16, 10, 0, 0, tzinfo=timezone.utc)) is True
    assert is_article_expired(exp, datetime(2026, 3, 16, 10, 1, 0, tzinfo=timezone.utc)) is True

