"""
Pure ASGI Observability Middleware for Sentinews.

Implements low-overhead (<2% latency penalty) HTTP and WebSocket request lifecycle tracking,
request ID generation/propagation, route template resolution, exception classification,
and crash signature correlation.
"""

import sys
import time
import uuid
from typing import Callable, Optional
from app.infrastructure.observability.context import (
    endpoint_ctx,
    get_cache_status,
    get_stage_timings,
    init_stage_timings,
    request_id_ctx,
    request_start_time_ctx,
    set_current_endpoint,
    set_request_id,
)
from app.infrastructure.observability.logging_handler import log_structured_crash
from app.infrastructure.observability.metrics import (
    crash_signature_total,
    http_errors_total,
    http_request_duration_seconds,
    http_requests_in_progress,
    http_requests_total,
    http_unhandled_exceptions_total,
    rate_limit_rejections_total,
    ws_connections_active,
)
from app.infrastructure.observability.route_matcher import route_matcher
from app.infrastructure.observability.signatures import crash_engine

# Paths to exclude from standard request metrics
EXCLUDED_PATHS = {
    "/internal/metrics",
    "/internal/crash-signatures",
    "/healthz",
    "/readyz",
    "/health",
    "/favicon.ico",
}


def classify_error_cause(exc: BaseException) -> str:
    """Classifies an unhandled exception into a bounded root cause label."""
    msg = str(exc).lower()
    exc_type = type(exc).__name__.lower()

    if isinstance(exc, MemoryError) or "out of memory" in msg:
        return "oom"
    elif "pool" in msg or "pooltimeout" in exc_type or "queuepool" in msg:
        return "db_pool_exhausted"
    elif "timeout" in msg or "timeout" in exc_type:
        if "http" in msg or "upstream" in msg or "httpx" in msg:
            return "upstream_timeout"
        return "db_timeout"
    elif "ratelimit" in msg or "too many requests" in msg or "429" in msg:
        return "rate_limited"
    elif "upstream" in msg or "connection error" in msg:
        return "upstream_error"
    return "unhandled_exception"


def get_status_class(status_code: int) -> str:
    """Maps HTTP status code to status class string (2xx, 3xx, 4xx, 5xx)."""
    if 200 <= status_code < 300:
        return "2xx"
    elif 300 <= status_code < 400:
        return "3xx"
    elif 400 <= status_code < 500:
        return "4xx"
    elif 500 <= status_code < 600:
        return "5xx"
    return "unknown"


