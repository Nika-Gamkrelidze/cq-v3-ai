/* The pure half of the NOC history view — no `@/` imports, so the unit tests load it straight
   from Node. Days are LOCAL calendar days (the browser's zone, which is also the `tz` the days
   route is asked for), and every boundary is built through `Date` rather than "+ 24 h", so a
   day with a daylight-saving change has 23 or 25 hours and the timeline simply has fewer or more
   cells. */

import type { DayRow, HistorySample, Status } from './types';

export const CELL_MS = 5 * 60_000;
const RANK: Record<Status, number> = { crit: 3, warn: 2, ok: 1, unknown: 0 };

const pad = (n: number) => String(n).padStart(2, '0');

/** `YYYY-MM-DD` of a Date in the local zone. */
export const localDay = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

const parts = (day: string): [number, number, number] | null => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
};

/** The day `n` calendar days after `day` (negative = before). Calendar arithmetic, not 24 h steps. */
export function shiftDay(day: string, n: number): string {
  const p = parts(day);
  if (!p) return day;
  return localDay(new Date(p[0], p[1] - 1, p[2] + n));
}

/** Local midnight to the next local midnight, as epoch ms; null for a string that is no date. */
export function dayBounds(day: string): { from: number; to: number } | null {
  const p = parts(day);
  if (!p) return null;
  return { from: new Date(p[0], p[1] - 1, p[2]).getTime(), to: new Date(p[0], p[1] - 1, p[2] + 1).getTime() };
}

const worse = (a: Status | null, b: Status): Status => (a === null || RANK[b] > RANK[a] ? b : a);

export interface Cell {
  start: number;
  end: number;
  /** Worst stored status inside the cell; null = nothing was stored. */
  status: Status | null;
  n: number;
  /** The first few `bad` entries seen in the cell, worst first. */
  bad: string[];
}

/** Cut [from, to) into `stepMs` cells and colour each by the worst sample inside it. Samples
    outside the range are ignored. */
export function bucketSamples(samples: HistorySample[], from: number, to: number, stepMs = CELL_MS): Cell[] {
  const count = Math.max(0, Math.ceil((to - from) / stepMs));
  const cells: Cell[] = Array.from({ length: count }, (_, i) => ({
    start: from + i * stepMs, end: Math.min(to, from + (i + 1) * stepMs), status: null, n: 0, bad: [],
  }));
  for (const s of samples) {
    const ms = Date.parse(s.ts);
    const i = Math.floor((ms - from) / stepMs);
    const c = cells[i];
    if (!c || ms < from || ms >= to) continue;
    c.status = worse(c.status, s.status);
    c.n += 1;
    for (const b of s.bad ?? []) if (!c.bad.includes(b)) c.bad.push(b);
  }
  for (const c of cells) c.bad.sort((a, b) => RANK[parseBad(b).status] - RANK[parseBad(a).status]);
  return cells;
}

export interface Bad { status: Status; kind: string; name: string }

/** `crit:check:disk:/` -> { crit, check, disk:/ } — only the first two colons separate; names carry their own. */
export function parseBad(s: string): Bad {
  const a = s.indexOf(':');
  const b = a < 0 ? -1 : s.indexOf(':', a + 1);
  if (b < 0) return { status: 'unknown', kind: '', name: s };
  const st = s.slice(0, a) as Status;
  return { status: st in RANK ? st : 'unknown', kind: s.slice(a + 1, b), name: s.slice(b + 1) };
}

export interface Incident {
  start: number;
  /** The last stored sample of the run. */
  end: number;
  worst: Status;
  /** One entry per `kind:name`, carrying the worst status it had during the run. */
  names: Bad[];
}

/** Runs of consecutive warn/crit samples, newest first. A healthy (or unknown) sample ends a
    run, and so does a silence longer than 3 sample intervals — the collector was not storing,
    so nobody knows the problem lasted. `samples` is ascending by ts, as the route sends it. */
export function incidents(samples: HistorySample[], stepMs = 60_000): Incident[] {
  const out: Incident[] = [];
  let cur: Incident | null = null;
  let last = 0;
  for (const s of samples) {
    const ms = Date.parse(s.ts);
    const bad = s.status === 'warn' || s.status === 'crit';
    if (cur && (!bad || ms - last > 3 * stepMs)) { out.push(cur); cur = null; }
    if (!bad) continue;
    if (!cur) cur = { start: ms, end: ms, worst: s.status, names: [] };
    cur.end = ms;
    last = ms;
    cur.worst = worse(cur.worst, s.status);
    for (const raw of s.bad ?? []) {
      const b = parseBad(raw);
      const seen = cur.names.find(n => n.kind === b.kind && n.name === b.name);
      if (!seen) cur.names.push(b);
      else seen.status = worse(seen.status, b.status);
    }
  }
  if (cur) out.push(cur);
  for (const i of out) i.names.sort((a, b) => RANK[b.status] - RANK[a.status]);
  return out.reverse();
}

/** Index of the newest sample with ts <= `ms`, or -1. `samples` is ascending by ts. */
export function nearestAtOrBefore(samples: HistorySample[], ms: number): number {
  let lo = 0;
  let hi = samples.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (Date.parse(samples[mid].ts) <= ms) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found;
}

/** The moment to ask the snapshot route for when cell `c` is chosen: the newest sample inside
    the cell, else the cell's last instant (the server may then answer "nothing stored").
    `limit` keeps the moment out of the future on today's strip. */
export function momentFor(samples: HistorySample[], c: Cell, limit = Infinity): number {
  const i = nearestAtOrBefore(samples, Math.min(c.end - 1, limit));
  const ms = i < 0 ? NaN : Date.parse(samples[i].ts);
  return ms >= c.start ? ms : Math.min(c.end - 1, limit);
}

export interface DaySlot { day: string; row: DayRow | null }

/** `n` slots ending at `today`, oldest first; a day the server has no rows for is `row: null`. */
export function daysStrip(rows: DayRow[], today: string, n = 31): DaySlot[] {
  const byDay = new Map(rows.map(r => [r.day, r]));
  return Array.from({ length: n }, (_, i) => {
    const day = shiftDay(today, i - (n - 1));
    return { day, row: byDay.get(day) ?? null };
  });
}

/** Parallel arrays -> chart points; a missing or non-finite value is a gap, not a zero. */
export function toPoints(ts: number[], vs: (number | null)[] | undefined): { t: number; v: number | null }[] {
  return ts.map((t, i) => {
    const v = vs?.[i];
    return { t, v: typeof v === 'number' && Number.isFinite(v) ? v : null };
  });
}
