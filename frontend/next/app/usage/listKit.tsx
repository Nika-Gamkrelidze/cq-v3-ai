'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, JSX, KeyboardEvent, MouseEvent, ReactNode, RefObject } from 'react';
import { Tip } from '@/components/ui/Tip';
import { apiGet, apiMessage } from '@/lib/session';
import { cached, duration, fmt } from './logic';
import { Stat } from './parts';
import type { SortState, T, Totals } from './types';

/* The plumbing the three server-sorted lists and the two drill-downs share: one fetch hook,
   one load/error/empty gate, the search box, the row-that-opens-a-panel behaviour. Kept apart
   from `parts.tsx` (the Overview's pieces too) so the lists could be built without editing a
   file another tab was building on. */

/** Right-aligned, fixed-width digits: a column of token counts only compares at a glance when
    the digits line up. */
export const NUM: CSSProperties = { textAlign: 'right', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' };

/** One line, cut with an ellipsis. File names and conversation ids are unbounded, and a long
    one would otherwise widen every row of the table to fit it. */
export const CLIP: CSSProperties = {
  maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
};

/** A button that reads as a link: it opens a panel or narrows a filter, it never navigates. */
export const LINK: CSSProperties = {
  background: 'none', border: 0, padding: 0, font: 'inherit', color: 'var(--beam)',
  cursor: 'pointer', textAlign: 'left', maxWidth: '100%',
};

const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';

/** GET an `/admin/usage/*` path whenever it changes; the previous request is aborted, so a
    fast typist or a double-clicked header never lets an older answer overwrite a newer one.

    `keepStale` is for the lists: while page 2 loads, page 1 stays on screen (dimmed) instead
    of the table collapsing into a spinner and jumping the page. A drill-down passes false —
    showing the previous recording's calls under the next recording's name would be a lie. */
export function useUsageGet<R>(path: string | null, keepStale = false): {
  data: R | null; error: unknown; loading: boolean; retry: () => void;
} {
  const [got, setGot] = useState<{ path: string; data: R } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!path) return;
    const ctl = new AbortController();
    setLoading(true);
    setError(null);
    // Guarded on the CONTROLLER, not the error type: an abort that lands while the body is
    // still streaming surfaces as a parse failure rather than an AbortError, and that stale
    // error would otherwise sit over the newer request's table.
    apiGet<R>(path, { scope: 'admin', signal: ctl.signal })
      .then(data => {
        if (ctl.signal.aborted) return;
        setGot({ path, data });
        setError(null);
        setLoading(false);
      })
      .catch(e => {
        if (ctl.signal.aborted || isAbort(e)) return;
        setError(e ?? new Error('failed'));
        setLoading(false);
      });
    return () => ctl.abort();
  }, [path, nonce]);

  const retry = useCallback(() => setNonce(n => n + 1), []);
  const data = got && (keepStale || got.path === path) ? got.data : null;
  return { data, error, loading, retry };
}

/** A value that settles `ms` after the last change — the search box, so a filename typed
    letter by letter is one request rather than twelve. */
export function useDebounced<V>(value: V, ms = 300): V {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return settled;
}

export function Loading({ t }: { t: T }): JSX.Element {
  return (
    <div className="empty" role="status">
      <span className="spinner" aria-hidden="true" />
      <span className="cq-sr">{t('ul.loading')}</span>
    </div>
  );
}

/** A failed load, with the server's own sentence and a way to try again. Never rendered as
    "nothing here": an outage that reads as an empty list is how an operator concludes a
    workspace spent nothing. */
export function LoadError({ error, onRetry, t }: { error: unknown; onRetry: () => void; t: T }): JSX.Element {
  return (
    <div role="alert">
      <p className="msg err" style={{ marginTop: 0 }}>{t('usage.loadfail')} {apiMessage(error, t)}</p>
      <div className="actions" style={{ marginTop: 10 }}>
        <button type="button" className="ghost" onClick={onRetry}>{t('ul.retry')}</button>
      </div>
    </div>
  );
}

/** Error, first load, empty, or the content — in that order of precedence. A reload over
    content already on screen dims it rather than replacing it. */
