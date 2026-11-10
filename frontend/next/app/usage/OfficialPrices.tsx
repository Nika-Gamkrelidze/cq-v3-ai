'use client';
import { useState } from 'react';
import type { JSX } from 'react';
import { toast } from '@/components/ui/Toast';
import { apiMessage, apiSend } from '@/lib/session';
import { NUM } from './listKit';
import { providerModel } from './logic';
import { PRICE_FIELDS } from './types';
import type { LookupResponse, LookupResult, PriceField, PriceRow, Pricing, T } from './types';

/* "Look up official prices": every model that has made calls, read off its provider's own
   pricing page by the server (`POST /admin/usage/prices/lookup`), shown beside the price in
   force today. Nothing is saved until the operator ticks rows and presses Save — a price that
   bills customers is a person's decision. Each proposed number carries the line of the page it
   came from (hover it); the server has already checked that line is really on the page. */

const today = (): string => new Date().toISOString().slice(0, 10);
const money = (v: number | null | undefined): string =>
  (v === null || v === undefined ? '—' : `$${v.toLocaleString('en-US', { maximumFractionDigits: 6 })}`);

/** The price row in force now for a call of (provider, model, kind) — the same precedence as
    the server's usage_price(): exact model over '*', a named kind over "any", newest first. */
export function inForce(prices: PriceRow[], provider: string, model: string, capability: string): PriceRow | null {
  const now = Date.now();
  const cands = prices.filter(p => p.provider === provider && (p.model === model || p.model === '*')
    && (p.capability === null || p.capability === capability) && Date.parse(p.effective_from) <= now);
  cands.sort((a, b) => (Number(b.model === model) - Number(a.model === model))
    || (Number(b.capability !== null) - Number(a.capability !== null))
    || (Date.parse(b.effective_from) - Date.parse(a.effective_from)));
  return cands[0] ?? null;
}

/** Whether the official numbers differ from the price in force (any field it states). */
function differs(r: LookupResult, cur: PriceRow | null): boolean {
  if (!cur) return true;
  return (Object.keys(r.prices) as PriceField[]).some(f => Math.abs((cur[f] ?? -1) - r.prices[f]!.value) > 1e-9);
}

const key = (r: { provider: string; model: string; capability: string }) => `${r.provider}|${r.model}|${r.capability}`;

