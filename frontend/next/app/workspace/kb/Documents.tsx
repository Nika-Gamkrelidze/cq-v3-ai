'use client';
/* DOCUMENTS — the list, its filters, its pager, and every write a document can take.

   Every call is a `/kb/*` route: the workspace is taken from the credential server-side, never
   from anything this page can set, so a tampered request can only ever reach this workspace's
   own knowledge base. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog, showModal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { toast } from '@/components/ui/Toast';
import { failMessage, STALE, useWs, type Sent, type T } from '../ctx';
import s from '../workspace.module.css';
import { docsOf, totalOf, type DocRow, type DocsBody } from './docs';

const LIMIT = 50;

export function Documents({ on, gen, reload, onChunks }: {
  on: boolean;
  gen: number;
  /** Bumped by an import that has just landed, so a visible list picks the new rows up. */
  reload: number;
  onChunks: (docId: string) => void;
}) {
  const ws = useWs();
  const { t } = ws;

  const [status, setStatus] = useState('');
  const [type, setType] = useState('');
  const [tag, setTag] = useState('');
  const [vis, setVis] = useState('');
  const [q, setQ] = useState('');
  const [offset, setOffset] = useState(0);

  const [docs, setDocs] = useState<DocRow[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [total, setTotal] = useState<number | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [busyRow, setBusyRow] = useState('');

  /* The filter boxes are applied by Enter or by Refresh, never per keystroke — so the load
     reads them from a ref rather than taking them as effect dependencies. */
  const filters = useRef({ status, type, tag, vis, q });
  filters.current = { status, type, tag, vis, q };

  const load = useCallback(async (at = offset) => {
    setSel(new Set());
    const f = filters.current;
    const p = new URLSearchParams({ limit: String(LIMIT), offset: String(at) });
    if (f.status) p.set('status', f.status);
    if (f.type.trim()) p.set('doc_type', f.type.trim());
    if (f.tag.trim()) p.set('tag', f.tag.trim());
    if (f.vis) p.set('visibility', f.vis);
    if (f.q.trim()) p.set('q', f.q.trim());
    const d = await ws.json<DocsBody>(`/kb/documents?${p}`);
    if (d === STALE) return;
    // null == the request failed; [] == the workspace genuinely has no documents. Saying
    // "no documents yet" when the server is down would be a lie about their own KB.
    if (d === null) { setFailed(true); setDocs(null); setTotal(null); return; }
    setFailed(false);
    setDocs(docsOf(d));
    // Two numbers, deliberately: how many rows THIS page holds, and how many exist in total
    // when the server is willing to say. Conflating them is what made the pager guess, and a
    // real total is the only way "›" can be right on the last page.
    setTotal(totalOf(d));
  }, [ws, offset]);

  useEffect(() => { if (on && ws.ready) void load(offset); }, [on, gen, reload, ws.ready, offset, load]);

  const apply = () => { if (offset === 0) void load(0); else setOffset(0); };

  const toggle = (id: string, checked: boolean) => {
    setSel(prev => {
      const next = new Set(prev);
      if (checked) next.add(id); else next.delete(id);
      return next;
    });
  };

  /* ---- single-row actions ---- */

  const visibility = async (act: 'publish' | 'unpublish', id: string, title: string) => {
    // Publishing decides what the public bot may repeat to a customer word for word, so even
    // the single-row toggle confirms and says out loud what "public" means.
    const ok = await confirmDialog(t(`vis.confirm.${act}.one`, { title: title || '' }),
      { ok: t('vis.' + act), danger: act === 'unpublish' });
    if (!ok) return;
    const r = await ws.send('PATCH', `/kb/documents/${id}/visibility`,
      { visibility: act === 'publish' ? 'public' : 'internal' });
    if (r.ok) { toast(t(`vis.done.${act}`), 'ok'); void load(); }
    else toast(failMessage(r, t), 'err');
  };

  const remove = async (id: string, title: string) => {
    if (!(await confirmDialog(t('tkb.del.confirm', { title: title || '' }), { ok: t('kba.delete') }))) return;
    const r = await ws.send('DELETE', `/kb/documents/${id}`);
    if (r.ok) { toast(t('toast.deleted'), 'ok'); void load(); }
    else toast(failMessage(r, t), 'err');
  };

  const reembed = async (id: string) => {
    setBusyRow(id);
    const r = await ws.send<{ reembedded_chunks?: number }>('POST', `/kb/documents/${id}/reembed`);
    setBusyRow('');
    if (r.ok) toast(t('tkb.reembed.done', { n: r.data?.reembedded_chunks ?? 0 }), 'ok');
    else toast(failMessage(r, t), 'err');
    void load();
  };

  const edit = async (id: string) => {
    const doc = await ws.json<DocRow>(`/kb/documents/${id}`);
    if (doc === STALE) return;
    if (!doc || !doc.id) { toast(t('toast.error'), 'err'); return; }
    const saved = await showModal(close => (
      <DocEditor
        t={t} doc={doc} close={close}
        save={body => ws.send('PUT', `/kb/documents/${id}`, body)}
      />
    ));
    if (saved) { toast(t('toast.saved'), 'ok'); void load(); }
  };

  /* ---- bulk ---- */

  const bulk = async (action: 'delete' | 'reembed' | 'publish' | 'unpublish' | 'retag', tags?: string[]) => {
    if (!sel.size) return;
    if (action === 'delete'
      && !(await confirmDialog(t('tkb.bulk.delete.confirm', { n: sel.size }), { ok: t('kba.delete') }))) return;
    // Bulk publish is the most consequential control on this page: it is the moment internal
    // text becomes something a customer can be shown verbatim. Say that, don't count rows.
    if (action === 'publish' || action === 'unpublish') {
      const ok = await confirmDialog(t(`vis.confirm.${action}`, { n: sel.size }),
        { ok: t('vis.' + action), danger: action === 'publish' });
      if (!ok) return;
    }
    if (action === 'reembed'
      && !(await confirmDialog(t('tkb.bulk.reembed.confirm', { n: sel.size }),
        { ok: t('kba.reembed'), danger: false }))) return;
    const r = await ws.send<{ affected?: number }>('POST', '/kb/bulk',
      { action, document_ids: [...sel], tags });
    if (r.ok) { toast(t('bulk.done.' + action, { n: r.data?.affected ?? 0 }), 'ok'); void load(); }
    else toast(failMessage(r, t), 'err');
  };

  /* A brand modal, never a native prompt() — a native prompt leaking into bulk retag was a
     fixed QA bug, and MIGRATION.md lists the rule. */
  const retag = async () => {
    const value = await showModal(close => <RetagPrompt t={t} close={close} />, { maxWidth: '460px' });
    if (typeof value !== 'string') return;
    void bulk('retag', value.split(',').map(x => x.trim()).filter(Boolean));
  };

  /* ---- pager ---- */

  const count = docs?.length ?? 0;
  const page = Math.floor(offset / LIMIT) + 1;
  // With a real total, "is there a next page" is a fact; without one, a full page is the only
  // hint there might be more.
  const more = total == null ? count >= LIMIT : offset + count < total;

  const allChecked = !!docs && docs.length > 0 && docs.every(x => sel.has(x.id));

  return (
    <div className="card">
      <div className="row">
        <div>
          <label htmlFor="kfStatus">{t('kba.f.status')}</label>
          <Select
            id="kfStatus" value={status} onChange={v => { setStatus(v); setOffset(0); }}
            ariaLabel={t('kba.f.status')}
            options={[
              { value: '', label: t('kba.f.all') },
              { value: 'ready', label: 'ready' },
              { value: 'processing', label: 'processing' },
              { value: 'pending', label: 'pending' },
              { value: 'error', label: 'error' },
            ]}
          />
        </div>
        <div>
          <label htmlFor="kfType">{t('kba.f.type')}</label>
          <input id="kfType" value={type} onChange={e => setType(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') apply(); }} />
        </div>
        <div>
          <label htmlFor="kfTag">{t('kba.f.tag')}</label>
          <input id="kfTag" value={tag} onChange={e => setTag(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') apply(); }} />
        </div>
        <div>
          <label htmlFor="kfVis">{t('vis.col')}</label>
          <Select
            id="kfVis" value={vis} onChange={v => { setVis(v); setOffset(0); }}
            ariaLabel={t('vis.col')}
            options={[
              { value: '', label: t('vis.all') },
              { value: 'public', label: t('vis.public') },
              { value: 'internal', label: t('vis.internal') },
            ]}
          />
        </div>
        <div style={{ flex: 2 }}>
          <label htmlFor="kfSearch">{t('kba.f.search')}</label>
          <input id="kfSearch" value={q} onChange={e => setQ(e.target.value)}
                 onKeyDown={e => { if (e.key === 'Enter') apply(); }} />
        </div>
        <div style={{ flex: 0, alignSelf: 'flex-end' }}>
          <button type="button" className="ghost" onClick={apply}>{t('btn.refresh')}</button>
        </div>
      </div>

      {sel.size > 0 && (
        <div className={s.selbar}>
          <span><b>{sel.size}</b> <span>{t('kba.selected')}</span></span>
          <button type="button" className="ghost" onClick={() => void bulk('publish')}>{t('vis.bulk.publish')}</button>
          <button type="button" className="ghost" onClick={() => void bulk('unpublish')}>{t('vis.bulk.unpublish')}</button>
          <button type="button" className="ghost" onClick={() => void bulk('reembed')}>{t('kba.bulk.reembed')}</button>
          <button type="button" className="ghost" onClick={() => void retag()}>{t('kba.bulk.retag')}</button>
          <button type="button" className="danger" onClick={() => void bulk('delete')}>{t('kba.bulk.delete')}</button>
        </div>
      )}

      <div style={{ marginTop: 10 }}>
        {failed ? <div className="msg err">{t('tkb.loadfail')}</div>
          : docs === null ? null
            : !docs.length ? <div className="empty">{t('tkb.docs.none')}</div>
              : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>
                          <input
                            type="checkbox" aria-label={t('kba.selected')} checked={allChecked}
                            onChange={e => setSel(e.target.checked ? new Set(docs.map(x => x.id)) : new Set())}
                          />
                        </th>
                        <th>{t('th.title')}</th><th>{t('th.category')}</th>
                        <th className="hide-md">{t('kba.doc.tags')}</th>
                        <th className={s.tight}>{t('th.chunks')}</th><th>{t('th.status')}</th>
                        <th className={s.tight}>{t('vis.col')}</th>
                        <th className={`${s.tight} hide-md`}>{t('tkb.th.source')}</th><th />
                      </tr>
                    </thead>
                    <tbody>
                      {docs.map(x => {
                        const pub = x.visibility === 'public';
                        const act = pub ? 'unpublish' : 'publish';
                        return (
                          <tr key={x.id}>
                            <td>
                              <input
                                type="checkbox" aria-label={x.title} checked={sel.has(x.id)}
                                onChange={e => toggle(x.id, e.target.checked)}
                              />
                            </td>
                            <td>{x.title}</td>
                            <td>{x.doc_type}</td>
                            <td className="hide-md">
                              {(Array.isArray(x.tags) ? x.tags : []).length
                                ? (x.tags as string[]).map(tg => <span className="chip" key={tg}>{tg}</span>)
                                : '—'}
                            </td>
                            <td className={s.tight}>{x.chunk_count ?? 0}</td>
                            <td>
                              <span className={`pill ${x.status}`}>{x.status}</span>
                              {x.error ? <> <span className="hint" title={x.error}>⚠</span></> : null}
                            </td>
                            <td className={s.tight}>
                              <span className={`pill ${pub ? s.visPublic : s.visInternal}`}>
                                {pub ? t('vis.public') : t('vis.internal')}
                              </span>
                            </td>
                            <td className={`${s.tight} hint hide-md`}>{x.source_type || '—'}</td>
                            <td className="inline">
                              <button
                                type="button" className="act" title={t('vis.' + act)} aria-label={t('vis.' + act)}
                                onClick={() => void visibility(act, x.id, x.title)}
                              >{pub ? '🚫' : '🤖'}</button>
                              <button type="button" className="act" title={t('kba.edit')} aria-label={t('kba.edit')}
                                      onClick={() => void edit(x.id)}>✏️</button>
                              <button type="button" className="act" title={t('kba.chunks')} aria-label={t('kba.chunks')}
                                      onClick={() => onChunks(x.id)}>🧩</button>
                              <button
                                type="button" className="act" title={t('kba.reembed')} aria-label={t('kba.reembed')}
                                disabled={busyRow === x.id} onClick={() => void reembed(x.id)}
                              >{busyRow === x.id ? <span className="spinner" /> : '↻'}</button>
                              <button type="button" className="act danger" title={t('kba.delete')} aria-label={t('kba.delete')}
                                      onClick={() => void remove(x.id, x.title)}>🗑</button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
      </div>

      <div className="inline" style={{ justifyContent: 'center', marginTop: 10 }}>
        {docs && !failed ? (
          (offset > 0 || more) ? (
            <>
              <button type="button" className="ghost" disabled={offset <= 0}
                      onClick={() => setOffset(Math.max(0, offset - LIMIT))}>‹</button>
              <span className="hint">{page}</span>
              <button type="button" className="ghost" disabled={!more}
                      onClick={() => setOffset(offset + LIMIT)}>›</button>
            </>
          ) : <span className="hint">{total == null ? count : total}</span>
        ) : null}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- the modals */

function DocEditor({ t, doc, close, save }: {
  t: T; doc: DocRow; close: (v?: unknown) => void;
  save: (body: Record<string, unknown>) => Promise<Sent>;
}) {
  const [title, setTitle] = useState(doc.title || '');
  const [type, setType] = useState(doc.doc_type || '');
  const [tags, setTags] = useState((doc.tags || []).join(', '));
  const [meta, setMeta] = useState(JSON.stringify(doc.metadata || {}));
  const [content, setContent] = useState(doc.content_text || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const go = async () => {
    let parsed: unknown;
    try { parsed = JSON.parse(meta || '{}'); }
    catch { setErr(t('tkb.badjson')); return; }
    const body: Record<string, unknown> = {
      title, doc_type: type,
      tags: tags.split(',').map(x => x.trim()).filter(Boolean),
      metadata: parsed,
    };
    // Only send `text` when it actually changed — sending it re-chunks and re-embeds.
    if (content !== (doc.content_text || '')) body.text = content;
    setBusy(true);
    const r = await save(body);
    if (r.ok) { close(true); return; }
    setBusy(false);
    setErr(failMessage(r, t));
  };

  return (
    <>
      <h3>{t('kba.edit')}: {doc.title}</h3>
      <label>{t('kba.doc.title')}</label>
      <input value={title} onChange={e => setTitle(e.target.value)} />
      <label>{t('kba.doc.type')}</label>
      <input value={type} onChange={e => setType(e.target.value)} />
      <label>{t('kba.doc.tags')}</label>
      <input value={tags} onChange={e => setTags(e.target.value)} />
      <label>{t('kba.doc.meta')}</label>
      <textarea style={{ minHeight: 60 }} value={meta} onChange={e => setMeta(e.target.value)} />
      <label>{t('kba.doc.content')}</label>
      <textarea style={{ minHeight: 160 }} value={content} onChange={e => setContent(e.target.value)} />
      <div className="hint" style={{ marginTop: 6 }}>{t('tkb.edit.warn')}</div>
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

function RetagPrompt({ t, close }: { t: T; close: (v?: unknown) => void }) {
  const [value, setValue] = useState('');
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  return (
    <>
      <h3>{t('kba.bulk.retag')}</h3>
      <label>{t('kba.doc.tags')}</label>
      <input
        ref={ref} placeholder="a, b" value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') close(value); }}
      />
      <div className="actions">
        <button type="button" className="ghost" onClick={() => close()}>{t('btn.cancel')}</button>
        <button type="button" className="primary" onClick={() => close(value)}>{t('kba.save')}</button>
      </div>
    </>
  );
}
