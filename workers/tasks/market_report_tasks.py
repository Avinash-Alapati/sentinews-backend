"""
Market Reports Generation Background Tasks.

Follows the standard Celery pattern (bind=True, max_retries, self.retry with exponential backoff).
Dispatches generation of domestic and global pre-market and post-market reports.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict

from app.db.session import AsyncSessionLocal
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
from app.modules.market_reports.infrastructure.adapters.finnhub_client import FinnhubClient
from app.modules.market_reports.infrastructure.adapters.stocknews_client import StockNewsApiClient
from app.modules.market_reports.infrastructure.locks import RedisReportLock
from app.modules.market_reports.infrastructure.repositories.market_report_repository import (
    SQLAlchemyMarketReportRepository,
)
from workers.celery_app import celery_app

logger = logging.getLogger("sentinews.workers.market_report_tasks")


async def _run_async_report_generation(report_type: str, force: bool = False) -> Dict[str, Any]:
    """Helper coroutine executing the requested market report generation use case."""
    async with AsyncSessionLocal() as session:
        repository = SQLAlchemyMarketReportRepository(session)
        finnhub_client = FinnhubClient()
        stocknews_client = StockNewsApiClient()
        lock_port = RedisReportLock()

        if report_type == "PRE_MARKET":
            use_case = GeneratePreMarketReportUseCase(
                repository=repository,
                finnhub_client=finnhub_client,
                stocknews_client=stocknews_client,
                lock_port=lock_port,
            )
        elif report_type == "POST_MARKET":
            use_case = GeneratePostMarketReportUseCase(
                repository=repository,
                finnhub_client=finnhub_client,
                stocknews_client=stocknews_client,
                lock_port=lock_port,
            )
        elif report_type == "GLOBAL_PRE_MARKET":
            use_case = GenerateGlobalPreMarketReportUseCase(
                repository=repository,
                finnhub_client=finnhub_client,
                stocknews_client=stocknews_client,
                lock_port=lock_port,
            )
        elif report_type == "GLOBAL_POST_MARKET":
            use_case = GenerateGlobalPostMarketReportUseCase(
                repository=repository,
                finnhub_client=finnhub_client,
                stocknews_client=stocknews_client,
                lock_port=lock_port,
            )
        else:
            raise ValueError(f"Unknown report type: {report_type}")

        report = await use_case.execute(force=force)
        return {
            "report_id": getattr(report, "id", None),
            "report_type": report_type,
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="workers.tasks.market_report_tasks.generate_market_report_task",
)
def generate_market_report_task(self, report_type: str, force: bool = False) -> Dict[str, Any]:
    """
    Celery background worker task for generating market reports.
    """
    logger.info(
        "Starting market report generation task for %s (force=%s, attempt %d/%d)...",
        report_type,
        force,
        self.request.retries + 1,
        self.max_retries + 1,
    )
    try:
        result = asyncio.run(_run_async_report_generation(report_type=report_type, force=force))
        logger.info("Market report generation succeeded for %s: %s", report_type, result)
        return result
    except Exception as exc:
        logger.error("Market report generation failed for %s: %s", report_type, exc, exc_info=True)
        countdown = (2 ** self.request.retries) * 30
        raise self.retry(exc=exc, countdown=countdown)
