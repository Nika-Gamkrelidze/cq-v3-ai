/* The four transcription parameters, and the arithmetic of a per-recording override.
   =================================================================================
   Scribe mis-hears Georgian: a recording that says `36 თვემდე` (36 MONTHS) comes back as
   `36 წლამდე` (36 YEARS), and that transcript is what fact-check and the rubric then score.
   Four knobs exist to fight that — the spoken language, speaker separation, key terms, and
   what we actually upload — and they resolve down one chain:

       code defaults  <-  superadmin default  <-  workspace override  <-  THIS recording

   Each level sets only what it wants to change. This module owns the last link: what the
   person at the upload box changed, and nothing else. `overridePatch` is deliberately narrow
   — a field the person never touched is ABSENT from the request, not sent at its inherited
   value, because sending it would freeze today's inherited value into a recording that was
   meant to follow the workspace. That is the difference between "I want Georgian on this
   call" and "I want Georgian and also whatever the format happened to be this afternoon".

   No imports, on purpose: `lib/__tests__/workbenchTranscribe.test.mts` runs this file
   under `node --experimental-strip-types`, whose ESM resolver needs a real extension on every
   import specifier — which the app's tsconfig forbids. Same rule as `logic.ts`; see its
   header. Everything that needs the dictionary returns a KEY, never a sentence. */

/* ------------------------------------------------------------------ the four parameters */

/** What we send to the speech-to-text API, in the order the UI lists them.

    `mp3_16k` is what the product does today (`services/audio.py::to_stt_format` always
    converts to mono 16 kHz MP3). 16 kHz is the rate the model expects and is not the suspect;
    the LOSSY step is, because a codec at that rate is exactly what blurs the consonant that
    separates წ from თ. The three lossless options exist to take that variable off the table. */
export const AUDIO_FORMATS = ['original', 'flac_full', 'flac_16k', 'wav_16k', 'mp3_16k'] as const;
export type AudioFormat = (typeof AUDIO_FORMATS)[number];

export function isAudioFormat(v: unknown): v is AudioFormat {
  return typeof v === 'string' && (AUDIO_FORMATS as readonly string[]).includes(v);
}

export interface TranscriptionSettings {
  /** ISO-639-1/3, or `null` for "detect automatically". */
  language_code: string | null;
  diarize: boolean;
  keyterms: string[];
  audio_format: AudioFormat;
}

/** The subset that travels with ONE upload. Every key optional — that is the whole point. */
export type TranscriptionPatch = Partial<TranscriptionSettings>;

/** `GET /transcription/config` — the settings that apply here, plus where they came from. */
export interface TranscriptionConfig extends TranscriptionSettings {
  /** True when this level has nothing of its own and is inheriting wholesale. */
  is_default: boolean;
  /** The layer underneath, so the UI can say what it would fall back to. */
  inherited: TranscriptionSettings | null;
}

export const FIELDS = ['language_code', 'diarize', 'keyterms', 'audio_format'] as const;
export type Field = (typeof FIELDS)[number];

/** The resting position of every control BEFORE the server has answered.

    Cosmetic only, and it has to stay that way: nothing here is ever sent unless the person
    touched that control (see `overridePatch`), and the moment the real config arrives it
    replaces every untouched field (`reseed`). It is today's shipped behaviour rather than the
    deployment's configured default precisely so that a failed GET shows the product as it
    actually is instead of as we hope it will be. */
export const CODE_DEFAULTS: TranscriptionSettings = {
  language_code: null,
  diarize: true,
  keyterms: [],
  audio_format: 'mp3_16k',
};

/* ------------------------------------------------------------------ key terms */

/** The API's own limits. 1000 terms; each under 50 characters and at most 5 words; the
    characters `< > { } [ ] \` are rejected outright. +20% on the price of a transcription,
    which is why the control says so and is off until asked for. */
export const KEYTERM_MAX = 1000;
export const KEYTERM_CHARS = 50;
export const KEYTERM_WORDS = 5;

const BAD_CHARS = /[<>{}[\]\\]/;

/** One line per term, and a repeated term is not paid for twice. */
export function parseKeyterms(text: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of String(text ?? '').split(/\r?\n/)) {
    const term = raw.trim();
    if (!term || seen.has(term)) continue;
    seen.add(term);
    out.push(term);
  }
  return out;
}

