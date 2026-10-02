"""
Use Case: Generate Enriched Pre-Market Report.

Orchestrates gathering:
1. Major global indices (last price)
2. Indian indices (previous day's close)
3. Indian sector performance (previous day recap)
4. Commodities (last price with international_benchmark flag)
5. INR currency pairs (USD/INR, EUR/INR, GBP/INR, JPY/INR)
6. Indian ADRs (overnight US session performance)
7. FII/DII data (previous day recap)
8. Stocks in the news (with factual SEBI-compliant summaries)
9. Corporate events / results (carried forward from yesterday's post-market report)
10. Market news (carried forward from yesterday's post-market report)
11. Today's economic calendar & market status

Implements fail-open vendor handling, SEBI compliance validation, idempotency,
and APM crash signature emission on hard failure.
"""

from datetime import date, datetime, timezone
import logging
from typing import Any, Dict, List, Optional

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
from app.modules.market_reports.domain.entities import (
    ADRItem,
    CommodityItem,
    CorporateEventItem,
    CurrencyPairItem,
    EconomicEventItem,
    FIIDIIData,
    GlobalCuesSection,
    HeadlineItem,
    IndexPerformanceItem,
    IndexPoint,
    MarketReport,
    PreMarketSections,
    SEBI_MANDATORY_DISCLAIMER,
    SectorPerformanceItem,
    StockInNewsItem,
)
from app.modules.market_reports.domain.enums import ReportStatus, ReportType
from app.modules.market_reports.domain.services.compliance import (
    filter_compliant_headlines,
    validate_market_report_compliance,
)
from app.modules.market_reports.domain.services.trading_calendar import NSETradingCalendar
from app.modules.market_reports.infrastructure.adapters.adr_adapter import IndianADRAdapter
from app.modules.market_reports.infrastructure.adapters.commodities_adapter import CommoditiesAdapter
from app.modules.market_reports.infrastructure.adapters.corporate_announcements_adapter import (
    CorporateAnnouncementsAdapter,
)
from app.modules.market_reports.infrastructure.adapters.currency_adapter import CurrencyAdapter
from app.modules.market_reports.infrastructure.adapters.finnhub_client import FinnhubClient
from app.modules.market_reports.infrastructure.adapters.global_indices_adapter import (
    GlobalIndicesAdapter,
)
from app.modules.market_reports.infrastructure.adapters.news_intelligence_adapter import (
    MarketReportNewsAdapter,
)
from app.modules.market_reports.infrastructure.adapters.nse_market_data_adapter import (
    NSEMarketDataProvider,
)

logger = logging.getLogger("sentinews.market_reports.pre_market")


