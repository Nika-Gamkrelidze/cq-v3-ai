'use client';
/* Score colour thresholds.
   =======================
   Two numbers decide three colours, so the form edits the two BOUNDARIES rather than three
   ranges: ranges let someone save a gap (or an overlap) that a real score falls into, and there
   is no colour for a score that belongs to no band. */

import { useCallback, useEffect, useState } from 'react';
import { toast } from '@/components/ui/Toast';
import { DEFAULT_BANDS } from '@/lib/aiShapes';
import { failMessage, STALE, useWs } from './ctx';

interface Bands { amber_from: number; green_from: number }

export function BandsCard() {
  const ws = useWs();
  const { t } = ws;
  const [amber, setAmber] = useState(String(DEFAULT_BANDS.amber_from));
  const [green, setGreen] = useState(String(DEFAULT_BANDS.green_from));
  const [gone, setGone] = useState(false);       // never offer Save over stale thresholds
  const [msg, setMsg] = useState<{ text: string; kind: '' | 'ok' | 'err' }>({ text: '', kind: '' });
  const [busy, setBusy] = useState<'' | 'save' | 'reset'>('');

  /* The two boxes ARE the state: the echoes beside them read the live input, so a half-typed
     threshold shows as what it will be rather than as what was last saved. */
  const apply = useCallback((b: Bands) => {
    setAmber(String(b.amber_from));
    setGreen(String(b.green_from));
  }, []);

  const load = useCallback(async () => {
    const d = await ws.json<Bands>('/scoring/bands');
    if (d === STALE) return;                     // thresholds belong to one workspace, like the rubric
    if (d === null) { setGone(true); setMsg({ text: t('tkb.loadfail'), kind: 'err' }); return; }
    setGone(false);
    apply({ amber_from: d.amber_from, green_from: d.green_from });
  }, [ws, t, apply]);

  useEffect(() => { void load(); }, [load]);

  const submit = async (which: 'save' | 'reset') => {
    setMsg({ text: '', kind: '' });
    setBusy(which);
    const r = which === 'save'
      ? await ws.send<Bands>('PUT', '/scoring/bands', { amber_from: Number(amber), green_from: Number(green) })
      // No password and no confirmation: this is two numbers anyone can retype, unlike the
      // rubric reset next to it, which discards work.
      : await ws.send<Bands>('POST', '/scoring/bands/reset');
    setBusy('');
    if (!r.ok) {
      const text = failMessage(r, t);
      setMsg({ text, kind: 'err' });
      toast(text, 'err');
      return;
    }
    apply({ amber_from: r.data!.amber_from, green_from: r.data!.green_from });
    setMsg({ text: t('tn.bands.saved'), kind: 'ok' });
    toast(t('tn.bands.saved'), 'ok');
  };

  if (gone) return null;

  return (
    <div className="card">
      <h3>{t('tn.bands.heading')}</h3>
      <p className="hint">{t('tn.bands.lead')}</p>
      <div className="bands-rows">
        <div className="bands-row">
          <span className="bands-dot bad" />
          <span className="bands-name">{t('tn.bands.red')}</span>
          <span className="bands-range">
            <span>{t('tn.bands.below')}</span>
            <input
              type="number" min={1} max={99} value={amber} aria-label={t('tn.bands.red')}
              onChange={e => setAmber(e.target.value)}
            />
          </span>
        </div>
        <div className="bands-row">
          <span className="bands-dot mid" />
          <span className="bands-name">{t('tn.bands.yellow')}</span>
          <span className="bands-range">
            <b>{amber || '—'}</b>
            <span>{t('tn.bands.upto')}</span>
            <input
              type="number" min={2} max={100} value={green} aria-label={t('tn.bands.yellow')}
              onChange={e => setGreen(e.target.value)}
            />
          </span>
        </div>
        <div className="bands-row">
          <span className="bands-dot good" />
          <span className="bands-name">{t('tn.bands.green')}</span>
          <span className="bands-range">
            <span>{t('tn.bands.from')}</span> <b>{green || '—'}</b> <span>{t('tn.bands.andup')}</span>
          </span>
        </div>
      </div>
      <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <button type="button" className="ghost" disabled={!!busy} onClick={() => void submit('reset')}>
          {busy === 'reset' ? <span className="spinner" /> : t('tn.bands.reset')}
        </button>
        <button type="button" className="primary" disabled={!!busy} onClick={() => void submit('save')}>
          {busy === 'save' ? <span className="spinner" /> : t('tn.bands.save')}
        </button>
      </div>
      <div className={`msg${msg.kind ? ' ' + msg.kind : ''}`} aria-live="polite">{msg.text}</div>
    </div>
  );
}
