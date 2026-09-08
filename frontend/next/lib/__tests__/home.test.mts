import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ADV_DEFAULTS, advBody, advCaps, advEffectiveModel, advLoad, advReset,
  type AdvState, type ModelCaps,
} from '../../app/home/ttsAdvanced.ts';
import { conversionAllowance, quotaView } from '../../app/home/quota.ts';

/* The public page's two pieces of pure logic.
   ==========================================
   Both are things a browser cannot show you going wrong.

   `ttsAdvanced` decides what ends up in a `POST /tts` body. A value that is out of range, or
   that belongs to a different model, produces a 422 whose cause is a number in localStorage —
   invisible from the page, and unreachable without clearing site data.

   `quota` decides which of four sentences the allowance banner shows, and one of them is new:
   `GET /limits` now answers `enabled:false, visitor_identified:false` when the deployment
   cannot tell one anonymous visitor from another, with every `remaining` count zeroed. Reading
   those zeroes as usage is the specific lie this port exists to stop telling. */

/* --------------------------------------------------------- advLoad: sanitising the blob */

test('advLoad: nothing stored is the defaults', () => {
  assert.deepEqual(advLoad(null), ADV_DEFAULTS);
  assert.deepEqual(advLoad(''), ADV_DEFAULTS);
});

test('advLoad: a blob that is not an object is the defaults', () => {
  assert.deepEqual(advLoad('not json at all'), ADV_DEFAULTS);
  assert.deepEqual(advLoad('42'), ADV_DEFAULTS);
  assert.deepEqual(advLoad('"a string"'), ADV_DEFAULTS);
});

test('advLoad: out-of-range numbers fall back per field, they do not clamp', () => {
  // Clamping would silently honour a value nobody chose; the default is the honest answer.
  const s = advLoad(JSON.stringify({ stability: 900, similarity: -3, style: 1e9, speed: 4 }));
  assert.equal(s.stability, 50);
  assert.equal(s.similarity, 75);
  assert.equal(s.style, 0);
  assert.equal(s.speed, 1);
});

test('advLoad: in-range numbers survive, including the edges', () => {
  const s = advLoad(JSON.stringify({ stability: 0, similarity: 100, style: 37, speed: 0.7 }));
  assert.equal(s.stability, 0);
  assert.equal(s.similarity, 100);
  assert.equal(s.style, 37);
  assert.equal(s.speed, 0.7);
});

test('advLoad: the v3 preset is SNAPPED to 0 / 0.5 / 1', () => {
  // ElevenLabs rejects any other value for v3, so a slider position left over from another
  // model has to land on one of the three rather than be sent as-is.
  assert.equal(advLoad(JSON.stringify({ preset: 0.1 })).preset, 0);
  assert.equal(advLoad(JSON.stringify({ preset: 0.4 })).preset, 0.5);
  assert.equal(advLoad(JSON.stringify({ preset: 0.9 })).preset, 1);
  assert.equal(advLoad(JSON.stringify({ preset: 'x' })).preset, 0.5);
});

test('advLoad: the two checkboxes default ON and only an explicit false turns them off', () => {
  assert.equal(advLoad(JSON.stringify({})).boost, true);
  assert.equal(advLoad(JSON.stringify({ boost: false })).boost, false);
  assert.equal(advLoad(JSON.stringify({ boost: 'nonsense' })).boost, true);
  assert.equal(advLoad(JSON.stringify({ forcelang: false })).forcelang, false);
});

test('advLoad: a non-string model is dropped', () => {
  assert.equal(advLoad(JSON.stringify({ model: 17 })).model, '');
  assert.equal(advLoad(JSON.stringify({ model: 'eleven_v3' })).model, 'eleven_v3');
});

/* ------------------------------------------------------ advEffectiveModel: what Auto means */

test('advEffectiveModel: an explicit pick always wins', () => {
  assert.equal(advEffectiveModel('eleven_flash_v2_5', 'ka', { ka: 'eleven_v3' }), 'eleven_flash_v2_5');
});

test('advEffectiveModel: Auto follows the API answer for the language', () => {
  assert.equal(advEffectiveModel('', 'ka', { ka: 'eleven_v3' }), 'eleven_v3');
  assert.equal(advEffectiveModel('', 'en', { en: 'eleven_multilingual_v2' }), 'eleven_multilingual_v2');
});

