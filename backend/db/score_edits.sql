-- Manual score edits, with the AI's own result kept intact underneath.
--
-- A reviewer overriding a machine score is a judgement about a person's work, so it has to be
-- auditable: WHO changed it, WHEN, from WHAT, and why. Revision 1 is always the model's own
-- output and is never rewritten — every edit is a new row, so the original survives no matter
-- how many times a scorecard is adjusted. `audio_jobs.scoring` keeps holding the CURRENT
-- result (nothing reading it needs to know about this table), and this is the history behind it.
CREATE TABLE IF NOT EXISTS scoring_revisions (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id     uuid NOT NULL REFERENCES audio_jobs(id) ON DELETE CASCADE,
    revision   integer NOT NULL,        -- 1 = the model's own scoring, immutable
    scoring    jsonb   NOT NULL,        -- the whole scorecard as it stood at this revision
    edited_by  text,                    -- NULL on revision 1: no person produced it
    note       text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_scoring_revisions_job ON scoring_revisions (job_id, revision);
