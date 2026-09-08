'use client';
/* RETRIEVAL — the playground (what the bot actually retrieves) and plain KB search.

   The playground answers "what does retrieval actually return for this question, and did the
   vector index or the keyword fallback produce it" — it never calls a model. */

import { useState } from 'react';
import { toast } from '@/components/ui/Toast';
import { failMessage, useWs } from '../ctx';
import s from '../workspace.module.css';
import { RetrFlag, RetrMeta, type RetrievalBody } from './confidence';

interface Hit {
  title?: string; doc_type?: string; chunk_index?: number;
  score?: number | null; content?: string; document_id?: string;
}

export function Retrieval({ onChunks }: { onChunks: (docId: string) => void }) {
  const ws = useWs();
  const { t } = ws;

  const [query, setQuery] = useState('');
  const [topk, setTopk] = useState('8');
  const [thresh, setThresh] = useState('0');
  const [pg, setPg] = useState<{ body: RetrievalBody | null; hits: Hit[] } | null>(null);
  const [pgBusy, setPgBusy] = useState(false);

  const [q, setQ] = useState('');
  const [search, setSearch] = useState<{ body: RetrievalBody | null; hits: Hit[] } | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchErr, setSearchErr] = useState('');

  const runPlayground = async () => {
    if (!query.trim()) return;
    setPgBusy(true);
    setPg(null);
    const r = await ws.send<RetrievalBody>('POST', '/kb/playground', {
      query: query.trim(),
      top_k: parseInt(topk, 10) || 8,
      threshold: parseFloat(thresh) || 0,
    });
    setPgBusy(false);
    // A refusal is reported as a toast, exactly as it was — the results area is cleared rather
    // than filled with an error, because a stale ranking under a fresh question is worse than
    // nothing.
    if (!r.ok) { toast(failMessage(r, t), 'err'); return; }
    const body = r.data;
    setPg({ body, hits: Array.isArray(body?.results) ? (body!.results as Hit[]) : [] });
  };

  const runSearch = async () => {
    if (!q.trim()) return;
    setSearchBusy(true);
    setSearch(null);
    setSearchErr('');
    const r = await ws.send<RetrievalBody>('POST', '/kb/search', { query: q.trim() });
    setSearchBusy(false);
    if (!r.ok) { setSearchErr(failMessage(r, t)); return; }
    const body = r.data;
    setSearch({ body, hits: Array.isArray(body?.results) ? (body!.results as Hit[]) : [] });
  };

  return (
    <>
      <div className="card">
        <h3>{t('tkb.pg.heading')}</h3>
        <label htmlFor="kPgQuery">{t('kba.pg.query')}</label>
        <input
          id="kPgQuery" value={query} onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') void runPlayground(); }}
        />
        <div className="row" style={{ marginTop: 10 }}>
          <div className="w-num">
            <label htmlFor="kPgTopk">{t('kba.pg.topk')}</label>
            <input id="kPgTopk" type="number" value={topk} onChange={e => setTopk(e.target.value)} />
          </div>
          <div className="w-num">
            <label htmlFor="kPgThresh">{t('kba.pg.threshold')}</label>
            <input id="kPgThresh" type="number" step="0.05" value={thresh} onChange={e => setThresh(e.target.value)} />
          </div>
          <div style={{ flex: 0, alignSelf: 'flex-end' }}>
            <button type="button" className="primary" onClick={() => void runPlayground()}>{t('kba.pg.run')}</button>
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          {pgBusy ? <div className="empty"><span className="spinner" /></div>
            : pg ? (
              <>
                {/* Zero hits is exactly when the banner matters most (an empty KB reads as
                    "broken search" otherwise), so it is rendered before the empty state, not
                    after it. */}
                <RetrFlag t={t} d={pg.body} n={pg.hits.length} />
                <RetrMeta t={t} d={pg.body} n={pg.hits.length} />
                {pg.hits.length
                  ? pg.hits.map((h, i) => (
                    <div className="fc-claim" style={{ borderLeftColor: 'var(--beam)' }} key={i}>
                      <div className="inline" style={{ justifyContent: 'space-between', gap: 8 }}>
                        <span className="hint">{h.title || h.doc_type || 'KB'} · #{h.chunk_index ?? 0}</span>
                        <b>{h.score != null ? Number(h.score).toFixed(4) : '—'}</b>
                      </div>
                      <div className={s.scorebar}>
                        <i style={{ width: `${Math.max(0, Math.min(100, Math.round((h.score || 0) * 100)))}%` }} />
                      </div>
                      <div style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{h.content}</div>
                      {h.document_id ? (
                        <div className="inline" style={{ marginTop: 8 }}>
                          <button type="button" className="ghost" onClick={() => onChunks(h.document_id!)}>
                            {t('retr.opendoc')}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  ))
                  : <div className="empty">{t('kba.pg.nohits')}</div>}
              </>
            ) : null}
        </div>
      </div>

      <div className="card">
        <div className="row" style={{ alignItems: 'flex-end' }}>
          <div style={{ flex: 2 }}>
            <label htmlFor="kbq">{t('kb.searchlabel')}</label>
            <input
              id="kbq" value={q} placeholder={t('kb.search_ph')} onChange={e => setQ(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') void runSearch(); }}
            />
          </div>
          <div style={{ flex: 0 }}>
            <button type="button" className="ghost" onClick={() => void runSearch()}>{t('btn.search')}</button>
          </div>
        </div>
        <div>
          {searchBusy ? <div className="empty"><span className="spinner" />{t('btn.search')}…</div>
            : searchErr ? <div className="msg err">{searchErr}</div>
              : search ? (
                <div style={{ marginTop: 12 }}>
                  {/* `/kb/search` returns the same `confidence` block as the playground, so the
                      plain search box tells the same story rather than presenting a flat noise
                      band as matches. */}
                  <RetrFlag t={t} d={search.body} n={search.hits.length} />
                  <RetrMeta t={t} d={search.body} n={search.hits.length} />
                  {search.hits.length
                    ? search.hits.map((h, i) => (
                      <div className="card" style={{ margin: '10px 0 0' }} key={i}>
                        <div className="inline" style={{ justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                          <span className="hint">
                            {h.title || h.doc_type || 'KB'} · {t('retr.top')} {h.score != null ? Number(h.score).toFixed(3) : '—'}
                          </span>
                          {h.document_id ? (
                            <button type="button" className="ghost" onClick={() => onChunks(h.document_id!)}>
                              {t('retr.opendoc')}
                            </button>
                          ) : null}
                        </div>
                        <div style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{h.content}</div>
                      </div>
                    ))
                    : <div className="empty">{t('kb.nomatch')}</div>}
                </div>
              ) : null}
        </div>
      </div>
    </>
  );
}
