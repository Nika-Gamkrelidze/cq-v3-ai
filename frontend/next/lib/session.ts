/* Who is signed in, and how to call the API as them.
   ==================================================
   Reads the SAME sessionStorage keys the legacy pages use, so a person moving between a
   migrated page and one that has not moved yet stays signed in. That compatibility is the
   whole reason these key names are not "improved" here — they can be renamed on the day the
   last legacy page is deleted, and not before.

   Nothing in this file is a permission. It decides what to SHOW and which header to send;
   every route re-checks the credential server-side.

   Two things live here that a "just fetch it" client would not have, both because the legacy
   code has them and the port must not lose them:

     * SCOPE IS EXPLICIT (§scopedHeaders). A tab can hold three tokens at once, and picking
       between them by precedence is wrong on most of the pages still to be ported, so the
       caller names the scope. One exception, and it is a debt rather than a design: `ApiOpts.
       scope` is still OPTIONAL, because `/usage` and `/ai-config` shipped before it existed.
       That path guesses by precedence, warns in dev naming the request, and goes away with
       `authHeaders`. New code names its scope.
     * ERRORS CARRY AN i18n KEY, not a sentence (§ApiError). `lib/` has no dictionary and no
       language — pages render the key with their own `t`. */

export type Role = 'superadmin' | 'tenant' | 'user' | 'anonymous';

export interface Session {
  admin: string;
  tenant: string;
  user: string;
  role: Role;
}

export function readSession(): Session {
  let admin = '', tenant = '', user = '';
  try {
    admin = sessionStorage.getItem('cq_admin_token') || '';
    tenant = sessionStorage.getItem('cq_tenant_token') || '';
    user = sessionStorage.getItem('cq_user_token') || '';
  } catch { /* private mode: treat as signed out */ }
  const role: Role = admin ? 'superadmin' : tenant ? 'tenant' : user ? 'user' : 'anonymous';
  return { admin, tenant, user, role };
}

export function signOut(): void {
  try {
    sessionStorage.removeItem('cq_admin_token');
    sessionStorage.removeItem('cq_tenant_token');
    sessionStorage.removeItem('cq_user_token');
  } catch { /* nothing to clear */ }
}

/* Same-origin by default: nginx proxies /api/ to the FastAPI container, so the browser never
   learns the backend's address and there is no CORS to configure.

   The override exists for `next dev`. The legacy pages compute their base from the port
   (`/api` on 80, `http://<host>:8000` otherwise), so they keep working on a dev server; a
   hardcoded '/api' here 404s every call under `next dev -p 3000`. It is an env var rather
   than the same port sniff because `output: 'export'` builds ONE artifact that nginx serves
   at whatever address production has — sniffing would make the shipped bundle's behaviour
   depend on how the operator reaches it. NEXT_PUBLIC_* is inlined at build time, so the
   production bundle still contains the literal '/api' and no ':8000' host reaches a customer.
   See .env.local.example. A rewrite in next.config.mjs is NOT an option: `output: 'export'`
   ignores rewrites entirely. */
export const API = (process.env.NEXT_PUBLIC_API_BASE || '/api').replace(/\/+$/, '');

/* ---------------- scope ---------------- */

/** Which credential a request runs under. Named by the caller, never inferred.

    | scope      | header sent                                             |
    |------------|---------------------------------------------------------|
    | `public`   | none — not even when a token is present                  |
    | `user`     | `Authorization: Bearer <cq_user_token>`                  |
    | `tenant`   | `Authorization: Bearer <cq_tenant_token>`                |
    | `admin`    | `X-Admin-Token` — `/admin/*` only, never with act-as     |
    | `operator` | `X-Admin-Token` + `X-Act-As-Tenant`, else tenant Bearer   | */
export type Scope = 'public' | 'user' | 'tenant' | 'admin' | 'operator';

/* Every header the backend treats as a credential (`resolve_principal` step 0). Lowercased
   because a caller's `extra` may spell them any way. `X-Act-As-Tenant` is deliberately NOT in
   this list — it is a selector, meaningless without the admin token beside it. */
