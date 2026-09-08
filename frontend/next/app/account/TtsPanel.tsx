'use client';
/* Text to speech, signed in.
   =========================
   The public page's form with the account's own credential on it, so the clip is stored
   against the account and comes back in History.

   The advanced panel below is MIRRORED with `index.html`'s copy — change both. Its arithmetic
   lives in `./advanced.ts` (pure, tested); this file is the form around it. WHICH controls
   exist depends on the model in play: Eleven v3 has a three-way stability preset and no style
   or speed at all, and only the Flash family enforces `language_code`. Nothing in the panel is
   sent until "Customise" is ticked — the default request is exactly what it was before the
   panel existed. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AudioPlayer, type PlayerHandle } from '@/components/AudioPlayer';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { apiGet, apiBase, scopedHeaders } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import {
  ADV_DEFAULTS, ADV_KEY, ADV_VOICE_KEYS, advBody, advCaps, advFmt, advLoadFrom,
  effectiveModel, isV3, type AdvState, type TtsModel,
} from './advanced';
import styles from './account.module.css';

interface LanguageRow { code: string; name?: string; note?: string; model?: string }
interface VoiceRow { voice_id: string; name?: string; category?: string; is_default?: boolean; preview_url?: string }

const LANG_KEY = 'cq_tts_lang';

export interface TtsPanelProps {
  onUnauthorized: () => void;
  /** A synthesis (or a preview that had to be synthesised) spends a quota unit; the profile
      meters and the tab list are read off `/limits`, so they are re-read after every one. */
  onSpend: () => void;
}

