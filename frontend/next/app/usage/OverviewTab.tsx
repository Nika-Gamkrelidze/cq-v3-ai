'use client';
import { useEffect, useMemo, useState } from 'react';
import type { JSX, KeyboardEvent, ReactNode } from 'react';
import { LineChart, type ChartPoint, type ChartSeries } from '@/components/ui/LineChart';
import { loadOverview } from './Filters';
import { LoadError } from './listKit';
import { cached, duration, fmt, nextSort, providerModel, sortRows, usageQuery, when } from './logic';
import { SortTh, Stat, groupLabel } from './parts';
import type { Overview, SortState, T, TabProps, Totals } from './types';

/* Overview: the totals for whatever the filter bar selects, the token curve over the range,
   and one aggregate table per dimension. Those tables are small (one row per workspace,
   provider, model…) and arrive whole, so they sort in the browser; the three lists on the
   other tabs are paginated and sort on the server.

   Clicking a workspace, analyser, provider or model row narrows the SHARED filters rather than
   opening a drill-down, so the question "where did this model's tokens go" is answered by the
   same bar the operator can read and undo. */

const STEP_MS = { hour: 3_600_000, day: 86_400_000 } as const;
/** Past this the range is drawn from the server's points as they are, not gap-filled. */
const MAX_POINTS = 2000;

/** The token series with its empty buckets put back as zeros. The server returns only the
    buckets that had calls, and a line drawn straight between two busy days reads as usage
    across the quiet week in between. Buckets are `date_trunc` in UTC, i.e. whole multiples of
    the step in epoch milliseconds, so they can be regenerated exactly. `to` is exclusive (a
    custom range ends at the next midnight), hence the millisecond back. */
function tokenPoints(ov: Overview): ChartPoint[] {
  const step = STEP_MS[ov.bucket] ?? STEP_MS.day;
  const raw = ov.series.map(p => ({ t: Date.parse(p.t), v: p.total_tokens }));
  const start = Math.floor(Date.parse(ov.from) / step) * step;
  const end = Math.floor((Date.parse(ov.to) - 1) / step) * step;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start || (end - start) / step > MAX_POINTS) {
    return raw;
  }
  const have = new Map(raw.map(p => [Math.floor(p.t / step) * step, p.v]));
  const out: ChartPoint[] = [];
  for (let ts = start; ts <= end; ts += step) out.push({ t: ts, v: have.get(ts) ?? 0 });
  return out;
}

const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 });

