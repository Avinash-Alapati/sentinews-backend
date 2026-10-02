"""
Unit tests for PureASGIObservabilityMiddleware.
"""

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from app.infrastructure.observability.asgi_middleware import PureASGIObservabilityMiddleware
from app.infrastructure.observability.route_matcher import route_matcher


@pytest.fixture
def test_app():
    app = FastAPI()

    @app.get("/api/v1/items/{item_id}")
    async def get_item(item_id: str):
        if item_id == "error":
            raise ValueError("Test unhandled exception")
        elif item_id == "404":
            raise HTTPException(status_code=404, detail="Not found")
        return {"item_id": item_id}

    @app.get("/healthz")
    async def health():
        return {"status": "alive"}

    app.add_middleware(PureASGIObservabilityMiddleware)
    route_matcher.initialize_from_app(app)
    return app


@pytest.mark.asyncio
async def test_middleware_request_id_generation_and_propagation(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. No incoming X-Request-ID -> generates new UUID
        resp = await client.get("/api/v1/items/123")
        assert resp.status_code == 200
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) > 10

        # 2. Incoming X-Request-ID -> echoes back
        custom_id = "custom-req-id-999"
        resp2 = await client.get("/api/v1/items/456", headers={"X-Request-ID": custom_id})
        assert resp2.status_code == 200
        assert resp2.headers["x-request-id"] == custom_id


@pytest.mark.asyncio
async def test_middleware_unhandled_exception_recording_and_reraise(test_app):
    transport = ASGITransport(app=test_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/items/error")
        assert resp.status_code == 500
