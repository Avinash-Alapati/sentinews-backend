"""
Unit & Integration tests for WebSocket Lifecycle, Connection Gauges, and Disconnect Cleanup.
"""

from unittest.mock import AsyncMock
import pytest
from app.infrastructure.observability.asgi_middleware import PureASGIObservabilityMiddleware
from app.infrastructure.observability.metrics import ws_connections_active


@pytest.mark.asyncio
async def test_websocket_lifecycle_gauge_tracking():
    """
    Verify PureASGIObservabilityMiddleware increments ws_connections_active on connect
    and decrements it upon clean disconnect.
    """
    initial_connections = ws_connections_active._value.get()

    scope = {
        "type": "websocket",
        "path": "/ws/market-stream",
        "headers": [],
    }

    async def mock_ws_app(s, receive, send):
        # Verify active gauge incremented while inside handler
        current = ws_connections_active._value.get()
        assert current == initial_connections + 1

    middleware = PureASGIObservabilityMiddleware(app=mock_ws_app)
    mock_receive = AsyncMock()
    mock_send = AsyncMock()

    await middleware(scope, mock_receive, mock_send)

    # After completion, gauge must return to baseline
    final_connections = ws_connections_active._value.get()
    assert final_connections == initial_connections


@pytest.mark.asyncio
async def test_websocket_error_resilient_cleanup():
    """
    Verify that if a WebSocket connection throws an unhandled exception,
    the active connections gauge is still decremented cleanly via the finally block.
    """
    initial_connections = ws_connections_active._value.get()

    scope = {
        "type": "websocket",
        "path": "/ws/alerts",
        "headers": [],
    }

    async def faulty_ws_app(s, receive, send):
        current = ws_connections_active._value.get()
        assert current == initial_connections + 1
        raise ConnectionResetError("Client disconnected abruptly")

    middleware = PureASGIObservabilityMiddleware(app=faulty_ws_app)
    mock_receive = AsyncMock()
    mock_send = AsyncMock()

    with pytest.raises(ConnectionResetError):
        await middleware(scope, mock_receive, mock_send)

    # Gauge must be cleanly decremented back to initial
    final_connections = ws_connections_active._value.get()
    assert final_connections == initial_connections
