'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { bytes as human } from '@/lib/format';
import { ApiError, apiBase, apiGet, apiMessage } from '@/lib/session';
import { StreamError, xhrStream } from '@/lib/xhrStream';
import { useI18n } from '@/lib/useI18n';
import { Dropzone } from './Dropzone';
import { conversionAllowance, type LimitsSnapshot } from './quota';

/* Audio converter — bulk transcode to the Asterisk formats, server-side.
   =====================================================================
   Open to signed-out visitors on purpose: it is useful on its own, and it is the cheapest thing
   this server runs (one ffmpeg process, no model call).

   ONE REQUEST PER BATCH, over XMLHttpRequest with `?stream=1`, because the request has two long
   phases and the person watching has to see both. Only `xhr.upload.onprogress` knows how many
   bytes have really left the browser; only the SSE frames that follow know how many files the
   server has really finished. `fetch()` reports neither and `EventSource` is GET-only so it
   cannot carry the upload. That machinery now lives in `@/lib/xhrStream` — including the one
   subtle part, which is that whether the response is a STREAM or a plain JSON refusal is decided
   ONCE, off `Content-Type`, at `readyState === 2`.

   THE BATCH STAYS A GUEST REQUEST even for a signed-in account, and that is deliberate: the
   allowance line below quotes `/convert/formats`' anonymous constant, and a signed batch expires
   on the storage retention rule instead. The account page's own Convert tab reads the batch's
   `expires_at` and is therefore the one that signs the request.

   The batch comes back as one ZIP: forty files otherwise means forty save dialogs, and the
   archive is the only place a per-file name survives intact — a bare .alaw or .sln handed to a
   browser gets renamed or reinterpreted on its way to disk. */

interface Format { id: string; label?: string; description?: string }
interface Limits {
  max_files?: number;
  max_file_bytes?: number;
  max_batch_bytes?: number;
  download_ttl_seconds?: number;
}
interface FormatsResponse {
  formats?: Format[];
  default?: string;
  limits?: Limits;
  available?: boolean;
}

type RowState = 'queued' | 'converting' | 'done' | 'failed';
interface Row {
  file: File;
  state: RowState;
  error: string | null;
  output: string | null;
  bytes: number | null;
}

/** One per-file frame from the server, and one entry of the terminal summary's `files[]`. */
interface FileFrame {
  index?: number;
  ok?: boolean;
  error?: string;
  output?: string;
  bytes?: number | null;
  total?: number;
}
interface DoneFrame {
  files?: FileFrame[];
  total?: number;
  converted?: number;
  failed?: number;
  token?: string | null;
  download_path?: string | null;
  quota_refusal?: string | null;
}

interface Verdict { total: number; ok: number; fail: number }

const ST_LABEL: Record<RowState, [key: string, pill: string]> = {
  queued: ['cv.st.queued', 'pending'],
  converting: ['cv.st.converting', 'processing'],
  done: ['cv.st.done', 'done'],
  failed: ['cv.st.failed', 'error'],
};

/** The server is given the files in order and reports one frame per finished file, so the next
    still-queued row is the one being worked on. Nothing stronger is claimed: a row only reads
    Done or Failed once its own frame has arrived. */
function markNext(rows: Row[]): Row[] {
  const i = rows.findIndex(r => r.state === 'queued');
  if (i < 0) return rows;
  const out = rows.slice();
  out[i] = { ...out[i], state: 'converting' };
  return out;
}

function applyFrame(rows: Row[], d: FileFrame, fallback: string): Row[] {
  const i = Number(d.index);
  if (!Number.isInteger(i) || i < 0 || i >= rows.length) return rows;
  const out = rows.slice();
  out[i] = {
    ...out[i],
    state: d.ok ? 'done' : 'failed',
    error: d.ok ? null : (d.error || fallback),
    output: d.output || null,
    bytes: d.bytes == null ? null : d.bytes,
  };
  return out;
}

/** A row the server never reported on was never converted. Queued, not done. */
const unwind = (rows: Row[]): Row[] =>
  rows.map(r => (r.state === 'converting' ? { ...r, state: 'queued' as const } : r));

