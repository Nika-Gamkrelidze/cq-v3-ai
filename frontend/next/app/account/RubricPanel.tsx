'use client';
/* The personal scoring rubric.
   ===========================
   The same editor as the workspace portal, against the account's own routes. It starts as a
   COPY of the operator's default (`is_default` on the response) and stays that way until this
   account saves its own — which is why "Reset to default" is hidden while it is still looking
   at the default: there is nothing to reset to yet.

   The arithmetic — the weight total, the ✓/✗ flag and the normalise button — comes from
   `lib/rubricMath.ts`, which mirrors the SERVER's rounding rather than JavaScript's. That is
   not pedantry: `Math.round(0.15*10)/10` is 0.2 where Python's `round(0.15,1)` is 0.1, and one
   tenth of a point is enough to show 79.9 beside a server-computed 80.0, on either side of a
   band boundary. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { showModal } from '@/components/ui/Modal';
import { toast } from '@/components/ui/Toast';
import { useAutogrow } from '@/lib/autogrow';
import { ApiError, apiGet, apiMessage, apiSend } from '@/lib/session';
import { normalizeWeights, weightTotal, weightsBalanced, validateRubric } from '@/lib/rubricMath';
import { useI18n } from '@/lib/useI18n';
import styles from './account.module.css';

/** A dimension as the EDITOR holds it. `weight` is the raw string from the input, not a
    number: the box is a real text field while someone is typing into it, and parsing on every
    keystroke turns "1." into "1" and moves the caret. It is parsed where it is used. */
interface EditDim {
  key?: string;
  name: string;
  weight: string;
  description: string;
  guidance: string;
}

interface ConfigResponse {
  dimensions?: { key?: string; name?: string; weight?: number; description?: string; guidance?: string }[];
  rubric?: string;
  version?: number;
  is_default?: boolean;
}

const num = (v: string) => parseFloat(v) || 0;
const weighted = (dims: EditDim[]) => dims.map(d => ({ name: d.name, weight: num(d.weight) }));

export interface RubricPanelProps {
  active: boolean;
  onUnauthorized: () => void;
}

