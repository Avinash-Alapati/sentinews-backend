"""
Integration tests for Market Reports Module: Endpoints, Repositories, APM, and SEBI Compliance.
"""

from datetime import date, datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.auth.dependencies import get_current_active_user
from app.api.v1.market_reports.dependencies import (
    get_adr_adapter,
    get_commodities_adapter,
    get_corporate_announcements_adapter,
    get_currency_adapter,
    get_finnhub_client,
    get_global_indices_adapter,
    get_market_report_news_adapter,
    get_market_report_repository,
    get_nse_market_data_adapter,
    get_stocknews_client,
)
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.infrastructure.observability.http_tracer import create_traced_async_client
from app.main import app
from app.modules.auth.domain.entities import User
from app.modules.market_reports.application.ports import (
    CommoditiesPort,
    CorporateAnnouncementsPort,
    CurrencyPort,
    FinnhubMarketClientPort,
    GlobalIndicesPort,
    IndianADRPort,
    MarketReportNewsPort,
    NSEMarketDataPort,
    StockNewsClientPort,
)
from app.modules.market_reports.domain.entities import (
    ADRItem,
    CommodityItem,
    CorporateEventItem,
    CurrencyPairItem,
    FIIDIIData,
    HeadlineItem,
    IndexPerformanceItem,
    IndexPoint,
    SEBI_MANDATORY_DISCLAIMER,
    SectorPerformanceItem,
    StockInNewsItem,
    TopMoverItem,
)
from app.modules.market_reports.infrastructure.repositories.market_report_repository import (
    SQLAlchemyMarketReportRepository,
)


class MockIntegrationFinnhub(FinnhubMarketClientPort):
    async def get_market_status(self, exchange: str = "IN"):
        return {"isOpen": True, "holiday": None}

    async def get_global_indices(self):
        return [
            {"symbol": "^DJI", "name": "Dow Jones", "region": "us", "last_price": 42100.0, "change": 120.0, "change_percent": 0.28},
            {"symbol": "^GSPC", "name": "S&P 500", "region": "us", "last_price": 5720.0, "change": 18.0, "change_percent": 0.31},
            {"symbol": "GIFT_NIFTY", "name": "GIFT Nifty", "region": "india_gift", "last_price": 24950.0, "change": 45.0, "change_percent": 0.18},
        ]

    async def get_economic_calendar(self, from_date, to_date):
        return [{"event": "US FOMC Rate Decision", "country": "US", "impact": "high"}]

    async def get_market_news(self, category: str = "general"):
        return []

    async def get_domestic_indices(self):
        return [
            {"symbol": "NIFTY 50", "name": "Nifty 50", "price": 24850.0, "change": 125.0, "change_percent": 0.51},
        ]

    async def get_top_gainers_losers(self):
        return {
            "gainers": [{"symbol": "INFY", "name": "Infosys Ltd", "price": 1920.0, "change_percent": 2.1}],
            "losers": [{"symbol": "TATASTEEL", "name": "Tata Steel", "price": 155.0, "change_percent": -1.2}],
        }

    async def get_extended_global_indices(self):
        return [
            {"symbol": "^DJI", "name": "Dow Jones", "region": "us", "last_price": 42100.0, "change": 120.0, "change_percent": 0.28},
            {"symbol": "^GSPC", "name": "S&P 500", "region": "us", "last_price": 5720.0, "change": 18.0, "change_percent": 0.31},
            {"symbol": "^FTSE", "name": "FTSE 100", "region": "europe", "last_price": 8200.0, "change": -10.0, "change_percent": -0.12},
        ]

    async def get_commodities_and_fx(self):
        return [
            {"symbol": "CL.F", "name": "Brent Crude", "category": "commodity", "last_price": 74.5, "change": 0.8, "change_percent": 1.08},
            {"symbol": "GC.F", "name": "Gold", "category": "commodity", "last_price": 2650.0, "change": 12.0, "change_percent": 0.45},
        ]

    async def get_global_movers(self):
        return {
            "gainers": [{"symbol": "AAPL", "name": "Apple", "price": 225.0, "change_percent": 1.5}],
            "losers": [{"symbol": "MSFT", "name": "Microsoft", "price": 430.0, "change_percent": -0.8}],
        }


