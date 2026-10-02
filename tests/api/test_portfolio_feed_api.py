"""
Integration test for GET /api/v1/portfolio/{id}/news-feed endpoint.
"""

from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.auth.dependencies import get_current_active_user
from app.api.v1.portfolio.dependencies import (
    get_personalized_feed_use_case,
    get_portfolio_repository,
)
from app.main import app
from app.modules.auth.domain.entities import User
from app.modules.news_intelligence.domain.entities import (
    NewsArticle,
    PersonalizedFeed,
    PersonalizedFeedItem,
)
from app.modules.portfolio.domain.entities import Portfolio


class MockPortfolioRepo:
    async def get_portfolio(self, portfolio_id: int):
        if portfolio_id == 42:
            return Portfolio(id=42, user_id=1, name="Mock Portfolio")
        return None


class MockUseCase:
    async def execute(self, portfolio_id: int, limit: int = 20, min_score=None):
        now = datetime.now(timezone.utc)
        article = NewsArticle(
            id=1,
            title="TCS announces Q3 earnings with double-digit growth",
            summary="Tata Consultancy Services reported strong results.",
            url="https://example.com/news/1",
            source="Moneycontrol",
            symbols=["TCS"],
            sectors=["IT"],
            article_tone="positive",
            market_context="market_hours",
            sentiment_score=0.8,
            sentiment_magnitude=1.0,
            is_trending=True,
            published_at=now,
        )
        item = PersonalizedFeedItem(
            article=article,
            relevance_score=0.85,
            matched_holdings=["TCS"],
            is_concentrated_holding_match=True,
            notification_triggered=True,
        )
        return PersonalizedFeed(
            portfolio_id=portfolio_id,
            generated_at=now,
            items=[item],
            total_count=1,
        )


@pytest.mark.asyncio
async def test_get_portfolio_news_feed_endpoint():
    # Override dependencies
    mock_user = User(id=1, email="test@sentinews.in", hashed_password="pwd", is_active=True)
    app.dependency_overrides[get_current_active_user] = lambda: mock_user
    app.dependency_overrides[get_portfolio_repository] = lambda: MockPortfolioRepo()
    app.dependency_overrides[get_personalized_feed_use_case] = lambda: MockUseCase()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/portfolio/42/news-feed?limit=10")
            assert response.status_code == 200
            data = response.json()

            assert data["portfolio_id"] == 42
            assert data["total_count"] == 1
            assert len(data["items"]) == 1
            assert "disclaimer" in data
            assert "Sentinews is not a SEBI-registered" in data["disclaimer"]

            first_item = data["items"][0]
            assert first_item["relevance_score"] == 0.85
            assert first_item["matched_holdings"] == ["TCS"]
            assert first_item["is_concentrated_holding_match"] is True
            assert first_item["article"]["title"] == "TCS announces Q3 earnings with double-digit growth"
            assert first_item["article"]["is_trending"] is True
            assert first_item["article"]["article_tone"] == "positive"
            assert "disclaimer" in first_item
    finally:
        app.dependency_overrides.clear()
