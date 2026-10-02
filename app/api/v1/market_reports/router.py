"""
Market Reports API Router.

SEBI Compliance & Architecture Rules:
1. Every response schema inherits from CompliantReportResponseBase and includes the mandatory SEBI disclaimer.
2. All public responses expose source_providers and is_partial flags.
3. Only PUBLISHED reports are exposed publicly (404 returned for drafts or non-existent reports).
4. Manual trigger endpoint is protected by admin / internal authentication at /internal/market-reports/generate.
"""

from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.v1.auth.dependencies import (
    get_current_active_user,
    get_token_from_header_or_oauth2,
    get_user_repository,
)
from app.api.v1.market_reports.dependencies import (
    get_generate_global_post_market_use_case,
    get_generate_global_pre_market_use_case,
    get_generate_post_market_use_case,
    get_generate_pre_market_use_case,
    get_latest_report_query,
    get_list_reports_query,
    get_report_by_id_query,
)
from app.core.config import settings
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import decode_access_token
from app.modules.auth.infrastructure.repositories.user_repository import SQLAlchemyUserRepository
from app.modules.market_reports.application.queries import (
    GetLatestReportQuery,
    GetReportByIdQuery,
    ListReportsQuery,
    ReportNotFoundError,
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
    MarketReport,
    SEBI_MANDATORY_DISCLAIMER,
)
from app.modules.market_reports.domain.enums import ReportStatus, ReportType

logger = logging.getLogger("sentinews.api.market_reports")

router = APIRouter(
    prefix="/market-reports",
    tags=["Market Reports"],
    dependencies=[Depends(get_current_active_user)],
)


# ============================================================================
# Response & Request Schemas
# ============================================================================

class CompliantReportResponseBase(BaseModel):
    """
    Base response schema guaranteeing every market report response carries
    the statutory SEBI disclaimer and vendor attribution metadata.
    """
    disclaimer: str = Field(
        default=SEBI_MANDATORY_DISCLAIMER,
        description="Mandatory SEBI regulatory disclaimer",
    )
    source_providers: List[str] = Field(
        default_factory=lambda: ["finnhub", "stocknews"],
        description="Third-party vendor data sources",
    )


class MarketReportResponse(CompliantReportResponseBase):
    """
    Detailed Market Report response with structured sections and degradation flag.
    """
    id: int
    report_type: str
    report_date: date
    status: str
    generated_at: datetime
    sections: Dict[str, Any] = Field(default_factory=dict)
    is_partial: bool = Field(
        default=False,
        description="True if report was generated with partial vendor data due to upstream outage",
    )


class MarketReportSummaryResponse(CompliantReportResponseBase):
    """
    Summary view of a market report for paginated listings.
    """
    id: int
    report_type: str
    report_date: date
    status: str
    generated_at: datetime
    is_partial: bool = False


class PaginatedMarketReportsResponse(CompliantReportResponseBase):
    """
    Paginated list of published market reports.
    """
    items: List[MarketReportSummaryResponse] = Field(default_factory=list)
    page: int
    limit: int
    total: int


class ManualGenerateRequest(BaseModel):
    """
    Admin payload for manually triggering or backfilling a report.
    """
    report_type: ReportType = Field(default=ReportType.PRE_MARKET)
    report_date: Optional[date] = Field(default=None, description="Report date (defaults to today)")
    force: bool = Field(default=False, description="Force re-generation even if report already exists")


# ============================================================================
# Admin / Internal Authentication Helper
# ============================================================================

async def verify_admin_or_internal_auth(
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret"),
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    token: Optional[str] = Depends(get_token_from_header_or_oauth2),
    user_repo: SQLAlchemyUserRepository = Depends(get_user_repository),
) -> None:
    """
    Allows execution if caller provides a valid internal secret or is an active superuser.
    """
    # 1. Internal secret check (for scheduler/automated scripts)
    secret_candidate = x_internal_secret or x_internal_token
    if secret_candidate:
        expected_secret = getattr(settings, "INTERNAL_API_SECRET", "")
        import hmac
        if expected_secret and hmac.compare_digest(secret_candidate, expected_secret):
            return

    # 2. Authenticated superuser check via Bearer token
    if token:
        try:
            payload = decode_access_token(
                token=token,
                secret_key=settings.SECRET_KEY,
                algorithm=settings.ALGORITHM,
            )
            user_id_str = payload.get("sub")
            if user_id_str:
                if user_id_str.isdigit():
                    user = await user_repo.get_by_id(int(user_id_str))
                else:
                    user = await user_repo.get_by_email(user_id_str)
                if user and user.is_active and user.is_superuser:
                    return
        except Exception:
            pass

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin or internal privileges required to execute manual report generation.",
    )


def _to_response(report: MarketReport) -> MarketReportResponse:
    """Helper to convert domain MarketReport entity into MarketReportResponse."""
    return MarketReportResponse(
        id=report.id or 0,
        report_type=report.report_type.value if hasattr(report.report_type, "value") else str(report.report_type),
        report_date=report.report_date,
        status=report.status.value if hasattr(report.status, "value") else str(report.status),
        generated_at=report.generated_at,
        sections=report.sections,
        disclaimer=report.disclaimer,
        source_providers=report.source_providers,
        is_partial=report.is_partial,
    )


# ============================================================================
# Public Endpoints (Protected by JWT Authentication)
# ============================================================================

