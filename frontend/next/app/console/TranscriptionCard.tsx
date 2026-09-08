'use client';
import { useEffect, useRef, useState } from 'react';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { apiGetOrNull } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import {
  KEYTERM_MAX, checkKeyterms, defaultsPayload, formFromDefaults, formatIds, languageCodes,
  parseKeyterms, type TrDefaults, type TrForm,
} from './logic';
import { CheckRow, Msg, type Note } from './parts';

/* The deployment default for transcription — `GET/PUT /admin/transcription/defaults`.
   ==================================================================================
   It sits in Integrations, under Models & voice, because it belongs to the same question that
   card answers: what exactly is sent to the providers. It is a SEPARATE card with a separate
   Save because it is a separate route — a failed transcription save must not look like a lost
   API key, and vice versa.

   WHY THIS EXISTS AT ALL. Scribe heard the owner's Georgian "36 თვემდე" (36 MONTHS) as
   "36 წლამდე" (36 YEARS), and that transcript then fed fact-check and rubric scoring: one
   misheard word became a compliance verdict. Naming the language, separating the speakers,
   biasing the model toward the words a call centre actually says, and — the suspect the
   owner's own A/B test could not isolate — not putting the audio through a LOSSY conversion
   first are the four levers that change what the model hears. This card sets the bottom layer
   of the chain; a workspace and a single upload can each override it from above.

   The card is deliberately quiet about which format is "right". The default is the server's
   answer, verified against the real API, and this page shows and stores it — see
   `formFromDefaults` for why an absent value reads as today's mp3 rather than as an opinion. */

interface Language { code?: string; name?: string }