const CREDENTIAL_HEADERS = ['authorization', 'x-admin-token', 'x-api-key', 'x-cq-key'];

const DEV = process.env.NODE_ENV !== 'production';

/** Headers for one request, at one explicitly named scope.

    Each row of the table above is a bug the legacy code already paid for:

    * PUBLIC is not "whatever token happens to be around". `index.html`'s `pubAuth()` attaches
      the registered-user Bearer and nothing else — reusing a precedence-based helper would
      send `X-Admin-Token` to `/tts`, `/transcribe`, `/sentiment` and `/limits`, silently
      promoting an operator to superadmin scope on the public surface and filing every row
      under the wrong principal.
    * OPERATOR is admin token PLUS the workspace selector. `tenant.html` is two consoles behind
      one URL and drives the CUSTOMER's routes in both modes; without `X-Act-As-Tenant` the
      operator runs unscoped, and the backend's owner predicate for a superadmin on
      `/recordings` is literally `True` — one workspace's page would list every tenant's calls.
      It falls back to the tenant Bearer so the same call site serves the customer unchanged.
    * ADMIN carries no selector: act-as trades the superadmin principal for a tenant-shaped
      one, and `/admin/*` then refuses it. See `adminOnlyHeaders`.

    `extra` is merged last so a caller can add `Content-Type` and friends — never a credential,
    which `withoutCredentials` refuses before the merge can overwrite the scope's choice. */
export function scopedHeaders(
  scope: Scope,
  actAsTenant?: string,
  extra: Record<string, string> = {},
): Record<string, string> {
  const s = readSession();
  let h: Record<string, string> = {};

  switch (scope) {
    case 'public':
      break;                                             // no token, ever
    case 'user':
      if (s.user) h = { Authorization: `Bearer ${s.user}` };
      break;
    case 'tenant':
      if (s.tenant) h = { Authorization: `Bearer ${s.tenant}` };
      break;
    case 'admin':
      if (s.admin) h = { 'X-Admin-Token': s.admin };
      break;
    case 'operator':
      // THE TENANT TOKEN WINS WHEN BOTH ARE PRESENT, and the order here is the whole point.
      // `tenant.html` decides which console it is with `adminMode = () => !!ADMIN && !TOKEN`
      // — a tenant session in the tab means CUSTOMER mode even for a superadmin. Checking the
      // admin token first inverts that: the page would render as the customer's own workspace
      // (so it holds no workspace id to pass as `actAsTenant`) while sending `X-Admin-Token`
      // with no selector beside it — an UNSCOPED superadmin request, whose owner predicate on
      // `/recordings` and `/summaries` is literally `True`. That is one workspace's page
      // listing every tenant's calls: the repo's #1 invariant, broken by an `if` order.
      if (s.tenant) {
        h = { Authorization: `Bearer ${s.tenant}` };     // the customer's own session
      } else if (s.admin) {
        h = { 'X-Admin-Token': s.admin };
        // Empty means "no workspace picked yet" — the operator's own pre-selection screen —
        // which is a valid superadmin request, not a scoped one.
        if (actAsTenant) h['X-Act-As-Tenant'] = actAsTenant;
      }
      break;
  }

  // `admin` silently ignores a selector above; say so out loud in dev rather than letting the
  // caller believe a request is scoped to a workspace when it is running unscoped.
  if (DEV && scope === 'admin' && actAsTenant) {
    throw new Error("session: scope 'admin' cannot act as a tenant — use 'operator'.");
  }
  return { ...h, ...withoutCredentials(extra, `scope '${scope}'`) };
}

/** `X-Admin-Token` alone, for the routes an operator calls AS the superadmin.

    Kept separate from `scopedHeaders('operator', ...)` rather than folded into it, because the
    difference is invisible at the call site otherwise: `GET /admin/tenants` (the workspace
    picker) must not carry `X-Act-As-Tenant`, or the principal becomes tenant-shaped and every
    `/admin/*` route refuses it. Same split as `tenant.html`'s `authH()` / `adminOnlyH()`. */
