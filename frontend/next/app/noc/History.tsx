'use client';
import { useEffect, useMemo, useRef, useState, type JSX } from 'react';
import type { ChartSeries } from '@/components/ui/LineChart';
import { ApiError } from '@/lib/session';
import { usePoll } from '@/lib/usePoll';
import { SessionExpired, adminGet, errText } from '../console/api';
import Board, { ChartCard } from './Board';
import { COLOR, PILL, span } from './format';
import {
  CELL_MS, bucketSamples, dayBounds, daysStrip, incidents, localDay, momentFor, parseBad, shiftDay, toPoints,
  type Bad, type Cell,
} from './historyLib';
import type { DaysResponse, HistoryResponse, SnapshotResponse, Status } from './types';

/* NOC history: what the collector stored (backend/app/services/noc_history.py), looked at by
   local calendar day. A month strip picks the day, a 5-minute timeline shows its worst status
   hour by hour, incidents list the runs of warnings and outages, and a chosen moment brings the
   whole board back as it was. The day boundaries are the browser's own zone — the same `tz` the
   days route is asked for — so "Tuesday" here and on the server are one Tuesday. */

type T = (k: string, v?: Record<string, string | number>) => string;

const SERIES_COLORS = ['var(--beam)', 'var(--ok)', 'var(--pending)', 'var(--coral)', 'var(--alert)', 'var(--muted)'];
const NO_DATA = 'repeating-linear-gradient(45deg, color-mix(in oklab, var(--muted) 45%, transparent) 0 2px, transparent 2px 5px)';
const MAX_NAMES = 4;

const zone = (): string => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
};

/** "Disk · /" for a check, "AI providers · Anthropic main" for the rest — the same words the live board uses. */
function badLabel(b: Bad, t: T): string {
  if (b.kind === 'check') {
    const at = b.name.indexOf(':');
    const key = at < 0 ? b.name : b.name.slice(0, at);
    const ref = at < 0 ? '' : b.name.slice(at + 1);
    const word = t(`noc.chk.${key}`);
    return (word === `noc.chk.${key}` ? key : word) + (ref ? ` · ${ref}` : '');
  }
  const group = b.kind === 'ai' ? 'noc.g.ai' : b.kind === 'probe' ? 'noc.g.network' : b.kind === 'worker' ? 'noc.g.workers' : '';
  return group ? `${t(group)} · ${b.name}` : b.name;
}

