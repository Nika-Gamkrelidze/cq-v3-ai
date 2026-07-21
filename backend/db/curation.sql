-- CQ v3 AI — KB curation loop (idempotent; applied on API startup).
-- Implements the "curation.sql" half of the data model in docs/ADR-001-conversational-ai.md:
-- the per-tenant/per-day run ledger, the reviewer's proposal queue, the verbatim evidence
-- behind each card, and semantic suppression of declined clusters.
--
-- READ BEFORE EDITING (same rules as chat.sql — they were learned the hard way):
--   * migrate.py executes this file WHOLESALE on every API boot. One error aborts startup
--     inside the lifespan and the container never becomes healthy — for every tenant, on a
--     push with no CI gate. Every statement here must be idempotent and must stay that way.
--   * `IF NOT EXISTS` matches by NAME only, so every DEFAULT and index definition below is
--     effectively permanent after the first auto-deploy. Changing one later needs its own
--     explicit migration.
--   * Partial uniqueness is ALWAYS a standalone `CREATE UNIQUE INDEX … WHERE`. Postgres does
--     not accept WHERE on a table-level UNIQUE constraint, and `INDEX(...)` inside CREATE
--     TABLE is MySQL syntax.
--   * No `vector(N)` column anywhere in this file. migrate.py's _reconcile_embedding_dim
--     inspects ONLY kb_chunks and the dim is runtime-configurable, so a hardcoded dimension
--     here would silently break on an embedding-provider switch. Clustering happens in worker
--     memory; medoids are stored as TEXT and re-embedded per run.
--
-- RUNBOOK — cold start is CHAT-driven, not call-driven:
--   Of the six harvest signals, S5 (kb_check verdict NOT_IN_KB) and S6 (CONTRADICTED) are the
--   highest-precision ones — and they are the two you will NOT have on day one.
--   `audio_jobs.kb_check` is computed only when the principal is a tenant AND that tenant's KB
--   already has chunks, and the whole block sits inside a bare `except`. So it is null for
--   exactly the thin-KB tenant this loop exists to help. Expect the first weeks of proposals to
--   come from S1–S4 (ungrounded turns, weak retrieval, operator corrections, post-handoff
--   answers). A tenant with no chat traffic yet will legitimately produce zero proposals.

-- ---- Run ledger -------------------------------------------------------------
-- One row per (tenant, day). This is the concurrency control for the whole loop: the worker
-- claims a tenant-day here before doing any work, so two worker replicas — or a worker and a
-- manual `POST /admin/curation/run` — cannot both mine the same window.
--
-- The claim is:
--
--   INSERT INTO curation_runs (client_id, day, state, started_at, heartbeat_at)
--   VALUES ($1, $2, 'running', now(), now())
--   ON CONFLICT (client_id, day) DO UPDATE
--       SET heartbeat_at = now(), state = 'running', started_at = now()
--       WHERE curation_runs.state <> 'done'
--         AND curation_runs.heartbeat_at < now() - interval '30 minutes'
--   RETURNING id;
--
-- WHY the heartbeat reclaim instead of the obvious `ON CONFLICT DO NOTHING RETURNING id`:
-- with DO NOTHING, a run that dies mid-flight leaves a permanent 'running' row, and every
-- later attempt returns no id and skips the tenant's day FOREVER — the exact opposite of
-- resuming. That is not a theoretical crash: every push to main auto-deploys and restarts the
-- whole stack, so a mid-run restart is routine. The 30-minute window is long enough that it
-- cannot steal a live run (a run is minutes, and it heartbeats while it works) and short
-- enough that a crashed one is picked up the same night.
-- Note the RETURNING contract that follows from this: NULL means "somebody else owns this
-- tenant-day right now, or it is already done" — a normal skip, never an error.
CREATE TABLE IF NOT EXISTS curation_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    day date NOT NULL,
    state text NOT NULL DEFAULT 'running',    -- running | done | error
    window_start timestamptz,
    window_end timestamptz,
    -- Funnel counters: candidates harvested (SQL, free) -> clusters (embeddings, free) ->
    -- proposals (one Claude call each). The ratio between the last two is the whole cost
    -- argument of the design, so it is measured rather than assumed.
    candidates integer NOT NULL DEFAULT 0,
    clusters integer NOT NULL DEFAULT 0,
    proposals integer NOT NULL DEFAULT 0,
    llm_calls integer NOT NULL DEFAULT 0,
    error text,
    -- Advanced by the runner while it works; read by the claim above. NOT started_at: a long
    -- but healthy run must not look abandoned.
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE (client_id, day)
);
-- The reclaim scan and the admin "recent runs" view.
CREATE INDEX IF NOT EXISTS idx_curation_runs_state ON curation_runs(state, heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_curation_runs_client ON curation_runs(client_id, day DESC);

-- ---- Proposals (the reviewer's queue) ---------------------------------------
-- One row per CLUSTER of failures, never per message: a tenant with 5,000 conversations sees
-- ~6–10 cards. `cluster_medoid` is kept as TEXT and re-embedded each run (see the no-vector
-- rule above) — it is both what the next run's semantic suppression compares against and what
-- the card headline is drawn from.
CREATE TABLE IF NOT EXISTS curation_proposals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    run_id uuid REFERENCES curation_runs(id) ON DELETE SET NULL,
    -- `remove` is NEVER a hard delete from this pathway: applying it sets the document's
    -- visibility to 'internal' and surfaces a human delete candidate. Enforced in apply.py;
    -- the CHECK here only bounds the vocabulary.
    op text NOT NULL CHECK (op IN ('add', 'update', 'remove')),
    -- pending  -> awaiting a human
    -- accepted -> applied to the KB successfully
    -- declined -> a human said no, with a reason; feeds curation_suppressions
    -- superseded -> a later run replaced this card (or the target doc changed underneath it)
    -- apply_failed -> the human said yes but ingest failed. It MUST be its own state:
    --   kb_ingest.ingest_document swallows every exception (sets the doc to status='error',
    --   logs, returns None), so apply.py re-reads kb_documents.status afterwards and lands
    --   here on error. Without this state a failed apply would be reported as a success and
    --   the gap would silently persist.
    state text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'accepted', 'declined', 'superseded', 'apply_failed')),
    signal text,                              -- S1..S6, the dominant harvest signal
    cluster_key text NOT NULL,                -- sha1 of the normalised medoid
    cluster_medoid text NOT NULL,             -- TEXT, re-embedded per run — never a vector column
    title text,
    proposed_content text,
    rationale text,
    -- No FK on either target, matching the kb_events convention: an audit/review row must
    -- survive the deletion of the document or chunk it refers to, otherwise deleting a
    -- document would erase the record of why it was changed.
    target_document_id uuid,
    target_chunk_id uuid,
    suggested_tags text[] NOT NULL DEFAULT '{}',
    occurrences integer NOT NULL DEFAULT 0,       -- candidates in the cluster
    distinct_sources integer NOT NULL DEFAULT 0,  -- poisoning defence: >= 2 unless S3/S5/S6
    confidence double precision,                  -- model-reported, 0..1
    -- ln(1+occurrences) * confidence * recency * source_weight. Precomputed at write time so
    -- the queue read is one index scan, not a sort over an expression.
    priority double precision NOT NULL DEFAULT 0,
    risk text,                                    -- low | medium | high (free text, model-reported)
    window_start timestamptz,
    window_end timestamptz,
    reviewed_by text,
    reviewed_at timestamptz,
    decline_reason text,                          -- required by the API on decline
    applied_document_id uuid,                     -- no FK, same survive-a-delete reasoning
    applied_at timestamptz,
    apply_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- At most ONE open card per cluster per tenant. Partial (WHERE state='pending') on purpose:
