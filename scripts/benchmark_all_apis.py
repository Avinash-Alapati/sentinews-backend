"""
Complete API Latency Benchmarking Harness for SentiNews Backend.

Profiles every endpoint across all modules:
- System & Probes
- Auth & User Session (Cold DB vs Warm Redis Cache)
- News Intelligence & Atomic Click Tracking
- Market Intelligence (Cold Miss vs Proactive Warm Cache)
- Market Reports (Cold DB vs 5-min Redis Cache)
- Watchlists
- Portfolio (Lightweight Metadata vs Full Eager, FIFO Overview, XIRR, Paginated Transactions)

Calculates Mean, P50 (Median), P95, P99, Min, Max, and RPS.
"""

import os
import sys

sys.path.insert(0, os.path.abspath("."))

import asyncio
from datetime import date, datetime, timedelta, timezone
import json
import statistics
import time
from typing import Any, Dict, List, Optional
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.v1.auth.dependencies import (
    get_current_active_user,
    get_user_repository,
)
from app.api.v1.market_reports.dependencies import (
    get_latest_report_query,
    get_market_report_repository,
)
from app.api.v1.portfolio.dependencies import (
    get_market_data_provider,
    get_personalized_feed_use_case,
    get_portfolio_repository,
)
from app.api.v1.watchlist.dependencies import get_watchlist_repository
from app.db.base import Base
from app.main import app
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import create_access_token, hash_password
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.modules.market_reports.application.queries import GetLatestReportQuery
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
from app.modules.watchlist.infrastructure.repositories.watchlist_repository import (
    SQLAlchemyWatchlistRepository,
)


class MockMarketDataProvider:
    async def get_current_price(self, symbol: str) -> float:
        prices = {"TCS": 3850.0, "INFY": 1620.0, "RELIANCE": 2980.0, "HDFCBANK": 1650.0}
        return prices.get(symbol.upper(), 500.0)

    async def get_quotes_batch(self, symbols: List[str]) -> Dict[str, float]:
        prices = {"TCS": 3850.0, "INFY": 1620.0, "RELIANCE": 2980.0, "HDFCBANK": 1650.0}
        return {s.upper(): prices.get(s.upper(), 500.0) for s in symbols}


class MockFeedUseCase:
    async def execute(self, portfolio_id: int, limit: int = 20, min_score=None):
        now = datetime.now(timezone.utc)
        items = []
        for i in range(min(limit, 10)):
            art = NewsArticle(
                id=i + 1,
                title=f"Market Intelligence Flash #{i+1} for Portfolio Holdings",
                summary=f"Strong quarterly operating performance reported across key asset lines #{i+1}.",
                url=f"https://sentinews.in/news/{i+1}",
                source="Financial Express",
                symbols=["TCS", "INFY", "RELIANCE"],
                sectors=["IT", "Energy"],
                article_tone="positive",
                market_context="market_hours",
                sentiment_score=0.75,
                sentiment_magnitude=1.1,
                is_trending=(i % 2 == 0),
                published_at=now,
            )
            items.append(
                PersonalizedFeedItem(
                    article=art,
                    relevance_score=0.88 - (i * 0.03),
                    matched_holdings=["TCS", "INFY"],
                    is_concentrated_holding_match=(i == 0),
                    notification_triggered=(i == 0),
                )
            )
        return PersonalizedFeed(
            portfolio_id=portfolio_id,
            generated_at=now,
            items=items,
            total_count=len(items),
        )


