-- CQ v3 AI — per-tenant scoring rubric additions (idempotent; applied on API startup).
-- Builds on `scoring_configs` (from schema.sql) and `audio_jobs` (analyzer.sql/kb.sql).

-- scoring_configs already exists (client_id, version, dimensions jsonb, weights jsonb,
-- rubric text, is_active). Ensure the columns/index we rely on are present even on
-- older volumes, and guarantee at most one active config per client.
ALTER TABLE scoring_configs ADD COLUMN IF NOT EXISTS dimensions jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE scoring_configs ADD COLUMN IF NOT EXISTS weights jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE scoring_configs ADD COLUMN IF NOT EXISTS rubric text;
ALTER TABLE scoring_configs ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT false;
ALTER TABLE scoring_configs ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE scoring_configs ADD COLUMN IF NOT EXISTS updated_by text;

-- One active config per tenant (the pipeline always scores against the active one).
CREATE UNIQUE INDEX IF NOT EXISTS uq_scoring_active ON scoring_configs(client_id) WHERE is_active;

-- Per-call rubric scoring result (mirrors the kb_check jsonb pattern).
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS scoring jsonb;
