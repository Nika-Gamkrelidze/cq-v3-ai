'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AudioPlayer, type PlayerHandle } from '@/components/AudioPlayer';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { apiBase, apiGetOrNull, scopedHeaders } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { AdvancedVoice } from './AdvancedVoice';
import {
  ADV_DEFAULTS, advBody, advCaps, advEffectiveModel, advLoad, advSave, isV3,
  type AdvState, type TtsModel,
} from './ttsAdvanced';

/* Text to speech — the public page's first tab.
   ============================================
   The three GETs behind it (`/languages`, `/voices`, `/tts/models`) take no principal at all,
   and the legacy page calls them with no headers; `scope: 'public'` is that, spelled out.

   `POST /tts` is different, twice over, and the difference is the security property this page
   exists to keep (docs/MIGRATION.md, "Deliberate decisions", first bullet):

     * GENERATING a clip runs at `scope: 'user'` — the registered-account Bearer and nothing
       else. A registered account has its own daily allowance and its own history, so its token
       rides on the request that spends them. An admin or tenant session in the same tab must
       NOT: their surfaces are the console and the portal, and this page has always run their
       requests as a guest. `authHeaders()` would send `X-Admin-Token` here and promote an
       operator to superadmin scope on the public surface.
     * PREVIEWING a voice runs at `scope: 'public'` — deliberately unsigned even for a signed-in
       account. The fallback preview is a six-character probe for a voice that ships no free
       sample, and charging it to the account would file a row of "Hello." clips in their
       history for every voice they auditioned. */

interface Language { code: string; name?: string; note?: string; model?: string }
interface Voice { voice_id: string; name?: string; category?: string; is_default?: boolean; preview_url?: string }

/** `POST /tts` answers with audio, not JSON, so it cannot go through `apiGet`/`apiSend`.
    The refusal path is JSON, and the server's own `detail` is preferred over our wording —
    it is specific, already about this request, and was written to be shown. */
