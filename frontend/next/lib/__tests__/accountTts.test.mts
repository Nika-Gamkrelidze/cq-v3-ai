import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ADV_DEFAULTS, advBody, advCaps, advFmt, advLoadFrom, effectiveModel, isV3,
} from '../../app/account/advanced.ts';
import { expiryLabel } from '../../app/account/expiry.ts';

/* The advanced voice panel is remembered in localStorage, which means the values that reach
   `POST /tts` can come from a blob written by an older build — or edited by hand. Every one of
   them is range-checked by the server, so an out-of-range number is not a cosmetic problem: it
   is a 422 on every clip, with nothing on screen to say why. These tests pin the sanitiser and
   the "only send what this model accepts" rule, which are the two halves of that guarantee. */

test('nothing stored, or not JSON, gives the defaults', () => {
  assert.deepEqual(advLoadFrom(null), ADV_DEFAULTS);
  assert.deepEqual(advLoadFrom(''), ADV_DEFAULTS);
  assert.deepEqual(advLoadFrom('not json at all'), ADV_DEFAULTS);
  assert.deepEqual(advLoadFrom('"a string"'), ADV_DEFAULTS);
});

test('every out-of-range field falls back on its own, not the whole blob', () => {
  const s = advLoadFrom(JSON.stringify({
    stability: 999, similarity: -4, style: 'wat', speed: 5, model: 42, preset: 7,
    custom: true, open: true,
  }));
  assert.equal(s.stability, 50);
  assert.equal(s.similarity, 75);
  assert.equal(s.style, 0);
  assert.equal(s.speed, 1);
  assert.equal(s.model, '');          // a non-string model id is no model id
  assert.equal(s.preset, 0.5);
  // The two booleans it DID understand survive: one bad number must not reset the panel.
  assert.equal(s.custom, true);
  assert.equal(s.open, true);
});

test('the preset snaps to the three values the segmented control can show', () => {
  const preset = (v: unknown) => advLoadFrom(JSON.stringify({ preset: v })).preset;
  assert.equal(preset(0), 0);
  assert.equal(preset(0.2), 0);
  assert.equal(preset(0.25), 0.5);
  assert.equal(preset(0.61), 0.5);
  assert.equal(preset(0.8), 1);
  assert.equal(preset(1), 1);
});

test('the two checkboxes default ON and only an explicit false turns them off', () => {
  assert.equal(advLoadFrom('{}').boost, true);
  assert.equal(advLoadFrom('{"boost":false}').boost, false);
  assert.equal(advLoadFrom('{"boost":0}').boost, true);      // not `false`: not a decision
  assert.equal(advLoadFrom('{"forcelang":false}').forcelang, false);
});

test('speed is bounded by what the API accepts', () => {
  assert.equal(advLoadFrom('{"speed":0.7}').speed, 0.7);
  assert.equal(advLoadFrom('{"speed":1.2}').speed, 1.2);
  assert.equal(advLoadFrom('{"speed":0.69}').speed, 1);
  assert.equal(advLoadFrom('{"speed":1.21}').speed, 1);
});

test('Auto resolves to the model the API named, and Georgian never falls back to v2', () => {
  assert.equal(effectiveModel('eleven_flash_v2_5', 'ka', { ka: 'eleven_v3' }), 'eleven_flash_v2_5');
  assert.equal(effectiveModel('', 'ka', { ka: 'eleven_v3' }), 'eleven_v3');
  assert.equal(effectiveModel('', 'en', { en: 'eleven_multilingual_v2' }), 'eleven_multilingual_v2');
  // No `model` field on /languages: the hardcoded pair mirrors what the server does for Auto,
  // and Georgian on eleven_multilingual_v2 is the English-accented-fake-Georgian bug.
  assert.equal(effectiveModel('', 'ka', {}), 'eleven_v3');
  assert.equal(effectiveModel('', 'ru', {}), 'eleven_multilingual_v2');
});

test('isV3 matches the family, not one id', () => {
  assert.equal(isV3('eleven_v3'), true);
  assert.equal(isV3('eleven_v3_preview'), true);
  assert.equal(isV3('eleven_multilingual_v2'), false);
  assert.equal(isV3(null), false);
});

test('capabilities come from the list when it has them', () => {
  const models = [{ model_id: 'x', supports: { presets: false, style: true, speaker_boost: false, speed: true, language_code: 'enforced' } }];
  assert.deepEqual(advCaps(models, 'x'),
    { presets: false, style: true, speaker_boost: false, speed: true, language_code: 'enforced' });
});

