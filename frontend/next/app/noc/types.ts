/* The shapes `GET /admin/noc/overview` answers with (backend/app/services/noc.py), and the
   history routes beside it (`/admin/noc/history*`, `/snapshot`). */

export type Status = 'ok' | 'warn' | 'crit' | 'unknown';

export interface Check {
  key: string;
  group: string;
  status: Status;
  value: number | string | null;
  unit: string;
  detail: Record<string, unknown> | null;
  ref: string | null;
}

export interface ProbeStats {
  n: number;
  loss_pct: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  consecutive_fail: number;
  last_ok_at: number | null;
  last_error: string | null;
}

export interface Probe extends ProbeStats {
  key: string;
  host: string;
  kind: 'baseline' | 'provider';
  provider?: string | null;
  status: Status;
  /** [epoch seconds, connect ms | null when the attempt failed] */
  history: [number, number | null][];
}

export interface AiRow {
  id: string | null;
  name: string | null;
  provider: string;
  capability: string | null;
  model: string | null;
  host: string | null;
  is_default: boolean;
  calls_15m: number;
  fails_15m: number;
  p50_ms: number | null;
  p95_ms: number | null;
  calls_24h: number;
  fails_24h: number;
  last_ok_at: string | null;
  last_fail_at: string | null;
  probe: ProbeStats | null;
  last_test: { ok?: boolean; at?: string; detail?: string } | null;
  status: Status;
}

export interface Worker {
  duty: string;
  interval_s: number | null;
  age_s: number;
  stale_after_s: number;
  took_ms: number | null;
  failing: boolean;
  last_error: string | null;
  status: Status;
}

export interface Noc {
  now: string;
  status: Status;
  counts: Record<Status, number>;
  checks: Check[];
  ai: AiRow[];
  probes: Probe[];
  workers: Worker[];
  sampler_interval_s?: number;
  /** The collector's cycle, seconds (a stored snapshot carries it too). */
  interval_s?: number;
}

/** The slice of `/admin/health/series` the charts read. */
export interface SeriesPoint {
  ts: string;
  cpu_pct?: number | null; load1?: number | null;
  mem_used_mb?: number | null; mem_total_mb?: number | null;
  net_rx_mb_s?: number | null; net_tx_mb_s?: number | null;
  net_pkts_s?: number | null; net_errs_s?: number | null; net_drops_s?: number | null;
  requests?: number | null; errors?: number | null; avg_ms?: number | null;
}

/* ---------------- history (GET /admin/noc/history/days, /history, /snapshot) ---------------- */

export interface DayRow {
  /** The LOCAL calendar day in the requested tz, `YYYY-MM-DD`. */
  day: string;
  /** Worst stored status of the day. */
  status: Status;
  samples: number;
  crit: number;
  warn: number;
}

export interface DaysResponse {
  tz: string;
  retention_days: number;
  days: DayRow[];
}

export interface HistorySample {
  ts: string;
  status: Status;
  crit: number;
  warn: number;
  /** Every item that was warn or crit, `<status>:<kind>:<name>`, kind = check|ai|probe|worker. */
  bad: string[];
}

export interface HistorySeries {
  /** epoch ms — every other array has the same length and order; null = no value then. */
  ts: number[];
  cpu: (number | null)[];
  mem: (number | null)[];
  swap_mb: (number | null)[];
  disk: (number | null)[];
  net_mbs: (number | null)[];
  net_loss: (number | null)[];
  net_errors: (number | null)[];
  db_ms: (number | null)[];
  http_rate: (number | null)[];
  http_5xx: (number | null)[];
  jobs_stuck: (number | null)[];
  probes: Record<string, { p50: (number | null)[]; loss: (number | null)[] }>;
  ai: Record<string, { name: string; calls: (number | null)[]; fails: (number | null)[]; p50: (number | null)[] }>;
}

export interface HistoryResponse {
  from: string;
  to: string;
  step_s: number;
  samples: HistorySample[];
  series: HistorySeries;
}

export interface SnapshotResponse {
  ts: string;
  /** A stored snapshot: the live shape with every probe's `history` emptied. */
  snapshot: Noc;
}
