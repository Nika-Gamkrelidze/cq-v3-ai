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
- **Operator scope (`X-Act-As-Tenant`)** — a *verified superadmin* may add this header (tenant uuid
  or slug) to get a **tenant-shaped principal** for that one workspace, so the ordinary tenant
  routes serve the operator console. This is why there is ONE page: `tenant.html` is both the
  customer portal and the superadmin console (`kb-admin.html` is now just a redirect), and the
  operator issues literally the same requests the customer does — no parallel twins to drift.
  Rules that must not regress (pinned by `backend/tests/test_act_as_tenant.py`):
  - The header is **inert for everyone else** — a tenant key asking to act as another workspace
    silently gets its *own* data back, so it cannot even probe whether that workspace exists.
  - Scoping trades the superadmin principal for a tenant one, so **`/admin/*` refuses it** — the
    console sends the header on tenant calls only (`adminOnlyH()` for the workspace list).
  - `role="superadmin"` is carried through rather than faked to `owner`, so audit actors
    (`user_id or role`) record `tenant:superadmin` and never a customer's user.
  - Authority is one predicate, `Principal.may_configure_workspace` (owner|apikey|superadmin) —
    do not re-introduce per-router `role not in (...)` tuples.
  - Anything guarded by the account holder's **own password** (`POST /scoring/reset`) stays closed
    to an operator: it requires `user_id`, which an operator does not have.

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
- **Multi-provider AI** (`services/ai_registry.py`, `ai_resolve.py`, `providers/*`). A registry of
  named **connections** — `ai_connections(name, capability llm|stt|tts, provider, model,
  base_url, sealed key, settings)`, one **default per capability** — plus per-workspace
  **assignment** (`tenant_ai_assignments`, a dropdown on `/ai-config`) and a workspace's **own
  key** (`tenant_ai_overrides`, the portal's *Your own AI subscription* tab; owners only, never
  a base URL). Text providers: Anthropic, OpenAI, Gemini; speech-to-text: ElevenLabs, OpenAI, Gemini
  (`gemini-3.5-transcribe`, the default, on Google's Interactions API — native diarization +
  word timestamps, BCP-47 language hints, key terms only when diarization is off; any other
  Gemini id is a chat model asked for a transcript through `generateContent` — segment-level
  timings, speakers by ear); text-to-speech: ElevenLabs, OpenAI — STT and TTS are resolved independently. Console: *AI providers* tab (Test connection / Make default /
  Deactivate). Provider keys are **encrypted at rest** (`services/secrets.py`, `SECRETS_KEY`).
- **Conversational AI — customer chat bot + operator copilot** (`routers/chat.py`, mounted at
  `/v1/chat/*`; design in `docs/ADR-001-conversational-ai.md`). The customer's **chat service**
  calls CQ server-to-server with a separate **integration credential** (`X-CQ-Key: cqi_…` +
  `X-CQ-Tenant`, granted per tenant — issued from the console's *Bot control → Chat connections*).
  **Autopilot** (`POST /v1/chat/answer`) answers customers from the tenant's KB documents that were
  *shared with the bot*, refuses with **zero tokens** when ungrounded, and returns a handoff for a
  human; after handoff the **copilot** (`/v1/chat/turns` → `/suggestions`) drafts replies for the
  operator. Per-tenant settings live in the portal's **BOT** tab (`/chat/config`), the inherited
  baseline in the console's **Default bot** tab (`/admin/chat/default-config`), and the operator
  brake in *Bot control* (kill switch). Token usage is metered per tenant in `llm_usage`
  (`feature = autopilot | copilot | handoff`). Contract for the chat-side team:
  `docs/CHAT_INTEGRATION.md`; paste-ready Claude Code prompt for their repo: `docs/CHAT_SIDE_PROMPT.md`.

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
- **Every AI call goes through one of two seams, or it bypasses a tenant's configuration.**
  Text: `services/llm.py::call_tool / stream_text` (signatures unchanged since the single-provider
  days) → `services/providers/llm_<provider>.py`. Voice: `services/voice.py::transcribe /
  synthesize / list_voices / list_models` → `providers/{stt,tts}_<provider>.py`. Nothing else
  may import `anthropic`, `elevenlabs` or a provider module — that is how a workspace can be
  moved to Gemini or another voice provider with a dropdown instead of a deploy. New AI code
  keeps the house pattern (forced tool-use + strict schema); `providers/llm_base.py::
  translate_schema` renders the schema in each provider's dialect (OpenAI strict wants
  `additionalProperties:false` everywhere; Gemini takes an OpenAPI subset).