export function ListBody({
  t, error, loading, hasData, isEmpty, emptyText, onRetry, children,
}: {
  t: T;
  error: unknown;
  loading: boolean;
  hasData: boolean;
  isEmpty: boolean;
  emptyText: string;
  onRetry: () => void;
  children: ReactNode;
}): JSX.Element {
  if (error) return <LoadError error={error} onRetry={onRetry} t={t} />;
  // An empty answer still on screen while a new search runs would announce "nothing matches"
  // for a query that has not come back yet.
  if (!hasData || (loading && isEmpty)) return <Loading t={t} />;
  if (isEmpty) return <div className="empty">{emptyText}</div>;
  return (
    <div aria-busy={loading} style={{ opacity: loading ? 0.6 : 1, transition: 'opacity .15s' }}>
      {children}
    </div>
  );
}

export function SearchBox({ value, onChange, label }: {
  value: string; onChange: (v: string) => void; label: string;
}): JSX.Element {
  return (
    <input
      type="search"
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={label}
      aria-label={label}
      style={{ maxWidth: 380 }}
    />
  );
}

/** A sortable header that `SortTh` cannot express: an ⓘ beside the label, and/or a second,
    smaller sort button in the same cell ("Recording · date") — the recording's date sits under
    its name rather than in a column of its own, so its sort lives there too. Without `k` the
    label is plain text (a column the server cannot sort by). */
export function HeadTh({
  label, k, alt, tip, sort, onSort, num = true,
}: {
  label: string;
  k?: string;
  alt?: { label: string; k: string };
  tip?: string;
  sort: SortState;
  onSort: (key: string) => void;
  num?: boolean;
}): JSX.Element {
  const arrow = (key: string) => (sort.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '');
  const active = sort.key === k || sort.key === alt?.k;
  const btn: CSSProperties = { background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer' };
  return (
    <th
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : k || alt ? 'none' : undefined}
      style={{ textAlign: num ? 'right' : 'left', whiteSpace: 'nowrap' }}
    >
      {k ? <button type="button" style={btn} onClick={() => onSort(k)}>{label}{arrow(k)}</button> : label}
      {alt ? (
        <>
          <span className="hint" aria-hidden="true"> · </span>
          <button type="button" style={{ ...btn, color: 'var(--muted)', fontWeight: 400 }} onClick={() => onSort(alt.k)}>
            {alt.label}{arrow(alt.k)}
          </button>
        </>
      ) : null}
      {tip ? <> <Tip text={tip} /></> : null}
    </th>
  );
}

/** Props that make a table row open its drill-down by mouse, Enter or Space. A click that
    lands on a control inside the row (the workspace filter link) is that control's, not the
    row's; `data-return` is where focus goes back to when the panel closes. */
export function rowOpen(id: string, onOpen: () => void) {
  return {
    tabIndex: 0,
    'data-return': id,
    style: { cursor: 'pointer' } as CSSProperties,
    onClick: (e: MouseEvent<HTMLTableRowElement>) => {
      if ((e.target as HTMLElement).closest('button, a, input')) return;
      onOpen();
    },
    onKeyDown: (e: KeyboardEvent<HTMLTableRowElement>) => {
      if (e.target !== e.currentTarget) return;
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(); }
    },
  };
}

/** Scroll so `el`'s top is visible, below the sticky header rather than under it — for a
    panel opened, or a page turned, from far down a long table. Does nothing when it already is. */
export function revealTop(el: HTMLElement | null): void {
  if (!el) return;
  const header = document.querySelector('.app-header')?.getBoundingClientRect().height ?? 0;
  const top = el.getBoundingClientRect().top;
  if (top >= header) return;
  window.scrollTo({ top: Math.max(0, window.scrollY + top - header - 12) });
}

/** A drill-down that REPLACES its list and gives it back intact. The list's sort, page and
    search live in the tab (which stays mounted), so going back is a re-render, not a refetch;
    this hook restores the rest — the scroll position, and keyboard focus on the row that was
    opened, so Back does not drop a keyboard user at the top of the page. */
