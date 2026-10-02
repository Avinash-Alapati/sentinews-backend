"""
Get Personalized Feed Use Case.

Compliance Framing:
Orchestrates the retrieval of news articles that mention holdings owned in a user's portfolio.
Scores and ranks chronological relevance to user holdings; does NOT provide market signals,
price impact assessment, or trading recommendations.
"""

from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Set, Union
from uuid import UUID

from app.modules.news_intelligence.application.ports import (
    EmbeddingProvider,
    HoldingWeightView,
    NewsRepository,
    NotificationDispatcher,
    PortfolioHoldingsReader,
)
from app.modules.news_intelligence.domain.entities import (
    NewsArticle,
    PersonalizedFeed,
    PersonalizedFeedItem,
)
from app.modules.news_intelligence.domain.services.clustering import cluster_articles
from app.modules.news_intelligence.domain.services.digest_service import (
    ACTION_IMMEDIATE_ALERT,
    route_article_notification,
)
from app.modules.news_intelligence.domain.services.relevance import (
    DEFAULT_CONCENTRATION_THRESHOLD,
    DEFAULT_NOTIFICATION_THRESHOLD,
    compute_relevance_score,
    is_feed_eligible,
    should_trigger_notification,
)

logger = logging.getLogger("sentinews.news_intelligence.use_case.get_personalized_feed")


