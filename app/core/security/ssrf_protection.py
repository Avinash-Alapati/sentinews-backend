"""
SSRF (Server-Side Request Forgery) Defensive Validation.

Blocks all private, loopback, link-local, multicast, and cloud metadata ranges
(including AWS IMDS 169.254.169.254) before initiating any outbound HTTP requests.
"""

import ipaddress
import socket
from typing import Any, Callable, Optional, Set
from urllib.parse import urlparse

# Disallowed IP Networks (RFC 1918, RFC 3927, RFC 6598, RFC 5737, RFC 2544, RFC 1112, RFC 4291)
BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),          # Current network (only valid as source address)
    ipaddress.ip_network("10.0.0.0/8"),         # RFC 1918 Private Class A
    ipaddress.ip_network("100.64.0.0/10"),      # RFC 6598 Shared Address Space (Carrier-grade NAT)
    ipaddress.ip_network("127.0.0.0/8"),        # RFC 1122 Loopback
    ipaddress.ip_network("169.254.0.0/16"),     # RFC 3927 Link-local / AWS IMDS (169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),      # RFC 1918 Private Class B
    ipaddress.ip_network("192.0.0.0/24"),       # RFC 6890 IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),       # RFC 5737 Documentation (TEST-NET-1)
    ipaddress.ip_network("192.168.0.0/16"),     # RFC 1918 Private Class C
    ipaddress.ip_network("198.18.0.0/15"),      # RFC 2544 Benchmarking
    ipaddress.ip_network("198.51.100.0/24"),    # RFC 5737 Documentation (TEST-NET-2)
    ipaddress.ip_network("203.0.113.0/24"),     # RFC 5737 Documentation (TEST-NET-3)
    ipaddress.ip_network("224.0.0.0/4"),        # RFC 1112 Multicast
    ipaddress.ip_network("240.0.0.0/4"),        # RFC 1112 Reserved
    ipaddress.ip_network("255.255.255.255/32"), # RFC 919 Broadcast
    # IPv6 Blocked Ranges
    ipaddress.ip_network("::/128"),             # Unspecified
    ipaddress.ip_network("::1/128"),           # Loopback
    ipaddress.ip_network("::ffff:0:0/96"),      # IPv4-mapped IPv6
    ipaddress.ip_network("64:ff9b::/96"),       # IPv4/IPv6 translation
    ipaddress.ip_network("100::/64"),           # Discard-only prefix
    ipaddress.ip_network("2001:db8::/32"),      # Documentation
    ipaddress.ip_network("fc00::/7"),           # Unique local address (ULA)
    ipaddress.ip_network("fe80::/10"),          # Link-local unicast
    ipaddress.ip_network("ff00::/8"),           # Multicast
]

BLOCKED_HOSTNAMES: Set[str] = {
    "localhost",
    "metadata.google.internal",
    "169.254.169.254",
    "instance-data",
    "aws-metadata",
}


class SSRFValidationError(ValueError):
    """Raised when an outbound URL resolves to a prohibited internal or private network destination."""
    pass


def validate_outbound_url(url: str, allow_http: bool = False, resolver_fn: Optional[Callable] = None) -> str:
    """
    Validates that a URL is well-formed, uses HTTP(S), does not point to
    prohibited hostnames, and resolves only to public, non-private IP addresses.

    Raises:
        SSRFValidationError: If the URL target is invalid, private, loopback, or link-local.
    """
    if not url or not isinstance(url, str):
        raise SSRFValidationError("URL must be a non-empty string.")

    try:
        parsed = urlparse(url.strip())
    except Exception as exc:
        raise SSRFValidationError(f"Invalid URL structure: {exc}")

    # 1. Scheme Check
    valid_schemes = ("http", "https") if allow_http else ("https",)
    if parsed.scheme.lower() not in valid_schemes:
        raise SSRFValidationError(
            f"Invalid URL scheme '{parsed.scheme}'. Only {valid_schemes} are permitted for outbound requests."
        )

    # 2. Hostname extraction
    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError("URL does not contain a valid hostname.")

    hostname_lower = hostname.lower().strip(".")

    # 3. Blocked Hostname Check
    if hostname_lower in BLOCKED_HOSTNAMES or hostname_lower.endswith(".internal") or hostname_lower.endswith(".local"):
        raise SSRFValidationError(f"Access to internal or reserved hostname '{hostname}' is prohibited.")

    # 4. IP Literal or DNS Resolution Check
    try:
        ip_obj = ipaddress.ip_address(hostname_lower)
        _verify_ip_is_public(ip_obj)
    except ValueError:
        if resolver_fn:
            try:
                resolved_ips = resolver_fn(hostname, 443)
                for ip_str in resolved_ips:
                    ip_obj = ipaddress.ip_address(ip_str)
                    _verify_ip_is_public(ip_obj)
            except Exception as exc:
                if isinstance(exc, SSRFValidationError):
                    raise
                raise SSRFValidationError(f"Host verification failed for '{hostname}': {exc}")
        else:
            try:
                addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for entry in addr_info:
                    ip_str = entry[4][0]
                    ip_obj = ipaddress.ip_address(ip_str)
                    _verify_ip_is_public(ip_obj)
            except socket.gaierror as exc:
                raise SSRFValidationError(f"DNS resolution failed for hostname '{hostname}': {exc}")
            except Exception as exc:
                if isinstance(exc, SSRFValidationError):
                    raise
                raise SSRFValidationError(f"Host verification failed for '{hostname}': {exc}")

    return url


