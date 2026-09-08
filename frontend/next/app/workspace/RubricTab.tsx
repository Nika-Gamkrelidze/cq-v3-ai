'use client';
/* RUBRIC — the workspace's weighted scoring rubric, plus the score colour bands.
   ============================================================================
   Self-serve config: view-only for a member, editable by an owner — and by an operator acting
   on the workspace, who has the same authority over its settings as the account's own owner
   (the server's `may_configure_workspace`). */

import { useCallback, useEffect, useRef, useState } from 'react';
import { showModal } from '@/components/ui/Modal';
import { toast } from '@/components/ui/Toast';
import { useAutogrow } from '@/lib/autogrow';
import {
  normalizeWeights, validateRubric, weightsBalanced, weightTotal,
} from '@/lib/rubricMath';
import { BandsCard } from './BandsCard';
import { failMessage, STALE, useWs, type Sent, type T } from './ctx';
import { ImportBar, RubricImport, type Stage } from './rubricImport';

export interface Dim {
  key?: string;
  name: string;
  weight: number;
  description: string;
  guidance: string;
}

interface Config {
  dimensions?: Partial<Dim>[];
  rubric?: string;
  version?: number | null;
  is_default?: boolean;
}

const asDims = (raw: Partial<Dim>[] | undefined): Dim[] => (raw || []).map(d => ({
  key: d.key,
  name: d.name || '',
  weight: Number(d.weight) || 0,
  description: d.description || '',
  guidance: d.guidance || '',
}));

