import pytest
from app.core.config import settings
from app.core.middleware.rate_limit import RateLimitMiddleware
from app.api.v1.websocket import resolve_client_ip


@pytest.mark.parametrize(
    "trusted_count,x_forwarded_for,expected_ip",
    [
        # 1 Proxy (Direct Caddy): Real client IP is ips[-1]
        (1, "203.0.113.195", "203.0.113.195"),
        # 1 Proxy with Spoofed Prefix: Attacker sends "1.2.3.4", Caddy appends "203.0.113.195" -> "1.2.3.4, 203.0.113.195"
        (1, "1.2.3.4, 203.0.113.195", "203.0.113.195"),
        # 1 Proxy with Multi-Hop Spoofing: Attacker sends "10.0.0.1, 10.0.0.2", Caddy appends "198.51.100.5"
        (1, "10.0.0.1, 10.0.0.2, 198.51.100.5", "198.51.100.5"),
        # 2 Proxies (Cloudflare -> Caddy): Real client IP is ips[-2], Caddy is ips[-1]
        (2, "203.0.113.195, 172.70.100.1", "203.0.113.195"),
        # 2 Proxies with Spoofed Prefix: "spoofed.ip, 203.0.113.195, 172.70.100.1" -> selects "203.0.113.195"
        (2, "10.0.0.1, 203.0.113.195, 172.70.100.1", "203.0.113.195"),
        # Edge Case: Fewer hops in header than trusted proxy count -> safely returns leftmost IP
        (2, "203.0.113.195", "203.0.113.195"),
    ],
)
def test_rate_limit_middleware_extract_ip_with_trusted_proxies(trusted_count, x_forwarded_for, expected_ip, monkeypatch):
    """
    Verify RateLimitMiddleware properly extracts the client IP according to TRUSTED_PROXY_COUNT,
    preventing proxy IP rate-limiting collisions and spoofed header bypassing.
    """
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", trusted_count)
    middleware = RateLimitMiddleware(app=None)
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", x_forwarded_for.encode("latin1"))],
        "client": ("127.0.0.1", 12345),
    }

    extracted_ip = middleware._extract_ip(scope)
    assert extracted_ip == expected_ip


@pytest.mark.parametrize(
    "trusted_count,x_forwarded_for,expected_ip",
    [
        (1, "203.0.113.195", "203.0.113.195"),
        (1, "1.2.3.4, 203.0.113.195", "203.0.113.195"),
        (2, "203.0.113.195, 172.70.100.1", "203.0.113.195"),
        (2, "10.0.0.1, 203.0.113.195, 172.70.100.1", "203.0.113.195"),
    ],
)
def test_websocket_resolve_client_ip_with_trusted_proxies(trusted_count, x_forwarded_for, expected_ip, monkeypatch):
    """
    Verify WebSocket resolve_client_ip resolves the real client IP respecting TRUSTED_PROXY_COUNT.
    """
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", trusted_count)
    scope = {
        "type": "websocket",
        "headers": [(b"x-forwarded-for", x_forwarded_for.encode("latin1"))],
        "client": ("127.0.0.1", 12345),
    }

    extracted_ip = resolve_client_ip(scope)
    assert extracted_ip == expected_ip