class GetPersonalizedFeedUseCase:
    """
    Use case orchestrating the generation of a personalized, ranked news feed
    that mentions holdings owned in a portfolio.
    """

    def __init__(
        self,
        portfolio_holdings_reader: Optional[PortfolioHoldingsReader] = None,
        news_repository: Optional[NewsRepository] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        notification_dispatcher: Optional[NotificationDispatcher] = None,
        notification_threshold: float = DEFAULT_NOTIFICATION_THRESHOLD,
        concentration_threshold: float = DEFAULT_CONCENTRATION_THRESHOLD,
        **kwargs,
    ):
        if portfolio_holdings_reader is None and "portfolio_repository" in kwargs:
            from app.modules.news_intelligence.infrastructure.adapters.portfolio_holdings_reader import (
                PortfolioHoldingsReaderAdapter,
            )
            portfolio_holdings_reader = PortfolioHoldingsReaderAdapter(
                portfolio_repository=kwargs["portfolio_repository"]
            )
        if news_repository is None and "news_repo" in kwargs:
            news_repository = kwargs["news_repo"]

        self.portfolio_reader = portfolio_holdings_reader
        self.news_repo = news_repository
        self.embedding_provider = embedding_provider
        self.notification_dispatcher = notification_dispatcher
        self.notification_threshold = notification_threshold
        self.concentration_threshold = concentration_threshold

    async def execute(
        self,
        portfolio_id: Union[UUID, int, str],
        user_id: Optional[int] = None,
        limit: int = 20,
        min_score: Optional[float] = None,
    ) -> PersonalizedFeed:
        """
        Generates a ranked feed of news that mentions holdings owned in the given portfolio.

        Args:
            portfolio_id: Target portfolio ID.
            user_id: Optional user ID for alert routing.
            limit: Maximum number of feed items to return.
            min_score: Optional explicit relevance score floor filter.

        Returns:
            PersonalizedFeed: Ranked list of personalized feed items.
        """
        # 1. Load portfolio holdings and weights via PortfolioHoldingsReader port
        holdings: List[HoldingWeightView] = await self.portfolio_reader.get_holdings_with_weights(
            portfolio_id=portfolio_id
        )

        if not holdings:
            # Fallback for empty portfolio: return latest general news
            general_articles = await self.news_repo.get_active_articles(limit=limit)
            items = [
                PersonalizedFeedItem(
                    article=art,
                    relevance_score=0.10,
                    matched_holdings=[],
                    is_concentrated_holding_match=False,
                    notification_triggered=False,
                )
                for art in general_articles
            ]
            numeric_pid = int(portfolio_id) if str(portfolio_id).isdigit() else 0
            return PersonalizedFeed(
                portfolio_id=numeric_pid,
                items=items,
                total_count=len(items),
            )

        # 2. Query candidate news corpus in a single batched call across all holding symbols & sectors
        symbols = [h.symbol for h in holdings if h.symbol]
        sectors = [h.sector for h in holdings if h.sector and h.sector.upper() != "GENERAL"]
        candidate_articles = await self.news_repo.get_articles_by_symbols_or_sectors(
            symbols=symbols,
            sectors=sectors,
            limit=max(60, limit * 3),
        )

        # Fallback to active articles if no targeted matches found
        if not candidate_articles:
            candidate_articles = await self.news_repo.get_active_articles(limit=limit * 2)

        # 3. Apply Full Coverage clustering so feed doesn't show near-duplicate reports
        clusters = cluster_articles(candidate_articles)
        clustered_primary_articles = [c.primary_article for c in clusters]

        # Pre-compute holding vector embeddings once instead of inside nested loops
        holding_vectors: Dict[str, List[float]] = {}
        if self.embedding_provider:
            for holding in holdings:
                holding_text = f"{holding.name} ({holding.symbol}) Sector: {holding.sector}"
                try:
                    holding_vectors[holding.symbol.upper()] = self.embedding_provider.embed_text(holding_text)
                except Exception as emb_err:
                    logger.debug("Holding embedding generation failed for %s: %s", holding.symbol, emb_err)

        now = datetime.now(timezone.utc)
        feed_items_map: Dict[Any, PersonalizedFeedItem] = {}

        # 4. Compute relevance score per clustered article per holding
        for article in clustered_primary_articles:
            art_id = article.id or hash(article.url or article.title)
            best_score = 0.0
            matched_symbols: List[str] = []
            is_concentrated_match = False
            notify_triggered = False

            for holding in holdings:
                holding_vec = holding_vectors.get(holding.symbol.upper())
                sim = self._calculate_similarity(holding, article, holding_vec=holding_vec)

                score = compute_relevance_score(
                    semantic_similarity=sim,
                    portfolio_weight_pct=holding.weight_pct,
                    published_at=article.published_at,
                    now=now,
                    article_tone=article.article_tone,
                    sentiment_magnitude=article.sentiment_magnitude,
                    is_trending=article.is_trending,
                )

                eligible = is_feed_eligible(
                    relevance_score=score,
                    portfolio_weight_pct=holding.weight_pct,
                    concentration_threshold=self.concentration_threshold,
                )

                if eligible or (min_score is not None and score >= min_score):
                    if score > best_score:
                        best_score = score

                    if holding.symbol not in matched_symbols:
                        matched_symbols.append(holding.symbol)

                    if holding.weight_pct >= self.concentration_threshold:
                        is_concentrated_match = True

                    # 5. Route notifications via digest_service.py threshold logic
                    action, reason = route_article_notification(
                        relevance_score=score,
                        portfolio_weight_pct=holding.weight_pct,
                        notification_threshold=self.notification_threshold,
                        concentration_threshold=self.concentration_threshold,
                    )

                    if action == ACTION_IMMEDIATE_ALERT:
                        notify_triggered = True
                        if self.notification_dispatcher is not None:
                            numeric_pid = int(portfolio_id) if str(portfolio_id).isdigit() else 1
                            self.notification_dispatcher.send_relevance_alert(
                                portfolio_id=numeric_pid,
                                user_id=user_id or 1,
                                article_id=article.id or 0,
                                symbol=holding.symbol,
                                relevance_score=score,
                                article_title=article.title,
                            )

            if best_score > 0.0:
                feed_items_map[art_id] = PersonalizedFeedItem(
                    article=article,
                    relevance_score=round(best_score, 4),
                    matched_holdings=matched_symbols,
                    is_concentrated_holding_match=is_concentrated_match,
                    notification_triggered=notify_triggered,
                )

        # 6. Rank feed in descending order of relevance score
        ranked_items = sorted(
            feed_items_map.values(),
            key=lambda item: item.relevance_score,
            reverse=True,
        )

        final_items = ranked_items[:limit]
        numeric_pid = int(portfolio_id) if str(portfolio_id).isdigit() else 0

        return PersonalizedFeed(
            portfolio_id=numeric_pid,
            items=final_items,
            total_count=len(final_items),
        )

    def _calculate_similarity(
        self,
        holding: HoldingWeightView,
        article: NewsArticle,
        holding_vec: Optional[List[float]] = None,
    ) -> float:
        """Helper to calculate similarity between a holding and an article."""
        art_symbols = [s.upper() for s in article.symbols]
        holding_sym = holding.symbol.upper()

        if holding_sym in art_symbols:
            return 0.95

        if self.embedding_provider and article.embedding:
            if holding_vec is None:
                holding_text = f"{holding.name} ({holding.symbol}) Sector: {holding.sector}"
                holding_vec = self.embedding_provider.embed_text(holding_text)
            return self.embedding_provider.compute_similarity(holding_vec, article.embedding)

        if holding.sector and any(
            holding.sector.upper() == sec.upper() for sec in article.sectors
        ):
            return 0.65

        # Check if symbol appears in title or summary
        title_summary_upper = f"{article.title} {article.summary}".upper()
        if holding_sym in title_summary_upper or (holding.name and holding.name.upper() in title_summary_upper):
            return 0.90

        # If article specifically tags other symbols/sectors that don't match, return 0.0
        if article.symbols or article.sectors:
            return 0.0

        return 0.10
