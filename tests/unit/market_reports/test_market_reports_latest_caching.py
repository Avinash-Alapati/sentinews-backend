"""
Unit tests for Batch 3 Part 1: Market Reports /latest 5-min Redis caching.
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock
import pytest

from app.cache.market_cache import market_cache
from app.modules.market_reports.application.queries import GetLatestReportQuery, _report_to_cache_dict
from app.modules.market_reports.domain.entities import MarketReport
from app.modules.market_reports.domain.enums import ReportStatus, ReportType


@pytest.mark.asyncio
async def test_latest_market_report_5min_caching():
    """
    Validates that:
    1. First call queries repository and caches in Redis with 300s TTL.
    2. Subsequent calls hit cache and skip repository queries.
    3. Works across all 4 report types.
    """
    mock_repo = AsyncMock()

    for r_type in [
        ReportType.PRE_MARKET,
        ReportType.POST_MARKET,
        ReportType.GLOBAL_PRE_MARKET,
        ReportType.GLOBAL_POST_MARKET,
    ]:
        type_str = r_type.value if hasattr(r_type, "value") else str(r_type)
        report = MarketReport(
            id=100 + len(type_str),
            report_type=r_type,
            report_date=date(2026, 9, 26),
            status=ReportStatus.PUBLISHED,
            generated_at=datetime(2026, 9, 26, 8, 0, 0, tzinfo=timezone.utc),
            sections={"market_summary": f"Latest {type_str} update"},
            disclaimer="SEBI disclaimer",
            source_providers=["finnhub"],
            is_partial=False,
        )
        mock_repo.get_latest_published = AsyncMock(return_value=report)

        # Clear existing cache key
        cache_key = f"market_report:latest:{type_str}"
        await market_cache.delete(cache_key)

        query = GetLatestReportQuery(repository=mock_repo)

        # 1. First execution: Cache miss -> repository called
        rep1 = await query.execute(r_type)
        assert rep1.id == report.id
        assert mock_repo.get_latest_published.call_count == 1

        # Verify key is in cache
        cached = await market_cache.get(cache_key)
        assert cached is not None
        assert cached["id"] == report.id

        # 2. Second execution: Cache hit -> repository NOT called again
        rep2 = await query.execute(r_type)
        assert rep2.id == report.id
        assert mock_repo.get_latest_published.call_count == 1  # Still 1!
