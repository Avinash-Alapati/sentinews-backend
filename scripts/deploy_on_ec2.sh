#!/bin/bash
set -euo pipefail

echo "========================================================"
echo " [INFO] Starting SentiNews EC2 Deployment"
echo " Date: $(date -u)"
echo " Working Dir: $(pwd)"
echo "========================================================"

cd /opt/sentinews

# 1. Fetch latest secrets from AWS SSM Parameter Store
echo "[1/6] Fetching production secrets from SSM Parameter Store (/sentinews/dev)..."
aws ssm get-parameters-by-path \
  --path "/sentinews/dev" \
  --with-decryption \
  --region ap-southeast-2 \
  --query "Parameters[*].[Name,Value]" \
  --output text | while read -r name value; do
    key=$(basename "$name")
    echo "${key}=${value}"
  done > .env.tmp

# Append deployment domain and environment flags
echo "DOMAIN=api.13.54.138.191.nip.io" >> .env.tmp
echo "ACME_EMAIL=avinashalapati11@gmail.com" >> .env.tmp
echo "ENVIRONMENT=production" >> .env.tmp

chmod 600 .env.tmp
mv .env.tmp .env
echo "[INFO] Successfully updated /opt/sentinews/.env"

# 2. Build Docker containers natively on Graviton3 ARM64
echo "[2/6] Building Docker container images natively..."
docker compose build

# 3. Execute Alembic Database Migrations
echo "[3/6] Running Alembic database migrations..."
docker compose --profile core run --rm migration

# 4. Bring up core & edge services
echo "[4/6] Bringing up application services with zero downtime..."
docker compose --profile core --profile edge up -d --remove-orphans

# 5. Local Healthcheck Verification
echo "[5/6] Verifying API container health (/healthz)..."
HEALTHY=0
for i in $(seq 1 15); do
  sleep 3
  if curl -sf http://localhost:8000/healthz > /dev/null; then
    echo "[SUCCESS] API is healthy and responding at http://localhost:8000/healthz (check ${i}/15)!"
    HEALTHY=1
    break
  fi
  echo "Waiting for API to become ready... (attempt ${i}/15)"
done

if [ "$HEALTHY" -ne 1 ]; then
  echo "[ERROR] Health check failed after 45 seconds!"
  docker compose ps
  docker compose logs --tail=60 api
  exit 1
fi

# 6. Cleanup dangling images to conserve disk space
echo "[6/6] Cleaning up unused dangling Docker images..."
docker image prune -f

echo "========================================================"
echo " [SUCCESS] SentiNews Deployment Completed Successfully!"
echo " Container Status:"
docker compose ps
echo "========================================================"