def _verify_ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Asserts that an IP address does not belong to any prohibited/private/link-local CIDR block."""
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise SSRFValidationError(
            f"SSRF Protection: Access to private/reserved IP destination '{ip}' is strictly blocked."
        )

    for blocked_net in BLOCKED_NETWORKS:
        if ip in blocked_net:
            raise SSRFValidationError(
                f"SSRF Protection: IP '{ip}' falls within restricted CIDR range '{blocked_net}'."
            )


def validate_redirect_hop(location_url: str, base_url: Optional[str] = None, allow_http: bool = False) -> str:
    """
    Validates an HTTP redirect target from a 'Location' header.
    Resolves relative paths against base_url and re-validates the target IP/host
    to prevent open redirect SSRF attacks targeting internal IP ranges.
    """
    if not location_url or not isinstance(location_url, str):
        raise SSRFValidationError("Redirect Location header is empty or invalid.")

    from urllib.parse import urljoin
    target = urljoin(base_url, location_url) if base_url else location_url
    return validate_outbound_url(target, allow_http=allow_http)


# ============================================================================
# Connect-Time IP-Pinned SSRF-Safe Async Transport
# ============================================================================

import anyio
import httpcore
from httpcore._backends.anyio import AnyIOBackend, AnyIOStream
import httpx
from typing import Any, Callable


class SSRFSafeNetworkBackend(AnyIOBackend):
    """
    Custom httpcore network backend that validates target IPs against private/reserved CIDRs
    and connects directly to the pre-validated IP address, completely eliminating DNS rebinding
    and TOCTOU vulnerabilities.
    """

    def __init__(self, resolver_fn: Optional[Callable] = None):
        super().__init__()
        self.resolver_fn = resolver_fn

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Any] = None,
    ) -> httpcore.AsyncNetworkStream:
        hostname_lower = host.lower().strip(".")
        if hostname_lower in BLOCKED_HOSTNAMES or hostname_lower.endswith(".internal") or hostname_lower.endswith(".local"):
            raise SSRFValidationError(f"Access to internal or reserved hostname '{host}' is prohibited.")

        if self.resolver_fn:
            resolved_ips = self.resolver_fn(host, port)
        else:
            try:
                ip_obj = ipaddress.ip_address(hostname_lower)
                resolved_ips = [str(ip_obj)]
            except ValueError:
                try:
                    addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                    resolved_ips = [entry[4][0] for entry in addr_info]
                except socket.gaierror as exc:
                    raise SSRFValidationError(f"DNS resolution failed for hostname '{host}': {exc}") from exc

        if not resolved_ips:
            raise SSRFValidationError(f"No IP addresses resolved for hostname '{host}'.")

        validated_ip_str = None
        for ip_str in resolved_ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                _verify_ip_is_public(ip_obj)
                if validated_ip_str is None:
                    validated_ip_str = ip_str
            except Exception as exc:
                if isinstance(exc, SSRFValidationError):
                    raise
                raise SSRFValidationError(f"Invalid IP address '{ip_str}' resolved for '{host}': {exc}") from exc

        if not validated_ip_str:
            raise SSRFValidationError(f"No valid public IP address available for '{host}'.")

        if socket_options is None:
            socket_options = []

        exc_map = {
            TimeoutError: httpcore.ConnectTimeout,
            OSError: httpcore.ConnectError,
            anyio.BrokenResourceError: httpcore.ConnectError,
        }
        from httpcore._exceptions import map_exceptions
        with map_exceptions(exc_map):
            with anyio.fail_after(timeout):
                stream: anyio.abc.ByteStream = await anyio.connect_tcp(
                    remote_host=validated_ip_str,
                    remote_port=port,
                    local_host=local_address,
                )
                for option in socket_options:
                    stream._raw_socket.setsockopt(*option)

        return AnyIOStream(stream)


class SSRFSafeAsyncTransport(httpx.AsyncHTTPTransport):
    """
    SSRF-Safe httpx.AsyncHTTPTransport using SSRFSafeNetworkBackend to guarantee
    all outbound TCP sockets connect directly to pre-validated public IPs.
    """

    def __init__(self, *args, resolver_fn: Optional[Callable] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=self._pool._ssl_context,
            max_connections=self._pool._max_connections,
            max_keepalive_connections=self._pool._max_keepalive_connections,
            keepalive_expiry=self._pool._keepalive_expiry,
            http1=self._pool._http1,
            http2=self._pool._http2,
            retries=self._pool._retries,
            network_backend=SSRFSafeNetworkBackend(resolver_fn=resolver_fn),
        )


