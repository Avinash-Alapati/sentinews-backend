"""
Ingest News From Feeds Use Case.

Coordinates the automated scheduled RSS ingestion pipeline:
1. Fetches articles across all allowlisted Indian and global financial RSS feeds.
2. Deduplicates incoming articles by canonical URL and deterministic content hash.
3. Tags ticker symbols and market sectors against known portfolio assets / benchmark list.
4. Classifies linguistic tone (article_tone) and trading hours context (market_context).
5. Computes dense embeddings locally (sentence-transformers) for relevance matching.
6. Persists compliant metadata only (no full body scraping) with a 24h initial expiry.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from app.integrations.news.rss.fetcher import RawFeedArticle, RSSFeedFetcher
from app.modules.news_intelligence.application.ports import (
    AssetReferenceReader,
    EmbeddingProvider,
    NewsRepository,
)
from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.domain.services.article_tone import classify_article_tone
from app.modules.news_intelligence.domain.services.financial_filter import (
    is_financial_or_market_news,
)
from app.modules.news_intelligence.domain.services.market_context import get_market_context
from app.modules.news_intelligence.domain.services.retention import calculate_initial_expiry

logger = logging.getLogger("sentinews.news_intelligence.use_case.ingest_news")


class IngestNewsFromFeedsUseCase:
    """
    Application use case for automated RSS news feed ingestion.
    """

    def __init__(
        self,
        news_repo: NewsRepository,
        rss_fetcher: Optional[RSSFeedFetcher] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        asset_reader: Optional[AssetReferenceReader] = None,
    ):
        self.news_repo = news_repo
        self.rss_fetcher = rss_fetcher or RSSFeedFetcher()
        self.embedding_provider = embedding_provider
        self.asset_reader = asset_reader

    async def execute(self) -> Dict[str, int]:
        """
        Executes the complete feed ingestion and pipeline processing.

        Returns:
            Dict[str, int]: Ingestion statistics (fetched, new_ingested, deduplicated).
        """
        logger.info("Starting automated RSS news feed ingestion cycle...")

        # 1. Fetch raw articles from all allowlisted feeds
        raw_items = await self.rss_fetcher.fetch_all()
        total_fetched = len(raw_items)
        if total_fetched == 0:
            logger.info("No articles fetched from RSS feeds.")
            return {"fetched": 0, "new_ingested": 0, "deduplicated": 0}

        # 2. Extract candidate URLs and content hashes
        candidate_urls = [item.url for item in raw_items if item.url]
        candidate_hashes = [item.content_hash for item in raw_items if item.content_hash]

        existing_urls, existing_hashes = await self.news_repo.get_existing_urls_and_hashes(
            urls=candidate_urls,
            hashes=candidate_hashes,
        )

        # 3. Retrieve known assets for ticker/sector tagging
        known_assets: List[Tuple[str, str, str]] = []
        if self.asset_reader:
            try:
                known_assets = await self.asset_reader.get_known_assets()
            except Exception as exc:
                logger.warning("Error reading known assets: %s", exc)

        # Build asset match patterns
        asset_patterns = []
        for symbol, name, sector in known_assets:
            if symbol and len(symbol) >= 2:
                # Regex word boundary match for symbol and company name
                escaped_sym = re.escape(symbol)
                sym_pattern = re.compile(rf"\b{escaped_sym}\b", re.IGNORECASE)
                name_pattern = None
                if name and len(name) > 3:
                    # Clean common suffixes like Ltd, Limited, Corp
                    clean_name = re.sub(r"\b(Ltd|Limited|Corp|Corporation|Industries|Company)\b", "", name, flags=re.IGNORECASE).strip()
                    if clean_name and len(clean_name) > 3:
                        escaped_name = re.escape(clean_name)
                        name_pattern = re.compile(rf"\b{escaped_name}\b", re.IGNORECASE)
                asset_patterns.append((symbol, sector, sym_pattern, name_pattern))

        # 4. Filter and process new articles
        seen_urls: Set[str] = set()
        seen_hashes: Set[str] = set()
        articles_to_save: List[NewsArticle] = []
        texts_to_embed: List[str] = []

        for item in raw_items:
            # Deduplicate against DB and current batch
            if item.url in existing_urls or item.url in seen_urls:
                continue
            if item.content_hash in existing_hashes or item.content_hash in seen_hashes:
                continue

            seen_urls.add(item.url)
            seen_hashes.add(item.content_hash)

            text_corpus = f"{item.title} {item.summary}"

            # Match tickers and sectors
            matched_symbols: Set[str] = set()
            matched_sectors: Set[str] = set()

            if item.category and item.category != "General":
                matched_sectors.add(item.category)

            for sym, sector, sym_pat, name_pat in asset_patterns:
                if sym_pat.search(text_corpus) or (name_pat and name_pat.search(text_corpus)):
                    matched_symbols.add(sym.upper())
                    if sector:
                        matched_sectors.add(sector)

            # Strict Financial, Macroeconomic, and Market Relevance Filter
            is_relevant, fin_score, reason = is_financial_or_market_news(
                title=item.title,
                summary=item.summary,
                category=item.category,
                matched_symbols=list(matched_symbols),
            )
            if not is_relevant:
                logger.debug(
                    "Skipping non-market article during ingestion '%s' (%s)",
                    item.title,
                    reason,
                )
                continue

            # Linguistic tone classification
            tone, score, magnitude = classify_article_tone(f"{item.title}. {item.summary}")

            # Market context classification
            market_ctx = get_market_context(item.published_at)

            # Initial 24h expiration
            initial_expiry = calculate_initial_expiry(item.published_at)

            article = NewsArticle(
                title=item.title,
                summary=item.summary,
                content="",  # Compliant metadata only: zero full-body scraping
                url=item.url,
                source=item.source,
                symbols=sorted(list(matched_symbols)),
                sectors=sorted(list(matched_sectors)),
                article_tone=tone,
                market_context=market_ctx,
                content_hash=item.content_hash,
                sentiment_score=score,
                sentiment_magnitude=magnitude,
                click_count=0,
                is_trending=False,
                published_at=item.published_at,
                expires_at=initial_expiry,
                embedding=None,
            )

            articles_to_save.append(article)
            texts_to_embed.append(f"{item.title}. {item.summary}")

        deduplicated_count = total_fetched - len(articles_to_save)

        # 5. Compute local embeddings in batch
        if articles_to_save and self.embedding_provider and texts_to_embed:
            try:
                embeddings = self.embedding_provider.embed_batch(texts_to_embed)
                for article, emb in zip(articles_to_save, embeddings):
                    article.embedding = emb
            except Exception as exc:
                logger.error("Failed to generate batch embeddings: %s", exc)

        # 6. Save new articles to database
        saved_articles = []
        if articles_to_save:
            saved_articles = await self.news_repo.save_articles_batch(articles_to_save)

        logger.info(
            "RSS Ingestion complete: %d fetched, %d new ingested, %d deduplicated.",
            total_fetched,
            len(saved_articles),
            deduplicated_count,
        )

        return {
            "fetched": total_fetched,
            "new_ingested": len(saved_articles),
            "deduplicated": deduplicated_count,
        }
