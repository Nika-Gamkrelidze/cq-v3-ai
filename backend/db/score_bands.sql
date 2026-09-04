-- Where the colours change on a scorecard, per workspace.
--
-- Three bands need only TWO boundaries, so that is what is stored: below `amber_from` is red,
-- up to `green_from` is amber, at or above it is green. Storing ranges instead would let an
-- operator save a gap (or an overlap) that no score falls into, and there is no sensible colour
-- for a score nobody can reach.
--
-- Deliberately NOT part of scoring_configs: a rubric is versioned and "reset rubric to default"
-- replaces it, while the colour thresholds are a display preference that must survive that reset
-- and be reset on their own.
CREATE TABLE IF NOT EXISTS score_bands (
    owner_key  text PRIMARY KEY,               -- 'tenant:<uuid>' | 'user:<uuid>'
    amber_from integer NOT NULL DEFAULT 50,
    green_from integer NOT NULL DEFAULT 80,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text,
    CONSTRAINT score_bands_order CHECK (amber_from > 0 AND green_from > amber_from AND green_from <= 100)
);
