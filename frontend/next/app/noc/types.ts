/* The shapes `GET /admin/noc/overview` answers with (backend/app/services/noc.py). */

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
