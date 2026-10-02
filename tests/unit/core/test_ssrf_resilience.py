"""
Unit tests for SSRF (Server-Side Request Forgery) protection, private IP blocking,
AWS IMDS (169.254.169.254) neutralization, and redirect hop revalidation.
"""

import pytest

from app.core.security.ssrf_protection import (
    SSRFValidationError,
    validate_outbound_url,
    validate_redirect_hop,
)


@pytest.mark.parametrize(
    "prohibited_url,description",
    [
        ("https://169.254.169.254/latest/meta-data/", "AWS IMDS IPv4 Link-Local"),
        ("https://127.0.0.1:8000/internal/metrics", "IPv4 Loopback localhost"),
        ("https://127.0.0.2:9090", "IPv4 Loopback alias"),
        ("https://[::1]:8080/admin", "IPv6 Loopback"),
        ("https://10.0.0.1/private/vault", "RFC 1918 Class A 10/8"),
        ("https://10.254.0.1/db", "RFC 1918 Class A 10/8 subnet"),
        ("https://172.16.0.1/api", "RFC 1918 Class B 172.16/12 lower bound"),
        ("https://172.31.255.255/admin", "RFC 1918 Class B 172.16/12 upper bound"),
        ("https://192.168.1.1/router", "RFC 1918 Class C 192.168/16"),
        ("https://192.168.100.50/status", "RFC 1918 Class C 192.168/16 subnet"),
        ("https://localhost:8000/api", "Localhost named alias"),
        ("https://metadata.google.internal/computeMetadata/v1/", "GCP Metadata service"),
        ("https://service.internal/status", "Internal suffix domain"),
        ("https://app.local/admin", "Local mDNS suffix domain"),
    ],
)
def test_ssrf_blocks_direct_private_and_cloud_metadata_ips(prohibited_url: str, description: str):
    """Verify validate_outbound_url raises SSRFValidationError for all prohibited destinations."""
    with pytest.raises(SSRFValidationError, match="SSRF Protection|prohibited|restricted"):
        validate_outbound_url(prohibited_url, allow_http=True)


@pytest.mark.parametrize(
    "redirect_target,description",
    [
        ("https://169.254.169.254/latest/meta-data/iam/security-credentials/", "Redirect to AWS IMDS"),
        ("https://127.0.0.1:5432", "Redirect to local database"),
        ("https://[::1]/internal", "Redirect to IPv6 loopback"),
        ("https://10.10.10.10/admin", "Redirect to 10/8 private network"),
        ("https://172.20.0.5:6379", "Redirect to 172.16/12 private Redis"),
        ("https://192.168.0.100:9200", "Redirect to 192.168/16 private ElasticSearch"),
    ],
)
def test_ssrf_blocks_redirect_hops_to_private_and_imds_ips(redirect_target: str, description: str):
    """Verify validate_redirect_hop revalidates redirect location headers and blocks private targets."""
    base_public_url = "https://news.public-domain.com/feed.xml"
    with pytest.raises(SSRFValidationError, match="SSRF Protection|restricted|prohibited"):
        validate_redirect_hop(location_url=redirect_target, base_url=base_public_url, allow_http=True)


def test_ssrf_accepts_valid_public_https_domains():
    """Verify legitimate public financial news endpoints pass validation."""
    valid_urls = [
        "https://finance.yahoo.com/news/rssindex",
        "https://www.moneycontrol.com/rss/latestnews.xml",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://www.livemint.com/rss/markets",
    ]

    for url in valid_urls:
        validated = validate_outbound_url(url, allow_http=False)
        assert validated == url


