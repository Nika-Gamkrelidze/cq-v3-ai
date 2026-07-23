-- CQ v3 AI — tenant KB operations (idempotent; applied on API startup).
-- One table: the queue for full-KB re-embeds requested from the tenant KB console.
--
-- READ BEFORE EDITING (same rules as chat.sql / curation.sql — learned the hard way):
--   * migrate.py executes this file WHOLESALE on every API boot, and the list of files there
--     is HARDCODED — a db/*.sql that is not in it is inert and silently never runs. One bad
--     statement here aborts the lifespan and the api container never becomes healthy, for
--     every tenant, on a push with no CI gate. Every statement must stay idempotent.
--   * `IF NOT EXISTS` matches by NAME only, so every DEFAULT, CHECK and index below is
--     effectively permanent after the first auto-deploy. Changing one later needs its own
--     explicit migration.
--   * Partial uniqueness is ALWAYS a standalone `CREATE UNIQUE INDEX … WHERE`. Postgres does
--     not accept WHERE on a table-level UNIQUE constraint, and `INDEX(...)` inside CREATE
--     TABLE is MySQL syntax.
--   * No `vector(N)` column here. migrate.py's _reconcile_embedding_dim inspects ONLY
--     kb_chunks and the dimension is runtime-configurable.

-- ---- Full-KB re-embed queue -------------------------------------------------
-- WHY a queue and not a request: re-embedding a whole knowledge base is the single most
-- expensive thing a tenant can trigger. Embeddings are fp32 BGE-M3 on ONE CPU-bound TEI
-- container with no CPU reservation, shared with live retrieval (the autopilot's read path and
-- every analysis job). Running it inline would blow past nginx's 300 s proxy_read_timeout,
-- hold one of ten asyncpg pool connections for the duration, and starve every other tenant's
-- queries for as long as it ran. So the API only ever writes a row here; `cq-worker` claims it
-- and drains it at a throttled rate while the console polls GET /kb/reembed/status.
--
-- The operator surface (POST /admin/kb/{tenant_id}/reembed) deliberately still runs INLINE and
-- does not touch this table: it predates the queue, its response shape is consumed by
-- kb-admin.html, and an operator triggering it knows what it costs. Only the self-serve tenant
-- path is queued.
CREATE TABLE IF NOT EXISTS kb_reembed_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    -- queued    -> written by the API, waiting for a worker
    -- running   -> claimed; heartbeat_at is advanced while it works
    -- done      -> finished (failed_documents may still be > 0)
    -- error     -> the run itself failed; `error` says why
    -- cancelled -> a human stopped it
    state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'running', 'done', 'error', 'cancelled')),
    -- The actor string kb_events uses ("tenant:<user_id>" / "superadmin"), not a FK: this is
    -- an audit field and must survive the deletion of the user who clicked the button.
    requested_by text,
    -- Denominator, numerator and failure count for the progress UI. total_documents is filled
    -- at enqueue time (count of status='ready' documents) so the bar has a scale immediately,
    -- and may be refreshed by the worker when it starts.
    total_documents integer NOT NULL DEFAULT 0,
    done_documents integer NOT NULL DEFAULT 0,
    failed_documents integer NOT NULL DEFAULT 0,
    error text,
    -- Advanced by the worker while it works, NOT started_at: a long but healthy run must not
    -- look abandoned. A reclaim sweep can treat `running` rows with a stale heartbeat as dead
    -- (every push to main auto-deploys and restarts the stack, so a mid-run kill is routine).
    heartbeat_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);

-- ONE active job per tenant. This is the double-click guard, and it lives in the database
-- rather than the UI on purpose: the enqueue is `INSERT ... ON CONFLICT DO NOTHING RETURNING`,
-- so a second request returns no row and the API answers 409 instead of queueing a second
-- full-KB re-embed behind the first.
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_reembed_jobs_active
    ON kb_reembed_jobs (client_id) WHERE state IN ('queued', 'running');

-- The worker's claim query: oldest queued job first, plus the stale-heartbeat reclaim scan.
CREATE INDEX IF NOT EXISTS idx_kb_reembed_jobs_claim
    ON kb_reembed_jobs (state, created_at);

-- GET /kb/reembed/status: this tenant's active-or-most-recent job.
CREATE INDEX IF NOT EXISTS idx_kb_reembed_jobs_client
    ON kb_reembed_jobs (client_id, created_at DESC);