class MockIntegrationStockNews(StockNewsClientPort):
    async def get_top_market_news(self, limit: int = 15):
        return [
            HeadlineItem(
                headline="Indian markets open in green tracking positive global cues",
                source="Economic Times",
                url="https://economictimes.com/markets",
                published_at="2026-09-24T07:30:00Z",
            ),
        ]


class MockIntegrationGlobalIndices(GlobalIndicesPort):
    async def get_major_global_indices(self):
        return [
            IndexPoint(symbol="^GSPC", name="S&P 500", last_price=5750.0, change=25.0, change_percent=0.44),
            IndexPoint(symbol="^DJI", name="Dow Jones", last_price=42200.0, change=140.0, change_percent=0.33),
            IndexPoint(symbol="GIFT_NIFTY", name="Gift Nifty", last_price=24950.0, change=45.0, change_percent=0.18),
        ]


class MockIntegrationNSE(NSEMarketDataPort):
    async def get_indian_indices(self):
        broad = [
            IndexPerformanceItem(symbol="NIFTY 50", name="Nifty 50", current_price=24850.0, change=120.0, change_percent=0.49),
            IndexPerformanceItem(symbol="NIFTY BANK", name="Nifty Bank", current_price=54200.0, change=310.0, change_percent=0.58),
        ]
        sectors = [
            SectorPerformanceItem(sector="NIFTY IT", change_percent=1.15, advances=8, declines=2),
        ]
        return broad, sectors

    async def get_top_gainers_and_losers(self):
        gainers = [TopMoverItem(symbol="INFY", company_name="Infosys", current_price=1920.0, change_percent=2.45, direction="gainer")]
        losers = [TopMoverItem(symbol="TATAMOTORS", company_name="Tata Motors", current_price=960.0, change_percent=-1.30, direction="loser")]
        return gainers, losers

    async def get_fii_dii_data(self):
        return FIIDIIData(
            date="24-Sep-2026",
            fii_buy=12500.0,
            fii_sell=11200.0,
            fii_net=1300.0,
            dii_buy=9800.0,
            dii_sell=8900.0,
            dii_net=900.0,
            unit="INR_CRORES",
        )


class MockIntegrationCommodities(CommoditiesPort):
    async def get_commodities(self):
        return [
            CommodityItem(symbol="BZ=F", name="Brent Crude Oil", last_price=74.20, change=0.85, change_percent=1.16, source="international_benchmark"),
        ]


class MockIntegrationCurrency(CurrencyPort):
    async def get_inr_currency_pairs(self):
        return [
            CurrencyPairItem(pair="USD/INR", last_price=83.65, change=0.04, change_percent=0.05),
        ]


class MockIntegrationADR(IndianADRPort):
    async def get_indian_adrs(self):
        return [
            ADRItem(symbol="INFY", company_name="Infosys ADR", last_price=22.80, change=0.45, change_percent=2.01, exchange="NYSE"),
        ]


class MockIntegrationCorporate(CorporateAnnouncementsPort):
    async def get_recent_corporate_events(self, limit: int = 15):
        return [
            CorporateEventItem(
                symbol="RELIANCE",
                company_name="Reliance Industries",
                event_type="Board Meeting",
                details="Board meeting to consider quarterly results.",
                announcement_date="24-Sep-2026 18:30",
                source="NSE",
            )
        ]


