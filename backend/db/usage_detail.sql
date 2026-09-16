-- AI usage detailed enough to answer the questions an operator actually asks about one call.
--
-- `llm_usage` already said which tenant, which feature, which model and which recording. What
-- it could not say: that a recording's TRANSCRIPTION cost anything at all (speech-to-text went
-- around the text seam and was never metered), which chat conversation and which customer
-- question a bot answer belonged to, and which summary a multi-call digest produced. One row
-- still means one call; these columns only say more about it.
--
-- Idempotent, like every migration here: ADD COLUMN IF NOT EXISTS, CREATE IF NOT EXISTS.
-- None of the new columns is a foreign key, for the reason job_id is not one: usage history
-- must outlive the recording, the conversation and the summary, all of which get deleted.

ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS capability text;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS conversation_id uuid;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS turn_id uuid;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS suggest_ref text;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS summary_id uuid;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS audio_seconds double precision;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS characters integer;

COMMENT ON COLUMN llm_usage.capability IS
  'llm | stt | tts | voice_tone. NULL on rows written before this column, all of which were '
  'text-model calls (llm): speech was not metered until then.';
COMMENT ON COLUMN llm_usage.conversation_id IS
  'chat_conversations.id for a bot or copilot call. Not a FK: a purged conversation keeps its cost.';
COMMENT ON COLUMN llm_usage.turn_id IS
  'chat_turns.id of the message that CAUSED the call (the customer question for autopilot, '
  'triage and handoff). Joined to show which question cost what.';
COMMENT ON COLUMN llm_usage.suggest_ref IS
  'copilot_suggestions.suggest_ref of the generation, which tells a regeneration apart from '
  'the original answer to the same turn.';
COMMENT ON COLUMN llm_usage.summary_id IS
  'call_summaries.id of the summary this call produced. A summary of several recordings '
  'belongs to no single job_id, so this is the only link its tokens have.';
COMMENT ON COLUMN llm_usage.audio_seconds IS
  'Seconds of audio processed, for speech providers that bill by duration rather than tokens '
  '(ElevenLabs Scribe reports no tokens at all). NULL when the length was not known.';
COMMENT ON COLUMN llm_usage.characters IS
  'Characters synthesised, for text-to-speech, which bills by characters.';

-- The usage page's lists: newest calls across every tenant, one conversation, one summary.
CREATE INDEX IF NOT EXISTS idx_llm_usage_created ON llm_usage(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_conversation
  ON llm_usage(conversation_id, created_at) WHERE conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_llm_usage_summary
  ON llm_usage(summary_id) WHERE summary_id IS NOT NULL;
