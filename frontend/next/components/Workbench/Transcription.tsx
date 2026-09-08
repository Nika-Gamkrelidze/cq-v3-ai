'use client';
/* "Transcription" — the per-recording end of the inheritance chain.
   ================================================================
   The same four settings appear on three surfaces: the operator console sets the deployment
   default, a workspace overrides that, and THIS panel overrides it for one upload. Same
   words, same controls, same validation in all three, or nobody can reason about which layer
   a call was actually transcribed with.

   Three decisions here that look like details and are not:

   * IT SHOWS WHAT IT WILL INHERIT, COLLAPSED. The resting state sends nothing, so the honest
     question a person has in front of the upload box is "what is going to happen to my file?"
     — not "what could I change?". The summary line answers it without being opened, and says
     whether the answer came from the workspace or from the deployment. When there IS an
     override the same line shows the override's own values, because a setting that changes
     the meaning of a compliance verdict must not be legible only to whoever clicks the
     triangle.
   * ONLY TOUCHED FIELDS TRAVEL. See `overridePatch` in `transcribe.ts`. A control the
     person opened and left alone is absent from the request, so the recording still follows
     the workspace.
   * IT RESETS WITH THE SOURCE. The override belongs to one file. `reset()` in the panel's
     parent drops it, which is what makes "Change" (pick a different recording) mean a clean
     slate rather than a setting that quietly outlives the file it was meant for. */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { apiGet } from '@/lib/session';
import type { Scope } from '@/lib/session';
import type { T } from './strings';
import {
  AUDIO_FORMATS, KEYTERM_MAX, effective, keytermsError, keytermsText, languageLabel,
  languageOptions, newDraft, normaliseConfig, overridePatch, parseKeyterms, reseed, setField,
  type Field, type Notice, type OverrideDraft, type TranscriptionConfig,
  type TranscriptionPatch,
  type TranscriptionSettings,
} from './transcribe';

/* ------------------------------------------------------------------ the hook */

export interface TranscriptionOverride {
  config: TranscriptionConfig | null;
  /** The GET did not answer. The controls still work; the panel simply stops CLAIMING to
      know what it would inherit, which is the one thing it must not guess at. */
  failed: boolean;
  draft: OverrideDraft;
  setDraft: (next: OverrideDraft) => void;
  /** The offending key term, as a dictionary key — or null. Blocks the upload. */
  error: Notice | null;
  /** What travels with this upload, or null when the inheritance is doing the work. */
  patch: TranscriptionPatch | null;
  /** Back to inheriting. Called by the panel's own button and by the workbench's reset. */
  clear: () => void;
}

/** Load the settings this surface would inherit, and hold the person's override over them.

    The fetch is on mount rather than on first open because the collapsed line quotes the
    inherited values — a panel that only learns them when expanded has nothing to say in the
    state it spends all its time in. A failure is silent: this is an ancillary control beside
    an upload button, and a toast on every workbench mount would train people to dismiss the
    ones that matter. The upload itself still reports its own 401. */
export function useTranscriptionOverride(scope: Scope): TranscriptionOverride {
  const [config, setConfig] = useState<TranscriptionConfig | null>(null);
  const [failed, setFailed] = useState(false);
  const [draft, setDraft] = useState<OverrideDraft>(() => newDraft(null));
  // Read inside the load without making it a dependency: re-running the fetch because
  // somebody ticked a checkbox would re-seed the panel underneath them.
  const draftRef = useRef(draft);
  draftRef.current = draft;

  useEffect(() => {
    const ac = new AbortController();
    (async () => {
      try {
        const raw = await apiGet<unknown>('/transcription/config', { scope, signal: ac.signal });
        if (ac.signal.aborted) return;
        const cfg = normaliseConfig(raw);
        setConfig(cfg);
        setFailed(false);
        setDraft(reseed(draftRef.current, cfg));
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return;
        setFailed(true);
      }
    })();
    return () => ac.abort();
  }, [scope]);

  /* `clear` is handed to the workbench's own `reset`, which is a `useCallback` the whole panel
     depends on — so it has to keep the SAME identity across a config load. Reading the base
     through a ref is what buys that; closing over `config` would rebuild `reset` (and every
     callback under it) the moment the GET answered. */
  const baseRef = useRef<TranscriptionSettings | null>(null);
  baseRef.current = config;
  const clear = useCallback(() => { setDraft(newDraft(baseRef.current)); }, []);

  const error = useMemo(
    () => (draft.on && draft.touched.keyterms ? keytermsError(draft.values.keyterms) : null),
    [draft],
  );
  // A draft the API would refuse sends NOTHING rather than sending the half of it that is
  // valid: the caller reads `patch` to build the request and `error` to refuse to make it.
  const patch = useMemo(() => (error ? null : overridePatch(draft, config)), [draft, config, error]);

  return { config, failed, draft, setDraft, error, patch, clear };
}

