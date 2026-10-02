"""
Domain service for classifying an article's own language tone.

SEBI Compliance Notice:
This service classifies ONLY the linguistic tone of the article text
(positive / neutral / negative language used by the author).
It is NOT a market signal, trading recommendation, or price forecast.
"""

from typing import Tuple
from app.infrastructure.observability.decorators import track_compute

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _analyzer = SentimentIntensityAnalyzer()
except ImportError:
    _analyzer = None

POSITIVE_THRESHOLD = 0.05
NEGATIVE_THRESHOLD = -0.05


@track_compute("news_classify_tone")
def classify_article_tone(text: str) -> Tuple[str, float, float]:
    """
    Analyzes the linguistic tone of the article headline and summary.

    Args:
        text: Article title and summary text.

    Returns:
        Tuple[str, float, float]:
            - tone: 'positive', 'neutral', or 'negative'
            - score: compound score in [-1.0, 1.0]
            - magnitude: absolute intensity in [0.0, 1.0+]
    """
    if not text or not text.strip():
        return "neutral", 0.0, 1.0

    if _analyzer is not None:
        scores = _analyzer.polarity_scores(text)
        compound = float(scores.get("compound", 0.0))
    else:
        # Simple fallback lexicon if VADER is not available
        lower = text.lower()
        pos_words = ["gain", "rise", "surge", "record", "growth", "jump", "profit", "bull", "beat", "rally", "strong"]
        neg_words = ["fall", "drop", "plunge", "loss", "crash", "decline", "bear", "miss", "weak", "slump", "deficit"]
        pos_count = sum(1 for w in pos_words if w in lower)
        neg_count = sum(1 for w in neg_words if w in lower)
        total = pos_count + neg_count
        if total == 0:
            compound = 0.0
        else:
            compound = (pos_count - neg_count) / total

    if compound >= POSITIVE_THRESHOLD:
        tone = "positive"
    elif compound <= NEGATIVE_THRESHOLD:
        tone = "negative"
    else:
        tone = "neutral"

    magnitude = max(0.5, abs(compound) * 2.0)
    return tone, compound, magnitude