export function RubricPanel({ active, onUnauthorized }: RubricPanelProps) {
  const { t } = useI18n();

  const [dims, setDims] = useState<EditDim[]>([]);
  const [rubric, setRubric] = useState('');
  const [version, setVersion] = useState<number | null>(null);
  const [isDefault, setIsDefault] = useState(false);
  const [msg, setMsg] = useState<{ text: string; kind: '' | 'ok' | 'err' }>({ text: '', kind: '' });
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const loaded = useRef(false);

  const apply = useCallback((cfg: ConfigResponse) => {
    setDims((Array.isArray(cfg.dimensions) ? cfg.dimensions : []).map(d => ({
      key: d.key,
      name: d.name || '',
      weight: String(d.weight ?? 0),
      description: d.description || '',
      guidance: d.guidance || '',
    })));
    setRubric(cfg.rubric || '');
    setVersion(cfg.version || null);
    setIsDefault(cfg.is_default === true || cfg.version === 0);
  }, []);

  const load = useCallback(async () => {
    setMsg({ text: '', kind: '' });
    try {
      apply(await apiGet<ConfigResponse>('/scoring/config', { scope: 'user' }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      setMsg({ text: apiMessage(e, t), kind: 'err' });
    }
  }, [apply, onUnauthorized, t]);

  // Read once, when the tab is first opened — as `showTab('rubric')` does. Re-reading on every
  // visit would throw away edits someone left in the form when they went to look at History.
  useEffect(() => {
    if (!active || loaded.current) return;
    loaded.current = true;
    void load();
  }, [active, load]);

  const patch = (i: number, p: Partial<EditDim>) =>
    setDims(prev => prev.map((d, j) => (j === i ? { ...d, ...p } : d)));

  const total = weightTotal(weighted(dims));
  const balanced = weightsBalanced(weighted(dims));

  async function save() {
    const check = validateRubric(weighted(dims));
    if (!check.ok) {
      setMsg({
        kind: 'err',
        text: check.problem === 'no-dimensions' ? t('sc.needone')
          : check.problem === 'unnamed-dimension' ? t('sc.needname')
            : t('sc.mustbe100', { total: check.total }),
      });
      return;
    }
    setSaving(true);
    try {
      apply(await apiSend<ConfigResponse>('PUT', '/scoring/config', {
        dimensions: dims.map(d => ({
          key: d.key,
          name: d.name.trim(),
          weight: num(d.weight),
          description: d.description.trim(),
          guidance: d.guidance.trim(),
        })),
        rubric,
      }, { scope: 'user' }));
      setMsg({ text: t('sc.saved'), kind: 'ok' });
      toast(t('sc.saved'), 'ok');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      setMsg({ text: apiMessage(e, t), kind: 'err' });
    } finally {
      setSaving(false);
    }
  }

  /* Reset throws away a rubric someone may have spent an afternoon on, so the server asks for
     this account's OWN password again. A brand modal, never a native prompt() — that rule is
     in MIGRATION.md, and a native prompt leaking into KB bulk retag was a shipped QA bug. */
  async function reset() {
    const pw = await askPassword(t('ac.rub.reset.ask'), t('ac.rub.reset'), t('f.password'), t('btn.cancel'));
    if (pw === null) return;                       // cancelled, dismissed, Escape
    setMsg({ text: '', kind: '' });
    if (!pw) { setMsg({ text: t('ac.rub.reset.needpw'), kind: 'err' }); return; }
    setResetting(true);
    try {
      apply(await apiSend<ConfigResponse>('POST', '/scoring/reset', { password: pw }, { scope: 'user' }));
      setMsg({ text: t('ac.rub.reset.done'), kind: 'ok' });
      toast(t('ac.rub.reset.done'), 'ok');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      // 403 here is the password, not the session — the server's English "Password does not
      // match" is replaced with this page's translated sentence.
      const text = e instanceof ApiError && e.status === 403 ? t('ac.rub.reset.badpw') : apiMessage(e, t);
      setMsg({ text, kind: 'err' });
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="card">
      <div className="inline" style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14 }}>
        <h3 style={{ margin: 0 }}>{t('sc.heading')}</h3>
        <span className="hint">
          {isDefault ? t('ac.rub.defaultver') : version ? `${t('sc.version')} ${version}` : t('sc.none')}
        </span>
      </div>

      {isDefault ? (
        <p className="msg" style={{ color: 'var(--pending)', marginTop: 0 }}>{t('ac.rub.default')}</p>
      ) : null}

      <div>
        {dims.length ? dims.map((d, i) => (
          <div className="sc-edit" key={i}>
            <div className="inline" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
              <span className="sc-edit-num">{i + 1}</span>
              <button
                type="button" className="act danger"
                title={t('sc.remove')} aria-label={t('sc.remove')}
                onClick={() => setDims(prev => prev.filter((_, j) => j !== i))}
              >
                🗑
              </button>
            </div>
            <div className="row">
              <div style={{ flex: 2 }}>
                <label>{t('sc.dname')}</label>
                <input
                  value={d.name} placeholder={t('sc.dname.ph')}
                  onChange={e => patch(i, { name: e.target.value })}
                />
              </div>
              <div className="w-num">
                <label>{t('sc.dweight')}</label>
                <input
                  type="number" min={0} step={1} value={d.weight}
                  onChange={e => patch(i, { weight: e.target.value })}
                />
              </div>
            </div>
            <label>{t('sc.ddesc')}</label>
            <input value={d.description} onChange={e => patch(i, { description: e.target.value })} />
            <label>{t('sc.dguide')}</label>
            <Guidance
              value={d.guidance}
              placeholder={t('sc.dguide.ph')}
              onChange={v => patch(i, { guidance: v })}
            />
          </div>
        )) : <div className="empty">{t('sc.nodims')}</div>}
      </div>

      <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="inline" style={{ gap: 8 }}>
          <button
            type="button" className="ghost"
            onClick={() => setDims(prev => [...prev, { name: '', weight: '0', description: '', guidance: '' }])}
          >
            {t('sc.adddim')}
          </button>
          {/* Rescale weights proportionally so they total exactly 100% (drift goes on the last). */}
          <button
            type="button" className="ghost"
            onClick={() => setDims(prev => {
              const scaled = normalizeWeights(prev.map(d => ({ ...d, weight: num(d.weight) })));
              return scaled.map(d => ({ ...d, weight: String(d.weight) }));
            })}
          >
            {t('sc.normalize')}
          </button>
        </div>
        <span className="hint">
          <span>{t('sc.sum')}</span>: <b style={{ color: balanced ? 'var(--ok)' : 'var(--coral)' }}>{total}</b>%{' '}
          <span style={{ color: balanced ? 'var(--ok)' : 'var(--coral)' }}>{balanced ? '✓' : '✗'}</span>
        </span>
      </div>

      <hr className="sep" />
      <label htmlFor="scRubric">{t('sc.rubric')}</label>
      <textarea
        id="scRubric" style={{ minHeight: 80 }} placeholder={t('sc.rubric.ph')}
        value={rubric} onChange={e => setRubric(e.target.value)}
      />

      <div className="actions" style={{ justifyContent: 'space-between' }}>
        <button type="button" className="primary" disabled={saving} onClick={() => void save()}>
          {saving ? <span className="spinner" /> : t('sc.save')}
        </button>
        {/* Nothing to reset to while this IS the default. */}
        {!isDefault ? (
          <button type="button" className="ghost" disabled={resetting} onClick={() => void reset()}>
            {resetting ? <span className="spinner" /> : t('ac.rub.reset')}
          </button>
        ) : null}
      </div>

      <div className={`msg${msg.kind ? ` ${msg.kind}` : ''}`} aria-live="polite">{msg.text}</div>
    </div>
  );
}

/* A guidance box grows with what is in it: AI rubric import fills it with a section's complete
   criteria verbatim, and a fixed 54px box showed two lines of the text the model actually
   reads. Its own component so each one can hold the ref `useAutogrow` needs. */
function Guidance({ value, placeholder, onChange }:
  { value: string; placeholder: string; onChange: (v: string) => void }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useAutogrow(ref);
  return (
    <textarea
      ref={ref} style={{ minHeight: 54 }} placeholder={placeholder}
      value={value} onChange={e => onChange(e.target.value)}
    />
  );
}

/** The password prompt in front of a destructive action. Resolves the typed value, or `null`
    for every way of saying no — cancel, backdrop, Escape. */
function askPassword(message: string, okLabel: string, pwLabel: string, cancelLabel: string): Promise<string | null> {
  return showModal(close => (
    <form
      onSubmit={e => {
        e.preventDefault();
        const input = (e.currentTarget.elements.namedItem('askPw') as HTMLInputElement | null);
        close(input ? input.value : '');
      }}
    >
      <p>{message}</p>
      <label htmlFor="askPw" className={styles.askPwLabel}>{pwLabel}</label>
      <input type="password" id="askPw" name="askPw" autoComplete="current-password" data-autofocus />
      <div className="actions">
        <button type="button" className="ghost" onClick={() => close(null)}>{cancelLabel}</button>
        <button type="submit" className="danger">{okLabel}</button>
      </div>
    </form>
  )).then(v => (typeof v === 'string' ? v : null));
}
