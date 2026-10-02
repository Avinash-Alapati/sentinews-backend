import time
from typing import Any, Dict
from fastapi import APIRouter
from app.core.config import settings
from app.cache.market_cache import market_cache

router = APIRouter(tags=["Health & Status"])

START_TIME = time.time()


@router.get("/health", summary="Application Health & Status")
async def health_check() -> Dict[str, Any]:
    """
    Returns application liveness, uptime, environment, and cache backend status.
    """
    uptime_seconds = round(time.time() - START_TIME, 2)
    cache_health = await market_cache.health_check()

    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": uptime_seconds,
        "market_provider": settings.MARKET_PROVIDER,
        "cache": cache_health,
    }
