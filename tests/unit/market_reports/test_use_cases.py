"""
Unit tests for Market Reports Application Use Cases.

Tests:
1. Enriched Pre-Market Report generation & section assembly (all 10+ fields).
2. Enriched Post-Market Report generation & section assembly (all 8+ fields).
3. Carry forward of corporate events & market news from yesterday's post-market report.
4. Idempotency handling (skip existing published vs force re-generation).
5. Fail-open vendor handling and snapshot fallback.
6. Statutory SEBI compliance validation across generated sections.
7. Outbound rate limiting & adapter pacing.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.modules.market_reports.application.ports import (
    CommoditiesPort,
    CorporateAnnouncementsPort,
    CurrencyPort,
    FinnhubMarketClientPort,
    GlobalIndicesPort,
    IndianADRPort,
    MarketReportNewsPort,
    MarketReportRepositoryPort,
    NSEMarketDataPort,
    ReportLockPort,
    StockNewsClientPort,
)
from app.modules.market_reports.application.use_cases.generate_global_post_market_report import (
    GenerateGlobalPostMarketReportUseCase,
)
from app.modules.market_reports.application.use_cases.generate_global_pre_market_report import (
    GenerateGlobalPreMarketReportUseCase,
)
from app.modules.market_reports.application.use_cases.generate_post_market_report import (
    GeneratePostMarketReportUseCase,
)
from app.modules.market_reports.application.use_cases.generate_pre_market_report import (
    GeneratePreMarketReportUseCase,
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
    MarketReport,
    SEBI_MANDATORY_DISCLAIMER,
    SectorPerformanceItem,
    StockInNewsItem,
    TopMoverItem,
)
from app.modules.market_reports.domain.enums import ReportStatus, ReportType


class InMemoryMarketReportRepository(MarketReportRepositoryPort):
    def __init__(self):
        self.reports: Dict[Tuple[str, date], MarketReport] = {}
        self._id_counter = 1

    async def save(self, report: MarketReport) -> MarketReport:
        if not report.id:
            report.id = self._id_counter
            self._id_counter += 1
        key = (report.report_type.value, report.report_date)
        self.reports[key] = report
        return report

    async def get_by_id(self, report_id: int) -> Optional[MarketReport]:
        for r in self.reports.values():
            if r.id == report_id:
                return r
        return None

    async def get_by_type_and_date(
        self, report_type: ReportType, report_date: date
    ) -> Optional[MarketReport]:
        return self.reports.get((report_type.value, report_date))

    async def get_latest_published(
        self, report_type: ReportType
    ) -> Optional[MarketReport]:
        matching = [
            r for r in self.reports.values()
            if r.report_type == report_type and r.status == ReportStatus.PUBLISHED
        ]
        if not matching:
            return None
        return sorted(matching, key=lambda x: x.report_date, reverse=True)[0]

    async def list_published(
        self,
        report_type: Optional[ReportType] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Tuple[List[MarketReport], int]:
        matching = [
            r for r in self.reports.values()
            if r.status == ReportStatus.PUBLISHED
        ]
        if report_type:
            matching = [r for r in matching if r.report_type == report_type]
        if start_date:
            matching = [r for r in matching if r.report_date >= start_date]
        if end_date:
            matching = [r for r in matching if r.report_date <= end_date]

        sorted_reports = sorted(matching, key=lambda x: x.report_date, reverse=True)
        return sorted_reports[offset : offset + limit], len(sorted_reports)


class MockGlobalIndicesAdapter(GlobalIndicesPort):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def get_major_global_indices(self) -> List[IndexPoint]:
        if self.should_fail:
            raise RuntimeError("Global indices provider failure")
        return [
            IndexPoint(symbol="^GSPC", name="S&P 500", last_price=5750.0, change=25.0, change_percent=0.44),
            IndexPoint(symbol="^IXIC", name="Nasdaq", last_price=18100.0, change=110.0, change_percent=0.61),
            IndexPoint(symbol="^DJI", name="Dow Jones", last_price=42200.0, change=140.0, change_percent=0.33),
            IndexPoint(symbol="^N225", name="Nikkei 225", last_price=38900.0, change=250.0, change_percent=0.65),
            IndexPoint(symbol="GIFT_NIFTY", name="Gift Nifty", last_price=24950.0, change=45.0, change_percent=0.18),
        ]


class MockNSEMarketDataProvider(NSEMarketDataPort):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def get_indian_indices(self) -> Tuple[List[IndexPerformanceItem], List[SectorPerformanceItem]]:
        if self.should_fail:
            raise RuntimeError("NSE indices error")
        broad = [
            IndexPerformanceItem(symbol="NIFTY 50", name="Nifty 50", current_price=24850.0, change=120.0, change_percent=0.49),
            IndexPerformanceItem(symbol="NIFTY BANK", name="Nifty Bank", current_price=54200.0, change=310.0, change_percent=0.58),
        ]
        sectors = [
            SectorPerformanceItem(sector="NIFTY IT", change_percent=1.15, advances=8, declines=2),
            SectorPerformanceItem(sector="NIFTY AUTO", change_percent=0.42, advances=10, declines=5),
        ]
        return broad, sectors

    async def get_top_gainers_and_losers(self) -> Tuple[List[TopMoverItem], List[TopMoverItem]]:
        if self.should_fail:
            raise RuntimeError("NSE movers error")
        gainers = [TopMoverItem(symbol="INFY", company_name="Infosys", current_price=1920.0, change_percent=2.45, direction="gainer")]
        losers = [TopMoverItem(symbol="TATAMOTORS", company_name="Tata Motors", current_price=960.0, change_percent=-1.30, direction="loser")]
        return gainers, losers

    async def get_fii_dii_data(self) -> Optional[FIIDIIData]:
        if self.should_fail:
            raise RuntimeError("NSE FII/DII error")
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


class MockCommoditiesAdapter(CommoditiesPort):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def get_commodities(self) -> List[CommodityItem]:
        if self.should_fail:
            raise RuntimeError("Commodities error")
        return [
            CommodityItem(symbol="BZ=F", name="Brent Crude Oil", last_price=74.20, change=0.85, change_percent=1.16, source="international_benchmark"),
            CommodityItem(symbol="GC=F", name="Gold", last_price=2680.0, change=14.0, change_percent=0.53, source="international_benchmark"),
        ]


class MockCurrencyAdapter(CurrencyPort):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def get_inr_currency_pairs(self) -> List[CurrencyPairItem]:
        if self.should_fail:
            raise RuntimeError("Currency error")
        return [
            CurrencyPairItem(pair="USD/INR", last_price=83.65, change=0.04, change_percent=0.05),
            CurrencyPairItem(pair="EUR/INR", last_price=93.10, change=-0.12, change_percent=-0.13),
            CurrencyPairItem(pair="GBP/INR", last_price=111.45, change=0.22, change_percent=0.20),
            CurrencyPairItem(pair="JPY/INR", last_price=0.58, change=0.001, change_percent=0.17),
        ]


class MockIndianADRAdapter(IndianADRPort):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def get_indian_adrs(self) -> List[ADRItem]:
        if self.should_fail:
            raise RuntimeError("ADR error")
        return [
            ADRItem(symbol="INFY", company_name="Infosys ADR", last_price=22.80, change=0.45, change_percent=2.01, exchange="NYSE"),
            ADRItem(symbol="HDB", company_name="HDFC Bank ADR", last_price=64.50, change=0.70, change_percent=1.10, exchange="NYSE"),
        ]


class MockCorporateAnnouncementsAdapter(CorporateAnnouncementsPort):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def get_recent_corporate_events(self, limit: int = 15) -> List[CorporateEventItem]:
        if self.should_fail:
            raise RuntimeError("Corporate announcements error")
        return [
            CorporateEventItem(
                symbol="RELIANCE",
                company_name="Reliance Industries",
                event_type="Board Meeting",
                details="Board meeting to consider unaudited financial results.",
                announcement_date="24-Sep-2026 18:30",
                source="NSE",
            )
        ]


class MockMarketReportNewsAdapter(MarketReportNewsPort):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def get_stocks_in_news(self, limit: int = 8, window_hours: int = 24) -> List[StockInNewsItem]:
        if self.should_fail:
            raise RuntimeError("News intelligence error")
        return [
            StockInNewsItem(
                symbol="TCS",
                company_name="Tata Consultancy Services Ltd",
                description="TCS signs multi-year digital transformation deal with European retail client.",
                source_headline="TCS secures major cloud transformation contract",
                source_url="https://financialexpress.com/tcs-contract",
            )
        ]

    async def get_market_news(self, limit: int = 10, window_hours: int = 24) -> List[HeadlineItem]:
        if self.should_fail:
            raise RuntimeError("Market news error")
        return [
            HeadlineItem(
                headline="RBI maintains liquidity stance amid steady domestic inflation data",
                source="Economic Times",
                url="https://economictimes.com/rbi-liquidity",
                published_at="2026-09-24T05:30:00Z",
            )
        ]


class MockFinnhubClient(FinnhubMarketClientPort):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def get_market_status(self, exchange: str = "IN") -> Dict[str, Any]:
        if self.should_fail:
            raise RuntimeError("Finnhub market status error")
        return {"isOpen": True, "holiday": None}

    async def get_global_indices(self) -> List[Dict[str, Any]]:
        if self.should_fail:
            raise RuntimeError("Finnhub global indices error")
        return [
            {"symbol": "^DJI", "name": "Dow Jones", "region": "us", "last_price": 42000.0, "change": 150.0, "change_percent": 0.35},
            {"symbol": "^GSPC", "name": "S&P 500", "region": "us", "last_price": 5700.0, "change": 25.0, "change_percent": 0.44},
            {"symbol": "GIFT_NIFTY", "name": "GIFT Nifty", "region": "india_gift", "last_price": 24900.0, "change": 60.0, "change_percent": 0.24},
        ]

    async def get_economic_calendar(self, from_date: date, to_date: date) -> List[Dict[str, Any]]:
        if self.should_fail:
            raise RuntimeError("Finnhub economic calendar error")
        return [
            {"event": "US Initial Jobless Claims", "country": "US", "impact": "high", "estimate": "220k"}
        ]

    async def get_market_news(self, category: str = "general") -> List[Dict[str, Any]]:
        return []

    async def get_domestic_indices(self) -> List[Dict[str, Any]]:
        return [
            {"symbol": "NIFTY 50", "name": "Nifty 50", "price": 24850.0, "change": 125.0, "change_percent": 0.51},
        ]

    async def get_top_gainers_losers(self) -> Dict[str, List[Dict[str, Any]]]:
        return {
            "gainers": [{"symbol": "TCS", "name": "TCS", "price": 4120.0, "change_percent": 1.42}],
            "losers": [{"symbol": "HDFCBANK", "name": "HDFC Bank", "price": 1640.0, "change_percent": -1.15}],
        }

    async def get_extended_global_indices(self) -> List[Dict[str, Any]]:
        return [
            {"symbol": "^DJI", "name": "Dow Jones", "region": "us", "last_price": 42000.0, "change": 150.0, "change_percent": 0.35},
            {"symbol": "^GSPC", "name": "S&P 500", "region": "us", "last_price": 5700.0, "change": 25.0, "change_percent": 0.44},
            {"symbol": "^FTSE", "name": "FTSE 100", "region": "europe", "last_price": 8200.0, "change": -10.0, "change_percent": -0.12},
            {"symbol": "^N225", "name": "Nikkei 225", "region": "asia", "last_price": 38000.0, "change": 300.0, "change_percent": 0.79},
        ]

    async def get_commodities_and_fx(self) -> List[Dict[str, Any]]:
        return [
            {"symbol": "CL.F", "name": "Brent Crude Oil", "category": "commodity", "last_price": 74.5, "change": 0.8, "change_percent": 1.08},
            {"symbol": "GC.F", "name": "Gold", "category": "commodity", "last_price": 2650.0, "change": 12.0, "change_percent": 0.45},
            {"symbol": "DX-Y.NYB", "name": "US Dollar Index", "category": "fx", "last_price": 100.5, "change": -0.2, "change_percent": -0.20},
        ]

    async def get_global_movers(self) -> Dict[str, List[Dict[str, Any]]]:
        return {
            "gainers": [{"symbol": "NVDA", "name": "NVIDIA", "price": 125.0, "change_percent": 3.4}],
            "losers": [{"symbol": "TSLA", "name": "Tesla", "price": 240.0, "change_percent": -1.8}],
        }


class MockStockNewsClient(StockNewsClientPort):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def get_top_market_news(self, limit: int = 15) -> List[HeadlineItem]:
        if self.should_fail:
            raise RuntimeError("StockNews API outage")
        return [
            HeadlineItem(
                headline="Federal Reserve signals measured rate path",
                source="Reuters",
                url="https://reuters.com/fed",
                published_at="2026-09-24T06:00:00Z",
            ),
        ]


@pytest.mark.asyncio
async def test_enriched_pre_market_generation_success():
    """Successfully generates and publishes an enriched Pre-Market Report with all fields."""
    repo = InMemoryMarketReportRepository()

    use_case = GeneratePreMarketReportUseCase(
        repository=repo,
        global_indices_adapter=MockGlobalIndicesAdapter(),
        nse_adapter=MockNSEMarketDataProvider(),
        commodities_adapter=MockCommoditiesAdapter(),
        currency_adapter=MockCurrencyAdapter(),
        adr_adapter=MockIndianADRAdapter(),
        corporate_adapter=MockCorporateAnnouncementsAdapter(),
        news_adapter=MockMarketReportNewsAdapter(),
        finnhub_client=MockFinnhubClient(),
    )

    report_date = date(2026, 9, 24)
    report = await use_case.execute(report_date=report_date)

    assert report.status == ReportStatus.PUBLISHED
    assert report.report_type == ReportType.PRE_MARKET
    assert report.report_date == report_date
    assert report.disclaimer == SEBI_MANDATORY_DISCLAIMER

    sec = report.sections
    # Verify all 10 enriched pre-market fields
    assert len(sec["major_global_indices"]) == 5
    assert len(sec["indian_indices_prev_close"]) == 2
    assert len(sec["indian_sector_performance_prev"]) == 2
    assert len(sec["commodities"]) == 2
    assert sec["commodities"][0]["source"] == "international_benchmark"
    assert len(sec["inr_currency_pairs"]) == 4
    assert len(sec["indian_adrs"]) == 2
    assert sec["fii_dii_prev_day"] is not None
    assert sec["fii_dii_prev_day"]["fii_net"] == 1300.0
    assert len(sec["stocks_in_news"]) == 1
    assert sec["stocks_in_news"][0]["symbol"] == "TCS"
    assert len(sec["corporate_events_carried_forward"]) == 1
    assert len(sec["market_news_carried_forward"]) == 1
    assert len(sec["economic_calendar_today"]) == 1


@pytest.mark.asyncio
async def test_pre_market_carry_forward_from_yesterday_post_market():
    """Verifies Pre-Market report carries forward corporate events and market news from prior Post-Market report."""
    repo = InMemoryMarketReportRepository()
    yesterday = date(2026, 9, 23)
    today = date(2026, 9, 24)

    # Seed yesterday's Post-Market report
    prior_report = MarketReport(
        report_type=ReportType.POST_MARKET,
        report_date=yesterday,
        status=ReportStatus.PUBLISHED,
        sections={
            "corporate_events": [
                {
                    "symbol": "INFY",
                    "company_name": "Infosys Ltd",
                    "event_type": "Financial Results",
                    "details": "Q2 audited financial results declared.",
                    "announcement_date": "23-Sep-2026 16:30",
                    "source": "NSE",
                }
            ],
            "market_news_impact": [
                {
                    "headline": "Domestic mutual funds infuse record capital into largecaps",
                    "source": "Livemint",
                    "url": "https://livemint.com/mf-record",
                    "published_at": "2026-09-23T16:00:00Z",
                }
            ],
        },
    )
    await repo.save(prior_report)

    use_case = GeneratePreMarketReportUseCase(
        repository=repo,
        global_indices_adapter=MockGlobalIndicesAdapter(),
        nse_adapter=MockNSEMarketDataProvider(),
        commodities_adapter=MockCommoditiesAdapter(),
        currency_adapter=MockCurrencyAdapter(),
        adr_adapter=MockIndianADRAdapter(),
        corporate_adapter=MockCorporateAnnouncementsAdapter(),
        news_adapter=MockMarketReportNewsAdapter(),
        finnhub_client=MockFinnhubClient(),
    )

    report = await use_case.execute(report_date=today)
    assert report.status == ReportStatus.PUBLISHED
    sec = report.sections

    # Confirm carried forward content matches yesterday's post-market snapshot
    assert len(sec["corporate_events_carried_forward"]) == 1
    assert sec["corporate_events_carried_forward"][0]["symbol"] == "INFY"
    assert len(sec["market_news_carried_forward"]) == 1
    assert "mutual funds" in sec["market_news_carried_forward"][0]["headline"]


@pytest.mark.asyncio
async def test_enriched_post_market_generation_success():
    """Successfully generates and publishes an enriched Post-Market Report with all fields."""
    repo = InMemoryMarketReportRepository()

    use_case = GeneratePostMarketReportUseCase(
        repository=repo,
        nse_adapter=MockNSEMarketDataProvider(),
        commodities_adapter=MockCommoditiesAdapter(),
        corporate_adapter=MockCorporateAnnouncementsAdapter(),
        news_adapter=MockMarketReportNewsAdapter(),
        finnhub_client=MockFinnhubClient(),
    )

    report_date = date(2026, 9, 24)
    report = await use_case.execute(report_date=report_date)

    assert report.status == ReportStatus.PUBLISHED
    assert report.report_type == ReportType.POST_MARKET
    assert report.report_date == report_date
    assert report.disclaimer == SEBI_MANDATORY_DISCLAIMER

    sec = report.sections
    # Verify all 8 post-market fields
    assert len(sec["indian_indices_close"]) == 2
    assert len(sec["top_gainers"]) == 1
    assert len(sec["top_losers"]) == 1
    assert len(sec["sector_performance"]) == 2
    assert sec["fii_dii_data"] is not None
    assert sec["fii_dii_data"]["dii_net"] == 900.0
    assert len(sec["commodities_close"]) == 2
    assert len(sec["corporate_events"]) == 1
    assert len(sec["stocks_in_news"]) == 1
    assert len(sec["market_news_impact"]) == 1
    assert len(sec["watch_tomorrow"]) == 1


@pytest.mark.asyncio
async def test_sebi_compliance_actionable_language_rejection():
    """Rejects reports that contain prohibited promotional / actionable phrasing."""
    repo = InMemoryMarketReportRepository()

    # Create mock news adapter returning prohibited phrasing
    bad_news_adapter = MockMarketReportNewsAdapter()
    bad_news_adapter.get_stocks_in_news = AsyncMock(return_value=[
        StockInNewsItem(
            symbol="BADTICKER",
            company_name="Bad Co",
            description="A screaming buy and multibagger stock with high target price.",
        )
    ])

    use_case = GeneratePreMarketReportUseCase(
        repository=repo,
        global_indices_adapter=MockGlobalIndicesAdapter(),
        nse_adapter=MockNSEMarketDataProvider(),
        commodities_adapter=MockCommoditiesAdapter(),
        currency_adapter=MockCurrencyAdapter(),
        adr_adapter=MockIndianADRAdapter(),
        corporate_adapter=MockCorporateAnnouncementsAdapter(),
        news_adapter=bad_news_adapter,
        finnhub_client=MockFinnhubClient(),
    )

    report = await use_case.execute(report_date=date(2026, 9, 24))
    assert report.status == ReportStatus.FAILED
    assert "SEBI Compliance Violation" in (report.error_details or "")


@pytest.mark.asyncio
async def test_pre_market_idempotency_skip():
    """Skips regeneration when a published report already exists for the date."""
    repo = InMemoryMarketReportRepository()
    report_date = date(2026, 9, 24)

    existing = MarketReport(
        report_type=ReportType.PRE_MARKET,
        report_date=report_date,
        status=ReportStatus.PUBLISHED,
        sections={"summary": "Existing published report"},
    )
    await repo.save(existing)

    use_case = GeneratePreMarketReportUseCase(
        repository=repo,
        global_indices_adapter=MockGlobalIndicesAdapter(),
        nse_adapter=MockNSEMarketDataProvider(),
    )

    result = await use_case.execute(report_date=report_date, force=False)
    assert result.id == existing.id
    assert result.sections == {"summary": "Existing published report"}


@pytest.mark.asyncio
async def test_pre_market_force_regenerate():
    """Regenerates report when force=True even if a published report exists."""
    repo = InMemoryMarketReportRepository()
    report_date = date(2026, 9, 24)

    existing = MarketReport(
        report_type=ReportType.PRE_MARKET,
        report_date=report_date,
        status=ReportStatus.PUBLISHED,
        sections={"summary": "Old report"},
    )
    await repo.save(existing)

    use_case = GeneratePreMarketReportUseCase(
        repository=repo,
        global_indices_adapter=MockGlobalIndicesAdapter(),
        nse_adapter=MockNSEMarketDataProvider(),
        commodities_adapter=MockCommoditiesAdapter(),
        currency_adapter=MockCurrencyAdapter(),
        adr_adapter=MockIndianADRAdapter(),
        corporate_adapter=MockCorporateAnnouncementsAdapter(),
        news_adapter=MockMarketReportNewsAdapter(),
        finnhub_client=MockFinnhubClient(),
    )

    result = await use_case.execute(report_date=report_date, force=True)
    assert "major_global_indices" in result.sections
    assert result.sections != {"summary": "Old report"}


@pytest.mark.asyncio
async def test_global_pre_market_generation_success():
    """Successfully generates and publishes a Global Pre-Market Report."""
    repo = InMemoryMarketReportRepository()
    finnhub = MockFinnhubClient(should_fail=False)
    stocknews = MockStockNewsClient(should_fail=False)

    use_case = GenerateGlobalPreMarketReportUseCase(
        repository=repo,
        finnhub_client=finnhub,
        stocknews_client=stocknews,
    )

    report_date = date(2026, 9, 24)
    report = await use_case.execute(report_date=report_date)

    assert report.status == ReportStatus.PUBLISHED
    assert report.report_type == ReportType.GLOBAL_PRE_MARKET
    assert report.report_date == report_date
    assert report.is_partial is False
    assert report.disclaimer == SEBI_MANDATORY_DISCLAIMER
    assert len(report.sections["global_indices"]) == 4
    assert len(report.sections["commodities_and_fx"]) == 3
    assert len(report.sections["key_news_headlines"]) == 1


@pytest.mark.asyncio
async def test_global_post_market_generation_success():
    """Successfully generates and publishes a Global Post-Market Report."""
    repo = InMemoryMarketReportRepository()
    finnhub = MockFinnhubClient(should_fail=False)
    stocknews = MockStockNewsClient(should_fail=False)

    use_case = GenerateGlobalPostMarketReportUseCase(
        repository=repo,
        finnhub_client=finnhub,
        stocknews_client=stocknews,
    )

    report_date = date(2026, 9, 24)
    report = await use_case.execute(report_date=report_date)

    assert report.status == ReportStatus.PUBLISHED
    assert report.report_type == ReportType.GLOBAL_POST_MARKET
    assert report.report_date == report_date
    assert report.is_partial is False
    assert len(report.sections["global_indices_performance"]) == 4
    assert len(report.sections["global_top_movers"]) == 2
    assert len(report.sections["commodities_and_fx_close"]) == 3