test('an unknown id assumes the documented shape of its family', () => {
  assert.deepEqual(advCaps([], 'eleven_v3'),
    { presets: true, style: false, speaker_boost: true, speed: false, language_code: 'rejected' });
  assert.deepEqual(advCaps([], 'eleven_multilingual_v2'),
    { presets: false, style: true, speaker_boost: true, speed: true, language_code: 'ignored' });
});

test('until Customise is ticked the request carries no voice settings at all', () => {
  const caps = advCaps([], 'eleven_multilingual_v2');
  assert.deepEqual(advBody({ ...ADV_DEFAULTS, custom: false }, caps), {});
  assert.deepEqual(advBody({ ...ADV_DEFAULTS, custom: false, model: 'eleven_v3' }, caps),
    { model_id: 'eleven_v3' });
});

test('a value remembered for another model is never sent to one that rejects it', () => {
  // v3 has no style and no speed, and rejects language_code. The panel still REMEMBERS a
  // style and a speed from the last time a v2 voice was in play; neither may go out.
  const adv = { ...ADV_DEFAULTS, custom: true, style: 40, speed: 1.15, similarity: 60, preset: 1 };
  const body = advBody(adv, advCaps([], 'eleven_v3'));
  assert.deepEqual(body.voice_settings, { stability: 1, similarity_boost: 0.6, use_speaker_boost: true });
  assert.equal('enforce_language' in body, false);
});

test('a slider model sends percentages as 0..1, and the preset is not one of them', () => {
  const adv = { ...ADV_DEFAULTS, custom: true, stability: 30, similarity: 80, style: 25, speed: 1.05, preset: 0 };
  const body = advBody(adv, advCaps([], 'eleven_multilingual_v2'));
  assert.deepEqual(body.voice_settings,
    { stability: 0.3, similarity_boost: 0.8, style: 0.25, use_speaker_boost: true, speed: 1.05 });
});

test('the language checkbox is sent only by a model that enforces it', () => {
  const enforced = { presets: false, style: false, speaker_boost: false, speed: false, language_code: 'enforced' };
  const ignored = { ...enforced, language_code: 'ignored' };
  const adv = { ...ADV_DEFAULTS, custom: true, forcelang: false };
  assert.equal(advBody(adv, enforced).enforce_language, false);
  assert.equal('enforce_language' in advBody(adv, ignored), false);
});

test('a value is written the way its control reads', () => {
  assert.equal(advFmt('speed', 1.05), '1.05×');
  assert.equal(advFmt('stability', 42.4), '42%');
});

/* The expiry label is the one place a batch's real deadline reaches the screen. Reading it
   from the format catalogue's TTL constant instead would quote the ANONYMOUS two hours on an
   account whose batches live for days. */
const T = (k: string, v?: Record<string, string | number>) => (v ? `${k}:${JSON.stringify(v)}` : k);
const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);
const inMs = (ms: number) => new Date(NOW + ms).toISOString();

test('no deadline means no sentence, not an expired one', () => {
  assert.equal(expiryLabel(null, T, NOW), '');
  assert.equal(expiryLabel(undefined, T, NOW), '');
  assert.equal(expiryLabel('not a date', T, NOW), '');
});

test('the unit follows how much is left', () => {
  assert.equal(expiryLabel(inMs(-1), T, NOW), 'ac.hist.expired');
  assert.equal(expiryLabel(inMs(0), T, NOW), 'ac.hist.expired');
  assert.equal(expiryLabel(inMs(3 * 86400000), T, NOW), 'ac.hist.left.d:{"n":3}');
  assert.equal(expiryLabel(inMs(24 * 3600000), T, NOW), 'ac.hist.left.d:{"n":1}');
  assert.equal(expiryLabel(inMs(23 * 3600000), T, NOW), 'ac.hist.left.h:{"n":23}');
  assert.equal(expiryLabel(inMs(3600000), T, NOW), 'ac.hist.left.h:{"n":1}');
  assert.equal(expiryLabel(inMs(30 * 60000), T, NOW), 'ac.hist.left.m:{"n":30}');
});

test('a live batch never reports zero minutes left', () => {
  assert.equal(expiryLabel(inMs(1000), T, NOW), 'ac.hist.left.m:{"n":1}');
});