async def benchmark_endpoint(
    client: AsyncClient,
    method: str,
    url: str,
    name: str,
    category: str,
    json_data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    iterations: int = 50,
    warmup: int = 5,
) -> Dict[str, Any]:
    """Runs a repeatable benchmark against a single endpoint and computes statistical percentiles."""
    
    # Warmup
    for _ in range(warmup):
        if method.upper() == "GET":
            await client.get(url, headers=headers)
        elif method.upper() == "POST":
            await client.post(url, json=json_data, headers=headers)
        elif method.upper() == "DELETE":
            await client.delete(url, headers=headers)

    latencies = []
    status_codes = []

    t_start_total = time.perf_counter()
    for _ in range(iterations):
        t0 = time.perf_counter()
        if method.upper() == "GET":
            resp = await client.get(url, headers=headers)
        elif method.upper() == "POST":
            resp = await client.post(url, json=json_data, headers=headers)
        elif method.upper() == "DELETE":
            resp = await client.delete(url, headers=headers)
        t1 = time.perf_counter()

        latencies.append((t1 - t0) * 1000.0)  # ms
        status_codes.append(resp.status_code)

    t_end_total = time.perf_counter()
    total_elapsed = t_end_total - t_start_total
    rps = iterations / total_elapsed if total_elapsed > 0 else 0.0

    latencies.sort()
    p50 = statistics.median(latencies)
    mean = statistics.mean(latencies)
    min_lat = min(latencies)
    max_lat = max(latencies)
    p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) >= 20 else latencies[-1]
    p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) >= 100 else latencies[-1]
    stdev = statistics.stdev(latencies) if len(latencies) > 1 else 0.0

    return {
        "category": category,
        "name": name,
        "method": method.upper(),
        "url": url,
        "status_code": status_codes[0],
        "samples": iterations,
        "mean_ms": round(mean, 2),
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "min_ms": round(min_lat, 2),
        "max_ms": round(max_lat, 2),
        "stdev_ms": round(stdev, 2),
        "rps": round(rps, 1),
    }