export function keytermsText(list: readonly string[]): string {
  return list.join('\n');
}

function wordCount(term: string): number {
  return term.split(/\s+/).filter(Boolean).length;
}

/** A dictionary key and its variables, for the caller to translate. */
export interface Notice {
  key: string;
  vars?: Record<string, string | number>;
}

/** The first term the API would refuse, as the message that names it — or null.

    Checked here as well as on the server (where the single source of truth lives) because the
    alternative is uploading a 40 MB recording, waiting for it, and being told about a stray
    bracket. The wording is shared with the other two surfaces: same rule, same sentence,
    wherever you meet it. */
export function keytermsError(list: readonly string[]): Notice | null {
  if (list.length > KEYTERM_MAX) {
    return { key: 'wb.tr.keyterms.toomany', vars: { max: KEYTERM_MAX } };
  }
  for (const term of list) {
    // Bad characters first: a 60-character term full of brackets is refused for the brackets,
    // and telling someone to shorten it would send them round a loop that never ends.
    if (BAD_CHARS.test(term)) return { key: 'tr.keyterms.badchars', vars: { term } };
    if (term.length >= KEYTERM_CHARS || wordCount(term) > KEYTERM_WORDS) {
      return { key: 'tr.keyterms.toolong', vars: { term } };
    }
  }
  return null;
}

/* ------------------------------------------------------------------ reading the server */

function strOrNull(v: unknown): string | null | undefined {
  if (v === null) return null;
  if (typeof v !== 'string') return undefined;
  const s = v.trim();
  return s ? s : null;                 // '' from an unset <select> means "detect", not a code
}

/** A payload → settings, with anything unreadable falling back rather than crashing a card.

    Defensive because this shape crosses a version boundary: a server that has not been
    redeployed yet answers with fewer keys, and a panel that renders `undefined` as the chosen
    audio format is worse than one that renders the fallback. */
export function normaliseSettings(raw: unknown, fallback: TranscriptionSettings): TranscriptionSettings {
  const o = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const lang = strOrNull(o.language_code);
  return {
    language_code: lang === undefined ? fallback.language_code : lang,
    diarize: typeof o.diarize === 'boolean' ? o.diarize : fallback.diarize,
    keyterms: Array.isArray(o.keyterms)
      ? o.keyterms.filter((x): x is string => typeof x === 'string').map(x => x.trim()).filter(Boolean)
      : [...fallback.keyterms],
    audio_format: isAudioFormat(o.audio_format) ? o.audio_format : fallback.audio_format,
  };
}

export function normaliseConfig(raw: unknown): TranscriptionConfig {
  const o = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const settings = normaliseSettings(o, CODE_DEFAULTS);
  return {
    ...settings,
    // Anything but a literal `true` is "this level has its own settings": a missing flag must
    // not make an override claim to be inherited, which is the reading that loses data.
    is_default: o.is_default === true,
    inherited: o.inherited && typeof o.inherited === 'object'
      ? normaliseSettings(o.inherited, CODE_DEFAULTS)
      : null,
  };
}

/* ------------------------------------------------------------------ the draft */

/** What the person at the upload box has done to the inherited settings.

    `touched` is tracked separately from the values because "the same as inherited" and "not
    overridden" are different requests, and only the second one follows the workspace when the
    workspace changes. A control the person opened, looked at and left alone stays absent. */
export interface OverrideDraft {
  /** The "Override for this recording" switch. */
  on: boolean;
  touched: Partial<Record<Field, true>>;
  values: TranscriptionSettings;
}

function copy(s: TranscriptionSettings): TranscriptionSettings {
  return { ...s, keyterms: [...s.keyterms] };
}

export function newDraft(base: TranscriptionSettings | null): OverrideDraft {
  return { on: false, touched: {}, values: copy(base || CODE_DEFAULTS) };
}

/** The config arrived after the panel painted: move every UNTOUCHED control onto the real
    inherited value, and leave the person's own edits exactly where they put them. */
export function reseed(draft: OverrideDraft, base: TranscriptionSettings): OverrideDraft {
  const values = copy(base);
  if (draft.touched.language_code) values.language_code = draft.values.language_code;
  if (draft.touched.diarize) values.diarize = draft.values.diarize;
  if (draft.touched.keyterms) values.keyterms = [...draft.values.keyterms];
  if (draft.touched.audio_format) values.audio_format = draft.values.audio_format;
  return { ...draft, values };
}

