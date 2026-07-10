-- CQ v3 AI — self-serve audio analyzer tables (idempotent; applied on API startup).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Runtime configuration edited from the admin panel (API keys, model choices, etc.).
-- One row per key; values are JSONB so a single 'integrations' row holds the config blob.
CREATE TABLE IF NOT EXISTS app_settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- One row per uploaded audio file that goes through the transcribe + analyze pipeline.
CREATE TABLE IF NOT EXISTS audio_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    filename text,
    content_type text,
    size_bytes integer,
    status text NOT NULL DEFAULT 'pending',   -- pending | transcribing | analyzing | done | error
    language text,
    transcript text,
    analysis jsonb,
    stt_model text,
    llm_model text,
    error text,
    processing_ms integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audio_jobs_created_at ON audio_jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audio_jobs_status ON audio_jobs(status);
