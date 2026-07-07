# CQ v3 AI — project context for Claude

Read this first. It orients a fresh Claude / Claude Code session on what this project is,
the decisions already made, and where we are.

## What this project is
A multi-tenant AI service that scores the quality of recorded customer-support phone calls.
An existing **PHP** web app records calls + stores audio, then POSTs call metadata (+ an audio
URI) to this backend. This backend, in **batch at end of day**:
1. Transcribes the audio (ElevenLabs Scribe) with speaker separation.
2. Retrieves the relevant per-client knowledge base (RAG over pgvector).
3. Scores the call against that KB with Claude (per-dimension scores + evidence).
4. Applies the client's dimension weights **in code** and stores structured results.
5. PHP / a dashboard reads results for a statistics page.

Clients are businesses across many industries (hospitality, banking, insurance, clinics, tech,
churches, ...). Calls are mostly **Georgian**, some Russian/English.

## Architecture (one monorepo)
- `backend/` — Python **FastAPI** service. Runs as the API (endpoints PHP calls + read
  endpoints) and, later, as background workers. "AI integration" is NOT a separate app — it is
  `backend/app/services/` (ElevenLabs, Claude, retrieval) + `backend/app/workers/`.
- `frontend/` — placeholder now (nginx status page). **Next.js** portal + dashboard later.
- **Postgres 16 + pgvector** — one DB doing relational + JSONB + vector work. Dev: container.
  Prod: DigitalOcean Managed Postgres (just change DATABASE_URL).
- **nginx** (`web` service) — serves the frontend, reverse-proxies `/api/` -> api:8000.
- **Docker Compose** orchestrates db + api + web.

## Key decisions (don't relitigate without a reason)
- **No model fine-tuning.** Domain knowledge = RAG + per-client config + few-shot examples,
  not training. The LLM stays general; per-tenant data makes it specific.
- **STT = ElevenLabs Scribe**, file/async mode (~$0.22/hr). Language auto-detected (no extra
  cost; handles Georgian/Russian/English incl. mid-file switching).
- **Audio is MONO** -> use Scribe diarization (included). It gives speaker_0/1; which one is the
  operator is decided by **Claude from transcript content** in the same scoring call.
- **Scoring = Claude Sonnet** by default (Haiku to cut cost, Opus for hard cases — choose via a
  golden-set eval vs human scores). Structured output via **tool use**. LLM scores each
  dimension w/ evidence; **code applies the weights** (auditable, tunable w/o re-running).
- **Batch end-of-day**: Anthropic **Batch API** (50% off) + **prompt caching** (~90% off the
  reused static block: rubric + few-shot + KB). Never use realtime/synchronous.
- **Cost**: ~3 cents/call at 1,000 calls/day (Sonnet + batch + cache + Scribe). Regulated
  clients (banks/clinics) enable Scribe entity detection (+$0.07/hr) to redact PII/PHI before
  Claude. Clinics need a HIPAA BAA with ElevenLabs (sales-gated, slow — start early).

## Database (backend/db/schema.sql) — 8 tables
clients, operators, scoring_configs, calls, transcripts, analyses, kb_documents, kb_chunks.
- Multi-tenant: every row carries `client_id`; all queries/retrieval filter by it.
- `scoring_configs` versioned + one active per client; every `analyses` row records the
  config_id + model that produced it (auditable, reproducible).
- `analyses` stores BOTH raw per-dimension scores (jsonb) AND computed `weighted_total`, so
  re-weighting is a cheap recompute with no LLM calls.
- `kb_chunks.embedding vector(1536)` — dimension MUST match the embedding model (multilingual,
  for Georgian). Change schema vector(...) + EMBEDDING_DIM together if the model changes.
- JSONB for evolving shapes (transcript segments, per-dimension scores/flags).

## Current status
- **Phase A DONE**: ingest endpoint (`POST /api/calls`, idempotent, X-API-Key), full schema,
  Docker stack (db+api+web), GitHub-webhook auto-deploy, nginx status page.
- **NEXT: transcription worker** — picks up `pending` calls, fetches audio, Scribe transcribe +
  diarize, writes transcript, moves status pending -> transcribing -> scoring.
  **BLOCKED ON**: where call audio is stored (DigitalOcean Spaces / S3 / other) — needed to
  fetch each `audio_uri`.
- After that: scoring worker (Claude + retrieval), KB ingestion + upload portal, Next.js
  dashboard, migrations (Alembic), privacy/redaction, prod on DO Managed Postgres.

## Run / deploy
```bash
cp .env.example .env      # set SERVICE_API_KEY
docker compose up -d --build
```
- Status page: http://SERVER/  ·  API: http://SERVER/api/  ·  health: /api/health
- DB user/name/password = `cq`. Inspect: `docker compose exec db psql -U cq -d cq`
- **Deploy is automatic**: push to `main` -> GitHub webhook (deploy/webhook.py, systemd
  `cq-webhook`) -> `deploy/deploy.sh` pulls, rebuilds, applies idempotent schema.
- **Data safety**: all DB data lives in the `pgdata` Docker volume, untouched by rebuilds.
  NEVER run `docker compose down -v` (deletes the volume). New tables apply via the idempotent
  schema on deploy; column changes will need Alembic (not added yet).

## Environment gotchas (server is Rocky Linux 8)
- Rocky uses `dnf`, not `apt`. Python is `python3.11` (plain `python3` is 3.9, too old).
- SELinux enforcing: Docker bind mounts need `:z` (already set in docker-compose.yml).
- Docker CE: install from the **centos** repo (the `rocky` repo has no matching packages), with
  `--allowerasing` (runc/containerd conflict). Native Postgres is disabled in favour of the
  container.

## Conventions
- Python 3.11, FastAPI, asyncpg (raw SQL, no ORM yet), pydantic-settings for config.
- Secrets in `.env` (git-ignored) — NEVER commit. `.env.example` is the template.
- Vendor prices/models are current as of early-mid 2026 — re-verify before committing budgets.
