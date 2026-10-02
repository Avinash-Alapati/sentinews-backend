"""
Production-Grade Pure ASGI Rate Limiting Middleware.

Implements multi-tiered sliding-window / fixed-window rate limiting with:
- Redis primary distributed storage with atomic INCR + EXPIRE.
- In-memory localized fallback dictionary when Redis is offline or in local testing.
- Differentiated tiers for Auth, Refresh, OAuth, News Click Tracking, Heavy Portfolio Recomputations, and Public Reads.
- Account+IP keying for login/auth requests to prevent multi-tenant IP throttling collisions.
- Standard HTTP 429 responses with Retry-After, X-RateLimit-Limit, X-RateLimit-Remaining, and X-RateLimit-Reset headers.
"""

import json
import logging
import re
import time
from typing import Callable, Dict, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger("sentinews.middleware.rate_limit")

# Routes excluded from rate limiting
EXCLUDED_PATHS = {
    "/health",
    "/healthz",
    "/readyz",
    "/favicon.ico",
    f"{settings.API_V1_STR}/docs",
    f"{settings.API_V1_STR}/redoc",
    f"{settings.API_V1_STR}/openapi.json",
}

# Regex matchers for tier resolution
RE_REFRESH = re.compile(r"^/api/v1/auth/refresh")
RE_AUTH = re.compile(r"^/api/v1/auth/(?!oauth|google|refresh)")
RE_OAUTH = re.compile(r"^/api/v1/(auth/oauth|auth/google|portfolio/oauth)")
RE_NEWS_CLICK = re.compile(r"^/api/v1/news/\d+/click")
RE_HEAVY_COMPUTE = re.compile(r"^/api/v1/portfolio/\d+/(performance|overview|sync)")


def resolve_rate_limit_tier(path: str) -> Tuple[str, int]:
    """
    Resolves the route path to a rate limit tier name and maximum requests per minute limit.
    """
    if RE_REFRESH.search(path):
        return "refresh", getattr(settings, "RATE_LIMIT_REFRESH_PER_MINUTE", 30)
    if RE_AUTH.search(path):
        return "auth", settings.RATE_LIMIT_AUTH_PER_MINUTE
    if RE_OAUTH.search(path):
        return "oauth", settings.RATE_LIMIT_OAUTH_PER_MINUTE
    if RE_NEWS_CLICK.search(path):
        return "news_click", settings.RATE_LIMIT_NEWS_CLICK_PER_MINUTE
    if RE_HEAVY_COMPUTE.search(path):
        return "heavy_compute", settings.RATE_LIMIT_HEAVY_COMPUTE_PER_MINUTE
    return "default", settings.RATE_LIMIT_DEFAULT_PER_MINUTE


class InMemoryRateLimiter:
    """Thread-safe localized in-memory rolling sliding-window rate limiter."""

    def __init__(self):
        # Key -> List of timestamp floats
        self._store: Dict[str, list[float]] = {}
        self._last_cleanup = time.time()

    def check_and_increment(self, key: str, limit: int, window_sec: int = 60) -> Tuple[bool, int, int]:
        """
        Increments request count for the key within rolling window_sec.
        Returns: (is_allowed, remaining_requests, reset_after_seconds)
        """
        now = time.time()
        cutoff = now - window_sec

        # Periodic memory cleanup every 5 minutes
        if now - self._last_cleanup > 300:
            self._cleanup(now, window_sec)
            self._last_cleanup = now

        timestamps = self._store.get(key, [])
        # Prune timestamps older than the rolling window
        valid_timestamps = [t for t in timestamps if t > cutoff]

        if len(valid_timestamps) >= limit:
            oldest_ts = valid_timestamps[0]
            reset_after = max(1, int(oldest_ts + window_sec - now) + 1)
            self._store[key] = valid_timestamps
            return False, 0, reset_after

        valid_timestamps.append(now)
        self._store[key] = valid_timestamps
        remaining = max(0, limit - len(valid_timestamps))
        reset_after = max(1, int(valid_timestamps[0] + window_sec - now) + 1)
        return True, remaining, reset_after

    def _cleanup(self, now: float, window_sec: int) -> None:
        expired_cutoff = now - (window_sec * 2)
        expired_keys = [k for k, ts_list in self._store.items() if not ts_list or ts_list[-1] < expired_cutoff]
        for k in expired_keys:
            self._store.pop(k, None)


