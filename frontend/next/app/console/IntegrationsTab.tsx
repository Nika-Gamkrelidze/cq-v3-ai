'use client';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { AudioPlayer, type PlayerHandle } from '@/components/AudioPlayer';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { apiBase, scopedHeaders } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import type { PreviewCache, VoicesPayload } from './api';
import { Msg, type Note } from './parts';

/* Provider keys, models, the analysis prompt, and the capability test.

   ONE ROW PER CAPABILITY, NOT PER VENDOR. A single green "ElevenLabs" row used to hide a key
   that could list voices but not transcribe: the capabilities carry separate permissions,
   separate model entitlements and separate credit balances, and ElevenLabs offers no way to
   read a key's own scopes. So each operation is actually performed, as cheaply as possible, and
   reported on its own line. That is also what the ⓘ beside the Test buttons warns about — every
   click really transcribes and really synthesises, and it costs credit. */

const INT_FIELDS = ['llm_model', 'stt_model', 'tts_model', 'tts_voice_id', 'analysis_instructions'] as const;
type IntField = (typeof INT_FIELDS)[number];

interface Settings extends Partial<Record<IntField, string>> {
  anthropic_api_key_set?: boolean;
  anthropic_api_key_hint?: string;
  elevenlabs_api_key_set?: boolean;
  elevenlabs_api_key_hint?: string;
}

const MODELS = ['claude-opus-4-8', 'claude-sonnet-5', 'claude-haiku-4-5'];

const CAP_LABEL: Record<string, string> = {
  database: 'cap.database', ffmpeg: 'cap.ffmpeg', elevenlabs_voices: 'cap.voices',
  elevenlabs_stt: 'cap.stt', elevenlabs_tts: 'cap.tts', elevenlabs_tts_ka: 'cap.ttska',
  embeddings: 'cap.embeddings', claude_analysis: 'cap.claude', claude_factcheck: 'cap.factcheck',
  claude_scoring: 'cap.scoring',
};
const CAP_ICON: Record<string, string> = { ok: '✅', warn: '⚠️', fail: '❌' };

interface Probe { level?: string; ok?: boolean; detail?: string; code?: string; scope?: string }
type TestResult = Record<string, unknown> & { order?: string[] };

