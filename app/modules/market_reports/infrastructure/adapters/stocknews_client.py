"""
Stock News API & Free Financial News Adapter.

Implements StockNewsClientPort using the shared instrumented HTTP client
and dual-tier response caching.

Free Tier / No-Cost Policy:
- If STOCKNEWS_API_KEY is not configured or unavailable, automatically falls back
  to 100% free financial news sources: Finnhub Free News and allowlisted financial RSS feeds
  (Yahoo Finance, Moneycontrol, LiveMint, Economic Times).
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
import httpx

from app.cache.market_cache import market_cache
from app.core.config import settings
from app.infrastructure.observability.http_tracer import create_traced_async_client
from app.modules.market_reports.application.ports import StockNewsClientPort
from app.modules.market_reports.domain.entities import HeadlineItem

logger = logging.getLogger("sentinews.market_reports.stocknews")


class StockNewsApiClient(StockNewsClientPort):
    """
    Adapter communicating with Stock News API with automatic zero-cost free fallback
    to Finnhub and Allowlisted RSS feeds.
    """

    BASE_URL = "https://stocknewsapi.com/api/v1"
    CACHE_TTL_SECONDS = 300  # 5 minutes

    def __init__(self, api_key: Optional[str] = None, timeout: float = 8.0):
        self.api_key = api_key or settings.STOCKNEWS_API_KEY
        self.timeout = timeout

    def _get_client(self) -> httpx.AsyncClient:
        return create_traced_async_client(
            provider="stocknews",
            base_url=self.BASE_URL,
            timeout=self.timeout,
        )

    def _cache_key(self, endpoint: str, params: Dict[str, Any]) -> str:
        param_str = json.dumps(params, sort_keys=True)
        h = hashlib.sha256(f"{endpoint}:{param_str}".encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"market_reports:vendor:stocknews:{h}"

    async def get_top_market_news(self, limit: int = 15) -> List[HeadlineItem]:
        """
        Retrieves top factual market news headlines.
        If STOCKNEWS_API_KEY is not configured or encounters an error/paywall,
        seamlessly falls back to free financial news sources.
        """
        # If no API key configured, use free sources directly
        if not self.api_key or not self.api_key.strip():
            logger.info("STOCKNEWS_API_KEY not configured. Using free news sources (Finnhub & RSS).")
            return await self._fetch_from_free_sources(limit=limit)

        try:
            params: Dict[str, Any] = {
                "section": "general",
                "items": limit,
                "token": self.api_key,
            }

            cache_key = self._cache_key("/category", params)
            cached = await market_cache.get(cache_key)

            data = cached
            if data is None:
                async with self._get_client() as client:
                    resp = await client.get("/category", params=params)
                    resp.raise_for_status()
                    data = resp.json()
                await market_cache.set(cache_key, data, ttl_seconds=self.CACHE_TTL_SECONDS)

            raw_articles = []
            if isinstance(data, dict) and "data" in data:
                raw_articles = data.get("data", [])
            elif isinstance(data, list):
                raw_articles = data

            headlines: List[HeadlineItem] = []
            for art in raw_articles:
                title = art.get("title", "").strip()
                if not title:
                    continue
                headlines.append(
                    HeadlineItem(
                        headline=title,
                        source=art.get("source_name", "StockNews"),
                        url=art.get("news_url", ""),
                        published_at=art.get("date", datetime.now(timezone.utc).isoformat()),
                    )
                )

            if headlines:
                return headlines[:limit]
        except Exception as exc:
            logger.warning(
                "StockNews API request failed (%s); seamlessly falling back to free news sources.",
                exc,
            )

        # Fallback to free news sources on error
        return await self._fetch_from_free_sources(limit=limit)

    async def _fetch_from_free_sources(self, limit: int = 15) -> List[HeadlineItem]:
        """
        Zero-cost free fallback: Fetches headlines from Finnhub Free news endpoint
        and verified allowlisted RSS feeds (Yahoo Finance, Moneycontrol, LiveMint, Economic Times).
        """
        headlines: List[HeadlineItem] = []

        # 1. Try free Finnhub general news
        try:
            from app.modules.market_reports.infrastructure.adapters.finnhub_client import FinnhubClient
            finnhub = FinnhubClient()
            raw_news = await finnhub.get_market_news(category="general")
            for item in raw_news:
                headline = item.get("headline", "").strip()
                if headline:
                    published_ts = item.get("datetime")
                    if isinstance(published_ts, (int, float)):
                        pub_str = datetime.fromtimestamp(published_ts, tz=timezone.utc).isoformat()
                    else:
                        pub_str = datetime.now(timezone.utc).isoformat()
                    headlines.append(
                        HeadlineItem(
                            headline=headline,
                            source=item.get("source", "Finnhub"),
                            url=item.get("url", ""),
                            published_at=pub_str,
                        )
                    )
        except Exception as exc:
            logger.debug("Free Finnhub news fallback attempt: %s", exc)

        # 2. If headlines are still needed, fetch from allowlisted financial RSS feeds
        if len(headlines) < limit:
            try:
                from app.integrations.news.rss.allowlist import get_allowlisted_feeds
                from app.integrations.news.rss.fetcher import RSSFeedFetcher

                active_feeds = get_allowlisted_feeds()[:4]  # Yahoo, Moneycontrol, LiveMint, Economic Times
                fetcher = RSSFeedFetcher(feeds=active_feeds, timeout_seconds=5.0)
                rss_items = await fetcher.fetch_all()
                for art in rss_items:
                    headlines.append(
                        HeadlineItem(
                            headline=art.title,
                            source=art.source,
                            url=art.url,
                            published_at=art.published_at.isoformat() if hasattr(art.published_at, "isoformat") else str(art.published_at),
                        )
                    )
            except Exception as exc:
                logger.debug("Free RSS news fallback attempt: %s", exc)

        return headlines[:limit]