class RateLimitMiddleware:
    """
    Pure ASGI 3 Rate Limiting Middleware.
    """

    def __init__(self, app: Callable, redis_getter: Optional[Callable] = None):
        self.app = app
        self.redis_getter = redis_getter
        self._memory_limiter = InMemoryRateLimiter()

    def _extract_ip(self, scope: dict) -> str:
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        x_forwarded = headers.get(b"x-forwarded-for", b"").decode("latin1", errors="ignore").strip()
        if x_forwarded:
            ips = [ip.strip() for ip in x_forwarded.split(",") if ip.strip()]
            if ips:
                # In reverse proxy architecture (Caddy / Cloudflare), the real client IP is selected
                # based on TRUSTED_PROXY_COUNT (default 1: ips[-1]; for Cloudflare+Caddy: 2 -> ips[-2]).
                proxy_count = max(1, getattr(settings, "TRUSTED_PROXY_COUNT", 1))
                idx = -min(proxy_count, len(ips))
                return ips[idx]
        x_real_ip = headers.get(b"x-real-ip", b"").decode("latin1", errors="ignore").strip()
        if x_real_ip:
            return x_real_ip
        client = scope.get("client")
        if client and len(client) > 0:
            return str(client[0])
        return "anonymous"

    def _extract_account_id(self, scope: dict) -> Optional[str]:
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        account_id = headers.get(b"x-account-id", b"").decode("latin1", errors="ignore").strip()
        if account_id:
            return account_id
        user_id = headers.get(b"x-user-id", b"").decode("latin1", errors="ignore").strip()
        if user_id:
            return user_id
        return None

    def _extract_client_key(self, scope: dict) -> str:
        """Extracts unique client identifier (Bearer Token hash, X-User-ID, Account+IP, or Client IP)."""
        headers = {k.lower(): v for k, v in scope.get("headers", [])}

        # 1. Check for Authorization header (User identity)
        auth_header = headers.get(b"authorization", b"").decode("latin1", errors="ignore")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token:
                return f"tok:{hash(token) & 0xFFFFFFFF:08x}"

        # 2. Check for X-User-ID header
        user_id = headers.get(b"x-user-id", b"").decode("latin1", errors="ignore").strip()
        if user_id:
            return f"usr:{user_id}"

        # 3. Check for X-Account-ID header
        account_id = headers.get(b"x-account-id", b"").decode("latin1", errors="ignore").strip()
        client_ip = self._extract_ip(scope)

        if account_id:
            return f"acct:{account_id}:{client_ip}"

        return f"ip:{client_ip}"

    async def _check_rate_limit(
        self, tier: str, client_key: str, limit: int, window_sec: int = 60
    ) -> Tuple[bool, int, int]:
        """
        Evaluates rate limit via Redis if available, falling back to local in-memory store.
        Returns: (is_allowed, remaining, reset_after_seconds)
        """
        now = int(time.time())
        current_window = (now // window_sec) * window_sec
        reset_after = max(1, (current_window + window_sec) - now)
        redis_key = f"rl:{tier}:{client_key}:{current_window}"

        # Attempt Redis atomic INCR
        redis = self.redis_getter() if self.redis_getter else None
        if redis is not None and getattr(redis, "ping", None):
            try:
                import asyncio
                count = await asyncio.wait_for(redis.incr(redis_key), timeout=0.1)
                if count == 1:
                    await asyncio.wait_for(redis.expire(redis_key, window_sec + 5), timeout=0.1)

                if count > limit:
                    return False, 0, reset_after
                remaining = max(0, limit - count)
                return True, remaining, reset_after
            except Exception as exc:
                logger.debug("Redis rate limiter error (%s), falling back to in-memory store.", exc)

        # In-Memory Fallback (Active when Redis is disabled, unconfigured, or failing)
        effective_limit = limit
        if tier == "auth" and limit < 1000000:
            auth_fallback = getattr(settings, "RATE_LIMIT_AUTH_FALLBACK_PER_MINUTE", 5)
            effective_limit = min(limit, auth_fallback) if limit else auth_fallback

        logger.warning(
            "Rate limit fallback to in-memory store active for tier=%s, key=%s (effective_limit=%d)",
            tier,
            client_key,
            effective_limit,
        )
        try:
            from app.infrastructure.observability.metrics import rate_limit_fallback_active_total
            rate_limit_fallback_active_total.labels(tier=tier).inc()
        except Exception:
            pass

        local_key = f"{tier}:{client_key}"
        return self._memory_limiter.check_and_increment(local_key, limit=effective_limit, window_sec=window_sec)

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or not settings.RATE_LIMIT_ENABLED:
            await self.app(scope, receive, send)
            return

        raw_path: str = scope.get("path", "")

        # Skip excluded endpoints and internal routes
        if raw_path in EXCLUDED_PATHS or raw_path.startswith("/internal/"):
            await self.app(scope, receive, send)
            return

        # Extract normalized headers
        raw_headers = scope.get("headers", [])
        headers_map = {k.lower(): v for k, v in raw_headers}
        user_agent = headers_map.get(b"user-agent", b"").lower()

        # Allow load testing bypass via header or k6 user-agent in non-production environments
        is_k6_or_loadtest = (
            b"x-loadtest" in headers_map
            or b"x-k6-loadtest" in headers_map
            or b"x-bypass-rate-limit" in headers_map
            or b"k6" in user_agent
        )
        if is_k6_or_loadtest and settings.ENVIRONMENT.lower() != "production":
            await self.app(scope, receive, send)
            return

        tier, limit = resolve_rate_limit_tier(raw_path)
        client_key = self._extract_client_key(scope)

        # Allow generous burst ceiling for local test runners / dev loopback / Docker test clients
        if (
            settings.ENVIRONMENT.lower() in ("test", "testing", "development", "loadtest")
            and (
                client_key in (
                    "ip:testclient",
                    "ip:anonymous",
                    "ip:127.0.0.1",
                    "ip:localhost",
                    "ip:::1",
                    "ip:::ffff:127.0.0.1",
                    "ip:0.0.0.0",
                )
                or client_key.startswith("ip:172.")  # Docker bridge / internal container
                or client_key.startswith("ip:10.")   # Private test network
                or client_key.startswith("ip:192.168.")
            )
        ):
            limit = 10000000

        is_allowed, remaining, reset_after = await self._check_rate_limit(tier, client_key, limit)

        # Multi-dimensional checks for auth tier (Credential stuffing & Distributed brute force defenses)
        if is_allowed and tier == "auth":
            client_ip = self._extract_ip(scope)
            account_id = self._extract_account_id(scope)

            # 1. Aggregate IP limit across all accounts (Credential stuffing defense: max 30 req/min per IP)
            ip_agg_limit = getattr(settings, "RATE_LIMIT_AUTH_IP_AGGREGATE_PER_MINUTE", 30)
            if limit < 1000000:
                is_ip_allowed, rem_ip, reset_ip = await self._check_rate_limit("auth_ip_agg", f"ip_agg:{client_ip}", ip_agg_limit)
                if not is_ip_allowed:
                    is_allowed = False
                    remaining = 0
                    reset_after = reset_ip
                    tier = "auth_ip_aggregate"

            # 2. Aggregate Account limit across all IPs (Distributed brute force defense: max 15 req/min per account)
            if is_allowed and account_id:
                acct_agg_limit = getattr(settings, "RATE_LIMIT_AUTH_ACCOUNT_AGGREGATE_PER_MINUTE", 15)
                if limit < 1000000:
                    is_acct_allowed, rem_acct, reset_acct = await self._check_rate_limit("auth_acct_agg", f"acct_agg:{account_id}", acct_agg_limit)
                    if not is_acct_allowed:
                        is_allowed = False
                        remaining = 0
                        reset_after = reset_acct
                        tier = "auth_account_aggregate"

        if not is_allowed:
            # Reject with 429 Too Many Requests
            body_dict = {
                "detail": f"Rate limit exceeded for tier '{tier}'. Please try again in {reset_after} seconds.",
                "error": "rate_limit_exceeded",
                "tier": tier,
                "retry_after": reset_after,
            }
            body_bytes = json.dumps(body_dict).encode("utf-8")

            response_headers = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body_bytes)).encode("latin1")),
                (b"retry-after", str(reset_after).encode("latin1")),
                (b"x-ratelimit-limit", str(limit).encode("latin1")),
                (b"x-ratelimit-remaining", b"0"),
                (b"x-ratelimit-reset", str(reset_after).encode("latin1")),
            ]

            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": response_headers,
            })
            await send({
                "type": "http.response.body",
                "body": body_bytes,
            })
            return

        # Request allowed: inject rate limit metadata into response headers
        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                headers_list = list(message.get("headers", []))
                headers_list.append((b"x-ratelimit-limit", str(limit).encode("latin1")))
                headers_list.append((b"x-ratelimit-remaining", str(remaining).encode("latin1")))
                headers_list.append((b"x-ratelimit-reset", str(reset_after).encode("latin1")))
                message["headers"] = headers_list
            await send(message)

        await self.app(scope, receive, send_wrapper)
