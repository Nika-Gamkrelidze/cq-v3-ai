'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AudioPlayer, type PlayerHandle } from '@/components/AudioPlayer';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import type { Voice, VoicesPayload } from './api';
import styles from './console.module.css';
import { CheckRow, Msg, type Note } from './parts';

/* Pre-listen every voice, then pick which ones a customer sees.

   `/admin/voices` is the UNFILTERED list on purpose: the public `/voices` is what this page
   curates, so an operator auditioning a voice they are about to hide has to be able to hear it.
   SYSTEM DEFAULTS (the shared Georgian voice among them) are always on and their checkbox is
   disabled — the Georgian TTS path depends on that voice existing, and an operator who could
   untick it would break Georgian speech for everyone with no error to explain it. */

export default function VoicesTab({
  epoch, onSaved,
}: {
  /** Bumped by the Integrations tab after a key change — a new key may be a new account. */
  epoch: number;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const [voices, setVoices] = useState<Voice[]>([]);
  const [missing, setMissing] = useState<Set<string>>(new Set());
  const [restrict, setRestrict] = useState(false);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);

  const player = useRef<PlayerHandle>(null);
  // Which url the one player is pointed at, so a second click on the same row toggles it
  // instead of reloading it from the start.
  const current = useRef<string | null>(null);

  const apply = useCallback((d: VoicesPayload) => {
    setVoices(Array.isArray(d.voices) ? d.voices : []);
    setMissing(new Set(d.missing || []));
    setRestrict(d.mode === 'allowlist');
  }, []);

  const load = useCallback(async () => {
    setNote(null);
    try {
      const d = await adminGet<VoicesPayload>('/admin/voices');
      apply(d);
      // `error` is reported IN BAND: the route still returns the system voices and the stored
      // allowlist when ElevenLabs is unreachable, so the panel renders and says why it is thin.
      if (d.error) setNote({ kind: 'err', text: `${t('v.loadfail')} ${d.error}` });
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: t('v.loadfail') });
    }
  }, [apply, t]);

  /* On mount, and again whenever Integrations reports a key change. Through a ref, so that a
     language switch — which gives `load` a new identity via `t` — does not re-fetch the list. */
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; });
  useEffect(() => { void loadRef.current(); }, [epoch]);

  const play = (v: Voice) => {
    if (!v.preview_url) return;
    if (current.current === v.preview_url) { player.current?.toggle(); return; }
    current.current = v.preview_url;
    // `own: false` — the url belongs to ElevenLabs, not to us; there is no object URL to revoke.
    player.current?.load(v.preview_url, 'preview.mp3', { own: false });
  };

  const toggle = (id: string, on: boolean) =>
    setVoices(list => list.map(v => (v.voice_id === id ? { ...v, selected: on } : v)));

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return voices;
    return voices.filter(v =>
      (v.name || '').toLowerCase().includes(needle) || (v.category || '').toLowerCase().includes(needle));
  }, [voices, q]);

  /* Counted over what is ON SCREEN, not over the whole list — the legacy count reads
     `voicesList.querySelectorAll('.v-tick:checked')`, i.e. the rendered rows. It sits in the
     search row and answers "how many of these matches are ticked", which is the question being
     asked while working through a filter. With no filter, which is how the panel opens, it is
     the total. */
  const selectedCount = shown.filter(v => v.selected).length;

  const save = async () => {
    setNote(null);
    // System voices are never sent: the server adds them back, and listing them would let a
    // stale page pin a shared voice id into one deployment's allowlist.
    const ids = voices.filter(v => v.selected && !v.system).map(v => v.voice_id);
    const mode = restrict ? 'allowlist' : 'all';
    if (mode === 'allowlist' && !ids.length) {
      setNote({ kind: 'err', text: t('v.pickone') });
      return;
    }
    setBusy(true);
    try {
      const d = await adminSend<VoicesPayload>('PUT', '/admin/voices', { mode, voice_ids: ids });
      apply(d);
      onSaved();                       // the Integrations preview map may now be stale
      setNote({ kind: 'ok', text: t('toast.saved') });
      toast(t('toast.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: errText(e, t) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3>{t('adm.voicevis')}</h3>
      {/* marginLeft:0 so the ⓘ sits on the row's own 8px gap instead of adding its default
          6px to it. A <button> inside a <label> does not activate the label, so it cannot
          toggle the checkbox by accident. */}
      <CheckRow checked={restrict} onChange={setRestrict}>
        <>
          <span>{t('f.restrictvoices')}</span>
          <span className={styles.tipFlush}><Tip text={t('v.hint')} /></span>
        </>
      </CheckRow>

      <div className="row" style={{ marginTop: 10 }}>
        <div style={{ flex: 2 }}>
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder={t('v.search')}
            aria-label={t('v.search')}
          />
        </div>
        <div style={{ flex: 0, alignSelf: 'center' }} className="hint">
          <b>{selectedCount}</b> <span>{t('v.selected')}</span>
        </div>
      </div>

      {/* One shared bar for every preview — no second play bar. */}
      <div style={{ marginTop: 8 }}><AudioPlayer ref={player} /></div>

      <div style={{ marginTop: 8, maxHeight: '52vh', overflow: 'auto' }}>
        {!shown.length ? (
          <div className="empty">{t('kb.nomatch')}</div>
        ) : shown.map(v => (
          <div
            key={v.voice_id}
            className="inline"
            style={{
              gap: 10, padding: '7px 0', borderBottom: '1px solid var(--hairline)',
              flexWrap: 'wrap', rowGap: 4,
            }}
          >
            <input
              type="checkbox"
              checked={!!v.selected}
              disabled={!!v.system}
              onChange={e => toggle(v.voice_id, e.target.checked)}
              aria-label={v.name || v.voice_id}
            />
            {v.preview_url ? (
              <button className="ghost" type="button" onClick={() => play(v)} aria-label="Preview">▶</button>
            ) : (
              <span className="chip">{t('v.nopreview')}</span>
            )}
            <span style={{ flex: 1 }}>{v.name || v.voice_id}</span>
            <span className="pill">{v.category || ''}</span>
            <span className="pill" title={v.voice_id}>{(v.voice_id || '').slice(0, 8)}…</span>
            {v.system ? <span className="chip">{t('v.system')}</span> : null}
            {missing.has(v.voice_id) ? <span className="chip">{t('v.unavailable')}</span> : null}
          </div>
        ))}
      </div>

      <div className="actions">
        <button className="primary" type="button" onClick={save} disabled={busy}>
          {busy ? <span className="spinner" /> : t('btn.savesettings')}
        </button>
      </div>
      <Msg note={note} />
    </div>
  );
}
