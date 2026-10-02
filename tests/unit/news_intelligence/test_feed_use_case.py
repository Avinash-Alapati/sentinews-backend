"""
Unit tests for GetPersonalizedFeedUseCase.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union
from uuid import UUID
import pytest

from app.modules.news_intelligence.application.ports import (
    HoldingWeightView,
    NewsRepository,
    NotificationDispatcher,
    PortfolioHoldingsReader,
)
from app.modules.news_intelligence.application.use_cases.get_personalized_feed import (
    GetPersonalizedFeedUseCase,
)
from app.modules.news_intelligence.domain.entities import NewsArticle


class MockPortfolioHoldingsReader(PortfolioHoldingsReader):
    def __init__(self, holdings: List[HoldingWeightView]):
        self.holdings = holdings

    async def get_holdings_with_weights(
        self, portfolio_id: Union[UUID, int, str]
    ) -> List[HoldingWeightView]:
        return self.holdings


class MockNewsRepository(NewsRepository):
    def __init__(self, articles: List[NewsArticle]):
        self.articles = articles

    async def get_active_articles(self, limit: int = 50) -> List[NewsArticle]:
        return self.articles[:limit]

    async def get_latest_articles(
        self,
        limit: int = 50,
        offset: int = 0,
        sector: Optional[str] = None,
        symbol: Optional[str] = None,
        tone: Optional[str] = None,
    ) -> List[NewsArticle]:
        results = []
        for art in self.articles:
            if symbol and symbol.upper() in [s.upper() for s in art.symbols]:
                results.append(art)
            elif sector and sector.upper() in [sec.upper() for sec in art.sectors]:
                results.append(art)
        return results if results else self.articles[:limit]


class MockNotificationDispatcher(NotificationDispatcher):
    def __init__(self):
        self.dispatched_alerts = []

    def send_relevance_alert(
        self,
        portfolio_id: int,
        user_id: int,
        article_id: int,
        symbol: str,
        relevance_score: float,
        article_title: str = "",
    ) -> None:
        self.dispatched_alerts.append({
            "portfolio_id": portfolio_id,
            "user_id": user_id,
            "article_id": article_id,
            "symbol": symbol,
            "relevance_score": relevance_score,
            "article_title": article_title,
        })


@pytest.mark.asyncio
async def test_get_personalized_feed_ranking_and_clustering():
    now = datetime.now(timezone.utc)

    # Portfolio with 2 holdings: RELIANCE (70% - concentrated), INFY (30%)
    holdings = [
        HoldingWeightView(symbol="RELIANCE", name="Reliance Industries", sector="Energy", quantity=10, weight_pct=0.70),
        HoldingWeightView(symbol="INFY", name="Infosys", sector="IT", quantity=10, weight_pct=0.30),
    ]

    # Two articles: one fresh for RELIANCE (trending, positive), one 4h old for INFY
    articles = [
        NewsArticle(
            id=1,
            title="Reliance announces massive green hydrogen expansion in Gujarat",
            summary="Reliance Industries commits capital to new energy projects.",
            symbols=["RELIANCE"],
            sectors=["Energy"],
            article_tone="positive",
            published_at=now,
            sentiment_magnitude=1.2,
            is_trending=True,
        ),
        NewsArticle(
            id=2,
            title="Infosys signs multi-year digital transformation deal in Europe",
            summary="Infosys expands cloud enterprise partnerships.",
            symbols=["INFY"],
            sectors=["IT"],
            article_tone="neutral",
            published_at=now - timedelta(hours=4),
            sentiment_magnitude=0.8,
            is_trending=False,
        ),
    ]

    mock_dispatcher = MockNotificationDispatcher()
    use_case = GetPersonalizedFeedUseCase(
        portfolio_holdings_reader=MockPortfolioHoldingsReader(holdings),
        news_repository=MockNewsRepository(articles),
        notification_dispatcher=mock_dispatcher,
        notification_threshold=0.70,
        concentration_threshold=0.15,
    )

    feed = await use_case.execute(portfolio_id=101, limit=10)

    assert feed.portfolio_id == 101
    assert len(feed.items) == 2

    # Top item should be Reliance article due to 70% weight, positive tone, trending boost, and 0h decay
    top_item = feed.items[0]
    assert top_item.article.id == 1
    assert "RELIANCE" in top_item.matched_holdings
    assert top_item.is_concentrated_holding_match is True
    assert top_item.relevance_score > feed.items[1].relevance_score

    # Check that notification dispatcher was invoked for the concentrated high-relevance article
    assert len(mock_dispatcher.dispatched_alerts) >= 1
    alert = mock_dispatcher.dispatched_alerts[0]
    assert alert["symbol"] == "RELIANCE"
    assert alert["portfolio_id"] == 101
    assert alert["relevance_score"] >= 0.70


@pytest.mark.asyncio
async def test_get_personalized_feed_full_coverage_clustering():
    """Near duplicate articles from different publishers covering the same event should be clustered."""
    now = datetime.now(timezone.utc)

    holdings = [
        HoldingWeightView(symbol="TCS", name="Tata Consultancy Services", sector="IT", quantity=5, weight_pct=0.40),
    ]

    # Two duplicate stories covering same event from different sources
    articles = [
        NewsArticle(
            id=10,
            title="TCS posts robust Q3 net profit of Rs 12000 crore",
            summary="Tata Consultancy Services reports quarterly numbers.",
            source="Moneycontrol",
            symbols=["TCS"],
            sectors=["IT"],
            published_at=now,
        ),
        NewsArticle(
            id=11,
            title="TCS reports strong Q3 net profit at Rs 12000 crore",
            summary="Quarterly financial results released by TCS.",
            source="Economic Times",
            symbols=["TCS"],
            sectors=["IT"],
            published_at=now,
        ),
    ]

    use_case = GetPersonalizedFeedUseCase(
        portfolio_holdings_reader=MockPortfolioHoldingsReader(holdings),
        news_repository=MockNewsRepository(articles),
    )

    feed = await use_case.execute(portfolio_id=202)
    # Both articles cluster together under one primary article
    assert feed.total_count == 1
    assert feed.items[0].article.id == 10


@pytest.mark.asyncio
async def test_get_personalized_feed_empty_portfolio():
    now = datetime.now(timezone.utc)
    articles = [
        NewsArticle(id=1, title="Nifty hits record high", symbols=["^NSEI"], published_at=now),
    ]

    use_case = GetPersonalizedFeedUseCase(
        portfolio_holdings_reader=MockPortfolioHoldingsReader([]),
        news_repository=MockNewsRepository(articles),
    )

    feed = await use_case.execute(portfolio_id=303)
    assert feed.portfolio_id == 303
    assert len(feed.items) == 1
    assert feed.items[0].article.title == "Nifty hits record high"
