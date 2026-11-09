-- What an AI call cost us, and what we bill for it.
--
-- `llm_usage` counts what each call consumed (tokens, audio seconds, characters). This file
-- adds the two things needed to turn that into money, and stores the result on each call:
--
--   ai_prices   — the operator's price list, per provider + model (+ optionally kind), in
--                 USD. VERSIONED: a price change is a new row with a later `effective_from`,
--                 never an edit of the old one, so a call is always costed at the price that
--                 was in force WHEN IT RAN. A price entered late can be back-dated, which is
--                 how a call made before anyone set a price gets one.
--   ai_markups  — the percentage added on top of cost when billing. One default row per
--                 period (client_id NULL) and optional per-workspace overrides, versioned the
--                 same way, so changing the markup next month does not re-price last month.
--   usage_price(llm_usage) — THE costing rule, in one place: which price and markup were in
--                 force for this call, and what it therefore cost and bills.
--   llm_usage.cost_usd / billed_usd / markup_pct / price_id — that answer, STORED. A trigger
--                 fills them as each call is recorded, and `services/pricing.py` re-runs
--                 usage_price over exactly the calls a price or markup change can affect, in
--                 the same transaction as the change. Computing it on every read instead (the
--                 first version, a view) made the overview 40 s slower at 200k calls.
--
-- Idempotent, like every migration here. Keep this file LAST in migrate.py: usage_price takes
-- the llm_usage row type, so every column it reads must exist before it is created.

