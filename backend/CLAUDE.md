# backend/ — Claude context

FastAPI service + (later) AI workers. See the root CLAUDE.md for the big picture.

## Layout
- `app/main.py` — FastAPI app + `/health`.
- `app/config.py` — settings from env (pydantic-settings).
- `app/db.py` — asyncpg pool (connect on lifespan startup).
- `app/models.py` — pydantic request/response models.
- `app/routers/calls.py` — `POST /calls` (ingest, idempotent, X-API-Key), `GET /calls/{id}`.
- `app/services/` — (to build) elevenlabs transcription, claude scoring, pgvector retrieval.
- `app/workers/` — (to build) transcribe + score background processes.
- `db/schema.sql`, `db/seed.sql` — schema (8 tables) + dev seed (demo client).

## Transcription worker (the next build)
New file `app/workers/transcribe.py`: query `calls WHERE status='pending'`, fetch audio from
object storage (S3/Spaces — TBD), call Scribe (diarize on, language auto), insert into
`transcripts`, update `calls.status`. Run as a separate compose service using the SAME image
with a different command, triggered on a schedule / simple queue.

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