export default function TranscriptionCard() {
  const { t } = useI18n();
  const [form, setForm] = useState<TrForm>({
    language: '', diarize: true, keyterms: '', format: 'mp3_16k',
  });
  const [langs, setLangs] = useState<Language[]>([]);
  const [note, setNote] = useState<Note | null>(null);
  const [busy, setBusy] = useState(false);

  /* Same latch as the Integrations card above, for the same reason: a PUT built from a form
     that never loaded would write this build's placeholder over whatever the operator really
     had. Every transcription in the product runs through these four values. */
  const loaded = useRef(false);

  const set = <K extends keyof TrForm>(k: K, v: TrForm[K]) => setForm(f => ({ ...f, [k]: v }));

  useEffect(() => {
    let live = true;

    adminGet<TrDefaults>('/admin/transcription/defaults')
      .then(d => {
        if (!live) return;
        setForm(formFromDefaults(d));
        loaded.current = true;
      })
      .catch(e => {
        if (e instanceof SessionExpired || !live) return;
        // The server's own words first; a bare 404 (a server older than this console) falls
        // through to "could not load", which is exactly what happened.
        setNote({ kind: 'err', text: errText(e, t, 'tr.loadfail') });
      });

    /* The PUBLIC catalogue, with no credential — `scope: 'public'` sends nothing. It is the
       same list the TTS pages read and it takes no principal; `adminGet` would file a
       superadmin token against a route that has no use for one. A failure here is not worth a
       message: the picker still offers detect-automatically and whatever code is stored. */
    apiGetOrNull<Language[]>('/languages', { scope: 'public' })
      .then(rows => { if (live && Array.isArray(rows)) setLangs(rows); })
      .catch(() => {});

    return () => { live = false; };
    // `t` is deliberately not a dependency: re-running this effect on a language switch would
    // re-fetch and discard whatever the operator had typed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const terms = parseKeyterms(form.keyterms);
  const termError = checkKeyterms(terms);

  const save = async () => {
    setNote(null);
    if (!loaded.current) { setNote({ kind: 'err', text: t('err.unavailable') }); return; }
    // The server validates the same four fields in one place; this only spares the round trip
    // and points at the line that is wrong, which a server message cannot do as precisely.
    if (termError) { setNote({ kind: 'err', text: t(termError.key, termError.vars) }); return; }
    setBusy(true);
    try {
      const d = await adminSend<TrDefaults>('PUT', '/admin/transcription/defaults', defaultsPayload(form));
      /* Re-fill from the response only when it actually IS the settings — a route that answers
         `{"ok":true}` would otherwise reset the card to the fallbacks `formFromDefaults` uses
         for an empty object, which the operator would read as a save that lost their work. */
      if (d && typeof d === 'object' && ('audio_format' in d || 'diarize' in d || 'language_code' in d)) {
        setForm(formFromDefaults(d));
      }
      setNote({ kind: 'ok', text: t('tr.saved') });
      toast(t('tr.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: errText(e, t) });
    } finally {
      setBusy(false);
    }
  };

  /* The API is the source of truth for WHICH languages exist; the wording is ours. `lang.*`
     when we have it — otherwise the catalogue's English `name`, otherwise the bare code, which
     is what a language this build has never heard of degrades to rather than a blank row. */
  const langOptions = languageCodes(langs, form.language).map(code => {
    if (!code) return { value: '', label: t('tr.language.auto') };
    const key = `lang.${code}`;
    const tr = t(key);
    if (tr !== key) return { value: code, label: tr };
    return { value: code, label: langs.find(l => l.code === code)?.name || code };
  });

  /* Name AND one-line description in every option: these five differ by a trade-off (fidelity
     against upload size), not by a name, so an operator choosing between them needs the
     sentence at the moment they choose. The trigger ellipsises the tail once one is picked;
     the menu wraps it (`.cq-opt { white-space:normal }`). */
  const formatOptions = formatIds(form.format).map(id => {
    const name = t(`tr.format.${id}`);
    const desc = t(`tr.format.${id}.desc`);
    // An id this build does not know renders as itself rather than as the raw key names.
    if (name === `tr.format.${id}`) return { value: id, label: id };
    return { value: id, label: desc === `tr.format.${id}.desc` ? name : `${name} — ${desc}` };
  });

  const over = terms.length > KEYTERM_MAX;

  return (
    <div className="card">
      <h3>{t('tr.heading')}</h3>
      <p className="hint">{t('tr.lead')}</p>
      <p className="hint">{t('adm.transcription.desc')}</p>

      <div className="row" style={{ marginTop: 12 }}>
        <div className="w-sel">
          <label htmlFor="tr_language">
            <span>{t('tr.language')}</span>
            <Tip text={t('tr.language.hint')} />
          </label>
          <Select
            id="tr_language"
            value={form.language}
            onChange={v => set('language', v)}
            options={langOptions}
            ariaLabel={t('tr.language')}
          />
        </div>
        <div>
          <label htmlFor="tr_format">
            <span>{t('tr.format')}</span>
            <Tip text={t('tr.format.hint')} />
          </label>
          <Select
            id="tr_format"
            value={form.format}
            onChange={v => set('format', v)}
            options={formatOptions}
            ariaLabel={t('tr.format')}
          />
        </div>
      </div>

      {/* The ⓘ is the warning, not the label: switching this off is what silently costs the
          per-speaker analysis and the timeline lanes further down the pipeline. */}
      <CheckRow checked={form.diarize} onChange={v => set('diarize', v)} style={{ marginTop: 14 }}>
        <>
          <span>{t('tr.diarize')}</span>
          <Tip text={t('tr.diarize.hint')} />
        </>
      </CheckRow>

      <label htmlFor="tr_keyterms">
        <span>{t('tr.keyterms')}</span>
        <Tip text={t('tr.keyterms.hint')} />
      </label>
      <textarea
        id="tr_keyterms"
        value={form.keyterms}
        placeholder={t('tr.keyterms.ph')}
        style={{ minHeight: 90 }}
        onChange={e => set('keyterms', e.target.value)}
      />
      {/* The counter is always on, the cost note only once there is something to charge for —
          a standing "+20%" beside an empty box reads as a fee this deployment already pays. */}
      <div className={`hint${over ? ' err' : ''}`}>
        {t('tr.keyterms.count', { n: terms.length, max: KEYTERM_MAX })}
        {terms.length ? ` · ${t('tr.keyterms.cost')}` : ''}
      </div>
      {/* Live, while the offending line is still on screen and still being typed. Over-length
          is already shouting from the counter above, so it is not repeated here. */}
      {termError && !over ? (
        <div className="msg err">{t(termError.key, termError.vars)}</div>
      ) : null}

      <div className="actions">
        <button className="primary" type="button" onClick={save} disabled={busy || !!termError}>
          {busy ? <span className="spinner" /> : t('btn.save')}
        </button>
      </div>
      <Msg note={note} />
    </div>
  );
}
