# CQ v3 AI

Multi-tenant AI call-quality analysis. PHP posts call metadata to the API; workers
(added in later phases) transcribe (ElevenLabs Scribe) and score (Claude + pgvector)
each call and write results back for the dashboard.

## Stack (docker compose)
- `db`  — PostgreSQL 16 + pgvector
- `api` — FastAPI backend (ingest + read endpoints)
- `web` — nginx: serves the frontend and reverse-proxies `/api/` to the backend

## Local / server run
```bash
cp .env.example .env          # then set SERVICE_API_KEY
docker compose up -d --build
```
Open http://SERVER/ for the status page; the API is at http://SERVER/api/ .

## Deploy (auto, via GitHub webhook)
Pushing to `main` triggers `deploy/deploy.sh` on the server, which pulls, rebuilds,
and applies the idempotent schema. The Postgres `pgdata` volume is never touched,
so database data survives every deploy. Never run `docker compose down -v` (that
deletes the volume).

## Layout
- `backend/app` — FastAPI app; `backend/app/services` + `workers` (AI, later phases)
- `backend/db`  — schema + seed
- `frontend/`   — placeholder now; Next.js portal + dashboard later
- `deploy/`     — nginx config, webhook listener, deploy script, systemd unit