export default function OverviewTab({ filters, onFilter, t }: TabProps): JSX.Element {
  const [data, setData] = useState<Overview | null>(null);
  // null = no error. The thrown value itself is kept and worded at render time (`LoadError`
  // -> apiMessage), so the operator reads the server's sentence or a translated one — never a
  // raw "HTTP 504" — and a language switch does not have to refetch to translate it.
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const query = usageQuery(filters);

  useEffect(() => {
    const ac = new AbortController();
    setData(null);
    setError(null);
    loadOverview(query, ac.signal).then(setData, e => {
      if (ac.signal.aborted) return;
      setError(e ?? new Error(''));
    });
    return () => ac.abort();
  }, [query, attempt]);

  const narrowed = Boolean(filters.client_id || filters.group || filters.provider || filters.model
    || filters.capability || filters.status);

  const workspaceName = useMemo(() => {
    if (!filters.client_id || !data) return '';
    return data.by_tenant.find(r => r.client_id === filters.client_id)?.name
      ?? data.facets.tenants.find(r => r.client_id === filters.client_id)?.name
      ?? filters.client_id;
  }, [data, filters.client_id]);

  const chart = useMemo(() => {
    if (!data) return null;
    // Hour buckets on a range that crosses midnight repeat their HH:MM, so the day is added;
    // day buckets are UTC days and are labelled as such, or a viewer west of UTC would see
    // every bar one day early.
    const pts = tokenPoints(data);
    const multiDay = pts.length > 1 && pts[pts.length - 1].t - pts[0].t >= STEP_MS.day;
    const time = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });
    const dayLocal = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
    const dayUtc = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
    const formatX = data.bucket === 'hour'
      ? (ts: number) => (multiDay ? `${dayLocal.format(ts)} ${time.format(ts)}` : time.format(ts))
      : (ts: number) => dayUtc.format(ts);
    const series: ChartSeries[] = [{ name: t('usage.th.total'), color: 'var(--beam)', points: pts }];
    return { series, formatX };
  }, [data, t]);

  if (error !== null) {
    return (
      <div className="card">
        <LoadError error={error} onRetry={() => setAttempt(n => n + 1)} t={t} />
      </div>
    );
  }

  if (!data) {
    return <div className="card"><div className="empty"><span className="spinner" /></div></div>;
  }

  const tot = data.total;
  const heading = (
    <div className="inline" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
      <h3 style={{ margin: 0, minWidth: 0, overflowWrap: 'anywhere' }}>
        {filters.client_id ? workspaceName : t('usage.scope.all')}
      </h3>
      {filters.client_id ? (
        <a className="ghost" href={`/ai-config#${filters.client_id}`}>{t('usage.configure')}</a>
      ) : null}
    </div>
  );

  if (!tot.calls) {
    return (
      <div className="card">
        {heading}
        <div className="empty">{t(narrowed ? 'usage.none.filtered' : 'usage.none')}</div>
      </div>
    );
  }

  return (
    <>
      <div className="card">
        {heading}
        <div className="stat-row">
          <Stat label={t('usage.th.total')} value={fmt(tot.total_tokens)} />
          <Stat label={t('usage.th.in')} value={fmt(tot.input_tokens)} />
          <Stat label={t('usage.th.out')} value={fmt(tot.output_tokens)} />
          <Stat label={t('usage.th.cache')} value={fmt(cached(tot))} />
          <Stat label={t('usage.th.calls')} value={fmt(tot.calls)} />
          <Stat label={t('usage.th.failed')} value={fmt(tot.failed)} />
          <Stat label={t('usage.th.audio')} value={duration(tot.audio_seconds)} />
          <Stat label={t('usage.th.chars')} value={tot.characters ? fmt(tot.characters) : '—'} />
        </div>
        {/* Only when there IS spend on the customer's own key: on an ordinary workspace it
            would be a permanent zero explaining a distinction that does not apply. */}
        {tot.byo_tokens ? (
          <p className="hint" style={{ marginTop: 10 }}>
            <b>{t('usage.th.byo')}: {fmt(tot.byo_tokens)}</b> — {t('usage.byo.hint')}
          </p>
        ) : null}
      </div>

      {chart ? (
        <div className="card">
          <h3 style={{ margin: '0 0 8px' }}>{t(data.bucket === 'hour' ? 'usage.chart.hour' : 'usage.chart.day')}</h3>
          {/* Tokens only: calls are two or three orders of magnitude smaller and would lie flat
              along the axis on a shared scale, so they are given as a figure instead. */}
          <LineChart
            series={chart.series}
            formatX={chart.formatX}
            formatY={v => compact.format(v)}
            emptyText={t('usage.empty.section')}
          />
          <p className="hint" style={{ margin: '8px 0 0' }}>
            {t('usage.chart.calls', { calls: fmt(tot.calls), failed: fmt(tot.failed) })}
          </p>
        </div>
      ) : null}

      <AggTable
        title={t('usage.bytenant')}
        rows={data.by_tenant}
        rowKey={r => r.client_id ?? 'none'}
        name={{ label: t('usage.th.tenant'), value: r => (r.client_id ? r.name : t('usage.noworkspace')),
          render: r => (r.client_id
            ? <><b>{r.name}</b>{r.slug ? <span className="hint"> · {r.slug}</span> : null}</>
            : <span className="hint">{t('usage.noworkspace')}</span>) }}
        // A null workspace is a deleted one or none at all; there is nothing to filter to.
        onRow={r => (r.client_id && r.client_id !== filters.client_id
          ? () => onFilter({ client_id: r.client_id! }) : null)}
        t={t}
      />
      <AggTable
        title={t('usage.bygroup')}
        rows={data.by_group}
        rowKey={r => r.group}
        name={{ label: t('usage.filter.group'), value: r => groupLabel(t, r.group) }}
        onRow={r => (r.group !== filters.group ? () => onFilter({ group: r.group }) : null)}
        t={t}
      />
      <AggTable
        title={t('usage.byprovider')}
        rows={data.by_provider}
        rowKey={r => r.provider}
        name={{ label: t('usage.filter.provider'), value: r => r.provider }}
        onRow={r => (r.provider && r.provider !== filters.provider ? () => onFilter({ provider: r.provider }) : null)}
        t={t}
      />
      <AggTable
        title={t('usage.bymodel')}
        rows={data.by_model}
        rowKey={r => `${r.provider}/${r.model}/${r.capability}`}
        name={{ label: t('usage.th.model'), value: r => providerModel(r.provider, r.model),
          render: r => <span style={{ overflowWrap: 'anywhere' }}>{providerModel(r.provider, r.model)}</span> }}
        extra={[{ key: 'capability', label: t('usage.filter.kind'), value: r => t(`usage.cap.${r.capability || 'llm'}`) }]}
        onRow={r => (r.model && r.model !== filters.model ? () => onFilter({ model: r.model }) : null)}
        t={t}
      />
      <AggTable
        title={t('usage.byfeature')}
        rows={data.by_feature}
        rowKey={r => r.feature}
        name={{ label: t('usage.th.feature'), value: r => r.feature, render: r => <code>{r.feature}</code> }}
        extra={[{ key: 'group', label: t('usage.filter.group'), value: r => groupLabel(t, r.group) }]}
        t={t}
      />
      <AggTable
        title={t('usage.byuser')}
        rows={data.by_user}
        rowKey={r => `${r.actor}|${r.client_id ?? ''}`}
        name={{ label: t('usage.th.user'), value: r => (r.actor === 'unattributed' ? t('usage.unattributed') : r.actor),
          render: r => (r.actor === 'unattributed'
            ? <span className="hint">{t('usage.unattributed')}</span>
            : <span style={{ overflowWrap: 'anywhere' }}>{r.actor}</span>) }}
        extra={[{ key: 'workspace', label: t('usage.th.tenant'),
          value: r => (r.client_id ? r.name : t('usage.noworkspace')) }]}
        t={t}
      />
    </>
  );
}