export function adminOnlyHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return scopedHeaders('admin', undefined, extra);
}

/** Strip — and in development, refuse — a credential header written by hand in `extra`.

    Two failures fold into this one rule, and the second is why the check runs on `extra`
    BEFORE the merge rather than counting the merged result:

      * The backend rejects a request bearing more than one credential outright (400, "Present
        exactly one credential; got …", `services/auth.py`) before it verifies any of them — a
        defence against resolution ORDER deciding who you are. Correct server behaviour, and a
        miserable thing to debug from the client, where the cause is one stray `extra` header.
      * `extra` is merged LAST, so a hand-written credential under the same name does not add a
        second one — it silently REPLACES the scope's, or hands `public` (documented as "no
        token, ever") a token. Counting the merged headers finds exactly one and waves that
        through: the safety net was catching only the harmless half of the mistake.

    Loud in development, where the mistake is being made and the stack trace points at the call
    site. Dropped in production, because the scope is what the call site actually asked for: a
    dropped header is one failed request, where a passed one is a credential on a surface that
    was chosen not to carry it. Nothing legitimate writes these — choosing the credential is
    the entire job of the `scope` argument. */
function withoutCredentials(extra: Record<string, string>, where: string): Record<string, string> {
  const bad = Object.keys(extra).filter(k => CREDENTIAL_HEADERS.includes(k.toLowerCase()));
  if (!bad.length) return extra;
  if (DEV) {
    throw new Error(
      `session: ${where} was handed credential header(s) ${bad.join(', ')} in \`extra\`. ` +
      'The scope chooses the credential — pass a different scope instead.',
    );
  }
  const clean = { ...extra };
  for (const k of bad) delete clean[k];
  return clean;
}

/** @deprecated Precedence-based scope: admin, then tenant, then user.
 *
 *  Wrong on most of the pages still to be ported — it is what would quietly send an operator's
 *  `X-Admin-Token` to the public TTS routes. It survives only because `/usage` and `/ai-config`
 *  shipped against it and both are superadmin consoles, where "admin first" happens to be the
 *  right answer. New call sites pass a scope: `scopedHeaders('admin')` here, or the `scope`
 *  option on `apiGet`/`apiSend`/`apiUpload`/`apiBlob`. Delete once those two pages name theirs. */
export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const s = readSession();
  // Same `extra` discipline as `scopedHeaders`: this path is deprecated, not exempt, and it is
  // the one every un-migrated call site still lands on.
  const e = withoutCredentials(extra, 'authHeaders()');
  if (s.admin) return { 'X-Admin-Token': s.admin, ...e };
  if (s.tenant) return { Authorization: `Bearer ${s.tenant}`, ...e };
  if (s.user) return { Authorization: `Bearer ${s.user}`, ...e };
  return { ...e };
}

/* ---------------- errors ---------------- */

/** A failed API call, carrying an i18n KEY rather than a sentence.

    `lib/` has no dictionary and no language: a hook owns the current language and a component
    owns the rendering, so anything here that produced English would be English forever. The
    key set matches `brand.js`'s `readResp()` exactly — `err.toolarge`, `err.timeout`,
    `err.unavailable`, `err.http` (with `{status}`), `err.badresp` — so the two stacks say the
    same thing about the same failure, in all three languages, and the keys stay shared until
    the last legacy page is gone.

    `detail` is the server's OWN words. The backend writes messages a person can act on ("This
    workspace already has a document with that checksum"), and they are rendered verbatim:
    never translated, never replaced with the generic key. `apiMessage` prefers it. */
export class ApiError extends Error {
  /** HTTP status, or **0** when no response arrived at all (DNS, offline, proxy hang-up).
      Pages distinguish these: an outage keeps the session token and offers a retry, where a
      401 forgets it. */
  readonly status: number;
  readonly detail: string | null;
  readonly i18nKey: string;
  readonly vars?: Record<string, string | number>;

