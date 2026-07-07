# deploy/ — Claude context

- `nginx.conf` — serves the frontend, reverse-proxies /api/ -> api:8000 (strips /api prefix).
- `deploy.sh` — safe redeploy: `git reset --hard origin/main`, `docker compose up -d --build`,
  wait for db, apply idempotent schema. Does NOT touch the `pgdata` volume (data survives).
- `webhook.py` — stdlib HTTP listener; verifies GitHub HMAC (X-Hub-Signature-256); on push to
  main runs deploy.sh in the background. Needs WEBHOOK_SECRET (from /etc/cq-webhook.env).
- `cq-webhook.service` — systemd unit running webhook.py on port 9000 as root.

## Setup recap
1. `echo "WEBHOOK_SECRET=..." > /etc/cq-webhook.env` (chmod 600)
2. `cp deploy/cq-webhook.service /etc/systemd/system/ && systemctl enable --now cq-webhook`
3. Open firewall 80 + 9000.
4. GitHub repo -> Settings -> Webhooks: payload `http://SERVER:9000/`, json, secret, push events.

## Security TODO (before real customer traffic)
Put nginx + webhook behind HTTPS (Caddy auto-certs, or nginx + certbot). Currently plain HTTP;
the webhook HMAC and the API key are the only protections. Add TLS before production.
