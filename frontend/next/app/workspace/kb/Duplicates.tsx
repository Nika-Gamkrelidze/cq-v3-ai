'use client';
import { useCallback, useEffect, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { toast } from '@/components/ui/Toast';
import { failMessage, STALE, useWs } from '../ctx';

interface DupBody {
  exact_duplicate_groups?: { count?: number; document_ids?: string[]; titles?: string[] }[];
  near_duplicate_pairs?: { similarity?: number; a_title?: string; b_title?: string }[];
  near_scan_skipped?: boolean;
}

export function Duplicates({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const [state, setState] = useState<'idle' | 'loading' | 'failed'>('idle');
  const [d, setD] = useState<DupBody | null>(null);

  const load = useCallback(async () => {
    setState('loading');
    const body = await ws.json<DupBody>('/kb/duplicates');
    if (body === STALE) return;
    if (!body || typeof body !== 'object') { setState('failed'); setD(null); return; }
    setState('idle');
    setD(body);
  }, [ws]);

  useEffect(() => { if (on && ws.ready) void load(); }, [on, gen, ws.ready, load]);

  const del = async (id: string, title: string) => {
    if (!(await confirmDialog(t('tkb.del.confirm', { title: title || '' }), { ok: t('kba.delete') }))) return;
    const r = await ws.send('DELETE', `/kb/documents/${id}`);
    if (r.ok) { toast(t('toast.deleted'), 'ok'); void load(); }
    else toast(failMessage(r, t), 'err');
  };

  const exact = Array.isArray(d?.exact_duplicate_groups) ? d!.exact_duplicate_groups! : [];
  const near = Array.isArray(d?.near_duplicate_pairs) ? d!.near_duplicate_pairs! : [];

  return (
    <div className="card">
      <div className="inline" style={{ justifyContent: 'space-between' }}>
        <h3 style={{ margin: 0 }}>{t('kba.tab.duplicates')}</h3>
        <button type="button" className="ghost" onClick={() => void load()}>{t('btn.refresh')}</button>
      </div>
      <div style={{ marginTop: 14 }}>
        {state === 'loading' ? <div className="empty"><span className="spinner" /></div>
          : state === 'failed' ? <div className="msg err">{t('tkb.loadfail')}</div>
            : (exact.length || near.length) ? (
              <>
                {exact.length ? <h4>{t('kba.dup.exact')}</h4> : null}
                {exact.map((g, gi) => {
                  const ids = Array.isArray(g.document_ids) ? g.document_ids : [];
                  const titles = Array.isArray(g.titles) ? g.titles : [];
                  return (
                    <div className="fc-claim v-CONTRADICTED" key={gi}>
                      <b>{g.count ?? ids.length}×</b> {t('tkb.dup.identical')}
                      {/* The first copy is kept by convention; the rest get a delete button
                          each, so nothing is removed without the tenant seeing which title it
                          is removing. */}
                      <div style={{ marginTop: 6 }}>
                        {ids.map((id, i) => (
                          <div className="inline" style={{ justifyContent: 'space-between', gap: 8, padding: '3px 0' }} key={id}>
                            <span>{titles[i] || id}{i === 0 ? <> <span className="chip">{t('tkb.dup.keep')}</span></> : null}</span>
                            {i === 0 ? null : (
                              <button type="button" className="danger" onClick={() => void del(id, titles[i] || '')}>
                                {t('kba.delete')}
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
                {near.length ? <h4>{t('kba.dup.near')}</h4> : null}
                {near.map((p, i) => (
                  <div className="fc-claim" key={i}>
                    <span className="pill notinkb">{(Number(p.similarity || 0) * 100).toFixed(1)}% {t('kba.dup.sim')}</span>
                    {' '}{p.a_title} ↔ {p.b_title}
                  </div>
                ))}
                {d?.near_scan_skipped ? <div className="hint" style={{ marginTop: 8 }}>{t('tkb.dup.skipped')}</div> : null}
              </>
            ) : <div className="empty">{t('kba.dup.none')}</div>}
      </div>
    </div>
  );
}
