"""
Internal APM Endpoints Router.

Exposes Prometheus metrics, crash signature inspection, liveness and readiness probes.
Secured with METRICS_TOKEN via constant-time token comparison.
"""

import asyncio
import secrets
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import settings
from app.infrastructure.observability.registry import get_registry
from app.infrastructure.observability.signatures import crash_engine

router = APIRouter(include_in_schema=False)


def verify_metrics_token(authorization: Optional[str] = Header(None)) -> bool:
    """
    Verifies Bearer token using constant-time string comparison.
    """
    expected_token = settings.METRICS_TOKEN
    if not expected_token:
        return True  # If no token configured, allow access (e.g. local dev)

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    if not secrets.compare_digest(token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid metrics token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True


@router.get("/internal/metrics", summary="Prometheus Metrics Exposition")
async def get_metrics(authorized: bool = Depends(verify_metrics_token)):
    """
    Prometheus scrape endpoint returning serialized metrics in text exposition format.
    """
    registry = get_registry()
    data = generate_latest(registry)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@router.get("/internal/crash-signatures", summary="Recent Crash Signatures")
async def get_crash_signatures(authorized: bool = Depends(verify_metrics_token)):
    """
    Returns the in-memory ring buffer of the last 200 crash signatures with occurrences and traces.
    """
    records = crash_engine.get_all_records()
    return {
        "count": len(records),
        "signatures": records,
    }


@router.get("/healthz", summary="Liveness Probe")
async def liveness_probe():
    """Fast liveness check indicating the process is running."""
    return {"status": "alive", "service": settings.SERVICE_NAME}


@router.get("/readyz", summary="Readiness Probe")
async def readiness_probe():
    """
    Deep readiness check verifying database and Redis connectivity within a 1.5s timeout.
    """
    db_ok = True
    redis_ok = True
    db_err = None
    redis_err = None

    # Check Database connection
    try:
        from app.db.session import engine
        from sqlalchemy import text

        async def _check_db():
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        await asyncio.wait_for(_check_db(), timeout=1.5)
    except Exception as exc:
        db_ok = False
        db_err = str(exc)

    # Check Redis connection
    try:
        from app.cache.market_cache import market_cache

        async def _check_redis():
            client = await market_cache._get_redis()
            if client:
                await client.ping()

        await asyncio.wait_for(_check_redis(), timeout=1.5)
    except Exception as exc:
        redis_ok = False
        redis_err = str(exc)

    is_ready = db_ok and (redis_ok or not settings.REDIS_ENABLED)
    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return Response(
        content=f'{{"status":"{"ready" if is_ready else "unhealthy"}","database":{str(db_ok).lower()},"redis":{str(redis_ok).lower()}}}',
        status_code=status_code,
        media_type="application/json",
    )

