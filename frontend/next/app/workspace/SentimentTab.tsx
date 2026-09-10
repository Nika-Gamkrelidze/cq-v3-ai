'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { useAutogrow } from '@/lib/autogrow';
import { failMessage, STALE, useWs } from './ctx';

/* SENTIMENT — how this workspace reads the mood of a call.
   =======================================================
   Sentiment has TWO independent halves and the whole design of this page follows from
   refusing to average them (see `services/sentiment.py`):

     the words   what was said, judged by the text model. Cross-lingual, which is why
                 Georgian works here at all.
     the voice   how it sounded — pitch, energy, timing — from the self-hosted tone sidecar.
                 Optional infrastructure, and the half that hears a caller who is calm but
                 furious.

   When the two DISAGREE that is the finding, not noise, so they are never collapsed into one
   number and this tab never offers a knob that would collapse them.

   Two things this tab exists to fix:

   1. **The settings had no home.** `enabled` and `guidance` were reachable only from inside
      the Analyse-a-call workbench, where the guidance box looked like a property of the
      panel rather than a workspace-wide rule. They are workspace configuration, like the
      rubric and transcription, so they belong beside those.

   2. **The voice half failed invisibly.** Prosody degrades to text-only and still answers
      200, by design — a missing tone model must never cost a tenant their transcript. The
      cost of that design is that a workspace whose voice tone was dead saw an empty column
      and no explanation anywhere in the product. `voice_tone` now comes back with the config
      and is reported here in words, including what the workspace can do about it (nothing,
      in most states — it is the operator's to fix, and saying so is the point). */

interface Cfg {
  enabled?: boolean;
  guidance?: string;
  voice_tone?: string;
  updated_at?: string | null;
}

/** The states `services/sentiment.py::status()` reports, worst to best. Anything unknown is
    treated as unknown rather than as working — an optimistic default here would recreate
    exactly the silence this panel exists to break. */
const VOICE_STATES = ['ok', 'warming', 'model_error', 'unreachable', 'disabled'] as const;

function voiceKind(state: string | undefined): { cls: string; key: string } {
  if (state === 'ok') return { cls: 'ready', key: 'ok' };
  if (state === 'warming') return { cls: 'notinkb', key: 'warming' };
  if (state && (VOICE_STATES as readonly string[]).includes(state)) {
    return { cls: 'error', key: state };
  }
  return { cls: 'notinkb', key: 'unknown' };
}

export function SentimentTab({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const canEdit = ws.canConfigure;
  const readonly = !canEdit;

  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [guidance, setGuidance] = useState('');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');
  const box = useRef<HTMLTextAreaElement>(null);
  useAutogrow(box);

  const load = useCallback(async () => {
    setErr('');
    const d = await ws.json<Cfg>('/sentiment/config');
    if (d === STALE) return;                 // the operator has moved on; render nothing
    if (d === null) { setErr(t('toast.error')); return; }
    setCfg(d);
    setEnabled(d.enabled !== false);
    setGuidance(d.guidance || '');
  }, [ws, t]);

  // Same load discipline as the other settings tabs: once when the tab is first shown, and
  // again when the workspace changes under it (`gen`), never on a language switch.
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; });
  const shown = useRef(false);
  useEffect(() => {
    if (!on) return;
    if (shown.current && !gen) return;
    shown.current = true;
    void loadRef.current();
  }, [on, gen]);

  const save = async () => {
    setSaving(true);
    setErr('');
    const r = await ws.send<Cfg>('PUT', '/sentiment/config', { enabled, guidance });
    setSaving(false);
    if (!r.ok) {
      const m = failMessage(r, t);
      setErr(m);
      toast(m, 'err');
      return;
    }
    // The PUT answers with the stored row but NOT with `voice_tone` (that rides on the GET),
    // so the sidecar pill is kept rather than blanked by a successful save.
    if (r.data) setCfg(prev => ({ ...prev, ...r.data }));
    toast(t('toast.saved'), 'ok');
  };

  const voice = voiceKind(cfg?.voice_tone);

  return (
    <>
      <div className="card">
        <h3>{t('snt.heading')}</h3>
        <p className="hint">{t('snt.intro')}</p>

        {err ? <div className="err">{err}</div> : null}

        <label className="inline" style={{ gap: 8, marginTop: 12 }}>
          <input
            type="checkbox" checked={enabled} disabled={readonly}
            onChange={e => setEnabled(e.target.checked)}
          />
          <span>{t('snt.enabled')}</span>
          <Tip text={t('snt.enabled.hint')} />
        </label>

        <div style={{ marginTop: 16 }}>
          <label htmlFor="snGuidance">
            <span>{t('snt.guidance')}</span>
            <Tip text={t('snt.guidance.hint')} />
          </label>
          <textarea
            id="snGuidance" ref={box} value={guidance} disabled={readonly}
            placeholder={t('snt.guidance.ph')}
            onChange={e => setGuidance(e.target.value)}
          />
          {/* Said plainly because it is a real coupling and not an obvious one: the same text
              steers the tone analyser behind the rubric's courtesy dimension, so a workspace
              editing this box is also moving a number on its scorecards. */}
          <div className="hint">{t('snt.guidance.shared')}</div>
        </div>

        {canEdit ? (
          <div className="actions">
            <button className="primary" type="button" onClick={save} disabled={saving}>
              {saving ? <><span className="spinner" /> {t('snt.saving')}</> : t('snt.save')}
            </button>
          </div>
        ) : <div className="hint" style={{ marginTop: 12 }}>{t('snt.readonly')}</div>}
      </div>

      <div className="card">
        <h3>{t('snt.halves')}</h3>
        <p className="hint">{t('snt.halves.intro')}</p>

        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <b>{t('snt.words')}</b>
            <span className="pill ready" style={{ marginLeft: 8 }}>{t('snt.voice.ok')}</span>
            <p className="hint">{t('snt.words.desc')}</p>
          </div>
          <div>
            <b>{t('snt.voice')}</b>
            <span className={`pill ${voice.cls}`} style={{ marginLeft: 8 }}>
              {t(`snt.voice.${voice.key}`)}
            </span>
            <p className="hint">{t('snt.voice.desc')}</p>
            {voice.key !== 'ok' ? (
              <p className="hint"><b>{t(`snt.voice.${voice.key}.what`)}</b></p>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}
