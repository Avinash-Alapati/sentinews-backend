"""
Multi-feed RSS Fetcher for Allowlisted Financial Publishers.

Adheres strictly to SEBI compliance constraints:
- Only metadata fields (title, short summary, source, canonical link, published_at) are processed.
- No full article bodies are scraped or stored.
- Handles feed-level failures independently with isolated error handling.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
import re
import time
from typing import List, Optional
from bs4 import BeautifulSoup
import feedparser
import httpx

from app.integrations.news.rss.allowlist import RSSFeedSource, get_allowlisted_feeds

logger = logging.getLogger("sentinews.integrations.rss_fetcher")

# Custom User-Agent to avoid publisher 403 rate-limiting on RSS endpoints
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 SentiNews/1.0"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


@dataclass
class RawFeedArticle:
    """
    Extracted raw article metadata directly from RSS feed entry.
    """
    title: str
    summary: str
    url: str
    source: str
    category: str
    published_at: datetime
    content_hash: str


def compute_article_id(url: str, content_hash: str = "") -> int:
    """
    Generates a deterministic, positive 32-bit integer ID for an article based on its URL/hash.
    Ensures stable numeric IDs for frontend React/Next.js keys and engagement tracking.
    """
    source_str = (url or content_hash).strip().lower()
    # Use sha256 to produce stable positive integer within 31-bit range
    h = int(hashlib.sha256(source_str.encode("utf-8")).hexdigest()[:8], 16)
    return max(1, h % 2_000_000_000)


def clean_html_text(raw_html: str, max_chars: int = 500) -> str:
    """
    Strips HTML tags, entities, and whitespace from RSS description/summary.
    Truncates to short description length to prevent reproducing full articles.
    """
    if not raw_html:
        return ""

    try:
        soup = BeautifulSoup(raw_html, "html.parser")
        # Remove script, style, a, img, figure, iframe elements
        for element in soup(["script", "style", "a", "img", "figure", "iframe"]):
            element.extract()
        text = soup.get_text(separator=" ", strip=True)
    except Exception:
        # Fallback to regex
        text = re.sub(r"<[^>]+>", " ", raw_html)
        text = re.sub(r"\s+", " ", text).strip()

    # Normalize whitespace and punctuation spacing
    text = " ".join(text.split())
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def compute_content_hash(title: str, summary: str, url: str) -> str:
    """
    Generates a deterministic SHA-256 hash for deduplication of republished news.
    """
    normalized = f"{title.strip().lower()}|{summary.strip().lower()}|{url.strip().lower()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_entry_datetime(entry: dict) -> datetime:
    """
    Parses published_parsed or updated_parsed struct_time into UTC datetime.
    """
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("created_parsed")
    if parsed_time:
        try:
            return datetime(*parsed_time[:6], tzinfo=timezone.utc)
        except Exception:
            pass

    return datetime.now(timezone.utc)


from app.core.security.ssrf_protection import SSRFSafeAsyncTransport, validate_outbound_url, SSRFValidationError

class RSSFeedFetcher:
    """
    Asynchronous multi-feed RSS fetcher with SSRF-safe pinned IP transport,
    isolated error handling, and bounded concurrency.
    """

    def __init__(
        self,
        feeds: Optional[List[RSSFeedSource]] = None,
        timeout_seconds: float = 15.0,
        max_concurrency: int = 5,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        resolver_fn: Optional[any] = None,
    ):
        self.feeds = feeds or get_allowlisted_feeds()
        self.timeout = timeout_seconds
        self.max_concurrency = max_concurrency
        self.transport = transport
        self.resolver_fn = resolver_fn

    async def fetch_feed(
        self,
        client: httpx.AsyncClient,
        feed_source: RSSFeedSource,
        semaphore: asyncio.Semaphore,
    ) -> List[RawFeedArticle]:
        """
        Fetches and parses a single RSS feed within the concurrency semaphore.
        Errors in this feed are isolated and logged.
        """
        async with semaphore:
            articles: List[RawFeedArticle] = []
            try:
                # Validate outbound URL against SSRF rules before sending
                validate_outbound_url(feed_source.url, allow_http=True, resolver_fn=self.resolver_fn)

                response = await client.get(
                    feed_source.url,
                    headers=DEFAULT_HEADERS,
                    timeout=self.timeout,
                    follow_redirects=True,
                )

                if response.status_code != 200:
                    logger.warning(
                        "Feed fetch HTTP error for %s (%s): Status %d",
                        feed_source.name,
                        feed_source.url,
                        response.status_code,
                    )
                    return []

                # Parse content with feedparser
                parsed = feedparser.parse(response.content)

                if parsed.bozo and not parsed.entries:
                    logger.debug(
                        "Feedparser failed to parse entries for %s: %s",
                        feed_source.name,
                        getattr(parsed, "bozo_exception", "Unknown parse error"),
                    )
                    return []

                for entry in parsed.entries:
                    title = entry.get("title", "").strip()
                    link = entry.get("link", "").strip()
                    if not title or not link:
                        continue

                    # Extract raw summary from best available field
                    raw_summary = (
                        entry.get("summary")
                        or entry.get("description")
                        or (entry.get("summary_detail", {}).get("value") if isinstance(entry.get("summary_detail"), dict) else "")
                        or ""
                    )
                    clean_summary = clean_html_text(raw_summary)
                    pub_date = parse_entry_datetime(entry)
                    content_hash = compute_content_hash(title, clean_summary, link)

                    articles.append(
                        RawFeedArticle(
                            title=title,
                            summary=clean_summary,
                            url=link,
                            source=feed_source.publisher,
                            category=feed_source.category,
                            published_at=pub_date,
                            content_hash=content_hash,
                        )
                    )

                logger.debug(
                    "Fetched %d articles from %s",
                    len(articles),
                    feed_source.name,
                )

            except (SSRFValidationError, ValueError) as ssrf_err:
                logger.warning("SSRF check blocked feed %s (%s): %s", feed_source.name, feed_source.url, ssrf_err)
                raise ssrf_err
            except httpx.TimeoutException:
                logger.warning("Timeout fetching feed %s (%s)", feed_source.name, feed_source.url)
            except Exception as exc:
                logger.warning(
                    "Error fetching feed %s (%s): %s",
                    feed_source.name,
                    feed_source.url,
                    str(exc),
                )

            return articles

    async def fetch_all(self) -> List[RawFeedArticle]:
        """
        Fetches all allowlisted feeds concurrently with individual failure isolation and bounded concurrency.
        """
        semaphore = asyncio.Semaphore(self.max_concurrency)
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
        from app.infrastructure.observability.http_tracer import instrument_httpx_client

        transport = self.transport or SSRFSafeAsyncTransport(limits=limits, resolver_fn=self.resolver_fn)

        async with httpx.AsyncClient(transport=transport, verify=True, timeout=self.timeout) as client:
            instrument_httpx_client(client, provider="rss_feeds")
            tasks = [self.fetch_feed(client, feed, semaphore) for feed in self.feeds if feed.enabled]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_articles: List[RawFeedArticle] = []
        for res in results:
            if isinstance(res, list):
                all_articles.extend(res)
            elif isinstance(res, Exception):
                logger.warning("Feed task encountered exception: %s", str(res))

        return all_articles
