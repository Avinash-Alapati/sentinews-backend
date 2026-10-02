"""
Security Headers ASGI Middleware.

Injects enterprise security headers into all HTTP responses:
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- Referrer-Policy: strict-origin-when-cross-origin
- Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
- Permissions-Policy: geolocation=(), camera=(), microphone=()
- Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; object-src 'none';

Enforces:
- Request body size limit (2MB ceiling, rejecting with 413 Payload Too Large)
- WebSocket Origin validation against allowed CORS origins in production
"""

import json
from typing import Callable, List, Set
from urllib.parse import urlparse
from app.core.config import settings

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"),
    (b"content-security-policy", b"default-src 'self'; frame-ancestors 'none'; object-src 'none';"),
]

HSTS_HEADER = (b"strict-transport-security", b"max-age=31536000; includeSubDomains; preload")
MAX_BODY_SIZE_BYTES = 2 * 1024 * 1024  # 2MB


class PayloadTooLargeError(Exception):
    """Raised when streaming or chunked request body exceeds maximum allowed size."""
    pass


class SecurityHeadersMiddleware:
    """Pure ASGI middleware enforcing security headers, payload size limits, and WebSocket origin check."""

    def __init__(self, app: Callable):
        self.app = app
        self._is_production = settings.ENVIRONMENT.lower() == "production"

    def _is_origin_allowed(self, origin: str) -> bool:
        if not origin:
            return True
        allowed_origins: Set[str] = set(settings.CORS_ORIGINS)
        if origin in allowed_origins or "*" in allowed_origins:
            return True
        # Check domain match without port if applicable
        try:
            origin_netloc = urlparse(origin).netloc
            for allowed in allowed_origins:
                if urlparse(allowed).netloc == origin_netloc:
                    return True
        except Exception:
            pass
        return False

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        scope_type = scope.get("type")

        # 1. WebSocket Origin validation
        if scope_type == "websocket":
            if settings.ENVIRONMENT.lower() == "production":
                headers = dict(scope.get("headers", []))
                origin = headers.get(b"origin", b"").decode("latin1", errors="ignore").strip()
                if origin and not self._is_origin_allowed(origin):
                    await send({"type": "websocket.close", "code": 4003})
                    return
            await self.app(scope, receive, send)
            return

        # 2. Pass-through non-HTTP
        if scope_type != "http":
            await self.app(scope, receive, send)
            return

        # 3. Request Body Size Guard (Content-Length check)
        headers = dict(scope.get("headers", []))
        content_length_raw = headers.get(b"content-length")
        if content_length_raw:
            try:
                content_length = int(content_length_raw)
                if content_length > MAX_BODY_SIZE_BYTES:
                    body = json.dumps({
                        "detail": "Request body exceeds maximum allowed size (2MB)",
                        "error": "payload_too_large",
                    }).encode("utf-8")
                    await send({
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("latin1")),
                            (b"connection", b"close"),
                        ],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": body,
                    })
                    return
            except ValueError:
                pass

        # Dynamic Streaming / Chunked Transfer Meter
        total_received_bytes = 0

        async def receive_wrapper() -> dict:
            nonlocal total_received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"")
                total_received_bytes += len(chunk)
                if total_received_bytes > MAX_BODY_SIZE_BYTES:
                    from starlette.exceptions import HTTPException
                    raise HTTPException(
                        status_code=413,
                        detail="Request body exceeds maximum allowed size (2MB)",
                    )
            return message

        # 4. Response Security Headers Injection
        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                headers_list = list(message.get("headers", []))
                for header_name, header_val in SECURITY_HEADERS:
                    headers_list.append((header_name, header_val))
                if self._is_production:
                    headers_list.append(HSTS_HEADER)
                message["headers"] = headers_list
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as exc:
            from starlette.exceptions import HTTPException as StarletteHTTPException
            if isinstance(exc, (PayloadTooLargeError, StarletteHTTPException)) and getattr(exc, "status_code", 413) == 413:
                body = json.dumps({
                    "detail": "Request body exceeds maximum allowed size (2MB)",
                    "error": "payload_too_large",
                }).encode("utf-8")
                await send({
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("latin1")),
                        (b"connection", b"close"),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": body,
                })
                return
            raise exc



