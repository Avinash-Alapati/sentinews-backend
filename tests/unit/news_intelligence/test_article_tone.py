"""
Unit tests for Article Tone Classification.
"""

import pytest
from app.modules.news_intelligence.domain.services.article_tone import classify_article_tone


def test_positive_article_tone():
    """Headlines with optimistic wording must be classified as 'positive'."""
    text = "Tata Motors Q3 profit jumps 45% on strong Jaguar Land Rover demand and robust EV sales"
    tone, score, mag = classify_article_tone(text)
    assert tone == "positive"
    assert score > 0.05
    assert mag > 0.0


def test_negative_article_tone():
    """Headlines with distressing or declining wording must be classified as 'negative'."""
    text = "IT stocks tumble sharply as revenue warnings trigger steep decline and unexpected loss"
    tone, score, mag = classify_article_tone(text)
    assert tone == "negative"
    assert score < -0.05
    assert mag > 0.0


def test_neutral_article_tone():
    """Factual, descriptive headlines without emotional valence must be classified as 'neutral'."""
    text = "Ministry of Finance releases scheduled GST monthly collection data report for August"
    tone, score, mag = classify_article_tone(text)
    assert tone == "neutral"
    assert -0.05 <= score <= 0.05


def test_empty_string_tone():
    """Empty text gracefully defaults to 'neutral'."""
    tone, score, mag = classify_article_tone("")
    assert tone == "neutral"
    assert score == 0.0
