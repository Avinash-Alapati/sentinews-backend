"""
Domain Enums for Market Reports Module.
"""

from enum import Enum


class ReportType(str, Enum):
    """Supported market report schedule types."""
    PRE_MARKET = "PRE_MARKET"
    POST_MARKET = "POST_MARKET"
    GLOBAL_PRE_MARKET = "GLOBAL_PRE_MARKET"
    GLOBAL_POST_MARKET = "GLOBAL_POST_MARKET"


class ReportStatus(str, Enum):
    """Lifecycle state of a market report."""
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
