'use client';
import { useCallback, useEffect, useMemo, useState, type JSX, type ReactNode } from 'react';
import Header from '@/components/Header';
import { LineChart, type ChartSeries } from '@/components/ui/LineChart';
import { Tip } from '@/components/ui/Tip';
import { EMPTY, count } from '@/lib/format';
import { ApiError, readSession } from '@/lib/session';
import { usePoll } from '@/lib/usePoll';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, errText } from '../console/api';
import { COLOR, PILL, RANK, TIPS, ago, detailLine, formatValue, num, span } from './format';
import type { AiRow, Check, Noc, Probe, SeriesPoint, Status, Worker } from './types';

/* NOC — one screen that says whether the platform is healthy right now, and which part is not.

   The server decides every colour (backend/app/services/noc.py, fixed thresholds); this page
   names things, formats them and gets out of the way. Two clocks: the status read refreshes
   every 10 s, the last-hour charts every 30 s — both pause while the tab is hidden, so a wall
   display left on overnight is the only thing polling the api it is watching.

   Order is by how badly it hurts: what needs attention first, then the AI providers (every
   analysis and bot reply is a call to one), the network path to them, the services the product
   cannot run without, the host, what users feel. Superadmin only, like the API behind it. */

const PROBE_COLORS = ['var(--beam)', 'var(--ok)', 'var(--pending)', 'var(--coral)', 'var(--alert)', 'var(--muted)'];

interface SeriesPayload { points: SeriesPoint[] }

function Dot({ status, label }: { status: Status; label: string }): JSX.Element {
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: COLOR[status], flex: 'none' }}
    />
  );
}

function Card({ title, hint, children }: { title: string; hint?: string; children: ReactNode }): JSX.Element {
  return (
    <div className="card">
      <h3 style={{ margin: '0 0 12px' }}>
        <span>{title}</span>
        {hint ? <Tip text={hint} /> : null}
      </h3>
      {children}
    </div>
  );
}