async function synthesize(body: unknown, scope: 'user' | 'public', fallback: string, offline: string): Promise<Blob> {
  let r: Response;
  try {
    r = await fetch(`${apiBase()}/tts`, {
      method: 'POST',
      headers: scopedHeaders(scope, undefined, { 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
  } catch {
    // The legacy page rendered the browser's own "Failed to fetch" here. Same outcome, said in
    // the visitor's language — the one wording change on this path.
    throw new Error(offline);
  }
  if (!r.ok) {
    const d = await r.json().catch(() => ({})) as { detail?: string };
    throw new Error(d.detail || fallback);
  }
  return r.blob();
}

export function TtsPanel({ ready, signedIn, onSpent }: {
  /** The session has been read. Nothing loads before it, because the Georgian default below
      depends on it and a dropdown that re-picks itself a tick after paint is worse than one
      that arrives a tick late. */
  ready: boolean;
  signedIn: boolean;
  /** A clip was generated: the allowance banner has to count down. */
  onSpent: () => void;
}) {
  const { t } = useI18n();

  const [langs, setLangs] = useState<Language[]>([]);
  const [langCode, setLangCode] = useState('');
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState('');
  const [text, setText] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [models, setModels] = useState<TtsModel[]>([]);
  const [modelsFailed, setModelsFailed] = useState(false);
  const [adv, setAdv] = useState<AdvState>(ADV_DEFAULTS);

  const playerRef = useRef<PlayerHandle>(null);
  /* The URL the single player is currently pointed at. Re-pressing ▶ on the SAME clip toggles
     it rather than reloading — reloading would restart a preview the visitor was listening to.
     One player, re-pointed: a second one leaves a second play bar on the page, which is a bug
     this app has already fixed once (docs/MIGRATION.md, "Deliberate decisions"). */
  const curSrc = useRef<string | null>(null);
  const loaded = useRef(false);

  // The remembered panel lives in localStorage, which does not exist while this page is being
  // prerendered at build time — so it is read after mount, never during render.
  useEffect(() => { setAdv(advLoad()); }, []);
  const changeAdv = useCallback((next: AdvState) => { setAdv(next); advSave(next); }, []);

  /* ---- the three catalogues ----
     Guarded by a ref rather than by an abort-on-cleanup, and the difference matters under
     `reactStrictMode`: dev mounts, tears down and re-mounts every effect, so a version that
     cancelled its in-flight work on cleanup AND refused to start again would load nothing at
     all in development. These three GETs are idempotent reads of a public catalog — running
     them exactly once and applying whatever lands is the whole requirement. */
  useEffect(() => {
    if (!ready || loaded.current) return;
    loaded.current = true;

    void (async () => {
      const list = await apiGetOrNull<Language[]>('/languages', { scope: 'public' });
      const rows = Array.isArray(list) ? list : [];
      setLangs(rows);
      /* Georgian first, on the PUBLIC page. This is sold to Georgian call centres and Georgian
         is what the product is actually differentiated on — an English default made every
         visitor change the dropdown before hearing what we built. Only signed out, though: a
         workspace user arrives with their own habits. A language the visitor picked themselves
         outranks both and survives a reload of this tab. */
      let want = '';
      try { want = sessionStorage.getItem('cq_tts_lang') || ''; } catch { /* private mode */ }
      const has = (code: string) => rows.some(l => l.code === code);
      if (want && has(want)) setLangCode(want);
      else if (!signedIn && has('ka')) setLangCode('ka');
      else if (rows.length) setLangCode(rows[0].code);
    })();

    void (async () => {
      const list = await apiGetOrNull<Voice[]>('/voices', { scope: 'public' });
      if (Array.isArray(list)) setVoices(list);
    })();

    void (async () => {
      const list = await apiGetOrNull<TtsModel[]>('/tts/models', { scope: 'public' });
      const rows = Array.isArray(list) ? list.filter(m => m && m.model_id) : [];
      if (!Array.isArray(list)) setModelsFailed(true);
      setModels(rows);
      /* A remembered model that is no longer on offer — or a list that failed to load — falls
         back to Auto rather than to a value the dropdown cannot show. */
      setAdv(prev => {
        if (!prev.model || rows.some(m => m.model_id === prev.model)) return prev;
        const next = { ...prev, model: '' };
        advSave(next);
        return next;
      });
    })();
  }, [ready, signedIn]);

  const pickLang = (code: string) => {
    setLangCode(code);
    try { sessionStorage.setItem('cq_tts_lang', code); } catch { /* private mode */ }
  };

  /* ---- what the selected language and model imply ---- */

  const langModel = useMemo(() => {
    const map: Record<string, string> = {};
    for (const l of langs) map[l.code] = l.model || '';
    return map;
  }, [langs]);

  const effectiveModel = advEffectiveModel(adv.model, langCode, langModel);
  const caps = advCaps(effectiveModel, models);

  /* The API is the source of truth for WHICH languages exist; the wording is ours. Its `name`
     and `note` are English-only, so they are the FALLBACK, not the display text — otherwise the
     Georgian dropdown and its pronunciation note stay in English on a Georgian page. Resolved on
     every render rather than cached at load, so both follow a language switch. */
  const langOptions = langs.map(l => {
    const key = `lang.${l.code}`;
    const tr = t(key);
    return { value: l.code, label: tr === key ? (l.name || l.code) : tr };
  });

  /* The voice note hangs off the Voice LABEL rather than standing under the row, because it is
     a warning about the voice: on Georgian, moving off the default voice is what produces
     English-accented fake Georgian, and that is the worst output this app can hand a visitor.
     An empty string hides the ⓘ entirely rather than leaving a circle that opens nothing. */
  const noteKey = `lang.note.${langCode}`;
  const noteTr = t(noteKey);
  const voiceNote = noteTr !== noteKey
    ? noteTr
    : (langs.find(l => l.code === langCode)?.note || '');

  const voiceOptions = [
    { value: '', label: t('f.defaultvoice') },
    ...voices.map(v => ({
      value: v.voice_id,
      label: (v.name || v.voice_id || '')
        + (v.category ? ` (${v.category})` : '')
        + (v.is_default ? ` — ${t('f.voice.isdefault')}` : ''),
    })),
  ];

  const previewUrls = useMemo(() => {
    const map: Record<string, string> = {};
    for (const v of voices) if (v.preview_url) map[v.voice_id] = v.preview_url;
    return map;
  }, [voices]);

  const playInto = (url: string, name: string) => {
    if (curSrc.current === url) { playerRef.current?.toggle(); return; }
    curSrc.current = url;
    // `own` follows the URL: a blob: clip is ours to revoke when it is replaced, a
    // preview_url on ElevenLabs' CDN is not (and revoking a non-blob URL is a no-op anyway).
    playerRef.current?.load(url, name);
  };

  const preview = async () => {
    setErr('');
    if (!voiceId) { setErr(t('tts.pickvoice')); return; }
    const free = previewUrls[voiceId];
    if (free) { playInto(free, 'preview.mp3'); return; }
    setPreviewBusy(true);
    try {
      const blob = await synthesize(
        { text: 'Hello.', voice_id: voiceId, language_code: langCode || undefined, ...advBody(adv, caps) },
        'public', t('tts.previewfail'), t('err.unavailable'),
      );
      playInto(URL.createObjectURL(blob), 'preview.mp3');
    } catch (e) {
      setErr(e instanceof Error ? e.message : t('tts.previewfail'));
    } finally {
      setPreviewBusy(false);
    }
  };

  const synth = async () => {
    const body = text.trim();
    setErr('');
    if (!body) { setErr(t('tts.needtext')); return; }
    setBusy(true);
    try {
      const blob = await synthesize(
        {
          text: body,
          voice_id: voiceId || undefined,
          language_code: langCode || undefined,
          ...advBody(adv, caps),
        },
        'user', t('toast.error'), t('err.unavailable'),
      );
      playInto(URL.createObjectURL(blob), 'speech.mp3');
    } catch (e) {
      const msg = e instanceof Error ? e.message : t('toast.error');
      setErr(msg);
      toast(msg, 'err');
    } finally {
      setBusy(false);
      onSpent();
    }
  };

  return (
    <div className="card">
      <h3>{t('tts.heading')}</h3>
      <div className="row">
        <div>
          <label htmlFor="langSelect">{t('f.language')}</label>
          <Select id="langSelect" value={langCode} onChange={pickLang} options={langOptions} ariaLabel={t('f.language')} />
        </div>
        <div>
          <label htmlFor="voiceSelect">
            <span>{t('f.voice')}</span>
            <Tip text={voiceNote} />
          </label>
          <div className="inline">
            <div style={{ flex: 1 }}>
              <Select id="voiceSelect" value={voiceId} onChange={setVoiceId} options={voiceOptions} ariaLabel={t('f.voice')} />
            </div>
            <button
              className="ghost" type="button" title={t('tts.previewtitle')} aria-label={t('tts.previewtitle')}
              disabled={previewBusy} onClick={() => void preview()}
            >
              {previewBusy ? '…' : '▶'}
            </button>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <label htmlFor="ttsText">{t('f.text')}</label>
        <textarea id="ttsText" value={text} placeholder={t('tts.text_ph')} onChange={e => setText(e.target.value)} />
        {/* The bracket-tag tip belongs to the MODEL, not to the Customise toggle: v3 reads
            [whispers] out of the text whether or not the sliders are in play. */}
        {isV3(effectiveModel) ? <div className="hint">{t('tts.v3tip')}</div> : null}
      </div>

      <AdvancedVoice adv={adv} onChange={changeAdv} models={models} modelsFailed={modelsFailed} caps={caps} />

      <div className="actions">
        <button className="primary" type="button" disabled={busy} onClick={() => void synth()}>
          {busy ? <><span className="spinner" />{t('btn.synth')}…</> : t('btn.synth')}
        </button>
      </div>
      <div className="err">{err}</div>
      {/* ONE player for the whole page, re-pointed by ref. */}
      <AudioPlayer ref={playerRef} />
    </div>
  );
}