async def run_full_suite():
    # Setup Async Engine and Session
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_port_repo():
        async with session_factory() as session:
            try:
                yield SQLAlchemyPortfolioRepository(session=session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_user_repo():
        async with session_factory() as session:
            try:
                yield SQLAlchemyUserRepository(session=session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_watchlist_repo():
        async with session_factory() as session:
            try:
                yield SQLAlchemyWatchlistRepository(session=session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_report_repo():
        async with session_factory() as session:
            try:
                yield SQLAlchemyMarketReportRepository(session=session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # Seed Database
    pwd_hash = await hash_password("traderPass@2026")
    async with session_factory() as session:
        user_repo = SQLAlchemyUserRepository(session=session)
        bench_user = await user_repo.create(
            User(
                email="benchmark.trader@sentinews.in",
                hashed_password=pwd_hash,
                full_name="Benchmark Trader",
                is_active=True,
                is_superuser=False,
            )
        )
        
        port_repo = SQLAlchemyPortfolioRepository(session=session)
        port = await port_repo.save_portfolio(
            Portfolio(user_id=bench_user.id, name="Active Tech Growth", cash_balance=150000.0)
        )

        # Seed 100 transactions
        base_time = datetime(2026, 1, 1, 9, 15, tzinfo=timezone.utc)
        for i in range(100):
            sym = ["TCS", "INFY", "RELIANCE", "HDFCBANK"][i % 4]
            sec = ["IT", "IT", "Energy", "Banking"][i % 4]
            t_type = TransactionType.BUY if i % 4 != 0 else TransactionType.SELL
            qty = 10.0 + (i % 20)
            price = 1000.0 + (i * 15)
            await port_repo.add_transaction(
                port.id,
                Transaction(
                    symbol=sym,
                    transaction_type=t_type,
                    quantity=qty,
                    price=price,
                    timestamp=base_time + timedelta(hours=i * 2),
                ),
            )
            # Add corresponding holding
            await port_repo.add_holding(
                port.id,
                Holding(symbol=sym, name=f"{sym} Corp", sector=sec, quantity=qty, avg_buy_price=price),
            )

        # Seed Published Market Reports
        report_repo = SQLAlchemyMarketReportRepository(session=session)
        for r_type in [ReportType.PRE_MARKET, ReportType.POST_MARKET, ReportType.GLOBAL_PRE_MARKET, ReportType.GLOBAL_POST_MARKET]:
            r = MarketReport(
                report_type=r_type,
                report_date=date(2026, 9, 26),
                status=ReportStatus.PUBLISHED,
                sections={
                    "headline": f"Daily Intelligence Brief: {r_type.value}",
                    "macro_indicators": {"gdp": "7.2%", "cpi": "4.8%"},
                    "indices": {"NIFTY_50": 25150.0, "SENSEX": 82200.0},
                    "sentiment_summary": "Neutral to bullish bias across banking and technology tranches.",
                },
                source_providers=["finnhub", "stocknews", "nse"],
                disclaimer=SEBI_MANDATORY_DISCLAIMER,
                is_partial=False,
            )
            await report_repo.save(r)

        # Seed Watchlist
        wl_repo = SQLAlchemyWatchlistRepository(session=session)
        wl = await wl_repo.create_watchlist(user_id=bench_user.id, name="Core Focus Watchlist")
        await wl_repo.add_item(wl.id, symbol="TCS", company_name="Tata Consultancy Services", sector="IT")
        await wl_repo.add_item(wl.id, symbol="INFY", company_name="Infosys Limited", sector="IT")
        await wl_repo.add_item(wl.id, symbol="RELIANCE", company_name="Reliance Industries", sector="Energy")

        await session.commit()
        user_id = bench_user.id
        port_id = port.id
        wl_id = wl.id

    # Create Auth Token
    token = create_access_token(data={"sub": str(user_id), "email": "benchmark.trader@sentinews.in"})
    auth_headers = {"Authorization": f"Bearer {token}"}

    # Dependency Overrides
    app.dependency_overrides[get_portfolio_repository] = override_get_port_repo
    app.dependency_overrides[get_user_repository] = override_get_user_repo
    app.dependency_overrides[get_watchlist_repository] = override_get_watchlist_repo
    app.dependency_overrides[get_market_report_repository] = override_get_report_repo
    app.dependency_overrides[get_market_data_provider] = lambda: MockMarketDataProvider()
    app.dependency_overrides[get_personalized_feed_use_case] = lambda: MockFeedUseCase()

    results = []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        print("Starting comprehensive API latency profiling across all modules...")

        # -------------------------------------------------------------
        # 1. System & Observability Probes
        # -------------------------------------------------------------
        results.append(await benchmark_endpoint(
            client, "GET", "/healthz", "Health Check Probe", "System & Probes", iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/readyz", "Readiness Probe", "System & Probes", iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/internal/metrics", "Prometheus Metrics Scrape", "System & Probes", iterations=30
        ))

        # -------------------------------------------------------------
        # 2. Auth Module
        # -------------------------------------------------------------
        reg_payload = {"email": "fresh.user@sentinews.in", "password": "SecurePassword123!", "full_name": "Fresh Trader"}
        results.append(await benchmark_endpoint(
            client, "POST", "/api/v1/auth/register", "User Registration (Bcrypt Hash Threadpool)", "Auth Module",
            json_data=reg_payload, iterations=20
        ))

        login_payload = {"email": "benchmark.trader@sentinews.in", "password": "traderPass@2026"}
        results.append(await benchmark_endpoint(
            client, "POST", "/api/v1/auth/login", "User Login (Bcrypt Verify Threadpool + JWT)", "Auth Module",
            json_data=login_payload, iterations=30
        ))

        # GET /auth/me (Warm Redis session cache)
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/auth/me", "Get Current User Profile (60s Redis Cache Hit)", "Auth Module",
            headers=auth_headers, iterations=50
        ))

        # -------------------------------------------------------------
        # 3. Market Intelligence Module
        # -------------------------------------------------------------
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/market/overview", "Market Overview (25s Background Warmed Cache)", "Market Intelligence",
            iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/market/indices", "Market Key Indices", "Market Intelligence",
            iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/market/movers", "Market Top Gainers & Losers", "Market Intelligence",
            iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/market/quotes/TCS", "Live Single Quote (TCS)", "Market Intelligence",
            iterations=50
        ))

        # -------------------------------------------------------------
        # 4. Market Reports Module
        # -------------------------------------------------------------
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/market-reports/pre-market/latest", "Latest Pre-Market Report (5-min Redis Cache)", "Market Reports",
            headers=auth_headers, iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/market-reports/post-market/latest", "Latest Post-Market Report (5-min Redis Cache)", "Market Reports",
            headers=auth_headers, iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/market-reports/global/pre-market/latest", "Latest Global Pre-Market Report (5-min Cache)", "Market Reports",
            headers=auth_headers, iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/market-reports/global/post-market/latest", "Latest Global Post-Market Report (5-min Cache)", "Market Reports",
            headers=auth_headers, iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/market-reports", "List Historical Market Reports", "Market Reports",
            headers=auth_headers, iterations=30
        ))

        # -------------------------------------------------------------
        # 5. News Intelligence Module
        # -------------------------------------------------------------
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/news/feed?limit=20", "Live Financial News Feed (SEBI Compliant)", "News Intelligence",
            headers=auth_headers, iterations=40
        ))
        results.append(await benchmark_endpoint(
            client, "POST", "/api/v1/news/1/click", "Article Engagement Click (Redis Atomic Counter)", "News Intelligence",
            headers=auth_headers, iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/news/trending", "Trending News Articles (72h Retention)", "News Intelligence",
            headers=auth_headers, iterations=40
        ))

        # -------------------------------------------------------------
        # 6. Watchlist Module
        # -------------------------------------------------------------
        results.append(await benchmark_endpoint(
            client, "GET", "/api/v1/watchlists", "List User Watchlists", "Watchlists",
            headers=auth_headers, iterations=40
        ))
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/watchlists/{wl_id}", "Get Watchlist Details with Quotes", "Watchlists",
            headers=auth_headers, iterations=40
        ))
        results.append(await benchmark_endpoint(
            client, "POST", f"/api/v1/watchlists/{wl_id}/items", "Add Symbol to Watchlist", "Watchlists",
            json_data={"symbol": "SBIN", "company_name": "State Bank of India", "sector": "Banking"},
            headers=auth_headers, iterations=20
        ))

        # -------------------------------------------------------------
        # 7. Portfolio Module
        # -------------------------------------------------------------
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/portfolio/{port_id}?metadata_only=true", "Get Portfolio Metadata (Optimized 1-Row Query)", "Portfolio",
            headers=auth_headers, iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/portfolio/{port_id}?metadata_only=false", "Get Portfolio Full Entity Graph (Eager Relations)", "Portfolio",
            headers=auth_headers, iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/portfolio/user/{user_id}", "Get Primary Portfolio by User", "Portfolio",
            headers=auth_headers, iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "POST", f"/api/v1/portfolio/{port_id}/transactions", "Record Transaction (FIFO Cost Basis)", "Portfolio",
            json_data={"symbol": "TCS", "transaction_type": "BUY", "quantity": 10.0, "price": 3850.0, "sector": "IT"},
            headers=auth_headers, iterations=25
        ))
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/portfolio/{port_id}/transactions?page=1&limit=50", "Get Transactions (SQL LIMIT/OFFSET 50/100 Rows)", "Portfolio",
            headers=auth_headers, iterations=50
        ))
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/portfolio/{port_id}/holdings", "Get Portfolio Active Holdings", "Portfolio",
            headers=auth_headers, iterations=40
        ))
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/portfolio/{port_id}/overview", "Get Portfolio Overview (FIFO P&L + Valuation)", "Portfolio",
            headers=auth_headers, iterations=40
        ))
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/portfolio/{port_id}/allocation", "Get Portfolio Sector & Asset Allocation", "Portfolio",
            headers=auth_headers, iterations=40
        ))
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/portfolio/{port_id}/performance", "Get Portfolio Performance & XIRR", "Portfolio",
            headers=auth_headers, iterations=40
        ))
        results.append(await benchmark_endpoint(
            client, "GET", f"/api/v1/portfolio/{port_id}/news-feed?limit=10", "Personalized Portfolio News Feed (SEBI Compliant)", "Portfolio",
            headers=auth_headers, iterations=40
        ))

    app.dependency_overrides.clear()
    await engine.dispose()

    # Output JSON and formatted report
    print("\n" + "=" * 105)
    print("SENTINEWS BACKEND COMPLETE API LATENCY AUDIT REPORT")
    print("=" * 105)
    print(f"{'Endpoint / Operation':<52} | {'Method':<6} | {'P50 (ms)':<8} | {'P95 (ms)':<8} | {'P99 (ms)':<8} | {'Mean (ms)':<9} | {'RPS':<6}")
    print("-" * 105)

    current_cat = None
    for r in results:
        if r["category"] != current_cat:
            current_cat = r["category"]
            print(f"\n[ {current_cat.upper()} ]")
        print(f"{r['name']:<52} | {r['method']:<6} | {r['p50_ms']:<8.2f} | {r['p95_ms']:<8.2f} | {r['p99_ms']:<8.2f} | {r['mean_ms']:<9.2f} | {r['rps']:<6.1f}")

    print("=" * 105)

    with open("docs/latency_audit_results.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    asyncio.run(run_full_suite())
