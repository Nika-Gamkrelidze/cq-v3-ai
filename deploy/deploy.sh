#!/usr/bin/env bash
# Push-to-deploy: fast-forward main and rebuild the cqv3 stack.
# Idempotent DB migrations run on API startup (services/migrate.py); the pgdata
# and hf_cache volumes are NEVER touched, so all data survives every deploy.
set -euo pipefail

# webhook.py fires deploys with subprocess.Popen and no lock, so two pushes landing
# seconds apart used to run `git pull` and `docker compose up --build` concurrently on
# the same checkout. Whoever gets the lock deploys; the loser exits 0 (its commit is
# already in the tree the winner pulled).
exec 9>/tmp/cq-deploy.lock
flock -n 9 || { echo "deploy already running -> skipping"; exit 0; }

cd /home/cqdeploy/cq-v3-ai

echo "=== deploy $(date -Is) ==="
git pull --ff-only origin "${DEPLOY_BRANCH:-main}"

docker compose -p cqv3 up -d --build

# nginx config is a single-file bind mount: Docker keeps the old file's inode across a
# `git pull` (which replaces the file), so `up -d` alone will NOT apply a changed nginx
# config — the web container must be force-recreated.
#
# The gate is STATE-based (hash of the files currently on disk vs. the hash last
# applied), never diff-based. The old `git diff $before..$after` version had a fatal
# hole: HEAD advanced before the build, and the force-recreate ran after it under
# `set -e`, so a single failed build left HEAD ahead with the recreate never executed —
# and every later deploy then computed an empty diff and skipped nginx FOREVER, serving
# the stale inode with no remaining trigger. A manual `git pull` did the same. Comparing
# state instead means a failed deploy simply leaves the marker stale and the next run
# retries; there is no way to lose the trigger.
MARKER=/home/cqdeploy/.cq-nginx-applied   # OUTSIDE the repo; git pull/checkout never touches it
HASH=$(cat deploy/nginx.conf deploy/tls-ssl.conf deploy/enable-tls.sh | sha256sum | cut -d' ' -f1)
if [[ "$(cat "$MARKER" 2>/dev/null || true)" != "$HASH" ]]; then
    echo "nginx config differs from last applied state -> validating + recreating web"
    # Validate the NEW files in a THROWAWAY container. `docker compose exec web nginx -t`
    # would test the OLD inode — the running container's bind mount stays stale until it
    # is recreated, i.e. it would happily green-light a config it has never seen.
    #
    # --add-host is NOT cosmetic: nginx resolves every `proxy_pass` upstream at CONFIG-PARSE
    # time, and a throwaway container is on the default bridge where the compose service name
    # `api` (and `host.docker.internal`) do not resolve — so without these the gate exits 1 on
    # a perfectly valid config, and since the marker is only written on success EVERY later
    # deploy re-enters this branch and fails identically. Pointing them at 127.0.0.1 is enough:
    # `nginx -t` only needs the name to resolve, it never connects.
    #
    # tls-ssl.conf is mounted straight into tls-enabled/ so the include at nginx.conf:4
    # actually picks it up and the HTTPS server block is parsed too. That mount is CONDITIONAL
    # on the certs actually existing, mirroring enable-tls.sh, which skips the HTTPS block in
    # exactly that case: validating a block the runtime would not even load would fail the
    # deploy on a fresh clone or a rebuilt server whose certbot has not run yet.
    tls_mounts=()
    if [[ -s deploy/certs/fullchain.pem && -s deploy/certs/privkey.pem ]]; then
        tls_mounts+=(-v "$PWD/deploy/tls-ssl.conf:/etc/nginx/tls-enabled/ssl.conf:ro"
                     -v "$PWD/deploy/certs:/etc/nginx/certs:ro")
    else
        echo "no certs on disk -> validating the HTTP-only config (same as enable-tls.sh would serve)"
    fi
    docker run --rm --add-host api:127.0.0.1 --add-host host.docker.internal:127.0.0.1 \
                    -v "$PWD/deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
                    ${tls_mounts[@]+"${tls_mounts[@]}"} nginx:alpine nginx -t
    docker compose -p cqv3 up -d --force-recreate web
    # Written only after both the validation and the recreate succeeded, so a partial
    # deploy is retried rather than recorded as applied.
    printf '%s' "$HASH" > "$MARKER"
fi

docker image prune -f >/dev/null 2>&1 || true

# Post-deploy smoke: proves nginx is up, its proxy still reaches the api, and the api
# survived migrations. Fails loudly (non-zero exit -> visible in deploy/webhook.log)
# instead of the old behaviour of exiting 0 on a stack that never came back.
# Bounded retry because the api container needs a few seconds to boot and apply
# migrations; a single immediate curl would fail on almost every deploy.
#
# The budget is minutes, not seconds, on purpose: a migration may build an index the database
# does not have yet (chat.sql's HNSW index on kb_chunks is exactly that on any DB that already
# had chunks), and that happens inside the startup lifespan, BEFORE uvicorn answers /health.
# A 60 s budget would report DEPLOY FAILED on a deploy that is merely still working.
HEALTH_ATTEMPTS=150   # x 2 s = 5 minutes
for attempt in $(seq 1 "$HEALTH_ATTEMPTS"); do
    if curl -fsS --max-time 10 http://localhost/api/health >/dev/null; then
        echo "health check ok (attempt $attempt)"
        break
    fi
    if [[ "$attempt" -eq "$HEALTH_ATTEMPTS" ]]; then
        echo "DEPLOY FAILED: /api/health did not come up after $HEALTH_ATTEMPTS attempts" >&2
        docker compose -p cqv3 ps >&2 || true
        exit 1
    fi
    sleep 2
done

echo "=== deploy done $(date -Is) ==="
