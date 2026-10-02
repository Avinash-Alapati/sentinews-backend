"""
Use Case: Generate Global Pre-Market Report.

Orchestrates fetching worldwide benchmark indices & futures (US, Europe, Asia),
commodities & FX, global economic calendar, and international financial headlines.
Implements fail-open vendor handling, strict SEBI compliance validation, idempotency,
and APM crash signature emission on hard failure.
"""

from datetime import date, datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from app.modules.market_reports.application.ports import (
    FinnhubMarketClientPort,
    MarketReportRepositoryPort,
    ReportLockPort,
    StockNewsClientPort,
)
from app.modules.market_reports.domain.entities import (
    EconomicEventItem,
    HeadlineItem,
    IndexPoint,
    MarketReport,
    SEBI_MANDATORY_DISCLAIMER,
)
from app.modules.market_reports.domain.enums import ReportStatus, ReportType
from app.modules.market_reports.domain.services.compliance import (
    filter_compliant_headlines,
    validate_market_report_compliance,
)

logger = logging.getLogger("sentinews.market_reports.global_pre_market")


class GenerateGlobalPreMarketReportUseCase:
    """
    Orchestrates the generation and publication of a Global Pre-Market Report.
    """

    def __init__(
        self,
        repository: MarketReportRepositoryPort,
        finnhub_client: FinnhubMarketClientPort,
        stocknews_client: StockNewsClientPort,
        lock_port: Optional[ReportLockPort] = None,
    ):
        self.repository = repository
        self.finnhub_client = finnhub_client
        self.stocknews_client = stocknews_client
        self.lock_port = lock_port

    async def execute(
        self, report_date: Optional[date] = None, force: bool = False
    ) -> MarketReport:
        target_date = report_date or datetime.now(timezone.utc).date()

        # 1. Idempotency check: Skip if already published and not forced
        existing = await self.repository.get_by_type_and_date(
            ReportType.GLOBAL_PRE_MARKET, target_date
        )
        if existing and existing.status == ReportStatus.PUBLISHED and not force:
            logger.info(
                "Global Pre-Market report for %s already exists and is PUBLISHED. Skipping regeneration.",
                target_date,
            )
            return existing

        # 2. Concurrency Lock
        if self.lock_port:
            locked = await self.lock_port.acquire_lock(
                ReportType.GLOBAL_PRE_MARKET, target_date, ttl_seconds=120
            )
            if not locked and not force:
                logger.warning(
                    "Lock for Global Pre-Market report %s is currently held by another runner.",
                    target_date,
                )
                if existing:
                    return existing

        finnhub_failed = False
        stocknews_failed = False
        source_providers: List[str] = []

        global_indices_raw: List[Dict[str, Any]] = []
        commodities_fx_raw: List[Dict[str, Any]] = []
        economic_calendar_raw: List[Dict[str, Any]] = []
        headlines_raw: List[HeadlineItem] = []

        # 3. Fetch from Finnhub (Global indices, commodities & FX, global economic calendar)
        try:
            global_indices_raw = await self.finnhub_client.get_extended_global_indices()
            commodities_fx_raw = await self.finnhub_client.get_commodities_and_fx()
            economic_calendar_raw = await self.finnhub_client.get_economic_calendar(
                from_date=target_date, to_date=target_date
            )
            source_providers.append("finnhub")
        except Exception as exc:
            finnhub_failed = True
            logger.warning("Finnhub provider call failed during global pre-market generation: %s", exc)

        # 4. Fetch from Stock News API (Global headlines)
        try:
            headlines_raw = await self.stocknews_client.get_top_market_news(limit=15)
            source_providers.append("stocknews")
        except Exception as exc:
            stocknews_failed = True
            logger.warning("Stock News API call failed during global pre-market generation: %s", exc)

        # 5. Fail-open assessment
        if finnhub_failed and stocknews_failed:
            err_msg = "Hard failure: Both Finnhub and Stock News API failed to return data for Global Pre-Market."
            logger.error(
                err_msg,
                extra={
                    "report_type": ReportType.GLOBAL_PRE_MARKET.value,
                    "report_date": str(target_date),
                    "error_code": "GLOBAL_MARKET_REPORT_GENERATION_FAILED",
                },
                exc_info=True,
            )
            self._record_apm_crash(RuntimeError(err_msg))

            failed_report = MarketReport(
                id=existing.id if existing else None,
                report_type=ReportType.GLOBAL_PRE_MARKET,
                report_date=target_date,
                status=ReportStatus.FAILED,
                disclaimer=SEBI_MANDATORY_DISCLAIMER,
                source_providers=[],
                is_partial=False,
                error_details=err_msg,
            )
            saved_failed = await self.repository.save(failed_report)
            if self.lock_port:
                await self.lock_port.release_lock(ReportType.GLOBAL_PRE_MARKET, target_date)
            return saved_failed

        is_partial = finnhub_failed or stocknews_failed

        # 6. Assemble Global Indices
        global_indices: List[IndexPoint] = []
        for item in global_indices_raw:
            global_indices.append(
                IndexPoint(
                    symbol=item.get("symbol", ""),
                    name=item.get("name", item.get("symbol", "")),
                    last_price=float(item.get("last_price", item.get("c", 0.0))),
                    change=float(item.get("change", item.get("d", 0.0))),
                    change_percent=float(item.get("change_percent", item.get("dp", 0.0))),
                )
            )

        # 7. Assemble Commodities and FX
        commodities_and_fx: List[IndexPoint] = []
        for item in commodities_fx_raw:
            commodities_and_fx.append(
                IndexPoint(
                    symbol=item.get("symbol", ""),
                    name=item.get("name", item.get("symbol", "")),
                    last_price=float(item.get("last_price", item.get("c", 0.0))),
                    change=float(item.get("change", item.get("d", 0.0))),
                    change_percent=float(item.get("change_percent", item.get("dp", 0.0))),
                )
            )

        # 8. Assemble Economic Calendar
        economic_events: List[EconomicEventItem] = []
        for ev in economic_calendar_raw:
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

        # 9. Filter Compliant Headlines
        compliant_headlines = filter_compliant_headlines(headlines_raw)

        sections_dict: Dict[str, Any] = {
            "global_indices": [p.__dict__ for p in global_indices],
            "commodities_and_fx": [p.__dict__ for p in commodities_and_fx],
            "key_news_headlines": [h.__dict__ for h in compliant_headlines],
            "economic_calendar_today": [e.__dict__ for e in economic_events],
            "summary_notes": "Worldwide benchmark index movements, commodity prices, and macro releases."
            if not finnhub_failed
            else "Global market data temporarily unavailable from primary vendor.",
        }

        # 10. Construct Domain Report Entity
        report = MarketReport(
            id=existing.id if existing else None,
            report_type=ReportType.GLOBAL_PRE_MARKET,
            report_date=target_date,
            status=ReportStatus.PUBLISHED,
            generated_at=datetime.now(timezone.utc),
            sections=sections_dict,
            disclaimer=SEBI_MANDATORY_DISCLAIMER,
            source_providers=source_providers,
            is_partial=is_partial,
            error_details=None,
        )

        # 11. Strict SEBI Compliance Validation
        try:
            validate_market_report_compliance(report)
        except Exception as comp_err:
            logger.error("Global Pre-Market report failed SEBI compliance: %s", comp_err, exc_info=True)
            self._record_apm_crash(comp_err)
            report.status = ReportStatus.FAILED
            report.error_details = str(comp_err)
            saved = await self.repository.save(report)
            if self.lock_port:
                await self.lock_port.release_lock(ReportType.GLOBAL_PRE_MARKET, target_date)
            return saved

        # 12. Persist published report
        saved_report = await self.repository.save(report)
        logger.info(
            "Successfully published Global Pre-Market report for %s (is_partial=%s, providers=%s)",
            target_date,
            is_partial,
            source_providers,
        )

        # Proactively warm latest report cache (5 min TTL)
        try:
            from app.cache.market_cache import market_cache
            from app.modules.market_reports.application.queries import _report_to_cache_dict
            await market_cache.set("market_report:latest:GLOBAL_PRE_MARKET", _report_to_cache_dict(saved_report), ttl_seconds=300)
            if saved_report.id:
                await market_cache.set(f"market_report:id:{saved_report.id}", _report_to_cache_dict(saved_report), ttl_seconds=300)
        except Exception as cache_err:
            logger.debug("Failed warming global pre-market report cache: %s", cache_err)

        if self.lock_port:
            await self.lock_port.release_lock(ReportType.GLOBAL_PRE_MARKET, target_date)

        return saved_report

    def _record_apm_crash(self, exc: BaseException) -> None:
        """Records exception into APM crash signature engine."""
        try:
            from app.infrastructure.observability.signatures import crash_signature_engine
            from app.infrastructure.observability.metrics import crash_signature_total

            sig_id, loc, prom_sig_id = crash_signature_engine.compute_signature(
                exc, endpoint="market_reports_global_pre_market_generator"
            )
            crash_signature_total.labels(signature_id=prom_sig_id, location=loc).inc()
        except Exception:
            pass
