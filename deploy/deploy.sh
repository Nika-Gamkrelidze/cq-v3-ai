#!/usr/bin/env bash
# Push-to-deploy: fast-forward main and rebuild the cqv3 stack.
# Idempotent DB migrations run on API startup (services/migrate.py); the pgdata
# and hf_cache volumes are NEVER touched, so all data survives every deploy.
set -euo pipefail
cd /home/cqdeploy/cq-v3-ai

echo "=== deploy $(date -Is) ==="
before=$(git rev-parse HEAD)
git pull --ff-only origin "${DEPLOY_BRANCH:-main}"
after=$(git rev-parse HEAD)

docker compose -p cqv3 up -d --build

# nginx config is a single-file bind mount: Docker keeps the old file's inode across a
# `git pull` (which replaces the file), so `up -d` alone will NOT apply a changed nginx
# config. Recreate the web container only when one of those files actually changed.
if git diff --name-only "$before" "$after" \
     | grep -qE '^deploy/(nginx\.conf|tls-ssl\.conf|enable-tls\.sh)$'; then
    echo "nginx config changed -> recreating web"
    docker compose -p cqv3 up -d --force-recreate web
fi

docker image prune -f >/dev/null 2>&1 || true
echo "=== deploy done $(date -Is) ==="
