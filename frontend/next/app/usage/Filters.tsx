'use client';
import { useEffect, useId, useMemo, useState } from 'react';
import type { JSX, ReactNode } from 'react';
import { Select, type Option } from '@/components/ui/Select';
import { apiGet } from '@/lib/session';
import { usageQuery } from './logic';
import { groupLabel } from './parts';
import {
  CAPABILITIES, EMPTY_FILTERS, GROUPS, WINDOWS,
  type Facets, type Filters, type Overview, type TabProps, type UsageWindow,
} from './types';

/* The filter bar every tab shares. Its options come from `overview.facets`, which the server
   computes over the RANGE alone: fetched with the other filters applied, choosing a workspace
   would shrink the workspace list to that one entry and there would be no way to pick another
   without clearing first. */

const inflight = new Map<string, Promise<Overview>>();

/** `GET /admin/usage/overview`, shared by this bar and the Overview tab. With no filter set
    both ask for the SAME url at the same moment (every page load, every period change), and
    it is the heaviest query on the page — so an identical request already in flight is joined
    rather than repeated. Nothing is cached once it settles, so a retry really retries.

    The signal only detaches the caller: the other caller may still want the answer. */
export function loadOverview(query: string, signal: AbortSignal): Promise<Overview> {
  let shared = inflight.get(query);
  if (!shared) {
    shared = apiGet<Overview>(`/admin/usage/overview${query}`, { scope: 'admin' })
      .finally(() => inflight.delete(query));
    inflight.set(query, shared);
  }
  const request = shared;
  return new Promise<Overview>((resolve, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'));
    if (signal.aborted) return abort();
    signal.addEventListener('abort', abort, { once: true });
    request.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

/** The narrowing filters — everything but the period. */
const NARROWING = ['client_id', 'group', 'provider', 'model', 'capability', 'status'] as const;

/** YYYY-MM-DD in UTC, the day the server's `from`/`to` mean. */
const utcDay = (ms: number): string => new Date(ms).toISOString().slice(0, 10);

export default function FilterBar({ filters, onFilter, t }: TabProps): JSX.Element {
  const uid = useId();
  const id = (name: string) => `${uid}-${name}`;
  const [facets, setFacets] = useState<Facets | null>(null);
  const [facetsFailed, setFacetsFailed] = useState(false);
  const [facetsAttempt, setFacetsAttempt] = useState(0);

  const rangeQuery = usageQuery({ ...EMPTY_FILTERS, window: filters.window, from: filters.from, to: filters.to });

  useEffect(() => {
    const ac = new AbortController();
    setFacetsFailed(false);
    loadOverview(rangeQuery, ac.signal).then(
      d => setFacets(d.facets),
      () => { if (!ac.signal.aborted) setFacetsFailed(true); },
    );
    return () => ac.abort();
  }, [rangeQuery, facetsAttempt]);

  const options = useMemo(() => {
    const all: Option = { value: '', label: t('usage.filter.all') };
    /* A value the facets do not list is kept as an option: a deep-linked workspace that spent
       nothing in this period is still the active filter, and a dropdown that cannot show its
       own value reads as "All" while the tables say otherwise. */
    const keep = (list: Option[], cur: string, label = cur): Option[] =>
      cur && !list.some(o => o.value === cur) ? [...list, { value: cur, label }] : list;
    const rank = <K extends string>(order: readonly K[]) => (v: string) => {
      const i = order.indexOf(v as K);
      return i < 0 ? order.length : i;
    };
    const byGroup = rank(GROUPS);
    const byCap = rank(CAPABILITIES);
    const f = facets;
    return {
      window: WINDOWS.map(w => ({ value: w, label: t(`usage.window.${w}`) })),
      tenant: [all, ...keep(
        [...(f?.tenants ?? [])]
          .sort((a, b) => a.name.localeCompare(b.name))
          .map(x => ({ value: x.client_id, label: x.name })),
        filters.client_id,
      )],
      group: [all, ...keep(
        [...(f?.groups ?? [])]
          .sort((a, b) => byGroup(a) - byGroup(b))
          .map(g => ({ value: g, label: groupLabel(t, g) })),
        filters.group, filters.group ? groupLabel(t, filters.group) : '',
      )],
      provider: [all, ...keep((f?.providers ?? []).map(p => ({ value: p, label: p })), filters.provider)],
      model: [all, ...keep((f?.models ?? []).map(m => ({ value: m, label: m })), filters.model)],
      capability: [all, ...keep(
        [...(f?.capabilities ?? [])]
          .sort((a, b) => byCap(a) - byCap(b))
          .map(c => ({ value: c, label: t(`usage.cap.${c}`) })),
        filters.capability, filters.capability ? t(`usage.cap.${filters.capability}`) : '',
      )],
      status: [all, { value: 'ok', label: t('usage.status.ok') }, { value: 'failed', label: t('usage.status.failed') }],
    };
  }, [facets, filters.client_id, filters.group, filters.provider, filters.model, filters.capability, t]);

  const setWindow = (v: string) => {
    const w = v as UsageWindow;
    // Opening a custom range prefilled with the last 30 days, so the switch alone changes
    // nothing on screen; an empty pair would silently fall back to the 30-day preset anyway.
    if (w === 'custom' && !filters.from && !filters.to) {
      const now = Date.now();
      onFilter({ window: w, from: utcDay(now - 29 * 86_400_000), to: utcDay(now) });
    } else {
      onFilter({ window: w });
    }
  };

  // Clearing leaves the period alone: it is the frame the operator is looking through, and
  // losing a hand-picked date range to "remove the workspace filter" would be a punishment.
  const narrowed = NARROWING.some(k => filters[k]);
  const clear = () => onFilter(Object.fromEntries(NARROWING.map(k => [k, ''])) as Partial<Filters>);

  return (
    <div className="card" role="group" aria-label={t('usage.filter.title')}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 12px', alignItems: 'flex-end' }}>
        <Field id={id('window')} label={t('usage.window')}>
          <Select id={id('window')} value={filters.window} onChange={setWindow} options={options.window} />
        </Field>
        {filters.window === 'custom' ? (
          <>
            <Field id={id('from')} label={t('usage.filter.from')}>
              <input
                id={id('from')}
                type="date"
                value={filters.from}
                max={filters.to || undefined}
                onChange={e => onFilter({ from: e.target.value })}
              />
            </Field>
            <Field id={id('to')} label={t('usage.filter.to')}>
              <input
                id={id('to')}
                type="date"
                value={filters.to}
                min={filters.from || undefined}
                onChange={e => onFilter({ to: e.target.value })}
              />
            </Field>
          </>
        ) : null}
        <Field id={id('tenant')} label={t('usage.filter.workspace')}>
          <Select id={id('tenant')} value={filters.client_id} options={options.tenant}
            onChange={v => onFilter({ client_id: v })} />
        </Field>
        <Field id={id('group')} label={t('usage.filter.group')}>
          <Select id={id('group')} value={filters.group} options={options.group}
            onChange={v => onFilter({ group: v })} />
        </Field>
        <Field id={id('provider')} label={t('usage.filter.provider')}>
          <Select id={id('provider')} value={filters.provider} options={options.provider}
            onChange={v => onFilter({ provider: v })} />
        </Field>
        <Field id={id('model')} label={t('usage.filter.model')}>
          <Select id={id('model')} value={filters.model} options={options.model}
            onChange={v => onFilter({ model: v })} />
        </Field>
        <Field id={id('capability')} label={t('usage.filter.kind')}>
          <Select id={id('capability')} value={filters.capability} options={options.capability}
            onChange={v => onFilter({ capability: v })} />
        </Field>
        <Field id={id('status')} label={t('usage.filter.status')}>
          <Select id={id('status')} value={filters.status} options={options.status}
            onChange={v => onFilter({ status: v as Filters['status'] })} />
        </Field>
        {narrowed ? (
          <div style={{ flex: '0 0 auto' }}>
            <button type="button" className="ghost" onClick={clear}>{t('usage.filter.clear')}</button>
          </div>
        ) : null}
      </div>
      {facetsFailed ? (
        <p className="hint" style={{ margin: '10px 0 0' }}>
          {t('usage.filter.facetsfail')}{' '}
          <button type="button" className="ghost" onClick={() => setFacetsAttempt(n => n + 1)}>{t('btn.retry')}</button>
        </p>
      ) : null}
    </div>
  );
}

/** One labelled control. `min-width: 0` lets the flex item shrink below its content's width,
    which is what keeps a long model id from pushing the page sideways on a phone. */
function Field({ id, label, children }: { id: string; label: string; children: ReactNode }): JSX.Element {
  return (
    <div style={{ flex: '1 1 150px', minWidth: 0 }}>
      <label htmlFor={id} style={{ marginTop: 0 }}>{label}</label>
      {children}
    </div>
  );
}
