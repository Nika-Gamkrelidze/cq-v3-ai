-- Who made a recording, as a name.
--
-- Every tenant user already sees the whole workspace's recordings (the scope is client_id), so
-- a shared History was always a list of things with no author. This is the missing half: the
-- person's name, captured AT CREATION rather than joined at read time, so the record still
-- says who ran a call after that account is renamed or removed — which is exactly when an
-- audit trail is worth having.
ALTER TABLE audio_jobs     ADD COLUMN IF NOT EXISTS created_by text;
ALTER TABLE call_summaries ADD COLUMN IF NOT EXISTS created_by text;
