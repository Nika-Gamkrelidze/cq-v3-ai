# ADR-001: Conversational AI — KB-grounded chat bot, operator copilot, and the KB curation loop

**Status:** Proposed
**Date:** 2026-07-21
**Deciders:** Product owner (CommuniQ), backend lead (CQ v3 AI), chat-product lead
**Supersedes:** nothing. Extends the architecture described in `CLAUDE.md` §2–§4.

---

## Context

CQ v3 AI today analyses **recorded calls**: upload → Scribe STT → Claude structured analysis →
RAG fact-check against the tenant's KB → weighted rubric scoring. Every AI feature is
single-turn, batch-shaped, and tolerant of multi-second latency.

We now want to bind a **separate, existing, standalone omnichannel chat product** (web widget +
Instagram DM + Facebook Messenger + WhatsApp, with human operators working an inbox) to this AI
project, delivering three capabilities:

1. **Autopilot bot** — answers end customers grounded in that tenant's KB, in KA/RU/EN, with
   human handoff and voice in/out.
2. **Operator copilot** — while an operator is deciding what to say, the AI proposes grounded
   replies. *Explicitly called out as latency-critical.*
3. **KB curation loop** — mine daily conversations **and** call transcripts, propose KB
   **add / update / remove**, and let callcenter admins **accept / decline / accept-with-edits**.

### Forces

- **The chat site owns the conversation thread.** It is the system of record; CQ AI is not.
- **The chat site is itself multi-tenant** and has its own backend — so it is a *single* API
  consumer legitimately acting on behalf of *many* tenants.
- **Transport is decided: REST + SSE now, WebSocket later**, and the later WS addition must be
  non-breaking.
- **The existing stack is small and fragile in specific, verified ways** (see below). Every
  latency and correctness claim in this ADR was checked against source, and five adversarial
  verification passes returned *"does not hold as stated"* — their corrections are folded in.

### Verified constraints that shaped the decision

| Constraint | Evidence |
|---|---|
| nginx cannot proxy WS **or** stream SSE today — no `proxy_http_version 1.1`, no `Upgrade`/`Connection`, `proxy_buffering` on | [nginx.conf:53](../deploy/nginx.conf) · [tls-ssl.conf:22](../deploy/tls-ssl.conf) |
| The two nginx files are hand-duplicated, and `tls-ssl.conf` is included into the **same http context** | [nginx.conf:4](../deploy/nginx.conf) |
| The nginx apply-gate is diff-based and **can permanently stop firing** after one failed deploy | [deploy.sh:9–22](../deploy/deploy.sh) |
| API is a **single uvicorn process**, no `--workers`; no Redis, no broker, no queue | `backend/Dockerfile` · `docker-compose.yml` |
| One global asyncpg pool, `min_size=1, max_size=10` | `backend/app/db.py:10` |
| Embeddings are **fp32 BGE-M3 on the CPU TEI image**, no quantization flag, no CPU reservation; `embed_texts()` does a DB SELECT **and** builds a new `httpx.AsyncClient` on every call | `docker-compose.yml:25` · `services/embeddings/tei.py:21` · `settings_store.py:32` |
| Anthropic client is constructed per call with **no timeout** (SDK default read=600s × 2 retries) | `services/claude.py:86,99` |
| `retrieve()` returns no chunk/document ids, discards the vector-vs-keyword flag, and the keyword fallback has **no score floor** | `services/retrieval.py:33,47,50` |
| `limits.reserve()` **returns immediately for every non-anonymous principal** — tenant LLM usage is entirely unmetered; the counter upsert is racy | `services/limits.py:22,39–49` |
| The anon quota key reads the **attacker-supplied** first element of `X-Forwarded-For` while nginx *appends* | `services/auth.py:132` · `nginx.conf:57` |
| An invalid Bearer **silently falls through** to the `X-API-Key` branch | `services/auth.py:118` |
| `clients.api_key` is plaintext, full-privilege, unscoped, non-expiring | `services/auth.py:98–102` |
| A new `db/*.sql` is **inert** unless appended to a hardcoded list | `services/migrate.py:71–74` |
| The HNSW vector index only exists via initdb or a `count == 0` branch — its presence in prod is incidental | `db/schema.sql:83` · `migrate.py:48` |
| Every push to `main` auto-deploys, restarts the API, and `sweep_stuck_jobs()` blanket-errors in-flight jobs | `services/analysis.py:145` · `main.py:27` |
| One test in the repo, no CI, no rollback | `backend/tests/` |

---

## Decision

Adopt **Option C — "Mirror and Gate, warm-railed."**

CQ AI stays a **stateless, tenant-scoped answer brain** that the chat site's own backend calls
**server-to-server** over the existing `https://ai.communiq.ge/api/v1/…` surface. CQ gets zero
browser-facing attack surface, and the chat site remains the system of record for the thread.

Four ideas carry the design:

1. **Speed comes from moving the work earlier, not from a faster LLM.** The chat site POSTs
   `/v1/chat/turns` the instant a customer message lands on any channel. CQ returns **202 in
   ~15 ms** and generates suggestions in the background. By the time the operator focuses the
   composer, their read is **one indexed SELECT (~25 ms p50)**.
2. **A three-tier ladder covers the cold path.** Tier 0: tenant canned snippets (0 ms). Tier 1:
   top-3 KB passage cards with **no LLM call at all** (~300 ms). Tier 2: streamed draft replies
   (~2 s). A prefetch miss still shows something useful before the model speaks.