export default function History({ t, lang, now, onLive }: {
  t: T;
  lang: string;
  now: number;
  onLive?: () => void;
}): JSX.Element {
  const tz = useMemo(zone, []);
  const today = localDay(new Date(now));
  const [day, setDay] = useState(today);
  const [days, setDays] = useState<DaysResponse | null>(null);
  const [hist, setHist] = useState<HistoryResponse | null>(null);
  const [loaded, setLoaded] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<{ cell: number; at: number } | null>(null);
  const [snap, setSnap] = useState<SnapshotResponse | null>(null);
  const [snapErr, setSnapErr] = useState<string | null>(null);
  const wanted = useRef(day);
  wanted.current = day;

  const fail = (e: unknown): string | null => {
    if (e instanceof SessionExpired) return null;
    return e instanceof ApiError && e.status === 404 ? t('noc.unavailable') : errText(e, t, 'noc.loadfail');
  };

  const loadDays = () => {
    adminGet<DaysResponse>(`/admin/noc/history/days?tz=${encodeURIComponent(tz)}&days=31`)
      .then(d => { setDays(d); })
      .catch(e => { const m = fail(e); if (m) setErr(m); });
  };
  const loadDay = (d: string) => {
    const b = dayBounds(d);
    if (!b) return;
    const q = `from=${encodeURIComponent(new Date(b.from).toISOString())}&to=${encodeURIComponent(new Date(b.to).toISOString())}`;
    adminGet<HistoryResponse>(`/admin/noc/history?${q}`)
      .then(h => { if (wanted.current === d) { setHist(h); setLoaded(d); setErr(null); } })
      .catch(e => { const m = fail(e); if (m && wanted.current === d) setErr(m); });
  };

  // Today keeps filling while the view is open, so it refreshes each minute (the store writes
  // one row a minute); a past day never changes and is read once.
  usePoll(() => {
    if (day !== today && loaded === day) return;
    loadDay(day);
    if (day === today) loadDays();
  }, 60_000, [day]);

  const pick = (d: string) => {
    if (d === day) return;
    setDay(d);
    setHist(null);
    setLoaded(null);
    setSel(null);
    setSnap(null);
    setSnapErr(null);
  };

  const bounds = useMemo(() => dayBounds(day), [day]);
  const samples = useMemo(() => (hist && loaded === day ? hist.samples : []), [hist, loaded, day]);
  const cells = useMemo(() => (bounds ? bucketSamples(samples, bounds.from, bounds.to) : []), [samples, bounds]);
  const incs = useMemo(() => incidents(samples, (hist?.step_s ?? 60) * 1000), [samples, hist]);
  const isToday = day === today;
  const lastCell = isToday ? cells.findIndex(c => c.end > now) : cells.length - 1;
  const maxCell = lastCell < 0 ? cells.length - 1 : lastCell;

  // The snapshot of the chosen moment. The previous one stays up until the next arrives.
  useEffect(() => {
    if (!sel) { setSnap(null); setSnapErr(null); return; }
    let stop = false;
    setSnapErr(null);
    adminGet<SnapshotResponse>(`/admin/noc/snapshot?at=${encodeURIComponent(new Date(sel.at).toISOString())}`)
      .then(s => { if (!stop) setSnap(s); })
      .catch(e => {
        if (stop || e instanceof SessionExpired) return;
        setSnap(null);
        setSnapErr(e instanceof ApiError && e.status === 404 ? t('noch.snap.none') : errText(e, t, 'noc.loadfail'));
      });
    return () => { stop = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel?.at]);

  const selectCell = (i: number) => {
    const c = cells[i];
    if (!c || i > maxCell) return;
    setSel({ cell: i, at: momentFor(samples, c, now) });
  };
  const step = (d: number) => {
    const base = sel ? sel.cell : d < 0 ? maxCell + 1 : -1;
    selectCell(Math.min(Math.max(base + d, 0), maxCell));
  };

  const time = useMemo(() => new Intl.DateTimeFormat(lang, { hour: '2-digit', minute: '2-digit' }), [lang]);
  const when = useMemo(() => new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeStyle: 'medium' }), [lang]);
  const status = (s: Status) => t(`noc.st.${s}`);

  const strip = useMemo(() => daysStrip(days?.days ?? [], today), [days, today]);
  const oldest = days?.days[0]?.day ?? shiftDay(today, -30);

  /* ---------- charts of the day ---------- */
  const charts = useMemo(() => {
    const s = hist?.series;
    if (!s || !Array.isArray(s.ts) || !s.ts.length) return null;
    const one = (name: string, color: string, vs: (number | null)[] | undefined, dashed?: boolean): ChartSeries =>
      ({ name, color, points: toPoints(s.ts, vs), dashed });
    return {
      cpu: [one(t('hl.s.cpu'), 'var(--beam)', s.cpu)],
      mem: [one(t('noch.s.mem'), 'var(--beam)', s.mem)],
      net: [one(t('noch.s.net'), 'var(--ok)', s.net_mbs)],
      loss: [one(t('noc.s.drops'), 'var(--alert)', s.net_loss), one(t('noc.s.errs'), 'var(--pending)', s.net_errors)],
      req: [one(t('hl.s.requests'), 'var(--ok)', s.http_rate)],
      fivexx: [one(t('noch.s.5xx'), 'var(--alert)', s.http_5xx)],
      db: [one(t('noch.s.db'), 'var(--beam)', s.db_ms)],
      probes: Object.entries(s.probes ?? {}).map(([host, p], i) => one(host, SERIES_COLORS[i % SERIES_COLORS.length], p.p50)),
      ai: Object.values(s.ai ?? {}).map((a, i) => one(a.name, SERIES_COLORS[i % SERIES_COLORS.length], a.fails)),
    };
  }, [hist, t]);
  const cc = (title: string, series: ChartSeries[], unit: string, yMax?: number) =>
    <ChartCard title={title} series={series} unit={unit} yMax={yMax} lang={lang} t={t} />;

  const cellTitle = (c: Cell): string => {
    const at = time.format(c.start);
    if (c.status === null) return t('noch.tl.cell.none', { time: at });
    const names = c.bad.slice(0, 3).map(b => badLabel(parseBad(b), t));
    return [t('noch.tl.cell', { time: at, status: status(c.status) }), ...names].join(' · ');
  };

  const hours = useMemo(() => {
    if (!bounds) return [];
    const d0 = new Date(bounds.from);
    return [0, 3, 6, 9, 12, 15, 18, 21].map(h => {
      const ms = new Date(d0.getFullYear(), d0.getMonth(), d0.getDate(), h).getTime();
      return { h, left: ((ms - bounds.from) / (bounds.to - bounds.from)) * 100, label: time.format(ms) };
    });
  }, [bounds, time]);

  const nowLeft = bounds && isToday ? ((now - bounds.from) / (bounds.to - bounds.from)) * 100 : null;
  const hasData = samples.length > 0;

  const bannerRight = snap ? (
    <>
      <b style={{ fontSize: 13, padding: '4px 12px', borderRadius: 999, border: '1px solid var(--beam)' }}>
        {t('noch.snap.note', { when: when.format(Date.parse(snap.ts)) })}
      </b>
      {onLive ? <button className="ghost" type="button" onClick={onLive}>{t('noch.snap.live')}</button> : null}
    </>
  ) : null;

  return (
    <>
      {err ? <div className="msg err">{err}</div> : null}

      <div className="card">
        <div className="inline" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <label className="inline" style={{ gap: 8 }}>
            <span>{t('noch.date')}</span>
            <input
              type="date"
              value={day}
              min={oldest}
              max={today}
              style={{ width: 'auto' }}
              onChange={e => { const v = e.target.value; if (/^\d{4}-\d{2}-\d{2}$/.test(v) && v >= oldest && v <= today) pick(v); }}
            />
          </label>
          <button className="ghost" type="button" disabled={day === today} onClick={() => pick(today)}>{t('noch.today')}</button>
          <button className="ghost" type="button" disabled={day === shiftDay(today, -1)} onClick={() => pick(shiftDay(today, -1))}>{t('noch.yesterday')}</button>
          {days ? <span className="hint" style={{ marginTop: 0 }}>{t('noch.kept', { n: days.retention_days })}</span> : null}
        </div>

        <h4 style={{ margin: '16px 0 8px', fontSize: 13 }}>{t('noch.days.title')}</h4>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {strip.map(({ day: d, row }) => {
            const tip = row ? t('noch.day.tip', { day: d, samples: row.samples, crit: row.crit, warn: row.warn }) : t('noch.day.none', { day: d });
            return (
              <button
                key={d}
                type="button"
                title={tip}
                aria-label={tip}
                aria-pressed={d === day}
                onClick={() => pick(d)}
                style={{
                  width: 26, height: 26, padding: 0, borderRadius: 6, fontSize: 10, fontWeight: 600, lineHeight: '26px',
                  border: 'none', cursor: 'pointer',
                  background: row ? COLOR[row.status] : NO_DATA,
                  color: row ? 'var(--on-beam)' : 'var(--muted)',
                  outline: d === day ? '2px solid var(--paper)' : 'none', outlineOffset: 1,
                }}
              >
                {Number(d.slice(8))}
              </button>
            );
          })}
        </div>
        <div className="hint">{t('noch.days.hint')}</div>
      </div>

      <div className="card">
        <h3 style={{ margin: '0 0 6px' }}>{t('noch.tl.title')}</h3>
        <div className="hint" style={{ marginTop: 0, marginBottom: 10 }}>{t('noch.tl.hint')}</div>
        {loaded !== day && !err ? <div className="empty">…</div> : !hasData ? <div className="empty">{t('noch.day.empty')}</div> : null}
        {cells.length ? (
          <div style={{ overflowX: 'auto', paddingBottom: 4 }}>
            <div style={{ position: 'relative', minWidth: cells.length * 2.2 }}>
              <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cells.length}, 1fr)`, gap: 1, height: 36 }}>
                {cells.map((c, i) => (
                  <div
                    key={c.start}
                    title={cellTitle(c)}
                    onClick={() => selectCell(i)}
                    style={{
                      background: c.status === null ? NO_DATA : COLOR[c.status],
                      opacity: i > maxCell ? 0.3 : 1,
                      cursor: i > maxCell ? 'default' : 'pointer',
                      outline: sel?.cell === i ? '2px solid var(--paper)' : 'none',
                      zIndex: sel?.cell === i ? 1 : 0,
                    }}
                  />
                ))}
              </div>
              {nowLeft !== null ? (
                <div
                  title={t('noch.tl.now')}
                  style={{ position: 'absolute', top: -4, bottom: 18, left: `${nowLeft}%`, width: 2, background: 'var(--paper)', pointerEvents: 'none' }}
                />
              ) : null}
              <div style={{ position: 'relative', height: 16, marginTop: 4 }}>
                {hours.map(h => (
                  <span key={h.h} className="hint" style={{ position: 'absolute', left: `${h.left}%`, top: 0, marginTop: 0, transform: 'translateX(-50%)' }}>{h.label}</span>
                ))}
              </div>
            </div>
          </div>
        ) : null}
        <div className="inline" style={{ gap: 10, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="ghost" type="button" aria-label={t('noch.tl.prev.label')} disabled={!hasData || (sel !== null && sel.cell <= 0)} onClick={() => step(-1)}>{t('noch.tl.prev')}</button>
          <button className="ghost" type="button" aria-label={t('noch.tl.next.label')} disabled={!hasData || (sel !== null && sel.cell >= maxCell)} onClick={() => step(1)}>{t('noch.tl.next')}</button>
          <span className="hint" style={{ marginTop: 0 }} role="status">
            {sel ? t('noch.tl.sel', { time: when.format(sel.at) }) : t('noch.tl.none')}
          </span>
        </div>
      </div>

      {hasData ? (
        <div className="card">
          <h3 style={{ margin: '0 0 10px' }}>{t('noch.inc.title')}</h3>
          {!incs.length ? <div className="hint" style={{ marginTop: 0 }}>{t('noch.inc.none')}</div> : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 6, maxHeight: 280, overflowY: 'auto' }}>
              {incs.map(inc => {
                const shown = inc.names.slice(0, MAX_NAMES).map(b => badLabel(b, t));
                const more = inc.names.length - shown.length;
                return (
                  <li key={inc.start}>
                    <button
                      type="button"
                      className="ghost"
                      style={{ width: '100%', textAlign: 'left', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}
                      onClick={() => {
                        const i = Math.min(Math.max(Math.floor((inc.start - (bounds?.from ?? 0)) / CELL_MS), 0), cells.length - 1);
                        setSel({ cell: i, at: inc.start });
                      }}
                    >
                      <span className={`pill ${PILL[inc.worst]}`}>{status(inc.worst)}</span>
                      <b style={{ fontWeight: 600 }}>{time.format(inc.start)} – {time.format(inc.end)}</b>
                      <span className="hint" style={{ marginTop: 0 }}>{span((inc.end - inc.start) / 1000 + (hist?.step_s ?? 60), t)}</span>
                      <span style={{ flex: 1, minWidth: 180 }}>{shown.join(' · ')}{more > 0 ? ` · ${t('noch.inc.more', { n: more })}` : ''}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}

      {snapErr ? <div className="msg err">{snapErr}</div> : null}
      {snap ? (
        <Board data={snap.snapshot} t={t} lang={lang} now={Date.parse(snap.ts)} bannerRight={bannerRight} noRtt />
      ) : !snapErr && hasData ? <div className="card"><p className="hint" style={{ margin: 0 }}>{t('noch.snap.pick')}</p></div> : null}

      {charts && hasData ? (
        <>
          <h3 style={{ margin: '4px 0 10px' }}>{t('noch.c.title')}</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 14, marginBottom: 14 }}>
            {cc(t('noch.c.cpu'), charts.cpu, '%', 100)}
            {cc(t('noch.c.mem'), charts.mem, '%', 100)}
            {cc(t('hl.c.net'), charts.net, ' MB/s')}
            {cc(t('noc.c.loss'), charts.loss, '%')}
            {cc(t('noch.c.req'), charts.req, '')}
            {cc(t('noch.c.5xx'), charts.fivexx, '%')}
            {cc(t('noch.c.db'), charts.db, ' ms')}
            {charts.probes.length ? cc(t('noc.c.rtt'), charts.probes, ' ms') : null}
            {charts.ai.length ? cc(t('noch.c.ai'), charts.ai, '') : null}
          </div>
        </>
      ) : null}
    </>
  );
}
