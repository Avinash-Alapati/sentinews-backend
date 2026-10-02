"""
Unit tests for RSS feed deduplication logic and content hashing.
"""

from datetime import datetime, timezone
import pytest

from app.integrations.news.rss.fetcher import (
    clean_html_text,
    compute_content_hash,
    RawFeedArticle,
)
from app.modules.news_intelligence.application.use_cases.ingest_news_from_feeds import (
    IngestNewsFromFeedsUseCase,
)
from app.modules.news_intelligence.domain.entities import NewsArticle


class MockNewsRepository:
    def __init__(self, existing_urls=None, existing_hashes=None):
        self.existing_urls = set(existing_urls or [])
        self.existing_hashes = set(existing_hashes or [])
        self.saved_articles = []

    async def get_existing_urls_and_hashes(self, urls, hashes):
        found_urls = set(u for u in urls if u in self.existing_urls)
        found_hashes = set(h for h in hashes if h in self.existing_hashes)
        return found_urls, found_hashes

    async def save_articles_batch(self, articles):
        self.saved_articles.extend(articles)
        return articles


class MockFetcher:
    def __init__(self, articles):
        self.articles = articles

    async def fetch_all(self):
        return self.articles


def test_clean_html_text_sanitization():
    """HTML tags, scripts, and excessive whitespace must be stripped."""
    raw = "<p>Sensex jumps <b>500 points</b>. <script>alert('x')</script> Read more <a href='http://evil.com'>here</a>.</p>"
    clean = clean_html_text(raw, max_chars=200)
    assert "<p>" not in clean
    assert "<script>" not in clean
    assert "alert" not in clean
    assert "Sensex jumps 500 points. Read more ." in clean or "Sensex jumps 500 points. Read more" in clean


def test_compute_content_hash_deterministic():
    """Content hash must be identical for identical content regardless of outer whitespace."""
    h1 = compute_content_hash("Tata Motors Q2 Results", "Profits rose by 20 percent.", "https://example.com/art1")
    h2 = compute_content_hash("  Tata Motors Q2 Results  ", "Profits rose by 20 percent. ", "https://example.com/art1 ")
    h3 = compute_content_hash("Different Title", "Profits rose by 20 percent.", "https://example.com/art1")

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


@pytest.mark.asyncio
async def test_ingest_deduplicates_existing_urls_and_hashes():
    """UseCase must reject articles with URLs or hashes already present in the DB or in the batch."""
    url1 = "https://moneycontrol.com/news/1"
    url2 = "https://economictimes.com/news/2"
    url3 = "https://livemint.com/news/3"

    hash1 = compute_content_hash("TCS Beats Estimates", "Q3 profits surge", url1)
    hash2 = compute_content_hash("Infosys Guidance", "Growth steady", url2)
    hash3 = compute_content_hash("Reliance AGM Updates", "Green energy investments", url3)

    # Pre-populate repository with url1 and hash2
    repo = MockNewsRepository(existing_urls=[url1], existing_hashes=[hash2])

    now = datetime.now(timezone.utc)
    raw_articles = [
        RawFeedArticle(title="TCS Beats Estimates", summary="Q3 profits surge", url=url1, source="Moneycontrol", category="Markets", published_at=now, content_hash=hash1),
        RawFeedArticle(title="Infosys Guidance", summary="Growth steady", url="https://another-url.com/republished", source="ET", category="Markets", published_at=now, content_hash=hash2),
        RawFeedArticle(title="Reliance AGM Updates", summary="Green energy investments", url=url3, source="LiveMint", category="Markets", published_at=now, content_hash=hash3),
        # Duplicate within same batch
        RawFeedArticle(title="Reliance AGM Updates", summary="Green energy investments", url=url3, source="LiveMint", category="Markets", published_at=now, content_hash=hash3),
    ]

    use_case = IngestNewsFromFeedsUseCase(
        news_repo=repo,
        rss_fetcher=MockFetcher(raw_articles),
    )

    stats = await use_case.execute()

    assert stats["fetched"] == 4
    assert stats["new_ingested"] == 1  # Only article 3 should be saved
    assert stats["deduplicated"] == 3
    assert len(repo.saved_articles) == 1
    assert repo.saved_articles[0].title == "Reliance AGM Updates"
    assert repo.saved_articles[0].content == ""  # Compliant: no full body stored
