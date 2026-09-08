/* The four transcription parameters, as data — no React in this file.
   ==================================================================
   Speech-to-text is the first link in the chain: the transcript feeds the analysis, the
   fact-check and the rubric score, so a misheard word does not stay a misheard word — it
   becomes a compliance verdict. These four settings are what the workspace gets to say about
   how that first link is made:

     language_code   name the language, or let the model detect it
     diarize         label who is speaking (what splits a call into turns)
     keyterms        bias recognition towards words it keeps getting wrong
     audio_format    what we actually upload, lossless or not

   They resolve down a chain — code defaults ← the deployment default ← this workspace ←
   one recording — and each level only states what it changes. This module is the workspace
   level's half of that: reading a server reply defensively, turning it into a form and back,
   and answering whether what is on screen may be sent.

   It is separate from `TranscriptionTab.tsx` because it is the part worth testing without a
   browser (`lib/__tests__/workspaceTranscription.test.mts`), and because Node's type stripping
   runs `.ts` but not `.tsx`. */

/** The upload formats, ids exactly as the API names them. Order is the order they are offered:
    most faithful first, today's behaviour last. */
export const AUDIO_FORMATS = ['original', 'flac_full', 'flac_16k', 'wav_16k', 'mp3_16k'] as const;
export type AudioFormat = (typeof AUDIO_FORMATS)[number];

export const KEYTERMS_MAX = 1000;
/** Per the API: a term is under 50 characters and at most five words. */
export const KEYTERM_MAX_CHARS = 50;
export const KEYTERM_MAX_WORDS = 5;
/** `< > { } [ ] \` are rejected by the provider outright, so they are rejected here — with the
    term named — rather than turned into a 400 nobody can act on. */
const KEYTERM_BAD_CHARS = /[<>{}[\]\\]/;

export interface Settings {
  /** ISO-639-1/3, or null for "detect automatically". */
  language_code: string | null;
  diarize: boolean;
  keyterms: string[];
  audio_format: AudioFormat;
}

/** What `GET /transcription/config` answers: the EFFECTIVE settings for this workspace, plus
    whether they are its own (`is_default: true` = it has none and is inheriting) and the layer
    underneath, so the page can show what "back to inherited" would mean. `formats` is the list
    the deployment can actually produce and `can_edit` is the server's own answer to
    `may_configure_workspace`. */
export interface ConfigReply extends Partial<Record<keyof Settings, unknown>> {
  is_default?: unknown;
  inherited?: unknown;
  formats?: unknown;
  can_edit?: unknown;
}

/* The last-resort values, used only when the server tells us nothing at all about a field.

   `mp3_16k` and `diarize: true` are not a preference — they are what the backend does TODAY
   (`services/audio.py::to_stt_format` always converts to mono 16 kHz mp3, and
   `elevenlabs.transcribe()` hardcodes diarize). The deployment default is the backend's to
   decide and it arrives in the reply; guessing a different one here would put a value on
   screen that the pipeline is not using. */
export const FALLBACK: Settings = {
  language_code: null,
  diarize: true,
  keyterms: [],
  audio_format: 'mp3_16k',
};

const isFormat = (v: unknown): v is AudioFormat =>
  typeof v === 'string' && (AUDIO_FORMATS as readonly string[]).includes(v);

/** Read one layer of settings out of whatever the server sent.

    Deliberately forgiving in the same way the backend's `_as_str_list` is: a field that is
    missing, null or the wrong type falls back to `base` rather than rendering a blank control
    or crashing the tab. `base` is the layer underneath — reading the effective settings against
    the inherited ones means an unknown `audio_format` shows what would actually be used rather
    than a hardcoded guess. */
export function readSettings(raw: unknown, base: Settings = FALLBACK): Settings {
  const d = (raw && typeof raw === 'object') ? raw as Record<string, unknown> : {};
  const lang = d.language_code;
  return {
    // '' and null both mean "detect automatically"; anything else is a code we pass through
    // even if it is not one of the three the product speaks, because the deployment default
    // may legitimately name a fourth.
    language_code: typeof lang === 'string' && lang.trim() ? lang.trim()
      : (lang === null || lang === '' ? null : base.language_code),
    diarize: typeof d.diarize === 'boolean' ? d.diarize : base.diarize,
    keyterms: Array.isArray(d.keyterms)
      ? d.keyterms.filter((x): x is string => typeof x === 'string').map(x => x.trim()).filter(Boolean)
      : base.keyterms,
    audio_format: isFormat(d.audio_format) ? d.audio_format : base.audio_format,
  };
}

