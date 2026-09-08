'use client';
import { useEffect, useRef, useState } from 'react';
import { toast } from '@/components/ui/Toast';
import { useAutogrow } from '@/lib/autogrow';
import { dateTime } from '@/lib/format';
import {
  normalizeWeights, validateRubric, weightTotal, weightsBalanced, type RubricDimension,
} from '@/lib/rubricMath';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import { RUBRIC_SOURCE, sourcePill } from './logic';
import { Msg, type Note } from './parts';

/* The default rubric — the tenant portal's dimension editor, one level up.

   This is what every owner WITHOUT a rubric of their own is scored against. Deliberately
   WITHOUT the AI import the portal has: this one is the fallback everybody inherits, and it is
   written once by hand rather than drafted from one customer's document.

   THE SOURCE PILL IS THE IMPORTANT HALF. Until an operator saves here, what is on screen is
   only a proposal — the demo tenant's rubric, or the built-in starter — and `pb.src.demo` /
   `pb.src.builtin` say so. Without it an operator edits what looks like their default and never
   notices that nothing was ever stored.

   The arithmetic is `lib/rubricMath.ts`, not a local copy: it mirrors
   `backend/app/services/scoring.py` down to Python's round-half-to-even, so the total shown
   next to the ✓ is the total the server will compute. */

interface Dim extends RubricDimension {
  description: string;
  guidance: string;
}

interface DefaultRubric {
  dimensions?: unknown;
  rubric?: string;
  source?: string;
  updated_at?: string | null;
  updated_by?: string | null;
}

const blank = (): Dim => ({ name: '', weight: 0, description: '', guidance: '' });

