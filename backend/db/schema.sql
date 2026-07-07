-- CQ v3 AI — database schema (PostgreSQL 14+), multi-tenant call analysis.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS clients (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE, name text NOT NULL,
    industry text, region text,
    data_tier text NOT NULL DEFAULT 'standard',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS operators (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    external_ref text, name text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, external_ref)
);
CREATE INDEX IF NOT EXISTS idx_operators_client ON operators(client_id);
CREATE TABLE IF NOT EXISTS scoring_configs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    version integer NOT NULL,
    dimensions jsonb NOT NULL DEFAULT '[]'::jsonb,
    weights jsonb NOT NULL DEFAULT '{}'::jsonb,
    rubric text, is_active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_config_per_client
    ON scoring_configs(client_id) WHERE is_active;
CREATE TABLE IF NOT EXISTS calls (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    operator_id uuid REFERENCES operators(id) ON DELETE SET NULL,
    external_ref text NOT NULL, audio_uri text NOT NULL,
    language text, duration_sec integer, recorded_at timestamptz,
    status text NOT NULL DEFAULT 'pending', error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, external_ref)
);
CREATE INDEX IF NOT EXISTS idx_calls_client_status ON calls(client_id, status);
CREATE INDEX IF NOT EXISTS idx_calls_recorded_at ON calls(recorded_at);
CREATE TABLE IF NOT EXISTS transcripts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id uuid NOT NULL UNIQUE REFERENCES calls(id) ON DELETE CASCADE,
    provider text, language text,
    segments jsonb NOT NULL DEFAULT '[]'::jsonb, full_text text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS analyses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id uuid NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    scoring_config_id uuid NOT NULL REFERENCES scoring_configs(id),
    model text NOT NULL, prompt_version text, category text,
    sentiment jsonb NOT NULL DEFAULT '{}'::jsonb,
    dimensions jsonb NOT NULL DEFAULT '{}'::jsonb,
    flags jsonb NOT NULL DEFAULT '{}'::jsonb,
    weighted_total numeric(6,2),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_analyses_call ON analyses(call_id);
CREATE INDEX IF NOT EXISTS idx_analyses_config ON analyses(scoring_config_id);
CREATE TABLE IF NOT EXISTS kb_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    doc_type text NOT NULL, title text, source_uri text,
    status text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_client ON kb_documents(client_id);
-- vector dimension MUST match your embedding model (default 1536).
CREATE TABLE IF NOT EXISTS kb_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    content text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_client ON kb_chunks(client_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding
    ON kb_chunks USING hnsw (embedding vector_cosine_ops);
