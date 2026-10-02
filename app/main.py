from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.cache.market_cache import market_cache
from app.core.config import settings
import app.db.base  # Ensure all ORM models are registered in SQLAlchemy registry
from app.db.session import engine
from app.infrastructure.observability import setup_observability
from app.modules.market_intelligence.application.service import market_service

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinews.main")


from app.modules.market_intelligence.infrastructure.fetcher import market_fetcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(" Starting %s in %s mode (Fetcher Mode: %s)...", settings.PROJECT_NAME, settings.ENVIRONMENT, settings.FETCHER_MODE)
    logger.info(" Active Market Provider: %s", settings.MARKET_PROVIDER)
    if settings.MARKET_ENABLE_LATENCY_FALLBACK:
        logger.info(
            " Market Resilience: Fallback=%s, Latency Threshold=%.1fs, Latency Fallback=Enabled",
            settings.MARKET_FALLBACK_PROVIDER,
            settings.MARKET_LATENCY_THRESHOLD_SECONDS,
        )
    logger.info(" Dual-Tier Envelope Cache configured (Redis URL: %s)", settings.REDIS_URL)

    fetcher_task = None
    warmup_task = None

    if settings.ENVIRONMENT.lower() != "test":
        import asyncio
        # 1. Non-blocking cold start cache warmup
        warmup_task = asyncio.create_task(market_fetcher.run_cold_start_warmup())

        # 2. In-process fetcher leader loop (if FETCHER_MODE=inprocess)
        if settings.FETCHER_MODE.lower() == "inprocess":
            fetcher_task = asyncio.create_task(market_fetcher.run_in_process_fetcher_loop())

    yield
    # Shutdown
    logger.info("Shutting down %s...", settings.PROJECT_NAME)
    if fetcher_task:
        fetcher_task.cancel()
        try:
            await fetcher_task
        except asyncio.CancelledError:
            pass

    if warmup_task and not warmup_task.done():
        warmup_task.cancel()

    await market_cache.close()
    await market_fetcher.close()
    if hasattr(market_service.provider, "close"):
        await market_service.provider.close()


docs_enabled = (settings.ENVIRONMENT.lower() != "production") and getattr(settings, "ENABLE_DOCS", True)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="SentiNews Backend API - High Performance Market & News Intelligence Platform",
    version="1.0.0",
    lifespan=lifespan,
    openapi_url=f"{settings.API_V1_STR}/openapi.json" if docs_enabled else None,
    docs_url=f"{settings.API_V1_STR}/docs" if docs_enabled else None,
    redoc_url=f"{settings.API_V1_STR}/redoc" if docs_enabled else None,
)

# GZip Response Compression Middleware (compresses responses > 1KB)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Production APM & Observability (Pure ASGI Middleware, Tracing, Metrics)
setup_observability(app, engine=engine, redis=lambda: market_cache._redis)

# Multi-Tier Rate Limiting Middleware
from app.core.middleware.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware, redis_getter=market_cache.get_redis_client)

# Defensive Security Headers Middleware
from app.core.middleware.security_headers import SecurityHeadersMiddleware
app.add_middleware(SecurityHeadersMiddleware)





@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception for %s %s: %s", request.method, request.url.path, str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Please try again later."},
    )


# Mount API V1 Router (includes /api/v1/health, /api/v1/market, and /api/v1/ws)
app.include_router(api_router, prefix=settings.API_V1_STR)

# Mount Root WebSocket Gateway (/ws)
from app.api.v1.websocket import router as ws_router
app.include_router(ws_router)

# Mount Internal Market Reports Router (/internal/market-reports/generate)
from app.api.v1.market_reports.router import internal_router as internal_market_reports_router
app.include_router(internal_market_reports_router)


@app.get("/health", tags=["Health & Status"], summary="Root Liveness Probe")
async def root_health():
    """Simple root health check endpoint."""
    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
    }
