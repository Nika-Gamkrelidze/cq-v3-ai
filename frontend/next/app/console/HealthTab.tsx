'use client';
import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';
import { LineChart, type ChartSeries } from '@/components/ui/LineChart';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { EMPTY, bytes, count } from '@/lib/format';
import { ApiError } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import { Msg, type Note } from './parts';

/* HEALTH — the first tab an operator opens on a bad day.

   Three reads, three clocks. The KPI strip (`/admin/health/overview`) is the server RIGHT NOW
   and refreshes every 10 s; the charts (`/series`) are the last hour to month and refresh every
   30 s (60 s on the long ranges, whose bins are 15 min and an hour wide — nothing changes
   faster than that); the tenant table (`/tenants`) aggregates a window and refreshes every
   minute. All three pause while the tab is hidden — a console left open overnight must not
   poll the api it is supposed to be watching — and the page.tsx mounts this component only
   while its tab is shown, so switching away stops the timers too.

   "Load share" is REQUEST WALL TIME, not CPU: analysis, transcription and every AI call run
   inside the request that asked for them, so the time the api spent answering a tenant is the
   honest measure of what that tenant cost the server. The table says so in one line. */

type Range = '1h' | '6h' | '24h' | '7d' | '30d';
const RANGES: Range[] = ['1h', '6h', '24h', '7d', '30d'];

interface DiskMount { mount: string; total_gb: number | null; used_gb: number | null; pct: number | null }

interface Latest {
  ts?: string;
  cpu_pct?: number | null; cpu_count?: number | null;
  load1?: number | null; load5?: number | null; load15?: number | null;
  mem_total_mb?: number | null; mem_used_mb?: number | null; mem_available_mb?: number | null;
  swap_used_mb?: number | null;
  disk?: DiskMount[] | null;
  disk_read_mb_s?: number | null; disk_write_mb_s?: number | null;
  net_rx_mb_s?: number | null; net_tx_mb_s?: number | null;
  api_rss_mb?: number | null; api_cpu_pct?: number | null; api_open_fds?: number | null;
  db_size_mb?: number | null; db_pool_size?: number | null; db_pool_used?: number | null;
  active_jobs?: number | null; uptime_s?: number | null;
}

interface Overview {
  now?: string;
  sampler?: { interval_s?: number; last_sample_at?: string | null; age_s?: number | null };
  latest?: Latest | null;
  db?: { size_mb?: number | null; metrics_rows?: number; load_rows?: number };
  retention_days?: number;
}

interface SeriesPoint {
  ts: string;
  cpu_pct?: number | null; load1?: number | null;
  mem_used_mb?: number | null; mem_total_mb?: number | null;
  disk_read_mb_s?: number | null; disk_write_mb_s?: number | null;
  net_rx_mb_s?: number | null; net_tx_mb_s?: number | null;
  api_rss_mb?: number | null; api_cpu_pct?: number | null;
  requests?: number | null; errors?: number | null; avg_ms?: number | null;
}

interface SeriesPayload { range: string; step_s: number; from: string; to: string; points: SeriesPoint[] }

interface TenantRow {
  client_id: string | null; slug: string | null; name: string | null; principal_kind: string;
  requests: number; errors: number; avg_ms: number; max_ms: number;
  bytes_in: number; bytes_out: number; ai_calls: number; ai_tokens: number;
  audio_jobs: number; audio_ms: number; load_share_pct: number;
}

interface TenantTable { range: string; rows: TenantRow[] }

interface HealthSettings { retention_days?: number; sample_interval_s?: number }

type SortKey = keyof Pick<TenantRow,
  'requests' | 'errors' | 'avg_ms' | 'max_ms' | 'bytes_in' | 'bytes_out'
  | 'ai_calls' | 'ai_tokens' | 'audio_jobs' | 'audio_ms' | 'load_share_pct'>;

const SORT_COLS: { key: SortKey; label: string }[] = [
  { key: 'requests', label: 'hl.th.requests' },
  { key: 'errors', label: 'hl.th.errors' },
  { key: 'avg_ms', label: 'hl.th.avgms' },
  { key: 'max_ms', label: 'hl.th.maxms' },
  { key: 'bytes_in', label: 'hl.th.in' },
  { key: 'bytes_out', label: 'hl.th.out' },
  { key: 'ai_calls', label: 'hl.th.aicalls' },
  { key: 'ai_tokens', label: 'hl.th.aitokens' },
  { key: 'audio_jobs', label: 'hl.th.audiojobs' },
  { key: 'audio_ms', label: 'hl.th.audioms' },
  { key: 'load_share_pct', label: 'hl.th.share' },
];

