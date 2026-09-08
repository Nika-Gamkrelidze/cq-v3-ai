import test from 'node:test';
import assert from 'node:assert/strict';
import {
  AUDIO_FORMATS, FALLBACK, fromDraft, KEYTERMS_MAX, KEYTERM_MAX_CHARS, parseKeyterms,
  readConfig, readFormats, readSettings, toDraft, validate, type Settings,
} from '../../app/workspace/transcription.ts';

/* The workspace's transcription override. Two properties carry the feature:

     1. READING A REPLY IS AN INHERITANCE, not a merge of defaults. A field the server left out
        of the effective layer is the INHERITED value — never a value this file made up. Get
        that wrong and the card shows settings the pipeline is not using, which is the exact
        failure the whole feature exists to end.
     2. VALIDATION NAMES THE OFFENDING TERM. The backend is the authority; this is the same
        rule said early, and a list of 200 key terms is unusable if the answer is only "400". */

const reply = (over: Record<string, unknown> = {}, inherited: Record<string, unknown> = {}) => ({
  ...over,
  inherited,
});

/* ------------------------------------------------------------------ reading a reply */

test('an empty reply falls back to what the pipeline does today', () => {
  const s = readSettings(undefined);
  assert.deepEqual(s, FALLBACK);
  // Not a preference: mp3 at 16 kHz and diarize-on are literally what the backend does now,
  // so a screen with nothing from the server still describes the real pipeline.
  assert.equal(s.audio_format, 'mp3_16k');
  assert.equal(s.diarize, true);
});

test('a field the effective layer omits is read as the inherited one', () => {
  const c = readConfig(reply(
    { language_code: 'ka', is_default: false },
    { language_code: 'en', diarize: false, keyterms: ['ფრანშიზა'], audio_format: 'flac_16k' },
  ));
  assert.equal(c.effective.language_code, 'ka');       // the override's own
  assert.equal(c.effective.diarize, false);            // inherited
  assert.deepEqual(c.effective.keyterms, ['ფრანშიზა']); // inherited
  assert.equal(c.effective.audio_format, 'flac_16k');  // inherited
  assert.equal(c.isDefault, false);
});

test('an explicit null language means detect, and does not fall through to the layer below', () => {
  const c = readConfig(reply({ language_code: null }, { language_code: 'ka' }));
  assert.equal(c.effective.language_code, null);
  assert.equal(c.inherited.language_code, 'ka');
});

test('an unknown audio format shows what would actually be used, not a guess', () => {
  const c = readConfig(reply({ audio_format: 'opus_8k' }, { audio_format: 'flac_16k' }));
  assert.equal(c.effective.audio_format, 'flac_16k');
});

test('is_default is only true when the server says so', () => {
  assert.equal(readConfig(reply({ is_default: true })).isDefault, true);
  assert.equal(readConfig(reply({ is_default: 'yes' })).isDefault, false);
  assert.equal(readConfig(reply({})).isDefault, false);
});

test('junk in keyterms is filtered rather than rendered', () => {
  const s = readSettings({ keyterms: ['  თვემდე ', 42, null, '', 'ფრანშიზა'] });
  assert.deepEqual(s.keyterms, ['თვემდე', 'ფრანშიზა']);
});

test('a language code outside the three the product speaks survives the round trip', () => {
  // The deployment default is allowed to name a fourth language; dropping it here would
  // silently retype somebody else's setting on save.
  const c = readConfig(reply({ language_code: 'de' }));
  assert.equal(c.effective.language_code, 'de');
  assert.equal(fromDraft(toDraft(c.effective)).language_code, 'de');
});

/* ------------------------------------------------------------------ the form */

test('the form round-trips settings unchanged', () => {
  const s: Settings = { language_code: 'ka', diarize: false, keyterms: ['a', 'b'], audio_format: 'wav_16k' };
  assert.deepEqual(fromDraft(toDraft(s)), s);
});

test('detect-automatically is the empty option and comes back as null', () => {
  const d = toDraft({ ...FALLBACK, language_code: null });
  assert.equal(d.language, '');
  assert.equal(fromDraft(d).language_code, null);
});

