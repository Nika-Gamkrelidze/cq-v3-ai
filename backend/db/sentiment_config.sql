-- Per-tenant sentiment configuration: on/off + free-text guidance for the LLM half of
-- standalone sentiment analysis (POST /sentiment, POST /admin/sentiment/{tenant_id}).
--
-- Deliberately NOT the scoring_configs pattern (versioned, one-active-of-many, audit trail):
-- a rubric determines a paid evaluation outcome and needs history; this is a much lower-stakes
-- toggle + a paragraph of steering text, so one row per tenant with a plain UPSERT is enough.
-- The public app's equivalent config is a superadmin-only, global row and lives in
-- app_settings (see settings_store.PUBLIC_SENTIMENT_KEY) — there is no "tenant" to key it by.
CREATE TABLE IF NOT EXISTS sentiment_configs (
    client_id  uuid PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    enabled    boolean     NOT NULL DEFAULT true,
    guidance   text        NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text
);
