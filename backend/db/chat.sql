-- CQ v3 AI — conversational AI additions (idempotent; applied on API startup).
-- Implements the "Data model" section of docs/ADR-001-conversational-ai.md: the chat-site
-- integration credential, the derived conversation mirror, copilot suggestions/feedback,
-- per-tenant chat config, and the metering tables.
--
-- READ BEFORE EDITING:
--   * migrate.py executes this file WHOLESALE on every API boot. One error aborts startup
--     inside the lifespan and the container never becomes healthy — for every tenant, on a
--     push with no CI gate. Every statement here must be idempotent and must stay that way.
--   * `IF NOT EXISTS` matches by NAME only, so every DEFAULT and index definition below is
--     effectively permanent after the first auto-deploy. Changing one later needs its own
--     explicit migration.
--   * Partial uniqueness is ALWAYS a standalone `CREATE UNIQUE INDEX … WHERE`. Postgres does
--     not accept WHERE on a table-level UNIQUE constraint.
--   * No hardcoded `vector(N)` anywhere — the embedding dim is runtime-configurable and
--     migrate.py's reconciler only inspects kb_chunks.

-- ---- Vector index: make its existence non-incidental ----------------------
-- The only other DDL for this index is initdb-only (db/schema.sql, which never re-runs on a
-- provisioned volume) or inside migrate.py's `count == 0` branch. So on any database that
-- already had chunks when it was upgraded, the index may simply not be there — its presence
-- in production today is incidental, and every latency budget in the ADR assumes it.
CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding ON kb_chunks USING hnsw (embedding vector_cosine_ops);

-- ---- Integration credentials (the chat site) -------------------------------
-- `integrations` deliberately has NO client_id column. The chat site is itself multi-tenant
-- and legitimately acts for many tenants, so the tenant is selected per request by the
-- X-CQ-Tenant header — which is only ever allowed to NARROW within integration_grants.
-- Without a column here there is no "home" tenant to fall back to, so a stripped or unknown
-- header intersects zero grant rows and 401s instead of silently resolving to somebody.
CREATE TABLE IF NOT EXISTS integrations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    kind text NOT NULL DEFAULT 'chat',        -- chat | (future integration kinds)
    scopes text[] NOT NULL DEFAULT '{}',      -- never kb:write / kb:delete / scoring:write / admin:*
    is_active boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Two live secrets per integration give dual-key rotation with a 7-day overlap: issue the new
-- one, let the caller cut over, then revoke the old. The secret itself is never stored —
-- `key_id` is the public lookup handle carried in X-CQ-Key, `secret_hash` is what we compare.
CREATE TABLE IF NOT EXISTS integration_secrets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id uuid NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    key_id text NOT NULL,
    secret_hash text NOT NULL,
    label text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    revoked_at timestamptz,
    last_used_at timestamptz
);
-- key_id is the lookup key for every authenticated request, so it must be globally unique
-- and indexed — credential verify is on the 25 ms warm path.
CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_secrets_key_id ON integration_secrets(key_id);
CREATE INDEX IF NOT EXISTS idx_integration_secrets_integration ON integration_secrets(integration_id);

-- The grant table is the whole authorization model: an integration may act for a tenant IFF a
-- row exists here. Onboarding a tenant is one INSERT — no code, no deploy. P1 issues
-- credentials with exactly one grant row each, so the blast radius is controlled by data.
CREATE TABLE IF NOT EXISTS integration_grants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id uuid NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    scopes text[] NOT NULL DEFAULT '{}',      -- may narrow further than integrations.scopes
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text,
    UNIQUE (integration_id, client_id)
);
CREATE INDEX IF NOT EXISTS idx_integration_grants_client ON integration_grants(client_id);

-- ---- The conversation mirror ----------------------------------------------
-- Derived, lossy and retention-bounded: the chat site remains the system of record for the
-- thread. We keep only what curation and the copilot need.
--
-- `state` uses a vocabulary deliberately DISJOINT from ('queued','transcribing','analyzing'):
-- sweep_stuck_jobs() blanket-UPDATEs rows in those states on EVERY boot with no client or age
-- filter, and every push to main restarts the API. Any overlap would have chat rows errored
-- out by an audio-pipeline janitor that has never heard of them.
CREATE TABLE IF NOT EXISTS chat_conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    external_ref text NOT NULL,               -- the chat site's own thread id
    channel text NOT NULL DEFAULT 'web',      -- web | instagram | messenger | whatsapp | …opaque
    locale text,                              -- ka | ru | en
    end_user_ref text,                        -- opaque, metering only — NEVER authorization
    state text NOT NULL DEFAULT 'open',       -- open | handoff | resolved | purged
    subject text,
    -- Durable, resumable curation watermark: the miner advances this per item, so a crash
    -- resumes rather than restarting the tenant's day.
    mined_through timestamptz,
    last_message_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, external_ref)
);
-- Composite-FK target: lets chat_turns reference (conversation_id, client_id) together, so a
-- turn's client_id can never disagree with its conversation's owner. Redundant with the PK on
-- its own, but Postgres requires a unique index on exactly these columns to point an FK at.
CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_conv_ident ON chat_conversations(id, client_id);
-- The nightly miner's index: "this tenant's conversations with activity since the watermark".
CREATE INDEX IF NOT EXISTS idx_chat_conv_mining ON chat_conversations(client_id, last_message_at DESC);