  constructor(init: {
    status: number;
    detail: string | null;
    i18nKey: string;
    vars?: Record<string, string | number>;
  }) {
    /* `message` is the DEVELOPER-facing fallback — a console line, a stack trace, and the
       `e.message` that `/usage` and `/ai-config` already render. It is deliberately not the
       i18n key: a page that has not adopted `apiMessage` yet would show a literal
       "err.unavailable" to a customer, which reads as a crash. */
    super(init.detail || `HTTP ${init.status || 'network error'}`);
    this.name = 'ApiError';
    this.status = init.status;
    this.detail = init.detail;
    this.i18nKey = init.i18nKey;
    this.vars = init.vars;
  }
}

/** Render any thrown value as a sentence in the visitor's language.
 *
 *  `t` is passed in rather than imported so this file stays free of the dictionary — see the
 *  note on `ApiError`. Usage: `catch (e) { setError(apiMessage(e, t)); }` */
export function apiMessage(
  e: unknown,
  t: (k: string, v?: Record<string, string | number>) => string,
): string {
  // The server's own words win: they are specific, already about this request, and were
  // written to be shown. Translating them is not possible here and paraphrasing loses them.
  if (e instanceof ApiError) return e.detail || t(e.i18nKey, e.vars);
  if (e instanceof Error && e.message) return e.message;
  return t('err.unavailable');
}

/** Status → key, mirroring `brand.js`'s `readResp()` so both stacks explain a 413 the same way.

    These three statuses are not hypothetical: uploads on the pages being ported routinely hit
    nginx's own 413 and 504 pages, which are HTML. That is why `readResp` exists at all — it
    replaced a cryptic "Unexpected token '<'" with an explanation.

    Exported because `xhrStream.ts` refuses the same requests over XHR and has to explain them
    with the same words. A second copy of this map is a copy that drifts — and the drift would
    be invisible, since both spellings render a plausible sentence. */
export function keyForStatus(status: number): { i18nKey: string; vars?: Record<string, string | number> } {
  if (status === 413) return { i18nKey: 'err.toolarge' };
  if (status === 504) return { i18nKey: 'err.timeout' };
  if (status === 502 || status === 503) return { i18nKey: 'err.unavailable' };
  return { i18nKey: 'err.http', vars: { status } };
}

/** Build the error for a non-2xx response, consuming its body for the server's `detail`. */
async function errorFrom(r: Response): Promise<ApiError> {
  const data = await readJsonBody(r);
  // Three spellings because the backend uses `detail` (FastAPI) and upstream/proxied errors
  // have been seen as `message` or `error`; brand.js accepts all three and so does this.
  const d = data as { detail?: unknown; message?: unknown; error?: unknown } | null;
  const raw = d ? (d.detail ?? d.message ?? d.error) : null;
  const detail = typeof raw === 'string' && raw ? raw : null;
  return new ApiError({ status: r.status, detail, ...keyForStatus(r.status) });
}

/** Parse a body as JSON, or `null` if it is not JSON at all (an nginx HTML error page). */
async function readJsonBody(r: Response): Promise<unknown> {
  const text = await r.text().catch(() => '');
  const trimmed = text.trimStart();
  if (!trimmed || (trimmed[0] !== '{' && trimmed[0] !== '[')) return null;
  try { return JSON.parse(trimmed); } catch { return null; }
}

/** Read a JSON response, throwing an `ApiError` for anything else.

    The old version RETURNED null for a 2xx whose body was not JSON, where `readResp` throws.
    That gap is worse than it sounds: `const { tenants } = await apiGet(...)` on a null
    destructures into a TypeError inside React's render, which blanks the page — instead of the
    error message the page was ready to show. A 200 that is not JSON is a broken response, and
    it is reported as one (`err.badresp`, same as the legacy stack).

    A 204/205 is the exception, and it is not a body that failed to parse — it is a route
    DECLARED to have no body. The backend has them on paths the port already covers:
    `POST /v1/chat/feedback` and `DELETE /v1/chat/conversations/{external_ref}` are both
    `status_code=204` (`routers/chat.py`), and `copilot-demo.html`'s `if (!r.ok && r.status !==
    204)` is the legacy spelling of this same exemption. Without it every one of those
    fire-and-forget calls would succeed server-side and then throw "unexpected response" at the
    person who clicked. Callers of a 204 route type the result `void` and read nothing. */
