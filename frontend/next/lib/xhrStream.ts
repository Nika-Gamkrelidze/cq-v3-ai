/* One upload, two long phases, one request.
   ========================================
   The audio converter and the rubric importer both POST a file and then wait minutes for the
   server to work on it, and the person watching has to see BOTH halves: real uploaded bytes
   first, then real server progress. Nothing else can do that in one request —

     * `fetch()` reports no upload progress at all (request streams are HTTP/2-only, one
       engine, and not usable for a plain FormData POST),
     * `EventSource` is GET-only, so it cannot carry the file,

   which leaves `XMLHttpRequest`: `upload.onprogress` knows how many bytes have really left
   the browser, and `onprogress` exposes the response body as it grows, so the server's SSE
   frames can be read while they arrive. Two requests instead would invent the join between
   them and could hand the second one to a different principal.

   Nothing here touches React or the DOM. It reports numbers and event names; the page decides
   what a bar, a toast or a table row does with them. */

import { ApiError, keyForStatus } from './session';
import { newSseState, sseFrames, type SseData } from './sse';

export type StreamFailure =
  /** the request never completed — no HTTP status exists (offline, DNS, connection reset) */
  | 'network'
  /** the server answered, and refused. `status` is real; `detail` may carry its own words */
  | 'http'
  /** a 2xx whose body is not JSON at all — an nginx page, or nothing */
  | 'badresp'
  /** an SSE `error` frame — the run started and gave up. `detail` is the server's reason */
  | 'server'
  /** the stream ended without a terminal frame: the connection died mid-run */
  | 'truncated';

/** A failure of a streamed upload — an `ApiError` that also says WHICH of the five it is.

    It extends `ApiError` rather than standing beside it so that a page catching from this
    module and a page catching from `session.ts` render the same failure identically: both go
    through `apiMessage`, which prefers the server's own `detail` and falls back to an i18n KEY.
    An earlier version carried a bare status and an empty `message`, which meant `apiMessage`
    fell past it to `err.unavailable` — so a 413 from nginx, the single most common refusal on
    these routes, lost the "that file is too large" wording that `brand.js` has always shown.

    `code` is the part `ApiError` cannot express: a page that wants its own sentence for a
    half-finished batch (`truncated`) versus a flat refusal (`http`) branches on this, and one
    that does not just renders the message. */
export class StreamError extends ApiError {
  readonly code: StreamFailure;
  constructor(init: {
    code: StreamFailure;
    /** 0 when no response arrived, matching `ApiError`'s convention. */
    status?: number;
    /** The server's own words, when it sent any. Rendered verbatim, never translated. */
    detail?: string;
    i18nKey: string;
    vars?: Record<string, string | number>;
  }) {
    super({
      status: init.status || 0,
      detail: init.detail || null,
      i18nKey: init.i18nKey,
      vars: init.vars,
    });
    this.name = 'StreamError';
    this.code = init.code;
  }
}

export interface StreamResult<T> {
  data: T;
  /** A note beside a real result. Non-empty only for the success-with-a-note case below. */
  note: string;
}

export interface XhrStreamOptions {
  /** Absolute path including `?stream=1`; the caller owns the API base. */
  url: string;
  body: FormData;
  /** Sent verbatim. NOT derived from the session in here on purpose: the public converter is
      deliberately a guest request even for a signed-in visitor, so who signs a batch is the
      page's decision to make, not this module's. */
  headers?: Record<string, string>;
  /** Event names that end the stream WITH a result — `done` for the converter, `draft` for
      the rubric import. Everything else is progress. */
  terminal: readonly string[];
  /** `null` means the browser could not size the body: draw the indeterminate bar and show no
      figure rather than a number nobody computed. */
  onUploadProgress?: (pct: number | null) => void;
  /** The bytes are gone and the server has not spoken yet — the "queued" gap. */
  onUploadEnd?: () => void;
  /** Every non-terminal frame, in arrival order. */
  onEvent?: (name: string, data: SseData) => void;
}

export interface XhrStream<T> {
  /** Rejects with a `StreamError`, or — for `abort()` — with a `DOMException` named
      `AbortError`, exactly as `session.ts`'s `request()` does. See the note on `abort`. */
  result: Promise<StreamResult<T>>;
  /** Cancel the request. Whatever finished before the abort really did finish; the page
      decides what to do with the partial queue (`index.html` puts the in-flight file back to
      `queued`, because it no longer knows what happened to it). */
  abort(): void;
}

