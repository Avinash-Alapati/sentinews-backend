"""
Use Case: Generate Enriched Post-Market Report.

Orchestrates gathering:
1. Indian indices — that day's closing price (Nifty 50, Sensex, Bank Nifty, etc.)
2. Top gainers and losers — that day (with percentage change and volume)
3. Sector-wise performance — that day (Nifty IT, Auto, Pharma, FMCG, Metal, etc.)
4. FII/DII data — that day's institutional trading disclosures
5. Commodities — last closed price (with international_benchmark source flag)
6. Corporate events — announcements and results during market hours
7. Stocks in the news — that day (derived from News Intelligence pipeline)
8. Market news impacting the market — that day's major macroeconomic stories
9. Tomorrow's watch items & economic calendar

Implements fail-open vendor handling, SEBI compliance validation, idempotency,
and APM crash signature emission on hard failure.
"""

from datetime import date, datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional

from app.modules.market_reports.application.ports import (
    CommoditiesPort,
    CorporateAnnouncementsPort,
    FinnhubMarketClientPort,
    MarketReportNewsPort,
    MarketReportRepositoryPort,
    NSEMarketDataPort,
    ReportLockPort,
    StockNewsClientPort,
)
from app.modules.market_reports.domain.entities import (
    CommodityItem,
    CorporateEventItem,
    EconomicEventItem,
    FIIDIIData,
    HeadlineItem,
    IndexPerformanceItem,
    MarketReport,
    PostMarketSections,
    SEBI_MANDATORY_DISCLAIMER,
    SectorPerformanceItem,
    StockInNewsItem,
    TopMoverItem,
)
from app.modules.market_reports.domain.enums import ReportStatus, ReportType
from app.modules.market_reports.domain.services.compliance import (
    filter_compliant_headlines,
    validate_market_report_compliance,
)
from app.modules.market_reports.domain.services.trading_calendar import NSETradingCalendar
from app.modules.market_reports.infrastructure.adapters.commodities_adapter import CommoditiesAdapter
from app.modules.market_reports.infrastructure.adapters.corporate_announcements_adapter import (
    CorporateAnnouncementsAdapter,
)
from app.modules.market_reports.infrastructure.adapters.finnhub_client import FinnhubClient
from app.modules.market_reports.infrastructure.adapters.news_intelligence_adapter import (
    MarketReportNewsAdapter,
)
from app.modules.market_reports.infrastructure.adapters.nse_market_data_adapter import (
    NSEMarketDataProvider,
)

logger = logging.getLogger("sentinews.market_reports.post_market")


