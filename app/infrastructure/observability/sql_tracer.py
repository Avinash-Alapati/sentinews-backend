"""
SQLAlchemy Database Event Listeners and Connection Pool Tracer.

Instruments query durations by SQL operation (select/insert/update/delete),
pool checkout wait times, pool connection gauges, and classified database errors.
"""

import logging
import re
import time
from typing import Any, Optional
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import Pool

from app.infrastructure.observability.metrics import (
    db_errors_total,
    db_pool_checked_in,
    db_pool_checked_out,
    db_pool_overflow,
    db_pool_size,
    db_pool_timeouts_total,
    db_pool_wait_seconds,
    db_query_duration_seconds,
)

logger = logging.getLogger("sentinews.observability.sql")

OP_PATTERN = re.compile(r"^\s*([a-zA-Z]+)", re.MULTILINE)
VALID_OPERATIONS = {"select", "insert", "update", "delete", "begin", "commit", "rollback"}


def extract_sql_operation(statement: str) -> str:
    """Extracts standardized SQL operation opcode without parameters or table names."""
    if not statement:
        return "other"
    match = OP_PATTERN.match(statement.strip())
    if match:
        op = match.group(1).lower()
        if op in VALID_OPERATIONS:
            return op
    return "other"


def classify_db_error(exc: BaseException) -> str:
    """Classifies a database exception into a bounded reason set."""
    msg = str(exc).lower()
    exc_name = type(exc).__name__.lower()

    if "timeout" in msg or "timeout" in exc_name:
        if "pool" in msg or "queuepool" in msg:
            return "pool_timeout"
        return "statement_timeout"
    elif "deadlock" in msg or "deadlock" in exc_name:
        return "deadlock"
    elif "connection" in msg or "disconnect" in msg or "broken pipe" in msg:
        return "connection_lost"
    return "other"


def setup_sql_tracing(engine_or_async: Any) -> None:
    """Attaches SQLAlchemy event listeners to monitor queries, pool state, and errors."""
    try:
        # Extract underlying sync engine if AsyncEngine
        if hasattr(engine_or_async, "sync_engine"):
            sync_engine: Engine = engine_or_async.sync_engine
        elif isinstance(engine_or_async, Engine):
            sync_engine = engine_or_async
        else:
            logger.warning("Unrecognized database engine type: %s", type(engine_or_async))
            return

        pool: Pool = sync_engine.pool

        # -------------------------------------------------------------
        # Cursor Execution Listeners (Query Duration by Operation)
        # -------------------------------------------------------------
        @event.listens_for(sync_engine, "before_cursor_execute")
        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("_query_start_time", []).append(time.perf_counter())

        @event.listens_for(sync_engine, "after_cursor_execute")
        def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            try:
                start_times = conn.info.get("_query_start_time")
                if start_times:
                    start_time = start_times.pop()
                    duration = time.perf_counter() - start_time
                    operation = extract_sql_operation(statement)
                    db_query_duration_seconds.labels(operation=operation).observe(duration)
            except Exception:
                pass

        @event.listens_for(sync_engine, "handle_error")
        def handle_error(exception_context):
            try:
                exc = exception_context.original_exception
                reason = classify_db_error(exc)
                db_errors_total.labels(reason=reason).inc()
                if reason == "pool_timeout":
                    db_pool_timeouts_total.inc()
            except Exception:
                pass

        # -------------------------------------------------------------
        # Pool Lifecycle Listeners (Checkout Latency & Gauges)
        # -------------------------------------------------------------
        @event.listens_for(pool, "checkout")
        def on_pool_checkout(dbapi_connection, connection_record, connection_proxy):
            try:
                checkout_start = getattr(connection_record.info, "checkout_start", None)
                if checkout_start:
                    wait_duration = time.perf_counter() - checkout_start
                    db_pool_wait_seconds.observe(wait_duration)

                # Update pool gauges
                update_pool_metrics(pool)
            except Exception:
                pass

        @event.listens_for(pool, "checkin")
        def on_pool_checkin(dbapi_connection, connection_record):
            try:
                update_pool_metrics(pool)
            except Exception:
                pass

        # Initial gauge update
        update_pool_metrics(pool)
        logger.info("Attached SQLAlchemy APM tracing and connection pool monitors")
    except Exception as exc:
        logger.warning("Failed attaching SQLAlchemy APM tracing (fail-open): %s", exc)


def update_pool_metrics(pool: Pool) -> None:
    """Updates database connection pool gauge values."""
    try:
        if hasattr(pool, "size"):
            db_pool_size.set(pool.size())
        if hasattr(pool, "checkedout"):
            db_pool_checked_out.set(pool.checkedout())
        if hasattr(pool, "overflow"):
            db_pool_overflow.set(pool.overflow())
        if hasattr(pool, "checkedin"):
            db_pool_checked_in.set(pool.checkedin())
    except Exception:
        pass
