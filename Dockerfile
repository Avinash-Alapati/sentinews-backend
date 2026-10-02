FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PORT=8000 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc \
    SHUTDOWN_MARKER_FILE=/tmp/.sentinews_clean_shutdown

# Install build dependencies, runtime libraries, and dumb-init
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    libpq5 \
    curl \
    dumb-init \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && apt-get purge -y gcc g++ libpq-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Install application dependencies into virtualenv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt && rm -f /tmp/requirements.txt

# Create unprivileged application user and runtime directories
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/sh -m appuser && \
    mkdir -p /app /tmp/prometheus_multiproc && \
    chown -R appuser:appuser /tmp/prometheus_multiproc

WORKDIR /app

# Copy application source code
COPY --chown=appuser:appuser . /app

# Ensure shell scripts have executable permissions
RUN chmod +x /app/entrypoint.sh

# Switch to non-root user
USER appuser

EXPOSE 8000

# Container liveness probe
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/healthz || exit 1

ENTRYPOINT ["dumb-init", "--", "/app/entrypoint.sh"]
CMD ["api"]