class GeneratePostMarketReportUseCase:
    """
    Orchestrates the generation and publication of an enriched Post-Market Report.
    """

    def __init__(
        self,
        repository: MarketReportRepositoryPort,
        finnhub_client: Optional[FinnhubMarketClientPort] = None,
        stocknews_client: Optional[StockNewsClientPort] = None,
        nse_adapter: Optional[NSEMarketDataPort] = None,
        commodities_adapter: Optional[CommoditiesPort] = None,
        corporate_adapter: Optional[CorporateAnnouncementsPort] = None,
        news_adapter: Optional[MarketReportNewsPort] = None,
        lock_port: Optional[ReportLockPort] = None,
    ):
        self.repository = repository
        self.finnhub_client = finnhub_client or FinnhubClient()
        self.stocknews_client = stocknews_client
        self.nse_adapter = nse_adapter or NSEMarketDataProvider()
        self.commodities_adapter = commodities_adapter or CommoditiesAdapter()
        self.corporate_adapter = corporate_adapter or CorporateAnnouncementsAdapter()
        self.news_adapter = news_adapter or MarketReportNewsAdapter()
        self.lock_port = lock_port

    async def execute(
        self, report_date: Optional[date] = None, force: bool = False
    ) -> MarketReport:
        target_date = report_date or datetime.now(timezone.utc).date()
        next_trading_day = NSETradingCalendar.get_next_trading_day(target_date)

        # 1. Idempotency check
        existing = await self.repository.get_by_type_and_date(
            ReportType.POST_MARKET, target_date
        )
        if existing and existing.status == ReportStatus.PUBLISHED and not force:
            logger.info(
                "Post-Market report for %s already exists and is PUBLISHED. Skipping regeneration.",
                target_date,
            )
            return existing

        # 2. Concurrency Lock
        if self.lock_port:
            locked = await self.lock_port.acquire_lock(
                ReportType.POST_MARKET, target_date, ttl_seconds=120
            )
            if not locked and not force:
                logger.warning(
                    "Lock for Post-Market report %s is currently held by another runner.",
                    target_date,
                )
                if existing:
                    return existing

        source_providers: List[str] = []
        is_partial = False

        # 3. Fetch Indian Indices & Sector Performance (Fields 1 & 3)
        indian_indices: List[IndexPerformanceItem] = []
        sector_performance: List[SectorPerformanceItem] = []
        try:
            indian_indices, sector_performance = await self.nse_adapter.get_indian_indices()
            source_providers.append("nse_indices")
        except Exception as exc:
            logger.warning("Failed fetching closing Indian indices: %s", exc)
            is_partial = True

        # 4. Fetch Top Gainers and Losers (Field 2)
        top_gainers: List[TopMoverItem] = []
        top_losers: List[TopMoverItem] = []
        try:
            top_gainers, top_losers = await self.nse_adapter.get_top_gainers_and_losers()
            source_providers.append("nse_movers")
        except Exception as exc:
            logger.warning("Failed fetching NSE top movers: %s", exc)
            is_partial = True

        # 5. Fetch FII/DII Trading Data (Field 4)
        fii_dii: Optional[FIIDIIData] = None
        try:
            fii_dii = await self.nse_adapter.get_fii_dII_data() if hasattr(self.nse_adapter, "get_fii_dII_data") else await self.nse_adapter.get_fii_dii_data()
            if fii_dii:
                source_providers.append("nse_fii_dii")
        except Exception as exc:
            logger.warning("Failed fetching closing FII/DII data: %s", exc)
            is_partial = True

        # 6. Fetch Commodities Close (Field 5)
        commodities_close: List[CommodityItem] = []
        try:
            commodities_close = await self.commodities_adapter.get_commodities()
            source_providers.append("commodities_adapter")
        except Exception as exc:
            logger.warning("Failed fetching commodities close: %s", exc)
            is_partial = True

        # 7. Fetch Corporate Events occurring during/after market hours (Field 6)
        corporate_events: List[CorporateEventItem] = []
        try:
            corporate_events = await self.corporate_adapter.get_recent_corporate_events(limit=15)
            source_providers.append("exchange_corporate_filings")
        except Exception as exc:
            logger.warning("Failed fetching corporate events in post-market: %s", exc)
            is_partial = True

        # 8. Fetch Stocks in the News that day (Field 7)
        stocks_in_news: List[StockInNewsItem] = []
        try:
            stocks_in_news = await self.news_adapter.get_stocks_in_news(limit=10, window_hours=12)
            source_providers.append("live_news_intelligence")
        except Exception as exc:
            logger.warning("Failed fetching stocks in news in post-market: %s", exc)
            is_partial = True

        # 9. Fetch Market News impacting the session (Field 8)
        market_news: List[HeadlineItem] = []
        try:
            market_news = await self.news_adapter.get_market_news(limit=12, window_hours=12)
            source_providers.append("live_news_intelligence")
        except Exception as exc:
            logger.warning("Failed fetching market news in post-market: %s", exc)
            is_partial = True

        # 10. Fetch Tomorrow's Watch Items & Economic Calendar
        watch_tomorrow: List[EconomicEventItem] = []
        try:
            cal_raw = await self.finnhub_client.get_economic_calendar(
                from_date=next_trading_day, to_date=next_trading_day + timedelta(days=1)
            )
            for ev in cal_raw:
                watch_tomorrow.append(
                    EconomicEventItem(
                        event=str(ev.get("event", "Economic Event")),
                        country=str(ev.get("country", "Global")),
                        date_time=str(ev.get("time", ev.get("date", str(next_trading_day)))),
                        impact=str(ev.get("impact", "medium")),
                        actual=str(ev.get("actual")) if ev.get("actual") is not None else None,
                        estimate=str(ev.get("estimate")) if ev.get("estimate") is not None else None,
                        previous=str(ev.get("prev")) if ev.get("prev") is not None else None,
                    )
                )
            if watch_tomorrow:
                source_providers.append("finnhub_calendar")
        except Exception as exc:
            logger.warning("Failed fetching tomorrow economic calendar: %s", exc)

        # 11. Assemble Backwards-Compatible Top Movers List
        combined_movers = top_gainers + top_losers

        # 12. Assemble Full Post-Market Sections
        sections = PostMarketSections(
            indian_indices_close=indian_indices,
            top_gainers=top_gainers,
            top_losers=top_losers,
            sector_performance=sector_performance,
            fii_dii_data=fii_dii,
            commodities_close=commodities_close,
            corporate_events=corporate_events,
            stocks_in_news=stocks_in_news,
            market_news_impact=market_news,
            watch_tomorrow=watch_tomorrow,
            # Backwards-compatible aliases
            index_performance=indian_indices,
            top_movers=combined_movers,
            key_news_recap=market_news,
            volume_summary={
                "market_trend": "positive" if sum(1 for g in top_gainers) >= sum(1 for l in top_losers) else "cautious",
                "advances_vs_declines": f"{len(top_gainers)} gainers vs {len(top_losers)} losers in broad basket",
            },
        )

        sections_dict: Dict[str, Any] = {
            "indian_indices_close": [p.__dict__ for p in sections.indian_indices_close],
            "top_gainers": [g.__dict__ for g in sections.top_gainers],
            "top_losers": [l.__dict__ for l in sections.top_losers],
            "sector_performance": [s.__dict__ for s in sections.sector_performance],
            "fii_dii_data": sections.fii_dii_data.__dict__ if sections.fii_dii_data else None,
            "commodities_close": [c.__dict__ for c in sections.commodities_close],
            "corporate_events": [e.__dict__ for e in sections.corporate_events],
            "stocks_in_news": [s.__dict__ for s in sections.stocks_in_news],
            "market_news_impact": [n.__dict__ for n in sections.market_news_impact],
            "watch_tomorrow": [e.__dict__ for e in sections.watch_tomorrow],
            # Backwards-compatible aliases
            "index_performance": [p.__dict__ for p in sections.indian_indices_close],
            "top_movers": [m.__dict__ for m in combined_movers],
            "key_news_recap": [n.__dict__ for n in sections.market_news_impact],
            "volume_summary": sections.volume_summary,
        }

        # 13. Construct Domain Entity
        report = MarketReport(
            id=existing.id if existing else None,
            report_type=ReportType.POST_MARKET,
            report_date=target_date,
            status=ReportStatus.PUBLISHED,
            generated_at=datetime.now(timezone.utc),
            sections=sections_dict,
            disclaimer=SEBI_MANDATORY_DISCLAIMER,
            source_providers=list(set(source_providers)),
            is_partial=is_partial,
            error_details=None,
        )

        # 14. Enforce SEBI Compliance Validation
        try:
            validate_market_report_compliance(report)
        except Exception as comp_err:
            logger.error("Post-Market report failed SEBI compliance validation: %s", comp_err, exc_info=True)
            self._record_apm_crash(comp_err)
            report.status = ReportStatus.FAILED
            report.error_details = str(comp_err)
            saved = await self.repository.save(report)
            if self.lock_port:
                await self.lock_port.release_lock(ReportType.POST_MARKET, target_date)
            return saved

        # 15. Persist to Database
        saved_report = await self.repository.save(report)
        logger.info(
            "Successfully published enriched Post-Market report for %s (is_partial=%s, providers=%s)",
            target_date,
            is_partial,
            source_providers,
        )

        # Proactively warm latest report cache (5 min TTL)
        try:
            from app.cache.market_cache import market_cache
            from app.modules.market_reports.application.queries import _report_to_cache_dict
            await market_cache.set("market_report:latest:POST_MARKET", _report_to_cache_dict(saved_report), ttl_seconds=300)
            if saved_report.id:
                await market_cache.set(f"market_report:id:{saved_report.id}", _report_to_cache_dict(saved_report), ttl_seconds=300)
        except Exception as cache_err:
            logger.debug("Failed warming post-market report cache: %s", cache_err)

        if self.lock_port:
            await self.lock_port.release_lock(ReportType.POST_MARKET, target_date)

        return saved_report

    def _record_apm_crash(self, exc: BaseException) -> None:
        try:
            from app.infrastructure.observability.signatures import crash_signature_engine
            from app.infrastructure.observability.metrics import crash_signature_total

            sig_id, loc, prom_sig_id = crash_signature_engine.compute_signature(
                exc, endpoint="market_reports_post_market_generator"
            )
            crash_signature_total.labels(signature_id=prom_sig_id, location=loc).inc()
        except Exception:
            pass
