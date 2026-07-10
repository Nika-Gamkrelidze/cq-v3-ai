-- CQ v3 AI — multi-tenant + knowledge base additions (idempotent; applied on API startup).
-- Builds on the existing clients / kb_documents / kb_chunks tables from schema.sql.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- keyword fallback for retrieval

-- ---- Tenants (clients) ----------------------------------------------------
ALTER TABLE clients ADD COLUMN IF NOT EXISTS api_key text;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS settings jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE UNIQUE INDEX IF NOT EXISTS uq_clients_api_key ON clients(api_key) WHERE api_key IS NOT NULL;

-- ---- Per-tenant human login accounts --------------------------------------
CREATE TABLE IF NOT EXISTS tenant_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    username text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    role text NOT NULL DEFAULT 'member',      -- member | owner
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tenant_users_client ON tenant_users(client_id);

-- ---- Tenant-definable KB categories (optional, for UI dropdowns) -----------
CREATE TABLE IF NOT EXISTS kb_categories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name text NOT NULL,
    description text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, name)
);

-- ---- kb_documents: flexible metadata + ingestion bookkeeping ---------------
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS tags text[] NOT NULL DEFAULT '{}';
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS content_text text;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS char_count integer;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS chunk_count integer NOT NULL DEFAULT 0;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS error text;
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS source_type text;   -- file | paste | csv | api
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS actor text;         -- who imported it
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS ingest_ms integer;  -- ingestion duration
ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS checksum text;      -- md5(content_text) for dedupe
CREATE INDEX IF NOT EXISTS idx_kb_documents_checksum ON kb_documents(client_id, checksum);

-- ---- kb_chunks: chunk bookkeeping (embedding dim handled in Python startup) -
ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS chunk_index integer;
ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS token_count integer;
CREATE INDEX IF NOT EXISTS idx_kb_chunks_content_trgm ON kb_chunks USING gin (content gin_trgm_ops);

-- ---- audio_jobs: tenant scoping + RAG provenance --------------------------
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS client_id uuid REFERENCES clients(id) ON DELETE SET NULL;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS principal_type text;   -- superadmin | tenant | anonymous
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS anon_key text;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS kb_used jsonb;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS kb_check jsonb;   -- KB fact-check result
CREATE INDEX IF NOT EXISTS idx_audio_jobs_client ON audio_jobs(client_id);

-- ---- KB activity log: ingestion history + change audit --------------------
CREATE TABLE IF NOT EXISTS kb_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid REFERENCES clients(id) ON DELETE CASCADE,
    document_id uuid,          -- no FK: audit rows survive document deletion
    action text NOT NULL,      -- import | edit | delete | reembed | chunk_edit | chunk_delete | bulk | export
    method text,               -- file | paste | csv | api
    status text,               -- pending | processing | ready | error | ok
    detail text,
    actor text,
    chunk_count integer,
    duration_ms integer,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_events_client ON kb_events(client_id, created_at DESC);

-- ---- Anonymous usage counters (per key per day) ---------------------------
CREATE TABLE IF NOT EXISTS anon_usage (
    anon_key text NOT NULL,
    day date NOT NULL,
    analyses integer NOT NULL DEFAULT 0,
    tts integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (anon_key, day)
);
