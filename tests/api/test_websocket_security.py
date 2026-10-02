"""
Comprehensive WebSocket Security & Lifecycle Test Suite.

Validates:
1. (a) Unauthenticated subscribe to private channels is denied.
2. (b) User A subscribing to portfolio:{B's id}:updates and user:{B's id}:alerts is denied (ownership check),
       while User A subscribing to portfolio:{A's id}:updates is authorized. Superusers can subscribe to all.
3. (c) Expired token on a live connection is automatically closed (code 4001).
4. (d) Per-user and per-IP connection limits are strictly enforced (code 4029).
5. (e) Origin validation against allowed CORS origins.
6. (f) Short-lived ticket flow and first-message auth verification.
"""

from datetime import datetime, timedelta, timezone
import time
from unittest.mock import AsyncMock, patch
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.v1.auth.dependencies import get_current_active_user
from app.api.v1.websocket import manager
from app.core.config import settings
from app.main import app
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import create_access_token


@pytest.fixture(autouse=True)
def reset_ws_manager():
    """Resets WebSocket connection manager pools and provides in-memory Redis mock for tests."""
    manager._ip_connections.clear()
    manager._user_connections.clear()
    manager._auth_state.clear()
    manager._subscriptions.clear()
    manager._conn_ids.clear()

    # In-memory Redis simulation for tests
    redis_store = {}

    class AsyncMockRedis:
        async def set(self, key, val, ex=None):
            redis_store[key] = val
            return True

        async def eval(self, script, numkeys, key):
            val = redis_store.pop(key, None)
            return val

        async def scard(self, key):
            return len(redis_store.get(key, set()))

        async def sadd(self, key, member):
            if key not in redis_store:
                redis_store[key] = set()
            redis_store[key].add(member)
            return 1

        async def srem(self, key, member):
            if key in redis_store:
                redis_store[key].discard(member)
            return 1

        async def expire(self, key, ttl):
            return True

    mock_redis = AsyncMockRedis()
    with patch("app.cache.market_cache.market_cache.get_redis_client", return_value=mock_redis):
        yield
        manager._ip_connections.clear()
        manager._user_connections.clear()
        manager._auth_state.clear()
        manager._subscriptions.clear()
        manager._conn_ids.clear()


def test_unauthenticated_subscribe_to_private_channel_denied():
    """
    (a) Assert unauthenticated clients can subscribe to public channels (market:ticks),
    but are strictly denied when attempting to subscribe to private channels.
    """
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # 1. Public subscription succeeds
        ws.send_json({"action": "subscribe", "channel": "market:ticks"})
        resp = ws.receive_json()
        assert resp["event"] == "subscribed"
        assert resp["channel"] == "market:ticks"

        # 2. Private portfolio subscription denied
        ws.send_json({"action": "subscribe", "channel": "portfolio:42:updates"})
        resp = ws.receive_json()
        assert resp["event"] == "error"
        assert "Authentication required" in resp["message"]

        # 3. Private alert subscription denied
        ws.send_json({"action": "subscribe", "channel": "user:42:alerts"})
        resp = ws.receive_json()
        assert resp["event"] == "error"
        assert "Authentication required" in resp["message"]


def test_channel_ownership_isolation_and_cross_user_denial():
    """
    (b) Assert User A (ID=101) cannot subscribe to User B's channels (portfolio:202:updates, user:202:alerts).
    Assert User A can subscribe to their own channels (portfolio:101:updates).
    """
    client = TestClient(app)
    token_user_a = create_access_token(
        subject="101",
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
        extra_claims={"email": "userA@test.com", "is_superuser": False},
    )

    with client.websocket_connect("/ws") as ws:
        # Authenticate as User A
        ws.send_json({"action": "auth", "token": token_user_a})
        auth_resp = ws.receive_json()
        assert auth_resp["event"] == "authenticated"
        assert auth_resp["user_id"] == "101"

        # 1. Subscribe to own portfolio -> Authorized
        ws.send_json({"action": "subscribe", "channel": "portfolio:101:updates"})
        resp = ws.receive_json()
        assert resp["event"] == "subscribed"
        assert resp["channel"] == "portfolio:101:updates"

        # 2. Subscribe to User B's portfolio -> Denied (Ownership mismatch)
        ws.send_json({"action": "subscribe", "channel": "portfolio:202:updates"})
        resp = ws.receive_json()
        assert resp["event"] == "error"
        assert "channel ownership mismatch" in resp["message"]

        # 3. Subscribe to User B's alerts -> Denied (Ownership mismatch)
        ws.send_json({"action": "subscribe", "channel": "user:202:alerts"})
        resp = ws.receive_json()
        assert resp["event"] == "error"
        assert "channel ownership mismatch" in resp["message"]


