"""
Integration test for personalized news feed ranking, compliance framing, and concentration floor filtering.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union
from uuid import UUID
import pytest

from app.api.v1.portfolio.router import SEBI_MANDATORY_DISCLAIMER
from app.modules.news_intelligence.application.ports import (
    HoldingWeightView,
    NewsRepository,
    PortfolioHoldingsReader,
)
from app.modules.news_intelligence.application.use_cases.get_personalized_feed import (
    GetPersonalizedFeedUseCase,
)
from app.modules.news_intelligence.domain.entities import NewsArticle


class InMemoryPortfolioHoldingsReader(PortfolioHoldingsReader):
    def __init__(self, holdings: List[HoldingWeightView]):
        self._holdings = holdings

    async def get_holdings_with_weights(
        self, portfolio_id: Union[UUID, int, str]
    ) -> List[HoldingWeightView]:
        return self._holdings


class InMemoryNewsRepository(NewsRepository):
    def __init__(self, articles: List[NewsArticle]):
        self._articles = articles

    async def get_active_articles(self, limit: int = 50) -> List[NewsArticle]:
        return self._articles[:limit]

    async def get_latest_articles(
        self,
        limit: int = 50,
        offset: int = 0,
        sector: Optional[str] = None,
        symbol: Optional[str] = None,
        tone: Optional[str] = None,
    ) -> List[NewsArticle]:
        matched = []
        for art in self._articles:
            if symbol and symbol.upper() in [s.upper() for s in art.symbols]:
                matched.append(art)
            elif sector and sector.upper() in [sec.upper() for sec in art.sectors]:
                matched.append(art)
        return matched if matched else self._articles[:limit]


@pytest.mark.asyncio
async def test_personalized_feed_integration_ranking_and_filtering():
    """
    Integration test:
    - Portfolio has HDFCBANK (60% weight, Banking sector).
    - News corpus has:
      1. Fresh HDFCBANK earnings article (highly relevant).
      2. Irrelevant Pharma article for SUNPHARMA (irrelevant to portfolio holdings).
    - Result: HDFCBANK ranks first; irrelevant article is filtered or ranked at bottom.
    """
    now = datetime.now(timezone.utc)

    # 1. Seed portfolio holdings
    holdings = [
        HoldingWeightView(
            symbol="HDFCBANK",
            name="HDFC Bank Limited",
            sector="Financial Services",
            weight_pct=0.60,  # 60% holding weight
            quantity=100,
        )
    ]

    # 2. Seed candidate news articles
    relevant_article = NewsArticle(
        id=101,
        title="HDFC Bank reports 33% increase in net profit for Q3",
        summary="HDFC Bank shows robust net interest margin growth across retail operations.",
        source="LiveMint",
        url="https://livemint.com/news/hdfc-bank-q3",
        symbols=["HDFCBANK"],
        sectors=["Financial Services"],
        article_tone="positive",
        published_at=now,
        is_trending=True,
    )

    irrelevant_article = NewsArticle(
        id=102,
        title="Sun Pharma receives USFDA approval for generic dermatology drug",
        summary="Sun Pharmaceutical expands generic pipeline in North America.",
        source="Economic Times",
        url="https://economictimes.com/news/sun-pharma-usfda",
        symbols=["SUNPHARMA"],
        sectors=["Healthcare"],
        article_tone="neutral",
        published_at=now - timedelta(hours=20),
        is_trending=False,
    )

    portfolio_reader = InMemoryPortfolioHoldingsReader(holdings)
    news_repo = InMemoryNewsRepository([relevant_article, irrelevant_article])

    use_case = GetPersonalizedFeedUseCase(
        portfolio_holdings_reader=portfolio_reader,
        news_repository=news_repo,
    )

    feed = await use_case.execute(portfolio_id=55, limit=10)

    # Assertions
    assert feed.portfolio_id == 55
    assert len(feed.items) >= 1

    # Clearly relevant article must rank first
    top_item = feed.items[0]
    assert top_item.article.id == 101
    assert "HDFCBANK" in top_item.matched_holdings
    assert top_item.is_concentrated_holding_match is True
    assert top_item.relevance_score > 0.40

    # Irrelevant article either is not in feed or has score far below relevant item
    if len(feed.items) > 1:
        assert feed.items[1].relevance_score < top_item.relevance_score

    # Mandatory regulatory disclaimer is present and verified
    assert "Sentinews is not a SEBI-registered" in SEBI_MANDATORY_DISCLAIMER