/** Put the override on a multipart upload — the ONE place that spells the wire format.

    A JSON string in a form field, because the body already has to be `multipart/form-data`
    (it is carrying a 40 MB recording) and multipart has no way to express a nested object;
    every field is a string. The backend reads `transcription: str | None = Form(default=None)`
    and parses it. A null patch appends NOTHING — an absent field is what "inherit" looks like
    on the wire, and an empty object is not the same request. */
export function appendTranscription(fd: FormData, patch: TranscriptionPatch | null): void {
  if (patch) fd.append('transcription', JSON.stringify(patch));
}

/* ------------------------------------------------------------------ the panel */

export interface TranscriptionPanelProps {
  t: T;
  /** The UI language, for naming the OTHER languages in the picker. */
  lang: string;
  state: TranscriptionOverride;
  /** An upload is in flight — the settings for it are already decided. */
  busy: boolean;
}

export function TranscriptionPanel({ t, lang, state, busy }: TranscriptionPanelProps) {
  const [open, setOpen] = useState(false);
  // Two surfaces mount the workbench and nothing stops a third from mounting two of them;
  // hand-written ids would silently point every label at the first copy's controls.
  const uid = useId();
  const { config, failed, draft, setDraft, error } = state;

  const on = draft.on;
  const values = draft.values;
  /* What would actually travel — which is NOT the same as the switch being on. A person can
     open the override, look at the four controls and change nothing, and this recording is
     still inheriting; a badge that said "overridden" there would be a lie about the one thing
     the panel exists to make legible.

     Computed from `overridePatch` rather than from the hook's `patch`, which is null while a
     key term is invalid: the summary should keep showing the language and format the person
     chose while they fix a bracket, not snap back to inherited for a second. The error line
     beside the textarea is what says the upload is blocked. */
  const sending = useMemo(() => overridePatch(draft, config), [draft, config]);
  const now = useMemo(() => effective(config, sending), [config, sending]);

  const set = <K extends 'language_code' | 'diarize' | 'keyterms' | 'audio_format'>(
    key: K, value: TranscriptionSettings[K],
  ) => setDraft(setField(draft, key, value));

  /* ---- the collapsed line ----

     What this file will be transcribed with, in the state the control spends its life in. A
     field is only quoted here when we actually KNOW it: after a failed GET the inherited
     values are unknown, so only the ones the person set themselves are shown, and a panel
     with nothing to say says nothing rather than repeating the code defaults as fact. */

  const known = (k: Field) => !failed || (!!sending && k in sending);
  const chips: string[] = [];
  if (known('language_code')) {
    chips.push(now.language_code ? languageLabel(now.language_code, lang, t) : t('tr.language.auto'));
  }
  if (known('audio_format')) chips.push(t(`tr.format.${now.audio_format}`));
  if (!now.diarize && known('diarize')) chips.push(t('wb.tr.diarize.off'));
  if (now.keyterms.length && known('keyterms')) {
    chips.push(t('tr.keyterms.count', { n: now.keyterms.length, max: KEYTERM_MAX }));
  }

  /* ---- the language picker ---- */

  const languages = useMemo(() => {
    const codes = languageOptions(values.language_code);
    const rows = codes.map(code => ({ value: code, label: languageLabel(code, lang, t) }));
    // The three the app itself speaks stay at the top, in the order `PINNED_LANGUAGES` lists
    // them (Georgian first — it is the language this whole feature exists for). Everything
    // else sorts by its NAME in the current UI language, which is the only order a reader can
    // scan; sorting by code would put "hy" between "hu" and "id" in an English list of names.
    const pinned = rows.slice(0, 3);
    const rest = rows.slice(3).sort((a, b) => a.label.localeCompare(b.label, lang || 'en'));
    return [{ value: '', label: t('tr.language.auto') }, ...pinned, ...rest];
  }, [values.language_code, lang, t]);

  const formats = useMemo(
    () => AUDIO_FORMATS.map(id => ({ value: id, label: t(`tr.format.${id}`) })),
    [t],
  );

  return (
    <details
      className="wb-tr"
      open={open}
      onToggle={e => setOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>
        <span className="wb-tr-title">{t('tr.heading')}</span>
        <span className={`pill wb-tr-badge${sending ? ' pending' : ''}`}>
          {sending ? t('tr.override.file') : t('tr.inherited')}
        </span>
        <span className="wb-tr-chips">{chips.join(' · ')}</span>
      </summary>

      <div className="wb-tr-body">
        <p className="hint wb-tr-lead">{t('tr.lead')}</p>
        <p className="hint wb-tr-from">
          {failed
            ? t('tr.loadfail')
            /* `is_default` is the server saying "this level has nothing of its own". At a
               workspace that means the deployment default is in force; false means the
               workspace set these itself. The account page has no workspace layer at all and
               so always lands on the first branch, which is the true sentence there too. */
            : t(config && !config.is_default ? 'tr.inherited.workspace' : 'tr.inherited.system')}
        </p>

        <label className="wb-tr-switch">
          <input
            type="checkbox" checked={on} disabled={busy}
            onChange={e => setDraft({ ...draft, on: e.target.checked })}
          />
          <span>{t('tr.override.file')}</span>
        </label>

        {on && (
          <div className="wb-tr-fields">
            <div className="wb-tr-field">
              <label htmlFor={`${uid}-lang`}>
                <span>{t('tr.language')}</span>
                <Tip text={t('tr.language.hint')} />
              </label>
              <Select
                id={`${uid}-lang`}
                value={values.language_code || ''}
                onChange={v => set('language_code', v || null)}
                options={languages}
                disabled={busy}
                ariaLabel={t('tr.language')}
              />
            </div>

            <div className="wb-tr-field">
              <label className="wb-tr-inline">
                <input
                  type="checkbox" checked={values.diarize} disabled={busy}
                  onChange={e => set('diarize', e.target.checked)}
                />
                <span>{t('tr.diarize')}</span>
              </label>
              {/* The one hint that is NOT behind a ⓘ. The others are advice; this one names a
                  consequence — no speaker lanes, no per-speaker analysis — and a consequence
                  a person only meets after the upload has to be on screen before it. It turns
                  amber once it is describing what is actually about to happen. */}
              <div className={`hint${values.diarize ? '' : ' wb-tr-warn'}`}>{t('tr.diarize.hint')}</div>
            </div>

            <div className="wb-tr-field">
              <label htmlFor={`${uid}-terms`}>
                <span>{t('tr.keyterms')}</span>
                <Tip text={t('tr.keyterms.hint')} />
              </label>
              <textarea
                id={`${uid}-terms`}
                className="wb-tr-terms"
                aria-label={t('tr.keyterms')}
                value={keytermsText(values.keyterms)}
                placeholder={t('tr.keyterms.ph')}
                disabled={busy}
                onChange={e => set('keyterms', parseKeyterms(e.target.value))}
              />
              <div className="hint wb-tr-terms-meta">
                <span>{t('tr.keyterms.count', { n: values.keyterms.length, max: KEYTERM_MAX })}</span>
                {values.keyterms.length ? <span>{t('tr.keyterms.cost')}</span> : null}
              </div>
              <div className="msg err wb-tr-err" aria-live="polite">
                {error ? t(error.key, error.vars) : ''}
              </div>
            </div>

            <div className="wb-tr-field">
              <label htmlFor={`${uid}-format`}>
                <span>{t('tr.format')}</span>
                <Tip text={t('tr.format.hint')} />
              </label>
              <Select
                id={`${uid}-format`}
                value={values.audio_format}
                onChange={v => set('audio_format', v as TranscriptionSettings['audio_format'])}
                options={formats}
                disabled={busy}
                ariaLabel={t('tr.format')}
              />
              {/* The five options differ by a trade nobody can guess from the name alone, and
                  `Select` shows one line per option — so the chosen one explains itself here. */}
              <div className="hint">{t(`tr.format.${values.audio_format}.desc`)}</div>
            </div>

            <div className="actions wb-tr-actions">
              <button
                type="button" className="ghost wb-tr-reset" disabled={busy}
                onClick={() => state.clear()}
              >
                {t('tr.reset')}
              </button>
              {/* No Save. There is nothing to save: the override rides along with the upload
                  itself. The switch being on with nothing changed is a legitimate state and
                  says so here, rather than letting the person believe they have changed
                  something because a panel is open. */}
              {!sending && !error ? <span className="hint wb-tr-noop">{t('tr.inherited')}</span> : null}
            </div>
          </div>
        )}
      </div>
    </details>
  );
}
