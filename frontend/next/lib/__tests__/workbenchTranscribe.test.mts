import test from 'node:test';
import assert from 'node:assert/strict';
import {
  AUDIO_FORMATS, CODE_DEFAULTS, KEYTERM_MAX, effective, isAudioFormat, keytermsError,
  keytermsText, languageLabel, languageOptions, newDraft, normaliseConfig, normaliseSettings,
  overridePatch, parseKeyterms, reseed, setField,
  type TranscriptionSettings,
} from '../../components/Workbench/transcribe.ts';

/* The transcription override is the one control in the panel whose mistakes are silent: a
   field sent when it should have been absent does not throw, it just pins a recording to
   whatever the workspace happened to say that afternoon, and a field NOT sent when it should
   have been transcribes a Georgian insurance call with the settings that mis-heard it in the
   first place. Both failures look identical on screen. Hence this file. */

const base: TranscriptionSettings = {
  language_code: 'ka',
  diarize: true,
  keyterms: ['თვემდე'],
  audio_format: 'flac_16k',
};

/* ------------------------------------------------------------------ key terms */

test('parseKeyterms takes one term per line, trimmed, deduped', () => {
  assert.deepEqual(parseKeyterms('  a \r\n\n b\nb\n  \n'), ['a', 'b']);
  assert.deepEqual(parseKeyterms(''), []);
  // A repeated term is a term paid for twice: key terms add ~20% to the price of the run.
  assert.deepEqual(parseKeyterms('x\nx\nx'), ['x']);
});

test('keytermsText round-trips through parseKeyterms', () => {
  const terms = ['სადაზღვევო', 'ფრანშიზა', 'no claims bonus'];
  assert.deepEqual(parseKeyterms(keytermsText(terms)), terms);
});

test('keytermsError names the offending term and prefers the specific rule', () => {
  assert.equal(keytermsError(['fine', 'ok']), null);

  const bad = keytermsError(['a<b']);
  assert.equal(bad?.key, 'tr.keyterms.badchars');
  assert.equal(bad?.vars?.term, 'a<b');

  const long = keytermsError(['x'.repeat(50)]);
  assert.equal(long?.key, 'tr.keyterms.toolong');

  const words = keytermsError(['one two three four five six']);
  assert.equal(words?.key, 'tr.keyterms.toolong');

  // A term that breaks BOTH rules is reported as bad characters: telling someone to shorten a
  // string whose real problem is a bracket sends them round a loop that never terminates.
  const both = keytermsError(['[' + 'x'.repeat(60) + ']']);
  assert.equal(both?.key, 'tr.keyterms.badchars');

  // Every character the API rejects, one at a time.
  for (const ch of ['<', '>', '{', '}', '[', ']', '\\']) {
    assert.equal(keytermsError([`a${ch}b`])?.key, 'tr.keyterms.badchars', ch);
  }

  // 49 characters is allowed; 50 is not ("under 50").
  assert.equal(keytermsError(['x'.repeat(49)]), null);
  assert.equal(keytermsError(['one two three four five']), null);
});

test('keytermsError enforces the 1000-term cap before it inspects any term', () => {
  const over = new Array(KEYTERM_MAX + 1).fill(0).map((_, i) => `t${i}`);
  const e = keytermsError(over);
  assert.equal(e?.key, 'wb.tr.keyterms.toomany');
  assert.equal(e?.vars?.max, KEYTERM_MAX);
  assert.equal(keytermsError(over.slice(0, KEYTERM_MAX)), null);
});

/* ------------------------------------------------------------------ reading the server */

test('normaliseSettings falls back per field rather than per payload', () => {
  const s = normaliseSettings({ language_code: 'ka', audio_format: 'nonsense' }, CODE_DEFAULTS);
  assert.equal(s.language_code, 'ka');
  assert.equal(s.audio_format, CODE_DEFAULTS.audio_format);
  assert.equal(s.diarize, CODE_DEFAULTS.diarize);
  assert.deepEqual(s.keyterms, []);
});

