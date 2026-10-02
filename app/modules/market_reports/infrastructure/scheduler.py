"""
APScheduler Jobs & Background Worker Dispatcher for Market Reports.

Architecture Convention:
- APScheduler is strictly ENQUEUE-ONLY.
- Checks NSETradingCalendar before enqueuing to prevent running on weekends or declared holidays.
- Executes via background worker / task queue.
"""

from datetime import datetime, timezone
import logging
from typing import Optional

from app.modules.market_reports.domain.enums import ReportType
from app.modules.market_reports.domain.services.trading_calendar import NSETradingCalendar

logger = logging.getLogger("sentinews.market_reports.scheduler")

# APScheduler Cron Expressions (Asia/Kolkata timezone)
# Domestic PRE_MARKET: Monday–Friday at 07:45 IST (02:15 UTC)
PRE_MARKET_CRON = {
    "day_of_week": "mon-fri",
    "hour": 7,
    "minute": 45,
    "timezone": "Asia/Kolkata",
}

# Domestic POST_MARKET: Monday–Friday at 16:00 IST (10:30 UTC)
POST_MARKET_CRON = {
    "day_of_week": "mon-fri",
    "hour": 16,
    "minute": 0,
    "timezone": "Asia/Kolkata",
}

# GLOBAL_PRE_MARKET: Monday–Friday at 07:00 IST (01:30 UTC)
GLOBAL_PRE_MARKET_CRON = {
    "day_of_week": "mon-fri",
    "hour": 7,
    "minute": 0,
    "timezone": "Asia/Kolkata",
}

# GLOBAL_POST_MARKET: Monday–Friday at 20:00 IST (14:30 UTC)
GLOBAL_POST_MARKET_CRON = {
    "day_of_week": "mon-fri",
    "hour": 20,
    "minute": 0,
    "timezone": "Asia/Kolkata",
}


def should_generate_report_today(now: Optional[datetime] = None) -> bool:
    """
    Evaluates whether domestic report generation should proceed for the current date.
    Returns False on weekends and declared NSE trading holidays.
    """
    current_date = (now or datetime.now(timezone.utc)).date()
    if not NSETradingCalendar.is_trading_day(current_date):
        reason = NSETradingCalendar.get_holiday_name(current_date) or "Weekend"
        logger.info(
            "Skipping scheduled market report for %s: Market closed (%s)",
            current_date,
            reason,
        )
        return False
    return True


def should_generate_global_report_today(now: Optional[datetime] = None) -> bool:
    """
    Evaluates whether global market report generation should proceed today.
    Returns False on weekends (Saturday & Sunday).
    """
    current_date = (now or datetime.now(timezone.utc)).date()
    # 5 = Saturday, 6 = Sunday
    if current_date.weekday() >= 5:
        logger.info("Skipping scheduled global market report for %s: Weekend", current_date)
        return False
    return True


async def _execute_inline_report_generation(report_type: str, force: bool = False) -> None:
    """Fallback inline execution for environments without a running Celery worker."""
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
            return

        await use_case.execute(force=force)


async def _dispatch_or_run_report_job(report_type: str, force: bool = False) -> None:
    """Dispatches report generation to Celery task queue, with inline async fallback."""
    try:
        from workers.tasks.market_report_tasks import generate_market_report_task

        generate_market_report_task.delay(report_type=report_type, force=force)
        logger.info("Successfully enqueued Celery task for %s report generation.", report_type)
    except Exception as exc:
        logger.warning(
            "Celery dispatch unavailable for %s report (%s). Executing via inline async fallback.",
            report_type,
            exc,
        )
        await _execute_inline_report_generation(report_type=report_type, force=force)


async def enqueue_pre_market_report_job(force: bool = False) -> None:
    """
    APScheduler trigger handler for Pre-Market Report generation.
    Checks trading calendar and dispatches execution.
    """
    if not force and not should_generate_report_today():
        return

    logger.info("Enqueuing scheduled PRE_MARKET report generation job.")
    await _dispatch_or_run_report_job("PRE_MARKET", force=force)


async def enqueue_post_market_report_job(force: bool = False) -> None:
    """
    APScheduler trigger handler for Post-Market Report generation.
    Checks trading calendar and dispatches execution.
    """
    if not force and not should_generate_report_today():
        return

    logger.info("Enqueuing scheduled POST_MARKET report generation job.")
    await _dispatch_or_run_report_job("POST_MARKET", force=force)


async def enqueue_global_pre_market_report_job(force: bool = False) -> None:
    """
    APScheduler trigger handler for Global Pre-Market Report generation.
    """
    if not force and not should_generate_global_report_today():
        return

    logger.info("Enqueuing scheduled GLOBAL_PRE_MARKET report generation job.")
    await _dispatch_or_run_report_job("GLOBAL_PRE_MARKET", force=force)


async def enqueue_global_post_market_report_job(force: bool = False) -> None:
    """
    APScheduler trigger handler for Global Post-Market Report generation.
    """
    if not force and not should_generate_global_report_today():
        return

    logger.info("Enqueuing scheduled GLOBAL_POST_MARKET report generation job.")
    await _dispatch_or_run_report_job("GLOBAL_POST_MARKET", force=force)


def register_market_report_jobs(scheduler) -> None:
    """
    Registers the Pre-Market, Post-Market, Global Pre-Market, and Global Post-Market
    scheduled triggers with APScheduler.
    """
    if scheduler is None:
        return

    scheduler.add_job(
        enqueue_pre_market_report_job,
        "cron",
        id="scheduled_pre_market_report",
        name="Generate Pre-Market Report",
        replace_existing=True,
        **PRE_MARKET_CRON,
    )
    scheduler.add_job(
        enqueue_post_market_report_job,
        "cron",
        id="scheduled_post_market_report",
        name="Generate Post-Market Report",
        replace_existing=True,
        **POST_MARKET_CRON,
    )
    scheduler.add_job(
        enqueue_global_pre_market_report_job,
        "cron",
        id="scheduled_global_pre_market_report",
        name="Generate Global Pre-Market Report",
        replace_existing=True,
        **GLOBAL_PRE_MARKET_CRON,
    )
    scheduler.add_job(
        enqueue_global_post_market_report_job,
        "cron",
        id="scheduled_global_post_market_report",
        name="Generate Global Post-Market Report",
        replace_existing=True,
        **GLOBAL_POST_MARKET_CRON,
    )
    logger.info(
        "Registered APScheduler jobs for PRE_MARKET (07:45 IST), POST_MARKET (16:00 IST), "
        "GLOBAL_PRE_MARKET (07:00 IST), and GLOBAL_POST_MARKET (20:00 IST)."
    )
