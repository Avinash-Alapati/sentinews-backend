"""
Unit tests for Auth Batch 2: Async threadpool password hashing and Redis user session caching.
"""

import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.api.v1.auth.dependencies import get_current_user, invalidate_user_cache
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import (
    async_get_password_hash,
    async_verify_password,
    create_access_token,
    get_password_hash,
    verify_password,
)


@pytest.mark.asyncio
async def test_async_password_hashing_and_verification():
    """Confirms async password hashing and verification run in threadpool without errors."""
    raw_password = "SecurePassword2026!"
    hashed = await async_get_password_hash(raw_password)

    assert hashed != raw_password
    assert await async_verify_password(raw_password, hashed) is True
    assert await async_verify_password("WrongPassword!", hashed) is False


class MockRedisClient:
    def __init__(self):
        self.store = {}
        self.ttls = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 60):
        self.store[key] = value
        self.ttls[key] = ex

    async def delete(self, *keys: str):
        for k in keys:
            self.store.pop(k, None)
            self.ttls.pop(k, None)


@pytest.mark.asyncio
async def test_get_current_user_redis_cache_hit_and_miss():
    """
    Validates:
    1. First call causes a DB lookup and sets Redis cache (60s TTL).
    2. Second call hits Redis cache directly and skips DB lookup.
    3. Invalidation clears Redis cache so third call queries DB again.
    """
    mock_redis = MockRedisClient()
    mock_user = User(
        id=777,
        email="cached.investor@sentinews.in",
        hashed_password="hashed_pwd",
        full_name="Cached Investor",
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(timezone.utc),
    )

    mock_repo = AsyncMock()
    mock_repo.get_by_id = AsyncMock(return_value=mock_user)

    secret_key = "test_secret_key"
    token = create_access_token(
        subject="777",
        secret_key=secret_key,
        extra_claims={"email": mock_user.email},
    )

    with patch("app.api.v1.auth.dependencies.market_cache._get_redis", return_value=mock_redis), \
         patch("app.api.v1.auth.dependencies.settings.SECRET_KEY", secret_key):

        # 1. First call: Cache miss -> calls DB repo
        user1 = await get_current_user(token=token, user_repo=mock_repo)
        assert user1.id == 777
        assert user1.email == "cached.investor@sentinews.in"
        assert mock_repo.get_by_id.call_count == 1

        # Verify Redis key is populated with 60s TTL
        cached_raw = await mock_redis.get("auth:user_session:777")
        assert cached_raw is not None
        assert mock_redis.ttls.get("auth:user_session:777") == 60

        # 2. Second call: Cache hit -> returns directly from Redis without calling DB repo
        user2 = await get_current_user(token=token, user_repo=mock_repo)
        assert user2.id == 777
        assert user2.email == "cached.investor@sentinews.in"
        assert mock_repo.get_by_id.call_count == 1  # Still 1, no second DB query!

        # 3. Invalidate cache
        await invalidate_user_cache(user_id=777, email="cached.investor@sentinews.in")
        assert await mock_redis.get("auth:user_session:777") is None

        # 4. Third call: Cache miss again -> queries DB
        user3 = await get_current_user(token=token, user_repo=mock_repo)
        assert user3.id == 777
        assert mock_repo.get_by_id.call_count == 2