export function TtsPanel({ onUnauthorized, onSpend }: TtsPanelProps) {
  const { t } = useI18n();

  const [langs, setLangs] = useState<LanguageRow[]>([]);
  const [voices, setVoices] = useState<VoiceRow[]>([]);
  const [models, setModels] = useState<TtsModel[]>([]);
  const [modelsFailed, setModelsFailed] = useState(false);

  const [lang, setLang] = useState('');
  const [voice, setVoice] = useState('');
  const [text, setText] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  const [adv, setAdv] = useState<AdvState>(ADV_DEFAULTS);

  const player = useRef<PlayerHandle>(null);
  /** What the single player is currently pointed at, so a second click on the same clip is a
      pause rather than a reload. ONE player per surface — a second one leaves a second play
      bar on the page, which was a shipped bug. */
  const playing = useRef('');
  const loaded = useRef(false);

  /* ---- the remembered panel ---- */

  // localStorage is only readable in the browser: this page is prerendered at build time.
  useEffect(() => {
    let raw: string | null = null;
    try { raw = localStorage.getItem(ADV_KEY); } catch { /* private mode: defaults */ }
    setAdv(advLoadFrom(raw));
  }, []);

  const patchAdv = useCallback((patch: Partial<AdvState>) => {
    setAdv(prev => {
      const next = { ...prev, ...patch };
      try { localStorage.setItem(ADV_KEY, JSON.stringify(next)); } catch { /* nothing to remember with */ }
      return next;
    });
  }, []);

  /* ---- the catalogues ----
     Public routes, fetched WITHOUT a credential — the same three plain `fetch`es the legacy
     page makes. They describe what the server offers, not what this account may have. */

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;

    void (async () => {
      try {
        const list = await apiGet<LanguageRow[]>('/languages', { scope: 'public' });
        const rows = Array.isArray(list) ? list.filter(l => l && l.code) : [];
        setLangs(rows);
        let want = '';
        try { want = sessionStorage.getItem(LANG_KEY) || ''; } catch { /* not remembered */ }
        setLang(rows.some(l => l.code === want) ? want : (rows[0]?.code || ''));
      } catch { /* the form still works; the selector is just empty */ }
    })();

    void (async () => {
      try {
        const list = await apiGet<VoiceRow[]>('/voices', { scope: 'public' });
        setVoices(Array.isArray(list) ? list.filter(v => v && v.voice_id) : []);
      } catch { /* the default voice is still reachable: it is the empty option */ }
    })();

    void (async () => {
      try {
        const list = await apiGet<TtsModel[]>('/tts/models', { scope: 'public' });
        if (!Array.isArray(list)) throw new Error('bad list');
        setModels(list.filter(m => m && m.model_id));
      } catch {
        // Auto alone, said once as a hint rather than a toast: the form still works, the
        // visitor just cannot pick a model this session.
        setModelsFailed(true);
      }
    })();
  }, []);

  /* A remembered model that is no longer on offer (or a list that failed) falls back to Auto
     rather than to a value the dropdown cannot show. */
  useEffect(() => {
    if (!adv.model) return;
    if (modelsFailed || (models.length && !models.some(m => m.model_id === adv.model))) {
      patchAdv({ model: '' });
    }
  }, [models, modelsFailed, adv.model, patchAdv]);

  /* ---- what the current model accepts ---- */

  const langModel = useMemo(() => {
    const out: Record<string, string> = {};
    for (const l of langs) out[l.code] = l.model || '';
    return out;
  }, [langs]);

  const eff = effectiveModel(adv.model, lang, langModel);
  const caps = useMemo(() => advCaps(models, eff), [models, eff]);

  /* ---- options ---- */

  // The API is the source of truth for WHICH languages exist; the wording is ours. Its
  // name/note are English-only, so they are the fallback, not the display text.
  const langOptions = useMemo(() => langs.map(l => {
    const key = `lang.${l.code}`;
    const trn = t(key);
    return { value: l.code, label: trn === key ? (l.name || l.code) : trn };
  }), [langs, t]);

  const voiceOptions = useMemo(() => [
    { value: '', label: t('f.defaultvoice') },
    ...voices.map(v => ({
      value: v.voice_id,
      label: (v.name || v.voice_id || '')
        + (v.category ? ` (${v.category})` : '')
        + (v.is_default ? ` — ${t('f.voice.isdefault')}` : ''),
    })),
  ], [voices, t]);

  const modelOptions = useMemo(() => [
    { value: '', label: t('tts.model.auto') },
    ...models.map(m => ({ value: m.model_id, label: m.name || m.model_id })),
  ], [models, t]);

  /* Hung off the Voice label because it is a warning about the VOICE: on Georgian, moving off
     the default voice is what produces English-accented fake Georgian. The dictionary wins;
     the API's English `note` is the fallback for a language it has nothing to say about. */
  const noteKey = `lang.note.${lang}`;
  const noteTrn = t(noteKey);
  const langNote = noteTrn !== noteKey
    ? noteTrn
    : (langs.find(l => l.code === lang)?.note || '');

  /* ---- playback ---- */

  const playInto = useCallback((url: string, name: string, own?: boolean) => {
    if (playing.current === url) { player.current?.toggle(); return; }
    playing.current = url;
    player.current?.load(url, name, own === undefined ? undefined : { own });
  }, []);

  /** POST /tts returns AUDIO, not JSON, so it cannot go through `apiSend`; and `apiBlob` is a
      GET. One hand-rolled request, with the scope's headers on it so the clip is filed under
      this account. */
  const synthesise = useCallback(async (body: unknown, fallbackKey: string): Promise<Blob> => {
    const r = await fetch(`${apiBase()}/tts`, {
      method: 'POST',
      headers: scopedHeaders('user', undefined, { 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    if (r.status === 401) { onUnauthorized(); throw new Error(t('session.expired')); }
    if (!r.ok) {
      const d = await r.json().catch(() => ({})) as { detail?: string };
      throw new Error(d?.detail || t(fallbackKey));
    }
    return r.blob();
  }, [onUnauthorized, t]);

  async function onPreview() {
    setErr('');
    if (!voice) { setErr(t('tts.pickvoice')); return; }
    // The catalogue's own preview clip costs nothing — no synthesis, no quota unit. The
    // player does NOT own that URL: it belongs to ElevenLabs and is replayed on every click.
    const free = voices.find(v => v.voice_id === voice)?.preview_url;
    if (free) { playInto(free, 'preview.mp3', false); return; }
    setPreviewing(true);
    try {
      const blob = await synthesise(
        { text: 'Hello.', voice_id: voice, language_code: lang || undefined, ...advBody(adv, caps) },
        'tts.previewfail',
      );
      playInto(URL.createObjectURL(blob), 'preview.mp3');
    } catch (e) {
      setErr(e instanceof Error ? e.message : t('tts.previewfail'));
    } finally {
      setPreviewing(false);
      onSpend();
    }
  }

  async function onSynth() {
    const body = text.trim();
    setErr('');
    if (!body) { setErr(t('tts.needtext')); return; }
    setBusy(true);
    try {
      const blob = await synthesise(
        { text: body, voice_id: voice || undefined, language_code: lang || undefined, ...advBody(adv, caps) },
        'toast.error',
      );
      playInto(URL.createObjectURL(blob), 'speech.mp3');
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('toast.error');
      setErr(msg);
      toast(msg, 'err');
    } finally {
      setBusy(false);
      onSpend();
    }
  }

  /* ---- the advanced controls ---- */

  const rangeRow = (
    key: 'stability' | 'similarity' | 'style' | 'speed',
    i18n: string, min: number, max: number, step: number, hintKey?: string, ends?: boolean,
  ) => (
    <div className={styles.ttsRow} key={key}>
      <label htmlFor={`adv_${key}`}>{t(i18n)}</label>
      <input
        type="range" id={`adv_${key}`} min={min} max={max} step={step} value={adv[key]}
        onChange={e => patchAdv({ [key]: Number(e.target.value) } as Partial<AdvState>)}
      />
      <output className={styles.ttsVal} htmlFor={`adv_${key}`}>{advFmt(key, adv[key])}</output>
      {ends ? (
        <div className={styles.ttsEnds}>
          <span>{t('tts.stability.expressive')}</span>
          <span>{t('tts.stability.stable')}</span>
        </div>
      ) : null}
      {hintKey ? <div className="hint">{t(hintKey)}</div> : null}
    </div>
  );

  const checkRow = (key: 'boost' | 'forcelang', i18n: string, hintKey: string) => (
    <div className={`${styles.ttsRow} ${styles.ttsRowCheck}`} key={key}>
      <label className={styles.ttsCheck} htmlFor={`adv_${key}`}>
        <input
          type="checkbox" id={`adv_${key}`} checked={adv[key]}
          onChange={e => patchAdv({ [key]: e.target.checked } as Partial<AdvState>)}
        />
        <span>{t(i18n)}</span>
      </label>
      <div className="hint">{t(hintKey)}</div>
    </div>
  );

  return (
    <div className="card">
      <h3>{t('tts.heading')}</h3>
      <div className="row">
        <div>
          <label htmlFor="langSelect">{t('f.language')}</label>
          <Select
            id="langSelect" value={lang} options={langOptions} ariaLabel={t('f.language')}
            onChange={v => {
              setLang(v);
              try { sessionStorage.setItem(LANG_KEY, v); } catch { /* not remembered */ }
            }}
          />
        </div>
        <div>
          <label htmlFor="voiceSelect">
            <span>{t('f.voice')}</span>
            <Tip text={langNote} />
          </label>
          <div className="inline">
            <div style={{ flex: 1 }}>
              <Select id="voiceSelect" value={voice} options={voiceOptions} ariaLabel={t('f.voice')} onChange={setVoice} />
            </div>
            <button
              type="button" className="ghost" title={t('tts.previewtitle')} aria-label={t('tts.previewtitle')}
              disabled={previewing} onClick={() => void onPreview()}
            >
              {previewing ? '…' : '▶'}
            </button>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <label htmlFor="ttsText">{t('f.text')}</label>
        <textarea id="ttsText" placeholder={t('tts.text_ph')} value={text} onChange={e => setText(e.target.value)} />
        {/* The bracket-tag tip belongs to the MODEL, not to the Customise toggle: v3 reads
            [whispers] out of the text whether or not the sliders are in play. */}
        {isV3(eff) ? <div className="hint">{t('tts.v3tip')}</div> : null}
      </div>

      <details
        className={styles.ttsAdv} open={adv.open}
        onToggle={e => patchAdv({ open: (e.currentTarget as HTMLDetailsElement).open })}
      >
        <summary><span>{t('tts.adv')}</span></summary>
        <div className={styles.ttsAdvBody}>
          <div className={styles.ttsRow}>
            <label htmlFor="ttsModel">{t('tts.model')}</label>
            <Select
              id="ttsModel" value={adv.model} options={modelOptions} ariaLabel={t('tts.model')}
              onChange={v => patchAdv({ model: v })}
            />
            <div className="hint">{modelsFailed ? t('tts.models.loadfail') : t('tts.model.hint')}</div>
          </div>

          <div className={`${styles.ttsRow} ${styles.ttsRowCheck}`}>
            <label className={styles.ttsCheck} htmlFor="ttsCustom">
              <input
                type="checkbox" id="ttsCustom" checked={adv.custom}
                onChange={e => patchAdv({ custom: e.target.checked })}
              />
              <span>{t('tts.custom')}</span>
            </label>
            <div className="hint">{t('tts.custom.hint')}</div>
          </div>

          {adv.custom ? (
            <div className={styles.ttsControls}>
              {caps.presets ? (
                <div className={styles.ttsRow}>
                  <span className={styles.ttsLbl} id="adv_preset_lbl">{t('tts.stability')}</span>
                  <div className={styles.seg} role="group" aria-labelledby="adv_preset_lbl">
                    {([[0, 'tts.preset.creative'], [0.5, 'tts.preset.natural'], [1, 'tts.preset.robust']] as const).map(([v, key]) => (
                      <button
                        key={key} type="button" aria-pressed={adv.preset === v}
                        onClick={() => patchAdv({ preset: v })}
                      >
                        {t(key)}
                      </button>
                    ))}
                  </div>
                  <div className="hint">{t('tts.preset.hint')}</div>
                </div>
              ) : rangeRow('stability', 'tts.stability', 0, 100, 1, 'tts.stability.hint', true)}

              {rangeRow('similarity', 'tts.similarity', 0, 100, 1, 'tts.similarity.hint')}
              {caps.style ? rangeRow('style', 'tts.style', 0, 100, 1, 'tts.style.hint') : null}
              {caps.speaker_boost ? checkRow('boost', 'tts.speakerboost', 'tts.speakerboost.hint') : null}
              {caps.speed ? rangeRow('speed', 'tts.speed', 0.7, 1.2, 0.05) : null}
              {caps.language_code === 'enforced' ? checkRow('forcelang', 'tts.forcelang', 'tts.forcelang.hint') : null}

              <div className={`${styles.ttsRow} ${styles.ttsRowReset}`}>
                <button
                  type="button" className={styles.ttsLink}
                  // Only the voice values: the model pick and the Customise switch are
                  // choices, not settings.
                  onClick={() => patchAdv(Object.fromEntries(
                    ADV_VOICE_KEYS.map(k => [k, ADV_DEFAULTS[k]]),
                  ) as Partial<AdvState>)}
                >
                  {t('tts.reset')}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </details>

      <div className="actions">
        <button type="button" className="primary" disabled={busy} onClick={() => void onSynth()}>
          {busy ? <><span className="spinner" />{t('btn.synth')}…</> : t('btn.synth')}
        </button>
      </div>
      <div className="err" aria-live="polite">{err}</div>
      <AudioPlayer ref={player} />
    </div>
  );
}