@router.get(
    "/pre-market/latest",
    response_model=MarketReportResponse,
    summary="Get Latest Published Pre-Market Report",
)
async def get_latest_pre_market_report(
    current_user: User = Depends(get_current_active_user),
    query: GetLatestReportQuery = Depends(get_latest_report_query),
):
    """
    Fetches the most recently published Domestic Pre-Market Report.
    """
    try:
        report = await query.execute(ReportType.PRE_MARKET)
        return _to_response(report)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get(
    "/post-market/latest",
    response_model=MarketReportResponse,
    summary="Get Latest Published Post-Market Report",
)
async def get_latest_post_market_report(
    current_user: User = Depends(get_current_active_user),
    query: GetLatestReportQuery = Depends(get_latest_report_query),
):
    """
    Fetches the most recently published Domestic Post-Market Report.
    """
    try:
        report = await query.execute(ReportType.POST_MARKET)
        return _to_response(report)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get(
    "/global/pre-market/latest",
    response_model=MarketReportResponse,
    summary="Get Latest Published Global Pre-Market Report",
)
async def get_latest_global_pre_market_report(
    current_user: User = Depends(get_current_active_user),
    query: GetLatestReportQuery = Depends(get_latest_report_query),
):
    """
    Fetches the most recently published Global Pre-Market Report.
    """
    try:
        report = await query.execute(ReportType.GLOBAL_PRE_MARKET)
        return _to_response(report)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get(
    "/global/post-market/latest",
    response_model=MarketReportResponse,
    summary="Get Latest Published Global Post-Market Report",
)
async def get_latest_global_post_market_report(
    current_user: User = Depends(get_current_active_user),
    query: GetLatestReportQuery = Depends(get_latest_report_query),
):
    """
    Fetches the most recently published Global Post-Market Report.
    """
    try:
        report = await query.execute(ReportType.GLOBAL_POST_MARKET)
        return _to_response(report)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get(
    "/{report_id}",
    response_model=MarketReportResponse,
    summary="Get Published Market Report by ID",
)
async def get_market_report_by_id(
    report_id: int,
    current_user: User = Depends(get_current_active_user),
    query: GetReportByIdQuery = Depends(get_report_by_id_query),
):
    """
    Fetches a specific published market report by ID.
    Returns 404 if the report is not found or is not published.
    """
    try:
        report = await query.execute(report_id)
        return _to_response(report)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get(
    "",
    response_model=PaginatedMarketReportsResponse,
    summary="List Published Market Reports (Paginated)",
)
async def list_market_reports(
    report_type: Optional[ReportType] = Query(None, description="Filter by PRE_MARKET, POST_MARKET, GLOBAL_PRE_MARKET, or GLOBAL_POST_MARKET"),
    start_date: Optional[date] = Query(None, description="Filter reports from this date"),
    end_date: Optional[date] = Query(None, description="Filter reports up to this date"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(get_current_active_user),
    query: ListReportsQuery = Depends(get_list_reports_query),
):
    """
    Retrieves a paginated list of published market reports with optional filters.
    """
    offset = (page - 1) * limit
    reports, total = await query.execute(
        report_type=report_type,
        start_date=start_date,
        end_date=end_date,
        offset=offset,
        limit=limit,
    )

    items = [
        MarketReportSummaryResponse(
            id=r.id or 0,
            report_type=r.report_type.value if hasattr(r.report_type, "value") else str(r.report_type),
            report_date=r.report_date,
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            generated_at=r.generated_at,
            disclaimer=r.disclaimer,
            source_providers=r.source_providers,
            is_partial=r.is_partial,
        )
        for r in reports
    ]

    return PaginatedMarketReportsResponse(
        items=items,
        page=page,
        limit=limit,
        total=total,
        disclaimer=SEBI_MANDATORY_DISCLAIMER,
        source_providers=["finnhub", "stocknews"],
    )


# ============================================================================
# Internal Router mounted at /internal/market-reports
# ============================================================================

internal_router = APIRouter(prefix="/internal/market-reports", tags=["Internal Market Reports"])


@internal_router.post(
    "/generate",
    response_model=MarketReportResponse,
    summary="Manually Trigger Market Report Generation (Internal Admin)",
    dependencies=[Depends(verify_admin_or_internal_auth)],
)
async def internal_generate_market_report_endpoint(
    body: ManualGenerateRequest,
    pre_market_use_case: GeneratePreMarketReportUseCase = Depends(get_generate_pre_market_use_case),
    post_market_use_case: GeneratePostMarketReportUseCase = Depends(get_generate_post_market_use_case),
    global_pre_market_use_case: GenerateGlobalPreMarketReportUseCase = Depends(get_generate_global_pre_market_use_case),
    global_post_market_use_case: GenerateGlobalPostMarketReportUseCase = Depends(get_generate_global_post_market_use_case),
):
    """
    Internal endpoint for automated schedulers or admins to trigger market report generation.
    Supports PRE_MARKET, POST_MARKET, GLOBAL_PRE_MARKET, and GLOBAL_POST_MARKET.
    """
    if body.report_type == ReportType.PRE_MARKET:
        report = await pre_market_use_case.execute(report_date=body.report_date, force=body.force)
    elif body.report_type == ReportType.POST_MARKET:
        report = await post_market_use_case.execute(report_date=body.report_date, force=body.force)
    elif body.report_type == ReportType.GLOBAL_PRE_MARKET:
        report = await global_pre_market_use_case.execute(report_date=body.report_date, force=body.force)
    elif body.report_type == ReportType.GLOBAL_POST_MARKET:
        report = await global_post_market_use_case.execute(report_date=body.report_date, force=body.force)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported report type: {body.report_type}",
        )

    if report.status == ReportStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Report generation failed: {report.error_details or 'Unknown error'}",
        )

    return _to_response(report)
