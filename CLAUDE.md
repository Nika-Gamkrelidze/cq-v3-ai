# CQ v3 AI — project context (read this first)

Single entry point for a fresh Claude / Claude Code session or a new engineer. It reflects the
**actual current state**, which has evolved well past the original batch-scoring spec.

> **This repo is PUBLIC on GitHub. Never commit secrets** (API keys, passwords, the superadmin
> password, the webhook secret, SSH keys). Secrets live in `.env` (git-ignored). Operational
> access details live in `docs/DEPLOYMENT.local.md` (git-ignored — see §Deployment). When you
> need a credential, read `.env` or ask the owner; do not paste it into a tracked file.

---

## 1. What this is + the goal

**CommuniQ CQ v3 AI** is a **multi-tenant AI call/audio-analysis SaaS**. Customer organizations
("tenants" — banks, insurers, clinics, hospitality, etc.) get, per tenant:

1. **Audio analysis** — upload a call recording → **ElevenLabs Scribe** transcribes it → **Claude**
   produces a structured analysis (summary, sentiment, topics, key points, action items, quality).
2. **KB fact-check** — the call's factual claims are checked (RAG) against **that tenant's own
   knowledge base**: each claim is `SUPPORTED` / `CONTRADICTED` / `NOT_IN_KB` with evidence, plus an
   overall accuracy score. Catches agents giving wrong information.
3. **Rubric scoring** — the call is scored against **that tenant's custom weighted rubric**
   (tenant-defined dimensions + weights + guidance). Claude scores each dimension 0–100 with
   evidence; **code applies the weights** to get an auditable weighted total.