- **The resolution chain** (`ai_resolve.resolve(client_id, capability)`) is legacy admin
  settings ← default connection ← assigned connection ← the tenant's own key, each layer
  overriding only what it sets. A layer that CHANGES the provider drops the inherited model,
  key and base URL (an OpenAI assignment over an Anthropic default is never handed the
  Anthropic key). `source` is honest (`byo` with `byo=False` when the tenant set only a model).
  It never raises for a lookup failure — a chain that ends keyless resolves with `api_key=''`
  and the adapter says so in words.
- **Secrets.** Stored values are `enc:v1:<fernet>`; `secrets.seal()` on every write,
  `open()` only inside the resolver; no API response ever carries a key (tests grep the bodies).
  Without `SECRETS_KEY` the API still boots in a **plaintext** mode (`/health.secrets`, console
  banner) and seals everything on the first boot after the key is set. **Losing the key makes
  every stored provider key unreadable — back it up.**
- **OpenAI and Gemini adapters are unverified against the live APIs** (this repo holds no key
  for either). Request/response shapes are pinned with `httpx.MockTransport`; the console's
  *Test connection* is the live verification. Known first-use watch-item: OpenAI reasoning
  models spend `max_completion_tokens` on hidden reasoning, so a 4096-token budget can come
  back truncated — pick a larger budget or a non-reasoning model for that connection.
- **The legacy Integrations key/model fields are gone from the console.** On the first boot
  with an empty registry, `ai_registry.seed_from_legacy()` turns the admin-panel keys into
  default connections once; a keyless deployment stays on the legacy path with an empty
  registry (that is the local dev state — the local `.env` has no provider keys at all).
- **The public bot reads only `kb_documents.visibility='public'`** (the 🤖 *share with the bot*
  toggle in the KB tab; default `internal`). A tenant with nothing shared cannot switch autopilot on
  (409) — by design, so an internal pricing floor is never quoted to a customer by accident. Two
  independent off switches: the tenant's `autopilot_enabled` and the superadmin kill switch
  (`app_settings.autopilot_kill`, 5 s cache).
- **Chat config resolution** = code defaults ← superadmin default blob
  (`app_settings.default_chat_config`) ← the tenant's active `chat_configs` row; `is_default` in
  the response tells the UI which layer it is looking at. Keep the default free of raw SQL in
  `chat_store.py` — `tests/test_chat_store_sql.py` fails any statement there without `client_id`.

---

## 5. Deployment

- **Where:** a single Linux server (Rocky 8), Docker Compose project `cqv3`, at
  `/home/cqdeploy/cq-v3-ai`, running as the `cqdeploy` user. **Live on HTTPS at
  https://ai.communiq.ge** — Let's Encrypt, terminated by the `cq-web` nginx
  (`deploy/tls-ssl.conf`, copied into place at container start only when certs exist, so a
  fresh clone still boots on HTTP). Port 80 redirects to 443 for that host and keeps
  serving the ACME challenge. **There are TWO nginx server blocks** — `deploy/nginx.conf`
  (80) and `deploy/tls-ssl.conf` (443) — and a routing or header change made in only one
  of them is not deployed.
- **The server dir is a git checkout tracking `origin/main`.** Deploys are `git pull --ff-only` +
  `docker compose -p cqv3 up -d --build`. The server `.env` is **untracked and preserved** across
  deploys; volumes are never touched. Idempotent migrations apply on api startup.
