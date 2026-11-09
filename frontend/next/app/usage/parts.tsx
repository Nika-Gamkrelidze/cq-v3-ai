'use client';
import type { JSX, ReactNode } from 'react';
import { duration, fmt, pageRange, usd } from './logic';
import type { Money, SortState, T } from './types';

/* The pieces every usage table is built from, so a sortable header or a pager looks and
   behaves the same on all four tabs. */

/** A sortable column header. `aria-sort` on the <th>, the arrow in the label, and a real
    <button> inside so the header is reachable and operable from the keyboard. */
export function SortTh({
  label, k, sort, onSort, num = true,
}: {
  label: string;
  k: string;
  sort: SortState;
  onSort: (key: string) => void;
  /** Right-align (numbers). Names and dates pass false. */
  num?: boolean;
}): JSX.Element {
  const active = sort.key === k;
  return (
    <th
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
      style={{ textAlign: num ? 'right' : 'left', whiteSpace: 'nowrap' }}
    >
      <button type="button" className="linklike" onClick={() => onSort(k)}
        style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer' }}>
        {label}{active ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
      </button>
    </th>
  );
}

/** "1–50 of 312" with previous / next. Hidden entirely when everything fits on one page. */
export function Pager({
  offset, limit, total, onPage, t,
}: {
  offset: number;
  limit: number;
  total: number;
  onPage: (offset: number) => void;
  t: T;
}): JSX.Element | null {
  if (total <= limit && offset === 0) return null;
  const { from, to } = pageRange(offset, limit, total);
  return (
    <div className="inline" style={{ gap: 10, justifyContent: 'flex-end', marginTop: 12, flexWrap: 'wrap' }}>
      <span className="hint">{t('usage.pager.range', { from: fmt(from), to: fmt(to), total: fmt(total) })}</span>
      <button type="button" className="ghost" disabled={offset <= 0}
        onClick={() => onPage(Math.max(0, offset - limit))}>
        ← {t('usage.pager.prev')}
      </button>
      <button type="button" className="ghost" disabled={offset + limit >= total}
        onClick={() => onPage(offset + limit)}>
        {t('usage.pager.next')} →
      </button>
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }): JSX.Element {
  return (
    <div className="stat" title={hint}>
      <b>{value}</b>
      <span>{label}</span>
    </div>
  );
}

/** What a call consumed, in the unit its provider bills: tokens first, then audio length
    (speech-to-text — ElevenLabs Scribe reports no tokens at all) and characters (text-to-
    speech). A call with none of the three shows a dash rather than a misleading 0 tokens. */
export function Consumed({
  tokens, audioSeconds, characters, t,
}: {
  tokens: number | null | undefined;
  audioSeconds?: number | null;
  characters?: number | null;
  t: T;
}): JSX.Element {
  const parts: string[] = [];
  if (tokens) parts.push(t('usage.unit.tokens', { n: fmt(tokens) }));
  if (audioSeconds) parts.push(t('usage.unit.audio', { d: duration(audioSeconds) }));
  if (characters) parts.push(t('usage.unit.chars', { n: fmt(characters) }));
  return <span style={{ whiteSpace: 'nowrap' }}>{parts.length ? parts.join(' · ') : '—'}</span>;
}

/** An analyser group's name, e.g. "Fact-check". */
export const groupLabel = (t: T, group: string): string => t(`usage.group.${group}`);

/** A call's status pill. */
export function StatusPill({ ok, t }: { ok: boolean; t: T }): JSX.Element {
  return <span className={`pill ${ok ? 'ready' : 'error'}`}>{t(ok ? 'usage.status.ok' : 'usage.status.failed')}</span>;
}

/** A sum of money with its caveat: when some of the calls behind it had no price, the figure
    is a floor, and it says so (a "+?" flag, and the count in the tooltip) instead of passing for
    the whole cost. `field` picks cost to us or the amount to bill. */
export function MoneySum({ m, field, t, strong = false }: {
  m: Money & { calls?: number }; field: 'cost_usd' | 'billed_usd'; t: T; strong?: boolean;
}): JSX.Element {
  // Not one call priced: there is no sum to show, only the fact that it is missing.
  if (m.calls && m.unpriced_calls === m.calls) {
    return <span className="hint" style={{ whiteSpace: 'nowrap' }}>{t('um.noprice')}</span>;
  }
  const value = usd(m[field]);
  const tip = m.unpriced_calls ? t('um.unpriced.tip', { n: fmt(m.unpriced_calls) }) : undefined;
  return (
    <span style={{ whiteSpace: 'nowrap' }} title={tip}>
      {strong ? <b>{value}</b> : value}
      {m.unpriced_calls ? (
        <span className="warn-flag" style={{ marginLeft: 4 }}>
          +?<span className="cq-sr"> {tip}</span>
        </span>
      ) : null}
    </span>
  );
}

/** One call's money: what it cost and, on hover, the markup and the amount to bill. A call
    with no price in force says "no price" — never $0, which would read as free. */
export function CallMoney({ cost, billed, markup, t }: {
  cost: number | null; billed: number | null; markup: number | null; t: T;
}): JSX.Element {
  if (cost === null) return <span className="hint" style={{ whiteSpace: 'nowrap' }}>{t('um.noprice')}</span>;
  const tip = t('um.call.tip', { cost: usd(cost), pct: fmt(markup ?? 0), billed: usd(billed) });
  return <span style={{ whiteSpace: 'nowrap' }} title={tip}>{usd(cost)}</span>;
}