/** The formats to OFFER: the ones this deployment says it can produce, in fidelity order, and
    only those this UI has words for. Falls back to all five when the server names none — an
    older build that does not send the list must not leave the control with no options. */
export function readFormats(raw: unknown): AudioFormat[] {
  const list = Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : [];
  const known = AUDIO_FORMATS.filter(f => list.includes(f));
  return known.length ? known : [...AUDIO_FORMATS];
}

export interface Config {
  /** What this workspace's transcriptions actually use right now. */
  effective: Settings;
  /** The layer underneath — what "back to inherited" would restore. */
  inherited: Settings;
  /** True when the workspace has nothing of its own and is inheriting every field. */
  isDefault: boolean;
  /** What this deployment can produce. */
  formats: AudioFormat[];
  /** The server's own authority answer, or null when it did not say. */
  canEdit: boolean | null;
}

export function readConfig(raw: unknown): Config {
  const d = (raw && typeof raw === 'object') ? raw as ConfigReply : {};
  const inherited = readSettings(d.inherited);
  return {
    // The effective layer is read AGAINST the inherited one: a field the server left out of
    // the top level is, by the contract's own definition, the inherited value.
    effective: readSettings(d, inherited),
    inherited,
    isDefault: d.is_default === true,
    formats: readFormats(d.formats),
    // Only an explicit false closes the form. A reply without the field is an older server,
    // not a refusal, and the page's own owner predicate still applies either way.
    canEdit: typeof d.can_edit === 'boolean' ? d.can_edit : null,
  };
}

/* ---------------------------------------------------------------- the form

   The form holds the key terms as RAW TEXT, not as a parsed array. Parsing on every keystroke
   would delete the blank line someone is typing into and fight the cursor; the array is derived
   where it is needed (the count, the validation, the payload). Same shape as the bot tab's
   escalation keywords. */

export interface Draft {
  language: string;            // '' = detect automatically
  diarize: boolean;
  keyterms: string;            // one per line
  audio_format: AudioFormat;
}

export function toDraft(s: Settings): Draft {
  return {
    language: s.language_code || '',
    diarize: s.diarize,
    keyterms: s.keyterms.join('\n'),
    audio_format: s.audio_format,
  };
}

/** One term per line, trimmed, blank lines dropped. */
export function parseKeyterms(text: string): string[] {
  return text.split('\n').map(x => x.trim()).filter(Boolean);
}

export function fromDraft(d: Draft): Settings {
  return {
    language_code: d.language.trim() || null,
    diarize: d.diarize,
    keyterms: parseKeyterms(d.keyterms),
    audio_format: d.audio_format,
  };
}

/* ---------------------------------------------------------------- validation

   The authority is the backend — one place, and its message names the offending field. This is
   the same rule stated in the visitor's own language before a round trip, so a typo in term 84
   of 200 is pointed at rather than reported as a 400.

   A problem is returned as an i18n KEY plus its variables rather than as a rendered sentence,
   so the module stays free of a translator and the caller does `t(p.key, p.vars)`. */

export interface Problem {
  /** The offending field, for the caller to focus or highlight. */
  field: keyof Settings;
  key: string;
  vars: Record<string, string | number>;
}

const words = (term: string) => term.split(/\s+/).filter(Boolean).length;
/** Count code points, not UTF-16 units: one emoji is one character to a person and to the API. */
const chars = (term: string) => Array.from(term).length;

/** The first problem with what is on screen, or null when it may be sent. */
export function validate(s: Settings): Problem | null {
  if (s.keyterms.length > KEYTERMS_MAX) {
    // There is no separate "too many" sentence — the count line IS the sentence, and read as
    // "1002 of 1000 terms" it says exactly what is wrong.
    return {
      field: 'keyterms',
      key: 'tr.keyterms.count',
      vars: { n: s.keyterms.length, max: KEYTERMS_MAX },
    };
  }
  for (const term of s.keyterms) {
    if (KEYTERM_BAD_CHARS.test(term)) {
      return { field: 'keyterms', key: 'tr.keyterms.badchars', vars: { term } };
    }
    if (chars(term) >= KEYTERM_MAX_CHARS || words(term) > KEYTERM_MAX_WORDS) {
      return { field: 'keyterms', key: 'tr.keyterms.toolong', vars: { term } };
    }
  }
  return null;
}
