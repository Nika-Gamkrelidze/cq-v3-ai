'use client';
/* MAINTENANCE: export, and the queued full-KB re-embed.

   A full-KB re-embed is the single most expensive thing this workspace can trigger: it rebuilds
   every vector on the one CPU-bound embedder that also serves live retrieval for everyone. So
   the button only ENQUEUES — the worker does the work, throttled — and this panel polls the job
   row for progress. The button stays disabled while a job is active, and the server's unique
   index makes a double-click a 409 rather than a second job. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { apiMessage, downloadAuthed } from '@/lib/session';
import { failMessage, SCOPE, STALE, useWs } from '../ctx';
import s from '../workspace.module.css';

interface Job {
  state?: string; total_documents?: number; done_documents?: number;
  failed_documents?: number; created_at?: string; finished_at?: string; error?: string;
}

export function Maint({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const [job, setJob] = useState<Job | null>(null);
  const [failed, setFailed] = useState(false);
  const [starting, setStarting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /* `on` is read inside the poll's own timeout, which outlives the render that scheduled it —
     only keep polling while the Maintenance subtab is the one on screen. */
  const onRef = useRef(on);
  useEffect(() => { onRef.current = on; }, [on]);

  const stop = useCallback(() => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
  }, []);

  const poll = useCallback(async (scheduled: boolean) => {
    if (!scheduled) stop();
    const d = await ws.json<{ job?: Job | null }>('/kb/reembed/status');
    if (d === STALE) return;
    if (d === null) {
      // Could not read the job row. The button stays enabled — the real double-start guard is
      // the server's one-active-job index, which answers 409, not this panel.
      setFailed(true); setJob(null); stop(); return;
    }
    setFailed(false);
    const j = (typeof d === 'object' && d.job) || null;
    setJob(j);
    stop();
    const active = !!j && (j.state === 'queued' || j.state === 'running');
    if (active && onRef.current) timer.current = setTimeout(() => void poll(true), 4000);
  }, [ws, stop]);

  useEffect(() => {
    if (on && ws.ready) void poll(false);
    else stop();
    return stop;
  }, [on, gen, ws.ready, poll, stop]);

  const start = async () => {
    if (!(await confirmDialog(t('tkb.reembed.confirm'), { ok: t('tkb.reembed.start'), danger: false }))) return;
    setStarting(true);
    const r = await ws.send('POST', '/kb/reembed');
    setStarting(false);
    if (r.status === 409) toast(t('tkb.reembed.busy'), 'err');
    else if (r.ok) toast(t('tkb.reembed.queued'), 'ok');
    else toast(failMessage(r, t), 'err');
    void poll(false);
  };

  const download = async (format: 'json' | 'csv') => {
    try { await downloadAuthed(`/kb/export?format=${format}`, `kb-export.${format}`, { scope: SCOPE }); }
    catch (e) { ws.funnel(e); toast(apiMessage(e, t) || t('toast.error'), 'err'); }
  };

  const active = !!job && (job.state === 'queued' || job.state === 'running');
  const total = job?.total_documents || 0;
  const done = job?.done_documents || 0;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : (job?.state === 'done' ? 100 : 0);
  const pill = job?.state === 'done' ? 'ready' : job?.state === 'error' ? 'error' : active ? 'processing' : 'notinkb';

  return (
    <>
      <div className="card">
        <h3>{t('kba.export')}</h3>
        <p className="hint">{t('tkb.exp.hint')}</p>
        <div className="actions">
          <button type="button" className="ghost" onClick={() => void download('json')}>{t('kba.export')}</button>
          <button type="button" className="ghost" onClick={() => void download('csv')}>{t('kba.exportcsv')}</button>
        </div>
      </div>
      <div className="card">
        <h3><span>{t('tkb.reembed.heading')}</span><Tip text={t('tkb.reembed.desc')} /></h3>
        <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <button type="button" className="ghost" disabled={active || starting} onClick={() => void start()}>
            {t('tkb.reembed.start')}
          </button>
          <button type="button" className="ghost" onClick={() => void poll(false)}>{t('btn.refresh')}</button>
        </div>
        <div style={{ marginTop: 6 }}>
          {failed ? <div className="msg err">{t('tkb.loadfail')}</div>
            : !job ? <div className="hint">{t('tkb.reembed.none')}</div>
              : (
                <>
                  <div className="inline" style={{ justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                    <span className={`pill ${pill}`}>{t('tkb.reembed.state.' + job.state)}</span>
                    <span className="hint">
                      {t('tkb.reembed.progress', { done, total })}
                      {job.failed_documents
                        ? <> · <span className="warn-flag">{t('tkb.reembed.failed', { n: job.failed_documents })}</span></>
                        : null}
                    </span>
                  </div>
                  <div className={s.kbProg}><i style={{ width: `${pct}%` }} /></div>
                  <div className="hint">
                    {t('th.when')}: {job.created_at ? new Date(job.created_at).toLocaleString() : '—'}
                    {job.finished_at ? ` → ${new Date(job.finished_at).toLocaleString()}` : ''}
                  </div>
                  {job.error ? <div className="msg err">{job.error}</div> : null}
                </>
              )}
        </div>
      </div>
    </>
  );
}
