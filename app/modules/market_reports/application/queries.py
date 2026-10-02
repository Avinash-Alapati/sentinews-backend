"""
Application Queries for Market Reports.

Provides read-only query use cases with business validation (e.g. 404 on draft/missing reports).
"""

from datetime import date, datetime
import logging
from typing import List, Optional, Tuple

from app.cache.market_cache import market_cache
from app.modules.market_reports.application.ports import MarketReportRepositoryPort
from app.modules.market_reports.domain.entities import MarketReport
from app.modules.market_reports.domain.enums import ReportStatus, ReportType

logger = logging.getLogger("sentinews.market_reports.queries")


def _report_to_cache_dict(report: MarketReport) -> dict:
    return {
        "id": report.id,
        "report_type": report.report_type.value if hasattr(report.report_type, "value") else str(report.report_type),
        "report_date": report.report_date.isoformat(),
        "status": report.status.value if hasattr(report.status, "value") else str(report.status),
        "generated_at": report.generated_at.isoformat(),
        "sections": report.sections,
        "disclaimer": report.disclaimer,
        "source_providers": report.source_providers,
        "is_partial": report.is_partial,
        "error_details": report.error_details,
    }


def _cache_dict_to_report(d: dict) -> MarketReport:
    return MarketReport(
        id=d.get("id"),
        report_type=ReportType(d["report_type"]),
        report_date=date.fromisoformat(d["report_date"]),
        status=ReportStatus(d["status"]),
        generated_at=datetime.fromisoformat(d["generated_at"]),
        sections=d.get("sections", {}),
        disclaimer=d.get("disclaimer", ""),
        source_providers=d.get("source_providers", []),
        is_partial=d.get("is_partial", False),
        error_details=d.get("error_details"),
    )


class ReportNotFoundError(Exception):
    """Raised when a requested published report does not exist."""
    pass


class GetLatestReportQuery:
    """Fetches the most recent PUBLISHED report of a given type with dual-tier caching."""

    def __init__(self, repository: MarketReportRepositoryPort):
        self.repository = repository

    async def execute(self, report_type: ReportType) -> MarketReport:
        type_str = report_type.value if hasattr(report_type, "value") else str(report_type)
        cache_key = f"market_report:latest:{type_str}"

        cached = await market_cache.get(cache_key)
        if cached and isinstance(cached, dict):
            try:
                return _cache_dict_to_report(cached)
            except Exception as e:
                logger.debug("Failed deserializing cached market report %s: %s", cache_key, e)

        report = await self.repository.get_latest_published(report_type)
        if not report or report.status != ReportStatus.PUBLISHED:
            raise ReportNotFoundError(
                f"No published {type_str} report found."
            )

        try:
            await market_cache.set(cache_key, _report_to_cache_dict(report), ttl_seconds=300)
        except Exception as e:
            logger.debug("Failed caching market report %s: %s", cache_key, e)

        return report


class GetReportByIdQuery:
    """Fetches a published report by ID with caching."""

    def __init__(self, repository: MarketReportRepositoryPort):
        self.repository = repository

    async def execute(self, report_id: int) -> MarketReport:
        cache_key = f"market_report:id:{report_id}"
        cached = await market_cache.get(cache_key)
        if cached and isinstance(cached, dict):
            try:
                return _cache_dict_to_report(cached)
            except Exception as e:
                logger.debug("Failed deserializing cached market report %s: %s", cache_key, e)

        report = await self.repository.get_by_id(report_id)
        if not report or report.status != ReportStatus.PUBLISHED:
            raise ReportNotFoundError(
                f"Report with ID {report_id} not found or is not published."
            )

        try:
            await market_cache.set(cache_key, _report_to_cache_dict(report), ttl_seconds=300)
        except Exception as e:
            logger.debug("Failed caching market report %s: %s", cache_key, e)

        return report


class ListReportsQuery:
    """Fetches a paginated list of published reports with optional filters."""

    def __init__(self, repository: MarketReportRepositoryPort):
        self.repository = repository

    async def execute(
        self,
        report_type: Optional[ReportType] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Tuple[List[MarketReport], int]:
        return await self.repository.list_published(
            report_type=report_type,
            start_date=start_date,
            end_date=end_date,
            offset=offset,
            limit=limit,
        )
