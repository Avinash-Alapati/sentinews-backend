"""
Production WebSocket Gateway and Real-Time Event Hub.

Architectural Guarantees:
1. First-Message & Short-Lived Ticket Auth: Avoids sending long-lived JWTs in URL query strings.
2. Channel Authorization & Strict Ownership:
   - Public channels: market:ticks, market:overview, news:live.
   - Private channels: portfolio:{id}:updates, user:{id}:alerts (enforces user_id ownership check).
3. Live Token Expiration: Connections are automatically terminated with code 4001 if token expires during session.
4. Concurrency Limits: Enforces per-IP and per-User connection ceilings.
5. Sensitive Token Redaction: All ticket/token exchanges are redacted from logs.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import secrets
import time
from typing import Any, Dict, Optional, Set, Tuple
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from app.api.v1.auth.dependencies import get_current_active_user
from app.cache.market_cache import market_cache
from app.core.config import settings
from app.modules.auth.domain.entities import User
from app.modules.auth.domain.services.security import (
    InvalidTokenError,
    TokenExpiredError,
    decode_access_token,
)

logger = logging.getLogger("sentinews.websocket")

router = APIRouter(tags=["WebSocket Gateway"])

# Public channel prefixes accessible without authentication
PUBLIC_CHANNELS = {"market:ticks", "market:overview", "news:live"}


GETDEL_LUA = """
local val = redis.call('GET', KEYS[1])
if val then
    redis.call('DEL', KEYS[1])
