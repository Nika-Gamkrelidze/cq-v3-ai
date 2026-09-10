# backend/ — Claude context

FastAPI service (`cq-api`) + one periodic worker process (`cq-worker`, same image, different
CMD). See the root CLAUDE.md for the big picture.

## Layout
- `app/main.py` — FastAPI app + `/health`.
- `app/config.py` — settings from env (pydantic-settings).
- `app/db.py` — asyncpg pool (connect on lifespan startup).
- `app/models.py` — pydantic request/response models.
- `app/routers/calls.py` — `POST /calls` (ingest, idempotent, X-API-Key), `GET /calls/{id}`.
- `app/services/` — the AI seams (`llm.py`, `voice.py`), retrieval, KB ingest/re-embed, fact-check,
  scoring, chat (`chat.py`, `chat_store.py`), `curation/` (miner → cluster → propose → apply),
  `retention.py`, `audio_convert.py`, `health_metrics.py` (server-health sampler, per-request
  load accumulator, `/admin/health/*` queries over `system_metrics` / `tenant_load`). Routers beyond `calls.py` are listed in the root CLAUDE.md.
- `app/worker.py` — the `cq-worker` container (`python -m app.worker`); see below.
- `db/schema.sql`, `db/seed.sql` — schema (8 tables) + dev seed (demo client).

## The worker (`app/worker.py`, container `cq-worker`)
Runs as `python -m app.worker` — a compose service on the SAME image as the api so a push to
`main` redeploys it too. Why a second process: the api serves one uvicorn worker and everything
in it sits in front of an operator waiting on a spinner, so anything periodic or minutes-long on
the shared TEI encoder must not share that event loop or pool (the worker has its own small pool,
`DB_POOL_MAX=5`). Duties, each a `_run_duty` loop on its own interval:
- `reap_stale_suggestions` — fails `copilot_suggestions` rows stuck in `running` (a deploy kills
  the api mid-precompute; without this the operator's poll says "pending" forever).
- `curation_pass` — nightly KB curation mining (`services/curation`: harvest → cluster → propose),
  one run per tenant staggered across 02:00–05:00 UTC; plus manual `POST /admin/curation/run`
  requests picked up per tick.
- `apply_accepted_proposals` — applies curation proposals a **human accepted**. Nothing
  auto-applies at any confidence.
- `kb_reembed_pass` — queued full-KB re-embeds (`kb_reembed_jobs`; the console gets a 202, the
  worker runs it — claim/throttle/resume live in `services/kb_reembed`).
- `retention_purge` — deletes anonymous submissions past their retention deadline (files, then
  rows), hence the shared `media` volume.
- `convert_sweep` — removes expired converted-audio ZIPs (`audio_convert`).
- `health_purge` — hourly `health_metrics.purge(retention_days)` over `system_metrics` /
  `tenant_load`. The **sampler and load flusher themselves run in the api** (lifespan tasks,
  `HEALTH_SAMPLER_ENABLED`), because that is the process whose requests and RSS are being
  measured; only the deletion is out-of-band.

Deliberately NOT done here: **no migrations** (`run_startup_migrations()` is api-only — two
processes racing CREATE/ALTER and the embedding-dim reconciliation on boot is not safe) and no
`analysis.sweep_stuck_jobs()` (api startup-only). The worker polls until the api's migrations
have created its tables (`_await_schema`) before the first sweep, and shuts down cooperatively on
SIGTERM (finishes the current sweep, bails at 45 s; compose grants 60 s).

The original spec's `app/workers/transcribe.py` (batch `calls` → Scribe → `transcripts`) was
never built; transcription runs synchronously inside `POST /analyze`.

## Conventions
- Raw SQL via asyncpg ($1 placeholders). uuid PKs (gen_random_uuid). timestamptz everywhere.
- Return pydantic models; keep DB writes idempotent (ON CONFLICT) where PHP may retry.

## Anonymous quota identity (services/auth.py)
- `client_ip(request)` = **what we saw**: X-Real-IP, else the **LAST** X-Forwarded-For element,
  else the socket peer. Never the first XFF element — a proxy that appends leaves that one under
  the caller's control, and reading it lets anyone mint a fresh quota bucket per request. Used by
  the `client_ip` audit columns and the per-address registration cap. Contract unchanged.
- `visitor_key(request)` = **who this visitor is**, or `None`. It refuses an address this
  deployment's own network could have substituted (loopback + RFC1918, where every docker bridge
  lives; **not** 100.64/10 — CGNAT is real mobile traffic). `Principal.anon_key` is this, so an
  anonymous caller we cannot tell apart from any other has **no** key.
- With no key the anonymous tier **fails closed**: `limits.reserve/check` raise **503**,
  `limits.snapshot` returns `enabled:false, visitor_identified:false` (200, same shape, so the
  public page renders a sign-in prompt rather than breaking), and `analyze._scope` matches no
  rows. Pooling every visitor into one shared allowance — the bug this replaces, where one
  visitor exhausting the day locked out the world — must never come back.
- Why it happened: a container reached through a published port sees the host's NAT gateway
  (172.x.0.1) as `$remote_addr`, so nginx's X-Real-IP was one constant. **No nginx setting can
  recover it** — `real_ip` needs a header from a trusted upstream proxy and the kernel's NAT
  sends none. The fix is on the host; `GET /health -> client_addressing: ok | nat-masked` says
  which state the deployment is in, from one unauthenticated curl.
- `ANON_TRUST_PRIVATE_CLIENT_IPS=true` (`.env`) is for local dev / LAN-only deployments, where
  private addresses really are the traffic. It must stay false on anything public.