test('advEffectiveModel: with no API answer, Georgian still resolves to v3', () => {
  // The hardcoded pair only covers a /languages that predates the `model` field, and it must
  // keep the Georgian path on v3 — the whole reason Georgian sounds Georgian.
  assert.equal(advEffectiveModel('', 'ka', {}), 'eleven_v3');
  assert.equal(advEffectiveModel('', 'en', {}), 'eleven_multilingual_v2');
});

/* ------------------------------------------------------------ advCaps: which controls exist */

test('advCaps: the model list is believed when it describes the model', () => {
  const supports: ModelCaps = { presets: false, style: false, speaker_boost: false, speed: true, language_code: 'enforced' };
  assert.deepEqual(advCaps('eleven_flash_v2_5', [{ model_id: 'eleven_flash_v2_5', supports }]), supports);
});

test('advCaps: an unlisted v3 id falls back to the v3 family shape', () => {
  const caps = advCaps('eleven_v3_preview', []);
  assert.equal(caps.presets, true);
  assert.equal(caps.style, false);
  assert.equal(caps.speed, false);
  assert.equal(caps.language_code, 'rejected');
});

test('advCaps: anything else falls back to the multilingual shape', () => {
  const caps = advCaps('eleven_multilingual_v2', []);
  assert.equal(caps.presets, false);
  assert.equal(caps.style, true);
  assert.equal(caps.speed, true);
});

/* ------------------------------------------------------- advBody: what reaches the API */

const V3: ModelCaps = { presets: true, style: false, speaker_boost: true, speed: false, language_code: 'rejected' };
const ML: ModelCaps = { presets: false, style: true, speaker_boost: true, speed: true, language_code: 'ignored' };
const FLASH: ModelCaps = { presets: false, style: true, speaker_boost: true, speed: true, language_code: 'enforced' };

const state = (over: Partial<AdvState> = {}): AdvState => ({ ...ADV_DEFAULTS, ...over });

test('advBody: Customise off sends NOTHING the panel owns', () => {
  // The default clip has to stay byte-for-byte the request this page made before the panel
  // existed — a voice speaks with the settings its creator tuned until someone says otherwise.
  assert.deepEqual(advBody(state({ custom: false }), ML), {});
  assert.deepEqual(advBody(state({ custom: false, model: 'eleven_v3' }), V3), { model_id: 'eleven_v3' });
});

test('advBody: percentages become 0..1', () => {
  const body = advBody(state({ custom: true, stability: 40, similarity: 90, style: 20 }), ML);
  assert.equal(body.voice_settings?.stability, 0.4);
  assert.equal(body.voice_settings?.similarity_boost, 0.9);
  assert.equal(body.voice_settings?.style, 0.2);
});

test('advBody: v3 sends the PRESET as stability and no style or speed at all', () => {
  const body = advBody(state({ custom: true, preset: 1, stability: 33, style: 80, speed: 1.2 }), V3);
  assert.equal(body.voice_settings?.stability, 1);
  assert.equal('style' in (body.voice_settings ?? {}), false);
  assert.equal('speed' in (body.voice_settings ?? {}), false);
});

test('advBody: a value remembered for another model is never sent to one that rejects it', () => {
  // The regression this guards: a speed set while Multilingual v2 was selected, still in
  // localStorage when the visitor switches to v3, which 422s on the field.
  const remembered = state({ custom: true, speed: 0.8, style: 55 });
  assert.equal(advBody(remembered, ML).voice_settings?.speed, 0.8);
  assert.equal(advBody(remembered, V3).voice_settings?.speed, undefined);
});

test('advBody: speed is rounded to two places', () => {
  assert.equal(advBody(state({ custom: true, speed: 1.15 }), ML).voice_settings?.speed, 1.15);
  assert.equal(advBody(state({ custom: true, speed: 0.7000001 }), ML).voice_settings?.speed, 0.7);
});

test('advBody: enforce_language rides along only for the family that honours it', () => {
  assert.equal(advBody(state({ custom: true }), FLASH).enforce_language, true);
  assert.equal(advBody(state({ custom: true, forcelang: false }), FLASH).enforce_language, false);
  assert.equal('enforce_language' in advBody(state({ custom: true }), ML), false);
  assert.equal('enforce_language' in advBody(state({ custom: true }), V3), false);
});

