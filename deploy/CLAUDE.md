# deploy/ — Claude context

- `nginx.conf` — serves the frontend, reverse-proxies /api/ -> api:8000 (strips /api prefix),
  and proxies /gh-webhook -> host.docker.internal:9000 (the push-to-deploy receiver on the host),
  so the webhook is reachable over the existing port 80 (no extra firewall port).
- `deploy.sh` — safe redeploy: `git pull --ff-only origin main`, `docker compose -p cqv3 up -d
  --build`. Idempotent migrations run on API startup (services/migrate.py); the `pgdata` and
  `hf_cache` volumes are never touched (data survives).
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