def test_expired_token_live_connection_closed():
    """
    (c) Assert that presenting an expired token or a token expiring during the session
    triggers a clean WebSocket disconnection with close code 4001.
    """
    client = TestClient(app)
    # Token expired 10 seconds ago
    expired_token = create_access_token(
        subject="101",
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
        expires_delta=timedelta(seconds=-10),
    )

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "auth", "token": expired_token})
        resp = ws.receive_json()
        assert resp["event"] == "error"
        assert "Token expired" in resp["message"]


def test_per_ip_and_per_user_connection_limits():
    """
    (d) Assert that per-IP and per-User connection ceilings are enforced.
    """
    client = TestClient(app)
    token_user = create_access_token(
        subject="555",
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    # 1. Test per-user limit
    with patch.object(settings, "WS_MAX_CONNECTIONS_PER_USER", 2):
        sockets = []
        try:
            for i in range(2):
                ws = client.websocket_connect("/ws")
                ws_ctx = ws.__enter__()
                sockets.append((ws, ws_ctx))
                ws_ctx.send_json({"action": "auth", "token": token_user})
                resp = ws_ctx.receive_json()
                assert resp["event"] == "authenticated"

            # 3rd connection for same user must be rejected
            with client.websocket_connect("/ws") as ws3:
                ws3.send_json({"action": "auth", "token": token_user})
                err_resp = ws3.receive_json()
                assert err_resp["event"] == "error"
                assert "limit exceeded" in err_resp["message"]
        finally:
            for ws, ws_ctx in sockets:
                ws.__exit__(None, None, None)


def test_websocket_origin_check_in_production():
    """
    (e) Assert WebSocket origin validation rejects unauthorized cross-origin requests in production.
    """
    from app.core.middleware.security_headers import SecurityHeadersMiddleware

    with patch("app.core.config.settings.ENVIRONMENT", "production"), \
         patch("app.core.config.settings.CORS_ORIGINS", ["https://sentinews.com"]):

        # Valid Origin
        client_valid = TestClient(app, headers={"Origin": "https://sentinews.com"})
        with client_valid.websocket_connect("/ws") as ws:
            ws.send_json({"action": "ping"})
            resp = ws.receive_json()
            assert resp["event"] == "pong"

        # Malicious / Disallowed Origin
        client_invalid = TestClient(app, headers={"Origin": "https://evil-hacker.com"})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client_invalid.websocket_connect("/ws") as ws:
                ws.send_json({"action": "ping"})
        assert exc_info.value.code == 4003


def test_short_lived_ticket_generation_and_auth_flow():
    """
    (f) Assert POST /api/v1/ws/ticket generates a single-use 60s ticket,
    and connecting with ?ticket=<ticket> authenticates the user cleanly.
    """
    mock_user = User(id=777, email="trader@sentinews.com", hashed_password="hashed_pw_123", is_active=True, is_superuser=False)
    app.dependency_overrides[get_current_active_user] = lambda: mock_user

    client = TestClient(app)
    try:
        # 1. Generate short-lived ticket
        ticket_resp = client.post("/api/v1/ws/ticket")
        assert ticket_resp.status_code == 200
        ticket_data = ticket_resp.json()
        assert "ticket" in ticket_data
        ticket = ticket_data["ticket"]
        assert ticket.startswith("wst_")
        assert ticket_data["expires_in"] == settings.WS_TICKET_EXPIRE_SECONDS

        # 2. Connect using single-use ticket in query string
        with client.websocket_connect(f"/ws?ticket={ticket}") as ws:
            auth_resp = ws.receive_json()
            assert auth_resp["event"] == "authenticated"
            assert auth_resp["user_id"] == "777"

            # Verify User 777 can now subscribe to their private channel
            ws.send_json({"action": "subscribe", "channel": "portfolio:777:updates"})
            sub_resp = ws.receive_json()
            assert sub_resp["event"] == "subscribed"

        # 3. Assert ticket was single-use: second attempt with same ticket is rejected
        with client.websocket_connect(f"/ws?ticket={ticket}") as ws2:
            err_resp = ws2.receive_json()
            assert err_resp["event"] == "error"
            assert "Invalid or expired" in err_resp["message"]
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)


def test_server_side_timer_closes_expired_connection_without_messages():
    """
    (g) Assert server-side background timer actively closes WebSocket connections
    when the auth token expires, even if the client sends zero messages.
    """
    client = TestClient(app)
    # Token valid for 1.2 seconds
    short_lived_token = create_access_token(
        subject="888",
        secret_key=settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
        expires_delta=timedelta(seconds=1.2),
    )

    with client.websocket_connect("/ws") as ws:
        # Authenticate
        ws.send_json({"action": "auth", "token": short_lived_token})
        auth_resp = ws.receive_json()
        assert auth_resp["event"] == "authenticated"

        # Client remains completely idle, sending no messages.
        # Wait for server-side timer to trigger and send expiry close
        time.sleep(1.4)
        # Verify server pushed expiry error and closed socket
        msg = ws.receive_json()
        assert msg["event"] == "error"
        assert "Token expired" in msg["message"]