3. **A deterministic pre-LLM grounding gate.** Hit count, top score, and the vector-vs-keyword
   method flag are checked **in code**. Fail the gate and **no Claude call is made** — the
   tenant's refusal copy plus a handoff is emitted. "I don't know" becomes a system property
   rather than a prompt hope, and costs zero tokens.
4. **Curation mines labelled failures, not raw volume.** Ungrounded turns, keyword-only
   retrievals, operator-edited drafts, and existing `kb_check` verdicts are clustered by
   embedding; **one Claude call per cluster**, not per message. LLM spend scales with question
   clusters, which is what makes a nightly job affordable on one box.

Tenant identity is resolved by **intersecting a hashed, scoped integration credential against a
server-side grant table**: `X-CQ-Tenant` can only ever *narrow* within what the DB already
grants, `Principal.client_id` is assigned from the returned row and never from a header, and a
mandatory `X-CQ-Expect-Tenant` assertion turns the chat site's own mapping bugs into 403s instead
of silent cross-tenant writes.

One new container (`cq-worker`, same image, different CMD). No Redis, no broker, no queue.

---

## Options considered

### Option A: Thin Waist — stateless engine, zero new containers

One router, one service, one `.sql`. The chat site holds the existing plaintext
`clients.api_key`. Everything — turn generation and nightly mining — runs inside the single
uvicorn process via `asyncio.create_task`.

| Dimension | Assessment |
|---|---|
| Complexity | **Low** — four services stay four; no new credential, process, or compose change |
| Cost | Cheapest to build; **most expensive to run** — `limits.reserve()` no-ops for tenants, so an auto-copilot across four channels is uncapped Anthropic spend from day one |
| Scalability | **Poor** — hard ceiling at one worker and a 10-connection pool; `pypdf` parsing runs synchronously inside an async handler (`kb.py:69`), so a business-hours KB import stalls every copilot turn regardless of any semaphore |
| Team familiarity | **Best** — nothing new to operate; the whole system still fits in one head |

**Pros:** fastest to demo (~1.5 weeks to copilot); smallest operational surface; correctly
sequences the nginx change early, since it is the one thing that cannot be validated locally.
**Cons:** hands another team's internet-facing codebase a plaintext, full-privilege, non-expiring
key that also grants `DELETE /v1/kb/documents/{id}` — a chat-site compromise deletes the tenant's
knowledge base. Nightly miner inside the API process, which every push restarts. No no-LLM tier,
so a prefetch miss is 1–2 s of blank screen. Declined proposals get re-proposed forever.

### Option B: Warm Rail — dedicated realtime + worker processes, full credential system up front

Split the backend image into three containers (api, rt, worker) plus a second TEI replica.
Postgres LISTEN/NOTIFY across processes, advisory locks, three credential kinds, SSE stream
tickets with `Last-Event-ID` replay from an UNLOGGED table.

| Dimension | Assessment |
|---|---|
| Complexity | **High** — seven processes on one box, cross-process notify, per-process pool sizing, a resume buffer, CI rewired into the webhook |
| Cost | Highest build **and** RAM cost — a second fp32 BGE-M3 replica is ~2.3 GB resident with no CPU isolation to show for it (compose declares no `cpus`/`cpuset` for any service) |
| Scalability | **Best headroom** — head-of-line blocking is structurally solved by isolating the realtime path |
| Team familiarity | **Poor** — layers advanced Postgres primitives onto a repo with one test, no CI, and auto-deploy with no rollback |

**Pros:** best security model (hashed scoped credentials, revocation, dual-key rotation); the
only design that fixes `limits.py` before any chat surface ships; the **tier-1 no-LLM passage
cards** are the single best copilot UX idea in the set; structurally prevents a 30 s
`run_pipeline` sitting in front of an operator.
**Cons:** `proxy_pass http://rt:8000/` makes nginx's startup depend on a brand-new container
resolving — combined with `--force-recreate web`, a failed `rt` build takes down the entire
public site with no rollback. M1 delivers zero user-visible product. A server-parsed
`<<<meta {...}>>>` trailer puts a control channel inside a token stream whose input is untrusted
WhatsApp text. Reintroduces the browser-credential problem its own topology had deleted.

### Option C: Mirror and Gate, warm-railed — **RECOMMENDED**

Option A's topology plus exactly one new process (`cq-worker`) and the two things A gets wrong:
**a real credential and a real meter**. Speed from precompute + a no-LLM tier-1. Quality from a
deterministic pre-LLM gate. Curation over labelled failures.

| Dimension | Assessment |
|---|---|
| Complexity | **Moderate** — five services, one new credential branch, one new nginx location. No LISTEN/NOTIFY, no advisory locks, no UNLOGGED tables, no stream-resume protocol (reconnect = re-GET the finished row) |
| Cost | Precompute roughly **doubles** LLM calls by design — which is exactly why the atomic `usage_counters` rewrite is phase-0 work, and why `llm_usage` records `message.usage` from the first turn |
| Scalability | Copes to the first real spike; the worker owns everything long-running. Beyond that, B's `rt` split is the documented escalation, and P0's measurement tells you when |
| Team familiarity | **Best fit** — one container that deploys itself via `docker compose up -d --build`; no new datastore; highest-value capability first, with a human reviewing every token |

**Pros:** the fast path is architecturally honest (one indexed SELECT, not an optimistic LLM
budget); the pre-LLM gate makes refusal auditable *and free*, and is the strongest injection
control available; `client_id` resolution is fail-closed by construction; curation spend scales
with clusters not messages; every milestone is independently demoable.
**Cons:** still one uvicorn worker on the request path, so the explicit `timeout=6.0` on the
copilot client is load-bearing, not a nicety. Every latency claim depends on a second team
calling `/v1/chat/turns` promptly — if they batch or debounce it, the warm path collapses and CQ
cannot fix it. Requires touching `resolve_principal`, the most isolation-critical function in the
repo. The mirror can silently diverge from the chat site's truth on edit/redaction.