test('an empty language string is "detect automatically", not a code', () => {
  // What an unset <select> sends. Read as a code it would be a language named '' — the API
  // would refuse it, and only after the audio had been uploaded.
  assert.equal(normaliseSettings({ language_code: '' }, base).language_code, null);
  assert.equal(normaliseSettings({ language_code: '  ' }, base).language_code, null);
  assert.equal(normaliseSettings({ language_code: null }, base).language_code, null);
  // Absent is different from empty: absent inherits.
  assert.equal(normaliseSettings({}, base).language_code, 'ka');
});

test('normaliseSettings drops non-strings out of keyterms', () => {
  const s = normaliseSettings({ keyterms: ['a', 3, null, ' b ', ''] }, CODE_DEFAULTS);
  assert.deepEqual(s.keyterms, ['a', 'b']);
});

test('is_default is only ever a literal true', () => {
  // A server that has not shipped the flag yet must not have its silence read as "this level
  // is inheriting" — that is the reading under which an override renders as absent.
  assert.equal(normaliseConfig({}).is_default, false);
  assert.equal(normaliseConfig({ is_default: 'yes' }).is_default, false);
  assert.equal(normaliseConfig({ is_default: 1 }).is_default, false);
  assert.equal(normaliseConfig({ is_default: true }).is_default, true);
});

test('normaliseConfig survives a payload that is not an object at all', () => {
  const c = normaliseConfig(null);
  assert.equal(c.audio_format, CODE_DEFAULTS.audio_format);
  assert.equal(c.inherited, null);
  assert.equal(c.is_default, false);
});

test('normaliseConfig reads the layer underneath when the server sends one', () => {
  const c = normaliseConfig({ language_code: 'ka', inherited: { language_code: null, diarize: false } });
  assert.equal(c.language_code, 'ka');
  assert.equal(c.inherited?.language_code, null);
  assert.equal(c.inherited?.diarize, false);
});

test('every documented audio format is accepted and nothing else is', () => {
  assert.deepEqual([...AUDIO_FORMATS], ['original', 'flac_full', 'flac_16k', 'wav_16k', 'mp3_16k']);
  for (const f of AUDIO_FORMATS) assert.ok(isAudioFormat(f), f);
  assert.equal(isAudioFormat('flac'), false);
  assert.equal(isAudioFormat(undefined), false);
});

/* ------------------------------------------------------------------ the override */

test('an untouched draft sends nothing, even with the switch on', () => {
  const d = { ...newDraft(base), on: true };
  assert.equal(overridePatch(d, base), null);
});

test('the switch off sends nothing however much was edited', () => {
  const d = setField(newDraft(base), 'language_code', 'en');
  assert.equal(d.on, false);
  assert.equal(overridePatch(d, base), null);
});

test('only the touched fields travel', () => {
  let d = { ...newDraft(base), on: true };
  d = setField(d, 'audio_format', 'original');
  // THE POINT OF THE WHOLE MODULE: language and diarize are not sent, so this recording still
  // follows the workspace on everything except the one thing that was actually changed.
  assert.deepEqual(overridePatch(d, base), { audio_format: 'original' });
});

test('a field touched and put back to the inherited value is not sent', () => {
  let d = { ...newDraft(base), on: true };
  d = setField(d, 'language_code', 'en');
  d = setField(d, 'language_code', 'ka');            // back to what the workspace says
  assert.equal(overridePatch(d, base), null);
  // ... and the same holds for the array, which needs a value comparison, not identity.
  let e = { ...newDraft(base), on: true };
  e = setField(e, 'keyterms', ['თვემდე']);
  assert.equal(overridePatch(e, base), null);
  e = setField(e, 'keyterms', ['თვემდე', 'ფრანშიზა']);
  assert.deepEqual(overridePatch(e, base), { keyterms: ['თვემდე', 'ფრანშიზა'] });
});

