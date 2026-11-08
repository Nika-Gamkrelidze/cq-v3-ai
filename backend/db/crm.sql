-- CQ v3 AI — the CRM analyser: per-tenant CRM forms and the forms filled from calls.
-- Idempotent, like every migration here: CREATE ... IF NOT EXISTS, ADD COLUMN IF NOT EXISTS.

-- A tenant's CRM forms. Versioned like scoring_configs: a save writes a NEW row and flips
-- `is_active`, so a filled form can always say which version of its form it was filled
-- against. A tenant may keep as many forms as it likes (one per line of business, say);
-- `form_key` is the stable name across versions and the one an integration maps on.
-- A tenant with NO row here fills the default form (app_settings['default_crm_form'], else
-- the built-in one) — nothing is copied for them until they save.
CREATE TABLE IF NOT EXISTS crm_forms (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id   uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    form_key    text NOT NULL,
    version     integer NOT NULL,
    name        text NOT NULL,
    definition  jsonb NOT NULL,
    is_active   boolean NOT NULL DEFAULT false,
    -- The form "Analyse a call" opens with. At most one per tenant among the active rows.
    is_primary  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text,
    UNIQUE (client_id, form_key, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_forms_active
    ON crm_forms(client_id, form_key) WHERE is_active;
CREATE UNIQUE INDEX IF NOT EXISTS uq_crm_forms_primary
    ON crm_forms(client_id) WHERE is_active AND is_primary;

COMMENT ON TABLE crm_forms IS
  'Per-tenant CRM form definitions, one row per saved version; exactly one active row per '
  '(client_id, form_key). A deleted form is a key with no active row — its versions stay, '
  'because the filled forms that point at them must stay readable.';

-- One filled form per run. A re-run writes a new row (the latest per form is what the call
-- shows), so a reviewer's approved values are never overwritten by a second click.
CREATE TABLE IF NOT EXISTS crm_results (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    job_id        uuid REFERENCES audio_jobs(id) ON DELETE CASCADE,
    form_key      text NOT NULL,
    form_version  integer NOT NULL,
    form_name     text,
    -- The option key the form's label field ended on (reviewed value when there is one), so a
    -- list can show and filter by "what kind of call was this" without opening the jsonb.
    label         text,
    -- What the model and the code produced: every field with status, value and evidence.
    result        jsonb NOT NULL,
    -- The reviewer's values {field key: value}; NULL until somebody saves a review.
    final         jsonb,
    status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    text,
    reviewed_at   timestamptz,
    reviewed_by   text
);
CREATE INDEX IF NOT EXISTS idx_crm_results_job
    ON crm_results(client_id, job_id, form_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_results_client
    ON crm_results(client_id, created_at DESC);
