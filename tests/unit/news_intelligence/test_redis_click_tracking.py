"""
Unit tests for Redis-backed atomic click tracking, 72h trending retention,
race condition prevention, and multi-worker synchronization in LiveNewsService.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
import pytest

from app.modules.news_intelligence.domain.entities import NewsArticle
from app.modules.news_intelligence.domain.services.live_news_service import LiveNewsService


class MockAsyncRedis:
    """Mock Redis client simulating Redis server hash operations in memory."""

    def __init__(self):
        self.hashes: Dict[str, Dict[str, str]] = {}
        self._lock = asyncio.Lock()

    async def hincrby(self, name: str, key: str, amount: int = 1) -> int:
        async with self._lock:
            if name not in self.hashes:
                self.hashes[name] = {}
            current = int(self.hashes[name].get(str(key), "0"))
            new_val = current + amount
            self.hashes[name][str(key)] = str(new_val)
            return new_val

    async def hsetnx(self, name: str, key: str, value: str) -> int:
        async with self._lock:
            if name not in self.hashes:
                self.hashes[name] = {}
            if str(key) in self.hashes[name]:
                return 0  # Field already exists
            self.hashes[name][str(key)] = str(value)
            return 1  # Field was set

    async def hset(self, name: str, key: str, value: str) -> int:
        async with self._lock:
            if name not in self.hashes:
                self.hashes[name] = {}
            is_new = 0 if str(key) in self.hashes[name] else 1
            self.hashes[name][str(key)] = str(value)
            return is_new

    async def hget(self, name: str, key: str) -> Optional[str]:
        async with self._lock:
            if name not in self.hashes:
                return None
            return self.hashes[name].get(str(key))

    async def hgetall(self, name: str) -> Dict[str, str]:
        async with self._lock:
            if name not in self.hashes:
                return {}
            return dict(self.hashes[name])


@pytest.mark.asyncio
async def test_redis_atomic_click_tracking_under_threshold():
    """Clicks below trending threshold (5) increment counter and return is_trending=False."""
    mock_redis = MockAsyncRedis()
    service = LiveNewsService(redis_client=mock_redis, trending_click_threshold=5)

    article_id = 101
    for expected_count in range(1, 5):
        count, is_trending = await service.record_click(article_id)
        assert count == expected_count
        assert is_trending is False

    # Check state in Redis
    raw_count = await mock_redis.hget("news:clicks", str(article_id))
    assert raw_count == "4"
    trending_ts = await mock_redis.hget("news:trending_timestamps", str(article_id))
    assert trending_ts is None


@pytest.mark.asyncio
async def test_redis_atomic_trending_threshold_crossing():
    """5th click reaches threshold, sets is_trending=True and writes trending timestamp atomically."""
    mock_redis = MockAsyncRedis()
    service = LiveNewsService(redis_client=mock_redis, trending_click_threshold=5)
    article_id = 202

    # Click 1 to 4
    for _ in range(4):
        await service.record_click(article_id)

    # Click 5 (Threshold crossed)
    count, is_trending = await service.record_click(article_id)
    assert count == 5
    assert is_trending is True

    # Verify Redis atomic state
    raw_count = await mock_redis.hget("news:clicks", str(article_id))
    assert raw_count == "5"
    trending_ts = await mock_redis.hget("news:trending_timestamps", str(article_id))
    assert trending_ts is not None


@pytest.mark.asyncio
async def test_concurrent_clicks_race_condition_immunity():
    """
    Simulates 2 concurrent worker requests triggering the 5th click simultaneously.
    Both must see is_trending=True, and only one HSETNX succeeds, guaranteeing
    the 72h trending timestamp is atomic and identical across workers.
    """
    mock_redis = MockAsyncRedis()
    worker_1_service = LiveNewsService(redis_client=mock_redis, trending_click_threshold=5)
    worker_2_service = LiveNewsService(redis_client=mock_redis, trending_click_threshold=5)
    article_id = 303

    # Seed with 4 clicks
    await mock_redis.hset("news:clicks", str(article_id), "4")

    # Both workers record a click simultaneously
    results = await asyncio.gather(
        worker_1_service.record_click(article_id),
        worker_2_service.record_click(article_id),
    )

    counts = [r[0] for r in results]
    trendings = [r[1] for r in results]

    assert sorted(counts) == [5, 6]
    assert all(t is True for t in trendings)

    # Trending timestamp in Redis was set exactly once
    trending_ts_str = await mock_redis.hget("news:trending_timestamps", str(article_id))
    assert trending_ts_str is not None


@pytest.mark.asyncio
async def test_multi_worker_engagement_synchronization():
    """
    Simulates Worker 1 recording clicks and Worker 2 reading the feed.
    Worker 2's feed must immediately reflect the trending status and 72h boost from Redis.
    """
    mock_redis = MockAsyncRedis()
    worker_1 = LiveNewsService(redis_client=mock_redis, trending_click_threshold=5)
    worker_2 = LiveNewsService(redis_client=mock_redis, trending_click_threshold=5)

    # Pre-seed worker 2 with an article
    test_article = NewsArticle(
        id=404,
        title="RBI keeps repo rate unchanged",
        summary="Monetary Policy Committee maintains status quo.",
        url="https://livemint.com/rbi-rate",
        source="LiveMint",
        published_at=datetime.now(timezone.utc),
        click_count=0,
        is_trending=False,
    )
    await worker_1.save_article(test_article)
    await worker_2.save_article(test_article)

    # Worker 1 receives 5 clicks
    for _ in range(5):
        await worker_1.record_click(404)

    # Worker 2 retrieves trending articles
    trending_feed = await worker_2.get_trending_articles()
    assert any(a.id == 404 and a.is_trending is True and a.click_count == 5 for a in trending_feed)


@pytest.mark.asyncio
async def test_fallback_when_redis_offline():
    """When Redis is unavailable or raises errors, service falls back safely to in-memory tracking."""
    service = LiveNewsService(redis_client=None, trending_click_threshold=5)
    article_id = 505

    for expected_count in range(1, 6):
        count, is_trending = await service.record_click(article_id)
        assert count == expected_count
        if expected_count < 5:
            assert is_trending is False
        else:
            assert is_trending is True