---

## Trade-off analysis

**The real fork is not transport — it is where the work happens, and who is allowed to say which tenant.**

**Where the work happens.** All three options face the same fact: a suggestion needs an
embedding, a vector search, and an LLM call, on one uvicorn process with a 10-connection pool.
B answers with process isolation — correct and expensive. A answers with `asyncio.Semaphore`,
which *does not work*: a semaphore cannot preempt `chunk_text` or the synchronous `pypdf` parse
at `kb.py:69`, both of which block the event loop outright. C answers with **time shifting** — do
the work when the customer's message arrives, so the latency-critical read never touches the LLM.
That is strictly cheaper than B and strictly more effective than A, and it degrades gracefully
(tier-1 cards) rather than to a blank screen. The residual event-loop hazard is fixed by two
small changes C makes explicit: move `extract_text`/`chunk_text` to `asyncio.to_thread`, and put
the concurrency limit on **`kb_ingest`'s** embed call — the thing that *starves* the copilot —
rather than on the copilot itself, which is the direction A got backwards.

**Who says which tenant.** The stated invariant is *"client_id never comes from the request"* —
but the chat site legitimately acts for many tenants, so something in the request must select
one. The correct restatement is **"client_id is never *trusted* from the request."** A dodges
this by handing over N copies of `clients.api_key`: the invariant holds trivially, but the key is
plaintext-compared, full-privilege and non-expiring, so the failure mode is a *deleted* knowledge
base rather than a leaked one. C's grant-table intersection is what makes the header safe — an
ungranted selector returns zero rows and 401s — and the mandatory `X-CQ-Expect-Tenant` assertion
is what makes the *chat site's own* bugs loud instead of silent. That matters more than it looks,
because the curation loop turns one write-side misattribution into a **human-approved, permanent**
cross-tenant KB contamination.

**SSE vs WebSocket.** SSE wins by a mile *here*. WS needs a parallel principal resolver
(`resolve_principal` takes `request: Request`), token-in-subprotocol because browsers cannot set
handshake headers, an explicit Origin check because `CORSMiddleware` does not govern WS
handshakes at all, heartbeats under the inherited 300 s `proxy_read_timeout`, and reconnect logic
exercised on every push to main. The deeper point: C **barely needs streaming for the copilot** —
the warm read is 25 ms and tier-1 is a pre-rendered card list. Streaming exists for the
autopilot's longer answers. Shipping the `map $http_upgrade` directive in P0 anyway costs one
line and makes the eventual WS purely application code.

**Structured output vs token streaming.** These do not combine. C splits by *path*: forced
tool-use everywhere latency is irrelevant (curation synthesis, and the copilot's short two-variant
generation), and streamed plain text with inline `[n]` markers for the autopilot answer.
Critically, C rejects B's server-parsed `<<<meta>>>` trailer: `[n]` resolves against a
**server-held hits list**, so a forged marker yields a wrong citation index, never spoofed
grounding state — which matters because the input is untrusted WhatsApp text.

**What we give up.** C accepts that the request path stays single-worker, that the mirror can
drift, and that the latency story is hostage to another team's integration discipline. Those are
real — and all *observable* (a stale-mirror counter, a turn-arrival-to-view lag metric). That is
the trade: C buys observability of its weak points rather than engineering them away, because
engineering them away is Option B, and Option B does not fit this team, this box, or this deploy
pipeline.

---

## The recommended system

### API contract

Everything the chat site calls lives under **one** prefix, `/v1/chat/`, so exactly **one** new
nginx location block is needed per config file.

```
X-CQ-Key: cqi_<key_id>.<secret>       # integration credential, server-side only
X-CQ-Tenant: <client_id | slug>       # MANDATORY. Narrows within the grant set; never trusted.
X-CQ-Expect-Tenant: <client_id>       # MANDATORY on writes. 403 unless == resolved client_id.
X-CQ-End-User: <opaque stable id>     # metering only, NEVER authorization
Idempotency-Key: <uuid>               # its own column; 409 on reuse with a different body
```

**The Turn envelope — one object, three renderings.** The blocking JSON body, the terminal SSE
`done` payload, and a future WS `turn.completed` frame carry this **byte-identically**. Nothing
in it encodes the transport. *That is what makes WS additive.*

```jsonc
Turn = {
  "proto": 1, "turn_ref": "…", "suggest_ref": "…", "conversation_ref": "…",
  "client_id": "<echoed, so the caller can detect its own mapping bug>",
  "channel": "web|instagram|messenger|whatsapp", "locale": "ka|ru|en",
  "grounding": { "grounded": true, "method": "vector|keyword|none",
                 "top_score": 0.71, "hit_count": 4, "kb_present": true },
  "citations":   [ { "n": 1, "document_id": "…", "chunk_id": "…", "title": "…", "score": 0.71 } ],
  "tier1":       [ { "n": 1, "title": "…", "snippet": "…", "chunk_id": "…" } ],
  "suggestions": [ { "index": 0, "kind": "answer|clarify|escalate", "text": "…", "citations": [1,3] } ],
  "reply":       { "text": "…", "citations": [1], "answered_from_kb": true } | null,
  "handoff":     { "recommended": false, "reason": null, "summary": null },
  "usage":       { "input_tokens": 0, "output_tokens": 0, "model": "…", "latency_ms": {…} }
}
```