/* ------------------------------------------------------------------ aggregate table */

interface TextCol<R> {
  label: string;
  /** What the column sorts by and, without `render`, what it shows. */
  value: (r: R) => string;
  render?: (r: R) => ReactNode;
}

interface NumCol {
  key: string;
  label: string;
  value: (r: Totals) => number | string | null;
  render: (r: Totals) => ReactNode;
  num: boolean;
}

/** The Totals columns every aggregate table ends with. Audio and characters appear only when
    some row in THAT table has any: most tables are text-model spend, and a column of dashes is
    width a phone does not have. */
function totalsColumns(rows: Totals[], t: T): NumCol[] {
  const cols: NumCol[] = [
    { key: 'total_tokens', label: t('usage.th.total'), value: r => r.total_tokens, render: r => <b>{fmt(r.total_tokens)}</b>, num: true },
    { key: 'input_tokens', label: t('usage.th.in'), value: r => r.input_tokens, render: r => fmt(r.input_tokens), num: true },
    { key: 'output_tokens', label: t('usage.th.out'), value: r => r.output_tokens, render: r => fmt(r.output_tokens), num: true },
    { key: 'cached', label: t('usage.th.cache'), value: r => cached(r), render: r => fmt(cached(r)), num: true },
  ];
  if (rows.some(r => r.audio_seconds > 0)) {
    cols.push({ key: 'audio_seconds', label: t('usage.th.audio'), value: r => r.audio_seconds, render: r => duration(r.audio_seconds), num: true });
  }
  if (rows.some(r => r.characters > 0)) {
    cols.push({ key: 'characters', label: t('usage.th.chars'), value: r => r.characters,
      render: r => (r.characters ? fmt(r.characters) : '—'), num: true });
  }
  cols.push(
    { key: 'calls', label: t('usage.th.calls'), value: r => r.calls, render: r => fmt(r.calls), num: true },
    { key: 'failed', label: t('usage.th.failed'), value: r => r.failed,
      render: r => (r.failed ? <span className="warn-flag">{fmt(r.failed)}</span> : '—'), num: true },
    { key: 'avg_latency_ms', label: t('usage.th.latency'), value: r => r.avg_latency_ms,
      render: r => (r.avg_latency_ms === null ? '—' : fmt(r.avg_latency_ms)), num: true },
    { key: 'last_used', label: t('usage.th.last'), value: r => r.last_used,
      render: r => <span className="hint" style={{ whiteSpace: 'nowrap' }}>{when(r.last_used)}</span>, num: false },
  );
  return cols;
}

