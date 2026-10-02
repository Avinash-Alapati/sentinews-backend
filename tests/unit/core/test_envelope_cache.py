"""
Unit tests for Dual-Tier Envelope Cache (HIT, STALE, MISS, Single-Flight & Leader Locks).
"""

import asyncio
import time
import pytest

from app.cache.market_cache import MarketCache


@pytest.fixture
def cache():
    return MarketCache()


@pytest.mark.asyncio
async def test_envelope_cache_hit(cache: MarketCache):
    payload = {"symbol": "TEST_STOCK_1", "price": 2950.0}
    await cache.set_envelope("mkt:test:STOCK1", payload, soft_ttl=10, hard_ttl=60, source="test")

    data, status, age = await cache.get_envelope("mkt:test:STOCK1")
    assert status == "HIT"
    assert data["symbol"] == "TEST_STOCK_1"
    assert data["price"] == 2950.0
    assert age >= 0.0 and age < 2.0


@pytest.mark.asyncio
async def test_envelope_cache_stale(cache: MarketCache):
    payload = {"symbol": "TEST_STOCK_2", "price": 3800.0}
    # Set soft TTL very short (0.05s) and hard TTL longer (10s)
    await cache.set_envelope("mkt:test:STOCK2", payload, soft_ttl=1, hard_ttl=10, source="test")

    # Override soft_ttl to 0.05s during check to simulate passage of time
    await asyncio.sleep(0.08)
    data, status, age = await cache.get_envelope("mkt:test:STOCK2", soft_ttl_override=0.05)

    assert status == "STALE"
    assert data["symbol"] == "TEST_STOCK_2"
    assert age >= 0.05


@pytest.mark.asyncio
async def test_envelope_cache_miss_on_empty(cache: MarketCache):
    data, status, age = await cache.get_envelope("mkt:test:NONEXISTENT_KEY")
    assert status == "MISS"
    assert data is None
    assert age == 0.0


@pytest.mark.asyncio
async def test_single_flight_lock(cache: MarketCache):
    key = "mkt:test:single_flight_lock"

    # 1st caller acquires lock
    acquired_1 = await cache.acquire_single_flight_lock(key, lock_ttl=2)
    assert acquired_1 is True

    # 2nd caller fails to acquire while lock is held
    acquired_2 = await cache.acquire_single_flight_lock(key, lock_ttl=2)
    assert acquired_2 is False

    # After releasing, new caller can acquire
    await cache.release_single_flight_lock(key)
    acquired_3 = await cache.acquire_single_flight_lock(key, lock_ttl=2)
    assert acquired_3 is True
    await cache.release_single_flight_lock(key)


@pytest.mark.asyncio
async def test_leader_lock_election_and_renewal(cache: MarketCache):
    leader_name = "test_fetcher_leader_custom"

    # 1st instance wins election
    is_leader = await cache.acquire_leader_lock(leader_name, lock_ttl=2)
    assert is_leader is True

    # 2nd instance loses election
    is_second_leader = await cache.acquire_leader_lock(leader_name, lock_ttl=2)
    assert is_second_leader is False

    # Leader renews lock
    renewed = await cache.renew_leader_lock(leader_name, lock_ttl=5)
    assert renewed is True