export function RubricTab({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;

  const [dims, setDims] = useState<Dim[]>([]);
  const [rubric, setRubric] = useState('');
  const [version, setVersion] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [isDefault, setIsDefault] = useState(false);
  const [msg, setMsg] = useState<{ text: string; kind: '' | 'ok' | 'err' }>({ text: '', kind: '' });
  const [saving, setSaving] = useState(false);
  const [importStage, setImportStage] = useState<Stage | null>(null);

  const readonly = !ws.canConfigure;

  /* Everything on this tab describes ONE workspace, and Save sends whatever is on screen to
     whichever workspace is selected at click time. So a rubric that failed to load — or that
     finished loading for a workspace the operator has since left — must not be left on screen:
     one Save would copy one customer's QA policy into another's account. The editor therefore
     fails CLOSED: the form is emptied and Save is hidden until a load completes for the
     workspace currently selected. */
  const clear = useCallback((text: string) => {
    setDims([]);
    setRubric('');
    setVersion(null);
    setLoaded(false);
    setIsDefault(false);
    if (text) setMsg({ text, kind: 'err' });
  }, []);

  const load = useCallback(async () => {
    setMsg({ text: '', kind: '' });
    if (ws.operator && !ws.tid) { clear(t('con.tenant.pick')); return; }
    const cfg = await ws.json<Config>('/scoring/config');
    if (cfg === STALE) return;                       // the operator moved on; that was A's
    if (cfg === null) { clear(t('tkb.loadfail')); return; }
    setDims(asDims(cfg.dimensions));
    setRubric(cfg.rubric || '');
    setVersion(cfg.version || null);
    setLoaded(true);
    // `is_default` means these numbers belong to the shared default, not to this workspace —
    // and there is nothing to reset to what is already on screen.
    setIsDefault(cfg.is_default === true);
  }, [ws, t, clear]);

  useEffect(() => { if (on) void load(); }, [on, gen, load]);

  const total = weightTotal(dims);
  const balanced = weightsBalanced(dims);

  const patch = (i: number, part: Partial<Dim>) =>
    setDims(prev => prev.map((d, j) => (j === i ? { ...d, ...part } : d)));

  const save = async () => {
    const v = validateRubric(dims);
    if (!v.ok) {
      setMsg({
        kind: 'err',
        text: v.problem === 'no-dimensions' ? t('sc.needone')
          : v.problem === 'unnamed-dimension' ? t('sc.needname')
            : t('sc.mustbe100', { total: v.total }),
      });
      return;
    }
    setSaving(true);
    const r = await ws.send<Config>('PUT', '/scoring/config', { dimensions: dims, rubric });
    setSaving(false);
    if (!r.ok) { setMsg({ text: failMessage(r, t), kind: 'err' }); return; }
    toast(t('sc.saved'), 'ok');
    const d = r.data || {};
    setDims(asDims(d.dimensions));
    setRubric(d.rubric || '');
    setVersion(d.version || null);
    // Saving is what forks the shared default into a rubric of this workspace's own.
    setIsDefault(false);
    setMsg({ text: '', kind: '' });
  };

  /* Reset to the default rubric. This throws away a rubric someone may have spent a day tuning,
     so the server asks the caller to re-enter their OWN password — which means a password
     field, in a brand modal (never a native prompt), and a 403 answered where the field is
     rather than as a toast that disappears.

     There is no operator twin of `/scoring/reset` on purpose: it needs a `user_id`, which an
     operator does not have. Hence `!ws.operator` on the button. */
  const reset = async () => {
    const done = await showModal(close => (
      <ResetBody t={t} close={close} send={pw => ws.send('POST', '/scoring/reset', { password: pw })} />
    ), { maxWidth: '520px' });
    if (done !== true) return;
    toast(t('tn.sc.reset.done'), 'ok');
    void load();
  };

  if (!ws.ready) return <div className="empty">{t('con.tenant.pick')}</div>;

  const canEdit = loaded && !readonly;
  const canReset = canEdit && !ws.operator && !isDefault;

  return (
    <>
      <div className="card">
        <div className="inline" style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14 }}>
          <h3 style={{ margin: 0 }}>{t('sc.heading')}</h3>
          <span className="hint">{version ? `${t('sc.version')} ${version}` : t('sc.none')}</span>
        </div>
        {readonly ? <p className="hint" style={{ color: 'var(--pending)' }}>{t('sc.readonly')}</p> : null}
        {/* Shown while `/scoring/config` answers with is_default: the numbers on screen are the
            shared default, not this workspace's rubric, and saving is what forks it. */}
        {loaded && isDefault ? <div className="msg">{t('tn.sc.isdefault')}</div> : null}

        <div>
          {dims.length ? dims.map((d, i) => (
            <div className="sc-edit" key={i}>
              <div className="inline" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
                <span className="sc-edit-num">{i + 1}</span>
                {canEdit ? (
                  <button
                    type="button" className="act danger" title={t('sc.remove')} aria-label={t('sc.remove')}
                    onClick={() => setDims(prev => prev.filter((_, j) => j !== i))}
                  >🗑</button>
                ) : null}
              </div>
              <div className="row">
                <div style={{ flex: 2 }}>
                  <label>{t('sc.dname')}</label>
                  <input
                    value={d.name} placeholder={t('sc.dname.ph')} disabled={readonly}
                    onChange={e => patch(i, { name: e.target.value })}
                  />
                </div>
                <div className="w-num">
                  <label>{t('sc.dweight')}</label>
                  <input
                    type="number" min={0} step={1} value={d.weight} disabled={readonly}
                    onChange={e => patch(i, { weight: parseFloat(e.target.value) || 0 })}
                  />
                </div>
              </div>
              <label>{t('sc.ddesc')}</label>
              <input
                value={d.description} disabled={readonly}
                onChange={e => patch(i, { description: e.target.value })}
              />
              <label>{t('sc.dguide')}</label>
              {/* Guidance can arrive from AI import as a section's whole criteria list; size
                  each box to its text so it is readable without scrolling a 54px window. */}
              <Guidance
                value={d.guidance} disabled={readonly} placeholder={t('sc.dguide.ph')}
                onChange={v => patch(i, { guidance: v })}
              />
            </div>
          )) : <div className="empty">{t('sc.nodims')}</div>}
        </div>

        <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="inline" style={{ gap: 8 }}>
            {canEdit ? (
              <>
                <button
                  type="button" className="ghost"
                  onClick={() => setDims(prev => [...prev, { name: '', weight: 0, description: '', guidance: '' }])}
                >{t('sc.adddim')}</button>
                {/* Rescale weights proportionally so they total exactly 100% (drift goes on the
                    last one). */}
                <button
                  type="button" className="ghost"
                  onClick={() => setDims(prev => normalizeWeights(prev))}
                >{t('sc.normalize')}</button>
                <RubricImport
                  t={t}
                  onLoaded={d => {
                    setDims(asDims(d.dimensions));
                    if (d.rubric) setRubric(d.rubric);
                  }}
                  onMessage={m => setMsg(m)}
                  onStage={setImportStage}
                  funnel={ws.funnel}
                />
              </>
            ) : null}
          </div>
          <span className="hint">
            <span>{t('sc.sum')}</span>: <b style={{ color: balanced ? 'var(--ok)' : 'var(--coral)' }}>{total}</b>%{' '}
            <span style={{ color: balanced ? 'var(--ok)' : 'var(--coral)' }}>{balanced ? '✓' : '✗'}</span>
          </span>
        </div>

        {/* Import progress. The stage caption says WHICH work is running; the bar is only ever
            a real fraction (uploaded bytes, then output tokens the model has produced against
            what the document needs) and goes indeterminate where no denominator exists. It
            lives next to the button that starts it. */}
        {importStage ? <ImportBar t={t} stage={importStage} /> : null}

        <hr className="sep" />
        <label htmlFor="scRubric">{t('sc.rubric')}</label>
        <textarea
          id="scRubric" style={{ minHeight: 80 }} placeholder={t('sc.rubric.ph')}
          value={rubric} disabled={readonly} onChange={e => setRubric(e.target.value)}
        />
        <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          {canReset
            ? <button type="button" className="ghost" onClick={() => void reset()}>{t('tn.sc.reset')}</button>
            : <span />}
          {canEdit ? (
            <button type="button" className="primary" disabled={saving} onClick={() => void save()}>
              {saving ? <span className="spinner" /> : t('sc.save')}
            </button>
          ) : null}
        </div>
        <div className={`msg${msg.kind ? ' ' + msg.kind : ''}`} aria-live="polite">{msg.text}</div>
      </div>

      {/* Colour thresholds. A separate card with its OWN reset, deliberately: resetting the
          rubric throws away work someone tuned, resetting two colour boundaries does not, and
          one shared button would make the cheap action feel as risky as the costly one. */}
      {loaded && !readonly ? <BandsCard /> : null}
    </>
  );
}