const NAME_KEY = '__name';

function AggTable<R extends Totals>({
  title, rows, rowKey, name, extra = [], onRow, t,
}: {
  title: string;
  rows: R[];
  rowKey: (r: R) => string;
  name: TextCol<R>;
  /** Text columns between the name and the numbers (a model's kind, a feature's analyser). */
  extra?: (TextCol<R> & { key: string })[];
  /** The row's click action, or null when that row is not clickable. */
  onRow?: (r: R) => (() => void) | null;
  t: T;
}): JSX.Element {
  const [sort, setSort] = useState<SortState>({ key: 'total_tokens', dir: 'desc' });
  const nums = useMemo(() => totalsColumns(rows, t), [rows, t]);

  // Not memoised: the parent rebuilds `name`/`extra` every render, and a table of a few dozen
  // aggregate rows sorts in well under a frame.
  const text = new Map<string, TextCol<R>>([[NAME_KEY, name], ...extra.map(c => [c.key, c] as const)]);
  const num = new Map(nums.map(c => [c.key, c]));
  const sorted = sortRows(rows, sort, (r, k) => text.get(k)?.value(r) ?? num.get(k)?.value(r));

  // Names start A→Z; numbers and dates biggest / latest first.
  const onSort = (k: string) => setSort(s => nextSort(s, k, k === NAME_KEY || extra.some(c => c.key === k) ? 'asc' : 'desc'));

  return (
    <div className="card">
      <h3>{title}</h3>
      {!rows.length ? (
        <div className="empty">{t('usage.empty.section')}</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <SortTh label={name.label} k={NAME_KEY} sort={sort} onSort={onSort} num={false} />
                {extra.map(c => <SortTh key={c.key} label={c.label} k={c.key} sort={sort} onSort={onSort} num={false} />)}
                {nums.map(c => <SortTh key={c.key} label={c.label} k={c.key} sort={sort} onSort={onSort} num={c.num} />)}
              </tr>
            </thead>
            <tbody>
              {sorted.map(r => {
                const act = onRow?.(r) ?? null;
                const onKeyDown = act
                  ? (e: KeyboardEvent<HTMLTableRowElement>) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(); }
                  }
                  : undefined;
                return (
                  <tr
                    key={rowKey(r)}
                    onClick={act ?? undefined}
                    onKeyDown={onKeyDown}
                    tabIndex={act ? 0 : undefined}
                    title={act ? t('usage.row.filter') : undefined}
                    style={act ? { cursor: 'pointer' } : undefined}
                  >
                    <td>{name.render ? name.render(r) : name.value(r)}</td>
                    {extra.map(c => <td key={c.key}>{c.render ? c.render(r) : c.value(r)}</td>)}
                    {nums.map(c => (
                      <td key={c.key} style={c.num ? { textAlign: 'right', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' } : undefined}>
                        {c.render(r)}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
