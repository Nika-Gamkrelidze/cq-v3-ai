'use client';
/* HISTORY — everything this workspace has run.
   ===========================================
   `/recordings` supersedes the old `/jobs` listing for a tenant: both read the same
   `audio_jobs` rows under the same scope, and the newer one also says which analysers have run,
   whether the audio is still stored and whether the source was a file or a pasted transcript.
   So the legacy list is MIGRATED, not kept beside it — rows the old /analyze pipeline created
   appear here too, and open in the workbench with whatever results they carry (no stored audio,
   so those play as text).

   A row is not a dead end: clicking it re-opens the recording in the Analyse tab. */

import { useCallback, useEffect, useState } from 'react';
import { STALE, useWs, type T } from './ctx';
import s from './workspace.module.css';

interface Recording {
  id: string; filename?: string | null; source?: string; status?: string;
  language?: string | null; duration_s?: number | null;
  ran?: Record<string, unknown> | null; created_by?: string | null; created_at: string;
}
interface Summary {
  id: string; call_count?: number; short_summary?: string | null;
  language?: string | null; created_by?: string | null; created_at: string;
}

const fmtSecs = (v: number | null | undefined) =>
  (v == null || !isFinite(v)) ? '—' : `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, '0')}`;

/** Which analysers have run, in the workbench's own words — never a bare true/false column. */
function RanChips({ t, ran }: { t: T; ran: Recording['ran'] }) {
  const r = (ran && typeof ran === 'object') ? ran : {};
  const on = (['factcheck', 'score', 'semantic'] as const).filter(k => r[k]);
  if (!on.length) return <span className="hint">—</span>;
  return <>{on.map(k => <span className="chip" key={k}>{t('wb.tab.' + k)}</span>)}</>;
}

/* Who ran it. Every user in the workspace sees the same History, so the author is what makes a
   row attributable; rows created before the name was recorded say so rather than showing a bare
   dash. */
function Who({ t, name }: { t: T; name?: string | null }) {
  return name ? <span className="chip">{name}</span> : <span className="hint">{t('tn.who.unknown')}</span>;
}

export function HistoryTab({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const [recs, setRecs] = useState<Recording[] | null>(null);
  const [recFailed, setRecFailed] = useState(false);
  const [sums, setSums] = useState<Summary[] | null>(null);
  const [sumFailed, setSumFailed] = useState(false);

  const load = useCallback(async () => {
    const [r, sm] = await Promise.all([
      ws.json<Recording[]>('/recordings?limit=50'),
      ws.json<Summary[]>('/summaries?limit=50'),
    ]);
    if (r !== STALE) {
      if (r === null) { setRecFailed(true); setRecs(null); }
      else { setRecFailed(false); setRecs(Array.isArray(r) ? r : []); }
    }
    if (sm !== STALE) {
      if (sm === null) { setSumFailed(true); setSums(null); }
      else { setSumFailed(false); setSums(Array.isArray(sm) ? sm : []); }
    }
  }, [ws]);

  useEffect(() => { if (on && ws.ready) void load(); }, [on, gen, ws.ready, load]);

  /* Without a workspace there is no scope header, and the recordings route answers an unscoped
     superadmin with EVERY tenant's calls — a listing from all customers under one workspace's
     name. Nothing loads until a workspace is chosen. */
  if (!ws.ready) return <div className="empty">{t('con.tenant.pick')}</div>;

  const rowProps = (go: () => void) => ({
    style: { cursor: 'pointer' },
    tabIndex: 0,
    role: 'button',
    title: t('tn.hist.open'),
    'aria-label': t('tn.hist.open'),
    onClick: go,
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
    },
  });

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>{t('tn.hist.rec')}</h3>
          <button type="button" className="ghost" onClick={() => void load()}>{t('btn.refresh')}</button>
        </div>
        <div style={{ marginTop: 12 }}>
          {recFailed ? <div className="msg err">{t('tkb.loadfail')}</div>
            : recs === null ? null
              : !recs.length ? <div className="empty">{t('tn.hist.rec.none')}</div>
                : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>{t('th.file')}</th><th className={s.tight}>{t('tn.th.source')}</th>
                          <th>{t('th.status')}</th><th className={s.tight}>{t('th.lang')}</th>
                          <th className={`${s.tight} hide-md`}>{t('tn.th.length')}</th>
                          <th className="hide-md">{t('tn.th.ran')}</th>
                          <th className={s.tight}>{t('tn.th.who')}</th><th>{t('th.when')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {recs.map(x => (
                          <tr key={x.id} {...rowProps(() => ws.openRecording(x.id))}>
                            <td>{x.filename || (x.source === 'text' ? t('wb.src.text') : '—')}</td>
                            <td className={s.tight}>
                              <span className="chip">{x.source === 'text' ? t('tn.src.text') : t('tn.src.audio')}</span>
                            </td>
                            <td>
                              <span className={`pill ${x.status === 'done' || x.status === 'ready' ? 'ready' : x.status === 'error' ? 'error' : 'processing'}`}>
                                {x.status}
                              </span>
                            </td>
                            <td className={s.tight}>{x.language || '—'}</td>
                            <td className={`${s.tight} hide-md`}>{fmtSecs(x.duration_s)}</td>
                            <td className="hide-md"><RanChips t={t} ran={x.ran} /></td>
                            <td className={s.tight}><Who t={t} name={x.created_by} /></td>
                            <td className="hint">{new Date(x.created_at).toLocaleString()}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
        </div>
      </div>

      <div className="card">
        <h3 style={{ margin: '0 0 12px' }}>{t('tn.hist.sum')}</h3>
        {sumFailed ? <div className="msg err">{t('tkb.loadfail')}</div>
          : sums === null ? null
            : !sums.length ? <div className="empty">{t('tn.hist.sum.none')}</div>
              : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th className={s.tight}>{t('tn.th.calls')}</th><th>{t('tn.th.summary')}</th>
                        <th className={s.tight}>{t('th.lang')}</th><th className={s.tight}>{t('tn.th.who')}</th>
                        <th>{t('th.when')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sums.map(x => (
                        <tr key={x.id} {...rowProps(() => ws.openSummary(x.id))}>
                          <td className={s.tight}>{x.call_count ?? 0}</td>
                          <td>{(x.short_summary || '').slice(0, 160) || '—'}</td>
                          <td className={s.tight}>{x.language || '—'}</td>
                          <td className={s.tight}><Who t={t} name={x.created_by} /></td>
                          <td className="hint">{new Date(x.created_at).toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
      </div>
    </>
  );
}
