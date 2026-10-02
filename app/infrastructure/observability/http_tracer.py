"""
Outbound HTTP Client Tracing and Upstream Metrics Instrumentation.

Instruments httpx.AsyncClient outbound calls to financial providers, OAuth services,
and RSS feeds, recording duration histograms and classified outcomes.
"""

import time
import httpx
from typing import Any, Callable, Dict, Optional
from app.infrastructure.observability.context import endpoint_ctx, upstream_in_flight_ctx
from app.infrastructure.observability.metrics import (
    external_calls_in_request_path_total,
    market_data_staleness_seconds,
    upstream_request_duration_seconds,
    upstream_requests_total,
)


def identify_provider(url_str: str) -> str:
    """Maps destination URL/host to standardized provider label."""
    url_lower = url_str.lower()
    if "googleapis.com" in url_lower or "google.com" in url_lower:
        return "google_oauth"
    elif "upstox.com" in url_lower:
        return "upstox"
    elif "yahoo.com" in url_lower or "finance.yahoo" in url_lower:
        return "yahoo_finance"
    elif "finnhub.io" in url_lower:
        return "finnhub"
    elif "alphavantage.co" in url_lower:
        return "alphavantage"
    elif "twelvedata.com" in url_lower:
        return "twelvedata"
    elif "stocknewsapi.com" in url_lower or "stocknews" in url_lower:
        return "stocknews"
    elif any(feed in url_lower for feed in ("economictimes", "moneycontrol", "livemint", "business-standard", "reuters", "ndtv")):
        return "rss_feeds"
    return "external_http"


def extract_http_operation(request: httpx.Request) -> str:
    """Extracts a high-level operation name from the HTTP request."""
    path = request.url.path
    if "quote" in path:
        return "get_quote"
    elif "history" in path or "chart" in path:
        return "get_history"
    elif "token" in path or "oauth" in path:
        return "oauth_token"
    elif "userinfo" in path:
        return "oauth_userinfo"
    elif "rss" in path or "feed" in path or path.endswith(".xml"):
        return "fetch_rss"
    elif "market-status" in path:
        return "market_status"
    elif "calendar" in path:
        return "economic_calendar"
    elif "category" in path or "news" in path:
        return "fetch_news"
    return request.method.lower()


class TracedAsyncTransport(httpx.AsyncBaseTransport):
    """
    HTTPX AsyncTransport wrapper that records upstream latency and outcome metrics.
    """

    def __init__(self, wrapped_transport: httpx.AsyncBaseTransport, provider_override: Optional[str] = None):
        self._wrapped = wrapped_transport
        self._provider_override = provider_override

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        provider = self._provider_override or identify_provider(str(request.url))
        operation = extract_http_operation(request)

        # Increment in-flight counter
        current_inflight = upstream_in_flight_ctx.get()
        upstream_in_flight_ctx.set(current_inflight + 1)

        # Flag any outbound HTTP calls triggered directly in any user request path
        current_endpoint = endpoint_ctx.get()
        if current_endpoint:
            try:
                external_calls_in_request_path_total.labels(provider=provider, endpoint=current_endpoint).inc()
            except Exception:
                pass

        start_time = time.perf_counter()
        outcome = "success"

        try:
            response = await self._wrapped.handle_async_request(request)
            if response.status_code >= 500:
                outcome = "http_5xx"
            elif response.status_code >= 400:
                outcome = "http_4xx"
            else:
                outcome = "success"
                # Update market data staleness tick on success
                if provider in ("yahoo_finance", "upstox", "finnhub", "alphavantage", "twelvedata", "stocknews"):
                    market_data_staleness_seconds.labels(provider=provider).set(0.0)

            return response
        except httpx.TimeoutException:
            outcome = "timeout"
            raise
        except (httpx.ConnectError, httpx.NetworkError):
            outcome = "connection_error"
            raise
        except Exception:
            outcome = "connection_error"
            raise
        finally:
            duration = time.perf_counter() - start_time
            # Decrement in-flight
            upstream_in_flight_ctx.set(max(0, upstream_in_flight_ctx.get() - 1))

            try:
                upstream_requests_total.labels(provider=provider, outcome=outcome).inc()
                upstream_request_duration_seconds.labels(provider=provider, operation=operation).observe(duration)
            except Exception:
                pass


def instrument_httpx_client(client: httpx.AsyncClient, provider: Optional[str] = None) -> httpx.AsyncClient:
    """Instruments an existing httpx.AsyncClient with tracing transport."""
    if not isinstance(client._transport, TracedAsyncTransport):
        client._transport = TracedAsyncTransport(client._transport, provider_override=provider)
    return client


def create_traced_async_client(provider: Optional[str] = None, **kwargs) -> httpx.AsyncClient:
    """Factory helper creating an instrumented httpx.AsyncClient."""
    client = httpx.AsyncClient(**kwargs)
    return instrument_httpx_client(client, provider=provider)
