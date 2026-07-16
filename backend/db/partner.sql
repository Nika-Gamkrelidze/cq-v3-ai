-- CQ v3 AI — B2B partner API additions (idempotent; applied on API startup).
-- Async single + bulk audio analysis on top of the existing audio_jobs table.

ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS batch_id uuid;          -- groups a bulk submit
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS external_ref text;      -- partner's own id (idempotency)

CREATE INDEX IF NOT EXISTS idx_audio_jobs_batch ON audio_jobs(batch_id) WHERE batch_id IS NOT NULL;
-- One job per (tenant, external_ref): a retried submit returns the existing job, never double-runs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_audio_jobs_extref
    ON audio_jobs(client_id, external_ref) WHERE external_ref IS NOT NULL;