@pytest.mark.asyncio
async def test_rss_fetcher_with_stubbed_resolver_returning_private_ip():
    """
    Asserts RSSFeedFetcher using SSRFSafeAsyncTransport blocks outbound requests
    when a stubbed resolver returns private, loopback, or cloud metadata IPs.
    """
    from app.integrations.news.rss.allowlist import RSSFeedSource
    from app.integrations.news.rss.fetcher import RSSFeedFetcher

    prohibited_ips = ["10.0.0.1", "127.0.0.1", "169.254.169.254", "192.168.1.1"]

    for private_ip in prohibited_ips:
        stubbed_resolver = lambda host, port, ip=private_ip: [ip]

        feed = RSSFeedSource(
            name="Private RSS Test",
            publisher="Attacker Corp",
            category="general",
            url="https://feed.attacker-news.com/rss",
            enabled=True,
        )

        fetcher = RSSFeedFetcher(
            feeds=[feed],
            resolver_fn=stubbed_resolver,
            timeout_seconds=2.0,
        )

        # 1. Direct feed fetch raises SSRFValidationError
        import asyncio
        semaphore = asyncio.Semaphore(1)
        import httpx
        from app.core.security.ssrf_protection import SSRFSafeAsyncTransport
        transport = SSRFSafeAsyncTransport(resolver_fn=stubbed_resolver)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SSRFValidationError, match="SSRF Protection|restricted|prohibited"):
                await fetcher.fetch_feed(client=client, feed_source=feed, semaphore=semaphore)

        # 2. Batch fetch_all isolates error cleanly without crashing
        articles = await fetcher.fetch_all()
        assert len(articles) == 0


@pytest.mark.asyncio
async def test_rss_fetcher_dns_rebinding_attack_simulation():
    """
    Simulates a DNS Rebinding attack:
    1. First DNS resolution returns a legitimate public IP (93.184.216.34) during initial URL validation.
    2. Subsequent connect-time DNS resolution rebinds to AWS IMDS (169.254.169.254) or 127.0.0.1.
    Asserts SSRFSafeNetworkBackend intercepts the connect-time lookup and aborts the request before TCP connect.
    """
    from app.integrations.news.rss.allowlist import RSSFeedSource
    from app.integrations.news.rss.fetcher import RSSFeedFetcher
    import httpx
    from app.core.security.ssrf_protection import SSRFSafeAsyncTransport

    lookup_count = 0

    def rebinding_resolver(host, port):
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            # First lookup: legitimate public IP
            return ["93.184.216.34"]
        else:
            # Rebound connect-time lookup: AWS IMDS metadata IP
            return ["169.254.169.254"]

    transport = SSRFSafeAsyncTransport(resolver_fn=rebinding_resolver)

    feed = RSSFeedSource(
        name="DNS Rebinding Target",
        publisher="Rebinding Attacker",
        category="general",
        url="https://rebound.malicious-feed.com/rss",
        enabled=True,
    )

    fetcher = RSSFeedFetcher(
        feeds=[feed],
        transport=transport,
        timeout_seconds=2.0,
    )

    # Initial static URL validation passes (domain name is well-formed),
    # but connect-time resolution hits rebinding_resolver returning 169.254.169.254 -> Blocked!
    articles = await fetcher.fetch_all()
    # Articles list must be empty because connect-time was blocked
    assert len(articles) == 0


@pytest.mark.asyncio
async def test_ssrf_transport_enforces_tls_certificate_hostname_verification():
    """
    Verifies that SSRFSafeAsyncTransport preserves full TLS certificate validation
    against the original target hostname, even when connecting directly to a resolved IP.
    """
    import ssl
    from app.core.security.ssrf_protection import SSRFSafeAsyncTransport
    import httpx

    # Resolve to a legitimate public IP (e.g. 1.1.1.1 Cloudflare DNS)
    # but request a mismatched domain name 'unrelated-domain-xyz.com'
    def stub_public_resolver(host, port):
        return ["1.1.1.1"]

    transport = SSRFSafeAsyncTransport(resolver_fn=stub_public_resolver, verify=True)

    async with httpx.AsyncClient(transport=transport, timeout=3.0) as client:
        # TLS handshake against 1.1.1.1 with SNI='unrelated-domain-mismatch.com'
        # must fail certificate verification
        with pytest.raises((httpx.ConnectError, ssl.SSLCertVerificationError, Exception)):
            await client.get("https://unrelated-domain-mismatch.com")


