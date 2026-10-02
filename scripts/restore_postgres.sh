#!/usr/bin/env bash
# ==============================================================================
# SentiNews PostgreSQL Restore Script
# Restores a compressed SQL dump (.sql.gz) into target PostgreSQL database
# ==============================================================================

set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Usage: $0 <path_to_backup_file.sql.gz>"
    exit 1
fi

BACKUP_FILE="$1"
POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-sentinews}"

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "[ERROR] Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

echo "[INFO] Starting database restore into ${POSTGRES_DB} from ${BACKUP_FILE}..."

PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" gunzip -c "${BACKUP_FILE}" | PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" psql \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    --single-transaction

echo "[INFO] Database restore completed successfully."
