'use client';
import { useCallback, useEffect, useState } from 'react';
import { STALE, useWs } from '../ctx';

interface Stats {
  documents?: number; public_documents?: number; chunks?: number; embedding_coverage?: number;
  failed?: number; in_progress?: number; approx_tokens?: number; last_updated?: string | null;
}

export function Overview({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const [stats, setStats] = useState<Stats | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    const st = await ws.json<Stats>('/kb/stats');
    if (st === STALE) return;
    if (!st || typeof st !== 'object') { setFailed(true); setStats(null); return; }
    setFailed(false);
    setStats(st);
  }, [ws]);

  useEffect(() => { if (on && ws.ready) void load(); }, [on, gen, ws.ready, load]);

  const tile = (n: string | number, l: string) => (
    <div className="stat" key={l}><b>{n}</b><span>{l}</span></div>
  );

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ margin: 0 }}>{t('tkb.overview.heading')}</h3>
        <button type="button" className="ghost" onClick={() => void load()}>{t('btn.refresh')}</button>
      </div>
      {failed ? <div className="msg err">{t('tkb.loadfail')}</div> : null}
      <div className="stat-grid" style={{ marginTop: 14 }}>
        {stats ? [
          tile(stats.documents ?? 0, t('kba.stat.documents')),
          tile(stats.public_documents ?? 0, t('vis.stat.public')),
          tile(stats.chunks ?? 0, t('kba.stat.chunks')),
          tile((stats.embedding_coverage ?? 0) + '%', t('kba.stat.coverage')),
          tile(stats.failed ?? 0, t('kba.stat.failed')),
          tile(stats.in_progress ?? 0, t('kba.stat.inprogress')),
          tile(stats.approx_tokens ?? 0, t('kba.stat.tokens')),
          tile(stats.last_updated ? new Date(stats.last_updated).toLocaleDateString() : '—', t('kba.stat.lastupd')),
        ] : null}
      </div>
    </div>
  );
}
