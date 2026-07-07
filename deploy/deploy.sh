#!/usr/bin/env bash
# Safe redeploy: pull latest, rebuild images, recreate containers.
# The Postgres 'pgdata' volume is NOT touched, so DB data survives every deploy.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== deploy $(date -Is) ==="
git fetch --all --prune
git reset --hard "origin/${DEPLOY_BRANCH:-main}"

docker compose up -d --build

echo "waiting for db to accept connections..."
until docker compose exec -T db pg_isready -U cq -d cq >/dev/null 2>&1; do sleep 1; done

# Idempotent schema apply — every statement is IF NOT EXISTS, so this only
# ADDS new tables/indexes and never drops or alters existing data.
docker compose exec -T db psql -U cq -d cq < backend/db/schema.sql

docker image prune -f >/dev/null 2>&1 || true
echo "=== deploy done $(date -Is) ==="