export function ConvertPanel({ limits, onSpent, onUnavailable }: {
  /** The latest `GET /limits`, for the per-file allowance line under the button. */
  limits: LimitsSnapshot | null;
  onSpent: () => void;
  /** The server has no converter: the tab and the panel go away entirely. */
  onUnavailable: () => void;
}) {
  const { t } = useI18n();

  const [formats, setFormats] = useState<Format[]>([]);
  const [format, setFormat] = useState('');
  const [caps, setCaps] = useState<Limits>({});
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [dl, setDl] = useState('');
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [prog, setProg] = useState<{ label: string; pct: number | null } | null>(null);

  const abortRef = useRef<(() => void) | null>(null);
  const finRef = useRef(0);
  const totalRef = useRef(0);
  const loaded = useRef(false);

  /* ---- the catalog ---- */
  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    void (async () => {
      let d: FormatsResponse;
      try {
        d = await apiGet<FormatsResponse>('/convert/formats', { scope: 'public' });
      } catch (e) {
        /* A definite "not here" — a server that predates the converter, or one with no ffmpeg —
           takes the tab away, because a tab that only ever apologises teaches a visitor that the
           site is broken. A 200 whose body is not the catalog is the same answer. A transient
           5xx leaves the tab up with the error showing: the whole API is probably down and a
           reload is the fix. */
        if (e instanceof ApiError && (e.status === 404 || e.i18nKey === 'err.badresp')) onUnavailable();
        else setErr(t('err.unavailable'));
        return;
      }
      if (d.available === false || !Array.isArray(d.formats)) { onUnavailable(); return; }
      const list = d.formats.filter(f => f && f.id);
      if (!list.length) { onUnavailable(); return; }
      setFormats(list);
      setCaps(d.limits || {});
      setFormat(list.some(f => f.id === d.default) ? (d.default as string) : list[0].id);
    })();
  }, [t, onUnavailable]);

  /* The catalog says WHICH formats exist; the wording is ours, because "alaw" means nothing to
     the person choosing it. A format id we have no key for falls back to the server's own
     English, so a codec added server-side shows up rather than disappearing. */
  const label = (f: Format) => { const k = `cv.f.${f.id}`; const tr = t(k); return tr === k ? (f.label || f.id) : tr; };
  const describe = (f: Format) => { const k = `cv.f.${f.id}.d`; const tr = t(k); return tr === k ? (f.description || '') : tr; };
  const selected = formats.find(f => f.id === format) || null;

  /* ---- the queue ---- */

  /** Any change to the list invalidates the ZIP built from the previous one, so the download
      goes away with it rather than quietly handing over yesterday's archive. */
  const clearResult = useCallback(() => {
    setDl('');
    setVerdict(null);
    setRows(prev => prev.map(r => ({ ...r, state: 'queued' as const, error: null, output: null, bytes: null })));
  }, []);

  /* Computed from `rows` directly rather than inside a `setRows` updater: the refusal message is
     a second output of the same pass, and a React updater has to be pure — under
     `reactStrictMode` it is called twice, which would fire the "too many files" line for a batch
     that was accepted. This runs from an event handler, where the closed-over `rows` is current. */
  const add = (picked: File[]) => {
    if (busy) return;                          // indices are in flight — do not move them
    const maxFiles = Number(caps.max_files) || 0;
    const maxOne = Number(caps.max_file_bytes) || 0;
    const next = rows.slice();
    let refused = '';
    for (const f of picked) {
      if (!f) continue;
      /* A dropped FOLDER arrives as a zero-byte entry with no type, and so does an empty file.
         Neither has any audio in it; refusing both here beats an upload that dies halfway with a
         browser-specific read error. */
      if (!f.size && !f.type) continue;
      if (next.some(x => x.file.name === f.name && x.file.size === f.size && x.file.lastModified === f.lastModified)) continue;
      if (maxOne && f.size > maxOne) { refused = t('cv.toobig', { name: f.name, max: human(maxOne) }); continue; }
      if (maxFiles && next.length >= maxFiles) { refused = t('cv.toomany', { max: maxFiles }); continue; }
      next.push({ file: f, state: 'queued', error: null, output: null, bytes: null });
    }
    // Adding to the list invalidates the previous batch's ZIP, so both the rows and the result
    // are replaced in one pass — every row queued again, no stale download offered.
    setRows(next.map(r => ({ ...r, state: 'queued' as const, error: null, output: null, bytes: null })));
    setDl('');
    setVerdict(null);
    setErr(refused);
  };

  /* ---- the run ---- */

  const summary = (v: Verdict): string =>
    v.ok === 0 ? t('cv.done.none')
      : v.fail === 0 ? t('cv.done.all', { n: v.ok })
        : t('cv.done.some', { ok: v.ok, total: v.total, fail: v.fail });

  const finish = (d: DoneFrame, note: string) => {
    const files = Array.isArray(d.files) ? d.files : [];
    setRows(prev => {
      let next = prev;
      for (const f of files) next = applyFrame(next, f, t('cv.fail'));
      return unwind(next);
    });

    const total = Number(d.total) || rows.length;
    const okN = Number(d.converted) || 0;
    const bad = d.failed != null ? Number(d.failed) : Math.max(0, total - okN);

    const path = d.download_path;
    if (d.token && typeof path === 'string' && path.indexOf('/convert/') === 0) setDl(apiBase() + path);

    const v: Verdict = { total, ok: okN, fail: bad };
    setVerdict(v);
    /* A quota refusal is the server saying the daily allowance ran out part-way through. The
       files before it really did convert, so it is a note beside a real result — not a reason to
       throw the batch away. */
    const why = note || d.quota_refusal || '';
    if (why) setErr(why);
    if (okN > 0) toast(summary(v), bad ? 'info' : 'ok');
    onSpent();
  };

  const run = async () => {
    setErr('');
    if (busy) return;
    if (!rows.length) { setErr(t('cv.nofiles')); return; }
    /* Checked here as well as on the server: a visitor should learn that 200 MB is too much
       before spending ten minutes uploading it, not after. */
    const size = rows.reduce((a, x) => a + (x.file.size || 0), 0);
    if (caps.max_files && rows.length > caps.max_files) { setErr(t('cv.toomany', { max: caps.max_files })); return; }
    if (caps.max_batch_bytes && size > caps.max_batch_bytes) {
      setErr(t('cv.batchtoobig', { max: human(caps.max_batch_bytes) })); return;
    }

    clearResult();
    finRef.current = 0;
    totalRef.current = rows.length;

    const fd = new FormData();
    for (const r of rows) fd.append('files', r.file, r.file.name);
    fd.append('format', format || '');

    setBusy(true);
    setProg({ label: t('cv.stage.upload'), pct: 0 });

    const stream = xhrStream<DoneFrame>({
      url: `${apiBase()}/convert?stream=1`,
      body: fd,
      // No credential, on purpose — see the header of this file.
      headers: {},
      terminal: ['done'],
      onUploadProgress: pct => setProg({ label: t('cv.stage.upload'), pct }),
      onUploadEnd: () => setProg({ label: t('cv.stage.queued'), pct: null }),
      onEvent: (name, data) => {
        const d = data as FileFrame;
        totalRef.current = Number(d.total) || totalRef.current;
        if (name === 'stage') {
          setRows(markNext);
          setProg({ label: t('cv.stage.converting', { done: 0, total: totalRef.current }), pct: 0 });
        } else if (name === 'progress') {
          finRef.current += 1;
          setRows(prev => markNext(applyFrame(prev, d, t('cv.fail'))));
          setProg({
            label: t('cv.stage.converting', { done: finRef.current, total: totalRef.current }),
            pct: totalRef.current ? (finRef.current / totalRef.current) * 100 : null,
          });
        }
      },
    });
    abortRef.current = stream.abort;

    try {
      const { data, note } = await stream.result;
      finish(data, note);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        /* Whatever finished before the abort keeps its verdict; the one in flight goes back to
           queued, because we no longer have any idea what happened to it. A cancel is a quiet
           line, never a toast — the visitor asked for it. */
        setRows(unwind);
        setErr(t('cv.cancelled'));
      } else {
        const msg = e instanceof StreamError
          ? (e.code === 'network' ? t('sc.import.netfail')
            : e.code === 'truncated' ? t('cv.fail')
              : e.code === 'server' ? (e.detail || t('cv.fail'))
                : apiMessage(e, t))
          : apiMessage(e, t);
        setErr(msg);
        toast(msg, 'err');
      }
      onSpent();
    } finally {
      abortRef.current = null;
      setBusy(false);
      /* One exit path for every ending: the bar goes away and the buttons come back. Unmounting
         it is also what the legacy version was reaching for when it wound the fill back to 0%
         while the bar was `display:none` — the next batch must not open with a 300ms animation
         of the last one draining away. */
      setProg(null);
    }
  };

  /* Leaving the page: drop the request so the server sees the disconnect instead of finishing a
     ZIP nobody can claim any more. */
  useEffect(() => {
    const bail = () => abortRef.current?.();
    window.addEventListener('pagehide', bail);
    return () => { window.removeEventListener('pagehide', bail); bail(); };
  }, []);

  const allowance = conversionAllowance(limits);
  const ttlHours = dl ? Math.max(1, Math.round((Number(caps.download_ttl_seconds) || 0) / 3600)) : 0;

  return (
    <div className="card">
      <h3>{t('cv.heading')}</h3>

      <label htmlFor="cvFiles">{t('cv.files')}</label>
      <Dropzone id="cvFiles" title={t('cv.drop.title')} sub={t('cv.drop.sub')} multiple onPick={add} />

      <div className="row" style={{ marginTop: 14 }}>
        <div>
          {/* The ⓘ carries the selected codec's description. It stays hidden while there is no
              format to describe, so it never appears as a dead circle while /convert/formats is
              still in flight. */}
          <label htmlFor="cvFormat">
            <span>{t('cv.format')}</span>
            <Tip text={selected ? describe(selected) : ''} />
          </label>
          <Select
            id="cvFormat"
            value={format}
            onChange={setFormat}
            options={formats.map(f => ({ value: f.id, label: label(f) }))}
            ariaLabel={t('cv.format')}
          />
        </div>
      </div>

      {rows.length ? (
        <div className="table-wrap" style={{ marginTop: 14 }}>
          <table>
            <thead>
              <tr><th>{t('th.file')}</th><th>{t('th.size')}</th><th>{t('th.status')}</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const [key, pill] = ST_LABEL[r.state];
                return (
                  <tr key={`${r.file.name}:${r.file.size}:${r.file.lastModified}:${i}`}>
                    <td className="cv-file">
                      {r.file.name}
                      {/* One row must be able to say WHY it failed while thirty-nine others say
                          Done — that is the whole point of listing them instead of one verdict. */}
                      {r.error ? <div className="cv-why">{r.error}</div> : null}
                      {!r.error && r.state === 'done' && r.output
                        ? <div className="cv-out">→ {r.output}{r.bytes == null ? '' : ` · ${human(r.bytes)}`}</div>
                        : null}
                    </td>
                    <td className="cv-size">{human(r.file.size)}</td>
                    <td><span className={`pill ${pill}`}>{t(key)}</span></td>
                    <td className="inline">
                      {busy ? null : (
                        <button
                          className="act danger" type="button"
                          title={t('btn.remove')} aria-label={t('btn.remove')}
                          onClick={() => { setRows(prev => prev.filter((_, j) => j !== i)); clearResult(); }}
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

      {/* Both phases of this bar have a real denominator: bytes that have left the browser, then
          files the server says it has finished. Where neither exists — uploaded, server not
          started yet — it goes indeterminate rather than inventing a number. */}
      {prog ? (
        <div className="imp-prog">
          <div className="imp-prog-head">
            <span id="cvProgLabel" aria-live="polite">{prog.label}</span>
            <span className="imp-prog-pct" aria-hidden="true">
              {prog.pct === null ? '' : `${Math.max(0, Math.min(100, Math.round(prog.pct)))}%`}
            </span>
          </div>
          <div
            className={prog.pct === null ? 'sc-bar indet' : 'sc-bar'}
            role="progressbar" aria-labelledby="cvProgLabel"
            aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={prog.pct === null ? undefined : Math.max(0, Math.min(100, Math.round(prog.pct)))}
          >
            <span style={prog.pct === null ? undefined : { width: `${Math.max(0, Math.min(100, Math.round(prog.pct)))}%` }} />
          </div>
        </div>
      ) : null}

      <div className="actions">
        <button
          className="primary" type="button"
          disabled={busy || !rows.length || !formats.length}
          onClick={() => void run()}
        >
          {t('cv.run')}
        </button>
        {busy ? (
          <button className="ghost" type="button" onClick={() => abortRef.current?.()}>{t('btn.cancel')}</button>
        ) : null}
        {!busy && dl ? <a className="btn-ghost" href={dl} download="">{t('cv.download')}</a> : null}
        {!busy && rows.length ? (
          <button
            className="ghost" type="button"
            onClick={() => { setRows([]); setDl(''); setVerdict(null); setErr(''); }}
          >
            {t('cv.clear')}
          </button>
        ) : null}
      </div>

      {/* The verdict is a sentence assembled from the DICT, so what is REMEMBERED is the counts
          rather than the finished sentence — otherwise switching language after a batch leaves
          the one line that says how it went stranded in the old one. `err` below deliberately is
          not re-derived: it carries the server's own words, which we cannot translate after the
          fact and must not silently drop either. */}
      <div className={`msg${verdict ? (verdict.ok === 0 ? ' err' : verdict.fail === 0 ? ' ok' : '') : ''}`}>
        {verdict ? summary(verdict) : ''}
      </div>
      <div className="err">{err}</div>
      <div className="hint">{ttlHours ? t('cv.ttl', { n: ttlHours }) : ''}</div>
      {/* The guest allowance, stated next to the button that spends it, and counted per FILE
          rather than per batch — otherwise a visitor learns that detail from a refusal halfway
          through thirty files. Nothing is claimed when the server sends no cap: an absent number
          is neither zero nor infinity, it is unknown. */}
      <div className="hint">
        {allowance ? (
          <>
            {t('cv.anon', { max: allowance.max, left: allowance.left })}{' '}
            <a href="/tenant.html">{t('nav.signin')}</a> {t('cv.anon.more')}
          </>
        ) : null}
      </div>
    </div>
  );
}
