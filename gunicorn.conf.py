"""
Production Gunicorn configuration for SentiNews FastAPI ASGI application.
"""

import os
import multiprocessing

# Network binding
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# Worker processes
# Default to 2 workers for small containers (0.5 - 1 vCPU)
web_concurrency = os.getenv("WEB_CONCURRENCY")
if web_concurrency:
    workers = int(web_concurrency)
else:
    # Cap auto-detected workers at 2 for containerized micro instances
    workers = min(2, max(1, multiprocessing.cpu_count()))

worker_class = "uvicorn.workers.UvicornWorker"

# Timeouts
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "65"))

# Server mechanics
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "10000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "1000"))
preload_app = False  # Set to False to allow each worker process to initialize its own asyncpg pool and event loop

# Access and Error Logging
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()


def child_exit(server, worker):
    """
    Cleans up per-process Prometheus metrics files upon Uvicorn worker termination.
    Prevents metric leakage and ghost gauges in multiprocess mode.
    """
    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir and os.path.exists(multiproc_dir):
        try:
            from prometheus_client import multiprocess
            multiprocess.mark_process_dead(worker.pid)
        except Exception:
            pass
