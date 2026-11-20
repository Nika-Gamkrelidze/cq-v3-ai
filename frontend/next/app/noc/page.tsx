'use client';
import { useEffect, useMemo, useState } from 'react';
import Header from '@/components/Header';
import type { ChartSeries } from '@/components/ui/LineChart';
import { readSession } from '@/lib/session';
import { usePoll } from '@/lib/usePoll';
import { useI18n } from '@/lib/useI18n';
import { adminGet } from '../console/api';
import Board, { ChartCard } from './Board';
import History from './History';
import { liveIndicator } from './live';
import { num } from './format';
import type { SeriesPoint } from './types';
import { useNocStream } from './useNocStream';

/* NOC — one screen that says whether the platform is healthy right now, and which part is not.

   Live mode listens to the server's snapshot stream (useNocStream): the server computes one
   snapshot per cycle for every viewer and this page only repaints it, so nothing here refreshes
   by hand. The last-hour charts are a separate read every 30 s; both pause while the tab is
   hidden. History mode shows what was stored (the History view). The server decides every
   colour (backend/app/services/noc.py, fixed thresholds); this page names things and gets out
   of the way. Superadmin only, like the API behind it. */

type Mode = 'live' | 'history';
const MODE_KEY = 'cq_noc_mode';

interface SeriesPayload { points: SeriesPoint[] }

export default function NocPage() {
  const { t, lang } = useI18n();
  // The session is only readable in the browser (this page is prerendered at build time).
  const [role, setRole] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>('live');
  const [series, setSeries] = useState<SeriesPoint[]>([]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const r = readSession().role;
    // Every workspace's platform health: a signed-in tenant or user is sent home, not shown the shell.
    if (r === 'tenant' || r === 'user') {
      window.location.replace(r === 'tenant' ? '/workspace' : '/account');
      return;
    }
    try { if (localStorage.getItem(MODE_KEY) === 'history') setMode('history'); } catch { /* blocked storage */ }
    setRole(r);
  }, []);
  const operator = role === 'superadmin';

  const pick = (m: Mode) => {
    setMode(m);
    try { localStorage.setItem(MODE_KEY, m); } catch { /* blocked storage */ }
  };

  // One-second clock so "updated N s ago" and every relative time tick; idle while hidden.
  useEffect(() => {
    const id = setInterval(() => { if (!document.hidden) setNow(Date.now()); }, 1000);
    const onVis = () => { if (!document.hidden) setNow(Date.now()); };
    document.addEventListener('visibilitychange', onVis);
    return () => { clearInterval(id); document.removeEventListener('visibilitychange', onVis); };
  }, []);

  const live = mode === 'live';
  const { data, state, failed, lastAt, reload } = useNocStream(operator && live);
  const [epoch, setEpoch] = useState(0);

  usePoll(() => {
    if (!operator || !live) return;
    adminGet<SeriesPayload>('/admin/health/series?range=1h')
      .then(d => setSeries(d && Array.isArray(d.points) ? d.points : []))
      .catch(() => { /* the status read already reports a broken api; the charts just stay as they were */ });
  }, 30_000, [operator, live, epoch]);

  const charts = useMemo(() => {
    const pickv = (f: (p: SeriesPoint) => number | null | undefined) =>
      series.map(p => ({ t: Date.parse(p.ts), v: num(f(p)) }));
    const ratio = (a?: number | null, b?: number | null) => (num(a) !== null && num(b) ? (num(a)! / num(b)!) * 100 : null);
    const s = (name: string, color: string, points: ChartSeries['points'], dashed?: boolean): ChartSeries => ({ name, color, points, dashed });
    return {
      cpu: [s(t('hl.s.cpu'), 'var(--beam)', pickv(p => p.cpu_pct))],
      mem: [s(t('hl.s.used'), 'var(--beam)', pickv(p => p.mem_used_mb)), s(t('hl.s.total'), 'var(--muted)', pickv(p => p.mem_total_mb), true)],
      net: [s(t('hl.s.rx'), 'var(--ok)', pickv(p => p.net_rx_mb_s)), s(t('hl.s.tx'), 'var(--beam)', pickv(p => p.net_tx_mb_s))],
      loss: [s(t('noc.s.drops'), 'var(--alert)', pickv(p => ratio(p.net_drops_s, p.net_pkts_s))),
             s(t('noc.s.errs'), 'var(--pending)', pickv(p => ratio(p.net_errs_s, p.net_pkts_s)))],
      req: [s(t('hl.s.requests'), 'var(--ok)', pickv(p => p.requests)), s(t('hl.s.errors'), 'var(--alert)', pickv(p => p.errors))],
      ms: [s(t('hl.s.avgms'), 'var(--beam)', pickv(p => p.avg_ms))],
    };
  }, [series, t]);

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

  const age = lastAt === null ? null : Math.max(0, Math.round((now - lastAt) / 1000));
  const ind = liveIndicator({ state, ageSeconds: age });
  const cc = (title: string, s: ChartSeries[], unit: string, yMax?: number) =>
    <ChartCard title={title} series={s} unit={unit} yMax={yMax} lang={lang} t={t} />;

  const bannerRight = (
    <>
      <span className="inline" style={{ gap: 6 }} role="status">
        <span aria-hidden style={{ display: 'inline-block', width: 9, height: 9, borderRadius: '50%', background: ind.color, flex: 'none' }} />
        <span className="hint">{t(`noc.live.${ind.key}`)}</span>
      </span>
      {age !== null ? <span className="hint">{t('noc.updated', { n: age })}</span> : null}
      <button className="ghost" type="button" onClick={() => { reload(); setEpoch(n => n + 1); }}>{t('btn.refresh')}</button>
    </>
  );

  return (
    <>
      <Header tag="Console" />
      <main className="console">
        <div style={{ marginBottom: 18 }}>
          <h1 style={{ margin: 0, fontSize: 'clamp(24px,4vw,32px)' }}>{t('noc.title')}</h1>
          <p className="lead">{t('noc.lead')}</p>
        </div>

        <div className="tabs" role="tablist" aria-label={t('noc.mode.label')}>
          {(['live', 'history'] as const).map(m => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              className={`tab${mode === m ? ' active' : ''}`}
              onClick={() => pick(m)}
            >
              {t(`noc.mode.${m}`)}
            </button>
          ))}
        </div>

        {live ? (
          <Board
            data={data}
            t={t}
            lang={lang}
            now={now}
            failed={failed}
            bannerRight={bannerRight}
            charts={(
              <>
                <h3 style={{ margin: '4px 0 10px' }}>{t('noc.g.charts')}</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 14, marginBottom: 14 }}>
                  {cc(t('hl.c.cpu'), charts.cpu, '%', 100)}
                  {cc(t('hl.c.mem'), charts.mem, ' MB')}
                  {cc(t('hl.c.net'), charts.net, ' MB/s')}
                  {cc(t('noc.c.loss'), charts.loss, '%')}
                  {cc(t('hl.c.req'), charts.req, '')}
                  {cc(t('hl.c.ms'), charts.ms, ' ms')}
                </div>
              </>
            )}
          />
        ) : <History t={t} lang={lang} now={now} onLive={() => pick('live')} />}
      </main>
    </>
  );
}
