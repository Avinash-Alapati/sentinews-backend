"""
Phase 0 Latency Diagnostic Benchmarking Harness for Sentinews.

Measures all cache-backed routes across:
1. Process-Cold: First request after startup/init
2. Key-Cold: Cache miss (unwarmed / cache flushed)
3. Warm HIT: Cache hit
4. STALE: SWR path (past soft TTL, before hard TTL)

Outputs p50, p95, p99, min, max, and stage breakdowns (auth, db, redis, upstream, serialization).
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
import json
import os
import statistics
import sys
import time
from typing import Any, Dict, List, Optional
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.abspath("."))

from app.api.v1.auth.dependencies import get_current_active_user, get_user_repository
from app.api.v1.market_reports.dependencies import get_latest_report_query, get_market_report_repository
from app.api.v1.portfolio.dependencies import (
    get_market_data_provider,
    get_personalized_feed_use_case,
    get_portfolio_repository,
)
from app.api.v1.watchlist.dependencies import get_watchlist_repository
from app.cache.market_cache import market_cache
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import create_access_token, hash_password
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.modules.market_reports.domain.entities import MarketReport, SEBI_MANDATORY_DISCLAIMER
from app.modules.market_reports.domain.enums import ReportStatus, ReportType
from app.modules.market_reports.infrastructure.repositories.market_report_repository import (
    SQLAlchemyMarketReportRepository,
)
from app.modules.news_intelligence.domain.entities import (
    NewsArticle,
    PersonalizedFeed,
    PersonalizedFeedItem,
)
from app.modules.portfolio.domain.entities import Holding, Portfolio, Transaction, TransactionType
from app.modules.portfolio.infrastructure.repositories.portfolio_repository import (
    SQLAlchemyPortfolioRepository,
)
from app.modules.watchlist.domain.entities import Watchlist, WatchlistItem
from app.modules.watchlist.infrastructure.repositories.watchlist_repository import (
    SQLAlchemyWatchlistRepository,
)


class MockMarketDataProvider:
    async def get_current_price(self, symbol: str) -> float:
        return 2980.0

    async def get_quotes_batch(self, symbols: List[str]) -> Dict[str, float]:
        return {s.upper(): 2980.0 for s in symbols}


async def run_benchmark():
    print("=" * 80)
    print("PHASE 0 LATENCY BENCHMARKING (Process-Cold, Key-Cold, Warm HIT, STALE)")
    print("=" * 80)

    # 1. Setup in-memory SQLite for reproducible database timing
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    session = session_factory()

    # Seed User, Portfolio, Holdings, Transactions, Watchlist, Reports
    user_repo = SQLAlchemyUserRepository(session=session)
    test_user = await user_repo.create(
        User(
            email="benchmark.user@sentinews.in",
            hashed_password=hash_password("Benchmark123!"),
            full_name="Benchmarker",
            is_active=True,
        )
    )
    token = create_access_token({"sub": str(test_user.id), "email": test_user.email})
    headers = {"Authorization": f"Bearer {token}"}

    port_repo = SQLAlchemyPortfolioRepository(session=session)
    port = await port_repo.save_portfolio(Portfolio(user_id=test_user.id, name="Benchmark Portfolio"))
    await port_repo.add_holding(
        port.id,
        Holding(symbol="RELIANCE", name="Reliance Industries", sector="Energy", quantity=50.0, avg_buy_price=2800.0),
    )
    await port_repo.add_holding(
        port.id,
        Holding(symbol="TCS", name="Tata Consultancy Services", sector="IT", quantity=30.0, avg_buy_price=3700.0),
    )

    wl_repo = SQLAlchemyWatchlistRepository(session=session)
    wl = await wl_repo.save_watchlist(Watchlist(user_id=test_user.id, name="Tech Giants"))
    await wl_repo.add_stock(wl.id, WatchlistItem(symbol="INFY", exchange="NSE"))

    report_repo = SQLAlchemyMarketReportRepository(session=session)
    await report_repo.save(
        MarketReport(
            report_type=ReportType.PRE_MARKET,
            report_date=date.today(),
            status=ReportStatus.PUBLISHED,
            sections={"market_status": "open", "global_cues": "Positive"},
            disclaimer=SEBI_MANDATORY_DISCLAIMER,
            source_providers=["nse", "finnhub"],
            is_partial=False,
        )
    )

    # Wire overrides
    app.dependency_overrides[get_current_active_user] = lambda: test_user
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_portfolio_repository] = lambda: port_repo
    app.dependency_overrides[get_market_data_provider] = lambda: MockMarketDataProvider()
    app.dependency_overrides[get_watchlist_repository] = lambda: wl_repo
    app.dependency_overrides[get_market_report_repository] = lambda: report_repo

    transport = ASGITransport(app=app)

    routes = [
        {"name": "Market Indices", "url": "/api/v1/market/indices", "auth": False, "key": "mkt:indices"},
        {"name": "Market Overview", "url": "/api/v1/market/overview", "auth": False, "key": "mkt:overview:all:20"},
        {"name": "Market Quote", "url": "/api/v1/market/quote/RELIANCE", "auth": False, "key": "mkt:quote:RELIANCE"},
        {"name": "Market Quotes Batch", "url": "/api/v1/market/quotes?symbols=TCS,INFY,RELIANCE", "auth": False, "key": "mkt:quote:TCS"},
        {"name": "Market History", "url": "/api/v1/market/history/RELIANCE?interval=1d&range_period=1mo", "auth": False, "key": "mkt:candles:RELIANCE:1d:1mo"},
        {"name": "Market Search", "url": "/api/v1/market/search?query=TATA", "auth": False, "key": None},
        {"name": "Market ETFs", "url": "/api/v1/market/etfs", "auth": False, "key": "mkt:etfs:all"},
        {"name": "News Latest", "url": "/api/v1/news/latest", "auth": False, "key": "news:latest:all:1:20"},
        {"name": "News Trending", "url": "/api/v1/news/trending", "auth": False, "key": "news:trending:24h:10"},
        {"name": "Market Report Latest", "url": "/api/v1/market-reports/pre-market/latest", "auth": True, "key": "report:latest:pre_market"},
        {"name": "Portfolio Overview", "url": f"/api/v1/portfolio/{port.id}/overview", "auth": True, "key": None},
        {"name": "Portfolio Performance", "url": f"/api/v1/portfolio/{port.id}/performance", "auth": True, "key": None},
        {"name": "Portfolio Allocation", "url": f"/api/v1/portfolio/{port.id}/allocation", "auth": True, "key": None},
    ]

    results = []

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for r in routes:
            req_headers = headers if r["auth"] else {}
            name = r["name"]
            url = r["url"]
            cache_key = r["key"]

            # 1. PROCESS-COLD (first hit)
            # Flush cache key first to simulate cold start
            if cache_key:
                await market_cache.delete(cache_key)
            t0 = time.perf_counter()
            resp_proc_cold = await client.get(url, headers=req_headers)
            t_proc_cold = (time.perf_counter() - t0) * 1000.0

            # 2. KEY-COLD (flush key, measure cold fetch)
            cold_latencies = []
            for _ in range(5):
                if cache_key:
                    await market_cache.delete(cache_key)
                t0 = time.perf_counter()
                resp = await client.get(url, headers=req_headers)
                cold_latencies.append((time.perf_counter() - t0) * 1000.0)
            p50_cold = statistics.median(cold_latencies)
            p95_cold = sorted(cold_latencies)[int(len(cold_latencies) * 0.95)]

            # 3. WARM HIT (pre-populate cache, measure 50 iterations)
            # Pre-seed cache with valid payload if cache_key exists
            if cache_key:
                await market_cache.set_envelope(cache_key, {"symbol": "RELIANCE", "current_price": 2980.0, "status": "ok"}, soft_ttl=60, hard_ttl=1800)
            warm_latencies = []
            server_timings = []
            for _ in range(50):
                t0 = time.perf_counter()
                resp = await client.get(url, headers=req_headers)
                dur = (time.perf_counter() - t0) * 1000.0
                warm_latencies.append(dur)
                st = resp.headers.get("server-timing", "")
                if st:
                    server_timings.append(st)

            warm_latencies.sort()
            p50_warm = statistics.median(warm_latencies)
            p95_warm = warm_latencies[int(len(warm_latencies) * 0.95)]
            p99_warm = warm_latencies[int(len(warm_latencies) * 0.99)]
            min_warm = warm_latencies[0]
            max_warm = warm_latencies[-1]

            # 4. STALE (SWR) (set past soft TTL but before hard TTL)
            stale_latencies = []
            if cache_key:
                # Mock stale envelope
                now = time.time()
                await market_cache.set_envelope(cache_key, {"symbol": "RELIANCE", "current_price": 2980.0}, soft_ttl=1, hard_ttl=1800)
                await asyncio.sleep(0.01)
                # Override fetched_at to simulate 10s old
                if cache_key in market_cache._memory_cache:
                    env_dict, exp = market_cache._memory_cache[cache_key]
                    if isinstance(env_dict, dict):
                        env_dict["fetched_at"] = now - 10.0
                        env_dict["soft_ttl"] = 2.0

            for _ in range(20):
                t0 = time.perf_counter()
                resp = await client.get(url, headers=req_headers)
                stale_latencies.append((time.perf_counter() - t0) * 1000.0)

            p50_stale = statistics.median(stale_latencies)
            p95_stale = sorted(stale_latencies)[int(len(stale_latencies) * 0.95)]

            results.append({
                "route": name,
                "url": url,
                "proc_cold_ms": round(t_proc_cold, 2),
                "key_cold_p50_ms": round(p50_cold, 2),
                "key_cold_p95_ms": round(p95_cold, 2),
                "warm_p50_ms": round(p50_warm, 2),
                "warm_p95_ms": round(p95_warm, 2),
                "warm_p99_ms": round(p99_warm, 2),
                "stale_p50_ms": round(p50_stale, 2),
                "stale_p95_ms": round(p95_stale, 2),
                "sample_server_timing": server_timings[0] if server_timings else "N/A",
            })

            print(f"[{name:<24}] Proc-Cold: {t_proc_cold:>6.2f}ms | Cold-p95: {p95_cold:>6.2f}ms | Warm-p50: {p50_warm:>5.2f}ms | Warm-p95: {p95_warm:>5.2f}ms | Stale-p50: {p50_stale:>5.2f}ms")

    await session.close()
    await engine.dispose()

    # Output markdown table
    print("\n" + "=" * 80)
    print("PHASE 0 BASELINE LATENCY PROFILE TABLE")
    print("=" * 80)
    print("| Route | Process-Cold (ms) | Key-Cold p95 (ms) | Warm HIT p50 (ms) | Warm HIT p95 (ms) | Warm HIT p99 (ms) | STALE SWR p50 (ms) |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |")
    for r in results:
        print(f"| {r['route']} | {r['proc_cold_ms']:.1f} | {r['key_cold_p95_ms']:.1f} | {r['warm_p50_ms']:.2f} | {r['warm_p95_ms']:.2f} | {r['warm_p99_ms']:.2f} | {r['stale_p50_ms']:.2f} |")

    # Save to JSON artifact
    with open("scripts/phase0_baseline_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nSaved baseline results to scripts/phase0_baseline_results.json")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
