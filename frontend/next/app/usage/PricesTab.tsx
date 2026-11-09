'use client';
import { useId, useMemo, useState } from 'react';
import type { FormEvent, JSX, ReactNode } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Select, type Option } from '@/components/ui/Select';
import { toast } from '@/components/ui/Toast';
import { apiMessage, apiSend } from '@/lib/session';
import { LoadError, Loading, NUM, useUsageGet } from './listKit';
import { duration, fmt, providerModel, when } from './logic';
import { CAPABILITIES, PRICE_FIELDS } from './types';
import type { MarkupRow, PriceField, PriceRow, Pricing, T, UnpricedRow } from './types';

/* Prices and markup: what turns the usage on the other tabs into money.

   Both lists are VERSIONED. A new price or markup starts on a date and every call is costed by
   the version in force when it ran, so raising a price today never re-prices last month. Editing
   a row in place is for correcting a mistake — it re-prices exactly the calls that row covered,
   which is what a correction means. A price can be dated in the past: that is how calls made
   before anyone entered a price get one (the "needs a price" list prefills the date of the
   first such call for that reason). */

const today = (): string => new Date().toISOString().slice(0, 10);
const day = (iso: string | null | undefined): string => (iso ? iso.slice(0, 10) : '');

/** Today at midnight UTC sent as a DATE means "from the start of today", which would re-price
    the calls already made today. A version that starts today starts NOW instead; any other
    date is that day's midnight (UTC), as the label says. */
const fromValue = (d: string, orig: string | null): string => {
  if (orig && day(orig) === d) return orig;   // an edit that kept the date keeps the exact moment
  return d === today() ? new Date().toISOString() : d;
};

type PriceForm = { id: string | null; orig: string | null; provider: string; model: string; capability: string; from: string; note: string }
  & Record<PriceField, string>;

const EMPTY_PRICE: PriceForm = {
  id: null, orig: null, provider: '', model: '', capability: '', from: today(), note: '',
  input_per_mtok: '', output_per_mtok: '', cache_write_per_mtok: '', cache_read_per_mtok: '',
  audio_per_hour: '', chars_per_k: '',
};

interface MarkupForm { id: string | null; orig: string | null; client_id: string; pct: string; from: string; note: string }
const EMPTY_MARKUP: MarkupForm = { id: null, orig: null, client_id: '', pct: '', from: today(), note: '' };

const num = (v: string): number | null => (v.trim() === '' ? null : Number(v));
const price = (v: number | null): string => (v === null || v === undefined ? '—' : `$${v.toLocaleString('en-US', { maximumFractionDigits: 6 })}`);

/** Which version of a (provider, model, kind) is in force now, which are history, which are
    scheduled. Rows arrive newest-first within each key. */
function statusOf(rows: PriceRow[]): Map<string, 'current' | 'past' | 'future'> {
  const now = Date.now();
  const out = new Map<string, 'current' | 'past' | 'future'>();
  const seen = new Set<string>();
  for (const r of rows) {
    const key = `${r.provider}|${r.model}|${r.capability ?? ''}`;
    if (Date.parse(r.effective_from) > now) { out.set(r.id, 'future'); continue; }
    out.set(r.id, seen.has(key) ? 'past' : 'current');
    seen.add(key);
  }
  return out;
}