CREATE TABLE IF NOT EXISTS ai_prices (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider             text        NOT NULL,
    -- An exact model id, or '*' for every model of the provider that has no row of its own.
    model                text        NOT NULL,
    -- llm | stt | tts | voice_tone, or NULL for any. The same Gemini model bills audio input
    -- (speech-to-text) at a different rate from text input, which is what this is for.
    capability           text,
    effective_from       timestamptz NOT NULL DEFAULT now(),
    -- USD. Tokens per MILLION, characters per THOUSAND, audio per HOUR — the units the
    -- providers' own price pages use, so a price is typed in as read. NULL = this unit is not
    -- billed by this provider; the cache prices fall back to the input price when NULL.
    input_per_mtok       numeric(14,6),
    output_per_mtok      numeric(14,6),
    cache_write_per_mtok numeric(14,6),
    cache_read_per_mtok  numeric(14,6),
    audio_per_hour       numeric(14,6),
    chars_per_k          numeric(14,6),
    note                 text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    created_by           text,
    updated_at           timestamptz,
    updated_by           text
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_prices_version
  ON ai_prices (provider, model, COALESCE(capability, ''), effective_from);
CREATE INDEX IF NOT EXISTS idx_ai_prices_lookup
  ON ai_prices (provider, model, effective_from DESC);

COMMENT ON TABLE ai_prices IS
  'Operator price list (USD), versioned by effective_from. A call is costed at the newest row '
  'in force at its created_at, preferring an exact model over ''*'' and an exact capability '
  'over NULL. See db/pricing.sql.';

CREATE TABLE IF NOT EXISTS ai_markups (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL = the default for every workspace without an override of its own.
    client_id      uuid REFERENCES clients(id) ON DELETE CASCADE,
    -- NULL only on a workspace row: "follow the default again from effective_from".
    pct            numeric(8,3) CHECK (pct >= 0),
    effective_from timestamptz NOT NULL DEFAULT now(),
    note           text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    created_by     text,
    updated_at     timestamptz,
    updated_by     text,
    CHECK (client_id IS NOT NULL OR pct IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_markups_version
  ON ai_markups (COALESCE(client_id, '00000000-0000-0000-0000-000000000000'::uuid), effective_from);

COMMENT ON TABLE ai_markups IS
  'Billing markup over cost, in percent, versioned by effective_from. A workspace override '
  'beats the default (client_id NULL) for calls made while it is in force.';


-- The view of the first version, if a database still has it.
DROP VIEW IF EXISTS llm_usage_priced;

ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS price_id   uuid;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS cost_usd   numeric(20,8);
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS markup_pct numeric(8,3);
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS billed_usd numeric(20,8);
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS priced_at  timestamptz;

COMMENT ON COLUMN llm_usage.cost_usd IS
  'USD at the ai_prices row in force at created_at (usage_price). NULL = no price for what the '
  'call consumed: unknown, never free. 0 for the workspace''s own key and for local models.';
COMMENT ON COLUMN llm_usage.billed_usd IS 'cost_usd * (1 + markup_pct/100): the amount to bill.';
COMMENT ON COLUMN llm_usage.priced_at IS
  'When cost was last computed. NULL = never (rows older than pricing), filled on boot.';

-- A one-row TABLE function in plain SQL, so the planner inlines it into a set-based UPDATE
-- (2.6x faster over 200k calls than the same body with OUT parameters). Dropped first because
-- CREATE OR REPLACE cannot change a return type; the whole file runs in one transaction.
DROP FUNCTION IF EXISTS usage_price(llm_usage);
CREATE FUNCTION usage_price(u llm_usage)
RETURNS TABLE (price_id uuid, cost_usd numeric, markup_pct numeric, billed_usd numeric)
LANGUAGE sql STABLE AS $fn$
  WITH k AS (
      -- The provider exactly as the reports label it (usage.provider_sql): a NULL provider on
      -- a text call predates the registry, when everything ran on Anthropic.
      SELECT COALESCE(u.provider, CASE WHEN COALESCE(u.capability, 'llm') = 'llm'
                                       THEN 'anthropic' ELSE 'unknown' END) AS provider,
             COALESCE(u.capability, 'llm') AS capability,
             COALESCE(u.input_tokens, 0) + COALESCE(u.output_tokens, 0)
               + COALESCE(u.cache_read_tokens, 0) + COALESCE(u.cache_creation_tokens, 0) AS tokens,
             COALESCE(u.audio_seconds, 0) AS secs,
             COALESCE(u.characters, 0) AS chars
  ), p AS (
      -- Exact model over '*', a named kind over "any kind", then the newest version in force.
      SELECT ap.* FROM ai_prices ap, k
       WHERE ap.provider = k.provider
         AND (ap.model = u.model OR ap.model = '*')
         AND (ap.capability IS NULL OR ap.capability = k.capability)
         AND ap.effective_from <= u.created_at
       ORDER BY (ap.model = u.model) DESC, (ap.capability IS NOT NULL) DESC, ap.effective_from DESC
       LIMIT 1
  ), c AS (
      SELECT p.id AS price_id,
             CASE
               -- On the workspace's own key the provider billed them, not us.
               WHEN u.byo THEN 0::numeric
               -- Runs on our own server (the voice-tone sidecar): no per-call charge.
               WHEN k.provider = 'local' THEN 0::numeric
               -- No price in force: unknown, never a silent zero.
               WHEN p.id IS NULL THEN NULL::numeric
               -- A price in force that bills none of the units this call consumed (an audio-
               -- only transcription against a tokens-only row) is just as unknown. A call that
               -- consumed nothing at all (most failures) is priced, at zero.
               WHEN NOT (
                      (k.tokens > 0 AND COALESCE(p.input_per_mtok, p.output_per_mtok,
                                                 p.cache_write_per_mtok, p.cache_read_per_mtok) IS NOT NULL)
                   OR (k.secs > 0 AND p.audio_per_hour IS NOT NULL)
                   OR (k.chars > 0 AND p.chars_per_k IS NOT NULL)
                   OR (k.tokens + k.secs + k.chars = 0)
                    ) THEN NULL::numeric
               ELSE ( COALESCE(u.input_tokens, 0)  * COALESCE(p.input_per_mtok, 0)
                    + COALESCE(u.output_tokens, 0) * COALESCE(p.output_per_mtok, 0)
                    + COALESCE(u.cache_creation_tokens, 0)
                        * COALESCE(p.cache_write_per_mtok, p.input_per_mtok, 0)
                    + COALESCE(u.cache_read_tokens, 0)
                        * COALESCE(p.cache_read_per_mtok, p.input_per_mtok, 0)
                    ) / 1000000
                    + k.secs::numeric  * COALESCE(p.audio_per_hour, 0) / 3600
                    + k.chars::numeric * COALESCE(p.chars_per_k, 0) / 1000
             END AS cost_usd,
             -- The workspace's own markup in force, else the default in force. An override whose
             -- pct is NULL means "back to the default from this date", so ending one never needs
             -- deleting the history that priced the months it covered.
             COALESCE(
               (SELECT am.pct FROM ai_markups am
                 WHERE am.client_id = u.client_id AND am.effective_from <= u.created_at
                 ORDER BY am.effective_from DESC LIMIT 1),
               (SELECT am.pct FROM ai_markups am
                 WHERE am.client_id IS NULL AND am.effective_from <= u.created_at
                 ORDER BY am.effective_from DESC LIMIT 1)) AS markup_pct
        FROM k LEFT JOIN p ON true
  )
  SELECT c.price_id, c.cost_usd, c.markup_pct,
         c.cost_usd * (1 + COALESCE(c.markup_pct, 0) / 100)
    FROM c
$fn$;

CREATE OR REPLACE FUNCTION llm_usage_price_trg() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE r record;
BEGIN
  BEGIN
    SELECT * INTO r FROM usage_price(NEW);
    NEW.price_id   := r.price_id;
    NEW.cost_usd   := r.cost_usd;
    NEW.markup_pct := r.markup_pct;
    NEW.billed_usd := r.billed_usd;
    NEW.priced_at  := now();
  EXCEPTION WHEN OTHERS THEN
    -- Costing must never cost us the usage row itself: record the call uncosted (priced_at
    -- NULL), which the next boot's catch-up below costs, and say why in the server log.
    RAISE WARNING 'usage_price failed for llm_usage %: %', NEW.id, SQLERRM;
    NEW.price_id := NULL; NEW.cost_usd := NULL; NEW.markup_pct := NULL;
    NEW.billed_usd := NULL; NEW.priced_at := NULL;
  END;
  RETURN NEW;
END
$fn$;

-- On insert, and on any change to what the cost depends on (never on the cost columns
-- themselves, which is what a re-price writes).
DROP TRIGGER IF EXISTS trg_llm_usage_price ON llm_usage;
CREATE TRIGGER trg_llm_usage_price
  BEFORE INSERT OR UPDATE OF input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                             audio_seconds, characters, provider, model, capability, client_id,
                             created_at, byo
  ON llm_usage FOR EACH ROW EXECUTE FUNCTION llm_usage_price_trg();

-- Calls recorded before pricing existed: costed once (zero for own-key and local calls, NULL
-- for the rest until prices are entered). A no-op on every later boot.
UPDATE llm_usage u
   SET price_id = q.price_id, cost_usd = q.cost_usd, markup_pct = q.markup_pct,
       billed_usd = q.billed_usd, priced_at = now()
  FROM (SELECT x.id, p.* FROM llm_usage x CROSS JOIN LATERAL usage_price(x) p
         WHERE x.priced_at IS NULL) q
 WHERE u.id = q.id;