export function usePanel<P>(): {
  panel: P | null;
  open: (p: P, returnId?: string | null) => void;
  back: () => void;
  topRef: RefObject<HTMLDivElement | null>;
} {
  const [panel, setPanel] = useState<P | null>(null);
  const topRef = useRef<HTMLDivElement | null>(null);
  const saved = useRef<{ y: number; focus: string | null } | null>(null);

  const open = useCallback((p: P, returnId: string | null = null) => {
    saved.current = { y: window.scrollY, focus: returnId };
    setPanel(p);
  }, []);
  const back = useCallback(() => setPanel(null), []);

  useEffect(() => {
    if (panel !== null) {
      // Opened from a row far down a long list: the panel starts where that row was.
      revealTop(topRef.current);
    } else if (saved.current) {
      const s = saved.current;
      saved.current = null;
      window.scrollTo({ top: s.y });
      if (s.focus) {
        document.querySelector<HTMLElement>(`[data-return="${CSS.escape(s.focus)}"]`)?.focus({ preventScroll: true });
      }
    }
  }, [panel]);

  return { panel, open, back, topRef };
}

/** Where a new list starts sorting a column: names A→Z, numbers and dates biggest/newest first. */
export const firstDir = (key: string, ascFirst: ReadonlySet<string>) => (ascFirst.has(key) ? 'asc' : 'desc');

/** A workspace name that narrows the shared filters to that workspace. Plain text when the
    list is already narrowed to it, or when the call has no workspace to narrow to. */
export function TenantCell({ id, name, active, onFilter, t }: {
  id: string | null; name: string | null; active: string; onFilter: (id: string) => void; t: T;
}): JSX.Element {
  const label = name || t('usage.unattributed');
  if (!id || active === id) return <span>{label}</span>;
  return (
    <button type="button" style={LINK} title={t('ul.filter.tenant')} onClick={() => onFilter(id)}>
      {label}
    </button>
  );
}

/* ---- The drill-down panels' header pieces -------------------------------------------- */

export function Kv({ label, children }: { label: string; children: ReactNode }): JSX.Element {
  return <div className="kv"><b>{label}</b> <span style={{ overflowWrap: 'anywhere' }}>{children}</span></div>;
}

export const KV_GRID = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,240px),1fr))', gap: '0 18px', marginTop: 10,
} as const;

/** The totals row both drill-downs open with. Audio and characters only appear when there
    are any — a chat conversation has neither, and a tile reading "—" twice is noise. */
export function TotalsStats({ total, t, extra }: { total: Totals; t: T; extra?: ReactNode }): JSX.Element {
  return (
    <div className="stat-row">
      <Stat label={t('usage.th.total')} value={fmt(total.total_tokens)} />
      <Stat label={t('usage.th.in')} value={fmt(total.input_tokens)} />
      <Stat label={t('usage.th.out')} value={fmt(total.output_tokens)} />
      <Stat label={t('usage.th.cache')} value={fmt(cached(total))} />
      <Stat label={t('usage.th.calls')} value={fmt(total.calls)} />
      <Stat label={t('usage.th.failed')} value={fmt(total.failed)} />
      {total.audio_seconds ? <Stat label={t('ul.stat.audio')} value={duration(total.audio_seconds)} /> : null}
      {total.characters ? <Stat label={t('ul.stat.chars')} value={fmt(total.characters)} /> : null}
      <Stat label={t('ul.stat.latency')} value={total.avg_latency_ms === null ? '—' : fmt(total.avg_latency_ms)} />
      {extra}
    </div>
  );
}

export function BackButton({ label, onBack }: { label: string; onBack: () => void }): JSX.Element {
  const ref = useRef<HTMLButtonElement>(null);
  // Focus lands on the way back: a keyboard user who opened the panel from a row is otherwise
  // left on a row that no longer exists.
  useEffect(() => { ref.current?.focus({ preventScroll: true }); }, []);
  return (
    <button ref={ref} type="button" className="ghost" onClick={onBack} style={{ marginBottom: 14 }}>
      ← {label}
    </button>
  );
}
