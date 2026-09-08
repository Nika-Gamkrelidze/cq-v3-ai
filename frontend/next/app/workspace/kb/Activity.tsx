'use client';
import { useCallback, useEffect, useState } from 'react';
import { STALE, useWs } from '../ctx';
import s from '../workspace.module.css';

interface Event {
  action?: string; method?: string; status?: string; detail?: string;
  chunk_count?: number | null; duration_ms?: number | null; actor?: string; created_at?: string;
}

export function Activity({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const [filter, setFilter] = useState('');
  const [state, setState] = useState<'idle' | 'loading' | 'failed'>('idle');
  const [events, setEvents] = useState<Event[]>([]);

  const load = useCallback(async () => {
    setState('loading');
    const p = new URLSearchParams({ limit: '100' });
    if (filter.trim()) p.set('action', filter.trim());
    const d = await ws.json<{ events?: Event[] }>(`/kb/activity?${p}`);
    if (d === STALE) return;
    if (d === null) { setState('failed'); setEvents([]); return; }
    setState('idle');
    setEvents(Array.isArray(d.events) ? d.events : []);
  }, [ws, filter]);

  useEffect(() => { if (on && ws.ready) void load(); },
    // Deliberately NOT on `filter`: the box is applied by Enter or Refresh, as it always was.
    [on, gen, ws.ready]);   // eslint-disable-line react-hooks/exhaustive-deps

  const pill = (st?: string) => (st === 'ready' || st === 'ok' ? 'ready' : st === 'error' ? 'error' : 'processing');

  return (
    <div className="card">
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div style={{ flex: 1 }}>
          <label htmlFor="kActFilter">{t('tkb.act.filter')}</label>
          <input
            id="kActFilter" value={filter} placeholder={t('tkb.act.filter.ph')}
            onChange={e => setFilter(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') void load(); }}
          />
        </div>
        <div style={{ flex: 0 }}>
          <button type="button" className="ghost" onClick={() => void load()}>{t('btn.refresh')}</button>
        </div>
      </div>
      <div style={{ marginTop: 10 }}>
        {state === 'loading' ? <div className="empty"><span className="spinner" /></div>
          : state === 'failed' ? <div className="msg err">{t('tkb.loadfail')}</div>
            : !events.length ? <div className="empty">{t('kba.act.none')}</div>
              : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t('tkb.act.filter')}</th><th>{t('tkb.act.method')}</th><th>{t('th.status')}</th>
                        <th>{t('tkb.act.detail')}</th><th className={s.tight}>{t('th.chunks')}</th>
                        <th className={s.tight}>ms</th><th>{t('tkb.act.actor')}</th><th>{t('th.when')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {events.map((e, i) => (
                        <tr key={i}>
                          <td><b>{e.action}</b></td>
                          <td>{e.method || '—'}</td>
                          <td><span className={`pill ${pill(e.status)}`}>{e.status || '—'}</span></td>
                          <td>{e.detail || '—'}</td>
                          <td className={s.tight}>{e.chunk_count ?? '—'}</td>
                          <td className={s.tight}>{e.duration_ms ?? '—'}</td>
                          <td className="hint">{e.actor || '—'}</td>
                          <td className="hint">{e.created_at ? new Date(e.created_at).toLocaleString() : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
      </div>
    </div>
  );
}
