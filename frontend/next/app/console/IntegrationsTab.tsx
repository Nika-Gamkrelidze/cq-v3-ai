'use client';
import { useEffect, useRef, useState } from 'react';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import { Msg, type Note } from './parts';
import TranscriptionCard from './TranscriptionCard';

/* The analysis prompt, the transcription defaults, and the capability test.

   The two provider keys and the three model fields that used to open this tab are GONE from
   it: they were the single-provider deployment settings, and the AI providers tab now holds
   them as named connections (the boot seed copied the old key and model into a default
   connection per capability, so nothing changed for a running deployment). What is left here is
   what is genuinely deployment-wide and not a connection's — the instructions every analysis is
   given, and the speech-to-text parameters — plus the probe, which now exercises whatever the
   DEFAULT connections are.

   ONE ROW PER CAPABILITY, NOT PER VENDOR, in the probe. A single green "ElevenLabs" row used to
   hide a key that could list voices but not transcribe: the capabilities carry separate
   permissions, separate model entitlements and separate credit balances, and a voice provider
   offers no way to read a key's own scopes. So each operation is actually performed, as cheaply
   as possible, and reported on its own line. That is also what the ⓘ beside the Test buttons
   warns about — every click really transcribes and really synthesises, and it costs credit. */

interface Settings {
  analysis_instructions?: string;
}

const CAP_LABEL: Record<string, string> = {
  database: 'cap.database', ffmpeg: 'cap.ffmpeg', elevenlabs_voices: 'cap.voices',
  elevenlabs_stt: 'cap.stt', elevenlabs_tts: 'cap.tts', elevenlabs_tts_ka: 'cap.ttska',
  embeddings: 'cap.embeddings', claude_analysis: 'cap.claude', claude_factcheck: 'cap.factcheck',
  claude_scoring: 'cap.scoring',
};
const CAP_ICON: Record<string, string> = { ok: '✅', warn: '⚠️', fail: '❌' };

interface Probe { level?: string; ok?: boolean; detail?: string; code?: string; scope?: string }
type TestResult = Record<string, unknown> & { order?: string[] };

export default function IntegrationsTab({ onOpenAi }: {
  /** Switch the console to the AI providers tab — the pointer's button. */
  onOpenAi: () => void;
}) {
  const { t } = useI18n();
  const [instructions, setInstructions] = useState('');
  const [note, setNote] = useState<Note | null>(null);
  const [testing, setTesting] = useState<'' | 'shallow' | 'deep'>('');
  const [test, setTest] = useState<TestResult | null>(null);
  const [testError, setTestError] = useState('');

  /* Never PUT a field that never loaded. Without this the first Save after a failed load sends
     an empty string and wipes the analysis instructions. */
  const loaded = useRef(false);

  const load = async () => {
    const d = await adminGet<Settings>('/admin/settings');
    setInstructions(d.analysis_instructions ?? '');
    loaded.current = true;
  };

  useEffect(() => { void load().catch(() => {}); }, []);

  const save = async () => {
    setNote(null);
    if (!loaded.current) { setNote({ kind: 'err', text: t('err.unavailable') }); return; }
    try {
      // ONLY the instructions. The route still accepts the legacy key and model fields, and
      // sending them back from here would overwrite the layer the registry now sits on top of.
      await adminSend('PUT', '/admin/settings', { analysis_instructions: instructions });
      await load();
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

  const order = test
    ? (Array.isArray(test.order) ? test.order : Object.keys(CAP_LABEL).filter(k => test[k]))
    : [];

  return (
    <>
      {/* Where the keys went. A short card rather than nothing: an operator who opens this tab
          looking for the Anthropic key box, as they have for a year, must not conclude it was
          lost. */}
      <div className="card">
        <h3>{t('adm.intkeys')}</h3>
        <p className="hint">{t('adm.ai.pointer')}</p>
        <div className="actions">
          <button className="ghost" type="button" onClick={onOpenAi}>{t('adm.ai.open')}</button>
        </div>
      </div>

      {/* Saved by its own route: `stt_model` on a connection names WHICH speech-to-text model
          runs, this names what that model is given to work with. */}
      <TranscriptionCard />

      <div className="card">
        <h3>{t('adm.instructions')}</h3>
        <textarea
          id="analysis_instructions"
          value={instructions}
          onChange={e => setInstructions(e.target.value)}
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
