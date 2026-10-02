"""
Celery Application Configuration for SentiNews background jobs.
"""

import os
import ssl
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_BROKER_URL = os.getenv("REDIS_BROKER_URL") or REDIS_URL
IS_REDIS_TLS = REDIS_BROKER_URL.startswith("rediss://") or REDIS_URL.startswith("rediss://") or os.getenv("REDIS_TLS_ENABLED", "").lower() in ("true", "1")

ssl_cert_map = {
    "none": ssl.CERT_NONE,
    "optional": ssl.CERT_OPTIONAL,
    "required": ssl.CERT_REQUIRED,
}
ssl_cert_req = ssl_cert_map.get(os.getenv("REDIS_SSL_CERT_REQS", "none").lower(), ssl.CERT_NONE)

ssl_options = {"ssl_cert_reqs": ssl_cert_req} if IS_REDIS_TLS else None

celery_app = Celery(
    "sentinews_workers",
    broker=REDIS_BROKER_URL,
    backend=REDIS_URL,
    include=[
        "workers.tasks.portfolio_sync",
        "workers.tasks.notifications",
        "workers.tasks.news_tasks",
        "workers.tasks.market_report_tasks",
        "workers.tasks.market_tasks",
    ],
)

celery_conf = {
    "task_serializer": "json",
    "accept_content": ["json"],
    "result_serializer": "json",
    "timezone": "UTC",
    "enable_utc": True,
    "task_track_started": True,
    "task_acks_late": True,
    "worker_prefetch_multiplier": 1,
    "task_soft_time_limit": 180,  # 3 minutes soft limit (raises SoftTimeLimitExceeded)
    "task_time_limit": 300,       # 5 minutes hard limit (SIGKILL)
    "result_expires": 3600,       # Expire task results after 1 hour to prevent Redis bloat
    "worker_max_tasks_per_child": 1000,  # Prevent memory leaks from long-running ML/regex workers
    "broker_connection_retry_on_startup": True,
    "broker_connection_max_retries": 10,
    "task_publish_retry": True,
    "broker_transport_options": {
        "visibility_timeout": 3600,
        "socket_timeout": 5.0,
        "socket_connect_timeout": 5.0,
        "socket_keepalive": True,
    },
    "beat_schedule": {
        "periodic-rss-news-ingestion": {
            "task": "workers.tasks.news_tasks.ingest_rss_news_task",
            "schedule": 600.0,  # Every 10 minutes
        },
        "daily-expired-articles-cleanup": {
            "task": "workers.tasks.news_tasks.cleanup_expired_articles_task",
            "schedule": 86400.0,  # Every 24 hours
        },
        "proactive-market-overview-cache-warming": {
            "task": "workers.tasks.market_tasks.warm_market_overview_cache_task",
            "schedule": 25.0,  # Proactively refresh every 25s (under 30s TTL)
        },
    },
}

if IS_REDIS_TLS:
    celery_conf["broker_use_ssl"] = ssl_options
    celery_conf["redis_backend_use_ssl"] = ssl_options

celery_app.conf.update(**celery_conf)