export default function IntegrationsTab({
  previews, onKeysChanged,
}: {
  previews: RefObject<PreviewCache>;
  onKeysChanged: () => void;
}) {
  const { t } = useI18n();
  const [fields, setFields] = useState<Record<IntField, string>>({
    llm_model: '', stt_model: '', tts_model: '', tts_voice_id: '', analysis_instructions: '',
  });
  const [anthropicKey, setAnthropicKey] = useState('');
  const [elevenKey, setElevenKey] = useState('');
  const [anthropicHint, setAnthropicHint] = useState('');
  const [elevenHint, setElevenHint] = useState('');
  const [note, setNote] = useState<Note | null>(null);
  const [testing, setTesting] = useState<'' | 'shallow' | 'deep'>('');
  const [test, setTest] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState('');

  /* Never PUT fields that never loaded. Without this the first Save after a failed load sends
     five empty strings and wipes the model, the voice and the analysis instructions. */
  const loaded = useRef(false);
  const player = useRef<PlayerHandle>(null);

  const set = (k: IntField, v: string) => setFields(f => ({ ...f, [k]: v }));

  const load = async () => {
    const d = await adminGet<Settings>('/admin/settings');
    setFields({
      llm_model: d.llm_model ?? '',
      stt_model: d.stt_model ?? '',
      tts_model: d.tts_model ?? '',
      tts_voice_id: d.tts_voice_id ?? '',
      analysis_instructions: d.analysis_instructions ?? '',
    });
    loaded.current = true;
    setAnthropicHint(d.anthropic_api_key_set ? `Key set (${d.anthropic_api_key_hint}).` : 'No key set.');
    setElevenHint(d.elevenlabs_api_key_set ? `Key set (${d.elevenlabs_api_key_hint}).` : 'No key set.');
  };

  useEffect(() => { void load().catch(() => {}); }, []);

  const save = async () => {
    setNote(null);
    if (!loaded.current) { setNote({ kind: 'err', text: t('err.unavailable') }); return; }
    const patch: Record<string, string> = { ...fields };
    // A key is sent only when one was TYPED. The routes never return the stored key, so an
    // empty box means "leave it alone" — sending it would clear a working credential.
    if (anthropicKey.trim()) patch.anthropic_api_key = anthropicKey.trim();
    if (elevenKey.trim()) patch.elevenlabs_api_key = elevenKey.trim();
    try {
      await adminSend('PUT', '/admin/settings', patch);
      setAnthropicKey('');
      setElevenKey('');
      await load();
      // A new ElevenLabs key may be a different ACCOUNT, so the voice list and the preview map
      // this page caches are both stale — the console tells the Voices tab to reload.
      onKeysChanged();
      setNote({ kind: 'ok', text: t('toast.saved') });
      toast(t('toast.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: errText(e, t) });
    }
  };

  const runTest = async (deep: boolean) => {
    setTest(null);
    setTestError('');
    setTesting(deep ? 'deep' : 'shallow');
    try {
      setTest(await adminSend<TestResult>('POST', `/admin/test${deep ? '?deep=true' : ''}`));
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setTestError(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting('');
    }
  };

  /* The preview map MUST come from `/admin/voices` (unfiltered). The public `/voices` is
     curated now, so a hidden voice would miss the map and fall through to a paid POST /tts. */
  const fillPreviewMap = async () => {
    if (previews.current.fetched) return;
    try {
      const d = await adminGet<VoicesPayload>('/admin/voices');
      const map: Record<string, string> = {};
      for (const v of Array.isArray(d.voices) ? d.voices : []) {
        if (v.preview_url) map[v.voice_id] = v.preview_url;
      }
      previews.current = { fetched: true, map };
    } catch { /* leave the cache empty; the paid path below still works */ }
  };

  const preview = async () => {
    const vid = fields.tts_voice_id.trim();
    if (!vid) return;
    await fillPreviewMap();
    const url = previews.current.map[vid];
    // `own: false` — the cached preview_url belongs to the map, not to the player, and
    // revoking it on the next load would 404 the second play of the same voice.
    if (url) { player.current?.load(url, 'preview.mp3', { own: false }); return; }
    try {
      /* The PUBLIC tts route, with NO credential — `scope: 'public'` sends nothing, ever.
         That is deliberate and matches the legacy call: this is a two-word synthesis on the
         same surface a visitor uses, and sending `X-Admin-Token` here would file it under the
         superadmin principal on a public endpoint. It is a plain `fetch` because it is a POST
         that answers with audio, which `apiBlob` (GET only) does not cover. */
      const r = await fetch(`${apiBase()}/tts`, {
        method: 'POST',
        headers: scopedHeaders('public', undefined, { 'Content-Type': 'application/json' }),
        body: JSON.stringify({ text: 'Hello.', voice_id: vid }),
      });
      if (r.ok) player.current?.load(URL.createObjectURL(await r.blob()), 'preview.mp3');
    } catch { /* a failed preview is not worth a toast — the ▶ simply does nothing */ }
  };

  const order = test
    ? (Array.isArray(test.order) ? test.order : Object.keys(CAP_LABEL).filter(k => test[k]))
    : [];

  /* The configured model may not be one of the three this list knows about — an operator can
     set any id through the API or `.env`. Offering it keeps the picker showing the truth
     instead of an empty box that a Save would then write back as an empty model. */
  const modelOptions = (MODELS.includes(fields.llm_model) || !fields.llm_model
    ? MODELS
    : [fields.llm_model, ...MODELS]).map(m => ({ value: m, label: m }));

  return (
    <>
      <div className="card">
        <h3>{t('adm.intkeys')}</h3>
        <label htmlFor="anthropic_api_key">{t('f.anthropic')}</label>
        <input
          id="anthropic_api_key"
          type="password"
          placeholder="sk-ant-…"
          autoComplete="off"
          value={anthropicKey}
          onChange={e => setAnthropicKey(e.target.value)}
        />
        <div className="hint">{anthropicHint}</div>
        <label htmlFor="elevenlabs_api_key">{t('f.eleven')}</label>
        <input
          id="elevenlabs_api_key"
          type="password"
          autoComplete="off"
          value={elevenKey}
          onChange={e => setElevenKey(e.target.value)}
        />
        <div className="hint">{elevenHint}</div>
      </div>

      <div className="card">
        <h3>{t('adm.models')}</h3>
        <div className="row">
          <div>
            <label htmlFor="llm_model">{t('f.claudemodel')}</label>
            <Select
              id="llm_model"
              value={fields.llm_model}
              onChange={v => set('llm_model', v)}
              options={modelOptions}
              ariaLabel={t('f.claudemodel')}
            />
          </div>
          <div>
            <label htmlFor="stt_model">{t('f.sttmodel')}</label>
            <input
              id="stt_model"
              placeholder="scribe_v1"
              value={fields.stt_model}
              onChange={e => set('stt_model', e.target.value)}
            />
          </div>
        </div>
        <div className="row">
          <div>
            <label htmlFor="tts_model">{t('f.ttsmodel')}</label>
            <input
              id="tts_model"
              placeholder="eleven_multilingual_v2"
              value={fields.tts_model}
              onChange={e => set('tts_model', e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="tts_voice_id">{t('f.voiceid')}</label>
            <div className="inline">
              <input
                id="tts_voice_id"
                style={{ flex: 1 }}
                value={fields.tts_voice_id}
                onChange={e => set('tts_voice_id', e.target.value)}
              />
              <button className="ghost" type="button" onClick={preview} aria-label={t('btn.test')}>▶</button>
            </div>
            {/* ONE player, re-pointed by `load()`. Mounting a second would leave a second play
                bar on the page — a bug this app has already fixed once. */}
            <AudioPlayer ref={player} />
          </div>
        </div>
      </div>

      <div className="card">
        <h3>{t('adm.instructions')}</h3>
        <textarea
          id="analysis_instructions"
          value={fields.analysis_instructions}
          onChange={e => set('analysis_instructions', e.target.value)}
        />
        {/* The ⓘ rides in the actions row rather than on a label: what it warns about belongs
            to the two Test buttons, not to a field. `describes` gives them the same
            description, so a keyboard user hears the cost note on the control that spends. */}
        <div className="actions">
          <button className="primary" type="button" onClick={save}>{t('btn.savesettings')}</button>
          <button
            className="ghost" type="button" id="testInt"
            onClick={() => runTest(false)} disabled={!!testing}
          >
            {testing === 'shallow' ? <><span className="spinner" />{t('btn.test')}…</> : t('btn.testconn')}
          </button>
          <button
            className="ghost" type="button" id="testDeep"
            onClick={() => runTest(true)} disabled={!!testing}
          >
            {testing === 'deep' ? <><span className="spinner" />{t('btn.test')}…</> : t('btn.testdeep')}
          </button>
          <span style={{ alignSelf: 'center' }}>
            <Tip text={t('adm.testnote')} describes={['testInt', 'testDeep']} />
          </span>
        </div>
        <Msg note={note} />
        {testError ? <div className="msg err">{testError}</div> : null}
        {order.map(k => {
          const x = (test?.[k] || {}) as Probe;
          const level = x.level || (x.ok ? 'ok' : 'fail');
          return (
            <div className="test" key={k}>
              <b>{t(CAP_LABEL[k] || k)}</b> {CAP_ICON[level] || '❌'} {x.detail || ''}
              {/* A missing permission is the one failure an operator can fix in 30 seconds —
                  name the scope instead of leaving it buried in the message. */}
              {x.code === 'missing_permission' && x.scope
                ? <div className="hint">{t('cap.fixscope', { scope: x.scope })}</div>
                : null}
            </div>
          );
        })}
      </div>
    </>
  );
}
