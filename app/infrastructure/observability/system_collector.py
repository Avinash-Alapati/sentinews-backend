"""
System Metrics Collector and Event Loop Lag Tracker.

Periodically samples CPU utilization, RSS memory, container memory limits,
open file handles, thread counts, AnyIO threadpool limiter status, and asyncio event loop drift.
"""

import asyncio
import logging
import os
import time
from typing import Optional
import psutil

from app.infrastructure.observability.metrics import (
    app_cpu_percent,
    app_memory_limit_bytes,
    app_memory_rss_bytes,
    app_open_fds,
    app_restarts_total,
    app_start_time_seconds,
    app_threads,
    event_loop_lag_seconds,
    event_loop_lag_seconds_hist,
    threadpool_tokens_borrowed,
    threadpool_tokens_total,
    threadpool_waiting,
)

logger = logging.getLogger("sentinews.observability.system")


def get_memory_limit_bytes(configured_limit: int = 536870912) -> int:
    """
    Detects container memory limit from cgroup filesystem or fallback to configured limit (512 MB default).
    """
    # Check cgroup v2
    cgroup2_file = "/sys/fs/cgroup/memory.max"
    if os.path.exists(cgroup2_file):
        try:
            with open(cgroup2_file, "r") as f:
                val = f.read().strip()
                if val != "max":
                    return int(val)
        except Exception:
            pass

    # Check cgroup v1
    cgroup1_file = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    if os.path.exists(cgroup1_file):
        try:
            with open(cgroup1_file, "r") as f:
                val = int(f.read().strip())
                # cgroup v1 returns very large number (e.g. 9223372036854771712) when unlimited
                if val < 1099511627776:  # Less than 1 TB
                    return val
        except Exception:
            pass

    return configured_limit


class SystemCollector:
    """
    Runs background sampling tasks for system runtime metrics and asyncio event loop lag.
    """

    def __init__(self, memory_limit_bytes: int = 536870912):
        self._memory_limit_bytes = get_memory_limit_bytes(memory_limit_bytes)
        self._process = psutil.Process()
        self._lag_task: Optional[asyncio.Task] = None
        self._metrics_task: Optional[asyncio.Task] = None
        self._is_running = False

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Starts background monitoring loops."""
        if self._is_running:
            return
        self._is_running = True

        app_start_time_seconds.set(time.time())
        app_memory_limit_bytes.set(float(self._memory_limit_bytes))

        # Initial sample
        self.sample_system_metrics()

        # Launch background async loops
        target_loop = loop or asyncio.get_event_loop()
        self._lag_task = target_loop.create_task(self._event_loop_lag_loop())
        self._metrics_task = target_loop.create_task(self._periodic_metrics_loop())

    def stop(self) -> None:
        """Stops background monitoring loops."""
        self._is_running = False
        if self._lag_task and not self._lag_task.done():
            self._lag_task.cancel()
        if self._metrics_task and not self._metrics_task.done():
            self._metrics_task.cancel()

    def sample_system_metrics(self) -> None:
        """Collects instantaneous process CPU, RSS, FDs, and threads."""
        try:
            # CPU utilization
            cpu_pct = self._process.cpu_percent(interval=None)
            app_cpu_percent.set(cpu_pct)

            # Memory RSS
            mem_info = self._process.memory_info()
            app_memory_rss_bytes.set(float(mem_info.rss))

            # Threads
            app_threads.set(float(self._process.num_threads()))

            # File Descriptors / Handles
            if hasattr(self._process, "num_fds"):
                app_open_fds.set(float(self._process.num_fds()))
            elif hasattr(self._process, "num_handles"):
                app_open_fds.set(float(self._process.num_handles()))

            # AnyIO Thread Limiter inspection
            self._sample_anyio_threadpool()
        except Exception as exc:
            logger.debug("Error sampling system metrics: %s", exc)

    def _sample_anyio_threadpool(self) -> None:
        """Inspects AnyIO default capacity limiter if active."""
        try:
            import anyio.to_thread
            limiter = anyio.to_thread.current_default_thread_limiter()
            if limiter:
                threadpool_tokens_total.set(float(limiter.total_tokens))
                threadpool_tokens_borrowed.set(float(limiter.borrowed_tokens))
                # Waiting tasks
                waiting = getattr(limiter, "_waiters", None)
                if waiting is not None:
                    threadpool_waiting.set(float(len(waiting)))
                else:
                    threadpool_waiting.set(0.0)
        except Exception:
            pass

    async def _event_loop_lag_loop(self) -> None:
        """
        Async loop sleeping 100ms and recording the drift between requested and actual wakeup time.
        """
        sleep_duration = 0.1  # 100 ms
        while self._is_running:
            try:
                start = time.perf_counter()
                await asyncio.sleep(sleep_duration)
                drift = (time.perf_counter() - start) - sleep_duration
                lag = max(0.0, drift)
                event_loop_lag_seconds.set(lag)
                event_loop_lag_seconds_hist.observe(lag)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(sleep_duration)

    async def _periodic_metrics_loop(self) -> None:
        """Periodic loop refreshing psutil metrics every 5 seconds."""
        while self._is_running:
            try:
                self.sample_system_metrics()
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5.0)


system_collector = SystemCollector()
