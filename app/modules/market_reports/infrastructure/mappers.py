"""
Mappers translating between MarketReport SQLAlchemy ORM models and Domain Entities.
"""

from app.db.models.market_report import MarketReportORM
from app.modules.market_reports.domain.entities import MarketReport
from app.modules.market_reports.domain.enums import ReportStatus, ReportType


def market_report_orm_to_domain(orm: MarketReportORM) -> MarketReport:
    """Converts a MarketReportORM database model to a MarketReport domain entity."""
    return MarketReport(
        id=orm.id,
        report_type=ReportType(orm.report_type),
        report_date=orm.report_date,
        status=ReportStatus(orm.status),
        generated_at=orm.generated_at,
        sections=dict(orm.sections or {}),
        disclaimer=orm.disclaimer,
        source_providers=list(orm.source_providers or []),
        is_partial=bool(orm.is_partial),
        error_details=orm.error_details,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )


def market_report_domain_to_orm(domain: MarketReport) -> MarketReportORM:
    """Converts a MarketReport domain entity to a MarketReportORM database model."""
    return MarketReportORM(
        id=domain.id,
        report_type=domain.report_type.value if hasattr(domain.report_type, "value") else str(domain.report_type),
        report_date=domain.report_date,
        status=domain.status.value if hasattr(domain.status, "value") else str(domain.status),
        generated_at=domain.generated_at,
        sections=domain.sections,
        disclaimer=domain.disclaimer,
        source_providers=domain.source_providers,
        is_partial=domain.is_partial,
        error_details=domain.error_details,
    )
