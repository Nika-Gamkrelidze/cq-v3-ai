#!/usr/bin/env bash
# Push-to-deploy: fast-forward main and rebuild the cqv3 stack.
# Idempotent DB migrations run on API startup (services/migrate.py); the pgdata
# and hf_cache volumes are NEVER touched, so all data survives every deploy.
set -euo pipefail
cd /home/cqdeploy/cq-v3-ai

echo "=== deploy $(date -Is) ==="
git pull --ff-only origin "${DEPLOY_BRANCH:-main}"
docker compose -p cqv3 up -d --build
docker image prune -f >/dev/null 2>&1 || true
echo "=== deploy done $(date -Is) ==="
