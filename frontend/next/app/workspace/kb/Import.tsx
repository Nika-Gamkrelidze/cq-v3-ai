'use client';
/* IMPORT — file, pasted text, or CSV.

   Several files of any accepted type in one go: each file is its own request and its own
   document, so one bad file fails alone (named below) instead of aborting the batch.
   Sequential — parsing is synchronous server-side. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { apiGet, apiMessage, apiUpload } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SCOPE, useWs } from '../ctx';

type Mode = 'file' | 'text' | 'csv';

const TEMPLATE_KINDS = ['pdf', 'docx', 'txt', 'md', 'csv'] as const;
const ACCEPT = '.pdf,.docx,.xlsx,.xlsm,.csv,.txt,.md';

export function Import({ onImported }: { onImported: () => void }) {
  const ws = useWs();
  const { t } = ws;
  const { lang } = useI18n();

  const [mode, setMode] = useState<Mode>('file');
  const [docType, setDocType] = useState('');
  const [title, setTitle] = useState('');
  const [tags, setTags] = useState('');
  const [meta, setMeta] = useState('');
  const [text, setText] = useState('');
  const [restructure, setRestructure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; kind: '' | 'ok' | 'err' }>({ text: '', kind: '' });

  const fileRef = useRef<HTMLInputElement>(null);
  const csvRef = useRef<HTMLInputElement>(null);
  /* A restructured import polls for minutes; a page that has moved on must not keep asking. */
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  /* Template downloads under the importer: users copy a working structure instead of guessing.
     Language-matched (KA files for the Georgian UI, EN otherwise). */
  const tplLang = lang === 'ka' ? 'ka' : 'en';
  const templates = (
    <div className="hint" style={{ marginTop: 8 }}>
      <span>{t('kb.templates')}</span>{' '}
      <span>
        {TEMPLATE_KINDS.map((x, i) => (
          <span key={x}>
            {i ? ' · ' : ''}
            <a href={`/guides/templates/kb-template-${tplLang}.${x}`} download>{x.toUpperCase()}</a>
          </span>
        ))}
      </span>
    </div>
  );

  /* A restructured import runs for minutes, far past the quick refresh timers below — poll the
     document until it settles and surface the outcome where the uploader is looking, instead of
     leaving the error as a tooltip they must hunt for. */
  const pollRestr = useCallback(async (id: string) => {
    for (let i = 0; i < 40; i++) {
      await new Promise(res => setTimeout(res, 5000));
      if (!alive.current) return;
      let doc: { status?: string; error?: string } | null = null;
      try { doc = await apiGet<{ status?: string; error?: string }>(`/kb/documents/${id}`, { scope: SCOPE }); }
      catch { /* a blip mid-poll is not an outcome */ }
      if (!doc || !doc.status || doc.status === 'pending' || doc.status === 'processing') continue;
      if (!alive.current) return;
      if (doc.status === 'ready') {
        setMsg({ text: t('kb.restr.done'), kind: 'ok' });
        toast(t('kb.restr.done'), 'ok');
      } else {
        setMsg({ text: doc.error || t('kb.restr.fail'), kind: 'err' });
        toast(t('kb.restr.fail'), 'err');
      }
      onImported();
      return;
    }
  }, [t, onImported]);

  const run = async () => {
    setMsg({ text: '', kind: '' });
    let parsedMeta: unknown;
    try { parsedMeta = meta.trim() ? JSON.parse(meta) : {}; }
    catch { setMsg({ text: t('tkb.badjson'), kind: 'err' }); return; }

    const type = docType.trim() || (mode === 'csv' ? 'faq' : mode === 'text' ? 'note' : 'document');
    setBusy(true);
    let wantAI = false;
    try {
      if (mode === 'text') {
        if (!text.trim()) throw new Error(t('tts.needtext'));
        const d = await ws.send<{ title?: string }>('POST', '/kb/documents/text', {
          title: title.trim(), doc_type: type, text,
          tags: tags.trim() ? tags.split(',').map(x => x.trim()) : [],
          metadata: parsedMeta,
        });
        if (!d.ok) throw new Error(d.error || t('toast.error'));
        setMsg({ text: `"${d.data?.title}" — ${t('kb.processing')}`, kind: 'ok' });
        toast(t('toast.imported'), 'ok');
      } else {
        const input = mode === 'csv' ? csvRef.current : fileRef.current;
        const files = [...(input?.files || [])];
        if (!files.length) throw new Error(t('kb.needfile'));
        wantAI = mode !== 'csv' && restructure;
        const failed: string[] = [];
        const made: string[] = [];
        for (let i = 0; i < files.length; i++) {
          setMsg({ text: t('kb.files.progress', { done: i + 1, total: files.length }), kind: '' });
          const fd = new FormData();
          fd.append('file', files[i]);
          fd.append('doc_type', type);
          fd.append('title', files.length === 1 ? title.trim() : '');
          fd.append('tags', tags.trim());
          fd.append('metadata', JSON.stringify(parsedMeta));
          if (wantAI) fd.append('restructure', '1');
          try {
            const dd = await apiUpload<{ id?: string }>(
              `/kb/documents/${mode === 'csv' ? 'csv' : 'upload'}`, fd, { scope: SCOPE });
            if (dd && dd.id) made.push(dd.id);
          } catch (err) {
            ws.funnel(err);
            failed.push(`${files[i].name} — ${apiMessage(err, t)}`);
          }
        }
        if (failed.length) {
          setMsg({
            text: `${t('kb.files.done', { n: made.length })} ${t('kb.files.failed')} ${failed.join(' · ')}`,
            kind: 'err',
          });
          toast(t('toast.error'), 'err');
        } else {
          setMsg({ text: t('kb.files.done', { n: made.length }), kind: 'ok' });
          toast(t('toast.imported'), 'ok');
        }
        if (wantAI) made.forEach(id => void pollRestr(id));
      }
      setText('');
      if (fileRef.current) fileRef.current.value = '';
      if (csvRef.current) csvRef.current.value = '';
      setRestructure(false);
      setTimeout(onImported, 1500);
      setTimeout(onImported, 4000);
    } catch (e) {
      setMsg({ text: e instanceof Error ? e.message : t('toast.error'), kind: 'err' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3>{t('kb.import')}</h3>
      <div className="subtabs" role="tablist">
        {(['file', 'text', 'csv'] as Mode[]).map(m => (
          <button
            key={m} type="button" role="tab" aria-selected={mode === m}
            className={`subtab${mode === m ? ' active' : ''}`} onClick={() => setMode(m)}
          >{t(m === 'file' ? 'imp.file' : m === 'text' ? 'imp.paste' : 'imp.csv')}</button>
        ))}
      </div>
      <div className="row">
        <div>
          <label htmlFor="i_type">{t('f.category')}</label>
          <input id="i_type" placeholder="policy" value={docType} onChange={e => setDocType(e.target.value)} />
        </div>
        <div>
          <label htmlFor="i_title">{t('f.title')}</label>
          <input id="i_title" value={title} onChange={e => setTitle(e.target.value)} />
        </div>
        <div>
          <label htmlFor="i_tags">{t('f.tags')}</label>
          <input id="i_tags" placeholder="refunds, billing" value={tags} onChange={e => setTags(e.target.value)} />
        </div>
      </div>
      <label htmlFor="i_meta">{t('kba.doc.meta')}</label>
      <input id="i_meta" placeholder='{"region":"ge"}' value={meta} onChange={e => setMeta(e.target.value)} />

      <div className={mode === 'file' ? undefined : 'hidden'}>
        <label htmlFor="i_file">{t('kb.filelabel')}</label>
        <input type="file" id="i_file" ref={fileRef} accept={ACCEPT} multiple />
        {templates}
        <label className="inline" style={{ gap: 8, marginTop: 12 }}>
          <input
            type="checkbox" style={{ width: 'auto' }} checked={restructure}
            onChange={e => setRestructure(e.target.checked)}
          />
          <span>{t('kb.restr')}</span><Tip text={t('kb.restr.hint')} />
        </label>
      </div>

      <div className={mode === 'text' ? undefined : 'hidden'}>
        <label htmlFor="i_text">{t('f.text')}</label>
        <textarea id="i_text" value={text} onChange={e => setText(e.target.value)} />
      </div>

      <div className={mode === 'csv' ? undefined : 'hidden'}>
        <label htmlFor="i_csv"><span>{t('kb.csvlabel')}</span><Tip text={t('kb.csvhint')} /></label>
        <input type="file" id="i_csv" ref={csvRef} accept=".csv" multiple />
        {templates}
      </div>

      <div className="actions">
        <button type="button" className="primary" disabled={busy} onClick={() => void run()}>
          {busy ? <><span className="spinner" />{t('btn.import')}…</> : t('btn.import')}
        </button>
      </div>
      <div className={`msg${msg.kind ? ' ' + msg.kind : ''}`} aria-live="polite">{msg.text}</div>
    </div>
  );
}
