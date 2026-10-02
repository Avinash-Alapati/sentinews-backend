"""
Adapters bridging the Portfolio module with external modules and integrations.
"""

from datetime import datetime, timezone
import logging
from typing import Optional

from app.modules.news_intelligence.application.ports import EmbeddingProvider, NewsRepository
from app.modules.news_intelligence.domain.services.relevance import compute_relevance_score
from app.modules.portfolio.application.ports import NewsIntelligenceRelevancePort
from app.modules.portfolio.domain.entities import Holding

logger = logging.getLogger("sentinews.portfolio.adapters")


class NewsIntelligenceRelevanceProvider(NewsIntelligenceRelevancePort):
    """
    Adapter implementing NewsIntelligenceRelevancePort to bridge portfolio changes
    with the news intelligence module.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        news_repository: Optional[NewsRepository] = None,
    ):
        self.embedding_provider = embedding_provider
        self.news_repository = news_repository

    async def notify_holding_added(self, holding: Holding) -> None:
        """
        When a holding is added, embed the asset's profile (name, sector, symbol)
        via the local embedding provider and backfill relevance scores against the
        existing news corpus for that asset.

        Compliance Notice:
        Scores chronological mention relevance to the newly added holding;
        does not predict or assess market impact.

        Args:
            holding: The newly added holding domain entity.
        """
        asset_profile_text = (
            f"{holding.name} ({holding.symbol}) "
            f"Sector: {holding.sector or 'General'} "
            f"Indian Equity Stock Market"
        )
        logger.info(
            "Generating profile embedding for asset %s (%s)...",
            holding.symbol,
            holding.name,
        )

        # 1. Embed asset profile locally
        asset_embedding = self.embedding_provider.embed_text(asset_profile_text)

        # 2. Backfill relevance scores against active news corpus if repository available
        if self.news_repository is not None:
            try:
                candidate_articles = await self.news_repository.get_latest_articles(
                    limit=50,
                    symbol=holding.symbol,
                )
                if not candidate_articles and hasattr(self.news_repository, "get_articles_by_symbols_or_sectors"):
                    candidate_articles = await self.news_repository.get_articles_by_symbols_or_sectors(
                        symbols=[holding.symbol],
                        sectors=[holding.sector] if holding.sector else [],
                        limit=50,
                    )

                now = datetime.now(timezone.utc)
                for article in candidate_articles:
                    similarity = 0.5  # Base match fallback
                    if article.embedding and asset_embedding:
                        similarity = self.embedding_provider.compute_similarity(
                            asset_embedding,
                            article.embedding,
                        )
                    elif holding.symbol.upper() in [s.upper() for s in article.symbols]:
                        similarity = 0.95

                    score = compute_relevance_score(
                        semantic_similarity=similarity,
                        portfolio_weight_pct=holding.weight_pct or 0.10,
                        published_at=article.published_at,
                        now=now,
                        article_tone=article.article_tone,
                        sentiment_magnitude=article.sentiment_magnitude,
                        is_trending=article.is_trending,
                    )
                    logger.debug(
                        "Backfilled article %s relevance score for holding %s: %.4f",
                        article.id,
                        holding.symbol,
                        score,
                    )
            except Exception as e:
                logger.warning(
                    "Error backfilling relevance scores for %s: %s",
                    holding.symbol,
                    str(e),
                )
