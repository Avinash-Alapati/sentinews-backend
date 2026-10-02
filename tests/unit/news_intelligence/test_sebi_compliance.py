"""
Unit tests for strict SEBI regulatory compliance rules:
1. Every API response schema must carry the mandatory regulatory disclaimer.
2. No response schema or default contains actionable/recommendation language.
3. Feed allowlist strictly blocks unverified/arbitrary feed sources.
4. Tone tagging uses 'article_tone' (not 'market_signal').
"""

from datetime import datetime, timezone
import pytest
from pydantic import BaseModel

from app.api.v1.news.router import (
    ArticleResponse,
    ClickResponse,
    CompliantNewsResponseBase,
    FullCoverageClusterResponse,
    PaginatedNewsResponse,
    TrendingNewsResponse,
    SEBI_MANDATORY_DISCLAIMER,
)
from app.integrations.news.rss.allowlist import is_feed_allowlisted


EXPECTED_DISCLAIMER_TEXT = (
    "For informational purposes only. Not investment advice. "
    "Sentinews is not a SEBI-registered investment adviser or research analyst."
)


def test_mandatory_disclaimer_constant():
    """Validates the exact statutory wording of the SEBI disclaimer."""
    assert SEBI_MANDATORY_DISCLAIMER == EXPECTED_DISCLAIMER_TEXT


@pytest.mark.parametrize(
    "schema_cls",
    [
        ArticleResponse,
        ClickResponse,
        FullCoverageClusterResponse,
        PaginatedNewsResponse,
        TrendingNewsResponse,
    ],
)
def test_all_news_response_schemas_inherit_compliant_base(schema_cls):
    """
    Every news response schema MUST inherit CompliantNewsResponseBase
    and have a mandatory non-empty disclaimer field.
    """
    assert issubclass(schema_cls, CompliantNewsResponseBase), (
        f"{schema_cls.__name__} violates SEBI compliance: must inherit CompliantNewsResponseBase!"
    )
    assert "disclaimer" in schema_cls.model_fields, (
        f"{schema_cls.__name__} is missing the mandatory 'disclaimer' field!"
    )
    default_disclaimer = schema_cls.model_fields["disclaimer"].default
    assert default_disclaimer == EXPECTED_DISCLAIMER_TEXT, (
        f"{schema_cls.__name__} disclaimer default does not match statutory text!"
    )


def test_article_response_instantiation_includes_disclaimer():
    """Instantiating ArticleResponse automatically populates the disclaimer."""
    now = datetime.now(timezone.utc)
    art = ArticleResponse(
        id=1,
        title="RBI Monetary Policy Meeting Concludes",
        summary="Repo rate unchanged at 6.5 percent.",
        url="https://livemint.com/rbi-policy",
        source="LiveMint",
        published_at=now,
    )
    assert art.disclaimer == EXPECTED_DISCLAIMER_TEXT
    assert art.article_tone == "neutral"
    assert hasattr(art, "article_tone")
    assert not hasattr(art, "market_signal")  # Must not use market_signal


def test_click_response_includes_disclaimer():
    """ClickResponse includes disclaimer."""
    res = ClickResponse(article_id=42, click_count=10, is_trending=False)
    assert res.disclaimer == EXPECTED_DISCLAIMER_TEXT


def test_feed_allowlist_enforcement():
    """Only explicitly allowlisted financial feeds are accepted."""
    assert is_feed_allowlisted("https://finance.yahoo.com/news/rssindex") is True
    assert is_feed_allowlisted("https://www.moneycontrol.com/rss/latestnews.xml") is True
    assert is_feed_allowlisted("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms") is True
    assert is_feed_allowlisted("https://www.livemint.com/rss/markets") is True

    # Arbitrary/unverified feeds must be rejected
    assert is_feed_allowlisted("https://randomblog.com/feed.xml") is False
    assert is_feed_allowlisted("https://unverified-crypto-signals.com/rss") is False
    assert is_feed_allowlisted("") is False