export default function NocPage() {
  const { t, lang } = useI18n();
  // The session is only readable in the browser (this page is prerendered at build time).
  const [role, setRole] = useState<string | null>(null);
  const [data, setData] = useState<Noc | null>(null);
  const [series, setSeries] = useState<SeriesPoint[]>([]);
  const [failed, setFailed] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const r = readSession().role;
    // Every workspace's platform health: a signed-in tenant or user is sent home, not shown the shell.
    if (r === 'tenant' || r === 'user') {
      window.location.replace(r === 'tenant' ? '/workspace' : '/account');
      return;
    }
    setRole(r);
  }, []);
  const operator = role === 'superadmin';

  const fail = useCallback((e: unknown) => {
    if (e instanceof SessionExpired) return;
    setFailed(e instanceof ApiError && e.status === 404 ? t('noc.unavailable') : errText(e, t, 'noc.loadfail'));
  }, [t]);

  usePoll(() => {
    if (!operator) return;
    adminGet<Noc>('/admin/noc/overview').then(d => { setData(d); setFailed(null); setNow(Date.now()); }).catch(fail);
  }, 10_000, [operator, epoch]);

  usePoll(() => {
    if (!operator) return;
    adminGet<SeriesPayload>('/admin/health/series?range=1h')
      .then(d => setSeries(d && Array.isArray(d.points) ? d.points : []))
      .catch(() => { /* the status read already reports a broken api; the charts just stay as they were */ });
  }, 30_000, [operator, epoch]);

  const label = (s: Status) => t(`noc.st.${s}`);
  const checks = (group: string) => (data?.checks ?? []).filter(c => c.group === group);

  /* ---------------- what needs attention ---------------- */

  const problems = useMemo(() => {
    if (!data) return [];
    const out: { status: Status; name: string; value: string; key: string }[] = [];
    for (const c of data.checks) {
      if (c.status !== 'warn' && c.status !== 'crit') continue;
      out.push({ status: c.status, key: `c:${c.key}:${c.ref}`, value: formatValue(c, t),
                 name: t(`noc.chk.${c.key}`) + (c.ref ? ` · ${c.ref}` : '') });
    }
    for (const a of data.ai) {
      if (a.status === 'warn' || a.status === 'crit') {
        out.push({ status: a.status, key: `a:${a.id ?? a.provider}`, name: `${t('noc.g.ai')} · ${aiName(a, t)}`,
                   value: a.calls_15m ? `${a.fails_15m}/${a.calls_15m} ${t('noc.ai.th.failed').toLowerCase()}` : '' });
      }
    }
    for (const p of data.probes) {
      if (p.status === 'warn' || p.status === 'crit') {
        out.push({ status: p.status, key: `p:${p.key}`, name: `${t('noc.g.network')} · ${probeName(p, t)}`,
                   value: p.loss_pct === null ? '' : `${p.loss_pct}%` });
      }
    }
    for (const w of data.workers) {
      if (w.status === 'warn' || w.status === 'crit') {
        out.push({ status: w.status, key: `w:${w.duty}`, name: `${t('noc.g.workers')} · ${w.duty}`,
                   value: w.status === 'crit' ? t('noc.w.stale', { n: span(w.age_s, t) }) : t('noc.w.failing', { error: w.last_error ?? '' }) });
      }
    }
    return out.sort((x, y) => RANK[y.status] - RANK[x.status]);
  }, [data, t]);

  /* ---------------- charts ---------------- */

  const formatX = useMemo(() => {
    const time = new Intl.DateTimeFormat(lang, { hour: '2-digit', minute: '2-digit' });
    return (ts: number) => time.format(ts);
  }, [lang]);

  const charts = useMemo(() => {
    const pick = (f: (p: SeriesPoint) => number | null | undefined) =>
      series.map(p => ({ t: Date.parse(p.ts), v: num(f(p)) }));
    const ratio = (a?: number | null, b?: number | null) => (num(a) !== null && num(b) ? (num(a)! / num(b)!) * 100 : null);
    const s = (name: string, color: string, points: ChartSeries['points'], dashed?: boolean): ChartSeries => ({ name, color, points, dashed });
    return {
      cpu: [s(t('hl.s.cpu'), 'var(--beam)', pick(p => p.cpu_pct))],
      mem: [s(t('hl.s.used'), 'var(--beam)', pick(p => p.mem_used_mb)), s(t('hl.s.total'), 'var(--muted)', pick(p => p.mem_total_mb), true)],
      net: [s(t('hl.s.rx'), 'var(--ok)', pick(p => p.net_rx_mb_s)), s(t('hl.s.tx'), 'var(--beam)', pick(p => p.net_tx_mb_s))],
      loss: [s(t('noc.s.drops'), 'var(--alert)', pick(p => ratio(p.net_drops_s, p.net_pkts_s))),
             s(t('noc.s.errs'), 'var(--pending)', pick(p => ratio(p.net_errs_s, p.net_pkts_s)))],
      req: [s(t('hl.s.requests'), 'var(--ok)', pick(p => p.requests)), s(t('hl.s.errors'), 'var(--alert)', pick(p => p.errors))],
      ms: [s(t('hl.s.avgms'), 'var(--beam)', pick(p => p.avg_ms))],
    };
  }, [series, t]);

  const rttSeries = useMemo<ChartSeries[]>(() => (data?.probes ?? []).map((p, i) => ({
    name: probeName(p, t), color: PROBE_COLORS[i % PROBE_COLORS.length],
    points: p.history.map(([at, ms]) => ({ t: at * 1000, v: ms })),
  })), [data, t]);

  const chart = (title: string, s: ChartSeries[], unit: string, yMax?: number) => (
    <div className="card" style={{ marginBottom: 0 }}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>{title}</h3>
      <LineChart series={s} unit={unit} formatX={formatX} yMax={yMax} emptyText={t('hl.nosamples')} />
    </div>
  );

  /* ---------------- pieces ---------------- */

  const tiles = (group: string) => {
    const list = checks(group);
    if (!list.length) return <div className="empty">{t('noc.nodata')}</div>;
    return (
      <div className="stat-row">
        {list.map(c => <Tile key={`${c.key}:${c.ref}`} c={c} t={t} label={label(c.status)} />)}
      </div>
    );
  };

  /* ---------------- render ---------------- */

  if (role === null) return <><Header tag="Console" /><main /></>;
  if (!operator) {
    return (
      <>
        <Header tag="Console" />
        <main className="narrow">
          <div className="card">
            <p className="lead">{t('noc.adminonly')}</p>
            <div className="actions"><a className="ghost" href="/tenant.html">{t('nav.signin')}</a></div>
          </div>
        </main>
      </>
    );
  }

  const status: Status = data?.status ?? 'unknown';
  const age = data ? Math.max(0, Math.round((now - Date.parse(data.now)) / 1000)) : null;

  return (
    <>
      <Header tag="Console" />
      <main className="console">
        <div style={{ marginBottom: 18 }}>
          <h1 style={{ margin: 0, fontSize: 'clamp(24px,4vw,32px)' }}>{t('noc.title')}</h1>
          <p className="lead">{t('noc.lead')}</p>
        </div>

        <div className="card" style={{ borderLeft: `4px solid ${COLOR[status]}` }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="inline" style={{ gap: 12, flex: 1 }}>
              <span className={`pill ${PILL[status]}`} style={{ fontSize: 15, padding: '5px 14px' }}>{t(`noc.s.${status}`)}</span>
              {data ? <span className="hint">{t('noc.counts', { crit: data.counts.crit, warn: data.counts.warn })}</span> : null}
            </div>
            <div className="inline" style={{ gap: 10, flex: 0 }}>
              {age !== null ? <span className="hint">{t('noc.updated', { n: age })}</span> : null}
              <button className="ghost" type="button" onClick={() => setEpoch(n => n + 1)}>{t('btn.refresh')}</button>
            </div>
          </div>
          {failed ? <div className="msg err" style={{ marginTop: 10 }}>{failed}</div> : null}
          {data ? (
            <div style={{ marginTop: 14 }}>
              <h4 style={{ margin: '0 0 8px', fontSize: 13 }}>{t('noc.problems')}</h4>
              {!problems.length ? <div className="hint">{t('noc.problems.none')}</div> : (
                <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 6 }}>
                  {problems.map(p => (
                    <li key={p.key} className="inline" style={{ gap: 8, alignItems: 'center' }}>
                      <Dot status={p.status} label={label(p.status)} />
                      <b style={{ fontWeight: 600 }}>{p.name}</b>
                      <span style={{ color: COLOR[p.status] }}>{p.value}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </div>

        <Card title={t('noc.g.ai')} hint={t('noc.ai.hint')}>
          <AiTable rows={data?.ai ?? []} t={t} loaded={!!data} />
        </Card>

        <Card title={t('noc.g.network')} hint={t('noc.p.hint')}>
          {tiles('network')}
          <div style={{ marginTop: 14 }}>
            <ProbeTable rows={data?.probes ?? []} t={t} />
          </div>
          <div style={{ marginTop: 14 }}>{chart(t('noc.c.rtt'), rttSeries, ' ms')}</div>
        </Card>

        <Card title={t('noc.g.services')}>
          {tiles('services')}
          <h4 style={{ margin: '16px 0 8px', fontSize: 13 }}>
            {t('noc.g.workers')}<Tip text={t('noc.w.hint')} />
          </h4>
          <WorkerTable rows={data?.workers ?? []} t={t} />
        </Card>

        <Card title={t('noc.g.host')}>{tiles('host')}</Card>
        <Card title={t('noc.g.workload')}>{tiles('workload')}</Card>
        {checks('edge').length ? <Card title={t('noc.g.edge')}>{tiles('edge')}</Card> : null}
        {checks('monitoring').length ? <Card title={t('noc.g.monitoring')}>{tiles('monitoring')}</Card> : null}

        <h3 style={{ margin: '4px 0 10px' }}>{t('noc.g.charts')}</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 14, marginBottom: 14 }}>
          {chart(t('hl.c.cpu'), charts.cpu, '%', 100)}
          {chart(t('hl.c.mem'), charts.mem, ' MB')}
          {chart(t('hl.c.net'), charts.net, ' MB/s')}
          {chart(t('noc.c.loss'), charts.loss, '%')}
          {chart(t('hl.c.req'), charts.req, '')}
          {chart(t('hl.c.ms'), charts.ms, ' ms')}
        </div>
        <p className="hint">{t('noc.foot')}</p>
      </main>
    </>
  );
}

/* ---------------- parts ---------------- */

function aiName(a: AiRow, t: (k: string) => string): string {
  return a.name ?? `${a.provider} · ${t('noc.ai.legacy')}`;
}

function probeName(p: Probe, t: (k: string) => string): string {
  return p.kind === 'baseline' ? `${t('noc.p.internet')} (${p.host})` : p.host;
}

function Tile({ c, t, label }: { c: Check; t: (k: string, v?: Record<string, string | number>) => string; label: string }): JSX.Element {
  const line = detailLine(c, t);
  return (
    <div className="stat" title={label}>
      <b style={{ color: COLOR[c.status] }}>{formatValue(c, t)}</b>
      <span>
        {t(`noc.chk.${c.key}`)}{c.ref ? ` · ${c.ref}` : ''}
        {TIPS.has(c.key) ? <Tip text={t(`noc.tip.${c.key}`)} /> : null}
      </span>
      {line ? <span className="hint" style={{ display: 'block' }}>{line}</span> : null}
    </div>
  );
}

type T = (k: string, v?: Record<string, string | number>) => string;

function AiTable({ rows, t, loaded }: { rows: AiRow[]; t: T; loaded: boolean }): JSX.Element {
  if (!loaded) return <div className="empty">…</div>;
  if (!rows.length) return <div className="empty">{t('noc.ai.empty')}</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th />
            <th>{t('noc.ai.th.conn')}</th><th>{t('noc.ai.th.path')}</th><th>{t('noc.ai.th.calls')}</th>
            <th>{t('noc.ai.th.failed')}</th><th>{t('noc.ai.th.latency')}</th>
            <th>{t('noc.ai.th.last')}</th><th>{t('noc.ai.th.lastfail')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(a => (
            <tr key={a.id ?? `legacy:${a.provider}:${a.capability}`}>
              <td><Dot status={a.status} label={t(`noc.st.${a.status}`)} /></td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <b>{aiName(a, t)}</b>{a.is_default ? <span className="pill on" style={{ marginLeft: 6 }}>{t('noc.ai.default')}</span> : null}
                <div className="hint">{[a.capability, a.provider, a.model].filter(Boolean).join(' · ')}</div>
                {a.last_test && a.last_test.ok === false ? <div className="hint" style={{ color: COLOR.warn }}>{t('noc.ai.testfail')}</div> : null}
              </td>
              <td style={{ whiteSpace: 'nowrap' }}>
                {a.probe && a.probe.n >= 3 ? `${a.probe.p50_ms ?? EMPTY} ms · ${a.probe.loss_pct}%` : EMPTY}
              </td>
              <td>{a.calls_15m ? count(a.calls_15m) : <span className="hint">{t('noc.ai.idle')}</span>}</td>
              <td>{a.calls_15m ? <span style={{ color: a.fails_15m ? COLOR[a.status === 'ok' ? 'warn' : a.status] : undefined }}>{count(a.fails_15m)}</span> : EMPTY}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{a.p50_ms === null ? EMPTY : `${count(a.p50_ms)} / ${count(a.p95_ms)} ms`}</td>
              <td>{a.last_ok_at ? ago(a.last_ok_at, t) : <span className="hint">{t('noc.ai.never')}</span>}</td>
              <td>{a.last_fail_at ? `${ago(a.last_fail_at, t)} · ${a.fails_24h}` : EMPTY}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProbeTable({ rows, t }: { rows: Probe[]; t: T }): JSX.Element {
  if (!rows.length) return <div className="empty">{t('noc.p.none')}</div>;
  const err = (e: string | null) => (e ? (t(`noc.p.err.${e}`) === `noc.p.err.${e}` ? e : t(`noc.p.err.${e}`)) : EMPTY);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th /><th>{t('noc.p.th.target')}</th><th>{t('noc.p.th.rtt')}</th>
            <th>{t('noc.p.th.loss')}</th><th>{t('noc.p.th.last')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(p => (
            <tr key={p.key}>
              <td><Dot status={p.status} label={t(`noc.st.${p.status}`)} /></td>
              <td style={{ whiteSpace: 'nowrap' }}>{probeName(p, t)}</td>
              <td>{p.p50_ms === null ? EMPTY : `${p.p50_ms} / ${p.p95_ms} ms`}</td>
              <td>{p.loss_pct === null ? EMPTY : `${p.loss_pct}% (${p.n})`}</td>
              <td>{p.consecutive_fail ? `${err(p.last_error)} ×${p.consecutive_fail}` : EMPTY}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WorkerTable({ rows, t }: { rows: Worker[]; t: T }): JSX.Element {
  if (!rows.length) return <div className="msg err">{t('noc.w.none')}</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th /><th>{t('noc.w.th.duty')}</th><th>{t('noc.w.th.age')}</th><th>{t('noc.w.th.took')}</th></tr></thead>
        <tbody>
          {rows.map(w => (
            <tr key={w.duty}>
              <td><Dot status={w.status} label={t(`noc.st.${w.status}`)} /></td>
              <td>
                {w.duty}
                {w.failing ? <div className="hint" style={{ color: COLOR.warn }}>{t('noc.w.failing', { error: w.last_error ?? '' })}</div> : null}
              </td>
              <td>{span(w.age_s, t)}</td>
              <td>{w.took_ms === null ? EMPTY : `${count(w.took_ms)} ms`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
