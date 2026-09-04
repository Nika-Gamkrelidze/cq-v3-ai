-- CQ v3 AI — Call Workbench v2 (idempotent; applied on API startup, LAST in migrate.py).
--
-- Four things land together because they share one release: timestamped recordings that the
-- analysers highlight on a player, self-service registered users, a history of audio
-- conversions, and multi-call summaries. Everything here is ALTER ... IF NOT EXISTS /
-- CREATE ... IF NOT EXISTS so a redeploy re-runs it for free.
--
-- WHY `user_id` columns are plain uuids with no foreign key: deleting a registered user must
-- never cascade into usage records (quotas, retention, abuse investigation all read them).
-- The app filters by `user_id`; the database does not enforce the link on purpose.

-- recordings
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS segments   jsonb;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS duration_s real;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS source     text NOT NULL DEFAULT 'audio';
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS semantic   jsonb;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS user_id    uuid;
CREATE INDEX IF NOT EXISTS idx_audio_jobs_user ON audio_jobs (user_id, created_at DESC) WHERE user_id IS NOT NULL;

-- registered users
CREATE TABLE IF NOT EXISTS app_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL,
    password_hash text NOT NULL,
    display_name text,
    is_active boolean NOT NULL DEFAULT true,
    limits jsonb NOT NULL DEFAULT '{}'::jsonb,   -- per-user overrides of the registered tier
    created_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_users_email ON app_users (lower(email));
ALTER TABLE tts_requests ADD COLUMN IF NOT EXISTS user_id uuid;
CREATE INDEX IF NOT EXISTS idx_tts_requests_user ON tts_requests (user_id, created_at DESC) WHERE user_id IS NOT NULL;

-- conversions history (the bytes still live in the convert dir with their manifest TTL)
CREATE TABLE IF NOT EXISTS convert_batches (
    token text PRIMARY KEY,
    principal_type text NOT NULL,
    client_id uuid, user_id uuid, anon_key text,
    format text NOT NULL, file_count integer NOT NULL DEFAULT 0, total_bytes bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_convert_batches_user   ON convert_batches (user_id, created_at DESC) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_convert_batches_client ON convert_batches (client_id, created_at DESC) WHERE client_id IS NOT NULL;

-- summaries
CREATE TABLE IF NOT EXISTS call_summaries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_type text NOT NULL,
    client_id uuid REFERENCES clients(id) ON DELETE CASCADE,
    user_id uuid,
    job_ids uuid[] NOT NULL,
    language text,
    summary jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_call_summaries_client ON call_summaries (client_id, created_at DESC) WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_call_summaries_user   ON call_summaries (user_id, created_at DESC) WHERE user_id IS NOT NULL;

-- personal rubrics: scoring_configs learns a second owner kind
ALTER TABLE scoring_configs ALTER COLUMN client_id DROP NOT NULL;
ALTER TABLE scoring_configs ADD COLUMN IF NOT EXISTS user_id uuid;
ALTER TABLE scoring_configs ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE scoring_configs ADD COLUMN IF NOT EXISTS updated_by text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_scoring_active_user ON scoring_configs (user_id) WHERE is_active AND user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_scoring_user_version ON scoring_configs (user_id, version) WHERE user_id IS NOT NULL;
-- The existing UNIQUE (client_id, version) tolerates NULL client_id rows (NULLs are distinct).