export default function OfficialPrices({ d, t, onSaved, onClose }: {
  d: Pricing; t: T; onSaved: () => void; onClose: () => void;
}): JSX.Element {
  const [state, setState] = useState<'idle' | 'loading' | 'done'>('idle');
  const [res, setRes] = useState<LookupResponse | null>(null);
  const [err, setErr] = useState('');
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [dates, setDates] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [rowErr, setRowErr] = useState<Record<string, string>>({});

  const firstCall = (r: LookupResult) =>
    d.unpriced.find(u => u.provider === r.provider && u.model === r.model && u.capability === r.capability)?.first_used;

  /* `fresh` is the "Read again" button: without it the server hands back the earlier reading of
     a page that has not changed, so two presses a minute apart cannot disagree. */
  const run = async (fresh = false) => {
    setState('loading');
    setErr('');
    setRowErr({});
    try {
      const out = await apiSend<LookupResponse>('POST', '/admin/usage/prices/lookup', fresh ? { fresh: true } : {}, { scope: 'admin' });
      setRes(out);
      const p: Record<string, boolean> = {};
      const dt: Record<string, string> = {};
      for (const r of out.results) {
        const cur = inForce(d.prices, r.provider, r.model, r.capability);
        p[key(r)] = r.status === 'found' && differs(r, cur);
        // A model with no price at all: date the official price from its first call, so the
        // calls already made are costed too. One that has a price: the new price starts now.
        const fc = firstCall(r);
        dt[key(r)] = !cur && fc ? fc.slice(0, 10) : today();
      }
      setPicked(p);
      setDates(dt);
      setState('done');
    } catch (x) {
      setErr(apiMessage(x, t));
      setState('idle');
    }
  };

  const save = async () => {
    if (!res) return;
    setSaving(true);
    const errors: Record<string, string> = {};
    let ok = 0;
    for (const r of res.results) {
      const k = key(r);
      if (!picked[k] || r.status !== 'found') continue;
      const d0 = dates[k] || today();
      const body = {
        provider: r.provider, model: r.model, capability: r.capability,
        effective_from: d0 === today() ? new Date().toISOString() : d0,
        note: t('um.official.note', { url: r.source_url ?? '', date: r.fetched_at.slice(0, 10) }).slice(0, 500),
        ...Object.fromEntries(PRICE_FIELDS.map(f => [f, r.prices[f]?.value ?? null])),
      };
      try {
        await apiSend('POST', '/admin/usage/prices', body, { scope: 'admin' });
        ok += 1;
        // The changes the page already announces become future versions, so the price list
        // switches on the provider's date without anyone remembering to.
        for (const v of r.scheduled ?? []) {
          await apiSend('POST', '/admin/usage/prices', {
            ...body, effective_from: v.effective_from,
            ...Object.fromEntries(PRICE_FIELDS.map(f => [f, (v.prices[f] ?? r.prices[f])?.value ?? null])),
          }, { scope: 'admin' });
          ok += 1;
        }
      } catch (x) {
        errors[k] = apiMessage(x, t);
      }
    }
    setSaving(false);
    setRowErr(errors);
    if (ok) {
      toast(t('um.official.saved', { n: ok }), 'ok');
      onSaved();
    }
    if (!Object.keys(errors).length) onClose();
  };

  const results = res?.results ?? [];
  const nPicked = results.filter(r => picked[key(r)] && r.status === 'found').length;
  const cachedAt = results.find(r => r.cached)?.fetched_at;
  const sources = [...new Set(results.map(r => r.source_url).filter(Boolean))] as string[];

  return (
    <div className="card" style={{ marginTop: 12, background: 'var(--input-bg)' }}>
      <h3 style={{ margin: '0 0 4px' }}>{t('um.official.title')}</h3>
      <p className="hint" style={{ margin: 0 }}>{t('um.official.lead')}</p>

      {state !== 'done' ? (
        <div className="actions" style={{ marginTop: 12 }}>
          <button type="button" className="primary" disabled={state === 'loading'} onClick={() => void run()}>
            {state === 'loading' ? <><span className="spinner" aria-hidden="true" /> {t('um.official.reading')}</> : t('um.official.run')}
          </button>
          <button type="button" className="ghost" onClick={onClose}>{t('um.cancel')}</button>
        </div>
      ) : null}
      {err ? <p className="msg err">{err}</p> : null}

      {state === 'done' ? (
        <>
          {sources.length ? (
            <p className="hint" style={{ margin: '10px 0 0' }}>
              {t('um.official.sources')}{' '}
              {sources.map((u, i) => (
                <span key={u}>{i ? ' · ' : ''}<a href={u} target="_blank" rel="noopener noreferrer">{new URL(u).hostname}</a></span>
              ))}
            </p>
          ) : null}
          {cachedAt ? (
            <p className="hint" style={{ margin: '8px 0 0' }}>
              {t('um.official.cached', { time: new Date(cachedAt).toLocaleTimeString() })}
            </p>
          ) : null}
          {!results.length ? <div className="empty">{t('um.official.none')}</div> : (
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table>
                <thead>
                  <tr>
                    <th />
                    <th>{t('usage.th.model')}</th>
                    <th>{t('usage.filter.kind')}</th>
                    <th>{t('um.state')}</th>
                    {PRICE_FIELDS.map(f => <th key={f} style={NUM}>{t(`um.f.${FIELD_KEY[f]}`)}</th>)}
                    <th>{t('um.from')}</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map(r => {
                    const k = key(r);
                    const cur = inForce(d.prices, r.provider, r.model, r.capability);
                    const usable = r.status === 'found';
                    return (
                      <tr key={k}>
                        <td>
                          <input type="checkbox" aria-label={t('um.official.pick', { m: r.model })} disabled={!usable}
                            checked={!!picked[k] && usable} onChange={e => setPicked(p => ({ ...p, [k]: e.target.checked }))} />
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          {providerModel(r.provider, r.model)}
                          {r.page_model_name ? <div className="hint">{t('um.official.aspage', { name: r.page_model_name })}</div> : null}
                          {r.notes ? <div className="hint" style={{ maxWidth: 320, whiteSpace: 'normal' }}>{r.notes}</div> : null}
                          {(r.scheduled ?? []).map(v => (
                            <div key={v.effective_from} className="hint" style={{ maxWidth: 320, whiteSpace: 'normal' }}
                              title={PRICE_FIELDS.filter(f => v.prices[f]).map(f => `“${v.prices[f]!.quote}”`).join(' ')}>
                              <span className="pill pending">{t('um.official.scheduled', { date: v.effective_from })}</span>{' '}
                              {PRICE_FIELDS.filter(f => v.prices[f]).map(f => `${t(`um.f.${FIELD_KEY[f]}`)} ${money(v.prices[f]!.value)}`).join(' · ')}
                            </div>
                          ))}
                          {(r.unstable ?? []).map(u => (
                            <div key={`${u.when}${u.field}`} className="hint" style={{ maxWidth: 320, whiteSpace: 'normal' }}
                              title={u.quote ? `“${u.quote}”` : undefined}>
                              <span className="pill pending">⚠</span>{' '}
                              {t('um.official.unstable', {
                                what: `${t(`um.f.${FIELD_KEY[u.field]}`)}${u.when ? ` (${u.when})` : ''}`,
                                values: u.values.map(v => money(v)).join(' / '),
                              })}
                            </div>
                          ))}
                          {rowErr[k] ? <div className="msg err" style={{ margin: 0 }}>{rowErr[k]}</div> : null}
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>{t(`usage.cap.${r.capability}`)}</td>
                        <td style={{ maxWidth: 260 }}>
                          <span className={`pill ${usable ? (differs(r, cur) ? 'pending' : 'ready') : 'error'}`} style={{ whiteSpace: 'nowrap' }}>
                            {usable ? t(differs(r, cur) ? (cur ? 'um.official.changed' : 'um.official.new') : 'um.official.same') : t(`um.official.st.${r.status}`)}
                          </span>
                          {r.error ? <div className="hint">{r.error}</div> : null}
                          {r.status === 'unverified' && r.rejected?.length ? (
                            <div className="hint" style={{ whiteSpace: 'normal' }}>
                              {t('um.official.why', { reasons: [...new Set(r.rejected.map(x => x.reason))].join('; ') })}
                            </div>
                          ) : null}
                        </td>
                        {PRICE_FIELDS.map(f => {
                          const p = r.prices[f];
                          const was = cur?.[f] ?? null;
                          const changed = p && (was === null || Math.abs(was - p.value) > 1e-9);
                          return (
                            <td key={f} style={NUM} title={p ? `${p.stated} — “${p.quote}”` : undefined}>
                              {p ? <b style={changed ? { color: 'var(--pending)' } : undefined}>{money(p.value)}</b> : '—'}
                              {changed && was !== null ? <div className="hint"><s>{money(was)}</s></div> : null}
                            </td>
                          );
                        })}
                        <td>
                          <input type="date" value={dates[k] ?? today()} disabled={!usable}
                            aria-label={t('um.from')} onChange={e => setDates(x => ({ ...x, [k]: e.target.value }))} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="hint" style={{ margin: '8px 0 0' }}>{t('um.official.hint')}</p>
          <div className="actions" style={{ marginTop: 10 }}>
            <button type="button" className="primary" disabled={!nPicked || saving} onClick={save}>
              {t('um.official.save', { n: nPicked })}
            </button>
            <button type="button" className="ghost" onClick={() => void run(true)} disabled={saving}>{t('um.official.again')}</button>
            <button type="button" className="ghost" onClick={onClose}>{t('um.cancel')}</button>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** The `um.f.*` label of each price column. */
export const FIELD_KEY: Record<PriceField, string> = {
  input_per_mtok: 'input', output_per_mtok: 'output', cache_write_per_mtok: 'cachewrite',
  cache_read_per_mtok: 'cacheread', audio_per_hour: 'audio', chars_per_k: 'chars',
};
