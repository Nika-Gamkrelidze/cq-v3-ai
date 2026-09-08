'use client';
/* CHUNKS — the finest-grained control there is: a chunk, not a document, is what retrieval
   matches. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog, showModal } from '@/components/ui/Modal';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { Select } from '@/components/ui/Select';
import { failMessage, STALE, useWs, type Sent, type T } from '../ctx';
import { docsOf, type DocRow } from './docs';

interface Chunk {
  id: string; chunk_index?: number; token_count?: number;
  has_embedding?: boolean; content?: string;
}

/** "Show me this document's chunks", as a one-shot request rather than a value to clear.
    `seq` rises on every ask, so arriving from a document row twice in a row is two requests —
    and an ordinary subtab click, which does not raise it, keeps the picker where it is. */
export interface ChunkRequest { id: string; seq: number }

export function Chunks({ on, gen, req }: { on: boolean; gen: number; req: ChunkRequest }) {
  const ws = useWs();
  const { t } = ws;
  const [docs, setDocs] = useState<DocRow[]>([]);
  const [docId, setDocId] = useState('');
  const [chunks, setChunks] = useState<Chunk[] | null>(null);
  const [state, setState] = useState<'empty' | 'loading' | 'failed' | 'ok'>('empty');
  const docIdRef = useRef('');
  useEffect(() => { docIdRef.current = docId; }, [docId]);
  const usedSeq = useRef(0);

  const loadChunks = useCallback(async (id: string) => {
    if (!id) { setState('empty'); setChunks(null); return; }
    setState('loading');
    const d = await ws.json<Chunk[]>(`/kb/documents/${id}/chunks`);
    if (d === STALE) return;
    if (d === null) { setState('failed'); setChunks(null); return; }
    setChunks(Array.isArray(d) ? d : []);
    setState('ok');
  }, [ws]);

  /* Opening Chunks from a document row goes through the picker reload (rather than loading the
     chunks a second time in parallel): two in-flight loads could finish out of order and leave
     the picker on the previously selected document. */
  const loadDocs = useCallback(async () => {
    const fresh = req.seq !== usedSeq.current;
    usedSeq.current = req.seq;
    const wanted = (fresh && req.id) || docIdRef.current;
    const d = await ws.json<DocRow[] | { documents?: DocRow[] }>('/kb/documents?limit=500');
    if (d === STALE) return;
    // Same two shapes as the Documents list: a bare list from the tenant/partner route, and
    // {documents} from the operator twin. Reading only the first left this picker always empty.
    const list = docsOf(d);
    setDocs(list);
    const pick = wanted && list.some(x => x.id === wanted) ? wanted : (list[0]?.id || '');
    setDocId(pick);
    if (pick) void loadChunks(pick);
    else { setState('empty'); setChunks(null); }
  }, [ws, req, loadChunks]);

  useEffect(() => { if (on && ws.ready) void loadDocs(); },
    // `req.seq` deliberately participates: arriving from a document row is a fresh request to
    // show that document, even when the subtab was already open.
    [on, gen, ws.ready, req.seq]);   // eslint-disable-line react-hooks/exhaustive-deps

  const delChunk = async (id: string) => {
    if (!(await confirmDialog(t('tkb.chunk.del.confirm'), { ok: t('kba.delete') }))) return;
    const r = await ws.send('DELETE', `/kb/chunks/${id}`);
    if (r.ok) { toast(t('toast.deleted'), 'ok'); void loadChunks(docId); }
    else toast(failMessage(r, t), 'err');
  };

  const editChunk = async (c: Chunk) => {
    const saved = await showModal(close => (
      <ChunkEditor
        t={t} chunk={c} close={close}
        save={body => ws.send('PUT', `/kb/chunks/${c.id}`, body)}
      />
    ));
    if (saved) { toast(t('toast.saved'), 'ok'); void loadChunks(docId); }
  };

  return (
    <div className="card">
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div style={{ flex: 2 }}>
          <label htmlFor="kChunkDoc"><span>{t('tkb.chunks.pick')}</span><Tip text={t('tkb.chunks.hint')} /></label>
          <Select
            id="kChunkDoc" value={docId}
            onChange={v => { setDocId(v); void loadChunks(v); }}
            options={docs.length
              ? docs.map(x => ({ value: x.id, label: `${x.title} · ${x.chunk_count ?? 0}` }))
              : [{ value: '', label: '—' }]}
            ariaLabel={t('tkb.chunks.pick')}
          />
        </div>
        <div style={{ flex: 0 }}>
          <button type="button" className="ghost" onClick={() => void loadChunks(docId)}>{t('btn.refresh')}</button>
        </div>
      </div>
      <div style={{ marginTop: 10 }}>
        {state === 'loading' ? <div className="empty"><span className="spinner" /></div>
          : state === 'failed' ? <div className="msg err">{t('tkb.loadfail')}</div>
            : state === 'empty' ? <div className="empty">{docs.length ? t('tkb.chunks.pickone') : t('tkb.docs.none')}</div>
              : !chunks?.length ? <div className="empty">{t('tkb.chunks.none')}</div>
                : chunks.map(c => (
                  <div className="fc-claim" key={c.id}
                       style={{ borderLeftColor: c.has_embedding ? 'var(--ok)' : 'var(--alert)' }}>
                    <div className="inline" style={{ justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                      <span className="hint">
                        #{c.chunk_index ?? 0} · {c.token_count ?? 0} tok
                        {c.has_embedding ? null : <> · <span className="warn-flag">{t('tkb.chunk.noembed')}</span></>}
                      </span>
                      <span className="inline">
                        <button type="button" className="ghost" onClick={() => void editChunk(c)}>{t('kba.chunk.edit')}</button>
                        <button type="button" className="danger" onClick={() => void delChunk(c.id)}>{t('kba.chunk.delete')}</button>
                      </span>
                    </div>
                    <div style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{c.content}</div>
                  </div>
                ))}
      </div>
    </div>
  );
}

/** The chunk editor, inside a brand modal — never a native prompt. */
function ChunkEditor({ t, chunk, close, save }: {
  t: T; chunk: Chunk; close: (v?: unknown) => void;
  save: (body: { content: string }) => Promise<Sent>;
}) {
  const [content, setContent] = useState(chunk.content || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const go = async () => {
    setBusy(true);
    const r = await save({ content });
    if (r.ok) { close(true); return; }
    setBusy(false);
    setErr(failMessage(r, t));
  };

  return (
    <>
      <h3>{t('kba.chunk.edit')}</h3>
      <div className="hint">{t('tkb.chunk.edit.hint')}</div>
      <textarea style={{ minHeight: 180 }} value={content} onChange={e => setContent(e.target.value)} />
      <div className="actions">
        <button type="button" className="ghost" onClick={() => close()}>{t('btn.cancel')}</button>
        <button type="button" className="primary" disabled={busy} onClick={() => void go()}>
          {busy ? <span className="spinner" /> : t('kba.save')}
        </button>
      </div>
      <div className={`msg${err ? ' err' : ''}`}>{err}</div>
    </>
  );
}
