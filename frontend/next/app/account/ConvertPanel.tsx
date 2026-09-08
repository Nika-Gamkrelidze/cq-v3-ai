'use client';
/* The audio converter, signed in.
   ==============================
   The public page's converter with the account's credential on it: the batch is kept for the
   account's retention window instead of the anonymous two hours, and the ZIP is fetched with
   the session header because the download route is scope-checked.

   ONE REQUEST PER BATCH, over XMLHttpRequest, with `?stream=1`. The reason is the same as on
   the public page: the request has two long phases — the upload, then the ffmpeg work — and
   only `xhr.upload.onprogress` knows how many bytes have really left the browser while only
   the SSE frames know how many files the server has really finished. `fetch` can report
   neither and `EventSource` cannot carry a file. `lib/xhrStream.ts` owns that transport,
   including the part that decides ONCE, off the Content-Type at `readyState === 2`, whether
   the answer is a stream or a plain JSON refusal. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { bytes as human } from '@/lib/format';
import { ApiError, apiBase, apiGet, apiMessage, downloadAuthed, scopedHeaders } from '@/lib/session';
import type { SseData } from '@/lib/sse';
import { useI18n } from '@/lib/useI18n';
import { xhrStream } from '@/lib/xhrStream';
import { expiryLabel } from './expiry';

interface Format { id: string; label?: string; description?: string }
interface FormatsResponse {
  formats?: Format[];
  default?: string;
  limits?: { max_files?: number; max_file_bytes?: number; max_batch_bytes?: number };
  available?: boolean;
}

type ItemState = 'queued' | 'converting' | 'done' | 'failed';

interface Item {
  file: File;
  state: ItemState;
  error: string | null;
  output: string | null;
  bytes: number | null;
}

/** One converted file, as a `progress` or `done` frame reports it. */
interface FileResult { index?: number; ok?: boolean; error?: string; output?: string; bytes?: number | null }

interface DoneData {
  files?: FileResult[];
  total?: number;
  converted?: number;
  failed?: number;
  token?: string;
  download_path?: string;
  expires_at?: string | null;
  quota_refusal?: string;
}

const ST: Record<ItemState, [string, string]> = {
  queued: ['cv.st.queued', 'pending'],
  converting: ['cv.st.converting', 'processing'],
  done: ['cv.st.done', 'done'],
  failed: ['cv.st.failed', 'error'],
};

export interface ConvertPanelProps {
  onUnauthorized: () => void;
  /** A batch spends conversion units: re-read `/limits` when one ends, however it ended. */
  onSpend: () => void;
  /** A finished batch is a new History row. */
  onConverted: () => void;
}

