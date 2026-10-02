"""
Prometheus Metrics Contract Definitions for Sentinews.

Implements exact metric names, labels, and histogram bucket distributions
defined in the APM specification.
"""

from prometheus_client import Counter, Gauge, Histogram
from app.infrastructure.observability.registry import REGISTRY

# =========================================================================
# RED METRICS (Rate, Errors, Duration)
# =========================================================================

HTTP_REQUEST_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

http_requests_total = Counter(
    "http_requests_total",
    "Total count of HTTP requests processed",
    ["method", "endpoint", "status_class"],
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "Latency distribution of HTTP requests in seconds",
    ["method", "endpoint"],
    buckets=HTTP_REQUEST_DURATION_BUCKETS,
    registry=REGISTRY,
)

http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["endpoint"],
    registry=REGISTRY,
)

http_errors_total = Counter(
    "http_errors_total",
    "Total count of HTTP request errors classified by root cause",
    ["endpoint", "cause"],
    registry=REGISTRY,
)

http_unhandled_exceptions_total = Counter(
    "http_unhandled_exceptions_total",
    "Total count of unhandled exceptions raised during HTTP request handling",
    ["endpoint", "exception_type"],
    registry=REGISTRY,
)

# =========================================================================
# SYSTEM & RUNTIME METRICS
# =========================================================================

app_cpu_percent = Gauge(
    "app_cpu_percent",
    "Current process CPU utilization percentage",
    registry=REGISTRY,
)

app_memory_rss_bytes = Gauge(
    "app_memory_rss_bytes",
    "Current process resident set size (RSS) memory in bytes",
    registry=REGISTRY,
)

app_memory_limit_bytes = Gauge(
    "app_memory_limit_bytes",
    "Configured or cgroup memory limit for the application container in bytes",
    registry=REGISTRY,
)

app_open_fds = Gauge(
    "app_open_fds",
    "Number of open file descriptors for the process",
    registry=REGISTRY,
)

app_threads = Gauge(
    "app_threads",
    "Total active thread count for the application process",
    registry=REGISTRY,
)

event_loop_lag_seconds = Gauge(
    "event_loop_lag_seconds",
    "Current asyncio event loop latency drift in seconds",
    registry=REGISTRY,
)

event_loop_lag_seconds_hist = Histogram(
    "event_loop_lag_distribution_seconds",
    "Asyncio event loop lag distribution in seconds",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=REGISTRY,
)

threadpool_tokens_total = Gauge(
    "threadpool_tokens_total",
    "Total capacity of worker threads in the AnyIO default threadpool limiter",
    registry=REGISTRY,
)

threadpool_tokens_borrowed = Gauge(
    "threadpool_tokens_borrowed",
    "Currently borrowed/active threads in the AnyIO thread limiter",
    registry=REGISTRY,
)

threadpool_waiting = Gauge(
    "threadpool_waiting",
    "Number of tasks waiting to acquire a thread in the AnyIO thread limiter",
    registry=REGISTRY,
)

app_start_time_seconds = Gauge(
    "app_start_time_seconds",
    "Epoch timestamp when the application process started",
    registry=REGISTRY,
)

app_restarts_total = Counter(
    "app_restarts_total",
    "Total count of application startup/restart events",
    registry=REGISTRY,
)

# =========================================================================
# DATABASE METRICS
# =========================================================================

db_pool_size = Gauge(
    "db_pool_size",
    "Current target connection pool size of the SQLAlchemy engine",
    registry=REGISTRY,
)

db_pool_checked_out = Gauge(
    "db_pool_checked_out",
    "Number of database connections currently checked out from the pool",
    registry=REGISTRY,
)

db_pool_overflow = Gauge(
    "db_pool_overflow",
    "Number of overflow connections currently created beyond pool_size",
    registry=REGISTRY,
)

db_pool_checked_in = Gauge(
    "db_pool_checked_in",
    "Number of idle database connections currently checked in to the pool",
    registry=REGISTRY,
)

db_pool_wait_seconds = Histogram(
    "db_pool_wait_seconds",
    "Time spent waiting to checkout a database connection from the pool in seconds",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=REGISTRY,
)

db_pool_timeouts_total = Counter(
    "db_pool_timeouts_total",
    "Total count of connection checkout timeouts from the database pool",
    registry=REGISTRY,
)

