"""
Process Lifecycle, Signal Handlers, Fault Handlers, and OOM Detection.

Manages clean shutdown detection, 10s memory heartbeat logs, faulthandler,
and async loop exception handlers.
"""

import asyncio
import faulthandler
import logging
import os
import signal
import sys
import tempfile
import time
from typing import Optional
import psutil

from app.infrastructure.observability.context import upstream_in_flight_ctx
from app.infrastructure.observability.metrics import app_restarts_total
from app.infrastructure.observability.signatures import crash_engine

logger = logging.getLogger("sentinews.observability.lifecycle")

CLEAN_SHUTDOWN_MARKER = os.getenv("SHUTDOWN_MARKER_FILE", os.path.join(tempfile.gettempdir(), ".sentinews_clean_shutdown"))


class ProcessLifecycleManager:
    """
    Coordinates process shutdown, unclean restart detection, signal handling, and memory heartbeats.
    """

    def __init__(self, marker_file: Optional[str] = None):
        self._marker_file = marker_file or CLEAN_SHUTDOWN_MARKER
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._is_running = False

    def setup_early_handlers(self) -> None:
        """Enables faulthandler and registers system-level excepthooks."""
        # Enable C-level traceback dumper on segfaults / aborts
        faulthandler.enable()

        # Global sys.excepthook for unhandled synchronous exceptions
        old_excepthook = sys.excepthook

        def custom_excepthook(exc_type, exc_value, exc_tb):
            try:
                sig_id, loc, prom_sig = crash_engine.compute_signature(exc_value, endpoint="process")
                crash_engine.record_crash(exc_value, endpoint="process")
                logger.critical(
                    "Fatal unhandled process exception [sig:%s, loc:%s]: %s",
                    sig_id,
                    loc,
                    str(exc_value),
                    exc_info=(exc_type, exc_value, exc_tb),
                )
            except Exception:
                pass
            old_excepthook(exc_type, exc_value, exc_tb)

        sys.excepthook = custom_excepthook

        # Check for unclean exit from previous run
        self._detect_restart_status()

    def _detect_restart_status(self) -> None:
        """Detects whether previous process exit was clean or unclean (e.g. OOM-kill)."""
        app_restarts_total.inc()

        if os.path.exists(self._marker_file):
            try:
                os.remove(self._marker_file)
                logger.info("Application startup clean. Previous shutdown was graceful.")
            except Exception:
                pass
        else:
            logger.warning("Restart detected: previous exit was UNCLEAN (possible OOM-kill or SIGKILL on Render).")

    def start_lifecycle_tasks(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Registers async loop exception handlers, signal hooks, and memory heartbeat."""
        target_loop = loop or asyncio.get_event_loop()
        self._is_running = True

        # Asyncio loop exception handler
        def custom_loop_exception_handler(async_loop, context):
            exc = context.get("exception")
            msg = context.get("message", "Unhandled async task exception")
            if exc:
                sig_id, loc, prom_sig = crash_engine.compute_signature(exc, endpoint="async_task")
                crash_engine.record_crash(exc, endpoint="async_task")
                logger.error(
                    "Asyncio task crash [sig:%s, loc:%s]: %s - %s",
                    sig_id,
                    loc,
                    msg,
                    str(exc),
                    exc_info=exc,
                )
            else:
                logger.error("Asyncio loop error: %s", msg)

        target_loop.set_exception_handler(custom_loop_exception_handler)

        # Register SIGTERM / SIGINT handlers
        try:
            if sys.platform != "win32":
                for sig in (signal.SIGTERM, signal.SIGINT):
                    target_loop.add_signal_handler(sig, self._handle_signal_shutdown)
            else:
                signal.signal(signal.SIGTERM, lambda s, f: self._handle_signal_shutdown())
                signal.signal(signal.SIGINT, lambda s, f: self._handle_signal_shutdown())
        except Exception as e:
            logger.debug("Signal handler registration note: %s", e)

        # Launch 10-second memory heartbeat logger
        self._heartbeat_task = target_loop.create_task(self._memory_heartbeat_loop())

    def _handle_signal_shutdown(self) -> None:
        """Logs termination metadata and marks clean shutdown."""
        try:
            proc = psutil.Process()
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            inflight = upstream_in_flight_ctx.get()
            logger.info("Shutdown signal received: memory_rss=%.2fMB, inflight_upstream=%d", rss_mb, inflight)
        except Exception:
            logger.info("Shutdown signal received")

        self.mark_clean_shutdown()

    def mark_clean_shutdown(self) -> None:
        """Writes clean shutdown marker file."""
        try:
            with open(self._marker_file, "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass

    async def _memory_heartbeat_loop(self) -> None:
        """Emits memory usage heartbeat every 10 seconds to aid in OOM-kill forensic analysis."""
        proc = psutil.Process()
        while self._is_running:
            try:
                await asyncio.sleep(10.0)
                mem = proc.memory_info()
                rss_mb = mem.rss / (1024 * 1024)
                logger.debug("last_known_memory heartbeat: rss=%.2fMB, vms=%.2fMB", rss_mb, mem.vms / (1024 * 1024))
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(10.0)

    def stop(self) -> None:
        """Stops background heartbeat loop and writes clean shutdown marker."""
        self._is_running = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self.mark_clean_shutdown()


lifecycle_manager = ProcessLifecycleManager()
