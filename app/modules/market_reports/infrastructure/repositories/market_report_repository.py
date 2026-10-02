"""
SQLAlchemy Repository implementing the MarketReportRepositoryPort.
"""

from datetime import date
import logging
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.market_report import MarketReportORM
from app.modules.market_reports.application.ports import MarketReportRepositoryPort
from app.modules.market_reports.domain.entities import MarketReport
from app.modules.market_reports.domain.enums import ReportStatus, ReportType
from app.modules.market_reports.infrastructure.mappers import (
    market_report_domain_to_orm,
    market_report_orm_to_domain,
)

logger = logging.getLogger("sentinews.market_reports.repository")


class SQLAlchemyMarketReportRepository(MarketReportRepositoryPort):
    """
    Asynchronous SQLAlchemy repository for MarketReport persistence.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, report: MarketReport) -> MarketReport:
        type_str = report.report_type.value if hasattr(report.report_type, "value") else str(report.report_type)
        status_str = report.status.value if hasattr(report.status, "value") else str(report.status)

        orm: Optional[MarketReportORM] = None

        if report.id:
            result = await self.session.execute(
                select(MarketReportORM).where(MarketReportORM.id == report.id)
            )
            orm = result.scalar_one_or_none()

        if orm is None:
            # Check unique by (report_type, report_date)
            result = await self.session.execute(
                select(MarketReportORM).where(
                    MarketReportORM.report_type == type_str,
                    MarketReportORM.report_date == report.report_date,
                )
            )
            orm = result.scalar_one_or_none()

        if orm:
            orm.report_type = type_str
            orm.report_date = report.report_date
            orm.status = status_str
            orm.generated_at = report.generated_at
            orm.sections = report.sections
            orm.disclaimer = report.disclaimer
            orm.source_providers = report.source_providers
            orm.is_partial = report.is_partial
            orm.error_details = report.error_details
        else:
            orm = market_report_domain_to_orm(report)
            self.session.add(orm)

        await self.session.commit()
        await self.session.refresh(orm)
        return market_report_orm_to_domain(orm)

    async def get_by_id(self, report_id: int) -> Optional[MarketReport]:
        result = await self.session.execute(
            select(MarketReportORM).where(MarketReportORM.id == report_id)
        )
        orm = result.scalar_one_or_none()
        return market_report_orm_to_domain(orm) if orm else None

    async def get_by_type_and_date(
        self, report_type: ReportType, report_date: date
    ) -> Optional[MarketReport]:
        type_str = report_type.value if hasattr(report_type, "value") else str(report_type)
        result = await self.session.execute(
            select(MarketReportORM).where(
                MarketReportORM.report_type == type_str,
                MarketReportORM.report_date == report_date,
            )
        )
        orm = result.scalar_one_or_none()
        return market_report_orm_to_domain(orm) if orm else None

    async def get_latest_published(
        self, report_type: ReportType
    ) -> Optional[MarketReport]:
        type_str = report_type.value if hasattr(report_type, "value") else str(report_type)
        result = await self.session.execute(
            select(MarketReportORM)
            .where(
                MarketReportORM.report_type == type_str,
                MarketReportORM.status == ReportStatus.PUBLISHED.value,
            )
            .order_by(MarketReportORM.report_date.desc(), MarketReportORM.id.desc())
            .limit(1)
        )
        orm = result.scalar_one_or_none()
        return market_report_orm_to_domain(orm) if orm else None

    async def list_published(
        self,
        report_type: Optional[ReportType] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Tuple[List[MarketReport], int]:
        query = select(MarketReportORM).where(
            MarketReportORM.status == ReportStatus.PUBLISHED.value
        )

        if report_type:
            type_str = report_type.value if hasattr(report_type, "value") else str(report_type)
            query = query.where(MarketReportORM.report_type == type_str)
        if start_date:
            query = query.where(MarketReportORM.report_date >= start_date)
        if end_date:
            query = query.where(MarketReportORM.report_date <= end_date)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total_count = (await self.session.execute(count_query)).scalar_one()

        # Paginated items
        paginated_query = (
            query.order_by(MarketReportORM.report_date.desc(), MarketReportORM.id.desc())
            .offset(offset)
            .limit(limit)
        )
        results = (await self.session.execute(paginated_query)).scalars().all()
        reports = [market_report_orm_to_domain(orm) for orm in results]

        return reports, total_count