class GeneratePreMarketReportUseCase:
    """
    Orchestrates the generation and publication of an enriched Pre-Market Report.
    """

    def __init__(
        self,
        repository: MarketReportRepositoryPort,
        finnhub_client: Optional[FinnhubMarketClientPort] = None,
        stocknews_client: Optional[StockNewsClientPort] = None,
        global_indices_adapter: Optional[GlobalIndicesPort] = None,
        nse_adapter: Optional[NSEMarketDataPort] = None,
        commodities_adapter: Optional[CommoditiesPort] = None,
        currency_adapter: Optional[CurrencyPort] = None,
        adr_adapter: Optional[IndianADRPort] = None,
        corporate_adapter: Optional[CorporateAnnouncementsPort] = None,
        news_adapter: Optional[MarketReportNewsPort] = None,
        lock_port: Optional[ReportLockPort] = None,
    ):
        self.repository = repository
        self.finnhub_client = finnhub_client or FinnhubClient()
        self.stocknews_client = stocknews_client
        self.global_indices_adapter = global_indices_adapter or GlobalIndicesAdapter(finnhub_fallback=self.finnhub_client)
        self.nse_adapter = nse_adapter or NSEMarketDataProvider()
        self.commodities_adapter = commodities_adapter or CommoditiesAdapter()
        self.currency_adapter = currency_adapter or CurrencyAdapter()
        self.adr_adapter = adr_adapter or IndianADRAdapter()
        self.corporate_adapter = corporate_adapter or CorporateAnnouncementsAdapter()
        self.news_adapter = news_adapter or MarketReportNewsAdapter()
        self.lock_port = lock_port

    async def execute(
        self, report_date: Optional[date] = None, force: bool = False
    ) -> MarketReport:
        target_date = report_date or datetime.now(timezone.utc).date()

        # 1. Idempotency check
        existing = await self.repository.get_by_type_and_date(
            ReportType.PRE_MARKET, target_date
        )
        if existing and existing.status == ReportStatus.PUBLISHED and not force:
            logger.info(
                "Pre-Market report for %s already exists and is PUBLISHED. Skipping regeneration.",
                target_date,
            )
            return existing

        # 2. Concurrency Lock
        if self.lock_port:
            locked = await self.lock_port.acquire_lock(
                ReportType.PRE_MARKET, target_date, ttl_seconds=120
            )
            if not locked and not force:
                logger.warning(
                    "Lock for Pre-Market report %s is currently held by another runner.",
                    target_date,
                )
                if existing:
                    return existing

        source_providers: List[str] = []
        is_partial = False

        # 3. Fetch Global Indices (Field 1)
        global_indices: List[IndexPoint] = []
        try:
            global_indices = await self.global_indices_adapter.get_major_global_indices()
            source_providers.append("global_indices_adapter")
        except Exception as exc:
            logger.warning("Failed fetching global indices in pre-market: %s", exc)
            is_partial = True

        # 4. Fetch Indian Indices & Sectors (Fields 2 & 3)
        indian_indices_prev: List[IndexPerformanceItem] = []
        indian_sectors_prev: List[SectorPerformanceItem] = []
        try:
            indian_indices_prev, indian_sectors_prev = await self.nse_adapter.get_indian_indices()
            source_providers.append("nse_indices")
        except Exception as exc:
            logger.warning("Failed fetching NSE indices in pre-market: %s", exc)
            is_partial = True

        # 5. Fetch Commodities (Field 4)
        commodities: List[CommodityItem] = []
        try:
            commodities = await self.commodities_adapter.get_commodities()
            source_providers.append("commodities_adapter")
        except Exception as exc:
            logger.warning("Failed fetching commodities in pre-market: %s", exc)
            is_partial = True

        # 6. Fetch INR Currency Pairs (Field 5)
        inr_currencies: List[CurrencyPairItem] = []
        try:
            inr_currencies = await self.currency_adapter.get_inr_currency_pairs()
            source_providers.append("currency_adapter")
        except Exception as exc:
            logger.warning("Failed fetching currency pairs in pre-market: %s", exc)
            is_partial = True

        # 7. Fetch Indian ADRs (Field 6)
        indian_adrs: List[ADRItem] = []
        try:
            indian_adrs = await self.adr_adapter.get_indian_adrs()
            source_providers.append("adr_adapter")
        except Exception as exc:
            logger.warning("Failed fetching Indian ADRs in pre-market: %s", exc)
            is_partial = True

        # 8. Fetch FII/DII Previous Day Data (Field 7)
        fii_dii_prev: Optional[FIIDIIData] = None
        try:
            fii_dii_prev = await self.nse_adapter.get_fii_dii_data()
            if fii_dii_prev:
                source_providers.append("nse_fii_dii")
        except Exception as exc:
            logger.warning("Failed fetching FII/DII data in pre-market: %s", exc)
            is_partial = True

        # 9. Fetch Stocks in the News (Field 8)
        stocks_in_news: List[StockInNewsItem] = []
        try:
            stocks_in_news = await self.news_adapter.get_stocks_in_news(limit=8, window_hours=24)
            source_providers.append("live_news_intelligence")
        except Exception as exc:
            logger.warning("Failed fetching stocks in news in pre-market: %s", exc)
            is_partial = True

        # 10. Items 9 & 10: Carry Forward from Yesterday's Post-Market Report
        corporate_events_cf: List[CorporateEventItem] = []
        market_news_cf: List[HeadlineItem] = []
        prev_trading_date = NSETradingCalendar.get_previous_trading_day(target_date)

        try:
            prev_post_market = await self.repository.get_by_type_and_date(
                ReportType.POST_MARKET, prev_trading_date
            )
            if prev_post_market and prev_post_market.sections:
                sec = prev_post_market.sections
                # Extract corporate events
                if "corporate_events" in sec and isinstance(sec["corporate_events"], list):
                    corporate_events_cf = [CorporateEventItem(**e) for e in sec["corporate_events"]]
                # Extract market news
                if "market_news_impact" in sec and isinstance(sec["market_news_impact"], list):
                    market_news_cf = [HeadlineItem(**n) for n in sec["market_news_impact"]]
                elif "key_news_recap" in sec and isinstance(sec["key_news_recap"], list):
                    market_news_cf = [HeadlineItem(**n) for n in sec["key_news_recap"]]

                if corporate_events_cf or market_news_cf:
                    source_providers.append(f"carried_forward:post_market_{prev_trading_date}")
        except Exception as exc:
            logger.warning("Could not carry forward from yesterday's post-market report: %s", exc)

        # Fallback for corporate events if no prior report found
        if not corporate_events_cf:
            try:
                corporate_events_cf = await self.corporate_adapter.get_recent_corporate_events(limit=8)
            except Exception as exc:
                logger.warning("Fallback corporate events fetch failed: %s", exc)

        # Fallback for market news if no prior report found
        if not market_news_cf:
            try:
                market_news_cf = await self.news_adapter.get_market_news(limit=10, window_hours=24)
            except Exception as exc:
                logger.warning("Fallback market news fetch failed: %s", exc)

        # 11. Fetch Today's Economic Calendar & Market Status
        economic_events: List[EconomicEventItem] = []
        market_status_str = "open"
        try:
            if not NSETradingCalendar.is_trading_day(target_date):
                market_status_str = "holiday"
            else:
                m_status = await self.finnhub_client.get_market_status(exchange="IN")
                market_status_str = "open" if m_status.get("isOpen", True) else "closed"

            cal_end_date = target_date if NSETradingCalendar.is_trading_day(target_date) else NSETradingCalendar.get_next_trading_day(target_date)
            cal_raw = await self.finnhub_client.get_economic_calendar(from_date=target_date, to_date=cal_end_date)
            for ev in cal_raw:
                economic_events.append(
                    EconomicEventItem(
                        event=str(ev.get("event", "Economic Event")),
                        country=str(ev.get("country", "Global")),
                        date_time=str(ev.get("time", ev.get("date", str(target_date)))),
                        impact=str(ev.get("impact", "medium")),
                        actual=str(ev.get("actual")) if ev.get("actual") is not None else None,
                        estimate=str(ev.get("estimate")) if ev.get("estimate") is not None else None,
                        previous=str(ev.get("prev")) if ev.get("prev") is not None else None,
                    )
                )
            if economic_events:
                source_providers.append("finnhub_calendar")
        except Exception as exc:
            logger.warning("Failed fetching economic calendar: %s", exc)

        # 12. Build Backwards-Compatible Global Cues
        us_indices = [p for p in global_indices if p.symbol in ("^DJI", "^GSPC", "^IXIC")]
        asian_indices = [p for p in global_indices if p.symbol not in ("^DJI", "^GSPC", "^IXIC")]
        gift_nifty = next((p for p in global_indices if "gift" in p.symbol.lower() or "gift" in p.name.lower()), None)
        global_cues = GlobalCuesSection(
            us_indices=us_indices,
            asian_indices=asian_indices,
            gift_nifty=gift_nifty,
            summary_notes="Overnight global index cues and market trends before market open.",
        )

        # 13. Assemble Full Structured Sections
        sections = PreMarketSections(
            major_global_indices=global_indices,
            indian_indices_prev_close=indian_indices_prev,
            indian_sector_performance_prev=indian_sectors_prev,
            commodities=commodities,
            inr_currency_pairs=inr_currencies,
            indian_adrs=indian_adrs,
            fii_dii_prev_day=fii_dii_prev,
            stocks_in_news=stocks_in_news,
            corporate_events_carried_forward=corporate_events_cf,
            market_news_carried_forward=market_news_cf,
            economic_calendar_today=economic_events,
            market_status=market_status_str,
            global_cues=global_cues,
            key_news_headlines=market_news_cf,
        )

        sections_dict: Dict[str, Any] = {
            "major_global_indices": [p.__dict__ for p in sections.major_global_indices],
            "indian_indices_prev_close": [p.__dict__ for p in sections.indian_indices_prev_close],
            "indian_sector_performance_prev": [p.__dict__ for p in sections.indian_sector_performance_prev],
            "commodities": [c.__dict__ for c in sections.commodities],
            "inr_currency_pairs": [c.__dict__ for c in sections.inr_currency_pairs],
            "indian_adrs": [a.__dict__ for a in sections.indian_adrs],
            "fii_dii_prev_day": sections.fii_dii_prev_day.__dict__ if sections.fii_dii_prev_day else None,
            "stocks_in_news": [s.__dict__ for s in sections.stocks_in_news],
            "corporate_events_carried_forward": [e.__dict__ for e in sections.corporate_events_carried_forward],
            "market_news_carried_forward": [n.__dict__ for n in sections.market_news_carried_forward],
            "economic_calendar_today": [e.__dict__ for e in sections.economic_calendar_today],
            "market_status": sections.market_status,
            # Backwards-compatible aliases
            "global_cues": {
                "us_indices": [p.__dict__ for p in global_cues.us_indices],
                "asian_indices": [p.__dict__ for p in global_cues.asian_indices],
                "gift_nifty": global_cues.gift_nifty.__dict__ if global_cues.gift_nifty else None,
                "summary_notes": global_cues.summary_notes,
            },
            "key_news_headlines": [n.__dict__ for n in sections.market_news_carried_forward],
        }

        # 14. Construct Domain Entity
        report = MarketReport(
            id=existing.id if existing else None,
            report_type=ReportType.PRE_MARKET,
            report_date=target_date,
            status=ReportStatus.PUBLISHED,
            generated_at=datetime.now(timezone.utc),
            sections=sections_dict,
            disclaimer=SEBI_MANDATORY_DISCLAIMER,
            source_providers=list(set(source_providers)),
            is_partial=is_partial,
            error_details=None,
        )

        # 15. Enforce SEBI Compliance Validation
        try:
            validate_market_report_compliance(report)
        except Exception as comp_err:
            logger.error("Pre-Market report failed SEBI compliance validation: %s", comp_err, exc_info=True)
            self._record_apm_crash(comp_err)
            report.status = ReportStatus.FAILED
            report.error_details = str(comp_err)
            saved = await self.repository.save(report)
            if self.lock_port:
                await self.lock_port.release_lock(ReportType.PRE_MARKET, target_date)
            return saved

        # 16. Persist to Database
        saved_report = await self.repository.save(report)
        logger.info(
            "Successfully published enriched Pre-Market report for %s (is_partial=%s, providers=%s)",
            target_date,
            is_partial,
            source_providers,
        )

        # Proactively warm latest report cache (5 min TTL)
        try:
            from app.cache.market_cache import market_cache
            from app.modules.market_reports.application.queries import _report_to_cache_dict
            await market_cache.set("market_report:latest:PRE_MARKET", _report_to_cache_dict(saved_report), ttl_seconds=300)
            if saved_report.id:
                await market_cache.set(f"market_report:id:{saved_report.id}", _report_to_cache_dict(saved_report), ttl_seconds=300)
        except Exception as cache_err:
            logger.debug("Failed warming pre-market report cache: %s", cache_err)

        if self.lock_port:
            await self.lock_port.release_lock(ReportType.PRE_MARKET, target_date)

        return saved_report

    def _record_apm_crash(self, exc: BaseException) -> None:
        try:
            from app.infrastructure.observability.signatures import crash_signature_engine
            from app.infrastructure.observability.metrics import crash_signature_total

            sig_id, loc, prom_sig_id = crash_signature_engine.compute_signature(
                exc, endpoint="market_reports_pre_market_generator"
            )
            crash_signature_total.labels(signature_id=prom_sig_id, location=loc).inc()
        except Exception:
            pass
