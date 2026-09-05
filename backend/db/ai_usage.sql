-- Token accounting detailed enough to price a tenant, and per-tenant AI configuration.
--
-- `llm_usage` already recorded WHICH TENANT spent WHAT on WHICH FEATURE. Two questions it
-- could not answer are exactly the ones an invoice argument turns on: *which of their people*
-- ran it, and *which recording* it belongs to. Both are added here rather than in a new table
-- so one row still means one call and the existing per-tenant totals keep working unchanged.
--
-- Idempotent, like every migration here: ADD COLUMN IF NOT EXISTS, CREATE IF NOT EXISTS.

ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS actor text;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS job_id uuid;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS byo boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN llm_usage.actor IS
  'Who ran it, in the same vocabulary as kb_events: tenant:<user_id> for a person, '
  'tenant:apikey for a server-to-server key, tenant:superadmin for an operator acting on '
  'the workspace, anonymous for the public app. NULL on rows written before this column.';
COMMENT ON COLUMN llm_usage.byo IS
  'True when the call ran on the TENANT''s own provider key: the tokens were consumed, but '
  'the charge landed on their account, not ours. The console keeps the two apart — summing '
  'them would overstate what the deployment actually pays for. False on rows written before '
  'this column, which is correct: bring-your-own-key did not exist yet.';
COMMENT ON COLUMN llm_usage.job_id IS
  'The recording (audio_jobs.id) this call was part of, when there is one — so a tenant '
  'querying a line on their bill can be shown the call it came from. Deliberately NOT a '
  'foreign key: usage history must outlive the recording, which retention deletes.';

-- The console reads these three ways round, and only these three.
CREATE INDEX IF NOT EXISTS idx_llm_usage_client_actor
  ON llm_usage(client_id, actor, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_job
  ON llm_usage(job_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_llm_usage_feature
  ON llm_usage(client_id, feature, created_at DESC);


-- Per-tenant AI configuration -------------------------------------------------------------
--
-- The default is the deployment's own model and key: every tenant uses it and nothing here
-- exists for them. A row appears only when a tenant asks for something else — a different
-- model, or their OWN provider credential so the spend lands on their account rather than
-- ours. That second case is the reason `api_key` is here at all, and it is why this table is
-- superadmin-managed: a tenant that could set its own key could also point the product at a
-- endpoint that logs every transcript it is handed.
CREATE TABLE IF NOT EXISTS tenant_ai_configs (
    client_id   uuid PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    -- 'anthropic' today. Kept as text (not an enum) because adding a provider must not need
    -- a migration on a running deployment.
    provider    text        NOT NULL DEFAULT 'anthropic',
    model       text,                       -- NULL = the deployment default
    api_key     text,                       -- NULL = bill to us, on the deployment's key
    base_url    text,                       -- for an Anthropic-compatible gateway
    -- Off by default even once a row exists, so an operator can prepare a tenant's settings
    -- and switch them over deliberately rather than the moment they hit Save.
    enabled     boolean     NOT NULL DEFAULT false,
    notes       text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text
);

COMMENT ON TABLE tenant_ai_configs IS
  'Per-tenant AI overrides. Absent row, or enabled=false, means the tenant runs on the '
  'deployment default — which is what almost every tenant does.';
COMMENT ON COLUMN tenant_ai_configs.api_key IS
  'The tenant''s OWN provider key, when they bring one. Never returned to any API response: '
  'the console shows only whether one is set. Usage on a tenant key is still recorded in '
  'llm_usage, because "what did this workspace consume" is a support question even when the '
  'answer costs us nothing.';