async function readBody<T>(r: Response): Promise<T> {
  if (!r.ok) throw await errorFrom(r);
  if (r.status === 204 || r.status === 205) return undefined as T;
  const data = await readJsonBody(r);
  if (data === null) {
    throw new ApiError({ status: r.status, detail: null, i18nKey: 'err.badresp' });
  }
  return data as T;
}

/* ---------------- transports ---------------- */

/** Per-request options shared by every transport below. */
export interface ApiOpts {
  /** Which credential to send. NAME IT. Omitting it falls back to the deprecated precedence of
      `authHeaders()`, which is the wrong answer on every page still to be ported — that is the
      fallback that sends an operator's `X-Admin-Token` to `/tts`. It stays optional only
      because `/usage` and `/ai-config` shipped before this option existed; the dev warning in
      `headersFor` names the path, and this becomes required when those two call sites do. */
  scope?: Scope;
  /** Workspace uuid or slug for `scope: 'operator'`. Ignored by every other scope. */
  actAs?: string;
  /** Extra headers. Never a credential — see `withoutCredentials`. */
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/* Warn once per path, not once per call: `/usage` polls, and a console line per request buries
   the other pages' warnings under one page's traffic. */
const warnedUnscoped = new Set<string>();

function headersFor(
  opts: ApiOpts = {},
  extra: Record<string, string> = {},
  path = '',
): Record<string, string> {
  const merged = { ...opts.headers, ...extra };
  if (opts.scope) return scopedHeaders(opts.scope, opts.actAs, merged);

  // No scope named. This is the unsafe default and it is deliberate only in the sense that two
  // shipped pages predate the option — so it warns instead of guessing quietly. It does not
  // throw: that would take `/usage` and `/ai-config` down on the next `npm run dev`, and the
  // precedence happens to be right on both (superadmin consoles). The warning is what makes the
  // remaining call sites findable; delete this branch with `authHeaders`.
  if (DEV && !warnedUnscoped.has(path)) {
    warnedUnscoped.add(path);
    console.warn(
      `session: ${path || 'a request'} was sent with no \`scope\` — falling back to ` +
      'authHeaders() precedence (admin, then tenant, then user). Pass e.g. ' +
      "{ scope: 'admin' } so the credential is chosen by the call site, not by which tokens " +
      'happen to be in the tab.',
    );
  }
  return authHeaders(merged);
}

/** `fetch`, with a transport failure turned into an `ApiError` of status 0.

    A page cannot treat "the server said 401" and "nothing answered" the same way: the first
    means forget the token, the second means keep it and offer a retry (`tenant.html` and
    `account.html` both do exactly this on boot). Making both an `ApiError` means one `catch`
    handles them, and `status === 0` is the test.

    An abort is re-thrown untouched — the caller asked for it, and dressing it up as an outage
    would toast an error at somebody who simply navigated away. */
async function request(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError({ status: 0, detail: null, i18nKey: 'err.unavailable' });
  }
}

export async function apiGet<T>(path: string, opts: ApiOpts = {}): Promise<T> {
  return readBody<T>(await request(`${API}${path}`, {
    headers: headersFor(opts, {}, path),
    signal: opts.signal,
  }));
}

/** PUT/POST/DELETE JSON. */
export async function apiSend<T>(
  method: 'POST' | 'PUT' | 'DELETE',
  path: string,
  body?: unknown,
  opts: ApiOpts = {},
): Promise<T> {
  return readBody<T>(await request(`${API}${path}`, {
    method,
    headers: headersFor(opts, body === undefined ? {} : { 'Content-Type': 'application/json' }, path),
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: opts.signal,
  }));
}

