"""
Observability Setup and Wiring Engine.

Coordinates the initialization of metrics, structured logging, SQLAlchemy tracing,
route matching, pure ASGI middleware, and internal health/metrics routes.
All setup operations are designed to FAIL OPEN.
"""

import asyncio
import logging
from typing import Any, List, Optional
from fastapi import FastAPI

from app.core.config import settings
from app.infrastructure.observability.asgi_middleware import PureASGIObservabilityMiddleware
from app.infrastructure.observability.chaos_router import router as chaos_router
from app.infrastructure.observability.decorators import track_compute
from app.infrastructure.observability.http_tracer import instrument_httpx_client
from app.infrastructure.observability.internal_router import router as internal_router
from app.infrastructure.observability.lifecycle import lifecycle_manager
from app.infrastructure.observability.logging_handler import setup_structured_logging
from app.infrastructure.observability.registry import get_registry, queue_depth_collector
from app.infrastructure.observability.route_matcher import route_matcher
from app.infrastructure.observability.signatures import crash_engine
from app.infrastructure.observability.sql_tracer import setup_sql_tracing
from app.infrastructure.observability.system_collector import system_collector

logger = logging.getLogger("sentinews.observability")


def setup_observability(
    app: FastAPI,
    engine: Optional[Any] = None,
    redis: Optional[Any] = None,
    http_clients: Optional[List[Any]] = None,
) -> None:
    """
    Single central initialization entrypoint for all Sentinews APM and observability subsystems.
    Guaranteed to fail open if any individual observability component encounters an error.
    """
    if not settings.APM_ENABLED:
        logger.info("APM is disabled via APM_ENABLED=False.")
        return

    try:
        logger.info("Initializing Sentinews Production APM & Observability layer...")

        # 1. Early lifecycle handlers (faulthandler, unclean restart detector)
        lifecycle_manager.setup_early_handlers()

        # 2. Structured JSON logging, Loki shipper, and Sentry
        setup_structured_logging(
            loki_url=settings.LOKI_URL,
            sentry_dsn=settings.SENTRY_DSN,
            service_name=settings.SERVICE_NAME,
            env=settings.ENV or settings.ENVIRONMENT,
        )

        # 3. Mount internal metrics and health probe router
        app.include_router(internal_router)

        # 4. Mount dev/test chaos engineering router if non-production
        if settings.ENVIRONMENT.lower() != "production":
            app.include_router(chaos_router)
            logger.info("Mounted /internal/chaos endpoints (non-production environment)")

        # 5. Attach SQLAlchemy database tracing if engine provided
        if engine is not None:
            setup_sql_tracing(engine)

        # 6. Configure Redis queue depth collector if redis getter/client provided
        if redis is not None:
            if callable(redis):
                queue_depth_collector.set_redis_getter(redis)
            else:
                queue_depth_collector.set_redis_getter(lambda: redis)

        # 7. Instrument any provided HTTP client instances
        if http_clients:
            for client in http_clients:
                try:
                    instrument_httpx_client(client)
                except Exception as e:
                    logger.debug("HTTP client instrumentation note: %s", e)

        # 8. Register Pure ASGI Middleware (innermost or outermost ASGI layer)
        app.add_middleware(PureASGIObservabilityMiddleware)

        # 9. Register startup hook to compile route matcher and start background collectors
        @app.on_event("startup")
        async def on_observability_startup():
            try:
                # Compile route template patterns from registered routes
                route_matcher.initialize_from_app(app)
                # Start system psutil and event loop lag background workers
                system_collector.start()
                # Start lifecycle memory heartbeat
                lifecycle_manager.start_lifecycle_tasks()
                logger.info("Observability background collectors started successfully")
            except Exception as startup_err:
                logger.warning("Observability startup task error (fail-open): %s", startup_err)

        @app.on_event("shutdown")
        async def on_observability_shutdown():
            try:
                system_collector.stop()
                lifecycle_manager.stop()
                logger.info("Observability collectors stopped cleanly")
            except Exception as shutdown_err:
                logger.warning("Observability shutdown error: %s", shutdown_err)

        logger.info("Sentinews APM & Observability layer successfully configured.")
    except Exception as exc:
        logger.critical("Failed to initialize observability layer (failing open): %s", exc, exc_info=True)