export default function DefaultRubricTab() {
  const { t } = useI18n();
  const [dims, setDims] = useState<Dim[]>([]);
  const [rubric, setRubric] = useState('');
  const [meta, setMeta] = useState<{ source: string; updated_at: string | null; updated_by: string }>({
    source: '', updated_at: null, updated_by: '',
  });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);
  // Nothing has loaded yet: the pill must not claim a source before it knows one.
  const [ready, setReady] = useState(false);

  const apply = (d: DefaultRubric) => {
    setDims((Array.isArray(d.dimensions) ? d.dimensions : []).map((x: Record<string, unknown>) => ({
      key: x.key as string | undefined,
      name: (x.name as string) || '',
      weight: (x.weight as number) || 0,
      description: (x.description as string) || '',
      guidance: (x.guidance as string) || '',
    })));
    setRubric(d.rubric || '');
    setMeta({ source: d.source || '', updated_at: d.updated_at || null, updated_by: d.updated_by || '' });
    setReady(true);
  };

  useEffect(() => {
    adminGet<DefaultRubric>('/admin/default-rubric')
      .then(apply)
      .catch(e => {
        if (e instanceof SessionExpired) return;
        setNote({ kind: 'err', text: t('toast.error') });
      });
    // Once, on mount. A language switch must NOT re-fetch: it would throw away dimensions
    // somebody has typed and not saved, which is exactly what the legacy `cq:lang` handler
    // went out of its way to avoid by reading the DOM back before re-rendering it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const edit = (i: number, patch: Partial<Dim>) =>
    setDims(list => list.map((d, n) => (n === i ? { ...d, ...patch } : d)));

  const total = weightTotal(dims);
  const balanced = weightsBalanced(dims);
  const pill = sourcePill(RUBRIC_SOURCE, meta.source, 'builtin');

  const save = async () => {
    setNote(null);
    const check = validateRubric(dims);
    if (!check.ok) {
      const text = check.problem === 'no-dimensions' ? t('sc.needone')
        : check.problem === 'unnamed-dimension' ? t('sc.needname')
          : t('sc.mustbe100', { total: check.total });
      setNote({ kind: 'err', text });
      return;
    }
    setBusy(true);
    try {
      const d = await adminSend<DefaultRubric>('PUT', '/admin/default-rubric', { dimensions: dims, rubric });
      apply(d);
      setNote({ kind: 'ok', text: t('pb.defrubric.saved') });
      toast(t('pb.defrubric.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: errText(e, t) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <div
        className="inline"
        style={{ justifyContent: 'space-between', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', rowGap: 6 }}
      >
        <h3 style={{ margin: 0 }}>{t('pb.defrubric.heading')}</h3>
        <span className="inline" style={{ gap: 8 }}>
          <span className={`pill ${ready ? pill.cls : ''}`}>{ready ? t(pill.key) : '—'}</span>
          <span className="hint">
            {meta.updated_at
              ? t('pb.defrubric.updated', { when: dateTime(meta.updated_at), who: meta.updated_by || '—' })
              : ''}
          </span>
        </span>
      </div>
      <p className="hint">{t('pb.defrubric.desc')}</p>

      <div>
        {!dims.length ? <div className="empty">{t('sc.nodims')}</div> : dims.map((d, i) => (
          <div className="sc-edit" key={d.key || `i${i}`}>
            <div className="inline" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
              <span className="sc-edit-num">{i + 1}</span>
              <button
                className="act danger"
                type="button"
                title={t('sc.remove')}
                aria-label={t('sc.remove')}
                onClick={() => setDims(list => list.filter((_, n) => n !== i))}
              >
                🗑
              </button>
            </div>
            <div className="row">
              <div style={{ flex: 2 }}>
                <label>{t('sc.dname')}</label>
                <input
                  value={d.name}
                  placeholder={t('sc.dname.ph')}
                  onChange={e => edit(i, { name: e.target.value })}
                />
              </div>
              <div className="w-num">
                <label>{t('sc.dweight')}</label>
                <input
                  type="number"
                  min={0}
                  step={1}
                  value={d.weight}
                  onChange={e => edit(i, { weight: parseFloat(e.target.value) || 0 })}
                />
              </div>
            </div>
            <label>{t('sc.ddesc')}</label>
            <input value={d.description} onChange={e => edit(i, { description: e.target.value })} />
            <label>{t('sc.dguide')}</label>
            <Guidance
              value={d.guidance}
              placeholder={t('sc.dguide.ph')}
              onChange={v => edit(i, { guidance: v })}
            />
          </div>
        ))}
      </div>

      <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="inline" style={{ gap: 8 }}>
          <button className="ghost" type="button" onClick={() => setDims(list => [...list, blank()])}>
            {t('sc.adddim')}
          </button>
          <button className="ghost" type="button" onClick={() => setDims(list => normalizeWeights(list))}>
            {t('sc.normalize')}
          </button>
        </div>
        <span className="hint">
          <span>{t('sc.sum')}</span>:{' '}
          <b style={{ color: balanced ? 'var(--ok)' : 'var(--coral)' }}>{total}</b>%{' '}
          <span style={{ color: balanced ? 'var(--ok)' : 'var(--coral)' }}>{balanced ? '✓' : '✗'}</span>
        </span>
      </div>

      <hr className="sep" />
      <label htmlFor="drRubric">{t('sc.rubric')}</label>
      <textarea
        id="drRubric"
        style={{ minHeight: 80 }}
        placeholder={t('sc.rubric.ph')}
        value={rubric}
        onChange={e => setRubric(e.target.value)}
      />
      <div className="actions">
        <button className="primary" type="button" onClick={save} disabled={busy}>
          {busy ? <span className="spinner" /> : t('sc.save')}
        </button>
      </div>
      <Msg note={note} />
    </div>
  );
}

/* The guidance box is the text the MODEL reads when scoring, so it is the field people most
   need to see the whole of. `useAutogrow` grows it to its content up to a cap — a fixed 54px
   box showed two lines of a real call-centre standard. */
function Guidance({
  value, placeholder, onChange,
}: {
  value: string;
  placeholder: string;
  onChange: (v: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useAutogrow(ref);
  return (
    <textarea
      ref={ref}
      style={{ minHeight: 54 }}
      placeholder={placeholder}
      value={value}
      onChange={e => onChange(e.target.value)}
    />
  );
}