export function xhrStream<T>(o: XhrStreamOptions): XhrStream<T> {
  const xhr = new XMLHttpRequest();
  const state = newSseState();
  let seen = 0;                 // how much of responseText has already been fed to the parser
  let settled = false;
  let serverSpoke = false;      // the server owns the bar from its first frame onwards

  /* Decided ONCE, off the headers, and this is the subtle part of the whole file. A refusal
     is not a stream: 400 unknown format, 413 too many files or too big, 503 no ffmpeg, 429
     out of quota and 401 expired session all come back as a real HTTP status with an ordinary
     JSON body. So does a server built before `?stream=1` existed — FastAPI ignores the unknown
     query param and answers normally. Sniffing per-tick would flip mid-response; sniffing the
     body would need bytes that a refusal never sends. Content-Type is the tell. */
  let streaming: boolean | null = null;
  const sniff = () =>
    (xhr.getResponseHeader('Content-Type') || '').toLowerCase().indexOf('text/event-stream') >= 0;

  let resolve!: (v: StreamResult<T>) => void;
  let reject!: (e: Error) => void;
  const result = new Promise<StreamResult<T>>((res, rej) => { resolve = res; reject = rej; });

  const ok = (data: T, note = '') => {
    if (settled) return;
    settled = true;
    resolve({ data, note });
  };
  const fail = (e: Error) => {
    if (settled) return;
    settled = true;
    reject(e);
  };

  const apply = (name: string, d: SseData) => {
    if (settled) return;
    // From the first frame onwards the bar belongs to the server — and NOT because the upload
    // must already have finished. A server may start answering before it has the whole body
    // (nginx's early 413 on an oversized batch is the case these routes actually hit, which is
    // why `readystatechange` 2 can fire with bytes still going out). The reason is simpler: a
    // server that is talking has made uploaded bytes irrelevant — the person is now waiting on
    // the run — so a late upload tick must not drag the bar backwards. Removing this guard
    // reintroduces exactly that.
    serverSpoke = true;
    if (o.terminal.includes(name)) { ok(d as T); return; }
    if (name === 'error') {
      // An `error` frame carrying `files[]` is a batch that RAN and converted some or none of
      // it — the per-file reasons in it are the useful part. Deliberately a success with a
      // note attached, not a failure: throwing it away would hide the results the visitor
      // already paid for, and a partial quota refusal lands here.
      if (Array.isArray((d as { files?: unknown }).files)) { ok(d as T, str(d.detail)); return; }
      // A run that started and gave up: there is no HTTP status to explain it with, so the
      // server's own `detail` is the whole story and `err.unavailable` covers the rare frame
      // that carries none.
      fail(new StreamError({ code: 'server', detail: str(d.detail), i18nKey: 'err.unavailable' }));
      return;
    }
    o.onEvent?.(name, d);
  };

  const drain = (text: string) => {
    if (!text) return;
    for (const [name, data] of sseFrames(state, text)) apply(name, data);
  };

  xhr.upload.addEventListener('progress', e => {
    if (settled || serverSpoke) return;
    o.onUploadProgress?.(e.lengthComputable && e.total ? (e.loaded / e.total) * 100 : null);
  });
  xhr.upload.addEventListener('load', () => {
    if (!settled && !serverSpoke) o.onUploadEnd?.();
  });

  xhr.addEventListener('readystatechange', () => {
    if (xhr.readyState !== 2 || streaming !== null) return;
    streaming = sniff();
    // A non-stream answer will produce no frames, so the bar would sit at 100% of the upload
    // forever. Say "queued" instead and wait for the body.
    if (!streaming && !settled && !serverSpoke) o.onUploadEnd?.();
  });

  xhr.addEventListener('progress', () => {
    if (!streaming || settled) return;
    const chunk = xhr.responseText.slice(seen);
    seen = xhr.responseText.length;
    drain(chunk);
  });

  xhr.addEventListener('load', () => {
    if (settled) return;
    if (streaming === null) streaming = sniff();   // readyState 2 can be missed; headers persist
    if (streaming) {
      // Drain what landed after the last tick — a terminal frame can share a chunk with EOF —
      // and then give up honestly rather than leaving a live bar on a dead stream.
      const tail = xhr.responseText.slice(seen);
      seen = xhr.responseText.length;
      drain(tail);
      if (settled) return;
      // A refused request that still answered `text/event-stream` should report what it was
      // refused with; only a genuinely truncated 2xx is `truncated`. There is no JSON body to
      // mine for a `detail` on this path, so the status map is all there is — which is exactly
      // the case `keyForStatus` was written for.
      if (xhr.status && (xhr.status < 200 || xhr.status >= 300)) {
        fail(new StreamError({ code: 'http', status: xhr.status, ...keyForStatus(xhr.status) }));
      } else {
        // A dead stream is a service problem from the visitor's side; a page with a better
        // sentence for a half-run batch branches on `code === 'truncated'`.
        fail(new StreamError({ code: 'truncated', i18nKey: 'err.unavailable' }));
      }
      return;
    }
    if (!xhr.status) { fail(networkError()); return; }
    try { ok(parseBody<T>(xhr.responseText, xhr.status)); }
    catch (e) { fail(e as Error); }
  });

  xhr.addEventListener('error', () => fail(networkError()));
  // A cancel is signalled the way `session.ts`'s `request()` signals it — a DOMException named
  // AbortError, re-thrown rather than dressed up as an outage. The two are matched on purpose:
  // a page that runs a `fetch` and an upload side by side tests `e.name === 'AbortError'` once
  // instead of asking which transport it happened to call. And it must not be a StreamError:
  // the legacy pages show a quiet "cancelled" line and deliberately do NOT toast, so an abort
  // that rendered through `apiMessage` would fire an error at somebody who pressed Cancel.
  xhr.addEventListener('abort', () => fail(new DOMException('The request was aborted.', 'AbortError')));

  xhr.open('POST', o.url, true);
  const h = o.headers || {};
  for (const k of Object.keys(h)) xhr.setRequestHeader(k, h[k]);
  // The bar exists from the first byte: a small body can finish uploading without ever firing
  // a progress event, and an empty bar that jumps straight to the server's stage reads as a
  // stall.
  o.onUploadProgress?.(0);
  xhr.send(o.body);   // no xhr.timeout: a forty-file batch legitimately runs for minutes

  return { result, abort: () => xhr.abort() };
}

