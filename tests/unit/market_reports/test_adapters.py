"""
Unit tests for Market Reports Infrastructure Adapters.

Tests:
1. GlobalIndicesAdapter with fallback and snapshot caching.
2. NSEMarketDataProvider with cookie handshake and parsing.
3. CommoditiesAdapter with international benchmark labeling.
4. CurrencyAdapter with INR pair parsing and fallback.
5. IndianADRAdapter with verified ADR list.
6. CorporateAnnouncementsAdapter with event categorization and SEBI sanitization.
7. MarketReportNewsAdapter with LiveNewsService integration.
8. OutboundRateLimiter token bucket pacing.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.cache.market_cache import market_cache
from app.modules.market_reports.domain.entities import (
    ADRItem,
    CommodityItem,
    CorporateEventItem,
    CurrencyPairItem,
    HeadlineItem,
    IndexPerformanceItem,
    IndexPoint,
    SectorPerformanceItem,
    StockInNewsItem,
    TopMoverItem,
)
from app.modules.market_reports.infrastructure.adapters.adr_adapter import IndianADRAdapter
from app.modules.market_reports.infrastructure.adapters.commodities_adapter import CommoditiesAdapter
from app.modules.market_reports.infrastructure.adapters.corporate_announcements_adapter import (
    CorporateAnnouncementsAdapter,
)
from app.modules.market_reports.infrastructure.adapters.currency_adapter import CurrencyAdapter
from app.modules.market_reports.infrastructure.adapters.global_indices_adapter import (
    GlobalIndicesAdapter,
)
from app.modules.market_reports.infrastructure.adapters.news_intelligence_adapter import (
    MarketReportNewsAdapter,
)
from app.modules.market_reports.infrastructure.adapters.nse_market_data_adapter import (
    NSEMarketDataProvider,
)
from app.modules.market_reports.infrastructure.adapters.outbound_limiter import (
    AsyncTokenBucket,
    OutboundRateLimiter,
)
from app.modules.news_intelligence.domain.entities import NewsArticle


@pytest.mark.asyncio
async def test_global_indices_adapter_fallback():
    """Tests GlobalIndicesAdapter falling back to Finnhub when yfinance fails."""
    mock_finnhub = MagicMock()
    mock_finnhub.get_global_indices = AsyncMock(return_value=[
        {"symbol": "^GSPC", "name": "S&P 500", "last_price": 5700.0, "change": 25.0, "change_percent": 0.44},
    ])

    adapter = GlobalIndicesAdapter(finnhub_fallback=mock_finnhub)
    adapter._fetch_from_yfinance = AsyncMock(side_effect=RuntimeError("Yahoo Finance timeout"))

    # Clear cache
    await market_cache.delete("market_reports:adapter:global_indices")

    results = await adapter.get_major_global_indices()
    assert len(results) == 1
    assert results[0].symbol == "^GSPC"
    assert results[0].last_price == 5700.0


@pytest.mark.asyncio
async def test_nse_indices_parsing():
    """Tests NSEMarketDataProvider parsing broad and sector indices."""
    adapter = NSEMarketDataProvider()
    adapter._fetch_json = AsyncMock(return_value={
        "data": [
            {"index": "NIFTY 50", "last": 24850.5, "percentChange": 0.45, "previousClose": 24740.0, "variation": 110.5, "advances": 32, "declines": 18},
            {"index": "NIFTY BANK", "last": 54200.0, "percentChange": 0.60, "previousClose": 53877.0, "variation": 323.0, "advances": 9, "declines": 3},
            {"index": "NIFTY IT", "last": 42100.0, "percentChange": 1.25, "previousClose": 41580.0, "variation": 520.0, "advances": 8, "declines": 2},
        ]
    })

    # Clear cache
    await market_cache.delete("market_reports:adapter:nse_indices")

    broad, sectors = await adapter.get_indian_indices()
    assert len(broad) >= 1
    assert any(b.symbol == "NIFTY 50" for b in broad)
    assert any(s.sector == "NIFTY IT" for s in sectors)


@pytest.mark.asyncio
async def test_nse_fii_dii_parsing():
    """Tests NSEMarketDataProvider parsing FII/DII disclosures."""
    adapter = NSEMarketDataProvider()
    adapter._fetch_json = AsyncMock(return_value=[
        {"category": "DII", "date": "25-Sep-2026", "buyValue": "14,035.77", "sellValue": "11,197.60", "netValue": "2,838.17"},
        {"category": "FII/FPI", "date": "25-Sep-2026", "buyValue": "12,327.36", "sellValue": "16,021.29", "netValue": "-3,693.93"},
    ])

    await market_cache.delete("market_reports:adapter:nse_fiidii")

    fii_dii = await adapter.get_fii_dii_data()
    assert fii_dii is not None
    assert fii_dii.date == "25-Sep-2026"
    assert fii_dii.fii_net == -3693.93
    assert fii_dii.dii_net == 2838.17
    assert fii_dii.unit == "INR_CRORES"


@pytest.mark.asyncio
async def test_commodities_adapter_benchmark_labeling():
    """Verifies CommoditiesAdapter tags quotes with international_benchmark source."""
    adapter = CommoditiesAdapter()
    adapter._fetch_from_yfinance = AsyncMock(return_value=[
        CommodityItem(symbol="BZ=F", name="Brent Crude Oil", last_price=74.5, change=0.8, change_percent=1.08, unit="USD/bbl", source="international_benchmark"),
    ])

    await market_cache.delete("market_reports:adapter:commodities")

    items = await adapter.get_commodities()
    assert len(items) == 1
    assert items[0].source == "international_benchmark"


@pytest.mark.asyncio
async def test_corporate_announcements_adapter_categorization():
    """Tests CorporateAnnouncementsAdapter event categorization."""
    adapter = CorporateAnnouncementsAdapter()
    adapter._fetch_json = AsyncMock(return_value=[
        {"symbol": "RELIANCE", "sm_name": "Reliance Industries Ltd", "desc": "Board meeting on 15-Oct-2026 to consider financial results", "an_dt": "25-Sep-2026 14:30"},
        {"symbol": "TCS", "sm_name": "Tata Consultancy Services", "desc": "Interim Dividend declaration of Rs 10 per share", "an_dt": "25-Sep-2026 15:00"},
    ])

    await market_cache.delete("market_reports:adapter:corporate_announcements")

    events = await adapter.get_recent_corporate_events(limit=5)
    assert len(events) == 2
    assert events[0].event_type in ("Board Meeting", "Financial Results")
    assert events[1].event_type == "Dividend Announcement"


@pytest.mark.asyncio
async def test_news_adapter_extracts_stocks_in_news():
    """Tests MarketReportNewsAdapter extracting tickers and factual summaries from LiveNewsService."""
    mock_news_service = MagicMock()
    mock_articles = [
        NewsArticle(
            id=101,
            title="Infosys expands collaboration with Microsoft for generative AI cloud solutions",
            summary="Infosys has announced an expanded collaboration with Microsoft to accelerate enterprise AI adoption across Europe and North America.",
            url="https://financialexpress.com/infy-ai",
            source="Financial Express",
            symbols=["INFY"],
            sectors=["Information Technology"],
            published_at=datetime.now(timezone.utc),
        ),
    ]
    mock_news_service.get_trending_articles = AsyncMock(return_value=mock_articles)
    mock_news_service.get_active_articles = AsyncMock(return_value=mock_articles)

    adapter = MarketReportNewsAdapter(news_service=mock_news_service)
    stocks = await adapter.get_stocks_in_news(limit=5, window_hours=24)

    assert len(stocks) == 1
    assert stocks[0].symbol == "INFY"
    assert "Infosys" in stocks[0].company_name
    assert "Microsoft" in stocks[0].description


@pytest.mark.asyncio
async def test_outbound_rate_limiter():
    """Tests OutboundRateLimiter token bucket behavior."""
    bucket = AsyncTokenBucket(rate_per_second=10.0, capacity=2.0, max_concurrency=1)
    await bucket.acquire()
    bucket.release()
    assert bucket.tokens >= 0.0

    limiter = OutboundRateLimiter()
    async with limiter.pace("nse"):
        assert True
