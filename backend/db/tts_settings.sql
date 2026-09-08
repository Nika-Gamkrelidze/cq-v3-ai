-- Advanced text-to-speech controls: what ElevenLabs was actually sent.
--
-- WHY a column and not a note in `tts_model`: a customer who says "this clip sounds wrong"
-- is describing the result of text + voice + model + settings, and until now the row kept
-- the first three. The settings written here are the SHAPED ones — after the server dropped
-- what the model does not take and snapped stability to a v3 preset — so replaying the row
-- reproduces the clip exactly, and support can see whether a style slider the customer swears
-- they moved ever reached the model. NULL means the voice's own defaults were used, which is
-- also what every row written before this column existed means.
ALTER TABLE tts_requests ADD COLUMN IF NOT EXISTS voice_settings jsonb;