/** POST a `FormData` — audio uploads, KB imports, anything multipart.

    NO `Content-Type`, on purpose. A multipart body is only parseable with the boundary token
    the browser generates, and it appends that to the header it sets itself; writing
    `multipart/form-data` by hand overwrites that header WITHOUT a boundary and the server
    rejects the body it can no longer split. The one header this file must not send. */
export async function apiUpload<T>(path: string, fd: FormData, opts: ApiOpts = {}): Promise<T> {
  const headers = headersFor(opts, {}, path);
  if (DEV && Object.keys(headers).some(k => k.toLowerCase() === 'content-type')) {
    throw new Error('session: apiUpload must not set Content-Type — the browser owns the multipart boundary.');
  }
  return readBody<T>(await request(`${API}${path}`, {
    method: 'POST',
    headers,
    body: fd,
    signal: opts.signal,
  }));
}

/** Fetch binary — stored audio, an export archive, a generated MP3.

    Needed because these routes are scope-checked like every other route, so a plain
    `<audio src>` or `<a href>` arrives with no header at all and gets the same 404 as a
    stranger's token. The bytes come back through `fetch` and the caller hands the browser an
    object URL. */
export async function apiBlob(path: string, opts: ApiOpts = {}): Promise<Blob> {
  const r = await request(`${API}${path}`, { headers: headersFor(opts, {}, path), signal: opts.signal });
  if (!r.ok) throw await errorFrom(r);
  return r.blob();
}

/** Save a scope-checked file to disk, named by the server.

    Same shape as `account.html`'s `authedDownload`, including the two details that are easy to
    lose and hard to notice:

      * The revoke is DELAYED. Revoking the object URL right after `click()` cancels the
        download in some browsers — the click has been dispatched but nothing has read the
        blob yet. 60s is far past any local save.
      * The name comes from `Content-Disposition` when the server sent one, so an export is
        called what the backend decided to call it rather than what this page guessed.

    Throws like every other transport; the caller decides whether that is a toast or a banner. */
export async function downloadAuthed(
  path: string,
  fallbackName?: string,
  opts: ApiOpts = {},
): Promise<void> {
  const r = await request(`${API}${path}`, { headers: headersFor(opts, {}, path), signal: opts.signal });
  if (!r.ok) throw await errorFrom(r);

  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filenameFrom(r.headers.get('Content-Disposition')) || fallbackName || 'download';
  // In the document, because a detached <a> does not reliably fire a download in every browser.
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

/** The filename out of a `Content-Disposition`, or '' if there isn't one.

    Handles both spellings the backend emits — `filename="x.csv"` and the RFC 5987
    `filename*=UTF-8''x.csv` — and percent-decodes the result, which is how a Georgian document
    title survives the trip. A malformed sequence makes `decodeURIComponent` throw, so the raw
    value is used rather than losing the whole download to a stray '%' in a filename. */
function filenameFrom(header: string | null): string {
  const m = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(header || '');
  if (!m) return '';
  try { return decodeURIComponent(m[1]); } catch { return m[1]; }
}

/* ---------------- 401 policy ----------------

   This file THROWS. It never redirects, never clears a token, never decides that a session is
   over — because the right answer differs per page and two of them are subtle:

     * `tenant.html` funnels its boot check through `if (TOKEN) … else if (ADMIN) …`. The
       `else if` is deliberate: clearing the tenant token flips `adminMode()` true inside the
       same pass, so a plain `if`/`if` would tear down a valid OPERATOR session while cleaning
       up an expired customer one. It also treats 401 and 403 alike, and any other status as an
       outage that KEEPS the token.
     * `account.html` reads five outcomes off one request: 2xx whose `kind` is `user` (sign in),
       2xx whose `kind` is not (someone else's workspace token in this tab — leave it alone),
       401 (forget this page's token), 403 (a DISABLED account whose token is still good — keep
       it and explain), and anything else, including no response at all (an outage: keep the
       token, offer a retry).

   Both are expressible with what is above: the resolved body on success, and on failure an
   `ApiError` whose `status` is the HTTP status or 0 for "nothing answered". A redirect built
   into this file would collapse three of those five outcomes into one. */
