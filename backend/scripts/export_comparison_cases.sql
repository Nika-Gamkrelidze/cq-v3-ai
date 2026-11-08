-- Read-only. Builds cases.jsonl for scripts/compare_scoring_models.py from ONE workspace.
--   docker compose -p cqv3 exec -T db psql -U cq -d cq -At \
--     -v ws="'<workspace client_id uuid>'" -f - < export_comparison_cases.sql > cases.jsonl
-- Picks Georgian recordings that already have a scorecard, recordings an owner CORRECTED first (those
-- corrections are the human ground truth the comparison measures against), then the newest.
-- Each line carries the rubric version the scorecard was made with, the stored model scores
-- ("reference") and, when there is one, the reviewer's scores ("human").
-- These are real customer calls: export a workspace you may use for this (your own / a test one),
-- keep the file off shared drives, and delete it when the comparison is done.
SELECT json_build_object(
  'id', j.id, 'language', j.language, 'transcript', j.transcript, 'segments', j.segments,
  'config', json_build_object('dimensions', sc.dimensions, 'rubric', sc.rubric, 'version', sc.version),
  'reference', (SELECT json_object_agg(d->>'key', (d->>'score')::int)
                FROM jsonb_array_elements(COALESCE(j.scoring->'dimensions', '[]'::jsonb)) d WHERE d->>'score' ~ '^[0-9]+$'),
  'human',     (SELECT json_object_agg(d->>'key', (d->>'score')::int)
                FROM jsonb_array_elements(COALESCE(r.scoring->'dimensions', '[]'::jsonb)) d WHERE d->>'score' ~ '^[0-9]+$')
)
FROM audio_jobs j
JOIN scoring_configs sc ON sc.client_id = j.client_id AND sc.version = NULLIF(j.scoring->>'version', '')::int
LEFT JOIN LATERAL (SELECT scoring FROM scoring_revisions
                   WHERE job_id = j.id AND edited_by IS NOT NULL ORDER BY revision DESC LIMIT 1) r ON true
WHERE j.client_id = :ws::uuid AND j.language = 'ka' AND length(j.transcript) > 400 AND j.scoring IS NOT NULL
ORDER BY (r.scoring IS NOT NULL) DESC, j.created_at DESC
LIMIT 30;