- **Push-to-deploy webhook** (`deploy/webhook.py` + `deploy/cq-webhook.service`, runs as `cqdeploy`):
  a stdlib HTTP receiver validates the GitHub **HMAC-SHA256** signature, and on push to `main` runs
  `deploy/deploy.sh` (the pull+rebuild above), logging to `deploy/webhook.log`. It's exposed via an
  nginx `location = /gh-webhook` → host `:9000` (over the existing port 80, **no extra firewall
  port**; `web` has `extra_hosts: host-gateway`). The server-side receiver is installed and tested.
  - **✅ REGISTERED in GitHub** (hook id `651713539` → `http://217.147.236.219/gh-webhook`, push
    event, json, insecure_ssl). The old dead hook (`ai.communiq.ge/deploy`) was removed. **Every push
    to `main` now auto-deploys — no VPN/SSH needed.** (The VPN is only needed for manual SSH access.)
- **Manual/non-destructive deploy flow (fallback), VPN + guard:** connect CQ VPN → confirm no
  concurrent deploy → `git pull --ff-only` → `docker compose -p cqv3 up -d --build` → verify over the
  public IP. Full runbook (server IP, user, key path, VPN name) is in **`docs/DEPLOYMENT.local.md`**.

---

## 6. Where we stopped (exact state)

- **2026-09-08 — multi-provider AI.** Connection registry + per-workspace assignment + tenant
  BYO keys, encrypted at rest; Anthropic/OpenAI/Gemini text adapters and ElevenLabs/OpenAI
  voice adapters behind two seams; all 15 voice call sites migrated. **Pending on the server:**
  set `SECRETS_KEY` in the server `.env` (see `.env.example`) so keys get sealed; add OpenAI /
  Gemini keys and press *Test connection* before assigning either to a workspace.
- **2026-09-08 — chat bot launched in the UI.** Tenant BOT tab live (was behind a "coming soon"
  flag), superadmin-editable default bot config, one chat-service credential with a grant per
  tenant + a console to manage them, integration contract + chat-side prompt written. **Pending:**
  the chat service's own integration (their repo, using `docs/CHAT_SIDE_PROMPT.md`), then issuing
  the credential, sharing KB documents with the bot, and enabling autopilot per pilot tenant.
- **Deployed to the server:** the full app — audio analysis, TTS, KB + KB-admin console, fact-check,
  rubric scoring, all three UIs — **including the QA fixes below** (pushed + deployed), plus the
  **registered auto-deploy webhook**. `origin/main` and the server are in sync.
- The **7 QA bug fixes** from the full local regression pass (now live):
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
1. ~~Register the GitHub webhook~~ ✅ done — pushes auto-deploy.
2. ~~Push + deploy the QA-fix commits~~ ✅ done.
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
- Frontend is **mid-migration to Next.js**. New work goes in `frontend/next` (App Router,
  React 19, TypeScript, **static export** — no Node process in production; nginx serves the
  exported files beside the legacy ones and `try_files $uri $uri.html` gives them clean URLs).
  The not-yet-ported pages are still vanilla JS in `frontend/public` (`brand.js` = `CQ.*`,
  `brand.css`). **Read `docs/MIGRATION.md` before touching either stack** — it is the port's
  contract, and it lists the deliberate decisions a rewrite silently turns into regressions.
  Trilingual either way: every user-facing string needs `en/ka/ru` keys and
  `python3 scripts/check_i18n.py` must pass (it also reports keys still shared by both stacks,
  a count that reaches zero when the last legacy page is deleted).
- New AI features: **forced tool-use + strict schema + array normalization**.
- New tenant-scoped queries: **always filter by `client_id`.**
- New DB columns/tables: idempotent (`ADD COLUMN IF NOT EXISTS`) in a `db/*.sql` applied by
  `migrate.py`.
- Keep secrets in `.env`; keep operational access notes in `docs/DEPLOYMENT.local.md` (git-ignored).

---
*Backend/deploy specifics also documented in `backend/CLAUDE.md` and `deploy/CLAUDE.md`.*
