"""
Prometheus Metrics Registry and Collector Setup.

Handles standard single-process mode and multiprocess mode via PROMETHEUS_MULTIPROC_DIR
when WEB_CONCURRENCY > 1.
"""

import os
import logging
from typing import Optional, Callable
from prometheus_client import (
    CollectorRegistry,
    REGISTRY,
    multiprocess,
    ProcessCollector,
    GCCollector,
)
from prometheus_client.registry import Collector

logger = logging.getLogger("sentinews.observability.registry")


class RedisQueueDepthCollector(Collector):
    """
    Custom Prometheus Collector that queries Redis LLEN for active task queues on scrape.
    Evaluates dynamically during scrape to avoid background polling overhead.
    """

    def __init__(self, redis_getter: Optional[Callable] = None):
        self._redis_getter = redis_getter
        self._queues = ["celery", "default", "priority", "notifications"]

    def set_redis_getter(self, getter: Callable) -> None:
        self._redis_getter = getter

    def collect(self):
        from prometheus_client.core import GaugeMetricFamily

        gauge = GaugeMetricFamily(
            "queue_depth",
            "Current number of pending tasks in the Redis queue",
            labels=["queue"],
        )

        if self._redis_getter is None:
            for q in self._queues:
                gauge.add_metric([q], 0.0)
            yield gauge
            return

        try:
            redis_client = self._redis_getter()
            if redis_client is None:
                for q in self._queues:
                    gauge.add_metric([q], 0.0)
                yield gauge
                return

            # Check if sync or async client
            for q in self._queues:
                depth = 0
                try:
                    if hasattr(redis_client, "llen"):
                        # If sync redis
                        val = redis_client.llen(q)
                        depth = int(val) if val is not None else 0
                except Exception:
                    depth = 0
                gauge.add_metric([q], float(depth))
            yield gauge
        except Exception as exc:
            logger.debug("Failed scraping queue_depth from Redis: %s", exc)
            for q in self._queues:
                gauge.add_metric([q], 0.0)
            yield gauge


# Global Redis Queue Depth Collector instance
queue_depth_collector = RedisQueueDepthCollector()


def get_registry() -> CollectorRegistry:
    """
    Returns the appropriate CollectorRegistry.
    If PROMETHEUS_MULTIPROC_DIR is configured and WEB_CONCURRENCY > 1,
    returns a multiprocess CollectorRegistry.
    Otherwise, returns the default global REGISTRY with default collectors.
    """
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    web_concurrency = int(os.environ.get("WEB_CONCURRENCY", "1"))

    if multiproc_dir and web_concurrency > 1 and os.path.exists(multiproc_dir):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry

    return REGISTRY