class PureASGIObservabilityMiddleware:
    """
    Pure ASGI 3 middleware tracking HTTP and WebSocket requests without BaseHTTPMiddleware buffering.
    """

    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        scope_type = scope.get("type")

        # Handle WebSocket connections
        if scope_type == "websocket":
            try:
                ws_connections_active.inc()
            except Exception:
                pass
            try:
                await self.app(scope, receive, send)
            finally:
                try:
                    ws_connections_active.dec()
                except Exception:
                    pass
            return

        # Pass through non-HTTP requests
        if scope_type != "http":
            await self.app(scope, receive, send)
            return

        raw_path: str = scope.get("path", "")
        method: str = scope.get("method", "GET").upper()

        # Check if path is an excluded internal probe
        if raw_path in EXCLUDED_PATHS:
            await self.app(scope, receive, send)
            return

        # 1. Resolve Route Template
        endpoint = route_matcher.match(raw_path, method=method)
        set_current_endpoint(endpoint)

        # 2. Extract or Generate Request ID
        request_id = ""
        headers = scope.get("headers", [])
        for name, value in headers:
            if name.lower() == b"x-request-id":
                request_id = value.decode("latin1", errors="ignore")
                break

        if not request_id:
            request_id = str(uuid.uuid4())

        set_request_id(request_id)
        init_stage_timings()
        start_time = time.perf_counter()
        request_start_time_ctx.set(start_time)

        # Increment in-progress gauge
        try:
            http_requests_in_progress.labels(endpoint=endpoint).inc()
        except Exception:
            pass

        status_code = 500
        response_started = False

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
                response_started = True

                dur_ms = (time.perf_counter() - start_time) * 1000.0
                timings = get_stage_timings()
                cache_status = get_cache_status()

                # Build Server-Timing header
                st_parts = [f"total;dur={dur_ms:.2f}"]
                for stage_name in ("redis", "db", "auth", "upstream", "serialization"):
                    stage_dur = timings.get(stage_name, 0.0)
                    if stage_dur > 0:
                        st_parts.append(f"{stage_name};dur={stage_dur:.2f}")
                server_timing_val = ", ".join(st_parts)

                # Inject observability headers into response
                headers_list = list(message.get("headers", []))
                headers_list.append((b"x-request-id", request_id.encode("latin1")))
                headers_list.append((b"x-response-time", f"{dur_ms:.2f}ms".encode("latin1")))
                headers_list.append((b"server-timing", server_timing_val.encode("latin1")))
                if cache_status and not any(h[0].lower() == b"x-cache" for h in headers_list):
                    headers_list.append((b"x-cache", cache_status.encode("latin1")))
                message["headers"] = headers_list

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException as exc:
            duration = time.perf_counter() - start_time
            duration_ms = duration * 1000.0
            exc_type = type(exc).__name__

            # Record crash signature & structured log
            try:
                sig_id, loc, prom_sig = crash_engine.compute_signature(exc, endpoint=endpoint)
                crash_engine.record_crash(exc, endpoint=endpoint)
                crash_signature_total.labels(
                    signature_id=prom_sig,
                    exception_type=exc_type,
                    location=loc,
                    endpoint=endpoint,
                ).inc()

                # Record unhandled exception metric
                http_unhandled_exceptions_total.labels(endpoint=endpoint, exception_type=exc_type).inc()

                # Classify error cause
                cause = classify_error_cause(exc)
                http_errors_total.labels(endpoint=endpoint, cause=cause).inc()

                # Write structured JSON crash log to stdout
                log_structured_crash(
                    exc=exc,
                    signature_id=sig_id,
                    location=loc,
                    endpoint=endpoint,
                    duration_ms=duration_ms,
                )
            except Exception as apm_err:
                sys.stderr.write(f"APM recording error (fail-open): {apm_err}\n")

            # Re-raise to let FastAPI exception handlers or server handle response
            raise exc
        finally:
            duration = time.perf_counter() - start_time
            duration_ms = duration * 1000.0
            timings = get_stage_timings()
            cache_status = get_cache_status()

            # Update RED metrics (fail-open)
            try:
                status_class = get_status_class(status_code)
                http_requests_total.labels(method=method, endpoint=endpoint, status_class=status_class).inc()
                http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)
                http_requests_in_progress.labels(endpoint=endpoint).dec()

                # Track rate limit rejections
                if status_code == 429:
                    rate_limit_rejections_total.labels(endpoint=endpoint).inc()
                    http_errors_total.labels(endpoint=endpoint, cause="rate_limited").inc()
                elif status_code >= 500 and not response_started:
                    http_errors_total.labels(endpoint=endpoint, cause="internal").inc()
            except Exception:
                pass

            # Structured request completion log
            try:
                import json
                from datetime import datetime, timezone
                req_log = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "level": "INFO" if status_code < 400 else ("WARN" if status_code < 500 else "ERROR"),
                    "logger": "sentinews.access",
                    "message": f"HTTP {method} {raw_path} {status_code} ({duration_ms:.2f}ms)",
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "method": method,
                    "path": raw_path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                    "timings_ms": {k: round(v, 2) for k, v in timings.items()},
                    "x_cache": cache_status or "N/A",
                }
                sys.stdout.write(json.dumps(req_log) + "\n")
                sys.stdout.flush()
            except Exception:
                pass
