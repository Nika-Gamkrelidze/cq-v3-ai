-- Retention of anonymous (unregistered) submissions + acoustic sentiment.
--
-- Two things live here because they share a lifetime: what an anonymous visitor sent us, and
-- what we concluded about it. Both are personal data, both are purged by the same sweep.
--
-- WHY the audio is a path and not a bytea: recordings are megabytes and pg_dump would carry
-- every one of them into every backup forever. The bytes live on the `media` volume; the row
-- keeps the path, the size and the checksum, so a purge (or a subject-access request) can find
-- and delete both halves from one place.

-- ---------------------------------------------------------------------------
-- audio_jobs: who sent it, where the audio is, and how it sounded
-- ---------------------------------------------------------------------------
-- `anon_key` already held the IP, but only for anonymous callers and only as a quota key.
-- `client_ip` is the explicit record, set for every principal kind, and is the column the
-- purge and any abuse investigation actually read.
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS client_ip     text;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS audio_path    text;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS audio_bytes   integer;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS audio_sha256  text;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS sentiment     jsonb;
ALTER TABLE audio_jobs ADD COLUMN IF NOT EXISTS purge_after   timestamptz;

-- The purge scans by deadline, and only rows that still have something to delete.
CREATE INDEX IF NOT EXISTS idx_audio_jobs_purge
    ON audio_jobs (purge_after) WHERE purge_after IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audio_jobs_anon_ip
    ON audio_jobs (client_ip, created_at DESC) WHERE principal_type = 'anonymous';

-- ---------------------------------------------------------------------------
-- tts_requests: the text an anonymous visitor asked us to speak, and the clip we returned
-- ---------------------------------------------------------------------------
-- /tts streamed its audio straight back and kept nothing, so a TTS submission left no record
-- at all — no text, no IP, nothing to investigate abuse with. One row per synthesis now.
CREATE TABLE IF NOT EXISTS tts_requests (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       uuid REFERENCES clients(id) ON DELETE SET NULL,
    principal_type  text,
    anon_key        text,
    client_ip       text,
    text            text        NOT NULL,
    text_chars      integer     NOT NULL DEFAULT 0,
    language_code   text,
    voice_id        text,
    tts_model       text,
    audio_path      text,
    audio_bytes     integer,
    purge_after     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tts_requests_created ON tts_requests (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tts_requests_purge
    ON tts_requests (purge_after) WHERE purge_after IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tts_requests_anon_ip
    ON tts_requests (client_ip, created_at DESC) WHERE principal_type = 'anonymous';
