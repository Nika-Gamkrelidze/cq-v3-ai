import test from 'node:test';
import assert from 'node:assert/strict';
import {
  BOT_SOURCE, RUBRIC_SOURCE, checkKeyterms, defaultsPayload, formFromConfig, formFromDefaults,
  formatIds, keyState, killListAfter, killRowState, languageCodes, parseKeyterms, parseOverrides,
  payloadFromForm, sourcePill,
} from '../../app/console/logic.ts';

/* The operator console's decisions. Every one of these was an inline expression inside a render
   function in `admin.html`, and every one of them is a SAFE-DIRECTION default that reads like an
   arbitrary choice — which is exactly the kind of thing a port flips by accident. */

const OFF = { global_disabled: false, disabled_clients: [] as string[] };

test('killRowState: a workspace that never switched autopilot on reads as off, not stopped', () => {
  const row = { id: 'a', name: 'Acme', autopilot: false, reachable: true };
  assert.deepEqual(killRowState(row, OFF), { cls: 'notinkb', key: 'kill.state.off' });
  // Even under the global brake: telling an operator a bot is "stopped" when the customer never
  // turned it on invites them to resume something that was never running.
  assert.equal(killRowState(row, { ...OFF, global_disabled: true }).key, 'kill.state.off');
  assert.equal(killRowState(row, { ...OFF, disabled_clients: ['a'] }).key, 'kill.state.off');
});

test('killRowState: live, and both ways of being stopped', () => {
  const row = { id: 'a', name: 'Acme', autopilot: true, reachable: true };
  assert.deepEqual(killRowState(row, OFF), { cls: 'ready', key: 'kill.state.live' });
  assert.equal(killRowState(row, { ...OFF, global_disabled: true }).key, 'kill.state.stopped');
  assert.equal(killRowState(row, { ...OFF, disabled_clients: ['a'] }).key, 'kill.state.stopped');
  // Another workspace's stop is not this one's.
  assert.equal(killRowState(row, { ...OFF, disabled_clients: ['b'] }).key, 'kill.state.live');
});

test('killListAfter: stopping twice does not duplicate an id', () => {
  const once = killListAfter(OFF, 'a', true);
  assert.deepEqual(once, ['a']);
  assert.deepEqual(killListAfter({ ...OFF, disabled_clients: once }, 'a', true), ['a']);
  assert.deepEqual(killListAfter({ ...OFF, disabled_clients: ['a', 'b'] }, 'a', false), ['b']);
  // Resuming something that was never stopped is a no-op, not an error.
  assert.deepEqual(killListAfter(OFF, 'a', false), []);
});

test('keyState: revoked, expired, still-valid-until, and plain live', () => {
  const now = Date.parse('2026-09-08T12:00:00Z');
  assert.equal(keyState({ revoked_at: '2026-09-01T00:00:00Z' }, now), 'revoked');
  // Revoked wins over an expiry that has not elapsed: the key is gone either way.
  assert.equal(keyState({ revoked_at: '2026-09-01T00:00:00Z', expires_at: '2026-12-01T00:00:00Z' }, now), 'revoked');
  assert.equal(keyState({ expires_at: '2026-09-01T00:00:00Z' }, now), 'expired');
  assert.equal(keyState({ expires_at: '2026-09-15T00:00:00Z' }, now), 'expires');
  assert.equal(keyState({}, now), 'live');
  // A malformed stamp must not render as "expires NaN" — it is simply not an expiring key.
  assert.equal(keyState({ expires_at: 'whenever' }, now), 'live');
});

test('parseOverrides: an empty box is "use the tier", not zero', () => {
  const r = parseOverrides({
    max_analyses_per_day: '', max_audio_mb: '25', max_tts_per_day: '  ', max_conversions_per_day: '0',
  });
  assert.equal(r.ok, true);
  // Only the two that were filled in — and 0 is a real override ("none allowed"), not an absence.
  assert.deepEqual(r.limits, { max_audio_mb: 25, max_conversions_per_day: 0 });
});

test('parseOverrides: a negative or unparseable cap fails the whole submit', () => {
  assert.equal(parseOverrides({ max_audio_mb: '-1' }).ok, false);
  assert.equal(parseOverrides({ max_audio_mb: 'lots' }).ok, false);
});

test('formFromConfig: the risky knob is off unless it is literally true', () => {
  assert.equal(formFromConfig({}).allowGeneral, false);
  assert.equal(formFromConfig({ settings: { allow_general_knowledge: 'yes' } }).allowGeneral, false);
  assert.equal(formFromConfig({ settings: { allow_general_knowledge: true } }).allowGeneral, true);
  // The mirror image: the handoff summary is on unless it is literally false.
  assert.equal(formFromConfig({}).handoffSummary, true);
  assert.equal(formFromConfig({ settings: { handoff_summary: false } }).handoffSummary, false);
});