/* ---------------- number helpers, all local: nothing else formats a megabyte ---------------- */

function num(v: unknown): number | null {
  const n = Number(v);
  return v === null || v === undefined || !Number.isFinite(n) ? null : n;
}

function pct(v: number | null, digits = 0): string {
  return v === null ? EMPTY : `${v.toFixed(digits)}%`;
}

/** Megabytes → `812 MB` / `3.4 GB`; the sampler reports everything in MB. */
function mb(v: number | null): string {
  if (v === null) return EMPTY;
  return v >= 1024 ? `${(v / 1024).toFixed(1)} GB` : `${Math.round(v)} MB`;
}

function rate(v: number | null): string {
  return v === null ? EMPTY : `${v.toFixed(v >= 10 ? 1 : 2)} MB/s`;
}

function one(v: number | null): string {
  return v === null ? EMPTY : v.toFixed(v >= 100 ? 0 : 1);
}

function ms(v: number | null): string {
  return v === null ? EMPTY : `${Math.round(v).toLocaleString()} ms`;
}

/** Milliseconds of audio → `1h 12m` / `4m 03s`, for the tenant table's "audio processed". */
function audioMs(v: number): string {
  const s = Math.round(v / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

/** A thin proportional bar. `tone` is a theme colour; the track is the hairline. */
function Bar({ pct: p, tone }: { pct: number | null; tone: string }): JSX.Element {
  const width = p === null ? 0 : Math.max(0, Math.min(100, p));
  return (
    <div style={{ height: 6, borderRadius: 999, background: 'color-mix(in oklab, var(--hairline) 70%, transparent)', overflow: 'hidden' }}>
      <div style={{ width: `${width}%`, height: '100%', background: tone, borderRadius: 999 }} />
    </div>
  );
}

/** Green under 70, amber to 90, red above — the same three tones the pills use. */
function tone(p: number | null): string {
  if (p === null) return 'var(--muted)';
  return p >= 90 ? 'var(--alert)' : p >= 70 ? 'var(--pending)' : 'var(--ok)';
}

/* Polling that stops with the tab. `document.hidden` gates every tick; coming back into view
   fires one at once so the operator never looks at a strip that is minutes stale after an
   alt-tab. The callback is read through a ref so a language switch or a range change does
   not tear the interval down and up again. */
function usePoll(fn: () => void, intervalMs: number, deps: unknown[]) {
  const ref = useRef(fn);
  useEffect(() => { ref.current = fn; });
  useEffect(() => {
    ref.current();
    const id = setInterval(() => { if (!document.hidden) ref.current(); }, intervalMs);
    const onVis = () => { if (!document.hidden) ref.current(); };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);
}

export default function HealthTab() {
  const { t, lang } = useI18n();
  const [range, setRange] = useState<Range>('24h');
  const [overview, setOverview] = useState<Overview | null>(null);
  const [series, setSeries] = useState<SeriesPayload | null>(null);
  const [tenants, setTenants] = useState<TenantTable | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'load_share_pct', dir: 'desc' });

  const fail = useCallback((e: unknown) => {
    if (e instanceof SessionExpired) return;
    // A 404 is a console one deploy ahead of its backend, not a broken server.
    setFailed(e instanceof ApiError && e.status === 404 ? t('hl.unavailable') : errText(e, t, 'hl.loadfail'));
  }, [t]);

  const loadOverview = useCallback(() => {
    adminGet<Overview>('/admin/health/overview')
      .then(d => { setOverview(d); setFailed(null); })
      .catch(fail);
  }, [fail]);

  const loadSeries = useCallback(() => {
    adminGet<SeriesPayload>(`/admin/health/series?range=${range}`)
      .then(d => setSeries(d && Array.isArray(d.points) ? d : { range, step_s: 0, from: '', to: '', points: [] }))
      .catch(fail);
  }, [range, fail]);

  const loadTenants = useCallback(() => {
    adminGet<TenantTable>(`/admin/health/tenants?range=${range}`)
      .then(d => setTenants(d && Array.isArray(d.rows) ? d : { range, rows: [] }))
      .catch(fail);
  }, [range, fail]);

  const longRange = range === '7d' || range === '30d';
  usePoll(loadOverview, 10_000, [epoch]);
  usePoll(loadSeries, longRange ? 60_000 : 30_000, [range, epoch]);
  usePoll(loadTenants, 60_000, [range, epoch]);

  /* ---------------- the strip ---------------- */

  const latest = overview?.latest ?? null;
  const sampler = overview?.sampler;
  const age = num(sampler?.age_s);
  const interval = num(sampler?.interval_s) ?? 10;
  const sampleCls = age === null ? 'error' : age > 3 * interval ? 'pending' : 'ready';
  const sampleText = age === null ? t('hl.nosample') : t('hl.lastsample', { n: Math.round(age) });

  const cpu = num(latest?.cpu_pct);
  const cores = num(latest?.cpu_count);
  const memUsed = num(latest?.mem_used_mb);
  const memTotal = num(latest?.mem_total_mb);
  const memPct = memUsed !== null && memTotal ? (memUsed / memTotal) * 100 : null;
  const disks = Array.isArray(latest?.disk) ? latest!.disk! : [];
  const uptime = num(latest?.uptime_s);
  const uptimeText = uptime === null ? EMPTY : (() => {
    const d = Math.floor(uptime / 86400), h = Math.floor((uptime % 86400) / 3600), m = Math.floor((uptime % 3600) / 60);
    return d > 0 ? `${d}${t('hl.u.d')} ${h}${t('hl.u.h')}` : h > 0 ? `${h}${t('hl.u.h')} ${m}${t('hl.u.m')}` : `${m}${t('hl.u.m')}`;
  })();
  const poolUsed = num(latest?.db_pool_used), poolSize = num(latest?.db_pool_size);

  /* ---------------- the charts ---------------- */

  const formatX = useMemo(() => {
    const time = new Intl.DateTimeFormat(lang, { hour: '2-digit', minute: '2-digit' });
    const day = new Intl.DateTimeFormat(lang, { month: 'short', day: 'numeric' });
    return longRange
      ? (ts: number) => `${day.format(ts)} ${time.format(ts)}`
      : (ts: number) => time.format(ts);
  }, [lang, longRange]);

  const charts = useMemo(() => {
    const pts = series?.points ?? [];
    const pick = (k: keyof SeriesPoint, map: (v: number) => number = v => v) =>
      pts.map(p => {
        const v = num(p[k]);
        return { t: Date.parse(p.ts), v: v === null ? null : map(v) };
      });
    const nCores = cores && cores > 0 ? cores : 1;
    const s = (name: string, color: string, points: ChartSeries['points'], dashed?: boolean): ChartSeries =>
      ({ name, color, points, dashed });
    return {
      // load1 is plotted as a share of the cores so it shares CPU%'s axis honestly: a load of
      // 4 on 4 cores is the same "full" as 100% CPU, and a raw 4 on a 0–100 axis is invisible.
      cpu: [s(t('hl.s.cpu'), 'var(--beam)', pick('cpu_pct')), s(t('hl.s.load'), 'var(--pending)', pick('load1', v => (v / nCores) * 100))],
      mem: [s(t('hl.s.used'), 'var(--beam)', pick('mem_used_mb')), s(t('hl.s.total'), 'var(--muted)', pick('mem_total_mb'), true)],
      net: [s(t('hl.s.rx'), 'var(--ok)', pick('net_rx_mb_s')), s(t('hl.s.tx'), 'var(--beam)', pick('net_tx_mb_s'))],
      disk: [s(t('hl.s.read'), 'var(--ok)', pick('disk_read_mb_s')), s(t('hl.s.write'), 'var(--beam)', pick('disk_write_mb_s'))],
      rss: [s(t('hl.s.rss'), 'var(--beam)', pick('api_rss_mb'))],
      apicpu: [s(t('hl.s.apicpu'), 'var(--pending)', pick('api_cpu_pct'))],
      req: [s(t('hl.s.requests'), 'var(--ok)', pick('requests')), s(t('hl.s.errors'), 'var(--alert)', pick('errors'))],
      ms: [s(t('hl.s.avgms'), 'var(--beam)', pick('avg_ms'))],
    };
  }, [series, cores, t]);

  /* ---------------- the table ---------------- */

  const rows = useMemo(() => {
    const list = [...(tenants?.rows ?? [])];
    const dir = sort.dir === 'asc' ? 1 : -1;
    list.sort((a, b) => ((Number(a[sort.key]) || 0) - (Number(b[sort.key]) || 0)) * dir);
    return list;
  }, [tenants, sort]);

  const clickSort = (key: SortKey) => setSort(s => ({
    key, dir: s.key === key ? (s.dir === 'desc' ? 'asc' : 'desc') : 'desc',
  }));

  const who = (r: TenantRow): string => {
    if (r.name || r.slug) return r.name ? `${r.name}${r.slug ? ` · ${r.slug}` : ''}` : String(r.slug);
    const k = ['anonymous', 'superadmin', 'user', 'tenant'].includes(r.principal_kind) ? r.principal_kind : 'unknown';
    return t(`hl.kind.${k}`);
  };

  const chart = (title: string, s: ChartSeries[], unit: string, opts: { yMax?: number } = {}) => (
    <div className="card" style={{ marginBottom: 0 }}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>{title}</h3>
      <LineChart series={s} unit={unit} formatX={formatX} yMax={opts.yMax} emptyText={t('hl.nosamples')} />
    </div>
  );

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <h3 style={{ margin: 0 }}>
              <span>{t('hl.heading')}</span>
              <Tip text={t('hl.desc')} />
            </h3>
          </div>
          <div className="inline" style={{ flex: 0, gap: 8 }}>
            <span className={`pill ${sampleCls}`}>{sampleText}</span>
            <button className="ghost" type="button" onClick={() => setEpoch(n => n + 1)}>{t('btn.refresh')}</button>
          </div>
        </div>
        {failed ? <div className="msg err">{failed}</div> : null}
        {!latest && !failed ? <div className="empty">{overview ? t('hl.nosamples') : '…'}</div> : null}
        {latest ? (
          <div className="stat-row">
            <div className="stat">
              <b style={{ color: tone(cpu) }}>{pct(cpu)}</b>
              <span>{t('hl.k.cpu')}{cores !== null ? ` · ${cores} ${t('hl.u.cores')}` : ''}</span>
            </div>
            <div className="stat">
              <b>{one(num(latest.load1))} / {one(num(latest.load5))} / {one(num(latest.load15))}</b>
              <span>{t('hl.k.load')}</span>
            </div>
            <div className="stat">
              <b style={{ color: tone(memPct) }}>{mb(memUsed)} / {mb(memTotal)}</b>
              <span>{t('hl.k.mem')} · {pct(memPct)}</span>
              <Bar pct={memPct} tone={tone(memPct)} />
            </div>
            <div className="stat">
              <b>{mb(num(latest.swap_used_mb))}</b>
              <span>{t('hl.k.swap')}</span>
            </div>
            {disks.map(d => {
              const p = num(d.pct);
              return (
                <div className="stat" key={d.mount}>
                  <b style={{ color: tone(p) }}>{num(d.used_gb) === null ? EMPTY : `${num(d.used_gb)!.toFixed(1)} / ${(num(d.total_gb) ?? 0).toFixed(0)} GB`}</b>
                  <span>{t('hl.k.disk')} {d.mount} · {pct(p)}</span>
                  <Bar pct={p} tone={tone(p)} />
                </div>
              );
            })}
            <div className="stat">
              <b>↓ {rate(num(latest.net_rx_mb_s))}</b>
              <span>{t('hl.k.net')} · ↑ {rate(num(latest.net_tx_mb_s))}</span>
            </div>
            <div className="stat">
              <b>R {rate(num(latest.disk_read_mb_s))}</b>
              <span>{t('hl.k.diskio')} · W {rate(num(latest.disk_write_mb_s))}</span>
            </div>
            <div className="stat">
              <b>{mb(num(latest.api_rss_mb))}</b>
              <span>{t('hl.k.api')} · {pct(num(latest.api_cpu_pct))}{num(latest.api_open_fds) !== null ? ` · ${count(latest.api_open_fds)} fd` : ''}</span>
            </div>
            <div className="stat">
              <b>{mb(num(latest.db_size_mb))}</b>
              <span>{t('hl.k.db')}{poolSize !== null ? ` · ${t('hl.k.pool')} ${poolUsed ?? 0}/${poolSize}` : ''}</span>
            </div>
            <div className="stat">
              <b>{count(latest.active_jobs)}</b>
              <span>{t('hl.k.jobs')}</span>
            </div>
            <div className="stat">
              <b>{uptimeText}</b>
              <span>{t('hl.k.uptime')}</span>
            </div>
          </div>
        ) : null}
      </div>

      <div className="tabs" role="tablist" style={{ marginBottom: 14 }}>
        {RANGES.map(r => (
          <button
            key={r}
            type="button"
            role="tab"
            aria-selected={range === r}
            className={`tab${range === r ? ' active' : ''}`}
            style={{ padding: '6px 14px', fontSize: 12.5 }}
            onClick={() => setRange(r)}
          >
            {t(`hl.r.${r}`)}
          </button>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 14, marginBottom: 14 }}>
        {chart(t('hl.c.cpu'), charts.cpu, '%', { yMax: 100 })}
        {chart(t('hl.c.mem'), charts.mem, ' MB')}
        {chart(t('hl.c.net'), charts.net, ' MB/s')}
        {chart(t('hl.c.diskio'), charts.disk, ' MB/s')}
        {chart(t('hl.c.apirss'), charts.rss, ' MB')}
        {chart(t('hl.c.apicpu'), charts.apicpu, '%')}
        {chart(t('hl.c.req'), charts.req, '')}
        {chart(t('hl.c.ms'), charts.ms, ' ms')}
      </div>

      <div className="card">
        <h3>{t('hl.tenants')}</h3>
        <p className="hint">{t('hl.share.note')}</p>
        <div style={{ marginTop: 12 }}>
          {!tenants ? <div className="empty">…</div>
            : !rows.length ? <div className="empty">{t('hl.tenants.empty')}</div> : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t('hl.th.who')}</th>
                      {SORT_COLS.map(c => (
                        <th
                          key={c.key}
                          onClick={() => clickSort(c.key)}
                          style={{ cursor: 'pointer', whiteSpace: 'nowrap', userSelect: 'none' }}
                          aria-sort={sort.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                        >
                          {t(c.label)}{sort.key === c.key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => (
                      <tr key={`${r.principal_kind}:${r.client_id ?? ''}`}>
                        <td style={{ whiteSpace: 'nowrap' }}>{who(r)}</td>
                        <td>{count(r.requests)}</td>
                        <td>{r.errors > 0 ? <span className="warn-flag">{count(r.errors)}</span> : '0'}</td>
                        <td>{ms(num(r.avg_ms))}</td>
                        <td>{ms(num(r.max_ms))}</td>
                        <td>{bytes(r.bytes_in)}</td>
                        <td>{bytes(r.bytes_out)}</td>
                        <td>{count(r.ai_calls)}</td>
                        <td>{count(r.ai_tokens)}</td>
                        <td>{count(r.audio_jobs)}</td>
                        <td>{r.audio_ms > 0 ? audioMs(r.audio_ms) : EMPTY}</td>
                        <td style={{ minWidth: 120 }}>
                          <div style={{ fontVariantNumeric: 'tabular-nums' }}>{pct(num(r.load_share_pct), 1)}</div>
                          <Bar pct={num(r.load_share_pct)} tone="var(--beam)" />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </div>
      </div>

      <HealthSettingsCard />
    </>
  );
}

/* Retention and the sample interval — the two knobs, saved together. Same shape as the
   Storage tab's one field: parse, range-check in the browser with the same limits the server
   enforces, then let the server's own words explain a rejection. */
function HealthSettingsCard(): JSX.Element {
  const { t } = useI18n();
  const [days, setDays] = useState('');
  const [interval, setInterval_] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);

  useEffect(() => {
    let live = true;
    adminGet<HealthSettings>('/admin/health/settings')
      .then(d => {
        if (!live) return;
        setDays(String(d.retention_days ?? 7));
        setInterval_(String(d.sample_interval_s ?? 10));
      })
      // A card that could not load stays empty rather than claiming a value nobody set.
      .catch(() => {});
    return () => { live = false; };
  }, []);

  const save = async () => {
    setNote(null);
    const d = parseInt(days, 10);
    const s = parseInt(interval, 10);
    if (!Number.isFinite(d) || d < 1 || d > 365 || !Number.isFinite(s) || s < 5 || s > 300) {
      setNote({ kind: 'err', text: t('hl.set.invalid') });
      return;
    }
    setBusy(true);
    try {
      const r = await adminSend<HealthSettings>('PUT', '/admin/health/settings', { retention_days: d, sample_interval_s: s });
      setDays(String(r.retention_days ?? d));
      setInterval_(String(r.sample_interval_s ?? s));
      setNote({ kind: 'ok', text: t('toast.saved') });
      toast(t('toast.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: errText(e, t) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3>{t('hl.set.heading')}</h3>
      <p className="hint">{t('hl.set.desc')}</p>
      <div className="row" style={{ marginTop: 6 }}>
        <div className="w-num">
          <label htmlFor="hl_days">
            <span>{t('hl.set.retention')}</span>
            <Tip text={t('hl.set.retention.hint')} />
          </label>
          <input id="hl_days" type="number" min={1} max={365} value={days} onChange={e => setDays(e.target.value)} />
        </div>
        <div className="w-num">
          <label htmlFor="hl_interval">
            <span>{t('hl.set.interval')}</span>
            <Tip text={t('hl.set.interval.hint')} />
          </label>
          <input id="hl_interval" type="number" min={5} max={300} value={interval} onChange={e => setInterval_(e.target.value)} />
        </div>
      </div>
      <div className="actions">
        <button className="primary" type="button" onClick={save} disabled={busy}>{t('btn.save')}</button>
      </div>
      <Msg note={note} />
    </div>
  );
}