class MockIntegrationNews(MarketReportNewsPort):
    async def get_stocks_in_news(self, limit: int = 8, window_hours: int = 24):
        return [
            StockInNewsItem(
                symbol="TCS",
                company_name="Tata Consultancy Services Ltd",
                description="TCS announces enterprise cloud digital transformation contract.",
                source_headline="TCS signs enterprise deal",
                source_url="https://financialexpress.com/tcs",
            )
        ]

    async def get_market_news(self, limit: int = 10, window_hours: int = 24):
        return [
            HeadlineItem(
                headline="RBI maintains liquidity stance amid steady domestic growth metrics",
                source="Economic Times",
                url="https://economictimes.com/rbi",
                published_at="2026-09-24T05:30:00Z",
            )
        ]


@pytest.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def mock_admin_user():
    return User(
        id=1,
        email="admin@sentinews.com",
        hashed_password="hashed_pwd",
        full_name="Admin User",
        is_active=True,
        is_superuser=True,
    )


@pytest.mark.asyncio
async def test_full_market_reports_flow(async_db: AsyncSession, mock_admin_user: User):
    """
    End-to-end integration test:
    1. Triggers manual generation via internal/admin endpoint.
    2. Verifies persisted report in DB.
    3. Fetches via GET /api/v1/market-reports/pre-market/latest and GET /api/v1/market-reports/post-market/latest.
    4. Fetches via GET /api/v1/market-reports/{id}.
    5. Validates statutory SEBI disclaimer, source providers, and is_partial on public schemas.
    6. Verifies paginated listing at GET /api/v1/market-reports.
    """
    # Override dependencies
    app.dependency_overrides[get_db] = lambda: async_db
    app.dependency_overrides[get_market_report_repository] = lambda: SQLAlchemyMarketReportRepository(async_db)
    app.dependency_overrides[get_finnhub_client] = lambda: MockIntegrationFinnhub()
    app.dependency_overrides[get_stocknews_client] = lambda: MockIntegrationStockNews()
    app.dependency_overrides[get_global_indices_adapter] = lambda: MockIntegrationGlobalIndices()
    app.dependency_overrides[get_nse_market_data_adapter] = lambda: MockIntegrationNSE()
    app.dependency_overrides[get_commodities_adapter] = lambda: MockIntegrationCommodities()
    app.dependency_overrides[get_currency_adapter] = lambda: MockIntegrationCurrency()
    app.dependency_overrides[get_adr_adapter] = lambda: MockIntegrationADR()
    app.dependency_overrides[get_corporate_announcements_adapter] = lambda: MockIntegrationCorporate()
    app.dependency_overrides[get_market_report_news_adapter] = lambda: MockIntegrationNews()
    app.dependency_overrides[get_current_active_user] = lambda: mock_admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Internal-Secret": settings.INTERNAL_API_SECRET}

        # 1. Trigger PRE_MARKET generation via internal endpoint
        pre_gen_resp = await client.post(
            "/internal/market-reports/generate",
            json={"report_type": "PRE_MARKET", "report_date": "2026-09-24", "force": True},
            headers=headers,
        )
        assert pre_gen_resp.status_code == 200, pre_gen_resp.text
        pre_data = pre_gen_resp.json()
        assert pre_data["report_type"] == "PRE_MARKET"
        assert pre_data["status"] == "PUBLISHED"
        assert pre_data["is_partial"] is False
        assert pre_data["disclaimer"] == SEBI_MANDATORY_DISCLAIMER
        pre_id = pre_data["id"]

        # 2. Trigger POST_MARKET generation
        post_gen_resp = await client.post(
            "/internal/market-reports/generate",
            json={"report_type": "POST_MARKET", "report_date": "2026-09-24", "force": True},
            headers=headers,
        )
        assert post_gen_resp.status_code == 200, post_gen_resp.text
        post_data = post_gen_resp.json()
        assert post_data["report_type"] == "POST_MARKET"
        assert post_data["status"] == "PUBLISHED"
        post_id = post_data["id"]

        # 3. Trigger GLOBAL_PRE_MARKET generation
        global_pre_gen_resp = await client.post(
            "/internal/market-reports/generate",
            json={"report_type": "GLOBAL_PRE_MARKET", "report_date": "2026-09-24", "force": True},
            headers=headers,
        )
        assert global_pre_gen_resp.status_code == 200, global_pre_gen_resp.text
        global_pre_data = global_pre_gen_resp.json()
        assert global_pre_data["report_type"] == "GLOBAL_PRE_MARKET"
        assert global_pre_data["status"] == "PUBLISHED"
        global_pre_id = global_pre_data["id"]

        # 4. Trigger GLOBAL_POST_MARKET generation
        global_post_gen_resp = await client.post(
            "/internal/market-reports/generate",
            json={"report_type": "GLOBAL_POST_MARKET", "report_date": "2026-09-24", "force": True},
            headers=headers,
        )
        assert global_post_gen_resp.status_code == 200, global_post_gen_resp.text
        global_post_data = global_post_gen_resp.json()
        assert global_post_data["report_type"] == "GLOBAL_POST_MARKET"
        assert global_post_data["status"] == "PUBLISHED"
        global_post_id = global_post_data["id"]

        # 5. Public GET /api/v1/market-reports/pre-market/latest
        latest_pre_resp = await client.get("/api/v1/market-reports/pre-market/latest")
        assert latest_pre_resp.status_code == 200
        latest_pre = latest_pre_resp.json()
        assert latest_pre["id"] == pre_id
        assert latest_pre["disclaimer"] == SEBI_MANDATORY_DISCLAIMER
        assert latest_pre["is_partial"] is False
        assert len(latest_pre["sections"]["major_global_indices"]) == 3
        assert len(latest_pre["sections"]["indian_indices_prev_close"]) == 2

        # 6. Public GET /api/v1/market-reports/post-market/latest
        latest_post_resp = await client.get("/api/v1/market-reports/post-market/latest")
        assert latest_post_resp.status_code == 200
        latest_post = latest_post_resp.json()
        assert latest_post["id"] == post_id
        assert len(latest_post["sections"]["indian_indices_close"]) == 2
        assert len(latest_post["sections"]["top_gainers"]) == 1

        # 7. Public GET /api/v1/market-reports/global/pre-market/latest
        latest_global_pre_resp = await client.get("/api/v1/market-reports/global/pre-market/latest")
        assert latest_global_pre_resp.status_code == 200
        latest_global_pre = latest_global_pre_resp.json()
        assert latest_global_pre["id"] == global_pre_id
        assert latest_global_pre["report_type"] == "GLOBAL_PRE_MARKET"
        assert len(latest_global_pre["sections"]["global_indices"]) == 3

        # 8. Public GET /api/v1/market-reports/global/post-market/latest
        latest_global_post_resp = await client.get("/api/v1/market-reports/global/post-market/latest")
        assert latest_global_post_resp.status_code == 200
        latest_global_post = latest_global_post_resp.json()
        assert latest_global_post["id"] == global_post_id
        assert latest_global_post["report_type"] == "GLOBAL_POST_MARKET"
        assert len(latest_global_post["sections"]["global_top_movers"]) == 2

        # 9. Public GET /api/v1/market-reports/{id}
        single_resp = await client.get(f"/api/v1/market-reports/{global_pre_id}")
        assert single_resp.status_code == 200
        assert single_resp.json()["id"] == global_pre_id

        # 10. Non-existent report returns 404
        not_found_resp = await client.get("/api/v1/market-reports/999999")
        assert not_found_resp.status_code == 404

        # 11. Paginated list GET /api/v1/market-reports
        list_resp = await client.get("/api/v1/market-reports?page=1&limit=10")
        assert list_resp.status_code == 200
        list_data = list_resp.json()
        assert list_data["total"] >= 4
        assert len(list_data["items"]) >= 4
        assert list_data["disclaimer"] == SEBI_MANDATORY_DISCLAIMER

    # Clean up overrides
    app.dependency_overrides.clear()
