"""
Unit tests for SEBI Regulatory Compliance in Market Reports.

Tests:
1. Mandatory statutory SEBI disclaimer enforcement.
2. Rejection of actionable, advisory, or promotional language in generated commentary.
3. Dropping individual offending third-party headlines without altering remaining quoted text.
4. Ensuring HeadlineItem contains strictly factual metadata with NO sentiment tags.
"""

import pytest
from datetime import date, datetime, timezone

from app.modules.market_reports.domain.entities import (
    HeadlineItem,
    MarketReport,
    SEBI_MANDATORY_DISCLAIMER,
)
from app.modules.market_reports.domain.enums import ReportStatus, ReportType
from app.modules.market_reports.domain.services.compliance import (
    ComplianceViolationError,
    check_actionable_language,
    filter_compliant_headlines,
    is_headline_compliant,
    validate_market_report_compliance,
)


def test_statutory_disclaimer_constant():
    """Validates the exact wording of the statutory SEBI disclaimer."""
    expected = (
        "For informational purposes only. Not investment advice. "
        "Sentinews is not a SEBI-registered investment adviser or research analyst."
    )
    assert SEBI_MANDATORY_DISCLAIMER == expected


def test_headline_item_has_no_sentiment_tags():
    """Validates that HeadlineItem domain model contains NO sentiment tags."""
    item = HeadlineItem(
        headline="RBI keeps repo rate unchanged at 6.5%",
        source="LiveMint",
        url="https://livemint.com/rbi",
        published_at="2026-09-24T08:00:00Z",
    )
    assert not hasattr(item, "sentiment_tag")
    assert not hasattr(item, "market_signal")
    assert not hasattr(item, "sentiment_score")
    assert item.headline == "RBI keeps repo rate unchanged at 6.5%"


@pytest.mark.parametrize(
    "prohibited_text",
    [
        "This stock is a strong buy for 50% upside.",
        "Buy now before breakout occurs.",
        "Analysts recommend a target price of Rs 2500.",
        "Top 3 multibagger stocks to accumulate shares today.",
        "Maintain strict stop loss at 1450.",
        "Guaranteed returns expected in quarterly results.",
        "Screaming buy opportunity in banking sector.",
        "Exclusive stock tips for intraday traders.",
    ],
)
def test_actionable_language_detection(prohibited_text):
    """Detects prohibited actionable phrases in synthesized text."""
    matches = check_actionable_language(prohibited_text)
    assert len(matches) > 0


def test_factual_commentary_passes_compliance():
    """Factual and objective market commentary passes compliance checks."""
    factual_text = (
        "US markets closed higher with S&P 500 up 0.45% and Nasdaq gaining 0.62%. "
        "Asian indices are trading mixed. GIFT Nifty indicates a flat to positive opening. "
        "Domestic headline inflation eased to 4.2% in August."
    )
    matches = check_actionable_language(factual_text)
    assert len(matches) == 0


def test_filter_compliant_headlines_drops_offending_items():
    """
    Third-party headlines with promotional/actionable text are individually dropped,
    while valid headlines are retained unaltered.
    """
    headlines = [
        HeadlineItem(
            headline="US Federal Reserve holds interest rates steady",
            source="Reuters",
            url="https://reuters.com/fed",
            published_at="2026-09-24T06:00:00Z",
        ),
        HeadlineItem(
            headline="Top 5 Multibagger Stocks You Must Buy Now",
            source="Unknown Blog",
            url="https://blog.com/tips",
            published_at="2026-09-24T06:15:00Z",
        ),
        HeadlineItem(
            headline="Tata Motors reports 12% rise in domestic vehicle sales",
            source="Economic Times",
            url="https://economictimes.com/tatamotors",
            published_at="2026-09-24T06:30:00Z",
        ),
        HeadlineItem(
            headline="Brokerage sets new price target on Reliance with strong buy rating",
            source="Financial Daily",
            url="https://findaily.com/ril",
            published_at="2026-09-24T06:45:00Z",
        ),
    ]

    filtered = filter_compliant_headlines(headlines)
    assert len(filtered) == 2
    assert filtered[0].headline == "US Federal Reserve holds interest rates steady"
    assert filtered[1].headline == "Tata Motors reports 12% rise in domestic vehicle sales"


def test_validate_report_compliance_success():
    """Validates that a well-formed report passes validation."""
    report = MarketReport(
        report_type=ReportType.PRE_MARKET,
        report_date=date(2026, 9, 24),
        status=ReportStatus.PUBLISHED,
        sections={
            "global_cues": {"summary_notes": "US indices ended positive overnight."},
            "key_news_headlines": [
                {"headline": "Crude oil slips below $75 per barrel", "source": "Bloomberg"}
            ],
        },
        disclaimer=SEBI_MANDATORY_DISCLAIMER,
        source_providers=["finnhub", "stocknews"],
        is_partial=False,
    )
    # Should not raise
    validate_market_report_compliance(report)


def test_validate_report_compliance_rejects_missing_disclaimer():
    """Rejects report if mandatory statutory disclaimer is missing or altered."""
    report = MarketReport(
        report_type=ReportType.PRE_MARKET,
        report_date=date(2026, 9, 24),
        status=ReportStatus.PUBLISHED,
        sections={"summary": "Market open summary"},
        disclaimer="Custom disclaimer text",
    )
    with pytest.raises(ComplianceViolationError, match="mandatory statutory disclaimer is missing"):
        validate_market_report_compliance(report)


def test_validate_report_compliance_rejects_actionable_commentary():
    """Rejects report if section commentary contains actionable recommendation language."""
    report = MarketReport(
        report_type=ReportType.POST_MARKET,
        report_date=date(2026, 9, 24),
        status=ReportStatus.PUBLISHED,
        sections={
            "analysis": "Traders should buy now and keep target price at 26000."
        },
        disclaimer=SEBI_MANDATORY_DISCLAIMER,
    )
    with pytest.raises(ComplianceViolationError, match="prohibited phrasing detected"):
        validate_market_report_compliance(report)
