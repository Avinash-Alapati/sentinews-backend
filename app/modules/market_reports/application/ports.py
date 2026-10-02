"""
Application Ports (Protocols) for Market Reports module.

Decouples use case orchestration from concrete infrastructure implementations
(HTTP clients, database repositories, distributed locks, caches).
"""

from datetime import date
from typing import Any, Dict, List, Optional, Protocol, Tuple

from app.modules.market_reports.domain.entities import (
    HeadlineItem,
    MarketReport,
)
from app.modules.market_reports.domain.enums import ReportType


class FinnhubMarketClientPort(Protocol):
    """Port for communicating with Finnhub financial market data API."""

    async def get_market_status(self, exchange: str = "IN") -> Dict[str, Any]:
        """Queries exchange operating status (open, closed, holiday)."""
        ...

    async def get_global_indices(self) -> List[Dict[str, Any]]:
        """Retrieves quotes for overnight US and major Asian indices."""
        ...

    async def get_economic_calendar(
        self, from_date: date, to_date: date
    ) -> List[Dict[str, Any]]:
        """Fetches scheduled macroeconomic releases for specified date range."""
        ...

    async def get_market_news(self, category: str = "general") -> List[Dict[str, Any]]:
        """Fetches general financial market news."""
        ...

    async def get_domestic_indices(self) -> List[Dict[str, Any]]:
        """Retrieves performance metrics for primary domestic indices (Nifty, Sensex)."""
        ...

    async def get_top_gainers_losers(self) -> Dict[str, List[Dict[str, Any]]]:
        """Retrieves factual equity gainers and losers by percentage move."""
        ...

    async def get_extended_global_indices(self) -> List[Dict[str, Any]]:
        """Retrieves quotes across US, European, Asian, and GIFT indices."""
        ...

    async def get_commodities_and_fx(self) -> List[Dict[str, Any]]:
        """Retrieves quotes for key commodities, yields, and FX pairs."""
        ...

    async def get_global_movers(self) -> Dict[str, List[Dict[str, Any]]]:
        """Retrieves quotes and performance for global mega-cap companies."""
        ...


class StockNewsClientPort(Protocol):
    """Port for communicating with Stock News API (stocknewsapi.com)."""

    async def get_top_market_news(self, limit: int = 15) -> List[HeadlineItem]:
        """
        Retrieves top factual market news headlines.
        Returns HeadlineItem objects with headline, source, url, published_at.
        """
        ...


class MarketReportRepositoryPort(Protocol):
    """Port for persisting and querying MarketReport entities."""

    async def save(self, report: MarketReport) -> MarketReport:
        """Saves or updates a market report entity."""
        ...

    async def get_by_id(self, report_id: int) -> Optional[MarketReport]:
        """Fetches a single market report by ID."""
        ...

    async def get_by_type_and_date(
        self, report_type: ReportType, report_date: date
    ) -> Optional[MarketReport]:
        """Fetches a report by its type and scheduled date."""
        ...

    async def get_latest_published(
        self, report_type: ReportType
    ) -> Optional[MarketReport]:
        """Fetches the most recently published report for the given type."""
        ...

    async def list_published(
        self,
        report_type: Optional[ReportType] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Tuple[List[MarketReport], int]:
        """Fetches paginated published reports with optional type and date filtering."""
        ...


class ReportLockPort(Protocol):
    """Port for distributed synchronization and concurrency locking."""

    async def acquire_lock(
        self, report_type: ReportType, report_date: date, ttl_seconds: int = 120
    ) -> bool:
        """Attempts to acquire a distributed run lock for a specific report."""
        ...

    async def release_lock(self, report_type: ReportType, report_date: date) -> None:
        """Releases the distributed run lock."""
        ...


class GlobalIndicesPort(Protocol):
    """Port for querying major global benchmark indices."""

    async def get_major_global_indices(self) -> List[Any]:
        ...


class NSEMarketDataPort(Protocol):
    """Port for querying NSE official indices, gainers/losers, and FII/DII disclosures."""

    async def get_indian_indices(self) -> Tuple[List[Any], List[Any]]:
        ...

    async def get_top_gainers_and_losers(self) -> Tuple[List[Any], List[Any]]:
        ...

    async def get_fii_dii_data(self) -> Optional[Any]:
        ...


class CommoditiesPort(Protocol):
    """Port for querying international commodity futures benchmark quotes."""

    async def get_commodities(self) -> List[Any]:
        ...


class CurrencyPort(Protocol):
    """Port for querying INR-relevant currency pair rates."""

    async def get_inr_currency_pairs(self) -> List[Any]:
        ...


class IndianADRPort(Protocol):
    """Port for querying active Indian ADRs."""

    async def get_indian_adrs(self) -> List[Any]:
        ...


class CorporateAnnouncementsPort(Protocol):
    """Port for querying exchange corporate filings and announcements."""

    async def get_recent_corporate_events(self, limit: int = 15) -> List[Any]:
        ...


class MarketReportNewsPort(Protocol):
    """Port for deriving stocks in news and market headlines from News Intelligence."""

    async def get_stocks_in_news(
        self, limit: int = 8, window_hours: int = 24
    ) -> List[Any]:
        ...

    async def get_market_news(
        self, limit: int = 10, window_hours: int = 24
    ) -> List[HeadlineItem]:
        ...