-- once a card is decided, a later run may legitimately raise the same cluster again — the gap
-- may have reopened, or a decline may have expired out of suppression. Without the partial the
-- second night's INSERT would collide with last month's declined row forever.
CREATE UNIQUE INDEX IF NOT EXISTS uq_curation_proposals_open
    ON curation_proposals(client_id, cluster_key) WHERE state = 'pending';
-- The queue read: "this tenant's pending cards, best first" (hard cap of 10 open per the ADR).
CREATE INDEX IF NOT EXISTS idx_curation_proposals_queue
    ON curation_proposals(client_id, state, priority DESC);
CREATE INDEX IF NOT EXISTS idx_curation_proposals_run ON curation_proposals(run_id);

-- ---- Evidence (the verbatim quotes on the card) -----------------------------
-- 2–3 real customer sentences with a channel icon are what make a card feel real rather than
-- machine-generated, and they are also the audit trail for a KB change.
--
-- `source_client_id` is stored SEPARATELY from client_id and is re-verified against
-- proposal.client_id at accept time. It is redundant only if the write side is always correct:
-- a single misattribution on ingest would otherwise be laundered into a permanent, human-
-- APPROVED cross-tenant KB contamination — one tenant's policy text living in another's KB,
-- with an approval record saying it was reviewed. The second copy makes that detectable at the
-- one moment a human is looking.
CREATE TABLE IF NOT EXISTS curation_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    proposal_id uuid NOT NULL REFERENCES curation_proposals(id) ON DELETE CASCADE,
    -- The tenant the underlying chat turn / feedback row / audio job actually belonged to.
    source_client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    signal text,                              -- S1..S6
    source_kind text,                         -- chat_turn | copilot_feedback | audio_job
    source_id uuid,                           -- no FK: evidence outlives its source row
    conversation_id uuid,                     -- no FK, same reason (deep-link target)
    channel text,                             -- web | instagram | messenger | whatsapp | call
    locale text,
    question text,                            -- the verbatim customer quote
    answer text,                              -- the human's answer, when the signal carries one
    occurred_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_curation_evidence_proposal
    ON curation_evidence(proposal_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_curation_evidence_client
    ON curation_evidence(client_id, created_at DESC);

-- ---- Suppression (declining makes the queue quieter) ------------------------
-- Declining a card must cost tokens exactly ONCE, not every night forever. The miner loads
-- these medoids, re-embeds them in the SAME batch as the day's candidates, and drops anything
-- within cosine 0.88 BEFORE any Claude call — so suppression is semantic, not a content hash.
-- A hash is defeated by any rewording, and rewording is what conversational traffic IS: the
-- same question arrives in Georgian, in Russian, with a typo, and each variant would be a
-- fresh billable cluster.
--
-- `medoid` is TEXT for the same reason as curation_proposals.cluster_medoid: no vector column,
-- re-embedded per run, survives an embedding-provider change.
-- `until` bounds it (the ADR's 90 days) — a decline reflects the KB as it was, and a year later
-- the answer may genuinely be worth adding. NULL means never expire.
CREATE TABLE IF NOT EXISTS curation_suppressions (
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    cluster_key text NOT NULL,
    medoid text NOT NULL,
    reason text,
    proposal_id uuid,                         -- no FK: the suppression outlives the card
    until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text,
    PRIMARY KEY (client_id, cluster_key)
);
-- The miner's load: "this tenant's still-active suppressions".
CREATE INDEX IF NOT EXISTS idx_curation_suppressions_active
    ON curation_suppressions(client_id, until);