Plus a **public Text-to-Speech** feature (ElevenLabs, EN/RU/**Georgian**) that doubles as the
public entry point, linked from the CommuniQ brand site.

**Users / roles:**
- **Superadmin (operator)** — configures integrations, tenants, anonymous limits; runs the
  KB-management console across all tenants. One superadmin, credentials in server `.env`.
- **Tenant users** — log in to their tenant portal (upload audio, manage their KB, see scorecards).
  A tenant can also integrate server-to-server with a **per-tenant API key**.
- **Anonymous users** — the public app, allowed limited TTS + analysis within superadmin-set daily
  quotas (no KB, no login).

**Why it matters:** call-QA at scale for regulated, multilingual (mostly Georgian) support teams —
automating what human QA reviewers do, grounded in each customer's own policies.

> **Divergence from the original spec:** the first CLAUDE.md described a batch, PHP-driven
> `POST /calls` pipeline scored end-of-day via the Anthropic Batch API. What actually got built is
> an **interactive, self-serve web app** (upload → synchronous analyze → results in the browser)
> with per-tenant KBs, fact-check, and rubric scoring. The PHP batch-ingestion path and background
> workers are **not built** (see `docs/ROADMAP.md`, optional item). Don't assume the batch design.

---

## 2. Architecture

Monorepo, orchestrated by **Docker Compose** (project name **`cqv3`** — always use `-p cqv3`).

| Service (container) | Image / build | Role |
|---|---|---|
| `cq-api`   | `./backend` (FastAPI, py3.11) | The app: all API endpoints + serves nothing itself in prod |
| `cq-db`    | `pgvector/pgvector:pg16` | Postgres 16 + **pgvector** (relational + JSONB + vectors) |
| `cq-web`   | `nginx:alpine` | Serves `frontend/public` static files; reverse-proxies `/api/` → `api:8000`; `/gh-webhook` → host |
| `cq-embeddings` | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.6` | Self-hosted **BGE-M3** embeddings (TEI), multilingual, no external key |

**Request flow (prod):** browser → `cq-web` (nginx :80) → static UI, and `/api/*` proxied to
`cq-api:8000`. The api calls `cq-db`, `cq-embeddings`, and the ElevenLabs/Anthropic APIs.
Locally, `docker-compose.override.yml` also publishes the api directly on `:8000` and can serve the
frontend from the api container — that override is **git-ignored / dev-only**.

### Data model (Postgres; migrations are idempotent `db/*.sql`, applied on api startup)
Base tables from `schema.sql`; the app added the rest via `analyzer.sql`, `kb.sql`, `scoring.sql`.

- **`clients`** — tenants. `id`, `slug`, `name`, `industry`, `region`, `api_key`, `is_active`, `settings`.
- **`tenant_users`** — per-tenant login accounts (`username`, `password_hash`, `role` member|owner).
- **`kb_documents`** — a KB source doc: `doc_type`, `title`, `tags[]`, `content_text`, `status`
  (pending|processing|ready|error), `source_type` (file|paste|csv|api), `chunk_count`, `checksum`
  (md5 for dedupe), `actor`, `ingest_ms`, `metadata` jsonb.
- **`kb_chunks`** — chunked text + `embedding vector(1024)` (HNSW cosine index) + `chunk_index`,
  `token_count`. **`client_id` on every row.**
- **`kb_events`** — KB activity/audit log (import|edit|delete|reembed|chunk_edit|bulk|export…).
- **`audio_jobs`** — one row per analyzed upload: `status`, `transcript`, `language`, `analysis`
  jsonb, **`kb_check`** jsonb (fact-check), **`scoring`** jsonb (rubric result), `kb_used`,
  `client_id` + `principal_type` + `anon_key` (who ran it), `processing_ms`.
- **`scoring_configs`** — per-tenant rubric, versioned, **one active per client** (partial unique
  index). `dimensions` jsonb = `[{key,name,description,weight,guidance}]`, `weights`, `rubric`.
- **`app_settings`** — runtime config edited from the admin panel (integration keys, models, anon
  limits) as JSONB blobs. Read via `services/settings_store.get_effective()` which layers
  `app_settings` over `.env` fallbacks.
- **`anon_usage`** — per-anon-key (IP) per-day counters for quota enforcement.
- Legacy/unused-so-far from the original spec: `operators`, `calls`, `transcripts`, `analyses`.

> **Multi-tenancy is enforced by `client_id` filtering in every query** (retrieval, KB, fact-check,
> scoring, jobs). This is the #1 invariant — never write a tenant-scoped query without it.

### Auth model (`services/auth.py::resolve_principal`)
One principal resolver produces `superadmin | tenant | anonymous`:
- **Superadmin** — `X-Admin-Token` header, or a login token with admin scope.
- **Tenant** — `Authorization: Bearer <token>` (from tenant login) **or** `X-API-Key` (tenant api_key).
- **Anonymous** — no creds → identified by IP, allowed within admin-set limits.
- Unified login: `POST /auth/login` returns `scope: admin|tenant` and routes the UI accordingly.

---

## 3. Features built (all working, QA-green locally)

- **Audio analysis pipeline** (`routers/analyze.py`): upload → Scribe STT → (tenant) RAG context →
  Claude structured analysis → (tenant) KB fact-check → (tenant) rubric scoring → stored + returned.
  All three AI layers **coexist** on one job.
- **ElevenLabs STT + TTS.** TTS supports EN / RU / **Georgian**. **Georgian fix (critical):** see §4.
- **Voice preview** — plays a voice's free `preview_url` inline (no token cost), reusing a single
  audio player (no second play bar).
- **Knowledge base + imports** (`routers/kb.py` tenant-facing; `services/kb_ingest.py`): file
  (**PDF/DOCX/TXT/MD**), paste text, CSV (Q&A / key-value), plus API-key ingestion. Chunk → embed →
  `ready`. Semantic (pgvector cosine) retrieval with a **keyword (pg_trgm) fallback** for
  low-resource languages.
- **KB admin console** (`frontend/public/kb-admin.html`, `routers/kb_admin.py`): superadmin operator
  command center across tenants — tenant selector, stats + params (embedding dim match), documents
  list/filter/search, edit doc (re-chunk/re-embed), chunk-level edit/delete, retrieval **playground**,
  duplicate detection (exact + near), activity/import logs, export (JSON/CSV), bulk actions.
- **Multi-tenancy + isolation** — strict `client_id` scoping; verified no cross-tenant leakage.
- **RAG fact-check** (`services/factcheck.py`): claim extraction → per-claim tenant-scoped retrieval →
  `SUPPORTED|CONTRADICTED|NOT_IN_KB` + rationale + evidence + overall accuracy. Cross-lingual.
- **Per-tenant weighted scoring rubric** (`services/scoring.py`, `scoring_store.py`, `routers/scoring.py`):
  superadmin or tenant defines dimensions+weights+guidance; Claude scores each with evidence; code
  computes weighted total + per-dimension contribution. Renders as a scorecard in the tenant portal.
- **Three brand-styled trilingual UIs** (EN/KA/RU, light/dark, custom dropdowns, toasts, confirm
  modals — no native browser dialogs). Shared `brand.css` + `brand.js` (`CQ.*` helpers). Pages:
  `index.html` (public TTS+analyze), `tenant.html` (portal), `admin.html` (console), `kb-admin.html`.
- **Single sign-in** with admin routing; superadmin creds validated server-side.
- **Auto-deploy webhook** (push to `main` → server pulls + rebuilds). See §5.

**All AI structured outputs use forced tool-use with `strict: true` schemas + array-normalization**
(`_as_str_list`) so the model can't return a shape that crashes the UI.

---

## 4. Key decisions + gotchas (a new session MUST know these)

- **🇬🇪 Georgian TTS.** `eleven_multilingual_v2` produces **English-accented fake Georgian**. The fix
  (in `routers/tts.py`): Georgian uses model **`eleven_v3`** + a **Georgian-capable voice**
  (id `3b8fXc91YHS1i2DYAlBQ`, "Laura"), and does **not** send a `language_code`. EN/RU use
  `eleven_multilingual_v2` with `language_code`. Don't "simplify" this back to one model.
- **🔌 SSH to the server requires the CQ VPN.** The server firewalls SSH (port 22) to the CQ VPN's
  egress IP. If the VPN is **off**, `ssh` to the server **times out** (port 80 still works, which is
  confusing). This cost hours of misdiagnosis. **Before any deploy/SSH: connect the CQ VPN and
  confirm your egress IP is the VPN IP.** Details in `docs/DEPLOYMENT.local.md`.
- **Embeddings are self-hosted BGE-M3, 1024-dim.** First container boot downloads the model
  (~2+ GB) — the `cq-embeddings` healthcheck has a long `start_period`; be patient on a cold start.
  The **pgvector column dim MUST equal `EMBEDDING_DIM`** — `services/migrate.py` reconciles this on
  startup and only auto-migrates the column if `kb_chunks` is empty; otherwise it warns and you must
  re-embed. Changing embedding model/dim ⇒ re-embed every KB.
- **Strict tool-use + array normalization everywhere** the model returns structured data. Preserve
  this pattern in any new AI feature.
- **Tenant isolation via `client_id`.** Every tenant-scoped query filters by it. Malformed ids now
  return 400 (global `asyncpg.DataError` handler in `main.py`), not 500.
- **Idempotent SQL migrations run on api startup** (`migrate.py` applies `analyzer.sql`, `kb.sql`,
  `scoring.sql` in order — `analyzer.sql` first because it creates `app_settings`, read during
  startup). No Alembic yet; column changes must stay `ADD COLUMN IF NOT EXISTS`.
- **Data safety:** all data lives in the `pgdata` (and `hf_cache`) Docker volumes. **Never
  `docker compose down -v`.** Rebuilds/redeploys don't touch volumes.
- **Server is Rocky Linux 8, SELinux enforcing.** Bind mounts need `:z` (set). systemd services
  can't read an `EnvironmentFile` under `/home` (home_t) — the webhook secret lives in `/etc`.
  `python3` is 3.9; use `python3.11`.
- **Models are configurable** via the admin panel / `.env` (Claude model, STT model, TTS voice).
  Don't hardcode a model id in new code — read from `settings_store`.

---

## 5. Deployment

- **Where:** a single Linux server (Rocky 8), Docker Compose project `cqv3`, at
  `/home/cqdeploy/cq-v3-ai`, running as the `cqdeploy` user. **Plain HTTP, no domain/TLS yet.**
- **The server dir is a git checkout tracking `origin/main`.** Deploys are `git pull --ff-only` +
  `docker compose -p cqv3 up -d --build`. The server `.env` is **untracked and preserved** across
  deploys; volumes are never touched. Idempotent migrations apply on api startup.
- **Push-to-deploy webhook** (`deploy/webhook.py` + `deploy/cq-webhook.service`, runs as `cqdeploy`):
  a stdlib HTTP receiver validates the GitHub **HMAC-SHA256** signature, and on push to `main` runs
  `deploy/deploy.sh` (the pull+rebuild above), logging to `deploy/webhook.log`. It's exposed via an
  nginx `location = /gh-webhook` → host `:9000` (over the existing port 80, **no extra firewall
  port**; `web` has `extra_hosts: host-gateway`). The server-side receiver is installed and tested.
  - **⚠️ OPEN ITEM: the webhook is NOT yet registered in GitHub.** Add it in repo
    **Settings → Webhooks** (URL `http://<server>/gh-webhook`, content-type `application/json`, the
    shared secret, event = push, SSL verify off since HTTP). Exact URL + secret location are in
    `docs/DEPLOYMENT.local.md`. Until registered, deploy manually (pull + compose up on the server).
- **Manual/non-destructive deploy flow, VPN + guard:** connect CQ VPN → confirm no concurrent
  deploy → sync/pull → `docker compose -p cqv3 up -d --build api` → verify over the public IP. Full
  runbook (server IP, user, key path, VPN name) is in **`docs/DEPLOYMENT.local.md`** (git-ignored).

---

## 6. Where we stopped (exact state)

- **Deployed to the server:** everything through the **scoring rubric** (audio analysis, TTS, KB +
  KB-admin console, fact-check, rubric scoring, all three UIs, the webhook receiver).
- **Local repo is 2 commits ahead of `origin/main` — NOT pushed** (owner will trigger). These are
  **7 QA bug fixes** from a full local regression pass:
  1. Malformed UUID in path/body → was **500**, now **400** (global `asyncpg.DataError` handler).
  2. `analyze.py` guarded `stt["text"]` (missing/None transcript no longer 500s + strands the job).
  3. `reembed_document` raises on embedding-count mismatch (was a silent partial re-embed).
  4. `save_config` retries on concurrent version `UniqueViolation` (was 500).
  5. `tenant.html` / `kb-admin.html` check `r.ok` + `Array.isArray` before `.map`/`.length`
     (expired-token/404 error body could crash the chunk/document views).
  6. kb-admin bulk **retag** uses a brand modal instead of native `prompt()`.
  7. Added `toast.error` i18n key (EN/KA/RU).
- **Local QA is fully green** (see the pass/fail matrix in the session that produced these fixes):
  auth, isolation, KB import (all methods + PDF/DOCX), KB-admin console, fact-check, scoring,
  coexistence, TTS EN/RU/KA, anonymous quota, trilingual + theme + contrast + responsive.

---

## 7. Roadmap / remaining steps

See **`docs/ROADMAP.md`** for the prioritized list. Top items:
1. **Register the GitHub webhook** (open item above).
2. **Push + deploy the 2 QA-fix commits.**
3. **HTTPS/TLS** once a domain exists (Caddy or nginx+certbot; open 443).
4. **PII/PHI redaction** before transcripts go to Claude (compliance for banks/clinics).
5. **Production hardening** — lock CORS to real domains, rotate keys, add Alembic, add tests/CI.
6. *(optional)* the original spec's PHP `POST /calls` batch ingestion + audio storage (S3/Spaces) +
   background/batch workers.
7. *(optional polish)* bulk upload, richer exports, per-tenant stats dashboards, flagged-call alerts.

---

## 8. Run locally + test

**Prereqs:** Docker Desktop. A `.env` with real keys (copy `.env.example` → `.env`, fill
`ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `SUPERADMIN_PASSWORD`, `ADMIN_TOKEN`, `JWT_SECRET`,
`SERVICE_API_KEY`). Ask the owner for working keys — **never commit `.env`.**

```bash
cp .env.example .env            # then fill in secrets
docker compose up -d --build    # brings up db + api + web + embeddings (cold start pulls BGE-M3 ~2GB)
docker compose ps               # all healthy?
curl localhost:8000/health      # {"status":"ok",...}
```

**URLs (local):**
- Public app (nginx): `http://localhost/`  ·  API direct: `http://localhost:8000/`
- Pages: `/index.html` (public TTS + analyze), `/tenant.html` (portal), `/admin.html` (console),
  `/kb-admin.html` (KB operator console).
- API health `/health`; unified login `POST /auth/login`; superadmin uses `X-Admin-Token`.

**Inspect the DB:** `docker compose exec db psql -U cq -d cq` (user/db/pass all `cq` locally).

**Quick functional checks (patterns used in QA):**
- Login: `POST /auth/login {username,password}` → `scope`.
- Tenant API-key: send `X-API-Key: <client.api_key>` to `/kb/*`, `/analyze`.
- KB admin: superadmin `X-Admin-Token` to `/admin/kb/{tenant_id}/...`.
- Scoring config: `PUT /admin/scoring/{tenant_id}/config` or tenant `PUT /scoring/config`.
- Full pipeline: `POST /analyze` (multipart audio) as a tenant → response has `analysis` +
  `kb_check` + `scoring`.

**Dev conventions:**
- Python 3.11, FastAPI, **asyncpg raw SQL** (`$1` params, uuid PKs, timestamptz), pydantic-settings.
- Frontend is **vanilla JS** (no build step); shared helpers in `brand.js` (`CQ.*`), styles in
  `brand.css`. Trilingual via a `DICT` in `brand.js` — every user-facing string needs `en/ka/ru`
  keys (all three must stay in sync; the QA pass verified 222 keys × 3).
- New AI features: **forced tool-use + strict schema + array normalization**.
- New tenant-scoped queries: **always filter by `client_id`.**
- New DB columns/tables: idempotent (`ADD COLUMN IF NOT EXISTS`) in a `db/*.sql` applied by
  `migrate.py`.
- Keep secrets in `.env`; keep operational access notes in `docs/DEPLOYMENT.local.md` (git-ignored).

---
*Backend/deploy specifics also documented in `backend/CLAUDE.md` and `deploy/CLAUDE.md`.*
