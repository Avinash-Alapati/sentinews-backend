"""
Unified Pagination Utilities and Response Schemas.

Provides standardized query parameter parsing, bounds enforcement (max limit <= 100),
and generic SEBI-compliant paginated response envelopes across all API modules.
"""

import math
from typing import Generic, List, Optional, Sequence, TypeVar
from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")

SEBI_MANDATORY_DISCLAIMER = (
    "For informational purposes only. Not investment advice. "
    "Sentinews is not a SEBI-registered investment adviser or research analyst."
)


class PaginationParams:
    """FastAPI dependency extracting and validating pagination query parameters."""

    def __init__(
        self,
        page: int = Query(default=1, ge=1, description="1-indexed page number"),
        limit: int = Query(default=20, ge=1, le=100, description="Items per page (max 100)"),
    ):
        self.page = page
        self.limit = min(limit, 100)
        self.offset = (page - 1) * self.limit


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Standardized pagination response envelope carrying items and statutory disclaimer.
    """
    items: List[T] = Field(default_factory=list)
    total: int = Field(default=0, description="Total count of available items")
    page: int = Field(default=1, description="Current page number")
    limit: int = Field(default=20, description="Page size limit")
    pages: int = Field(default=1, description="Total number of pages")
    has_next: bool = Field(default=False, description="Whether a subsequent page exists")
    has_prev: bool = Field(default=False, description="Whether a previous page exists")
    disclaimer: str = Field(
        default=SEBI_MANDATORY_DISCLAIMER,
        description="Mandatory statutory regulatory disclaimer",
    )


def paginate_sequence(
    items: Sequence[T],
    page: int,
    limit: int,
    total: Optional[int] = None,
) -> PaginatedResponse[T]:
    """
    Helper function constructing a PaginatedResponse envelope from an in-memory or database slice.
    """
    total_count = total if total is not None else len(items)
    page_limit = max(1, min(limit, 100))
    total_pages = max(1, math.ceil(total_count / page_limit)) if total_count > 0 else 1

    return PaginatedResponse[T](
        items=list(items),
        total=total_count,
        page=page,
        limit=page_limit,
        pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1,
        disclaimer=SEBI_MANDATORY_DISCLAIMER,
    )