| # | Endpoint | Purpose |
|---|---|---|
| 1 | `POST /v1/chat/turns` → **202** | The single ingest, called for **every** inbound message on every channel, autopilot or human-staffed. Writes the mirror row, fires background precompute, returns in ~15 ms. Idempotent on `(client_id, turn_ref)`. |
| 2 | `GET /v1/chat/suggestions/{suggest_ref}` | **The warm path.** One indexed SELECT. `state: running` carries a `retry_after_ms`. |
| 3 | `POST /v1/chat/stream-tickets` | `EventSource` cannot set headers. Without this the credential lands in nginx access logs and browser history. Reuses the existing HMAC signer, 60 s single-use. Same primitive a browser WS handshake will need. |
| 4 | `GET /v1/chat/stream?ticket=…` | `text/event-stream`; events `open` → `grounding` → `tier1` → `delta` → `suggestion` → `done`, `: ping` every 15 s. **No resume protocol** — a reconnecting client re-GETs #2 and reads the finished row from Postgres. |
| 5 | `POST /v1/chat/regenerate` → 202 | `transform: shorter\|warmer\|formal\|to_ru\|to_ka`. Tone is a transform on a chosen card, not extra cards — operator *reading* cost is the bottleneck. |
| 6 | `POST /v1/chat/feedback` → 204 | `shown\|inserted\|edited_sent\|sent_asis\|ignored` + `final_text`. Simultaneously the ROI metric and the highest-quality curation signal in the system. |
| 7 | `POST /v1/chat/answer?stream=1` | Autopilot. Returns the refusal copy + `handoff.recommended=true` when `gate()` fails — **with no Claude call at all**. |
| 8 | `POST /v1/chat/conversations:sync` | Bulk mirror of threads CQ never served (pure operator↔customer, social DMs) — where the *best* answers live. **Every conversation carries its own `client_id`; the whole batch is rejected on any mismatch.** A P1 deliverable owned by the chat-site team. |
| 9 | `DELETE /v1/chat/conversations/{external_ref}` | GDPR purge. Pending proposals whose evidence is purged become `superseded`, not orphaned. |
| 10 | `GET /v1/chat/config` | Persona, greeting, refusal copy, languages, `autopilot_enabled`. |
| 11 | `/v1/curation/proposals…` | List / get / `accept` (body `content` present == accept-with-edits, same endpoint) / `decline {reason}` / `bulk` (**`op='remove'` rejected**). `POST /admin/curation/run` → **202**. |

**Errors** keep FastAPI's exact shape — `{"detail": "<string>"}` — because `CQ.readResp`
(`brand.js:191`) requires `detail` to be a string. Add a **sibling** machine key only:
`{"detail": "…", "code": "kb_empty"}`.

### nginx — the one change that cannot be validated locally

Declare the map **exactly once**, at http level in `deploy/nginx.conf` only. `nginx.conf:4`
includes `tls-ssl.conf` into the **same http context**, so declaring it in both files is a fatal
duplicate-variable error and nginx refuses to start — with no rollback anywhere in the pipeline.

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
```

Then in **both** files, inside the server block, **before** the existing `location /api/`:

```nginx
location /api/v1/chat/ {
    proxy_pass http://api:8000/v1/chat/;      # MUST restate the URI — longest-prefix wins over
                                              # /api/, and reusing http://api:8000/ silently 404s
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;   # ships now; makes the later WS pure app code
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_request_buffering off;
    chunked_transfer_encoding on;
    gzip off;
    proxy_read_timeout 3600s;
    access_log off;                            # the SSE URL carries a stream ticket
}
```

Do **not** factor these into a shared include with a new name (the deploy gate matches filenames),
and do **not** name an upstream container that does not exist yet (nginx resolves literal
upstreams at config load).

### `deploy/deploy.sh` — the gate must become state-based

**Verified defect:** `before=$(git rev-parse HEAD)` runs *before* the pull; the force-recreate
runs *after* `docker compose up -d --build` under `set -euo pipefail`. If the build fails, the
script aborts with HEAD already advanced — so every subsequent deploy computes an empty diff and
**web is never force-recreated again**, serving the stale bind-mount inode permanently with no
remaining trigger. A manual `git pull` (the documented fallback) does the same, and
`webhook.py:57` uses `subprocess.Popen` with no lock, so concurrent pushes race.

```bash
set -euo pipefail
exec 9>/tmp/cq-deploy.lock; flock -n 9 || { echo "deploy already running"; exit 0; }
cd /home/cqdeploy/cq-v3-ai
git pull --ff-only origin "${DEPLOY_BRANCH:-main}"
docker compose -p cqv3 up -d --build

# State-based, not diff-based: a failed or manual deploy can never skip this.
MARKER=/home/cqdeploy/.cq-nginx-applied            # OUTSIDE the repo; git pull never touches it
HASH=$(cat deploy/nginx.conf deploy/tls-ssl.conf deploy/enable-tls.sh | sha256sum | cut -d' ' -f1)
if [[ "$(cat "$MARKER" 2>/dev/null || true)" != "$HASH" ]]; then
    # Validate the NEW files in a throwaway container. `exec web nginx -t` would test the
    # OLD inode, since the bind mount is stale until the container is recreated.
    docker run --rm -v "$PWD/deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
                    -v "$PWD/deploy/tls-ssl.conf:/etc/nginx/tls-enabled/ssl.conf:ro" \
                    -v "$PWD/deploy/certs:/etc/nginx/certs:ro" nginx:alpine nginx -t
    docker compose -p cqv3 up -d --force-recreate web
    printf '%s' "$HASH" > "$MARKER"