end
return val
"""


def resolve_client_ip(scope: dict) -> str:
    """
    Resolves real client IP behind reverse proxy / Caddy.
    Protects against X-Forwarded-For spoofing by taking the client IP based on TRUSTED_PROXY_COUNT.
    """
    headers = dict(scope.get("headers", []))
    x_fwd = headers.get(b"x-forwarded-for", b"").decode("latin1", errors="ignore").strip()
    if x_fwd:
        ips = [ip.strip() for ip in x_fwd.split(",") if ip.strip()]
        if ips:
            proxy_count = max(1, getattr(settings, "TRUSTED_PROXY_COUNT", 1))
            idx = -min(proxy_count, len(ips))
            return ips[idx]
    x_real = headers.get(b"x-real-ip", b"").decode("latin1", errors="ignore").strip()
    if x_real:
        return x_real
    client = scope.get("client")
    if client and len(client) > 0:
        return str(client[0])
    return "unknown_ip"


class WSTicketResponse(BaseModel):
    ticket: str
    expires_in: int


class ConnectionManager:
    """Manages active WebSocket connections, rate limits, subscriptions, and security checks."""

    def __init__(self):
        self._ip_connections: Dict[str, Set[WebSocket]] = {}
        self._user_connections: Dict[str, Set[WebSocket]] = {}
        self._auth_state: Dict[WebSocket, Dict[str, Any]] = {}
        self._subscriptions: Dict[str, Set[WebSocket]] = {}
        self._conn_ids: Dict[WebSocket, str] = {}

    def get_client_ip(self, websocket: WebSocket) -> str:
        """Resolves client IP from headers or connection scope."""
        return resolve_client_ip(websocket.scope)

    async def create_ticket(self, user: User) -> str:
        """Generates a short-lived (60s) single-use WebSocket authentication ticket in Redis."""
        redis_client = market_cache.get_redis_client()
        if not redis_client or not settings.REDIS_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WebSocket ticket service requires active Redis. In-memory fallback is disabled in production.",
            )

        ticket = f"wst_{secrets.token_urlsafe(32)}"
        ticket_data = {
            "user_id": str(user.id),
            "email": user.email,
            "is_superuser": bool(user.is_superuser),
            "exp": time.time() + settings.WS_TICKET_EXPIRE_SECONDS,
        }
        cache_key = f"ws_ticket:{ticket}"
        try:
            await redis_client.set(cache_key, json.dumps(ticket_data), ex=settings.WS_TICKET_EXPIRE_SECONDS)
        except Exception as exc:
            logger.error("Failed saving WS ticket to Redis: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed saving WebSocket ticket in Redis.",
            ) from exc

        return ticket

    async def claim_ticket(self, ticket: str) -> Optional[Dict[str, Any]]:
        """Validates and atomically consumes a single-use ticket from Redis."""
        if not ticket:
            return None

        redis_client = market_cache.get_redis_client()
        if not redis_client or not settings.REDIS_ENABLED:
            logger.warning("Rejecting WS ticket claim: Redis client unavailable (fail closed)")
            return None

        cache_key = f"ws_ticket:{ticket}"
        try:
            val = await redis_client.eval(GETDEL_LUA, 1, cache_key)
            if not val:
                return None
            if isinstance(val, bytes):
                val = val.decode("utf-8")
            ticket_data = json.loads(val)
            if time.time() > ticket_data.get("exp", 0):
                return None
            return ticket_data
        except Exception as exc:
            logger.warning("Failed claiming WS ticket in Redis: %s", exc)
            return None

    async def can_connect_ip(self, ip: str, conn_id: str) -> bool:
        """Asserts client IP does not exceed max concurrent WebSocket connections."""
        redis_client = market_cache.get_redis_client()
        if redis_client:
            try:
                key = f"ws:conns:ip:{ip}"
                count = await redis_client.scard(key)
                if count >= settings.WS_MAX_CONNECTIONS_PER_IP:
                    return False
                await redis_client.sadd(key, conn_id)
                await redis_client.expire(key, 3600)
            except Exception:
                pass

        current_count = len(self._ip_connections.get(ip, set()))
        return current_count < settings.WS_MAX_CONNECTIONS_PER_IP

    async def can_connect_user(self, user_id: str, conn_id: str) -> bool:
        """Asserts user does not exceed max concurrent WebSocket connections."""
        redis_client = market_cache.get_redis_client()
        if redis_client:
            try:
                key = f"ws:conns:user:{user_id}"
                count = await redis_client.scard(key)
                if count >= settings.WS_MAX_CONNECTIONS_PER_USER:
                    return False
                await redis_client.sadd(key, conn_id)
                await redis_client.expire(key, 3600)
            except Exception:
                pass

        current_count = len(self._user_connections.get(str(user_id), set()))
        return current_count < settings.WS_MAX_CONNECTIONS_PER_USER

    async def connect(self, websocket: WebSocket) -> bool:
        """Accepts connection and registers IP limit."""
        ip = self.get_client_ip(websocket)
        conn_id = secrets.token_hex(8)
        self._conn_ids[websocket] = conn_id

        if not await self.can_connect_ip(ip, conn_id):
            logger.warning("WebSocket connection rejected: IP limit exceeded for %s", ip)
            await websocket.close(code=4029)  # 4029: Too Many Connections
            return False

        await websocket.accept()
        if ip not in self._ip_connections:
            self._ip_connections[ip] = set()
        self._ip_connections[ip].add(websocket)
        return True

    async def authenticate_connection(self, websocket: WebSocket, user_info: Dict[str, Any]) -> bool:
        """Associates connection with authenticated user session, verifying user limits."""
        user_id = str(user_info["user_id"])
        conn_id = self._conn_ids.get(websocket, secrets.token_hex(8))
        if not await self.can_connect_user(user_id, conn_id):
            return False

        self._auth_state[websocket] = user_info
        if user_id not in self._user_connections:
            self._user_connections[user_id] = set()
        self._user_connections[user_id].add(websocket)
        return True

    def is_token_expired(self, websocket: WebSocket) -> bool:
        """Checks if connection's auth token has passed its exp timestamp."""
        auth_data = self._auth_state.get(websocket)
        if not auth_data:
            return False
        exp = auth_data.get("exp")
        if exp and time.time() > exp:
            return True
        return False

    def authorize_subscription(self, websocket: WebSocket, channel: str) -> Tuple[bool, str]:
        """
        Validates channel subscription rules:
        - Public channels allowed for all.
        - Private channels require valid authentication and matching ownership.
        """
        if channel in PUBLIC_CHANNELS:
            return True, "Authorized public channel"

        # Private channel parsing
        auth_data = self._auth_state.get(websocket)
        if not auth_data:
            return False, "Authentication required for private channels"

        user_id = str(auth_data["user_id"])
        is_superuser = bool(auth_data.get("is_superuser", False))

        # Check portfolio channel ownership: portfolio:{user_id}:updates
        if channel.startswith("portfolio:"):
            parts = channel.split(":")
            if len(parts) >= 2:
                target_user_id = parts[1]
                if target_user_id == user_id or is_superuser:
                    return True, "Authorized portfolio channel"
                return False, f"Access denied: channel ownership mismatch (cannot access portfolio of user {target_user_id})"

        # Check user alert channel ownership: user:{user_id}:alerts
        if channel.startswith("user:"):
            parts = channel.split(":")
            if len(parts) >= 2:
                target_user_id = parts[1]
                if target_user_id == user_id or is_superuser:
                    return True, "Authorized user alerts channel"
                return False, f"Access denied: channel ownership mismatch (cannot access alerts of user {target_user_id})"

        return False, f"Unknown or unauthorized channel: {channel}"

    def subscribe(self, websocket: WebSocket, channel: str) -> None:
        """Registers websocket to channel subscriptions."""
        if channel not in self._subscriptions:
            self._subscriptions[channel] = set()
        self._subscriptions[channel].add(websocket)

    def unsubscribe(self, websocket: WebSocket, channel: str) -> None:
        """Removes websocket from channel subscriptions."""
        if channel in self._subscriptions:
            self._subscriptions[channel].discard(websocket)
            if not self._subscriptions[channel]:
                del self._subscriptions[channel]

    async def disconnect(self, websocket: WebSocket) -> None:
        """Frees all resources associated with disconnected WebSocket."""
        conn_id = self._conn_ids.pop(websocket, None)
        redis_client = market_cache.get_redis_client()

        # 1. Remove from IP pool
        ip = self.get_client_ip(websocket)
        if ip in self._ip_connections:
            self._ip_connections[ip].discard(websocket)
            if not self._ip_connections[ip]:
                del self._ip_connections[ip]

        if redis_client and conn_id:
            try:
                await redis_client.srem(f"ws:conns:ip:{ip}", conn_id)
            except Exception:
                pass

        # 2. Remove from User pool
        auth_data = self._auth_state.pop(websocket, None)
        if auth_data:
            user_id = str(auth_data["user_id"])
            if user_id in self._user_connections:
                self._user_connections[user_id].discard(websocket)
                if not self._user_connections[user_id]:
                    del self._user_connections[user_id]

            if redis_client and conn_id:
                try:
                    await redis_client.srem(f"ws:conns:user:{user_id}", conn_id)
                except Exception:
                    pass

        # 3. Remove from all channel subscriptions
        for ch, ws_set in list(self._subscriptions.items()):
            ws_set.discard(websocket)
            if not ws_set:
                del self._subscriptions[ch]


