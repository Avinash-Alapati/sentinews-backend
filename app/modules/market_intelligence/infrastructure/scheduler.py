"""
APScheduler Jobs & Background Worker Dispatcher for Market Intelligence.

Follows the architectural rule:
- APScheduler is strictly ENQUEUE-ONLY.
- Dispatches tasks onto the Celery Redis work queue.
"""

import logging

from app.core.config import settings

logger = logging.getLogger("sentinews.market_intelligence.scheduler")


def enqueue_market_overview_refresh_job():
    """APScheduler trigger handler enqueuing market overview cache refresh."""
    if settings.FETCHER_MODE.lower() == "inprocess":
        logger.debug("Skipping Celery enqueue for market overview: FETCHER_MODE is inprocess")
        return
    logger.debug("APScheduler trigger: enqueuing market overview refresh...")
    try:
        from workers.tasks.market_tasks import warm_market_overview_cache_task
        warm_market_overview_cache_task.delay(filter_name="all", limit=20)
    except Exception as exc:
        logger.debug("Could not enqueue market overview Celery task: %s", exc)


def enqueue_market_indices_refresh_job():
    """APScheduler trigger handler enqueuing market indices refresh."""
    if settings.FETCHER_MODE.lower() == "inprocess":
        logger.debug("Skipping Celery enqueue for market indices: FETCHER_MODE is inprocess")
        return
    logger.debug("APScheduler trigger: enqueuing market indices refresh...")
    try:
        from workers.tasks.market_tasks import refresh_market_indices_task
        refresh_market_indices_task.delay()
    except Exception as exc:
        logger.debug("Could not enqueue market indices Celery task: %s", exc)


def enqueue_full_market_cycle_job():
    """APScheduler trigger handler enqueuing full market data refresh cycle."""
    if settings.FETCHER_MODE.lower() == "inprocess":
        logger.debug("Skipping Celery enqueue for full market cycle: FETCHER_MODE is inprocess")
        return
    logger.info("APScheduler trigger: enqueuing full market refresh cycle...")
    try:
        from workers.tasks.market_tasks import run_full_market_refresh_cycle_task
        run_full_market_refresh_cycle_task.delay()
    except Exception as exc:
        logger.debug("Could not enqueue full market cycle Celery task: %s", exc)


def register_market_intelligence_jobs(scheduler) -> None:
    """
    Registers periodic enqueue jobs with APScheduler if running in worker mode.
    """
    if scheduler is None or settings.FETCHER_MODE.lower() == "inprocess":
        return

    scheduler.add_job(
        enqueue_full_market_cycle_job,
        "interval",
        seconds=int(settings.MARKET_REFRESH_INTERVAL_IN_HOURS),
        id="scheduled_full_market_cycle",
        name="Enqueue Full Market Refresh Cycle",
        replace_existing=True,
    )
    logger.info("Registered APScheduler market intelligence enqueue job every %ds.",
                int(settings.MARKET_REFRESH_INTERVAL_IN_HOURS))