function Guidance({ value, disabled, placeholder, onChange }: {
  value: string; disabled: boolean; placeholder: string; onChange: (v: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useAutogrow(ref);
  return (
    <textarea
      ref={ref} style={{ minHeight: 54 }} placeholder={placeholder}
      value={value} disabled={disabled} onChange={e => onChange(e.target.value)}
    />
  );
}

function ResetBody({ t, close, send }: {
  t: T; close: (v?: unknown) => void; send: (pw: string) => Promise<Sent>;
}) {
  const [pw, setPw] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);

  const go = async () => {
    setErr('');
    if (!pw) { setErr(t('tn.sc.reset.needpw')); ref.current?.focus(); return; }
    setBusy(true);
    const r = await send(pw);
    if (r.ok) { close(true); return; }
    setBusy(false);
    // 403 is the wrong password — say so in this language, not in the server's English.
    setErr(r.status === 403 ? t('tn.sc.reset.bad') : failMessage(r, t));
    ref.current?.select();
  };

  return (
    <>
      <h3>{t('tn.sc.reset.heading')}</h3>
      <p className="hint">{t('tn.sc.reset.warn')}</p>
      <label htmlFor="scResetPw">{t('tn.sc.reset.pw')}</label>
      <input
        type="password" id="scResetPw" ref={ref} autoComplete="current-password" value={pw}
        onChange={e => setPw(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') void go(); }}
      />
      <div className="actions">
        <button type="button" className="ghost" onClick={() => close()}>{t('btn.cancel')}</button>
        <button type="button" className="danger" disabled={busy} onClick={() => void go()}>
          {busy ? <span className="spinner" /> : t('tn.sc.reset')}
        </button>
      </div>
      <div className="msg err" aria-live="polite">{err}</div>
    </>
  );
}
