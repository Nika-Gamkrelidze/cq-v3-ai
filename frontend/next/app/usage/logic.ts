/* Pure helpers for the usage page: no React, no fetch, so `lib/__tests__/usage.test.mts` can
   drive them directly. */

import type { Filters, SortDir, SortState, Totals } from './types';

/** The query string for `/admin/usage/*`: the shared filters plus a tab's own params.
    Empty values are left out, so the server's defaults apply rather than an empty-string
    filter that matches nothing. A custom range sends `from`/`to` and no `window`; a preset
    sends `window` and no dates, so a stale date left in the form never narrows a preset. */
export function usageQuery(
  f: Filters,
  extra: Record<string, string | number | null | undefined> = {},
): string {
  const p = new URLSearchParams();
  if (f.window === 'custom') {
    if (f.from) p.set('from', f.from);
    if (f.to) p.set('to', f.to);
    if (!f.from && !f.to) p.set('window', '30d');
  } else {
    p.set('window', f.window);
  }
  for (const k of ['client_id', 'group', 'provider', 'model', 'capability', 'status'] as const) {
    if (f[k]) p.set(k, f[k]);
  }
  for (const [k, v] of Object.entries(extra)) {
    if (v === null || v === undefined || v === '') continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

/** Clicking a column header: the same column flips direction, a new column starts at
    `firstDir` — descending for numbers ("biggest first" is the question a usage page asks),
    ascending for names. */
export function nextSort(cur: SortState, key: string, firstDir: SortDir = 'desc'): SortState {
  if (cur.key === key) return { key, dir: cur.dir === 'desc' ? 'asc' : 'desc' };
  return { key, dir: firstDir };
}

/** Client-side sort for the small aggregate tables (the server sorts the paginated lists).
    Numbers compare numerically, everything else as locale strings; nulls always sink to the
    bottom whichever way the column is sorted, so "no value" never tops a biggest-first list. */
export function sortRows<R>(rows: R[], s: SortState, get: (row: R, key: string) => unknown): R[] {
  const dir = s.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = get(a, s.key);
    const vb = get(b, s.key);
    const na = va === null || va === undefined || va === '';
    const nb = vb === null || vb === undefined || vb === '';
    if (na || nb) return na === nb ? 0 : na ? 1 : -1;
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
    return String(va).localeCompare(String(vb)) * dir;
  });
}

/** "1–50 of 312", as numbers. `to` never passes `total`; an empty list is 0–0. */
export function pageRange(offset: number, limit: number, total: number): { from: number; to: number } {
  if (!total) return { from: 0, to: 0 };
  return { from: Math.min(offset + 1, total), to: Math.min(offset + limit, total) };
}

export const fmt = (n: number | null | undefined): string => (n ?? 0).toLocaleString();

export const when = (iso?: string | null): string => (iso ? new Date(iso).toLocaleString() : '—');

/** Seconds of audio as m:ss or h:mm:ss. Null/0 is a dash: "no audio" and "zero seconds" both
    read as nothing to bill. */
export function duration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) return '—';
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

/** Cache read + cache write, the one "cached" figure the tables show. */
export const cached = (r: Pick<Totals, 'cache_read_tokens' | 'cache_creation_tokens'>): number =>
  (r.cache_read_tokens || 0) + (r.cache_creation_tokens || 0);

/** "anthropic / claude-sonnet-5", or just the model when the provider is unknown. */
export function providerModel(provider: string | null | undefined, model: string | null | undefined): string {
  const m = model || '—';
  return provider ? `${provider} / ${m}` : m;
}

/** US dollars, precise enough to tell calls apart: whole cents from $1 up, four decimals
    below a dollar (always four, so a column of small amounts lines up) (one call often costs a fraction of a cent, and "$0.00" for every row hides
    which ones are expensive), and anything above zero but under $0.0001 as "<$0.0001" rather
    than a zero that reads as free. Null is a dash — no price, which is not the same as $0. */
export function usd(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs > 0 && abs < 0.0001) return n < 0 ? '−<$0.0001' : '<$0.0001';
  const digits = abs >= 1 || abs === 0 ? 2 : 4;
  return n.toLocaleString('en-US', {
    style: 'currency', currency: 'USD', minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

/** The markup a cost became, as a percentage: (billed − cost) / cost. Null without a cost. */
export function markupOf(m: { cost_usd: number | null; billed_usd: number | null }): number | null {
  if (!m.cost_usd || m.billed_usd === null || m.billed_usd === undefined) return null;
  return ((m.billed_usd - m.cost_usd) / m.cost_usd) * 100;
}
