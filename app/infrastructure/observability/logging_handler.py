"""
Structured JSON Logging, PII/Secret Scrubbing, and Loki/Sentry Handlers.

Emits uniform JSON log records to stdout and optionally pushes to Grafana Loki in a non-blocking queue.
"""

import json
import logging
import queue
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import psutil

from app.infrastructure.observability.context import (
    get_current_endpoint,
    get_request_id,
    upstream_in_flight_ctx,
    user_id_hash_ctx,
)

logger = logging.getLogger("sentinews.observability.logging")

# Regex patterns for sensitive data scrubbing
SECRET_PATTERNS = [
    (re.compile(r"Bearer\s+([a-zA-Z0-9_\-\.]+)", re.IGNORECASE), r"Bearer [REDACTED_TOKEN]"),
    (re.compile(r'\b(authorization["\']?\s*[:=]\s*["\'])([^"\']+)(["\'])', re.IGNORECASE), r'\1[REDACTED_AUTH]\3'),
    (re.compile(r'\b(password["\']?\s*[:=]\s*["\']?)([^"\'\s,;]+)(["\']?)', re.IGNORECASE), r'\1[REDACTED_PASSWORD]\3'),
    (re.compile(r'\b((?:secret[_-]?key|secret)["\']?\s*[:=]\s*["\']?)([^"\'\s,;]+)(["\']?)', re.IGNORECASE), r'\1[REDACTED_SECRET]\3'),
    (re.compile(r'\b(api[_-]?key["\']?\s*[:=]\s*["\']?)([^"\'\s,;]+)(["\']?)', re.IGNORECASE), r'\1[REDACTED_KEY]\3'),
    (re.compile(r'\b(refresh[_-]?token["\']?\s*[:=]\s*["\']?)([^"\'\s,;]+)(["\']?)', re.IGNORECASE), r'\1[REDACTED_REFRESH_TOKEN]\3'),
    (re.compile(r'\b(access[_-]?token["\']?\s*[:=]\s*["\']?)([^"\'\s,;]+)(["\']?)', re.IGNORECASE), r'\1[REDACTED_ACCESS_TOKEN]\3'),
    (re.compile(r'\b(token["\']?\s*[:=]\s*["\']?)([^"\'\s,;]+)(["\']?)', re.IGNORECASE), r'\1[REDACTED_TOKEN]\3'),
    (re.compile(r'\b(ticket["\']?\s*[:=]\s*["\']?)([^"\'\s,;]+)(["\']?)', re.IGNORECASE), r'\1[REDACTED_TICKET]\3'),
    (re.compile(r'([?&](?:token|ticket|access_token|refresh_token|secret)=)[^&\s]+', re.IGNORECASE), r'\1[REDACTED]'),
    (re.compile(r"\bya29\.[a-zA-Z0-9_\-]+", re.IGNORECASE), r"[REDACTED_GOOGLE_TOKEN]"),
    (re.compile(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', re.IGNORECASE), r"[REDACTED_EMAIL]"),
]


def scrub_sensitive_data(text: str) -> str:
    """Scrubs tokens, passwords, keys, and PII from log strings."""
    if not text:
        return text
    scrubbed = str(text)
    for pattern, replacement in SECRET_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


class LokiPushQueueHandler(logging.Handler):
    """
    Non-blocking logging handler that batches and pushes log lines to Grafana Loki via HTTP.
    Uses an in-memory bounded queue and a background daemon thread.
    """

    def __init__(self, loki_url: str, app_name: str = "sentinews", env: str = "production"):
        super().__init__()
        self.loki_url = loki_url.rstrip("/") + "/loki/api/v1/push"
        self.app_name = app_name
        self.env = env
        self._queue: queue.Queue = queue.Queue(maxsize=1000)
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._push_worker, daemon=True)
        self._worker_thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._queue.put_nowait((record.created, record.levelname, msg))
        except (queue.Full, Exception):
            pass  # Drop under extreme backpressure to protect memory

    def _push_worker(self) -> None:
        import urllib.request

        while not self._stop_event.is_set():
            batch = []
            try:
                item = self._queue.get(timeout=2.0)
                batch.append(item)
                # Pull any additional pending items up to 50
                while len(batch) < 50:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                continue

            if not batch:
                continue

            try:
                streams = []
                for created, level, msg in batch:
                    # Loki timestamp is nanoseconds as string
                    ts_ns = str(int(created * 1_000_000_000))
                    streams.append({
                        "stream": {"app": self.app_name, "env": self.env, "level": level.lower()},
                        "values": [[ts_ns, msg]],
                    })

                if not (self.loki_url.startswith("http://") or self.loki_url.startswith("https://")):
                    return

                payload = json.dumps({"streams": streams}).encode("utf-8")
                req = urllib.request.Request(
                    self.loki_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:  # nosec B310
                    pass
            except Exception:
                pass  # Fail open on Loki network errors


class StructuredJsonFormatter(logging.Formatter):
    """
    Formats log records as structured JSON lines with request correlation and PII scrubbing.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_sensitive_data(record.getMessage()),
            "request_id": getattr(record, "request_id", get_request_id() or "none"),
            "endpoint": getattr(record, "endpoint", get_current_endpoint()),
        }

        # Include optional contextual APM attributes if present
        for attr in (
            "signature_id",
            "exception_type",
            "full_traceback",
            "user_id_hash",
            "duration_ms",
            "db_pool_state",
            "memory_rss",
            "upstream_in_flight",
        ):
            if hasattr(record, attr):
                val = getattr(record, attr)
                if attr == "full_traceback" and val:
                    log_entry[attr] = scrub_sensitive_data(str(val))
                else:
                    log_entry[attr] = val

        if record.exc_info and "full_traceback" not in log_entry:
            log_entry["full_traceback"] = scrub_sensitive_data(self.formatException(record.exc_info))
            if record.exc_info[0]:
                log_entry["exception_type"] = record.exc_info[0].__name__

        return json.dumps(log_entry, default=str)


def log_structured_crash(
    exc: BaseException,
    signature_id: str,
    location: str,
    endpoint: str = "unknown",
    duration_ms: float = 0.0,
    db_pool_state: str = "normal",
) -> None:
    """
    Logs a structured JSON crash record to stdout with comprehensive root-cause metadata.
    """
    try:
        import traceback

        rss_bytes = 0
        try:
            rss_bytes = psutil.Process().memory_info().rss
        except Exception:
            pass

        tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

        extra_data = {
            "signature_id": signature_id,
            "location": location,
            "exception_type": type(exc).__name__,
            "full_traceback": scrub_sensitive_data(tb_str),
            "endpoint": endpoint,
            "request_id": get_request_id() or "none",
            "user_id_hash": user_id_hash_ctx.get(),
            "duration_ms": round(duration_ms, 2),
            "db_pool_state": db_pool_state,
            "memory_rss": rss_bytes,
            "upstream_in_flight": upstream_in_flight_ctx.get(),
        }

        logger_root = logging.getLogger("sentinews.crash")
        logger_root.error(
            "Crash signature %s detected at %s: %s",
            signature_id,
            location,
            str(exc),
            extra=extra_data,
        )
    except Exception as e:
        sys.stderr.write(f"Failed writing structured crash log: {e}\n")


def sentry_scrub_sensitive_data(event: dict, hint: dict) -> Optional[dict]:
    """
    Scrubs PII, authorization headers, cookies, passwords, and sensitive fields
    from Sentry events before transmission to the Sentry server.
    """
    if not event or not isinstance(event, dict):
        return event

    # 1. Scrub Request Headers
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            for h in list(headers.keys()):
                if h.lower() in ("authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"):
                    headers[h] = "[REDACTED]"

        # 2. Scrub Request Body
        data = request.get("data")
        if isinstance(data, dict):
            for k in list(data.keys()):
                if any(s in k.lower() for s in ("password", "secret", "token", "key", "credential", "auth")):
                    data[k] = "[REDACTED]"
        elif isinstance(data, str):
            request["data"] = scrub_sensitive_data(data)

    # 3. Scrub Breadcrumbs
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict) and "values" in breadcrumbs:
        for b in breadcrumbs.get("values", []):
            if "message" in b and isinstance(b["message"], str):
                b["message"] = scrub_sensitive_data(b["message"])
            if "data" in b and isinstance(b["data"], dict):
                for k in list(b["data"].keys()):
                    if any(s in k.lower() for s in ("password", "secret", "token", "key", "auth")):
                        b["data"][k] = "[REDACTED]"

    # 4. Scrub Exception / Message values
    logentry = event.get("logentry")
    if isinstance(logentry, dict) and "message" in logentry:
        logentry["message"] = scrub_sensitive_data(str(logentry["message"]))

    return event


def setup_structured_logging(
    loki_url: Optional[str] = None,
    sentry_dsn: Optional[str] = None,
    service_name: str = "sentinews-api",
    env: str = "development",
) -> None:
    """Configures root JSON logging formatter, non-blocking Loki pusher, and Sentry SDK."""
    root = logging.getLogger()

    # Apply StructuredJsonFormatter to stdout handlers
    formatter = StructuredJsonFormatter()
    for h in root.handlers:
        h.setFormatter(formatter)

    # Attach non-blocking Loki handler if configured
    if loki_url:
        try:
            loki_handler = LokiPushQueueHandler(loki_url=loki_url, app_name=service_name, env=env)
            loki_handler.setFormatter(formatter)
            root.addHandler(loki_handler)
            logger.info("Attached non-blocking Loki log shipper targeting %s", loki_url)
        except Exception as e:
            logger.warning("Failed initializing Loki handler: %s", e)

    # Initialize Sentry SDK if DSN configured
    if sentry_dsn:
        try:
            import sentry_sdk
            sentry_sdk.init(
                dsn=sentry_dsn,
                environment=env,
                traces_sample_rate=0.1,
                release=service_name,
                before_send=sentry_scrub_sensitive_data,
            )
            logger.info("Initialized Sentry SDK for environment %s with before_send data scrubbing", env)
        except ImportError:
            logger.warning("Sentry DSN provided but sentry-sdk package not installed")
        except Exception as e:
            logger.warning("Failed initializing Sentry SDK: %s", e)