export default function PricesTab({ t }: { t: T }): JSX.Element {
  const [nonce, setNonce] = useState(0);
  const data = useUsageGet<Pricing>(`/admin/usage/pricing?n=${nonce}`, true);
  const reload = () => setNonce(n => n + 1);
  const [priceForm, setPriceForm] = useState<PriceForm | null>(null);
  const [markupForm, setMarkupForm] = useState<MarkupForm | null>(null);

  if (data.error && !data.data) return <div className="card"><LoadError error={data.error} onRetry={data.retry} t={t} /></div>;
  if (!data.data) return <div className="card"><Loading t={t} /></div>;
  const d = data.data;

  const startPrice = (u?: UnpricedRow) => {
    setPriceForm(u
      ? { ...EMPTY_PRICE, provider: u.provider, model: u.model, capability: '', from: day(u.first_used) || today() }
      : { ...EMPTY_PRICE });
    requestAnimationFrame(() => document.getElementById('price-form')?.scrollIntoView({ block: 'start', behavior: 'smooth' }));
  };

  return (
    <>
      <div className="card">
        <h3 style={{ margin: '0 0 4px' }}>{t('um.prices.title')}</h3>
        <p className="hint" style={{ margin: 0 }}>{t('um.prices.lead')}</p>
      </div>

      <MarkupCard d={d} t={t} form={markupForm} setForm={setMarkupForm} onSaved={reload} />

      {d.unpriced.length ? (
        <div className="card">
          <h3 style={{ marginBottom: 4 }}>{t('um.todo.title')}</h3>
          <p className="hint" style={{ margin: '0 0 12px' }}>{t('um.todo.lead')}</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t('usage.th.model')}</th>
                  <th>{t('usage.filter.kind')}</th>
                  <th style={NUM}>{t('usage.th.calls')}</th>
                  <th style={NUM}>{t('um.th.consumed')}</th>
                  <th>{t('um.th.since')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {d.unpriced.map(u => (
                  <tr key={`${u.provider}|${u.model}|${u.capability}`}>
                    <td style={{ whiteSpace: 'nowrap' }}>{providerModel(u.provider, u.model)}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{t(`usage.cap.${u.capability}`)}</td>
                    <td style={NUM}>{fmt(u.calls)}</td>
                    <td style={NUM}>{consumed(u, t)}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{when(u.first_used)}</td>
                    <td><button type="button" className="ghost" onClick={() => startPrice(u)}>{t('um.todo.set')}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="card"><p className="msg ok" style={{ margin: 0 }}>{t('um.todo.none')}</p></div>
      )}

      <div className="card" id="price-form">
        <div className="inline wrap" style={{ justifyContent: 'space-between', gap: 10 }}>
          <h3 style={{ margin: 0 }}>{t('um.list.title')}</h3>
          {!priceForm ? <button type="button" className="primary" onClick={() => startPrice()}>{t('um.list.add')}</button> : null}
        </div>
        {priceForm ? (
          <PriceEditor form={priceForm} setForm={setPriceForm} d={d} t={t}
            onSaved={() => { setPriceForm(null); reload(); }} />
        ) : null}
        <PriceTable rows={d.prices} t={t} onEdit={r => {
          setPriceForm({
            id: r.id, orig: r.effective_from, provider: r.provider, model: r.model, capability: r.capability ?? '',
            from: day(r.effective_from), note: r.note ?? '',
            ...Object.fromEntries(PRICE_FIELDS.map(f => [f, r[f] === null ? '' : String(r[f])])) as Record<PriceField, string>,
          });
          requestAnimationFrame(() => document.getElementById('price-form')?.scrollIntoView({ block: 'start', behavior: 'smooth' }));
        }} onDeleted={reload} />
      </div>
    </>
  );
}

function consumed(u: UnpricedRow, t: T): string {
  const parts: string[] = [];
  const tokens = u.input_tokens + u.output_tokens;
  if (tokens) parts.push(t('usage.unit.tokens', { n: fmt(tokens) }));
  if (u.audio_seconds) parts.push(t('usage.unit.audio', { d: duration(u.audio_seconds) }));
  if (u.characters) parts.push(t('usage.unit.chars', { n: fmt(u.characters) }));
  return parts.join(' · ') || '—';
}

/* ------------------------------------------------------------------ markup */

function MarkupCard({ d, t, form, setForm, onSaved }: {
  d: Pricing; t: T; form: MarkupForm | null; setForm: (f: MarkupForm | null) => void; onSaved: () => void;
}): JSX.Element {
  const uid = useId();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const now = Date.now();
  const inForce = (rows: MarkupRow[]) => rows.find(r => Date.parse(r.effective_from) <= now) ?? null;
  const defaults = d.markups.filter(m => !m.client_id);
  const current = inForce(defaults);
  const overrides = useMemo(() => {
    const by = new Map<string, MarkupRow[]>();
    for (const m of d.markups) if (m.client_id) by.set(m.client_id, [...(by.get(m.client_id) ?? []), m]);
    return by;
  }, [d.markups]);

  const tenantOpts: Option[] = [
    { value: '', label: t('um.markup.default') },
    ...d.tenants.map(x => ({ value: x.client_id, label: x.name })),
  ];

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setBusy(true);
    setErr('');
    const body = { client_id: form.client_id || null, pct: num(form.pct), effective_from: fromValue(form.from, form.orig), note: form.note };
    try {
      await apiSend(form.id ? 'PUT' : 'POST', `/admin/usage/markups${form.id ? `/${form.id}` : ''}`, body, { scope: 'admin' });
      toast(t('um.saved'), 'ok');
      setForm(null);
      onSaved();
    } catch (x) {
      setErr(apiMessage(x, t));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (m: MarkupRow) => {
    if (!(await confirmDialog(t('um.markup.delete.confirm'), { ok: t('um.delete'), danger: true }))) return;
    try {
      await apiSend('DELETE', `/admin/usage/markups/${m.id}`, undefined, { scope: 'admin' });
      onSaved();
    } catch (x) {
      toast(apiMessage(x, t), 'err');
    }
  };

  const pctLabel = (m: MarkupRow) => (m.pct === null ? t('um.markup.backtodefault') : `${fmt(m.pct)}%`);

  return (
    <div className="card">
      <div className="inline wrap" style={{ justifyContent: 'space-between', gap: 10 }}>
        <div>
          <h3 style={{ margin: 0 }}>{t('um.markup.title')}</h3>
          <p className="hint" style={{ margin: '4px 0 0' }}>{t('um.markup.lead')}</p>
        </div>
        {!form ? <button type="button" className="primary" onClick={() => setForm({ ...EMPTY_MARKUP })}>{t('um.markup.add')}</button> : null}
      </div>
      <div className="stat-row" style={{ marginTop: 12 }}>
        <div className="stat"><b>{current?.pct !== null && current ? `${fmt(current.pct)}%` : '0%'}</b><span>{t('um.markup.current')}</span></div>
        <div className="stat"><b>{fmt([...overrides.values()].filter(rows => inForce(rows)?.pct !== null && inForce(rows)).length)}</b><span>{t('um.markup.overrides')}</span></div>
      </div>
      {!current ? <p className="hint">{t('um.markup.none')}</p> : null}

      {form ? (
        <form onSubmit={save} className="card" style={{ marginTop: 12, background: 'var(--input-bg)' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 12px', alignItems: 'flex-end' }}>
            <Field id={`${uid}-ws`} label={t('um.markup.appliesto')}>
              <Select id={`${uid}-ws`} value={form.client_id} options={tenantOpts}
                onChange={v => setForm({ ...form, client_id: v })} />
            </Field>
            <Field id={`${uid}-pct`} label={t('um.markup.pct')}>
              <input id={`${uid}-pct`} type="number" min={0} step="0.1" inputMode="decimal" value={form.pct}
                placeholder={form.client_id ? t('um.markup.blank') : '30'}
                onChange={e => setForm({ ...form, pct: e.target.value })} required={!form.client_id} />
            </Field>
            <Field id={`${uid}-from`} label={t('um.from')}>
              <input id={`${uid}-from`} type="date" value={form.from} required onChange={e => setForm({ ...form, from: e.target.value })} />
            </Field>
            <Field id={`${uid}-note`} label={t('um.note')} wide>
              <input id={`${uid}-note`} value={form.note} maxLength={500} onChange={e => setForm({ ...form, note: e.target.value })} />
            </Field>
          </div>
          <p className="hint" style={{ margin: '8px 0 0' }}>{t('um.from.hint')}</p>
          {err ? <p className="msg err">{err}</p> : null}
          <div className="actions" style={{ marginTop: 10 }}>
            <button type="submit" className="primary" disabled={busy}>{t(form.id ? 'um.save' : 'um.add')}</button>
            <button type="button" className="ghost" onClick={() => { setForm(null); setErr(''); }}>{t('um.cancel')}</button>
          </div>
        </form>
      ) : null}

      {d.markups.length ? (
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table>
            <thead>
              <tr>
                <th>{t('um.markup.appliesto')}</th>
                <th style={NUM}>{t('um.markup.pct')}</th>
                <th>{t('um.from')}</th>
                <th>{t('um.state')}</th>
                <th>{t('um.note')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {d.markups.map(m => {
                const rows = m.client_id ? overrides.get(m.client_id) ?? [] : defaults;
                const state = Date.parse(m.effective_from) > now ? 'future' : inForce(rows)?.id === m.id ? 'current' : 'past';
                return (
                  <tr key={m.id}>
                    <td>{m.client_id ? (m.tenant_name || m.client_id) : <b>{t('um.markup.default')}</b>}</td>
                    <td style={NUM}>{pctLabel(m)}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>{when(m.effective_from)}</td>
                    <td><StatePill state={state} t={t} /></td>
                    <td className="hint">{m.note || ''}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button type="button" className="ghost" onClick={() => setForm({
                        id: m.id, orig: m.effective_from, client_id: m.client_id ?? '', pct: m.pct === null ? '' : String(m.pct),
                        from: day(m.effective_from), note: m.note ?? '',
                      })}>{t('um.edit')}</button>{' '}
                      <button type="button" className="ghost" onClick={() => remove(m)}>{t('um.delete')}</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ prices */

const FIELD_LABEL: Record<PriceField, string> = {
  input_per_mtok: 'um.f.input', output_per_mtok: 'um.f.output', cache_write_per_mtok: 'um.f.cachewrite',
  cache_read_per_mtok: 'um.f.cacheread', audio_per_hour: 'um.f.audio', chars_per_k: 'um.f.chars',
};

function PriceEditor({ form, setForm, d, t, onSaved }: {
  form: PriceForm; setForm: (f: PriceForm | null) => void; d: Pricing; t: T; onSaved: () => void;
}): JSX.Element {
  const uid = useId();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const providers = useMemo(() => [...new Set(d.models.map(m => m.provider))].sort(), [d.models]);
  const models = useMemo(() => [...new Set(d.models.filter(m => !form.provider || m.provider === form.provider).map(m => m.model))].sort(),
    [d.models, form.provider]);
  const kindOpts: Option[] = [{ value: '', label: t('um.kind.any') }, ...CAPABILITIES.map(c => ({ value: c, label: t(`usage.cap.${c}`) }))];

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr('');
    const body = {
      provider: form.provider, model: form.model, capability: form.capability || null,
      effective_from: fromValue(form.from, form.orig), note: form.note,
      ...Object.fromEntries(PRICE_FIELDS.map(f => [f, num(form[f])])),
    };
    try {
      await apiSend(form.id ? 'PUT' : 'POST', `/admin/usage/prices${form.id ? `/${form.id}` : ''}`, body, { scope: 'admin' });
      toast(t('um.saved'), 'ok');
      onSaved();
    } catch (x) {
      setErr(apiMessage(x, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="card" style={{ marginTop: 12, background: 'var(--input-bg)' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 12px', alignItems: 'flex-end' }}>
        <Field id={`${uid}-prov`} label={t('usage.filter.provider')}>
          <input id={`${uid}-prov`} list={`${uid}-provs`} value={form.provider} required maxLength={64}
            onChange={e => setForm({ ...form, provider: e.target.value })} />
          <datalist id={`${uid}-provs`}>{providers.map(p => <option key={p} value={p} />)}</datalist>
        </Field>
        <Field id={`${uid}-model`} label={t('usage.th.model')}>
          <input id={`${uid}-model`} list={`${uid}-models`} value={form.model} required maxLength={200}
            placeholder={t('um.model.any')} onChange={e => setForm({ ...form, model: e.target.value })} />
          <datalist id={`${uid}-models`}><option value="*" />{models.map(m => <option key={m} value={m} />)}</datalist>
        </Field>
        <Field id={`${uid}-kind`} label={t('usage.filter.kind')}>
          <Select id={`${uid}-kind`} value={form.capability} options={kindOpts} onChange={v => setForm({ ...form, capability: v })} />
        </Field>
        <Field id={`${uid}-from`} label={t('um.from')}>
          <input id={`${uid}-from`} type="date" value={form.from} required onChange={e => setForm({ ...form, from: e.target.value })} />
        </Field>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 12px', alignItems: 'flex-end', marginTop: 10 }}>
        {PRICE_FIELDS.map(f => (
          <Field key={f} id={`${uid}-${f}`} label={t(FIELD_LABEL[f])}>
            <input id={`${uid}-${f}`} type="number" min={0} step="any" inputMode="decimal" value={form[f]}
              placeholder={f.startsWith('cache_') ? t('um.f.cache.placeholder') : ''}
              onChange={e => setForm({ ...form, [f]: e.target.value })} />
          </Field>
        ))}
      </div>
      <div style={{ marginTop: 10 }}>
        <Field id={`${uid}-note`} label={t('um.note')} wide>
          <input id={`${uid}-note`} value={form.note} maxLength={500} placeholder={t('um.note.placeholder')}
            onChange={e => setForm({ ...form, note: e.target.value })} />
        </Field>
      </div>
      <p className="hint" style={{ margin: '8px 0 0' }}>{t('um.price.hint')}</p>
      <p className="hint" style={{ margin: '4px 0 0' }}>{t(form.id ? 'um.price.edit.hint' : 'um.from.hint')}</p>
      {err ? <p className="msg err">{err}</p> : null}
      <div className="actions" style={{ marginTop: 10 }}>
        <button type="submit" className="primary" disabled={busy}>{t(form.id ? 'um.save' : 'um.add')}</button>
        <button type="button" className="ghost" onClick={() => setForm(null)}>{t('um.cancel')}</button>
      </div>
    </form>
  );
}

function PriceTable({ rows, t, onEdit, onDeleted }: {
  rows: PriceRow[]; t: T; onEdit: (r: PriceRow) => void; onDeleted: () => void;
}): JSX.Element {
  const state = useMemo(() => statusOf(rows), [rows]);
  if (!rows.length) return <div className="empty">{t('um.list.empty')}</div>;
  const remove = async (r: PriceRow) => {
    if (!(await confirmDialog(t('um.price.delete.confirm'), { ok: t('um.delete'), danger: true }))) return;
    try {
      await apiSend('DELETE', `/admin/usage/prices/${r.id}`, undefined, { scope: 'admin' });
      onDeleted();
    } catch (x) {
      toast(apiMessage(x, t), 'err');
    }
  };
  return (
    <div className="table-wrap" style={{ marginTop: 12 }}>
      <table>
        <thead>
          <tr>
            <th>{t('usage.th.model')}</th>
            <th>{t('usage.filter.kind')}</th>
            <th>{t('um.from')}</th>
            <th>{t('um.state')}</th>
            {PRICE_FIELDS.map(f => <th key={f} style={NUM}>{t(FIELD_LABEL[f])}</th>)}
            <th>{t('um.note')}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.id} style={state.get(r.id) === 'past' ? { opacity: 0.6 } : undefined}>
              <td style={{ whiteSpace: 'nowrap' }}>
                {providerModel(r.provider, r.model === '*' ? t('um.model.all') : r.model)}
              </td>
              <td style={{ whiteSpace: 'nowrap' }}>{r.capability ? t(`usage.cap.${r.capability}`) : t('um.kind.any')}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{when(r.effective_from)}</td>
              <td><StatePill state={state.get(r.id) ?? 'past'} t={t} /></td>
              {PRICE_FIELDS.map(f => <td key={f} style={NUM}>{price(r[f])}</td>)}
              <td className="hint">{r.note || ''}</td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <button type="button" className="ghost" onClick={() => onEdit(r)}>{t('um.edit')}</button>{' '}
                <button type="button" className="ghost" onClick={() => remove(r)}>{t('um.delete')}</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatePill({ state, t }: { state: 'current' | 'past' | 'future'; t: T }): JSX.Element {
  const cls = state === 'current' ? 'ready' : state === 'future' ? 'pending' : '';
  return <span className={`pill ${cls}`} style={{ whiteSpace: 'nowrap' }}>{t(`um.state.${state}`)}</span>;
}

function Field({ id, label, children, wide = false }: { id: string; label: string; children: ReactNode; wide?: boolean }): JSX.Element {
  return (
    <div style={{ flex: wide ? '1 1 100%' : '1 1 150px', minWidth: 0 }}>
      <label htmlFor={id} style={{ marginTop: 0 }}>{label}</label>
      {children}
    </div>
  );
}