fi
curl -fsS --max-time 10 http://localhost/api/health >/dev/null     # fail loudly
```

### Data model

Two new files, **both appended to the hardcoded list at `migrate.py:71–74`**. Three rules applied
throughout, learned from the existing schema:

1. **No hardcoded `vector(N)` anywhere.** `_reconcile_embedding_dim` inspects only `kb_chunks`
   and the dim is runtime-configurable. Curation clustering happens in worker memory; suppression
   medoids are stored as **text** and re-embedded per run.
2. **Partial uniqueness is always a standalone `CREATE UNIQUE INDEX … WHERE`.** Postgres does not
   accept `WHERE` on a table-level `UNIQUE`, and `migrate.py:16` executes each file wholesale — one
   syntax error aborts startup inside the lifespan and the container never becomes healthy, **for
   every tenant, on a push with no CI gate**.
3. **`IF NOT EXISTS` matches by NAME only**, so every `DEFAULT` and index definition below is
   effectively **permanent after the first auto-deploy**. Decide once, in writing, before pushing.

`backend/db/chat.sql`:

- **`integrations` / `integration_secrets` / `integration_grants`** — the credential system.
  `integrations` deliberately has **no `client_id` column**, so a stripped `X-CQ-Tenant` header
  401s and can never fall open to a "home" tenant. `integration_secrets` gives dual-key rotation
  with a 7-day overlap.
- **`chat_conversations`** — the derived mirror. Uses `state`, and its vocabulary is deliberately
  disjoint from `('queued','transcribing','analyzing')` because `sweep_stuck_jobs()` blanket-UPDATEs
  those on **every** boot with no client or age filter. Carries `mined_through` as a durable,
  resumable curation watermark, plus `UNIQUE (id, client_id)` as a composite FK target.
- **`chat_turns`** — with **DB-enforced tenancy**:
  `FOREIGN KEY (conversation_id, client_id) REFERENCES chat_conversations(id, client_id)`, so a
  row's `client_id` cannot disagree with its conversation's owner. `idempotency_key` gets its
  **own** column (fixing, not copying, the `partner.py:147` namespace collapse). A partial index
  `WHERE grounded = false` makes the curation pre-filter one index scan rather than an LLM pass.
- **`copilot_suggestions`** — `suggest_ref` in its **own** namespace (sharing an index with
  `turn_ref` makes `/chat/turns` and `:sync` collide). `stages jsonb` per-stage timings are the
  tuning telemetry. A partial index `WHERE state='running'` is the reaper's index.
- **`copilot_feedback`**, **`chat_configs`** (versioned like `scoring_configs`, `autopilot_enabled`
  **false by default**), **`usage_counters`**, **`llm_usage`** (store **tokens, never dollars**).
- `ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'internal'` —
  on `kb_documents` **only**, never `kb_chunks`, because `ingest_document` does
  `DELETE FROM kb_chunks` + re-INSERT with an explicit column list and would silently revert a
  chunk-level flag on every edit.
- `CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding ON kb_chunks USING hnsw (…)` — because the
  only existing DDL for it is initdb-only or inside a `count == 0` branch. **Its presence in
  production today is incidental**, and every latency budget assumes it.

`backend/db/curation.sql`: `curation_runs` (claimed via a `heartbeat_at` 30-minute stale reclaim —
a plain `ON CONFLICT DO NOTHING` would make a mid-run restart **skip that tenant's day
permanently**), `curation_proposals`, `curation_evidence` (with `source_client_id`, re-verified at
accept time), `curation_suppressions` (semantic, 90-day — a content hash is defeated by any
rewording).

### Latency

> **The headline number is not a per-turn LLM budget — it is 25 ms, because the read path does
> not call the LLM.**

Every embedding figure the design pass initially produced (55–70 ms) was **rejected as
unsupportable**: compose pins the fp32 CPU TEI image with no `--dtype`, no ONNX/int8 flag, and no
CPU reservation. That is XLM-RoBERTa-large-shaped inference (~24 GFLOPs for a 40-token query) on
shared VPS cores, and **the repo has no instrumentation on the embed path at all**.

**P0 therefore ships a measurement harness, and its result gates the P1 promise.** If measured
p95 > 150 ms, an ONNX/int8 TEI image becomes **P0 scope, not a future escalation** — no
application code recovers a slow forward pass.

**Warm path — the product promise (target ≥90% of suggestion views):** nginx→FastAPI 8 ms +
credential verify 3 ms (indexed, 30 s TTL cache) + one indexed SELECT 6 ms + serialize/TLS 8 ms =
**~25 ms p50, ~80 ms p95**. Real because `/v1/chat/turns` fired generation when the customer's
message arrived, and a human takes 2–5 s to read it. **This is the whole latency architecture.**

**Cold path (honest):** auth 10 ms → query build 1 ms (**no LLM condensation hop** — a rewrite
call costs 300–600 ms on the one path called latency-critical) → embed **MEASURE** (assume 250 ms)
→ pgvector ×2 + RRF 25 ms → `gate()` 5 ms → **tier-1 cards on screen ~300 ms p50** → Claude first
token +700 ms → two variants complete +1000 ms ⇒ **~2.0 s p50 / ~4.0 s p95**.

The seven changes that buy those numbers, in priority order: **(1)** precompute on arrival — a
contract decision that beats every micro-optimization combined; **(2)** warm the embedding path
(60 s TTL config cache + module-level keep-alive httpx client) — *this fixes the small term; it
cannot move 300 ms of GEMM*; **(3)** module-level `AsyncAnthropic` with explicit timeouts
(copilot `timeout=6.0, max_retries=0` — a retried suggestion is worse than none, the operator has
already typed); **(4)** protect the encoder **from** ingestion, not the reverse; **(5)** truncate
chat queries to 512 chars (`retrieval.py:24` sends 4000); **(6)** admission control — 429 fast
rather than queue; **(7)** `min_size=2`, env-driven `DB_POOL_MAX`.

### The curation loop

> **Principle: do not summarize yesterday's conversations. Mine the failures the system already
> labels for free.**

**Stage 1 — Harvest (pure SQL, zero tokens).** Six signals: bot could not ground · weak retrieval
(keyword-only or below `min_score`) · **operator corrected the AI** (high edit-distance
`edited_sent`) · human answered after a refusal · `kb_check` verdict `NOT_IN_KB` · `kb_check`
verdict `CONTRADICTED`. Capped at 500 conversations + 200 calls per run, watermark advanced **per
item** so a crash resumes rather than restarts.

> **Cold-start caveat for the runbook:** `audio_jobs.kb_check` is only computed when the principal
> is a tenant **and** the KB already has chunks, inside a bare `except`. So the two highest-precision
> signals are **null for exactly the tenant curation exists to help**. Cold start is chat-driven.

**Stage 2 — Cluster (embeddings, no LLM).** Greedy medoid at cosine ≥ 0.82. Cross-lingual works
out of the box — BGE-M3 puts the Georgian and Russian phrasings of one question in one cluster.
**Suppression is semantic**, so declining a card costs tokens exactly once, not every night
forever. **Poisoning defence:** clusters require `distinct_sources >= 2` *unless* the signal is an
operator correction or a fact-check verdict.

**Stage 3 — Propose.** **One forced-tool Claude call per cluster.** Input includes the *current*
top-5 chunks for the medoid, so it can propose `update chunk X` rather than a duplicate `add`.
`remove` only when directly contradicted or fully superseded. Customer text is delimited as
**data, never instruction**. Explicit `timeout=60.0`.

**Stage 4 — Don't drown the reviewer.** Proposals are per-**cluster** (a tenant with 5,000
conversations sees ~6–10 cards). **Hard cap of 10 open proposals.** Ranked by
`ln(1+occurrences) × confidence × recency × source_weight`. Declining makes the queue *quieter*.
Weekly digest headlines the outcome: *"3 proposals covering 41 conversations your bot could not
answer."*

**Stage 5 — Review UI.** A "KB Health" tab in `tenant.html` plus a cross-tenant tab in
`kb-admin.html`. Each card shows 2–3 **verbatim** customer quotes with channel icons — this is
what makes it feel real rather than machine-generated — deep-linked to the source, and a
character-level diff for updates. **Accept / Accept-with-edits / Decline-with-required-reason.**
Bulk accept excludes `remove`, which needs a typed confirmation. (Native `prompt()` is already a
fixed QA bug and must not reappear.)

**Stage 6 — Apply.** The single most important correction in the whole design:

> **`update` must call `ingest_document`, NOT `reembed_document`.** `reembed_document` only runs
> `UPDATE kb_chunks SET embedding=…` — its own docstring says *"reuses existing chunk content,
> only replaces vectors."* It **never reads `content_text` and never re-chunks.** An accepted
> update would rewrite `content_text`, leave retrieval **bit-for-bit unchanged**, and the next
> night's miner would re-detect the identical gap forever.

Also: `source_type` must be set on the INSERT (`ingest_document`'s argument is never used in its
body); status must be reconciled afterwards (`ingest_document` **swallows every exception**); and
`remove` is `visibility='internal'` plus a human delete candidate — **never a hard delete from an
automated pathway**. Every accept/decline writes a `kb_events` row, so the existing activity
timeline covers curation with zero new UI.

**The loop closes:** an accepted proposal is a re-chunked, re-embedded document, so the very next
copilot query is grounded in it, and the next night's miner sees the gap closed.

### Security bar — must ship before any external customer sees a bot reply

1. **The credential, the grant table, and three rules.** `Principal.client_id` is assigned from
   `row["client_id"]`, never a header. `X-CQ-Tenant` can only *narrow* — an ungranted selector
   returns zero rows → 401. Fail-closed: `integrations` has no `client_id`, and the header is
   mandatory. **Pilot escape hatch that is not a compromise:** P1 issues credentials with
   **exactly one grant row each** — identical code path, blast radius of one tenant, controlled by
   *data*. Going multi-tenant later is inserting rows, not shipping code.
2. **Credential exclusivity + no silent downgrade.** 400 on more than one credential header, and
   fix `auth.py:118` — today an **expired Bearer silently drops to the `X-API-Key` branch**, so a
   caller with a stale default `Authorization` header resolves to one tenant and **flips** to
   another the moment the token expires. *Same request, different tenant, decided by wall clock.*
3. **Mandatory `X-CQ-Expect-Tenant`** on every write, 403 on mismatch, resolved `client_id` echoed
   in every response. The only defence against the *chat site's* mapping bugs.
4. **Metering that actually counts.** Replace the racy read-then-upsert with one atomic
   conditional upsert (`… DO UPDATE SET n = n+1 WHERE n < $4 RETURNING n` — no row returned means
   cap reached). Three dimensions: end-user/hour, tenant/minute, tenant/day.
5. **`llm_usage` from the first turn.** `message.usage` is read nowhere today; chat is 100–1000×
   the request volume of audio, and retrofitting means a permanently blind period on cost.
6. **Close the live quota bypass** (2 lines, worth doing regardless of chat): read `X-Real-IP`,
   which both nginx files already set correctly from `$remote_addr`.
7. **Prompt injection — capability minimization, not a classifier.** KB in `<knowledge_base>`,
   customer text in a **separate user turn** inside `<untrusted_customer_message>`, wrapping the
   **entire** inbound envelope (display name, filename), not just the text. **Load-bearing: the
   chat model gets NO tools** beyond a terminal `submit_answer`; retrieval runs in code *before*
   the call. A successful injection can make the bot say something wrong — never *act*. Python-side
   output validation drops URLs not present in retrieved chunks and strips markdown links entirely
   in v1. Any commitment-shaped output (price, discount, refund) **forces handoff**.
8. **Publishability, default-safe.** `visibility DEFAULT 'internal'`, applied as an **optional
   filter argument** on `retrieve_ranked(…, visibility=None)` — so it is both safe *and*
   zero-regression: analyze, factcheck, scoring, kb_admin and the operator copilot stay
   byte-identical, and only the public autopilot passes `'public'`.
9. **Autopilot launch gate.** OFF by default. Ungrounded ⇒ refuse + offer handoff, never general
   knowledge unless the tenant opts in **in writing**. Keyword-only counts as ungrounded *for the
   public bot* (fine for the copilot — a human reviews it). AI disclosure. **Kill switch** read
   from `app_settings` with a short TTL cache — it must **not** require a redeploy, because every
   deploy also blanket-errors in-flight audio jobs.
10. **Two tests in the same PR as the credential work.** The existing `test_tenant_isolation.py`
    calls `retrieve()` directly and never constructs a `Request` — mirroring it would validate
    none of the new surface. Required: non-granted tenant → 401; expectation mismatch → 403; two
    credentials → 400; invalid Bearer + valid API key → 401 (no downgrade); tenant A's key with
    tenant B's `external_ref` → new conversation under A.

**Never issued to the chat integration:** `kb:write`, `kb:delete`, `scoring:write`, `admin:*`.
Assert the never-issued list in the isolation test — the predictable scope creep is *"just let
curation apply proposals directly,"* which would silently make the integration credential as
dangerous as the plaintext key it replaced. **The write path must be a tenant-admin-authenticated
review action, never an integration-authenticated one.**

---

## Consequences

### What becomes easier
- **WebSocket later is pure application code** — the nginx map and Upgrade headers ship in P0, and
  the Turn envelope is byte-identical across all three transports.
- **Onboarding a tenant to chat is inserting one `integration_grants` row** — no code, no deploy;
  revoked by deactivating the tenant (the query already JOINs `clients ON c.is_active`).
- **A fourth channel (Telegram, Viber, IVR) is zero CQ work** — `channel` is an opaque string and
  the ingest endpoint is channel-agnostic.
- **Per-tenant cost and billing** — `llm_usage` from turn one, so no blind period.
- **Auditing what the bot told a customer** — every AI turn carries grounding, method, top score,
  chunk ids, model and tokens; accepted KB changes appear in the existing activity timeline.
- **Empirical retrieval tuning** — `stages` jsonb gives per-stage p50/p95 per tenant.

### What becomes harder
- **Adding `--workers` to uvicorn.** The precompute BackgroundTask and every in-process TTL cache
  are correct *only* because there is one worker. This needs a loud comment at every cache, a line
  in `CLAUDE.md`, and a startup assert. **The trigger for pain is adding workers, not adding load.**
- **Changing the embedding provider or dimension** — it already meant re-embedding every KB; now
  the clusterer and suppression medoids re-embed too.
- **Changing any new column DEFAULT or index definition** — `IF NOT EXISTS` matches by name, so the
  first pushed shape is effectively permanent.
- **Letting the chat site call CQ from a browser** — reintroduces the browser-safe credential,
  Origin allowlists and CORS narrowing: the largest single deferred item.
- **Moving conversation ownership to CQ** — the mirror is deliberately derived, lossy and
  retention-bounded.
- **Reasoning about a chat turn in isolation** — debugging "the copilot feels slow" now spans two
  codebases.

### What we will need to revisit
- **Immediately after P0's measurement** — if p95 query-embed exceeds ~150 ms, an ONNX/int8 image
  becomes P0 scope.
- **At ~2,000 turns/day for one tenant** — re-measure head-of-line blocking; the escalation is
  Option B's `rt` split (a compose change plus one `proxy_pass`).
- **Before the chat product's second customer** — move from single-grant to multi-grant
  credentials, and re-run the isolation suite *before* flipping.
- **When anyone proposes `--workers`** — caches and precompute must move to the worker or Postgres
  first. This is the single most likely way to break the system silently.
- **When a review queue is consistently empty or full** — the 10-open cap, the `distinct_sources`
  floor and the 0.82 cluster threshold are all guesses. Only real traffic sets them.
- **Once WhatsApp/Instagram volume is real** — PII/PHI redaction stops being roadmap item 4 and
  becomes a compliance blocker for bank, insurer and clinic tenants.

---

## Action items — phased

**P0 — Rail & Ruler (1 week, no product surface).** *Proves the transport in production before
anything depends on it, makes the deploy pipeline unable to silently skip nginx config, and
replaces every latency number here with a measured one.*
1. [ ] Rewrite `deploy/deploy.sh`: `flock`, state-based sha256 marker outside the repo, `nginx -t`
       in a **throwaway** container, post-deploy health smoke.
2. [ ] Land the nginx change as **its own commit with nothing else in it**.
3. [ ] Measurement harness: time a 40-token Georgian query against TEI on the server, cold and
       under a concurrent 300-chunk import. `EXPLAIN ANALYZE` the `client_id`-filtered HNSW query
       for a **small** tenant (pgvector post-filters HNSW — a small tenant can silently get fewer
       than `top_k` hits and spuriously trip the `pg_trgm` fallback).
4. [ ] `services/llm.py`: memoized `AsyncAnthropic`, explicit timeouts, admission semaphore,
       `llm_usage` recording on **every existing** Anthropic call.
5. [ ] Warm the embedding path; move `extract_text`/`chunk_text` to `asyncio.to_thread`; semaphore
       + 32-chunk batching on the **ingest** side.
6. [ ] `retrieve_ranked()` with ids, method flag, keyword score floor, relative gate, optional
       `visibility`; keep `retrieve()` as a thin wrapper so existing callers are byte-identical.
7. [ ] `backend/db/chat.sql` + the `migrate.py` append. **Run it twice against a scratch DB first.**
8. [ ] `limits.py` atomic upsert; `reserve()` stops no-op'ing for tenants; `X-Real-IP` fix.

**P1 — Copilot (2 weeks).** *The emphasized capability, first. Every output passes a human before
a customer sees it — the lowest-risk possible start — and it begins generating the edit-distance
signal that curation depends on.*
Demo: paste a Georgian message → KB cards in ~300 ms, two grounded drafts stream in behind them;
re-open the thread → ~25 ms from cache; ask something off-KB → clean refusal with **zero
Anthropic spend, provable in `llm_usage`**.
1. [ ] `chat_credentials.py` (hashed, scoped, grant-intersection, 7-day dual-key rotation) +
       issuance UI. **Single-grant credentials in P1.**
2. [ ] `auth.py` integration branch, exclusivity 400, no Bearer fall-through, expectation 403.
3. [ ] `chat_store.py` (`client_id` first positional everywhere) + `chat.py` `ChatEvent` generator
       + deterministic `gate()`.
4. [ ] Endpoints 1–6; the tier ladder; `cq-worker` container running the stale-suggestion reaper.
5. [ ] `conversations:sync` — **owned by the chat-site team**, with per-conversation assertion and
       a "mirrored yesterday" counter so silence is visible.
6. [ ] `test_chat_isolation.py` (router-level, 5 cases) + static SQL scan — **same PR** as the
       credential work.

**P2 — KB curation loop (2 weeks).** *Promoted ahead of the public bot because it is what was
described in the most operational detail, and because it needs P1's feedback signal.*
Demo: seed a week of real omnichannel conversations and calls → "Run now" → review ~8 prioritized
proposals with real customer quotes → accept one with an edit → ask the copilot the previously
unanswerable question and watch it answer and cite the new document. **The whole loop in one
sitting.**

**P3 — Autopilot bot (2 weeks).** Gated behind everything that makes it safe to point at the
public internet. Voice reuses the **existing** `/v1/transcriptions` and the `/v1`-mounted TTS
router including the Georgian `eleven_v3` path — **zero new CQ endpoints**.

**P4 — Retrieval quality for short Georgian questions (1.5 weeks + pilot calendar time).** The
0.35 threshold and `top_k=6` were tuned for whole transcripts and cannot survive contact with
4-word questions. Needs ~100 labelled questions per pilot tenant. **The most likely schedule slip
in the plan, and not an engineering task.**

**P5 — Hardening & scale (2–3 weeks, prioritized against real usage).** WebSocket as a pure
adapter · CI gate in `webhook.py` with `git reset --hard` rollback · Bearer revocation · hash and
retire the plaintext `clients.api_key` · narrow CORS · retention pruning + mirror reconciliation ·
cost dashboard · PII/PHI redaction.

---

## Open decisions for the product owner

1. **What does the public bot do when the KB has no answer?** Refuse with the tenant's copy +
   handoff (recommended, and what this design assumes), or answer from general knowledge with a
   disclaimer? Must be settled **before the prompt is written**. If refusal: someone writes that
   copy in EN/KA/RU per tenant, and it must survive a lawyer reading it.
2. **Who audits each tenant's KB for publishability, and when?** `visibility` defaults to
   `internal`, so no bot answers anything until a human marks documents public. Deliberate — but
   if nobody owns it, the bot refuses everything and the product looks broken.
3. **Is the copilot metered commercially at launch, and at what daily cap?** Precompute roughly
   doubles LLM calls by design (~1.6× naive cost). Caps are enforced from P0; the *numbers* are a
   pricing decision. "Unlimited for the pilot" is legitimate — but must be chosen, not defaulted.
4. **Should precompute run for unassigned conversations and out of hours?** Pure cost vs. a cold
   first suggestion after a shift change.
5. **Mirror retention, and what happens to pending proposals when the chat site purges a thread?**
   Proposed 90 days + `superseded`. A bank or clinic may demand shorter, which directly weakens
   the curation corpus. Needs a written contract term that the chat site calls resync on edit.
6. **May an operator's edited final text be stored and reused as a KB exemplar?** The single
   highest-quality signal in the system — and an employee's authored text being retained and fed
   to a model. Employment-policy call, per tenant.
7. **AI-disclosure wording per tenant, per channel, in three languages.** Regulatory exposure
   varies by industry, and Instagram/WhatsApp have their own platform rules.
8. **Which tenant is the pilot, and who produces the labelled KA/RU evaluation set?**
9. **Escalation keyword list and after-hours fallback message, per tenant.**