db_query_duration_seconds = Histogram(
    "db_query_duration_seconds",
    "Latency distribution of database queries categorized by SQL operation",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=REGISTRY,
)

db_errors_total = Counter(
    "db_errors_total",
    "Total count of database errors categorized by cause",
    ["reason"],
    registry=REGISTRY,
)

# =========================================================================
# FINANCIAL & DOMAIN COMPUTE METRICS
# =========================================================================

COMPUTE_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)

compute_duration_seconds = Histogram(
    "compute_duration_seconds",
    "Execution duration of heavy computational algorithms in seconds",
    ["function"],
    buckets=COMPUTE_DURATION_BUCKETS,
    registry=REGISTRY,
)

compute_in_progress = Gauge(
    "compute_in_progress",
    "Number of heavy compute routines currently running",
    ["function"],
    registry=REGISTRY,
)

compute_failures_total = Counter(
    "compute_failures_total",
    "Total count of computational function exceptions",
    ["function"],
    registry=REGISTRY,
)

upstream_request_duration_seconds = Histogram(
    "upstream_request_duration_seconds",
    "Latency distribution of outbound third-party HTTP requests in seconds",
    ["provider", "operation"],
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

upstream_requests_total = Counter(
    "upstream_requests_total",
    "Total count of outbound HTTP requests by provider and outcome",
    ["provider", "outcome"],
    registry=REGISTRY,
)

market_data_staleness_seconds = Gauge(
    "market_data_staleness_seconds",
    "Elapsed time in seconds since the last successful market tick/feed update",
    ["provider"],
    registry=REGISTRY,
)

job_duration_seconds = Histogram(
    "job_duration_seconds",
    "Execution latency of background worker tasks in seconds",
    ["job"],
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
    registry=REGISTRY,
)

job_failures_total = Counter(
    "job_failures_total",
    "Total count of background worker job failures",
    ["job"],
    registry=REGISTRY,
)

ws_connections_active = Gauge(
    "ws_connections_active",
    "Number of currently open and active WebSocket connections",
    registry=REGISTRY,
)

rate_limit_rejections_total = Counter(
    "rate_limit_rejections_total",
    "Total count of client requests rejected due to rate limiting",
    ["endpoint"],
    registry=REGISTRY,
)

rate_limit_fallback_active_total = Counter(
    "rate_limit_fallback_active_total",
    "Total count of requests processed under local in-memory rate limiting fallback when Redis is unavailable",
    ["tier"],
    registry=REGISTRY,
)


# =========================================================================
# CRASH SIGNATURE METRICS
# =========================================================================

crash_signature_total = Counter(
    "crash_signature_total",
    "Occurrence count of unique exception crash signatures",
    ["signature_id", "exception_type", "location", "endpoint"],
    registry=REGISTRY,
)

# =========================================================================
# PHASE 3: FETCHER-BROKER, CACHE, & CIRCUIT BREAKER METRICS
# =========================================================================

external_calls_in_request_path_total = Counter(
    "external_calls_in_request_path_total",
    "Total count of forbidden external HTTP calls attempted inside user request path",
    ["provider", "endpoint"],
    registry=REGISTRY,
)

market_cache_requests_total = Counter(
    "market_cache_requests_total",
    "Total count of market intelligence envelope cache lookups by outcome status",
    ["resource", "status"],
    registry=REGISTRY,
)

market_cache_age_seconds = Gauge(
    "market_cache_age_seconds",
    "Current age in seconds of cached market resources",
    ["resource"],
    registry=REGISTRY,
)

market_fetcher_duration_seconds = Histogram(
    "market_fetcher_duration_seconds",
    "Duration of background market data fetcher cycles in seconds",
    ["job"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

market_fetcher_operations_total = Counter(
    "market_fetcher_operations_total",
    "Total count of background market fetcher executions categorized by job and outcome",
    ["job", "status"],
    registry=REGISTRY,
)

circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "Current state of the upstream provider circuit breaker (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
    ["provider"],
    registry=REGISTRY,
)

circuit_breaker_trips_total = Counter(
    "circuit_breaker_trips_total",
    "Total count of times an upstream provider circuit breaker tripped to OPEN",
    ["provider"],
    registry=REGISTRY,
)
