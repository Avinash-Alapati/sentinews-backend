"""
Domain service for strict SEBI regulatory compliance validation.

SEBI Compliance Rules:
1. Reports are strictly informational and factual.
2. No section may contain actionable investment advice, price targets, buy/sell framing,
   or stock recommendations.
3. Third-party headlines containing aggressive promotional/actionable phrasing are dropped
   individually without altering the quoted text of remaining headlines.
4. Every report must carry the mandatory SEBI disclaimer.
"""

import re
from typing import Any, Dict, List
from app.modules.market_reports.domain.entities import (
    HeadlineItem,
    MarketReport,
    SEBI_MANDATORY_DISCLAIMER,
)


class ComplianceViolationError(ValueError):
    """Raised when generated report text violates statutory SEBI compliance."""
    pass


# Actionable, advisory, or promotional terms strictly prohibited in synthesized text
PROHIBITED_PHRASES = [
    r"\bbuy\s+now\b",
    r"\bstrong\s+buy\b",
    r"\bstrong\s+sell\b",
    r"\bmultibagger\b",
    r"\btarget\s+price\b",
    r"\bprice\s+target\b",
    r"\bstop\s+loss\b",
    r"\bentry\s+point\b",
    r"\bexit\s+point\b",
    r"\baccumulate\s+shares\b",
    r"\bguaranteed\s+returns?\b",
    r"\btrade\s+recommendation\b",
    r"\bstock\s+tips?\b",
    r"\bscreaming\s+buy\b",
    r"\bportfolio\s+pick\b",
    r"\bhot\s+stock\b",
]

COMPILED_PROHIBITED_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in PROHIBITED_PHRASES]


def check_actionable_language(text: str) -> List[str]:
    """
    Scans text for prohibited actionable advisory phrases.
    Returns list of matched prohibited phrases.
    """
    if not text:
        return []
    matches = []
    for pattern in COMPILED_PROHIBITED_PATTERNS:
        found = pattern.findall(text)
        if found:
            matches.extend(found)
    return matches


def is_headline_compliant(headline: str) -> bool:
    """
    Evaluates whether a third-party headline is free from prohibited actionable phrasing.
    Returns True if compliant, False if it should be dropped.
    """
    if not headline or not headline.strip():
        return False
    matches = check_actionable_language(headline)
    return len(matches) == 0


def filter_compliant_headlines(headlines: List[HeadlineItem]) -> List[HeadlineItem]:
    """
    Filters a list of headlines, dropping any individual headlines containing actionable language.
    Does not modify third-party text.
    """
    return [item for item in headlines if is_headline_compliant(item.headline)]


def validate_market_report_compliance(report: MarketReport) -> None:
    """
    Strictly validates a MarketReport domain entity for SEBI compliance:
    1. Verifies non-empty statutory disclaimer matching required text.
    2. Validates that all generated commentary / section text contains NO actionable phrasing.
    """
    if not report.disclaimer or report.disclaimer != SEBI_MANDATORY_DISCLAIMER:
        raise ComplianceViolationError(
            "Report violates SEBI compliance: mandatory statutory disclaimer is missing or altered."
        )

    # Validate recursive text content in report sections (excluding external URLs/sources)
    def scan_sections_recursively(data: Any, path: str = ""):
        if isinstance(data, str):
            # Ignore URLs and source identifiers
            if data.startswith("http://") or data.startswith("https://") or path.endswith(".source"):
                return
            prohibited = check_actionable_language(data)
            if prohibited:
                raise ComplianceViolationError(
                    f"SEBI Compliance Violation in section '{path}': prohibited phrasing detected: {prohibited}"
                )
        elif isinstance(data, dict):
            for k, v in data.items():
                scan_sections_recursively(v, f"{path}.{k}" if path else k)
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                scan_sections_recursively(item, f"{path}[{idx}]")

    scan_sections_recursively(report.sections)