test('advReset: the voice values go back, the two CHOICES do not', () => {
  const dirty = state({ custom: true, open: true, model: 'eleven_v3', stability: 12, boost: false, speed: 1.2 });
  const clean = advReset(dirty);
  assert.equal(clean.stability, ADV_DEFAULTS.stability);
  assert.equal(clean.boost, ADV_DEFAULTS.boost);
  assert.equal(clean.speed, ADV_DEFAULTS.speed);
  assert.equal(clean.custom, true);
  assert.equal(clean.open, true);
  assert.equal(clean.model, 'eleven_v3');
});

/* ------------------------------------------------------------- quotaView: the four states */

test('quotaView: a non-anonymous snapshot shows no banner', () => {
  assert.equal(quotaView({ anonymous: false, enabled: true }, true).kind, 'none');
  assert.equal(quotaView(null, false).kind, 'none');
});

test('quotaView: enabled:false alone is the operator switch', () => {
  assert.equal(quotaView({ anonymous: true, enabled: false }, false).kind, 'disabled');
});

test('quotaView: enabled:false WITH visitor_identified:false is the server condition', () => {
  // The state that must not read as "you have used your allowance": the zeroes below are the
  // absence of a per-visitor counter, not a spent one.
  const v = quotaView(
    { anonymous: true, enabled: false, visitor_identified: false, remaining: { analyses: 0, tts: 0, conversions: 0 } },
    false,
  );
  assert.equal(v.kind, 'unavailable');
});

test('quotaView: a server that does not send visitor_identified still says "disabled"', () => {
  // Additive field: absent is not false, or every older deployment would change its wording.
  assert.equal(quotaView({ anonymous: true, enabled: false, remaining: {} }, false).kind, 'disabled');
});

test('quotaView: signed out, the transcription allowance is not quoted', () => {
  // Those tabs are not rendered for a signed-out visitor, so naming their allowance would be
  // advertising a door that is not in the wall.
  const v = quotaView({ anonymous: true, enabled: true, remaining: { analyses: 4, tts: 9 } }, false);
  assert.equal(v.kind, 'counts');
  if (v.kind !== 'counts') return;
  assert.deepEqual(v.parts.map(p => p.labelKey), ['quota.clips']);
});

test('quotaView: signed in with an anonymous snapshot quotes transcriptions too', () => {
  const v = quotaView({ anonymous: true, enabled: true, remaining: { analyses: 4, tts: 9 } }, true);
  assert.equal(v.kind, 'counts');
  if (v.kind !== 'counts') return;
  assert.deepEqual(v.parts.map(p => p.labelKey), ['quota.analyses', 'quota.clips']);
});

test('quotaView: an ABSENT conversions key claims nothing; a null one is uncapped', () => {
  const without = quotaView({ anonymous: true, enabled: true, remaining: { tts: 1 } }, false);
  const withNull = quotaView({ anonymous: true, enabled: true, remaining: { tts: 1, conversions: null } }, false);
  if (without.kind !== 'counts' || withNull.kind !== 'counts') { assert.fail('expected counts'); return; }
  assert.equal(without.parts.length, 1);
  assert.equal(withNull.parts.length, 2);
  assert.equal(withNull.parts[1].left, null);      // rendered as ∞, never as 0
});

test('quotaView: an exhausted count turns the banner amber, an uncapped one does not', () => {
  const spent = quotaView({ anonymous: true, enabled: true, remaining: { tts: 0 } }, false);
  const free = quotaView({ anonymous: true, enabled: true, remaining: { tts: null } }, false);
  if (spent.kind !== 'counts' || free.kind !== 'counts') { assert.fail('expected counts'); return; }
  assert.equal(spent.warn, true);
  assert.equal(free.warn, false);
});

/* ------------------------------------------------- conversionAllowance: the converter's line */

test('conversionAllowance: no cap named means claim nothing', () => {
  assert.equal(conversionAllowance({ anonymous: true, enabled: true }), null);
  assert.equal(conversionAllowance({ anonymous: true, enabled: true, max_conversions_per_day: 0 }), null);
  assert.equal(conversionAllowance({ anonymous: false, max_conversions_per_day: 60 }), null);
});

test('conversionAllowance: an unreported remainder falls back to the whole cap', () => {
  assert.deepEqual(
    conversionAllowance({ anonymous: true, enabled: true, max_conversions_per_day: 60, remaining: {} }),
    { max: 60, left: 60 },
  );
  assert.deepEqual(
    conversionAllowance({ anonymous: true, enabled: true, max_conversions_per_day: 60, remaining: { conversions: 7 } }),
    { max: 60, left: 7 },
  );
});
