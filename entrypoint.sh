#!/bin/sh
set -e

# Ensure Python can import top-level modules from /app
export PYTHONPATH="/app:${PYTHONPATH}"

# Initialize Prometheus multiprocess directory if configured
if [ -n "$PROMETHEUS_MULTIPROC_DIR" ]; then
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
    rm -f "$PROMETHEUS_MULTIPROC_DIR"/* || true
fi

# Dispatch based on first argument / container command
case "$1" in
    api)
        echo "Starting SentiNews API server with Gunicorn (workers: ${WEB_CONCURRENCY:-2})..."
        exec gunicorn -c gunicorn.conf.py app.main:app
        ;;
    api-dev)
        echo "Starting SentiNews API server in Development mode with auto-reload..."
        exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
        ;;
    worker)
        echo "Starting SentiNews Celery Worker (concurrency: ${CELERY_CONCURRENCY:-4})..."
        exec celery -A workers.celery_app.celery_app worker --loglevel="${LOG_LEVEL:-INFO}" --concurrency="${CELERY_CONCURRENCY:-4}" -Ofair
        ;;
    scheduler|beat)
        echo "Starting SentiNews Celery Beat Scheduler..."
        exec celery -A workers.celery_app.celery_app beat --loglevel="${LOG_LEVEL:-INFO}" -s /tmp/celerybeat-schedule
        ;;
    migration)
        echo "Running Alembic database migrations..."
        exec alembic upgrade head
        ;;
    test)
        echo "Running Pytest test suite inside container..."
        shift
        exec pytest "$@"
        ;;
    *)
        exec "$@"
        ;;
esac