/** Read a whole non-streamed body — the refusal path, and any server without `?stream=1`.

    Deliberately not `session.ts`'s `readBody`: that one takes a `Response` (and hands the body
    to `readJsonBody`, which `await r.text()`s it), so reaching it would mean wrapping
    `responseText` in a synthetic `Response` for nothing but an allocation. The RESULT is
    identical on purpose — same three spellings of the server's detail, same status→key map,
    same `err.badresp` for a 2xx that is not JSON — because the two transports refuse the same
    routes and a visitor must not be able to tell which one was used. */
function parseBody<T>(text: string, status: number): T {
  let data: unknown = null;
  const trimmed = (text || '').trimStart();
  if (trimmed && (trimmed[0] === '{' || trimmed[0] === '[')) {
    try { data = JSON.parse(trimmed); } catch { /* fall through to the status */ }
  }
  const body = data as { detail?: unknown; message?: unknown; error?: unknown } | null;
  if (status < 200 || status >= 300) {
    // Three spellings because the backend uses `detail` (FastAPI) and proxied errors have been
    // seen as `message` or `error`. When there is none — an nginx 413 or 504 page, which is
    // HTML — `keyForStatus` supplies the wording instead of leaving the page with a bare number.
    const detail = str(body?.detail) || str(body?.message) || str(body?.error);
    throw new StreamError({ code: 'http', status, detail, ...keyForStatus(status) });
  }
  // A 2xx whose body is an nginx page or an empty string is not a result.
  if (data === null) throw new StreamError({ code: 'badresp', status, i18nKey: 'err.badresp' });
  return data as T;
}

/** No response at all: no status to map, so it takes the same key `session.ts` gives its own
    status-0 `ApiError`. */
const networkError = () => new StreamError({ code: 'network', i18nKey: 'err.unavailable' });

const str = (v: unknown): string => (typeof v === 'string' ? v : '');
