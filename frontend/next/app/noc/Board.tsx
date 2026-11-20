import { useMemo, type JSX, type ReactNode } from 'react';
import { LineChart, type ChartSeries } from '@/components/ui/LineChart';
import { Tip } from '@/components/ui/Tip';
import { EMPTY, count } from '@/lib/format';
import { COLOR, PILL, RANK, TIPS, ago, detailLine, formatValue, span } from './format';
import type { AiRow, Check, Noc, Probe, Status, Worker } from './types';

/* The NOC's status sections, rendered from ONE snapshot. No fetching in here: the live page
   feeds it the stream, the history view feeds it a stored snapshot. Anything relative ("3 m",
   "last success") is measured against the `now` prop so a ticking clock moves it. */

const PROBE_COLORS = ['var(--beam)', 'var(--ok)', 'var(--pending)', 'var(--coral)', 'var(--alert)', 'var(--muted)'];

type T = (k: string, v?: Record<string, string | number>) => string;

export interface BoardProps {
  data: Noc | null;
  t: T;
  lang: string;
  /** Wall clock in ms, owned by the caller. */
  now: number;
  /** Placed at the right of the banner row (live indicator, refresh button, …). */
  bannerRight?: ReactNode;
  /** Shown under the banner message when set. */
  failed?: string | null;
  /** Extra sections after the monitoring card (the live page's last-hour charts). */
  charts?: ReactNode;
  /** Leave out the connect-time chart — a stored snapshot carries no probe history. */
  noRtt?: boolean;
}

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

/** A titled line chart in the NOC's card style; the x axis is clock time in the page language. */
export function ChartCard({ title, series, unit, yMax, lang, t }: {
  title: string; series: ChartSeries[]; unit: string; yMax?: number; lang: string; t: T;
}): JSX.Element {
  const formatX = useMemo(() => {
    const time = new Intl.DateTimeFormat(lang, { hour: '2-digit', minute: '2-digit' });
    return (ts: number) => time.format(ts);
  }, [lang]);
  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>{title}</h3>
      <LineChart series={series} unit={unit} formatX={formatX} yMax={yMax} emptyText={t('hl.nosamples')} />
    </div>
  );
}

export default function Board({ data, t, lang, now, bannerRight, failed, charts, noRtt }: BoardProps): JSX.Element {
  const label = (s: Status) => t(`noc.st.${s}`);
  const checks = (group: string) => (data?.checks ?? []).filter(c => c.group === group);

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

  const rttSeries = useMemo<ChartSeries[]>(() => (data?.probes ?? []).map((p, i) => ({
    name: probeName(p, t), color: PROBE_COLORS[i % PROBE_COLORS.length],
    points: p.history.map(([at, ms]) => ({ t: at * 1000, v: ms })),
  })), [data, t]);

  const tiles = (group: string) => {
    const list = checks(group);
    if (!list.length) return <div className="empty">{t('noc.nodata')}</div>;
    return (
      <div className="stat-row">
        {list.map(c => <Tile key={`${c.key}:${c.ref}`} c={c} t={t} label={label(c.status)} />)}
      </div>
    );
  };

  const status: Status = data?.status ?? 'unknown';

  return (
    <>
      <div className="card" style={{ borderLeft: `4px solid ${COLOR[status]}` }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="inline" style={{ gap: 12, flex: 1 }}>
            <span className={`pill ${PILL[status]}`} style={{ fontSize: 15, padding: '5px 14px' }}>{t(`noc.s.${status}`)}</span>
            {data ? <span className="hint">{t('noc.counts', { crit: data.counts.crit, warn: data.counts.warn })}</span> : null}
          </div>
          {bannerRight ? <div className="inline" style={{ gap: 10, flex: 0 }}>{bannerRight}</div> : null}
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
        <AiTable rows={data?.ai ?? []} t={t} loaded={!!data} now={now} />
      </Card>

      <Card title={t('noc.g.network')} hint={t('noc.p.hint')}>
        {tiles('network')}
        <div style={{ marginTop: 14 }}>
          <ProbeTable rows={data?.probes ?? []} t={t} />
        </div>
        {noRtt ? null : (
          <div style={{ marginTop: 14 }}>
            <ChartCard title={t('noc.c.rtt')} series={rttSeries} unit=" ms" lang={lang} t={t} />
          </div>
        )}
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

      {charts}
      <p className="hint">{t('noc.foot')}</p>
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

function AiTable({ rows, t, loaded, now }: { rows: AiRow[]; t: T; loaded: boolean; now: number }): JSX.Element {
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
              <td>{a.last_ok_at ? ago(a.last_ok_at, t, now) : <span className="hint">{t('noc.ai.never')}</span>}</td>
              <td>{a.last_fail_at ? `${ago(a.last_fail_at, t, now)} · ${a.fails_24h}` : EMPTY}</td>
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