export function ConvertPanel({ onUnauthorized, onSpend, onConverted }: ConvertPanelProps) {
  const { t } = useI18n();

  const [formats, setFormats] = useState<Format[]>([]);
  const [limits, setLimits] = useState<NonNullable<FormatsResponse['limits']>>({});
  const [format, setFormat] = useState('');
  const [queue, setQueue] = useState<Item[]>([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ label: string; pct: number | null } | null>(null);
  const [result, setResult] = useState<{ total: number; ok: number; fail: number } | null>(null);
  const [download, setDownload] = useState<{ path: string; expires: string | null } | null>(null);
  const [drag, setDrag] = useState(false);

  const abortRef = useRef<(() => void) | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const loaded = useRef(false);

  /* ---- the catalogue ----
     Public, and the LIMITS ride along with it deliberately: a UI that validates against its
     own hardcoded numbers drifts, and then rejects a batch the server would have accepted. */
  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    void (async () => {
      let d: FormatsResponse;
      try {
        d = await apiGet<FormatsResponse>('/convert/formats', { scope: 'public' });
      } catch (e) {
        setErr(e instanceof ApiError && e.status === 404 ? t('cv.unavailable') : t('err.unavailable'));
        return;
      }
      if (!d || d.available === false || !Array.isArray(d.formats) || !d.formats.length) {
        setErr(t('cv.unavailable'));
        return;
      }
      const rows = d.formats.filter(f => f && f.id);
      setFormats(rows);
      setLimits(d.limits || {});
      setFormat(rows.some(f => f.id === d.default) ? (d.default as string) : rows[0].id);
    })();
  }, [t]);

  const label = useCallback((f: Format) => {
    const k = `cv.f.${f.id}`;
    const trn = t(k);
    return trn === k ? (f.label || f.id) : trn;
  }, [t]);

  const describe = useCallback((f: Format) => {
    const k = `cv.f.${f.id}.d`;
    const trn = t(k);
    return trn === k ? (f.description || '') : trn;
  }, [t]);

  const options = useMemo(() => formats.map(f => ({ value: f.id, label: label(f) })), [formats, label]);
  const note = useMemo(() => {
    const f = formats.find(x => x.id === format);
    return f ? describe(f) : '';
  }, [formats, format, describe]);

  /* Any change to the list invalidates the ZIP built from the previous one, so the download
     goes away with it rather than quietly handing over the last batch. */
  const clearResult = useCallback(() => {
    setDownload(null);
    setResult(null);
    setQueue(prev => prev.map(it => ({ ...it, state: 'queued', error: null, output: null, bytes: null })));
  }, []);

  /* Computed OUTSIDE the state updater, deliberately: `refused` is a side effect, and React
     invokes an updater twice in development to surface exactly this. `queue` is current here
     because every caller is an event handler. */
  function add(list: FileList | File[] | null) {
    if (busy) return;
    setErr('');
    const maxFiles = Number(limits.max_files) || 0;
    const maxOne = Number(limits.max_file_bytes) || 0;
    let refused = '';
    const next = queue.slice();
    for (const f of Array.prototype.slice.call(list || []) as File[]) {
      if (!f) continue;
      // A dropped FOLDER arrives as a zero-byte entry with no type, and so does an empty
      // file. Neither has audio in it.
      if (!f.size && !f.type) continue;
      if (next.some(x => x.file.name === f.name && x.file.size === f.size && x.file.lastModified === f.lastModified)) continue;
      if (maxOne && f.size > maxOne) { refused = t('cv.toobig', { name: f.name, max: human(maxOne) }); continue; }
      if (maxFiles && next.length >= maxFiles) { refused = t('cv.toomany', { max: maxFiles }); continue; }
      next.push({ file: f, state: 'queued', error: null, output: null, bytes: null });
    }
    // Any change to the list invalidates the ZIP built from the previous one, so every row
    // goes back to `queued` and the download goes away with it.
    setQueue(next.map(it => ({ ...it, state: 'queued' as ItemState, error: null, output: null, bytes: null })));
    setDownload(null);
    setResult(null);
    if (refused) setErr(refused);
  }

  /* ---- the run ---- */

  const applyRow = (rows: Item[], d: FileResult): Item[] => {
    const i = Number(d.index);
    if (!rows[i]) return rows;
    const out = rows.slice();
    out[i] = {
      ...out[i],
      state: d.ok ? 'done' : 'failed',
      error: d.ok ? null : (d.error || t('cv.fail')),
      output: d.output || null,
      bytes: d.bytes == null ? null : d.bytes,
    };
    return out;
  };

  const markNext = (rows: Item[]): Item[] => {
    const i = rows.findIndex(x => x.state === 'queued');
    if (i < 0) return rows;
    const out = rows.slice();
    out[i] = { ...out[i], state: 'converting' };
    return out;
  };

  function run() {
    setErr('');
    if (abortRef.current) return;
    if (!queue.length) { setErr(t('cv.nofiles')); return; }
    const total = queue.reduce((a, x) => a + (x.file.size || 0), 0);
    if (limits.max_files && queue.length > limits.max_files) { setErr(t('cv.toomany', { max: limits.max_files })); return; }
    if (limits.max_batch_bytes && total > limits.max_batch_bytes) { setErr(t('cv.batchtoobig', { max: human(limits.max_batch_bytes) })); return; }

    clearResult();
    setBusy(true);
    setProgress({ label: t('cv.stage.upload'), pct: 0 });

    const fd = new FormData();
    for (const it of queue) fd.append('files', it.file, it.file.name);
    fd.append('format', format);

    // How many files the server says are in the batch, and how many it has finished. Refs, not
    // state: the SSE handlers run several times per second and only the label reads them.
    const seen = { total: queue.length, fin: 0, started: false };

    const stream = xhrStream<DoneData>({
      url: `${apiBase()}/convert?stream=1`,
      body: fd,
      headers: scopedHeaders('user'),
      terminal: ['done'],
      onUploadProgress: pct => setProgress({ label: t('cv.stage.upload'), pct }),
      onUploadEnd: () => setProgress({ label: t('cv.stage.queued'), pct: null }),
      onEvent: (name, d: SseData) => {
        if (name === 'stage') {
          seen.started = true;
          seen.total = Number(d.total) || seen.total;
          setQueue(markNext);
          setProgress({ label: t('cv.stage.converting', { done: 0, total: seen.total }), pct: 0 });
        } else if (name === 'progress') {
          seen.total = Number(d.total) || seen.total;
          seen.fin += 1;
          setQueue(rows => markNext(applyRow(rows, d as FileResult)));
          setProgress({
            label: t('cv.stage.converting', { done: seen.fin, total: seen.total }),
            pct: seen.total ? (seen.fin / seen.total) * 100 : null,
          });
        }
      },
    });
    abortRef.current = stream.abort;

    const finish = () => { abortRef.current = null; setBusy(false); setProgress(null); };

    void stream.result.then(({ data, note: detail }) => {
      finish();
      const files = Array.isArray(data.files) ? data.files : [];
      setQueue(rows => {
        let out = rows;
        for (const f of files) out = applyRow(out, f);
        // Whatever was mid-flight goes back to queued: with the run over, "converting" is a
        // state nothing will ever move it out of.
        return out.map(it => (it.state === 'converting' ? { ...it, state: 'queued' } : it));
      });
      const totalN = Number(data.total) || queue.length;
      const okN = Number(data.converted) || 0;
      const bad = data.failed != null ? Number(data.failed) : Math.max(0, totalN - okN);
      const path = data.download_path;
      if (data.token && typeof path === 'string' && path.indexOf('/convert/') === 0) {
        setDownload({ path, expires: data.expires_at || null });
      }
      setResult({ total: totalN, ok: okN, fail: bad });
      // A quota refusal is the server saying the allowance ran out part-way through. The files
      // before it really did convert, so it is a note beside a real result.
      const why = detail || data.quota_refusal || '';
      if (why) setErr(why);
      if (okN > 0) toast(summarise({ total: totalN, ok: okN, fail: bad }), bad ? 'info' : 'ok');
      onSpend();
      onConverted();
    }).catch((e: unknown) => {
      finish();
      if (e instanceof DOMException && e.name === 'AbortError') {
        // Whatever finished before the abort keeps its verdict; the one in flight goes back to
        // queued, because we no longer have any idea what happened to it.
        setQueue(rows => rows.map(it => (it.state === 'converting' ? { ...it, state: 'queued' } : it)));
        setErr(t('cv.cancelled'));
        onSpend();
        return;
      }
      if (e instanceof ApiError && e.status === 401) { onUnauthorized(); return; }
      // `status === 0` is "nothing answered", which is the network failure the legacy page
      // names with the import helper's wording rather than the generic converter one.
      const msg = e instanceof ApiError && e.status === 0 ? t('sc.import.netfail') : apiMessage(e, t);
      setErr(msg);
      toast(msg, 'err');
      onSpend();
    });
  }

  const summarise = (r: { total: number; ok: number; fail: number }) =>
    r.ok === 0 ? t('cv.done.none')
      : r.fail === 0 ? t('cv.done.all', { n: r.ok })
        : t('cv.done.some', { ok: r.ok, total: r.total, fail: r.fail });

  // Leaving the page: drop the request so the server sees the disconnect instead of finishing
  // a ZIP nobody can claim any more. Also covers a client-side navigation away from /account.
  useEffect(() => {
    const drop = () => abortRef.current?.();
    window.addEventListener('pagehide', drop);
    return () => { window.removeEventListener('pagehide', drop); drop(); };
  }, []);

  /* ---- render ---- */

  const ttl = download ? expiryLabel(download.expires, t) : '';

  return (
    <div className="card">
      <h3>{t('cv.heading')}</h3>
      <label>{t('cv.files')}</label>
      <div
        className={`drop${drag ? ' drag' : ''}`}
        onDragOver={e => { e.preventDefault(); setDrag(true); }}
        onDragEnter={e => { e.preventDefault(); setDrag(true); }}
        onDragLeave={e => { e.preventDefault(); setDrag(false); }}
        onDrop={e => { e.preventDefault(); setDrag(false); if (e.dataTransfer) add(e.dataTransfer.files); }}
      >
        <input
          type="file" multiple ref={fileInput}
          accept="audio/*,video/*,.m4a,.aac,.flac,.opus,.wma,.amr,.aiff,.oga,.3gp"
          aria-label={t('cv.files')}
          onChange={e => {
            const picked = Array.prototype.slice.call(e.target.files || []) as File[];
            e.target.value = '';                  // so picking the same file again still fires
            add(picked);
          }}
        />
        <div className="drop-title">{t('cv.drop.title')}</div>
        <div className="drop-sub">{t('cv.drop.sub')}</div>
      </div>

      <div className="row" style={{ marginTop: 14 }}>
        <div>
          <label htmlFor="cvFormat">
            <span>{t('cv.format')}</span>
            {/* Hidden until there is a format to describe — `Tip` renders nothing for an
                empty sentence, which is what the legacy empty `data-tip` achieved. */}
            <Tip text={note} />
          </label>
          <Select id="cvFormat" value={format} options={options} ariaLabel={t('cv.format')} onChange={setFormat} />
        </div>
      </div>

      {queue.length ? (
        <div className="table-wrap" style={{ marginTop: 14 }}>
          <table>
            <thead>
              <tr>
                <th>{t('th.file')}</th><th>{t('th.size')}</th><th>{t('th.status')}</th><th />
              </tr>
            </thead>
            <tbody>
              {queue.map((it, i) => {
                const s = ST[it.state] || ST.queued;
                return (
                  <tr key={`${it.file.name}:${it.file.size}:${it.file.lastModified}:${i}`}>
                    <td className="cv-file">
                      {it.file.name}
                      {it.error ? <div className="cv-why">{it.error}</div> : null}
                      {!it.error && it.state === 'done' && it.output ? (
                        <div className="cv-out">→ {it.output}{it.bytes == null ? '' : ` · ${human(it.bytes)}`}</div>
                      ) : null}
                    </td>
                    <td className="cv-size">{human(it.file.size)}</td>
                    <td><span className={`pill ${s[1]}`}>{t(s[0])}</span></td>
                    <td className="inline">
                      {busy ? null : (
                        <button
                          type="button" className="act danger"
                          title={t('btn.remove')} aria-label={t('btn.remove')}
                          onClick={() => { setQueue(prev => prev.filter((_, j) => j !== i)); clearResult(); }}
                        >
                          ✕
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {progress ? (
        <div className="imp-prog">
          <div className="imp-prog-head">
            <span aria-live="polite">{progress.label}</span>
            <span className="imp-prog-pct" aria-hidden="true">
              {progress.pct === null ? '' : `${Math.max(0, Math.min(100, Math.round(progress.pct)))}%`}
            </span>
          </div>
          <div
            className={`sc-bar${progress.pct === null ? ' indet' : ''}`}
            role="progressbar" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={progress.pct === null ? undefined : Math.max(0, Math.min(100, Math.round(progress.pct)))}
            aria-label={progress.label}
          >
            <span style={{ width: progress.pct === null ? undefined : `${Math.max(0, Math.min(100, Math.round(progress.pct)))}%` }} />
          </div>
        </div>
      ) : null}

      <div className="actions">
        <button
          type="button" className="primary"
          disabled={busy || !queue.length || !formats.length}
          onClick={run}
        >
          {t('cv.run')}
        </button>
        {busy ? (
          <button type="button" className="ghost" onClick={() => abortRef.current?.()}>{t('btn.cancel')}</button>
        ) : null}
        {!busy && download ? (
          <button
            type="button" className="ghost"
            onClick={() => {
              void downloadAuthed(download.path, 'converted.zip', { scope: 'user' })
                .catch((e: unknown) => {
                  if (e instanceof ApiError && e.status === 401) { onUnauthorized(); return; }
                  toast(apiMessage(e, t), 'err');
                });
            }}
          >
            {t('cv.download')}
          </button>
        ) : null}
        {!busy && queue.length ? (
          <button
            type="button" className="ghost"
            onClick={() => { setQueue([]); setDownload(null); setResult(null); setErr(''); }}
          >
            {t('cv.clear')}
          </button>
        ) : null}
      </div>

      <div className={`msg${result ? (result.ok === 0 ? ' err' : result.fail === 0 ? ' ok' : '') : ''}`} aria-live="polite">
        {result ? summarise(result) : ''}
      </div>
      <div className="err">{err}</div>
      {/* A batch's remaining life is read from its OWN expires_at, never from the format
          catalogue's TTL constant — that number is the anonymous two hours and would be a lie
          on an account whose batches live for days. */}
      <div className="hint">{ttl ? t('ac.cv.expires', { when: ttl }) : ''}</div>
    </div>
  );
}
