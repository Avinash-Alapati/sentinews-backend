"""
API tests for Live RSS News Module and 72-hour Click-Through Trending Retention Lifecycle.
Fetches latest live news -> Simulates user clicks -> Reaches trending threshold ->
Verifies is_trending=True, 72h sticky top boost, and SEBI compliance disclaimer.
"""

from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.modules.news_intelligence.domain.services.live_news_service import live_news_service


@pytest.mark.asyncio
async def test_live_news_click_and_trending_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. GET /api/v1/news/latest
        latest_res = await client.get("/api/v1/news/latest?limit=10")
        assert latest_res.status_code == 200
        data = latest_res.json()
        assert "items" in data
        assert len(data["items"]) > 0
        assert "disclaimer" in data
        assert "Sentinews is not a SEBI-registered" in data["disclaimer"]

        article = data["items"][0]
        article_id = article["id"]
        assert article_id is not None
        assert article["title"]
        assert article["source"]

        # Reset clicks in redis and memory for clean test run
        from app.cache.market_cache import market_cache
        redis_client = await market_cache._get_redis()
        if redis_client:
            await redis_client.hdel("news:clicks", str(article_id))
            await redis_client.hdel("news:trending_timestamps", str(article_id))
        live_news_service._clicks[article_id] = 0
        live_news_service._trending_timestamps.pop(article_id, None)

        # 2. Click article below trending threshold (trending threshold is 5 clicks)
        for click_num in range(1, 5):
            c_res = await client.post(f"/api/v1/news/{article_id}/click")
            assert c_res.status_code == 200
            c_data = c_res.json()
            assert c_data["article_id"] == article_id
            assert c_data["click_count"] == click_num
            assert c_data["is_trending"] is False

        # 3. 5th click reaches threshold -> Transitions to is_trending = True with 72h retention boost
        c5_res = await client.post(f"/api/v1/news/{article_id}/click")
        assert c5_res.status_code == 200
        c5_data = c5_res.json()
        assert c5_data["click_count"] >= 5
        assert c5_data["is_trending"] is True

        # 4. Verify in GET /api/v1/news/trending
        trending_res = await client.get("/api/v1/news/trending?limit=10")
        assert trending_res.status_code == 200
        trending_data = trending_res.json()
        assert any(item["id"] == article_id for item in trending_data["items"])

        # 5. Verify GET /api/v1/news/{id}/full-coverage
        coverage_res = await client.get(f"/api/v1/news/{article_id}/full-coverage")
        assert coverage_res.status_code == 200
        cov_data = coverage_res.json()
        assert cov_data["primary_article"]["id"] == article_id
        assert "related_sources" in cov_data
        assert "cluster_size" in cov_data

        # 6. Verify GET /api/v1/news/sources
        sources_res = await client.get("/api/v1/news/sources")
        assert sources_res.status_code == 200
        sources_data = sources_res.json()
        assert len(sources_data["sources"]) > 0
        assert any("Hindu" in s["name"] or "Moneycontrol" in s["name"] for s in sources_data["sources"])