test('key terms are one per line, trimmed, and blank lines are not terms', () => {
  assert.deepEqual(parseKeyterms('  თვემდე \n\n სადაზღვევო\n   \nფრანშიზა\n'),
    ['თვემდე', 'სადაზღვევო', 'ფრანშიზა']);
  // A half-typed list keeps its blank line in the textarea and simply does not count it.
  assert.equal(parseKeyterms('one\n\n').length, 1);
});

/* ------------------------------------------------------------------ validation */

const withTerms = (terms: string[]) => ({ ...FALLBACK, keyterms: terms });

test('an ordinary list passes', () => {
  assert.equal(validate(withTerms(['თვემდე', 'deductible', 'no claims bonus'])), null);
  assert.equal(validate(FALLBACK), null);
});

test('every character the API rejects is refused, and the term is named', () => {
  for (const bad of ['<b>', 'a>b', '{x}', 'a{', '[list]', 'back\\slash']) {
    const p = validate(withTerms(['fine', bad]));
    assert.ok(p, `expected ${bad} to be refused`);
    assert.equal(p!.key, 'tr.keyterms.badchars');
    assert.equal(p!.field, 'keyterms');
    // The message interpolates {term}: a bad entry in a long list has to be findable.
    assert.equal(p!.vars.term, bad);
  }
});

test('a term over the length or the word limit is refused', () => {
  const long = 'x'.repeat(KEYTERM_MAX_CHARS);
  const p = validate(withTerms([long]));
  assert.equal(p!.key, 'tr.keyterms.toolong');
  assert.equal(p!.vars.term, long);
  assert.equal(validate(withTerms(['x'.repeat(KEYTERM_MAX_CHARS - 1)])), null);
  // Five words is the limit, not the first refusal.
  assert.equal(validate(withTerms(['one two three four five'])), null);
  assert.equal(validate(withTerms(['one two three four five six']))!.key, 'tr.keyterms.toolong');
});

test('length is counted in characters a person would count', () => {
  // '🇬🇪' is four UTF-16 units and one thing on screen; counting units would refuse a term
  // the API accepts.
  assert.equal(validate(withTerms(['🇬🇪'.repeat(20)])), null);
});

test('more than the maximum number of terms is refused by the count itself', () => {
  const many = Array.from({ length: KEYTERMS_MAX + 2 }, (_, i) => `t${i}`);
  const p = validate(withTerms(many));
  assert.equal(p!.key, 'tr.keyterms.count');
  assert.equal(p!.vars.n, KEYTERMS_MAX + 2);
  assert.equal(p!.vars.max, KEYTERMS_MAX);
  assert.equal(validate(withTerms(many.slice(0, KEYTERMS_MAX))), null);
});

test('the five formats the API accepts are the five offered, in fidelity order', () => {
  assert.deepEqual([...AUDIO_FORMATS],
    ['original', 'flac_full', 'flac_16k', 'wav_16k', 'mp3_16k']);
});

test('the offered formats are the deployment\u2019s, re-sorted into fidelity order', () => {
  // The server sends them alphabetically; offering them in that order would put the lossy one
  // in the middle and hide the trade the setting is about.
  assert.deepEqual(readFormats(['flac_16k', 'flac_full', 'mp3_16k', 'original', 'wav_16k']),
    ['original', 'flac_full', 'flac_16k', 'wav_16k', 'mp3_16k']);
  // A deployment without ffmpeg offers fewer, and the control must not offer what it cannot do.
  assert.deepEqual(readFormats(['original']), ['original']);
  // An id this UI has no words for is not offered rather than shown as a raw key.
  assert.deepEqual(readFormats(['opus_8k', 'flac_16k']), ['flac_16k']);
  // An older server that sends no list at all still gets a usable control.
  assert.deepEqual(readFormats(undefined), [...AUDIO_FORMATS]);
  assert.deepEqual(readFormats([]), [...AUDIO_FORMATS]);
});

test('can_edit closes the form only when the server explicitly says false', () => {
  assert.equal(readConfig(reply({ can_edit: false })).canEdit, false);
  assert.equal(readConfig(reply({ can_edit: true })).canEdit, true);
  // Silence is an older server, not a refusal: the page's own owner predicate still decides.
  assert.equal(readConfig(reply({})).canEdit, null);
});