export function setField<K extends Field>(
  draft: OverrideDraft, key: K, value: TranscriptionSettings[K],
): OverrideDraft {
  return {
    ...draft,
    touched: { ...draft.touched, [key]: true },
    values: { ...copy(draft.values), [key]: value },
  };
}

function sameTerms(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

/** True when this field would say nothing new. */
function unchanged(base: TranscriptionSettings, values: TranscriptionSettings, key: Field): boolean {
  if (key === 'keyterms') return sameTerms(base.keyterms, values.keyterms);
  return base[key] === values[key];
}

/** The `transcription` object to send with THIS upload, or null for "send nothing".

    Null rather than `{}` because the two are not the same request: an empty object is still
    an override, and a backend is entitled to read it as one. Nothing sent is the inheritance
    working. */
export function overridePatch(
  draft: OverrideDraft, base: TranscriptionSettings | null,
): TranscriptionPatch | null {
  if (!draft.on) return null;
  const take = (k: Field) => !!draft.touched[k] && !(base && unchanged(base, draft.values, k));
  const out: TranscriptionPatch = {};
  if (take('language_code')) out.language_code = draft.values.language_code;
  if (take('diarize')) out.diarize = draft.values.diarize;
  if (take('keyterms')) out.keyterms = [...draft.values.keyterms];
  if (take('audio_format')) out.audio_format = draft.values.audio_format;
  return Object.keys(out).length ? out : null;
}

/** What this upload will actually be transcribed with — the inherited settings with the
    override laid over them. This is the line the collapsed panel shows. */
export function effective(
  base: TranscriptionSettings | null, patch: TranscriptionPatch | null,
): TranscriptionSettings {
  return { ...copy(base || CODE_DEFAULTS), ...(patch || {}) };
}

/* ------------------------------------------------------------------ languages */

/** The codes offered in the picker.

    Not every language Scribe accepts — a 99-row dropdown is a worse control than a 45-row one
    — but every one this product plausibly meets, with the three the UI itself speaks pinned
    to the top. A code that arrives from the server and is NOT in this list is still shown
    (see `languageOptions`): a workspace default the picker cannot represent would silently
    reset itself the first time somebody opened the panel. */
export const PINNED_LANGUAGES = ['ka', 'en', 'ru'] as const;

export const LANGUAGES: readonly string[] = [
  ...PINNED_LANGUAGES,
  'hy', 'az', 'tr', 'uk', 'be', 'kk', 'ar', 'he', 'fa', 'de', 'fr', 'es', 'it', 'pt', 'nl',
  'pl', 'ro', 'el', 'bg', 'sr', 'hr', 'cs', 'sk', 'sl', 'hu', 'sv', 'nb', 'da', 'fi', 'et',
  'lv', 'lt', 'hi', 'ur', 'bn', 'zh', 'ja', 'ko', 'vi', 'th', 'id', 'ms',
];

/** The list to render: the curated codes, plus whatever the server already chose. */
export function languageOptions(current: string | null): string[] {
  const out = [...LANGUAGES];
  if (current && !out.includes(current)) out.push(current);
  return out;
}

function capFirst(s: string): string {
  // Several locales (ru, de) hand back a lower-case language name; a dropdown of lower-case
  // options beside "Detect automatically" reads as broken. `slice(1)` is safe after
  // `charAt(0)`: both operate on UTF-16 units and only the first unit is being replaced.
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/** A code as the language's name, in the language the UI is currently in.

    Three sources, in order: the app's own dictionary for the three it speaks (so Georgian
    reads exactly as it does in the language switcher), then ICU via `Intl.DisplayNames`, then
    the raw code. `translate` answers with the key itself on a miss, which is how the first
    step is tested for. */
export function languageLabel(
  code: string, uiLang: string, t: (key: string) => string,
): string {
  const key = `lang.${code.toLowerCase().slice(0, 2)}`;
  const named = t(key);
  if (named && named !== key) return named;
  try {
    const dn = new Intl.DisplayNames([uiLang], { type: 'language' });
    const label = dn.of(code);
    if (label && label !== code) return capFirst(label);
  } catch {
    /* An engine without DisplayNames, or a code ICU refuses to parse. */
  }
  return code;
}
