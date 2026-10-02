#!/usr/bin/env bash
# ==============================================================================
# SentiNews PostgreSQL Backup Script
# Performs pg_dump -> gzip -> S3 upload with timestamping and error handling
# ==============================================================================

set -euo pipefail

POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-sentinews}"
BACKUP_DIR="${BACKUP_DIR:-/tmp/backups}"
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%SZ")
BACKUP_FILENAME="sentinews_backup_${TIMESTAMP}.sql.gz"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_FILENAME}"

mkdir -p "${BACKUP_DIR}"

echo "[INFO] Starting database backup for database: ${POSTGRES_DB} at ${TIMESTAMP}..."

# Execute streaming pg_dump -> gzip
PGPASSWORD="${POSTGRES_PASSWORD:-postgres}" pg_dump \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" \
    -d "${POSTGRES_DB}" \
    --no-owner \
    --no-acl \
    --clean \
    --if-exists | gzip -9 > "${BACKUP_PATH}"

BACKUP_SIZE=$(du -h "${BACKUP_PATH}" | cut -f1)
echo "[INFO] Backup created successfully: ${BACKUP_PATH} (${BACKUP_SIZE})"

# Upload to S3 if AWS_S3_BACKUP_BUCKET is configured
if [ -n "${AWS_S3_BACKUP_BUCKET:-}" ]; then
    echo "[INFO] Uploading backup to s3://${AWS_S3_BACKUP_BUCKET}/backups/${BACKUP_FILENAME}..."
    aws s3 cp "${BACKUP_PATH}" "s3://${AWS_S3_BACKUP_BUCKET}/backups/${BACKUP_FILENAME}" --sse AES256
    echo "[INFO] S3 upload completed successfully."
    # Clean up local temporary file after upload
    rm -f "${BACKUP_PATH}"
fi

echo "[INFO] Database backup workflow finished successfully."
