import { EMPTY, count } from '@/lib/format';
import type { Check, Status } from './types';

type T = (key: string, vars?: Record<string, string | number>) => string;

export const COLOR: Record<Status, string> = {
  ok: 'var(--ok)', warn: 'var(--pending)', crit: 'var(--alert)', unknown: 'var(--muted)',
};
/** The existing pill classes: ready = green, pending = amber, error = red, off = grey. */
export const PILL: Record<Status, string> = { ok: 'ready', warn: 'pending', crit: 'error', unknown: 'off' };
export const RANK: Record<Status, number> = { crit: 3, warn: 2, ok: 1, unknown: 0 };

export const num = (v: unknown): number | null => {
  const n = Number(v);
  return v === null || v === undefined || !Number.isFinite(n) ? null : n;
};

/** Seconds as `45 s` / `3 m` / `2 h` / `4 d` — one unit, the biggest that fits. */
export function span(sec: number | null, t: T): string {
  if (sec === null) return EMPTY;
  const s = Math.max(0, Math.round(sec));
  if (s < 90) return `${s} ${t('noc.u.s')}`;
  if (s < 5400) return `${Math.round(s / 60)} ${t('hl.u.m')}`;
  if (s < 172800) return `${Math.round(s / 3600)} ${t('hl.u.h')}`;
  return `${Math.round(s / 86400)} ${t('hl.u.d')}`;
}

/** "3 m ago" for an ISO timestamp or epoch seconds. */
export function ago(at: string | number | null, t: T): string {
  if (at === null || at === undefined) return EMPTY;
  const ms = typeof at === 'number' ? at * 1000 : Date.parse(at);
  return Number.isNaN(ms) ? EMPTY : span((Date.now() - ms) / 1000, t);
}

const pct = (v: number) => `${v === 0 ? '0' : v < 0.1 ? v.toFixed(3) : v < 10 ? v.toFixed(2) : v.toFixed(0)}%`;
const mb = (v: number) => (v >= 1024 ? `${(v / 1024).toFixed(1)} GB` : `${Math.round(v)} MB`);

export function formatValue(c: Check, t: T): string {
  if (c.value === null || c.value === undefined) return EMPTY;
  if (c.unit === 'text') {
    const key = `noc.v.${String(c.value)}`;
    const word = t(key);
    return word === key ? String(c.value) : word;
  }
  const v = Number(c.value);
  switch (c.unit) {
    case 'pct': return pct(v);
    case 'mb': return mb(v);
    case 'ms': return `${Math.round(v).toLocaleString()} ms`;
    case 's': return span(v, t);
    case 'min': return `${v} ${t('noc.u.min')}`;
    case 'days': return `${v} ${t('noc.u.days')}`;
    case 'per_min': return `${v}${t('noc.u.perMin')}`;
    case 'mbs': return `${v.toFixed(2)} MB/s`;
    case 'ratio': return v.toFixed(2);
    default: return count(v);
  }
}

/** The small line under a tile's number — what the number is a share OF. */
export function detailLine(c: Check, t: T): string {
  const d = c.detail ?? {};
  const g = (k: string) => num(d[k]);
  const of = (a: number | null, b: number | null) => (a === null || b === null ? '' : t('noc.d.of', { a, b }));
  switch (c.key) {
    case 'cpu': return g('cores') === null ? '' : t('noc.d.cores', { n: g('cores')! });
    case 'mem': return g('available_mb') === null ? '' : t('noc.d.mem', { avail: g('available_mb')!, total: g('total_mb')! });
    case 'disk': return of(num(g('used_gb')?.toFixed(1)), g('total_gb') === null ? null : Math.round(g('total_gb')!)) + ' GB';
    case 'db_conns': return of(g('used'), g('max'));
    case 'db_pool': return of(g('used'), g('size'));
    case 'http_5xx': return g('requests') === null ? '' : t('noc.d.requests', { errors: g('errors') ?? 0, requests: g('requests')! });
    case 'jobs_stuck': return g('minutes') === null ? '' : t('noc.d.minutes', { n: g('minutes')! });
    case 'net_loss': return g('window_min') === null ? '' : t('noc.d.window', { n: g('window_min')! });
    case 'net_rate': return d.rx === undefined ? '' : `↓ ${num(d.rx)?.toFixed(2)} · ↑ ${num(d.tx)?.toFixed(2)}`;
    case 'embeddings': case 'tls': case 'collector': case 'worker':
      return String(d.error ?? d.reason ?? d.model ?? '');
    case 'voice_tone': return String(d.model ?? '');
    default: return '';
  }
}

export const TIPS = new Set([
  'sampler', 'mem', 'load', 'net_loss', 'net_errors', 'db_pool', 'embeddings', 'voice_tone', 'worker',
  'http_5xx', 'jobs_stuck', 'reembed_backlog', 'tls',
]);
