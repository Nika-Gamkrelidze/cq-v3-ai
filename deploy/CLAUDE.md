# deploy/ — Claude context

- `nginx.conf` — serves the frontend, reverse-proxies /api/ -> api:8000 (strips /api prefix),
  and proxies /gh-webhook -> host.docker.internal:9000 (the push-to-deploy receiver on the host),
  so the webhook is reachable over the existing port 80 (no extra firewall port).
  It also declares `map $http_upgrade $connection_upgrade` — **exactly once, here**. Line 4
  includes `tls-ssl.conf` into the SAME http context, so a second copy of that map is a fatal
  duplicate-variable error and nginx refuses to start (nothing in the pipeline rolls back).
- `location /api/v1/chat/` — the chat transport (SSE now, WebSocket later), present in **both**
  `nginx.conf` and `tls-ssl.conf`, placed **before** `location /api/` because longest-prefix wins.
  It restates the URI (`proxy_pass http://api:8000/v1/chat/;`) — reusing `http://api:8000/` like
  `/api/` does would strip the prefix twice and silently 404. Streaming needs
  `proxy_http_version 1.1` + Upgrade/Connection + `proxy_buffering off` /
  `proxy_request_buffering off` / `gzip off` / `proxy_read_timeout 3600s`; `access_log off`
  because the SSE URL carries a single-use stream ticket.
- `deploy.sh` — safe redeploy under `flock` (concurrent pushes no longer race): `git pull
  --ff-only origin ${DEPLOY_BRANCH:-main}`, `docker compose -p cqv3 up -d --build`. Idempotent
  migrations run on API startup (services/migrate.py); the `pgdata` and `hf_cache` volumes are
  never touched (data survives). Ends with a bounded `/api/health` smoke that exits non-zero.
  - **nginx apply-gate is state-based, not diff-based.** It compares a sha256 of
    `nginx.conf + tls-ssl.conf + enable-tls.sh` against a marker at
    `/home/cqdeploy/.cq-nginx-applied` (outside the repo, so `git pull` never touches it) and
    force-recreates `web` on mismatch — Docker keeps the old single-file bind-mount inode
    otherwise. The previous `git diff $before..$after` gate could permanently stop firing: HEAD
    advanced before the build, the recreate ran after it under `set -e`, so one failed build left
    the diff empty forever. Validation runs `nginx -t` in a **throwaway** `nginx:alpine`
    container mounting the NEW files — `docker compose exec web nginx -t` would test the stale
    inode. The marker is written only after both validation and recreate succeed.
- `measure-embed-latency.sh` — run **on the server** (`docker compose -p cqv3 exec`): times a
  ~40-token Georgian query against TEI, cold then N samples, printing p50/p95; `--under-load`
  repeats it with continuous 32-text import batches in flight (contention is the real question).
  Per ADR-001 this gates the latency promise: **p95 > 150 ms ⇒ an ONNX/int8 TEI image becomes P0
  scope**, since no application code recovers a slow forward pass.
- `webhook.py` — stdlib HTTP listener on 127.0.0.1-reachable :9000; verifies GitHub HMAC
  (X-Hub-Signature-256); on push to `main` runs deploy.sh in the background, logging to
  `deploy/webhook.log`. Needs WEBHOOK_SECRET (from /etc/cq-webhook.env).
- `cq-webhook.service` — systemd unit running webhook.py as **cqdeploy**, targeting the NEW
  stack at /home/cqdeploy/cq-v3-ai (NOT the old /root stack).

## Setup recap (current server layout)
1. `openssl rand -hex 32` -> `printf 'WEBHOOK_SECRET=%s\n' <hex> | sudo tee /etc/cq-webhook.env`,
   then `sudo chmod 600 /etc/cq-webhook.env` (must live in /etc, not /home — SELinux blocks
   systemd from reading an EnvironmentFile with a home_t context).
2. `sudo cp deploy/cq-webhook.service /etc/systemd/system/ && sudo systemctl enable --now cq-webhook`.
3. Firewall: only port 80 open externally (+ 22 via VPN). Port 9000 stays host-local; the hook is
   reached via nginx /gh-webhook. `docker-compose.yml` `web` has `extra_hosts: host-gateway`.
4. GitHub repo -> Settings -> Webhooks: payload `http://217.147.236.219/gh-webhook`,
   content-type `application/json`, the shared secret, event = push.

## Security TODO (before real customer traffic)
Put nginx + webhook behind HTTPS (Caddy auto-certs, or nginx + certbot). Currently plain HTTP;
the webhook HMAC and the API key are the only protections. Add TLS before production.