test('with no base loaded, every touched field is sent', () => {
  // The GET failed. Nothing is known about what would be inherited, so "the same as
  // inherited" cannot be claimed — an explicit choice is the only honest request.
  let d = { ...newDraft(null), on: true };
  d = setField(d, 'diarize', true);
  assert.deepEqual(overridePatch(d, null), { diarize: true });
});

test('turning diarize off is a real override, not a falsy one', () => {
  // `if (value)` anywhere in the patch builder would drop this and silently keep speaker
  // separation on — the one setting whose absence changes what the analysis can even see.
  let d = { ...newDraft(base), on: true };
  d = setField(d, 'diarize', false);
  assert.deepEqual(overridePatch(d, base), { diarize: false });
});

test('detect-automatically is a real override too', () => {
  let d = { ...newDraft(base), on: true };
  d = setField(d, 'language_code', null);
  const patch = overridePatch(d, base);
  assert.deepEqual(patch, { language_code: null });
  assert.ok(patch && 'language_code' in patch);
});

test('setField does not mutate the draft it was given', () => {
  const first = { ...newDraft(base), on: true };
  const second = setField(first, 'keyterms', ['x']);
  assert.deepEqual(first.values.keyterms, ['თვემდე']);
  assert.deepEqual(second.values.keyterms, ['x']);
  assert.equal(first.touched.keyterms, undefined);
});

test('newDraft copies the base rather than aliasing its array', () => {
  const d = newDraft(base);
  d.values.keyterms.push('leaked');
  assert.deepEqual(base.keyterms, ['თვემდე']);
});

/* ------------------------------------------------------------------ late-arriving config */

test('reseed moves untouched controls onto the real base and leaves edits alone', () => {
  // The panel paints before the GET answers, so it starts on the code defaults. When the
  // server finally speaks, a control nobody touched must show the truth — and one somebody
  // did touch must not jump under their hands.
  let d = newDraft(null);
  assert.equal(d.values.language_code, CODE_DEFAULTS.language_code);
  d = setField(d, 'audio_format', 'original');
  const after = reseed(d, base);
  assert.equal(after.values.language_code, 'ka');       // untouched -> follows the base
  assert.deepEqual(after.values.keyterms, ['თვემდე']);  // untouched -> follows the base
  assert.equal(after.values.audio_format, 'original');  // touched   -> kept
  assert.equal(after.touched.audio_format, true);
  assert.equal(after.on, d.on);
});

test('reseed does not alias the base it was handed', () => {
  const after = reseed(newDraft(null), base);
  after.values.keyterms.push('leaked');
  assert.deepEqual(base.keyterms, ['თვემდე']);
});

/* ------------------------------------------------------------------ what will happen */

test('effective is the base with the patch over it', () => {
  assert.deepEqual(effective(base, { audio_format: 'original' }), { ...base, audio_format: 'original' });
  assert.deepEqual(effective(base, null), base);
  assert.deepEqual(effective(null, null), CODE_DEFAULTS);
  assert.deepEqual(effective(null, { diarize: false }), { ...CODE_DEFAULTS, diarize: false });
});

/* ------------------------------------------------------------------ languages */

test('a code the picker does not carry is added rather than dropped', () => {
  // Otherwise a workspace default of, say, Swahili would silently reset itself to "detect"
  // the first time anybody opened the panel and closed it again.
  const opts = languageOptions('sw');
  assert.ok(opts.includes('sw'));
  assert.equal(opts.filter(x => x === 'sw').length, 1);
  assert.equal(languageOptions('ka').filter(x => x === 'ka').length, 1);
  assert.equal(languageOptions(null).includes('ka'), true);
});

test('languageLabel prefers the app’s own word, then falls back to the code', () => {
  // `translate` answers with the key itself on a miss — that is how the first branch is told
  // apart from the second.
  const dict: Record<string, string> = { 'lang.ka': 'ქართული' };
  const t = (k: string) => dict[k] ?? k;
  assert.equal(languageLabel('ka', 'ka', t), 'ქართული');
  // A code no dictionary and no ICU locale data can name comes back as itself, never blank.
  assert.equal(languageLabel('zzz', 'en', t), 'zzz');
});
