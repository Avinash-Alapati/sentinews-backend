"""
Domain entities and value objects for the Market Reports module.

Adheres strictly to SEBI compliance:
- Headline items contain only factual attribution metadata (no sentiment/advisory tags).
- Sections are structured and factual without actionable recommendations.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.modules.market_reports.domain.enums import ReportStatus, ReportType

# Statutory SEBI disclaimer text
SEBI_MANDATORY_DISCLAIMER = (
    "For informational purposes only. Not investment advice. "
    "Sentinews is not a SEBI-registered investment adviser or research analyst."
)


@dataclass
class HeadlineItem:
    """
    Factual news headline with attribution.
    Strict SEBI compliance: NO sentiment tags or advisory signals.
    """
    headline: str
    source: str
    url: str
    published_at: str


@dataclass
class IndexPoint:
    """Factual benchmark index or commodity quote."""
    symbol: str
    name: str
    last_price: float
    change: float
    change_percent: float


@dataclass
class GlobalCuesSection:
    """Overnight global market movements."""
    us_indices: List[IndexPoint] = field(default_factory=list)
    asian_indices: List[IndexPoint] = field(default_factory=list)
    gift_nifty: Optional[IndexPoint] = None
    summary_notes: str = ""


@dataclass
class EconomicEventItem:
    """Macroeconomic calendar release."""
    event: str
    country: str
    date_time: str
    impact: str = "medium"  # low, medium, high
    actual: Optional[str] = None
    estimate: Optional[str] = None
    previous: Optional[str] = None


@dataclass
class IndexPerformanceItem:
    """Domestic index performance metric."""
    symbol: str
    name: str
    current_price: float
    change: float
    change_percent: float


@dataclass
class TopMoverItem:
    """
    Factual equity mover percentage (data only, no buy/sell framing).
    """
    symbol: str
    company_name: str
    current_price: float
    change_percent: float
    direction: str = "gainer"  # 'gainer' or 'loser'


@dataclass
class SectorPerformanceItem:
    """Sectoral index performance."""
    sector: str
    change_percent: float
    advances: Optional[int] = None
    declines: Optional[int] = None


@dataclass
class CommodityItem:
    """Factual commodity futures benchmark quote."""
    symbol: str
    name: str
    last_price: float
    change: float
    change_percent: float
    unit: str = "USD"
    source: str = "international_benchmark"


@dataclass
class CurrencyPairItem:
    """Factual INR-relevant currency pair quote."""
    pair: str  # e.g. "USD/INR", "EUR/INR", "GBP/INR", "JPY/INR"
    last_price: float
    change: float
    change_percent: float
    source: str = "yfinance"


@dataclass
class ADRItem:
    """Performance quote for Indian company ADR in US markets."""
    symbol: str
    company_name: str
    last_price: float
    change: float
    change_percent: float
    exchange: str = "NYSE"


@dataclass
class FIIDIIData:
    """Institutional investment flows in INR Crores."""
    date: str
    fii_buy: float
    fii_sell: float
    fii_net: float
    dii_buy: float
    dii_sell: float
    dii_net: float
    unit: str = "INR_CRORES"
    source_note: str = "NSE Institutional Disclosure (informational use only)"


@dataclass
class StockInNewsItem:
    """Stock in news candidate with factual, non-actionable summary."""
    symbol: str
    company_name: str
    description: str
    source_headline: str = ""
    source_url: str = ""


@dataclass
class CorporateEventItem:
    """Corporate announcements and board meetings."""
    symbol: str
    company_name: str
    event_type: str  # "Financial Results", "Board Meeting", "Dividend", "AGM/EGM", etc.
    details: str
    announcement_date: str
    source: str = "NSE/BSE"


@dataclass
class PreMarketSections:
    """Structured sections for enriched Pre-Market Report."""
    major_global_indices: List[IndexPoint] = field(default_factory=list)
    indian_indices_prev_close: List[IndexPerformanceItem] = field(default_factory=list)
    indian_sector_performance_prev: List[SectorPerformanceItem] = field(default_factory=list)
    commodities: List[CommodityItem] = field(default_factory=list)
    inr_currency_pairs: List[CurrencyPairItem] = field(default_factory=list)
    indian_adrs: List[ADRItem] = field(default_factory=list)
    fii_dii_prev_day: Optional[FIIDIIData] = None
    stocks_in_news: List[StockInNewsItem] = field(default_factory=list)
    corporate_events_carried_forward: List[CorporateEventItem] = field(default_factory=list)
    market_news_carried_forward: List[HeadlineItem] = field(default_factory=list)
    economic_calendar_today: List[EconomicEventItem] = field(default_factory=list)
    market_status: str = "open"  # 'open', 'holiday', 'pre-open'
    # Backwards-compatible aliases
    global_cues: GlobalCuesSection = field(default_factory=GlobalCuesSection)
    key_news_headlines: List[HeadlineItem] = field(default_factory=list)


@dataclass
class PostMarketSections:
    """Structured sections for enriched Post-Market Report."""
    indian_indices_close: List[IndexPerformanceItem] = field(default_factory=list)
    top_gainers: List[TopMoverItem] = field(default_factory=list)
    top_losers: List[TopMoverItem] = field(default_factory=list)
    sector_performance: List[SectorPerformanceItem] = field(default_factory=list)
    fii_dii_data: Optional[FIIDIIData] = None
    commodities_close: List[CommodityItem] = field(default_factory=list)
    corporate_events: List[CorporateEventItem] = field(default_factory=list)
    stocks_in_news: List[StockInNewsItem] = field(default_factory=list)
    market_news_impact: List[HeadlineItem] = field(default_factory=list)
    watch_tomorrow: List[EconomicEventItem] = field(default_factory=list)
    # Backwards-compatible aliases
    index_performance: List[IndexPerformanceItem] = field(default_factory=list)
    top_movers: List[TopMoverItem] = field(default_factory=list)
    key_news_recap: List[HeadlineItem] = field(default_factory=list)
    volume_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GlobalPreMarketSections:
    """Structured sections for Global Pre-Market Report."""
    global_indices: List[IndexPoint] = field(default_factory=list)
    commodities_and_fx: List[IndexPoint] = field(default_factory=list)
    key_news_headlines: List[HeadlineItem] = field(default_factory=list)
    economic_calendar_today: List[EconomicEventItem] = field(default_factory=list)
    summary_notes: str = ""


@dataclass
class GlobalPostMarketSections:
    """Structured sections for Global Post-Market Report."""
    global_indices_performance: List[IndexPerformanceItem] = field(default_factory=list)
    global_top_movers: List[TopMoverItem] = field(default_factory=list)
    commodities_and_fx_close: List[IndexPerformanceItem] = field(default_factory=list)
    key_news_recap: List[HeadlineItem] = field(default_factory=list)
    watch_tomorrow_global: List[EconomicEventItem] = field(default_factory=list)


@dataclass
class MarketReport:
    """
    Core domain entity representing a generated Pre-Market or Post-Market Report.
    """
    report_type: ReportType
    report_date: date
    status: ReportStatus = ReportStatus.DRAFT
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sections: Dict[str, Any] = field(default_factory=dict)
    disclaimer: str = SEBI_MANDATORY_DISCLAIMER
    source_providers: List[str] = field(default_factory=lambda: ["nse", "yfinance", "live_news"])
    is_partial: bool = False
    error_details: Optional[str] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