-- ---- Turns -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_turns (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL,
    turn_ref text NOT NULL,                   -- the chat site's own message id
    role text NOT NULL,                       -- customer | operator | bot
    content text,
    locale text,
    -- Grounding telemetry, written by the deterministic pre-LLM gate. Recorded even when no
    -- Claude call was made, which is what makes a refusal auditable and free.
    grounded boolean NOT NULL DEFAULT false,
    method text,                              -- vector | keyword | none
    top_score double precision,
    hit_count integer,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- Its own column, NOT folded into turn_ref: sharing one namespace is exactly the
    -- partner.py:147 collapse this design is fixing.
    idempotency_key text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    -- DB-enforced tenancy: the pair must match a real (conversation, owner) pair.
    FOREIGN KEY (conversation_id, client_id)
        REFERENCES chat_conversations(id, client_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_turns_ref ON chat_turns(client_id, turn_ref);
-- Composite-FK target, same reasoning as uq_chat_conv_ident: lets copilot_suggestions point at
-- (turn_id, client_id) together.
CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_turns_ident ON chat_turns(id, client_id);
-- Partial: only the rows that actually carry a key participate, so replays 409 while ordinary
-- ingest is unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_turns_idem
    ON chat_turns(client_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_turns_conv ON chat_turns(conversation_id, created_at);
-- The curation pre-filter: "everything the bot could not ground" becomes one index scan
-- instead of an LLM pass over the day's volume.
CREATE INDEX IF NOT EXISTS idx_chat_turns_ungrounded
    ON chat_turns(client_id, created_at DESC) WHERE grounded = false;

-- ---- Copilot suggestions (the precomputed warm path) -----------------------
-- `suggest_ref` lives in its OWN namespace — sharing an index with turn_ref would make
-- /chat/turns and conversations:sync collide on the same generated identifier.
CREATE TABLE IF NOT EXISTS copilot_suggestions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    conversation_id uuid,
    turn_id uuid,
    suggest_ref text NOT NULL,
    state text NOT NULL DEFAULT 'running',    -- running | ready | refused | error | expired
    grounding jsonb NOT NULL DEFAULT '{}'::jsonb,
    tier1 jsonb NOT NULL DEFAULT '[]'::jsonb,       -- no-LLM KB passage cards
    suggestions jsonb NOT NULL DEFAULT '[]'::jsonb,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    handoff jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Per-stage timings (embed / search / gate / llm). The tuning telemetry that turns the
    -- ADR's estimated latency budget into a measured one, per tenant.
    stages jsonb NOT NULL DEFAULT '{}'::jsonb,
    model text,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
-- Same DB-enforced tenancy chat_turns gets, for the same reason: without it a suggestion could
-- reference a conversation (or turn) owned by a DIFFERENT tenant, or a deleted one, and nothing
-- would object. Both columns are nullable and the FK is MATCH SIMPLE, so a suggestion with no
-- conversation/turn yet is still allowed — but a non-NULL one must belong to this client.
-- Added as ALTER … not inline, because the table may already exist from an earlier boot;
-- ADD CONSTRAINT has no IF NOT EXISTS, hence the catalog check.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'copilot_suggestions_conv_fkey') THEN
        ALTER TABLE copilot_suggestions ADD CONSTRAINT copilot_suggestions_conv_fkey
            FOREIGN KEY (conversation_id, client_id)
            REFERENCES chat_conversations(id, client_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'copilot_suggestions_turn_fkey') THEN
        ALTER TABLE copilot_suggestions ADD CONSTRAINT copilot_suggestions_turn_fkey
            FOREIGN KEY (turn_id, client_id)
            REFERENCES chat_turns(id, client_id) ON DELETE CASCADE;
    END IF;
END $$;
-- The generation's own INPUTS, captured when the row is claimed. Without them the SSE path has
-- nowhere to read the caller's locale/mode back from — a stream started from a stored row would
-- silently regenerate every turn in the default language — and `regenerate` could not inherit
-- them either. `envelope` is the generation's OUTPUT: the exact Turn object the engine built,
-- stored verbatim so the warm read, the SSE replay and the live `done` frame are the same bytes
-- instead of three reconstructions that drift. The dedicated columns beside it (grounding,
-- tier1, suggestions, citations, handoff, stages) stay, because they are what the curation
-- queries index and aggregate on; `envelope` is never queried into.
ALTER TABLE copilot_suggestions ADD COLUMN IF NOT EXISTS locale text;
ALTER TABLE copilot_suggestions ADD COLUMN IF NOT EXISTS mode text;
ALTER TABLE copilot_suggestions ADD COLUMN IF NOT EXISTS integration_id uuid;
ALTER TABLE copilot_suggestions ADD COLUMN IF NOT EXISTS envelope jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS uq_copilot_suggest_ref ON copilot_suggestions(client_id, suggest_ref);
CREATE INDEX IF NOT EXISTS idx_copilot_suggestions_conv ON copilot_suggestions(conversation_id, created_at DESC);
-- The reaper's index: the worker sweeps abandoned generations, and this keeps that scan
-- proportional to what is actually in flight rather than to the whole table.
CREATE INDEX IF NOT EXISTS idx_copilot_suggestions_running
    ON copilot_suggestions(created_at) WHERE state = 'running';

-- Simultaneously the ROI metric and the highest-quality curation signal in the system:
-- a high edit distance between `text` and `final_text` is an operator correcting the AI.
CREATE TABLE IF NOT EXISTS copilot_feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    suggestion_id uuid REFERENCES copilot_suggestions(id) ON DELETE SET NULL,
    suggest_ref text,
    suggestion_index integer,
    action text NOT NULL,          -- shown | inserted | edited_sent | sent_asis | ignored
    final_text text,
    operator_ref text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_copilot_feedback_client ON copilot_feedback(client_id, created_at DESC);

-- ---- Per-tenant chat config (versioned, like scoring_configs) --------------
CREATE TABLE IF NOT EXISTS chat_configs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    version integer NOT NULL DEFAULT 1,
    persona text,
    greeting jsonb NOT NULL DEFAULT '{}'::jsonb,      -- {en,ka,ru}
    refusal_copy jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {en,ka,ru} — a lawyer reads this
    languages text[] NOT NULL DEFAULT '{}',
    canned jsonb NOT NULL DEFAULT '[]'::jsonb,        -- tier-0 snippets, 0 ms
    -- OFF by default and it must stay that way: the public bot is the only surface where a
    -- model speaks to an end customer with no human in the loop.
    autopilot_enabled boolean NOT NULL DEFAULT false,
    settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text,
    UNIQUE (client_id, version)
);
-- One active config per tenant (mirrors uq_scoring_active).
CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_configs_active ON chat_configs(client_id) WHERE is_active;

-- ---- Metering --------------------------------------------------------------
-- usage_counters has no client_id: `scope_key` is a text discriminator carrying the dimension
-- (end-user/hour, tenant/minute, tenant/day), so one atomic conditional upsert covers all
-- three. llm_usage's FK is nullable + ON DELETE SET NULL so cost history survives a tenant
-- being deleted.
CREATE TABLE IF NOT EXISTS usage_counters (
    scope_key text NOT NULL, bucket text NOT NULL, kind text NOT NULL,
    n integer NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope_key, bucket, kind));

-- Tokens, never dollars: prices change, usage doesn't. Written from the first turn, because
-- chat is 100–1000× the request volume of audio and retrofitting means a permanently blind
-- period on cost.
CREATE TABLE IF NOT EXISTS llm_usage (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid REFERENCES clients(id) ON DELETE SET NULL,
    integration_id uuid, feature text NOT NULL, model text NOT NULL,
    input_tokens integer, output_tokens integer,
    cache_read_tokens integer, cache_creation_tokens integer,
    latency_ms integer, ok boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_llm_usage_client ON llm_usage(client_id, created_at DESC);

-- ---- Publishability --------------------------------------------------------
-- Default-safe: nothing is visible to the public autopilot until a human marks it public.
-- On kb_documents ONLY, never kb_chunks — ingest_document does `DELETE FROM kb_chunks` plus a
-- re-INSERT with an explicit column list, so a chunk-level flag would silently revert to its
-- default on every document edit. Applied as an optional filter on retrieve_ranked(), so
-- analyze / factcheck / scoring / kb_admin / the operator copilot stay byte-identical.
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'internal';
CREATE INDEX IF NOT EXISTS idx_kb_documents_visibility ON kb_documents(client_id, visibility);