test('formFromConfig: an unknown disclosure mode falls back to disclosing, never to silence', () => {
  assert.equal(formFromConfig({}).disclosureMode, 'first');
  assert.equal(formFromConfig({ settings: { disclosure_mode: 'sometimes' } }).disclosureMode, 'first');
  assert.equal(formFromConfig({ settings: { disclosure_mode: 'off' } }).disclosureMode, 'off');
});

test('formFromConfig: a stored 0 survives, an absent value gets the built-in default', () => {
  assert.equal(formFromConfig({}).minScore, '0.35');
  assert.equal(formFromConfig({ min_score: 0 }).minScore, '0');
  assert.equal(formFromConfig({ min_hits: 3 }).minHits, '3');
  // A cap nobody set stays EMPTY — an empty box means "the built-in default", where a 0 would
  // read as "none allowed".
  assert.equal(formFromConfig({}).caps.tenant, '');
  assert.equal(formFromConfig({ settings: { limits: { tenant_per_minute: 12 } } }).caps.tenant, '12');
});

test('formFromConfig: escalation keywords arrive as a list and are shown as one line', () => {
  assert.equal(formFromConfig({ settings: { escalation_keywords: ['lawyer', 'chargeback'] } }).escalation,
    'lawyer, chargeback');
  // A server that stored a bare string is not a crash.
  assert.equal(formFromConfig({ settings: { escalation_keywords: 'lawyer' } }).escalation, 'lawyer');
  assert.equal(formFromConfig({}).escalation, '');
});

test('payloadFromForm: an empty language box is omitted, never sent as silence', () => {
  const form = formFromConfig({ greeting: { en: 'Hi', ka: '  ' } });
  const body = payloadFromForm(form, null);
  // `ka` would otherwise shadow the built-in Georgian wording with an empty string for every
  // workspace inheriting this default.
  assert.deepEqual(body.greeting, { en: 'Hi' });
  assert.deepEqual(body.refusal_copy, {});
});

test('payloadFromForm: a cleared cap goes back to the built-in, it does not survive', () => {
  const cfg = { settings: { limits: { tenant_per_minute: 12, enduser_per_hour: 5 } } };
  const form = formFromConfig(cfg);
  form.caps.tenant = '';                       // the operator emptied the box
  const settings = payloadFromForm(form, cfg).settings as { limits: Record<string, number> };
  assert.deepEqual(settings.limits, { enduser_per_hour: 5 });
});

test('payloadFromForm: an unknown knob in settings survives a save from this form', () => {
  const cfg = { settings: { strict: true, some_future_knob: 7 } };
  const settings = payloadFromForm(formFromConfig(cfg), cfg).settings as Record<string, unknown>;
  assert.equal(settings.strict, true);
  assert.equal(settings.some_future_knob, 7);
  // …while the fields this form owns are the form's values, not the previous blob's.
  assert.equal(settings.min_score, 0.35);
});

test('payloadFromForm: keywords are split, trimmed and de-blanked', () => {
  const form = formFromConfig({});
  form.escalation = ' lawyer ,, chargeback , ';
  const settings = payloadFromForm(form, null).settings as { escalation_keywords: string[] };
  assert.deepEqual(settings.escalation_keywords, ['lawyer', 'chargeback']);
});

test('payloadFromForm: canned snippets are carried through untouched', () => {
  // The form has no editor for them; dropping them would silently delete a workspace-wide
  // feature on the next unrelated save.
  const cfg = { canned: [{ q: 'hours', a: '9-5' }] };
  assert.deepEqual(payloadFromForm(formFromConfig(cfg), cfg).canned, [{ q: 'hours', a: '9-5' }]);
  assert.deepEqual(payloadFromForm(formFromConfig({}), {}).canned, []);
});

/* ------------------------------------------------------- the transcription defaults */

test('formFromDefaults: speakers stay separated unless the server literally says otherwise', () => {
  // Diarization is what splits a call into turns; a field that failed to arrive must not read
  // as "off" and quietly cost the per-speaker analysis on every future upload.
  assert.equal(formFromDefaults({}).diarize, true);
  assert.equal(formFromDefaults(null).diarize, true);
  assert.equal(formFromDefaults({ diarize: false }).diarize, false);
  // Not truthiness: only an explicit false turns it off.
  assert.equal(formFromDefaults({ diarize: 0 }).diarize, true);
});

test('formFromDefaults: an absent format reads as today\'s conversion, not as an opinion', () => {
  assert.equal(formFromDefaults({}).format, 'mp3_16k');
  assert.equal(formFromDefaults({ audio_format: '' }).format, 'mp3_16k');
  assert.equal(formFromDefaults({ audio_format: 'flac_16k' }).format, 'flac_16k');
});

