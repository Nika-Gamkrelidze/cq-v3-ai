-- The AI provider registry: named connections, per-workspace assignment, bring-your-own keys.
--
-- Three capabilities (llm | stt | tts), each resolved on its own. The chain, top layer wins
-- for every field it actually sets (services/ai_resolve.py):
--
--     code defaults
--       <- the DEFAULT connection for the capability          (ai_connections.is_default)
--       <- the connection ASSIGNED to this workspace          (tenant_ai_assignments)
--       <- the workspace's OWN key for the capability         (tenant_ai_overrides)
--     with the legacy admin-panel key + model underneath everything, so a deployment whose
--     registry is empty behaves exactly as it did before these tables existed.
--
-- Keys are stored SEALED (services/secrets.py: 'enc:v1:<token>' when SECRETS_KEY is set,
-- plaintext otherwise) — hence the `_enc` suffix. Nothing here is ever returned by an API:
-- responses carry `has_key` and a masked hint only.
--
-- Idempotent, like every migration here: CREATE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS ai_connections (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text        NOT NULL,
    capability  text        NOT NULL,          -- llm | stt | tts (checked in code, not an enum)
    provider    text        NOT NULL,          -- anthropic | openai | gemini | elevenlabs | ...
    model       text,                          -- NULL = the adapter's own default
    base_url    text,                          -- a gateway; superadmin-only by construction
    api_key_enc text,                          -- sealed; NULL = the legacy key underneath applies
    settings    jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- e.g. {"voice_id": ...} for tts
    is_active   boolean     NOT NULL DEFAULT true,
    is_default  boolean     NOT NULL DEFAULT false,
    last_test   jsonb,                         -- {ok, at, detail} from the Test button
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text
);

-- ONE default per capability, enforced by the database and not only by the transaction that
-- clears the previous default (services/ai_registry.set_default): two operators racing the
-- button cannot leave two defaults behind.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_connections_default
    ON ai_connections(capability) WHERE is_default;
CREATE INDEX IF NOT EXISTS idx_ai_connections_capability
    ON ai_connections(capability, is_active);

COMMENT ON TABLE ai_connections IS
  'Superadmin-created, named provider connections. Deactivated, never deleted: llm_usage '
  'rows reference them by id so spend can be grouped by connection later.';
COMMENT ON COLUMN ai_connections.api_key_enc IS
  'The provider key, sealed by services/secrets.py. Never returned by any API response.';


-- Which connection a workspace runs on, per capability. Absent row = the capability''s
-- default connection. Deliberately NOT ON DELETE CASCADE on the connection: connections are
-- deactivated rather than deleted, and the resolver skips an inactive one (falling back to
-- the default), so a stale assignment is harmless and visible.
CREATE TABLE IF NOT EXISTS tenant_ai_assignments (
    client_id     uuid        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    capability    text        NOT NULL,
    connection_id uuid        NOT NULL REFERENCES ai_connections(id),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    text,
    PRIMARY KEY (client_id, capability)
);


-- The workspace's OWN provider settings, per capability — the successor of tenant_ai_configs
-- (which was LLM-only and whose `provider` column was stored but never acted on).
--
-- Written by two hands: the workspace owner from the portal (provider, model, key, settings —
-- NEVER base_url: an endpoint a tenant could set is an endpoint that can keep every transcript
-- it is handed) and the superadmin through /admin/ai-config/{tenant_id}, which may also set
-- base_url. `enabled=false` keeps a row without applying it.
CREATE TABLE IF NOT EXISTS tenant_ai_overrides (
    client_id   uuid        NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    capability  text        NOT NULL,
    provider    text        NOT NULL,
    model       text,
    api_key_enc text,                          -- sealed; NULL = model-only override, spend is ours
    settings    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    enabled     boolean     NOT NULL DEFAULT true,
    notes       text,
    base_url    text,                          -- superadmin-only (see above)
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text,
    PRIMARY KEY (client_id, capability)
);

COMMENT ON TABLE tenant_ai_overrides IS
  'Per-workspace, per-capability bring-your-own provider settings. Absent row, or '
  'enabled=false, means the workspace runs on its assigned or the default connection.';
COMMENT ON COLUMN tenant_ai_overrides.api_key_enc IS
  'The workspace''s OWN provider key, sealed. Never returned to any API response — only '
  'has_key and a masked hint. When set, the spend lands on their account (llm_usage.byo).';
COMMENT ON COLUMN tenant_ai_overrides.base_url IS
  'Superadmin-only. A tenant PUT that carries base_url is refused (400 base_url_not_allowed).';

-- The old LLM-only table stays in place (its rows are copied into tenant_ai_overrides as
-- capability=''llm'' by services/migrate.py, idempotently) so a rollback still finds its data.
COMMENT ON TABLE tenant_ai_configs IS
  'SUPERSEDED by tenant_ai_overrides (capability=llm). Copied on boot by migrate.py; no code '
  'reads or writes this table any more. Kept so a rollback still finds its data.';


-- Spend grouped by provider and connection, later. Nullable and unindexed on purpose: rows
-- written before this column carry NULL, which reads as "the legacy Anthropic key".
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS provider text;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS connection_id uuid;

COMMENT ON COLUMN llm_usage.provider IS
  'Which provider answered (anthropic | openai | gemini | ...). NULL on rows written before '
  'the registry existed, which all ran on Anthropic.';
COMMENT ON COLUMN llm_usage.connection_id IS
  'The ai_connections row whose key paid for the call, when one did. NULL for the legacy '
  'deployment key and for a call on the tenant''s own key (byo=true). Not a foreign key: '
  'usage history must outlive nothing here, but a hard-deleted connection must not take its '
  'history with it either.';
