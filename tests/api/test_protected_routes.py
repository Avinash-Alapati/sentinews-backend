"""
Tests verifying that Portfolio and Market Reports routes are strictly protected:
1. Unauthenticated requests to /api/v1/portfolio/* return 401 Unauthorized.
2. Unauthenticated requests to /api/v1/market-reports/* return 401 Unauthorized.
3. Accessing another user's portfolio returns 403 Forbidden.
4. Authenticated users can successfully access their own portfolios and market reports.
"""

from datetime import date
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.auth.dependencies import get_current_active_user
from app.api.v1.market_reports.dependencies import get_latest_report_query
from app.api.v1.portfolio.dependencies import get_portfolio_repository
from app.main import app
from app.modules.auth.domain.entities import User
from app.modules.market_reports.domain.entities import MarketReport, SEBI_MANDATORY_DISCLAIMER
from app.modules.market_reports.domain.enums import ReportStatus, ReportType
from app.modules.portfolio.domain.entities import Portfolio


class MockPortfolioRepo:
    def __init__(self):
        self.portfolios = {
            1: Portfolio(id=1, user_id=10, name="User 10's Portfolio"),
            2: Portfolio(id=2, user_id=20, name="User 20's Portfolio"),
        }

    async def get_portfolio(self, portfolio_id: int):
        return self.portfolios.get(portfolio_id)

    async def get_portfolio_by_user(self, user_id: int):
        for p in self.portfolios.values():
            if p.user_id == user_id:
                return p
        return None

    async def save_portfolio(self, portfolio: Portfolio):
        portfolio.id = 3
        return portfolio

    async def delete_portfolio(self, portfolio_id: int):
        if portfolio_id in self.portfolios:
            del self.portfolios[portfolio_id]
            return True
        return False


class MockLatestReportQuery:
    async def execute(self, report_type: ReportType):
        return MarketReport(
            id=101,
            report_type=report_type,
            report_date=date(2026, 9, 26),
            status=ReportStatus.PUBLISHED,
            sections={"market_status": "open"},
            disclaimer=SEBI_MANDATORY_DISCLAIMER,
            source_providers=["finnhub", "stocknews"],
            is_partial=False,
        )


@pytest.mark.asyncio
async def test_unauthenticated_portfolio_routes_return_401():
    """Unauthenticated calls without token must receive 401 Unauthorized."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create portfolio
        r1 = await client.post("/api/v1/portfolio", json={"name": "My Tech Portfolio"})
        assert r1.status_code == 401
        assert "detail" in r1.json()

        # Get portfolio by ID
        r2 = await client.get("/api/v1/portfolio/1")
        assert r2.status_code == 401

        # Get portfolio by user
        r3 = await client.get("/api/v1/portfolio/user/10")
        assert r3.status_code == 401

        # Delete portfolio
        r4 = await client.delete("/api/v1/portfolio/1")
        assert r4.status_code == 401

        # Get holdings
        r5 = await client.get("/api/v1/portfolio/1/holdings")
        assert r5.status_code == 401

        # Get overview
        r6 = await client.get("/api/v1/portfolio/1/overview")
        assert r6.status_code == 401

        # Get allocation
        r7 = await client.get("/api/v1/portfolio/1/allocation")
        assert r7.status_code == 401

        # Get performance
        r8 = await client.get("/api/v1/portfolio/1/performance")
        assert r8.status_code == 401

        # Get news feed
        r9 = await client.get("/api/v1/portfolio/1/news-feed")
        assert r9.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_market_reports_routes_return_401():
    """Unauthenticated calls to market reports must receive 401 Unauthorized."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Latest pre-market
        r1 = await client.get("/api/v1/market-reports/pre-market/latest")
        assert r1.status_code == 401

        # Latest post-market
        r2 = await client.get("/api/v1/market-reports/post-market/latest")
        assert r2.status_code == 401

        # Latest global pre-market
        r3 = await client.get("/api/v1/market-reports/global/pre-market/latest")
        assert r3.status_code == 401

        # Latest global post-market
        r4 = await client.get("/api/v1/market-reports/global/post-market/latest")
        assert r4.status_code == 401

        # Get by ID
        r5 = await client.get("/api/v1/market-reports/101")
        assert r5.status_code == 401

        # List reports
        r6 = await client.get("/api/v1/market-reports")
        assert r6.status_code == 401


@pytest.mark.asyncio
async def test_portfolio_cross_user_isolation_returns_404():
    """Authenticated user attempting to access another user's portfolio receives 404 Not Found (IDOR protection)."""
    user_10 = User(id=10, email="user10@sentinews.in", hashed_password="pwd", is_active=True, is_superuser=False)
    app.dependency_overrides[get_current_active_user] = lambda: user_10
    app.dependency_overrides[get_portfolio_repository] = lambda: MockPortfolioRepo()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # User 10 accessing User 10's own portfolio (ID 1) -> 200 OK
            r_own = await client.get("/api/v1/portfolio/1")
            assert r_own.status_code == 200
            assert r_own.json()["user_id"] == 10

            # User 10 accessing User 20's portfolio (ID 2) -> 404 Not Found
            r_other = await client.get("/api/v1/portfolio/2")
            assert r_other.status_code == 404

            # User 10 attempting to get User 20's portfolio by user_id -> 404 Not Found
            r_user_other = await client.get("/api/v1/portfolio/user/20")
            assert r_user_other.status_code == 404

            # User 10 attempting to delete User 20's portfolio -> 404 Not Found
            r_del_other = await client.delete("/api/v1/portfolio/2")
            assert r_del_other.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_authenticated_market_reports_success():
    """Authenticated user successfully accesses market reports endpoints."""
    user = User(id=10, email="user@sentinews.in", hashed_password="pwd", is_active=True)
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_latest_report_query] = lambda: MockLatestReportQuery()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/market-reports/pre-market/latest")
            assert r.status_code == 200
            data = r.json()
            assert data["id"] == 101
            assert data["status"] == "PUBLISHED"
            assert "disclaimer" in data
            assert "Sentinews is not a SEBI-registered" in data["disclaimer"]
    finally:
        app.dependency_overrides.clear()
