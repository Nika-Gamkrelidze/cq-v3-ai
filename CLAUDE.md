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

1. **Audio analysis** — upload a call recording → a **speech-to-text** provider transcribes it →
   a **text model** produces a structured analysis (summary, sentiment, topics, key points,
   action items, quality). ElevenLabs Scribe and Claude are the defaults, but both are a
   per-workspace dropdown now (§3, multi-provider AI) — do not hardwire either.
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
> with per-tenant KBs, fact-check, and rubric scoring. The PHP batch-ingestion path and batch
> scoring workers are **not built** (see `docs/ROADMAP.md`, optional item) — the one background
> process that exists, `cq-worker`, serves the chat feature (§2), not batch scoring. Don't assume
> the batch design.

---

## 2. Architecture

Monorepo, orchestrated by **Docker Compose** (project name **`cqv3`** — always use `-p cqv3`).

| Service (container) | Image / build | Role |
|---|---|---|
| `cq-api`   | `./backend` (FastAPI, py3.11) | The app: all API endpoints + serves nothing itself in prod. Also runs the **server-health sampler** (psutil: host-level CPU/load/memory from `/proc`, the container's own network counters, DB size → `system_metrics`) and the **load flusher** (per-tenant request counters → `tenant_load`) as lifespan tasks |
| `cq-db`    | `pgvector/pgvector:pg16` | Postgres 16 + **pgvector** (relational + JSONB + vectors) |
| `cq-web`   | `./frontend` (build stage: `node:20-alpine` → `nginx:alpine`) | Compiles the Next app to static files (`output:'export'`) and serves them beside `frontend/public` (assets only) from one docroot; reverse-proxies `/api/` → `api:8000`; `/gh-webhook` → host |
| `cq-embeddings` | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.6` | Self-hosted **BGE-M3** embeddings (TEI), multilingual, no external key |
| `cq-worker` | `./backend` (same image as api), `python -m app.worker` | Periodic out-of-band duties: stale copilot-suggestion reaper, nightly KB **curation** mining + applying human-accepted proposals, queued full-KB re-embeds, media/anon **retention** purge, hourly **`health_purge`** of old health rows. Runs **no migrations** (api-only) and has its own small pool (`DB_POOL_MAX=5`) |

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
  routes serve the operator console. This is why there is ONE page: **`/workspace`** is both the
  customer portal and the superadmin console (the old `kb-admin.html` is a 301 into it), and the
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
- **KB admin console** (the KB tab of `/workspace` under operator scope, `routers/kb_admin.py`): superadmin operator
  command center across tenants — tenant selector, stats + params (embedding dim match), documents
  list/filter/search, edit doc (re-chunk/re-embed), chunk-level edit/delete, retrieval **playground**,
  duplicate detection (exact + near), activity/import logs, export (JSON/CSV), bulk actions.
- **Multi-tenancy + isolation** — strict `client_id` scoping; verified no cross-tenant leakage.
- **RAG fact-check** (`services/factcheck.py`): claim extraction → per-claim tenant-scoped retrieval →
  `SUPPORTED|CONTRADICTED|NOT_IN_KB` + rationale + evidence + overall accuracy. Cross-lingual.
- **Per-tenant weighted scoring rubric** (`services/scoring.py`, `scoring_store.py`, `routers/scoring.py`):
  superadmin or tenant defines dimensions+weights+guidance; Claude scores each with evidence; code
  computes weighted total + per-dimension contribution. Renders as a scorecard in the tenant portal.
- **One brand-styled trilingual Next.js frontend** (EN/KA/RU, light/dark, custom dropdowns,
  toasts, confirm modals — no native browser dialogs; shared React components in
  `frontend/next/components/ui/*`, dictionaries in `lib/i18n/`). Pages: `/` (public
  TTS + analyze), `/workspace` (portal **and** operator console), `/console` (superadmin),
  `/ai-config` (AI setup), `/usage`, `/editor` (audio editor), `/account`, `/copilot`.
- **Single sign-in** with admin routing; superadmin creds validated server-side.
- **Auto-deploy webhook** (push to `main` → server pulls + rebuilds). See §5.
- **Multi-provider AI** (`services/ai_registry.py`, `ai_resolve.py`, `providers/*`). A registry of
  named **connections** — `ai_connections(name, capability llm|stt|tts, provider, model,
  base_url, sealed key, settings)`, one **default per capability** — plus per-workspace
  **assignment** (`tenant_ai_assignments`, a dropdown on `/ai-config`) and a workspace's **own
  key** (`tenant_ai_overrides`, the portal's *Your own AI subscription* tab; owners only, never
  a base URL). Text providers: Anthropic, OpenAI, Gemini; speech-to-text: ElevenLabs, OpenAI, Gemini
  (`gemini-3.5-transcribe`, the default, on Google's Interactions API — native diarization +
  word timestamps, BCP-47 language hints, key terms never sent (timings win — §4); any other
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
  **The consumer is Swift Chat ("CQ Chat")** — `swiftchat-server` (Express + Prisma + Socket.IO,
  `src/services/cq/*`, `src/routes/cqRouter.ts`) and `swiftchat-suite` (React dashboards + the
  customer widget). Its conversation `aiState` is `bot` (CQ answers; the session stays `waiting`
  with no operator and is **excluded from auto-assignment** until handoff, though operators see
  it with a "Bot answering" badge and may claim it) → `handed_off` (a human owns it; every message
  is mirrored to CQ for the copilot; one-way) | `copilot` (tenant mapped to CQ but the bot was not
  offered — autopilot off, circuit open or bot paused: plain human flow + copilot mirrors) |
  `closed`. Customer socket events: `session:ai-state`, `session:request-human`. Inline `[n]`
  citation markers are **stripped** from bot replies on every channel; operators get citations in
  the copilot panel.
- **KB curation loop** (`routers/curation.py`, `services/curation/{miner,cluster,propose,apply,
  runner}.py`, `db/curation.sql`, run by `cq-worker`): nightly, per tenant (staggered 02:00–05:00
  UTC), chat turns + call transcripts are mined → clustered → turned into KB **add / update /
  remove proposals** that a human reviews (`/v1/curation/proposals` for tenants,
  `/admin/curation/{tenant_id}/...` for the operator; accept / decline / bulk). **Nothing
  auto-applies at any confidence**, and curation is **never reachable with an integration
  credential** (`_deny_integration`) — the chat service can feed it, not review it.
- **Server health (superadmin)** (`services/health_metrics.py`, `routers/admin.py` under
  `/admin/health/*`, console tab `health` → `app/console/HealthTab.tsx`, tables
  `system_metrics` / `tenant_load` in `db/health.sql`): KPIs (CPU, load, memory, swap, disk,
  network and disk throughput, api RSS/CPU/fds, DB size + pool, active jobs, uptime), charts over
  **1h / 6h / 24h / 7d / 30d** downsampled **server-side** with `date_bin` (≤ ~400 points), and a
  **per-tenant load table** (requests, errors, latency, bytes, AI calls/tokens from `llm_usage`,
  audio jobs/ms) where **share = request wall time** — the honest attribution, because analyze and
  AI work run inside the request. **Retention days** (1–365, default 7) and **sample interval**
  (5–300 s, default 10) are settable in the tab (`app_settings` key `health`).

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
  `scoring.sql`, `partner.sql`, `chat.sql`, **`curation.sql`** (after `chat.sql` — same feature's
  second half), `kb_ops.sql`, `media.sql`, … in a **hardcoded** order — `analyzer.sql` first because
  it creates `app_settings`, read during startup; a new `db/*.sql` that is not added to that list
  never runs; **`health.sql` is the last entry**). Only the api runs migrations — `cq-worker`
  waits for its tables instead. No Alembic yet; column changes must stay `ADD COLUMN IF NOT EXISTS`.
- **Server health sampler lives inside Docker.** psutil's CPU / load / memory numbers come from
  `/proc`, i.e. the **host** (all containers share the kernel), while the network counters are the
  **api container's own** interface and `api_*` is the api process alone — read the page with that
  in mind. `HEALTH_SAMPLER_ENABLED` (env, default true) starts the sampler + load flusher in the
  api lifespan; **tests set it false** (conftest) so nothing samples in the background. The
  **retention purge runs only in `cq-worker`** (`health_purge`, hourly) — an api-only deployment
  grows `system_metrics` / `tenant_load` forever. `requirements.txt` gained **`psutil`**, so the
  deploy that ships this must **rebuild the image** (`up -d --build`, which the webhook does).
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
- **Gemini speech-to-text speaks TWO Google APIs, chosen by the model id** — do not collapse
  them (`providers/stt_gemini.py`). An id containing *transcribe* (`gemini-3.5-transcribe`,
  the adapter's default) is a dedicated ASR model on the **Interactions API**
  (`POST /v1beta/interactions`, `generation_config.transcription_config`): native speaker
  diarization, word timestamps, BCP-47 hints (our `ka` → `ka-GE`), `store:false`. Any other id
  is a chat model on `generateContent` with a transcript schema — segment-level timings,
  speakers by ear. `-live` is WebSocket-only and is refused before any request.
  **Google rejects `custom_vocabulary` combined with diarization or word timestamps**, so on
  this model key terms and timings are mutually exclusive and **timings always win**: key terms
  are never sent, and the result `detail` (plus the workspace's key-terms hint) says why. Do not
  "restore" them — three features are built on word timings and none of them fails loudly:
  the player timeline, per-speaker scoring/attribution, and **Voice tone**, whose per-segment
  prosody needs a start and an end per turn and otherwise reports `no_timestamps`, i.e. a
  recording that silently goes quiet. A recording that genuinely needs key terms belongs on
  ElevenLabs Scribe.
- **Adapters verified against the live APIs, and adapters not.** Verified: Anthropic and
  ElevenLabs (the deployment has always run on them) and, since 2026-09-10, **Gemini
  speech-to-text** — a real `gemini-3.5-transcribe` connection passes *Test connection* on the
  server. Still unverified because this repo holds no key: **Gemini text**, **OpenAI text and
  voice**. Their request/response shapes are pinned with `httpx.MockTransport` only; the
  console's *Test connection* is the live verification, and a failure now shows the provider's
  own sentence in the row and the toast rather than a bare "Failed". Known first-use
  watch-item: OpenAI reasoning models spend `max_completion_tokens` on hidden reasoning, so a
  4096-token budget can come back truncated — pick a larger budget or a non-reasoning model.
- **The legacy Integrations key/model fields are gone from the console.** On the first boot
  with an empty registry, `ai_registry.seed_from_legacy()` turns the admin-panel keys into
  default connections once; a keyless deployment stays on the legacy path with an empty
  registry (that is the local dev state — the local `.env` has no provider keys at all).
- **The public bot reads only `kb_documents.visibility='public'`** (the 🤖 *share with the bot*
  toggle in the KB tab; default `internal`). A tenant with nothing shared cannot switch autopilot on
  (409) — by design, so an internal pricing floor is never quoted to a customer by accident. Two
  independent off switches: the tenant's `autopilot_enabled` and the superadmin kill switch
  (`app_settings.autopilot_kill`, 5 s cache).
- **Bot 429s carry a machine-readable `code`** the chat side branches on: `rate_limited_tenant`
  (the tenant's `answer_tenant_per_minute` cap) and `rate_limited_enduser` (the end user's
  `answer_enduser_per_hour` cap), beside the existing `llm_busy`. Swift Chat pauses the *bot* for
  the tenant on `rate_limited_tenant` and on `409 autopilot_not_enabled` (copilot mirrors keep
  flowing), hands off **only that conversation** on `rate_limited_enduser`, and opens its circuit
  only on 401/403/503/5xx/timeouts — so never collapse these into one generic 429, and keep the
  kill switch answering `503 autopilot_disabled`. Until the codes ship it string-matches
  "per minute" in the 429 message, so do not reword that message either.
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
  set `SECRETS_KEY` in the server `.env` (see `.env.example`) so keys get sealed (`/api/health`
  still reports `secrets: plaintext`); press *Test connection* on any OpenAI connection before
  assigning it to a workspace — Gemini speech-to-text has since been verified (below).
- **2026-09-08 — chat bot launched in the UI.** Tenant BOT tab live (was behind a "coming soon"
  flag), superadmin-editable default bot config, one chat-service credential with a grant per
  tenant + a console to manage them, integration contract + chat-side prompt written.
  **Done 2026-09-08 (chat side):** Swift Chat integrated — `swiftchat-server` `ca1a3aa`,
  `swiftchat-suite` `47f2539`.
- **2026-09-09 — server health page.** Console tab *Server health*: host + api + DB samples every
  `sample_interval_s` into `system_metrics`, every request metered per principal/tenant into
  `tenant_load` (minute buckets), ranged charts + per-tenant load table + settings, retention
  purge in the worker. Lands with a `psutil` image rebuild; nothing to configure on the server.
- **2026-09-09 — the CQ key moved into Swift Chat's database.** `PlatformSettings` holds the
  `cqi_` key encrypted (AES-256-GCM) with a public key-id for display; the Swift Chat superadmin
  dashboard's *CQ AI connection* card edits and tests it; the `CQ_API_KEY` env var is only a
  one-time bootstrap import on server start (see checklist step 10).
- **2026-09-09 — integration audit + fix pass (both products, in flight).** CQ: the 429 `code`s
  above, the contract docs (`docs/CHAT_INTEGRATION.md`, `docs/CHAT_SIDE_PROMPT.md`) re-aligned
  with the code. Swift Chat: `aiState` gains `copilot`, bot sessions leave the human queue until
  handoff, `handOff` is the single re-entry into the queue, customer events `session:ai-state` /
  `session:request-human`, `[n]` markers stripped, per-contact ingest queue, `facebook` channel
  renamed `messenger`. **Pending (operational) — the pilot go-live checklist, in order:**
  1. Deploy the Swift Chat fixes together (push = auto-deploy; confirm `git log origin/main` on
     both repos) — until then a widget bot session shows a spinner with a disabled input.
  2. CQ server `.env` (VPN for SSH): set `SECRETS_KEY`, `docker compose -p cqv3 up -d`, then
     `GET /api/health` must report secrets sealed.
  3. Console → *AI providers*: a default `llm` connection exists and *Test connection* passes
     (otherwise every `/answer` degrades to refusal + handoff with an `llm_error` frame).
  4. Console → *Default bot*: greeting **and** refusal copy in en/ka/ru (code defaults are
     empty), disclosure mode, `answer_tenant_per_minute` / `answer_enduser_per_hour` (60/60).
  5. Console → *Tenants*: copy the pilot tenant's `client_id` uuid in **lower case** (Swift Chat
     rejects slugs).
  6. Pilot workspace → KB tab: share at least one document with the bot (`visibility=public`).
  7. Pilot workspace → BOT tab: languages, refusal copy per language, escalation keywords, tick
     Autopilot, Save (a 409 means no public document yet).
  8. Console → *Bot control* → *Chat connections* → New: **all four scopes** (`chat:turn`,
     `chat:suggest`, `chat:answer`, `chat:sync` — the GDPR purge needs `chat:sync`), tick the
     pilot workspace, copy the one-time `cqi_<key_id>.<secret>`. Later tenants get a **grant**
     on the same row (`POST /admin/integrations/{id}/grants`), never a new key.
  9. Console → *Bot control*: global pill *running*, pilot row autopilot ● and not Stopped.
  10. Swift Chat SuperAdmin dashboard → *CQ AI connection*: paste the `cqi_` key (stored
      encrypted in `PlatformSettings`; the `CQ_API_KEY` env var is only a one-time bootstrap
      import), base URL `https://ai.communiq.ge/api` → Test connection. Admin dashboard → *AI
      Assistant (CQ)*: paste the `client_id` → Save → Test connection (health ok +
      `autopilot_enabled: true`) → switch on *Let the bot answer first*.
  11. Probe from the widget: grounded question → bot answer with the disclosure line; off-KB
      question → refusal + handoff, session in the operator queue with the summary in the copilot
      panel; "I want a human" → handoff; an operator reply after handoff → a copilot draft in ~1 s.
  12. Kill-switch drill: *Stop* on the pilot row → next customer message gets `503
      autopilot_disabled`, `[cq] circuit opened` in the Swift Chat log, refusal copy sent, new
      conversations go straight to humans; *Resume* → the half-open `GET /config` probe closes
      the circuit within 45 s. Separately flip the tenant's Autopilot toggle → expect `409
      autopilot_not_enabled` (bot paused, no circuit open).
  13. Confirm metering (`llm_usage.feature` in `autopilot | copilot | handoff`) for the pilot
      tenant, and run Swift Chat's `scripts/validate-cq.ts` locally and record the real pass count.
- **2026-09-10 — Gemini speech-to-text, live and verified.** Google's dedicated ASR model
  `gemini-3.5-transcribe` (GA 2026-08-26) lives on the **Interactions API**, not
  `generateContent`, so the first adapter's connection failed its test; the console showed only
  "Failed" because the reason sat behind the ⓘ. Both are fixed (§4): the adapter routes by model
  id and a failed test now prints the provider's own sentence. **An operator added a real Google
  key and *Test connection* passed** — the first live verification of a non-founding provider.
  Not done: Gemini **text-to-speech** (Google has a TTS model; no adapter yet), and no Georgian
  call has been transcribed through it end to end, so diarization quality, word timings and the
  *36 თვემდე / 36 წლამდე* class of error are still unmeasured against Scribe.
- **Deployed to the server:** the full app — audio analysis, TTS, KB + KB-admin console, fact-check,
  rubric scoring, the whole Next.js frontend — **including the QA fixes below** (pushed + deployed), plus the
  **registered auto-deploy webhook**. `origin/main` and the server are in sync.
- The **7 QA bug fixes** from the full local regression pass (now live):
  1. Malformed UUID in path/body → was **500**, now **400** (global `asyncpg.DataError` handler).
  2. `analyze.py` guarded `stt["text"]` (missing/None transcript no longer 500s + strands the job).
  3. `reembed_document` raises on embedding-count mismatch (was a silent partial re-embed).
  4. `save_config` retries on concurrent version `UniqueViolation` (was 500).
  5. The portal / KB console check `r.ok` + `Array.isArray` before `.map`/`.length`
     (expired-token/404 error body could crash the chunk/document views). Fixed on the legacy
     pages; the guard was carried into the React port, which is what still runs.
  6. KB bulk **retag** uses a brand modal instead of native `prompt()`.
  7. Added `toast.error` i18n key (EN/KA/RU).
- **Local QA is fully green** (see the pass/fail matrix in the session that produced these fixes):
  auth, isolation, KB import (all methods + PDF/DOCX), KB-admin console, fact-check, scoring,
  coexistence, TTS EN/RU/KA, anonymous quota, trilingual + theme + contrast + responsive.

---

## 7. Roadmap / remaining steps

See **`docs/ROADMAP.md`** for the prioritized list. Top items:
1. ~~Register the GitHub webhook~~ ✅ done — pushes auto-deploy.
2. ~~Push + deploy the QA-fix commits~~ ✅ done.
3. ~~HTTPS/TLS~~ ✅ done — `https://ai.communiq.ge`, Let's Encrypt in `cq-web` (see §5). The
   open item in its place is the **chat bot pilot go-live** — the operational checklist in §6.
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
- Pages (clean URLs, no `.html`): `/` (public TTS + analyze), `/workspace` (portal + KB operator
  console), `/console` (superadmin), `/ai-config`, `/usage`, `/editor`, `/account`, `/copilot`.
  The old `.html` paths 301 to these, query string intact.
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
- Frontend is **Next.js, and only Next.js** — the migration finished and the vanilla stack was
  deleted (`frontend/public` is assets only: favicon, logos, the guides zip). All work goes in
  `frontend/next` (App Router, React 19, TypeScript, **static export** — no Node process in
  production; `try_files $uri $uri.html` gives the exported files clean URLs).
  **Read `docs/MIGRATION.md` before touching these pages** — it is the port's
  contract, and it lists the deliberate decisions a rewrite silently turns into regressions.
  Trilingual: every user-facing string needs `en/ka/ru` keys and
  `python3 scripts/check_i18n.py` must pass (it also reports keys still shared with the legacy
  stack — that count is **0** since the cutover, and anything above zero means one came back).
- New AI features: **forced tool-use + strict schema + array normalization**.
- New tenant-scoped queries: **always filter by `client_id`.**
- New DB columns/tables: idempotent (`ADD COLUMN IF NOT EXISTS`) in a `db/*.sql` applied by
  `migrate.py`.
- Keep secrets in `.env`; keep operational access notes in `docs/DEPLOYMENT.local.md` (git-ignored).

---
*Backend/deploy specifics also documented in `backend/CLAUDE.md` and `deploy/CLAUDE.md`.*