manager = ConnectionManager()


@router.post("/ws/ticket", response_model=WSTicketResponse, summary="Generate Short-Lived WebSocket Auth Ticket")
async def issue_websocket_ticket(
    current_user: User = Depends(get_current_active_user),
):
    """
    Issues a single-use, 60-second WebSocket connection ticket.
    Enables secure authentication without sending long-lived JWTs in URL query parameters.
    """
    ticket = await manager.create_ticket(current_user)
    return WSTicketResponse(ticket=ticket, expires_in=settings.WS_TICKET_EXPIRE_SECONDS)


async def handle_websocket_session(websocket: WebSocket, initial_ticket: Optional[str] = None) -> None:
    """Core WebSocket event loop handling connection, authentication, subscriptions, and teardown."""
    connected = await manager.connect(websocket)
    if not connected:
        return

    expiry_task: Optional[asyncio.Task] = None

    def schedule_server_expiry_timer(exp_timestamp: float) -> None:
        nonlocal expiry_task
        if expiry_task and not expiry_task.done():
            expiry_task.cancel()

        delay = max(0.01, exp_timestamp - time.time())

        async def _timer():
            try:
                await asyncio.sleep(delay)
                logger.info("Server-side timer closing expired WebSocket connection")
                await websocket.send_json({"event": "error", "message": "Token expired"})
                await websocket.close(code=4001)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("Server expiry timer error: %s", e)

        expiry_task = asyncio.create_task(_timer())

    try:
        # Check initial query ticket if provided (single-use ticket)
        if initial_ticket:
            ticket_data = await manager.claim_ticket(initial_ticket)
            if ticket_data:
                if not await manager.authenticate_connection(websocket, ticket_data):
                    await websocket.send_json({"event": "error", "message": "User connection limit exceeded"})
                    await websocket.close(code=4029)
                    return
                schedule_server_expiry_timer(ticket_data.get("exp", time.time() + 3600))
                await websocket.send_json({"event": "authenticated", "user_id": ticket_data["user_id"]})
            else:
                await websocket.send_json({"event": "error", "message": "Invalid or expired connection ticket"})

        while True:
            # 1. Live token expiration check
            if manager.is_token_expired(websocket):
                logger.info("Closing WebSocket connection due to expired token.")
                await websocket.send_json({"event": "error", "message": "Token expired"})
                await websocket.close(code=4001)  # 4001: Token Expired
                break

            try:
                # Bounded receive with periodic expiration checks
                data_str = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send heartbeat ping on idle
                if manager.is_token_expired(websocket):
                    await websocket.send_json({"event": "error", "message": "Token expired"})
                    await websocket.close(code=4001)
                    break
                await websocket.send_json({"event": "ping"})
                continue

            try:
                msg = json.loads(data_str)
            except Exception:
                await websocket.send_json({"event": "error", "message": "Malformed JSON payload"})
                continue

            action = msg.get("action", "").lower()

            # Handle First-Message Authentication (JWT or Ticket)
            if action == "auth":
                token = msg.get("token")
                ticket = msg.get("ticket")

                if ticket:
                    ticket_data = await manager.claim_ticket(ticket)
                    if not ticket_data:
                        await websocket.send_json({"event": "error", "message": "Invalid or expired ticket"})
                        continue
                    if not await manager.authenticate_connection(websocket, ticket_data):
                        await websocket.send_json({"event": "error", "message": "User connection limit exceeded"})
                        await websocket.close(code=4029)
                        break
                    schedule_server_expiry_timer(ticket_data.get("exp", time.time() + 3600))
                    await websocket.send_json({"event": "authenticated", "user_id": ticket_data["user_id"]})
                    continue

                if token:
                    try:
                        payload = decode_access_token(
                            token=token,
                            secret_key=settings.SECRET_KEY,
                            algorithm=settings.ALGORITHM,
                        )
                        user_id = str(payload.get("sub", ""))
                        exp = payload.get("exp", time.time() + 900)
                        user_info = {
                            "user_id": user_id,
                            "email": payload.get("email", ""),
                            "is_superuser": bool(payload.get("is_superuser", False)),
                            "exp": exp,
                        }
                        if not await manager.authenticate_connection(websocket, user_info):
                            await websocket.send_json({"event": "error", "message": "User connection limit exceeded"})
                            await websocket.close(code=4029)
                            break
                        schedule_server_expiry_timer(exp)
                        await websocket.send_json({"event": "authenticated", "user_id": user_id})
                    except TokenExpiredError:
                        await websocket.send_json({"event": "error", "message": "Token expired"})
                    except InvalidTokenError:
                        await websocket.send_json({"event": "error", "message": "Invalid authentication token"})
                    continue

                await websocket.send_json({"event": "error", "message": "Missing token or ticket in auth payload"})
                continue

            # Handle Subscription
            elif action == "subscribe":
                channel = msg.get("channel", "").strip()
                if not channel:
                    await websocket.send_json({"event": "error", "message": "Missing channel in subscribe request"})
                    continue

                allowed, reason = manager.authorize_subscription(websocket, channel)
                if not allowed:
                    await websocket.send_json({
                        "event": "error",
                        "message": reason,
                        "channel": channel,
                    })
                    continue

                manager.subscribe(websocket, channel)
                await websocket.send_json({
                    "event": "subscribed",
                    "channel": channel,
                })
                continue

            # Handle Unsubscription
            elif action == "unsubscribe":
                channel = msg.get("channel", "").strip()
                manager.unsubscribe(websocket, channel)
                await websocket.send_json({"event": "unsubscribed", "channel": channel})
                continue

            # Handle Ping/Pong
            elif action == "ping":
                await websocket.send_json({"event": "pong", "ts": int(time.time())})
                continue

            else:
                await websocket.send_json({"event": "error", "message": f"Unsupported action: '{action}'"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket exception: %s", exc)
    finally:
        if expiry_task and not expiry_task.done():
            expiry_task.cancel()
        await manager.disconnect(websocket)


@router.websocket("/ws")
async def websocket_gateway_endpoint(
    websocket: WebSocket,
    ticket: Optional[str] = Query(None),
):
    """
    Real-Time WebSocket Gateway mounted at /api/v1/ws (and /ws).
    Accepts public clients and enforces channel-level ownership on private subscriptions.
    """
    await handle_websocket_session(websocket, initial_ticket=ticket)