test('formFromDefaults: detect-automatically is an empty box, and key terms are one per line', () => {
  const f = formFromDefaults({ language_code: null, keyterms: ['თვემდე', 'ფრანშიზა'] });
  assert.equal(f.language, '');
  assert.equal(f.keyterms, 'თვემდე\nფრანშიზა');
  // A keyterms field that is not a list at all must not crash the card.
  assert.equal(formFromDefaults({ keyterms: 'nope' }).keyterms, '');
});

test('parseKeyterms: lines only — a comma inside a term is part of the term', () => {
  assert.deepEqual(parseKeyterms('  a \n\n b  \n'), ['a', 'b']);
  assert.deepEqual(parseKeyterms(''), []);
  assert.deepEqual(parseKeyterms('Smith, Jones'), ['Smith, Jones']);
});

test('checkKeyterms: the first offending term is named, and nothing else is', () => {
  assert.equal(checkKeyterms(['fine', 'also fine']), null);
  // Characters the API rejects outright.
  assert.deepEqual(checkKeyterms(['ok', 'a<b', 'c{d']), { key: 'tr.keyterms.badchars', vars: { term: 'a<b' } });
  assert.deepEqual(checkKeyterms(['a\\b']), { key: 'tr.keyterms.badchars', vars: { term: 'a\\b' } });
  // Five words is allowed, six is not.
  assert.equal(checkKeyterms(['one two three four five']), null);
  assert.deepEqual(checkKeyterms(['one two three four five six']),
    { key: 'tr.keyterms.toolong', vars: { term: 'one two three four five six' } });
  // 50 characters is the documented ceiling, so it passes; 51 does not.
  assert.equal(checkKeyterms(['x'.repeat(50)]), null);
  assert.equal(checkKeyterms(['x'.repeat(51)])?.key, 'tr.keyterms.toolong');
});

test('checkKeyterms: too many terms is reported through the counter, with both numbers', () => {
  assert.equal(checkKeyterms(Array(1000).fill('x')), null);
  assert.deepEqual(checkKeyterms(Array(1001).fill('x')),
    { key: 'tr.keyterms.count', vars: { n: 1001, max: 1000 } });
});

test('defaultsPayload: an empty language is null, never an empty string', () => {
  // '' is not a language code; the field is absent-means-detect, and the two must not be
  // conflated when the server layers this default under a workspace override.
  assert.deepEqual(
    defaultsPayload({ language: '  ', diarize: true, keyterms: ' ka \n\n', format: 'flac_16k' }),
    { language_code: null, diarize: true, keyterms: ['ka'], audio_format: 'flac_16k' },
  );
  assert.equal(defaultsPayload({ language: 'ka', diarize: false, keyterms: '', format: 'original' }).language_code, 'ka');
});

test('languageCodes: detect first, and a stored code the catalogue does not list survives', () => {
  const rows = [{ code: 'en' }, { code: 'ka' }, { code: 'ru' }];
  assert.deepEqual(languageCodes(rows, ''), ['', 'en', 'ka', 'ru']);
  assert.deepEqual(languageCodes(rows, 'ka'), ['', 'en', 'ka', 'ru']);
  // Speech-to-text accepts far more languages than the TTS catalogue lists: an operator who set
  // `pl` through the API must not open a picker that reads "detect" and save that over it.
  assert.deepEqual(languageCodes(rows, 'pl'), ['', 'en', 'ka', 'ru', 'pl']);
  assert.deepEqual(languageCodes([], 'pl'), ['', 'pl']);
});

test('formatIds: the five known ids, plus an unknown stored one shown first', () => {
  assert.deepEqual(formatIds('flac_16k'),
    ['original', 'flac_full', 'flac_16k', 'wav_16k', 'mp3_16k']);
  assert.equal(formatIds('opus_48k')[0], 'opus_48k');
  assert.equal(formatIds('opus_48k').length, 6);
  assert.equal(formatIds('').length, 5);
});

test('sourcePill: demo and builtin both mean "nothing is saved here yet"', () => {
  assert.deepEqual(sourcePill(RUBRIC_SOURCE, 'stored', 'builtin'), { key: 'pb.src.stored', cls: 'ready' });
  assert.deepEqual(sourcePill(RUBRIC_SOURCE, 'demo', 'builtin'), { key: 'pb.src.demo', cls: 'processing' });
  // An unknown or missing source is never reported as stored.
  assert.deepEqual(sourcePill(RUBRIC_SOURCE, undefined, 'builtin'), { key: 'pb.src.builtin', cls: 'notinkb' });
  assert.deepEqual(sourcePill(BOT_SOURCE, 'nonsense', 'builtin'),
    { key: 'pb.defbot.source.builtin', cls: 'notinkb' });
});
