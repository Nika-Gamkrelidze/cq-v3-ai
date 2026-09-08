import test from 'node:test';
import assert from 'node:assert/strict';

/* The scope table, pinned.

   `scopedHeaders` is a pure function of (scope, actAs, three sessionStorage keys) and it decides
   which credential leaves the browser — the one thing in this migration that fails silently and
   fails across tenants. The case this file exists for is `operator` with BOTH an admin token and
   a tenant token in the tab, which really happens: neither login path clears its sibling key, so
   an operator who signs in to the console and then into a workspace has both. `tenant.html` reads
   that state as CUSTOMER mode (`adminMode = () => !!ADMIN && !TOKEN`); an admin-first port reads
   it as operator mode, and then has no workspace id to put in `X-Act-As-Tenant` — an unscoped
   superadmin request, whose owner predicate on `/recordings` is literally `True`. One `if` order,
   every tenant's calls on one customer's page. Nothing else guards it.

   session.ts reads `sessionStorage` and `fetch` off the global. Node has neither Web Storage
   (without --experimental-webstorage) nor a server to talk to, so both are stubbed here. */

const store = new Map<string, string>();
let storageThrows = false;

(globalThis as unknown as { sessionStorage: unknown }).sessionStorage = {
  getItem(k: string) {
    if (storageThrows) throw new Error('private mode');   // Safari private browsing, historically
    return store.has(k) ? store.get(k)! : null;
  },
  setItem(k: string, v: string) { store.set(k, String(v)); },
  removeItem(k: string) {
    if (storageThrows) throw new Error('private mode');
    store.delete(k);
  },
};

const {
  scopedHeaders, adminOnlyHeaders, authHeaders, readSession, signOut,
  apiGet, apiGetOrNull, apiSend, ApiError,
  setActingTenant, getActingTenant, apiBase,
} = await import('../session.ts');

/** Put exactly these tokens in the tab, and nobody acting as anybody.
 *
 *  The act-as selection is MODULE state that outlives one test, so clearing it here rather than
 *  in each test is what keeps a later test from reading an earlier one's workspace — which
 *  would show up as a header appearing in a table row that pins its absence. */
function signedIn(t: { admin?: string; tenant?: string; user?: string } = {}): void {
  store.clear();
  setActingTenant(null);
  if (t.admin) store.set('cq_admin_token', t.admin);
  if (t.tenant) store.set('cq_tenant_token', t.tenant);
  if (t.user) store.set('cq_user_token', t.user);
}

const ALL = { admin: 'A', tenant: 'T', user: 'U' };

/* ---------------- the table ---------------- */

test('public sends no credential, even with all three tokens in the tab', () => {
  signedIn(ALL);
  assert.deepEqual(scopedHeaders('public'), {});
  // The legacy `pubAuth()` bug this prevents: an operator's X-Admin-Token on /tts.
  assert.deepEqual(scopedHeaders('public', 'some-tenant'), {});
});

test('user sends the registered-user Bearer only', () => {
  signedIn(ALL);
  assert.deepEqual(scopedHeaders('user'), { Authorization: 'Bearer U' });
  signedIn({ admin: 'A', tenant: 'T' });
  assert.deepEqual(scopedHeaders('user'), {});     // no user token: no header, not a fallback
});

test('tenant sends the workspace Bearer only', () => {
  signedIn(ALL);
  assert.deepEqual(scopedHeaders('tenant'), { Authorization: 'Bearer T' });
  signedIn({ admin: 'A' });
  // A superadmin with NO workspace picked: nothing, not a bare admin token. That token with no
  // selector beside it is an unscoped superadmin request, and `/recordings`' owner predicate
  // for one is literally `True` — a workspace panel would list every tenant's calls.
  assert.deepEqual(scopedHeaders('tenant'), {});
});

test('admin sends X-Admin-Token only', () => {
  signedIn(ALL);
  assert.deepEqual(scopedHeaders('admin'), { 'X-Admin-Token': 'A' });
  signedIn({ tenant: 'T', user: 'U' });
  assert.deepEqual(scopedHeaders('admin'), {});
});

test('admin refuses a workspace selector — that is what operator is for', () => {
  signedIn(ALL);
  // Act-as trades the superadmin principal for a tenant-shaped one and /admin/* then refuses it,
  // so a caller who passes it here is asking for a request that cannot work.
  assert.throws(() => scopedHeaders('admin', 'acme'), /cannot act as a tenant/);
  assert.deepEqual(adminOnlyHeaders(), { 'X-Admin-Token': 'A' });
});

test('operator: THE TENANT TOKEN WINS when both are present', () => {
  signedIn({ admin: 'A', tenant: 'T' });
  const h = scopedHeaders('operator', 'acme');
  // Read the two that matter BEFORE the deepEqual: assert.deepEqual is an assertion signature,
  // so it narrows `h` to the literal shape and indexing it afterwards no longer typechecks.
  const adminToken = h['X-Admin-Token'];
  const selector = h['X-Act-As-Tenant'];
  assert.deepEqual(h, { Authorization: 'Bearer T' });
  // Spelled out, because these two are the leak: an admin token with no selector beside it.
  assert.equal(adminToken, undefined);
  assert.equal(selector, undefined);
});

test('operator: admin alone carries the selector', () => {
  signedIn({ admin: 'A', user: 'U' });
  assert.deepEqual(scopedHeaders('operator', 'acme'), {
    'X-Admin-Token': 'A',
    'X-Act-As-Tenant': 'acme',
  });
});

test('operator: admin with no workspace picked yet is a valid unscoped superadmin request', () => {
  signedIn({ admin: 'A' });
  // The operator's own pre-selection screen — GET /admin/tenants has no workspace to name.
  assert.deepEqual(scopedHeaders('operator'), { 'X-Admin-Token': 'A' });
  assert.deepEqual(scopedHeaders('operator', ''), { 'X-Admin-Token': 'A' });
});

test('operator: the plain customer, and nobody at all', () => {
  signedIn({ tenant: 'T' });
  assert.deepEqual(scopedHeaders('operator', 'acme'), { Authorization: 'Bearer T' });
  signedIn({});
  assert.deepEqual(scopedHeaders('operator'), {});
});

/* ---------------- `extra` may not carry a credential ---------------- */

test('a credential in `extra` is refused, not merged over the scope', () => {
  signedIn(ALL);
  // The dangerous spelling: same header name, so it REPLACES the scope's choice and a
  // count-the-merged-result check would see exactly one credential and pass it.
  assert.throws(() => scopedHeaders('public', undefined, { Authorization: 'Bearer stolen' }),
    /credential header/);
  assert.throws(() => scopedHeaders('tenant', undefined, { Authorization: 'Bearer other' }),
    /credential header/);
  // And the spelling the old check did catch: a different name, two credentials on the wire.
  assert.throws(() => scopedHeaders('tenant', undefined, { 'X-Admin-Token': 'A' }),
    /credential header/);
  assert.throws(() => scopedHeaders('user', undefined, { 'x-api-key': 'k' }), /credential header/);
  assert.throws(() => authHeaders({ 'X-CQ-Key': 'k' }), /credential header/);
});

test('ordinary extra headers still pass through', () => {
  signedIn({ tenant: 'T' });
  assert.deepEqual(scopedHeaders('tenant', undefined, { 'Content-Type': 'application/json' }), {
    Authorization: 'Bearer T',
    'Content-Type': 'application/json',
  });
  // X-Act-As-Tenant is a selector, not a credential: meaningless without the admin token.
  assert.deepEqual(scopedHeaders('public', undefined, { 'X-Act-As-Tenant': 'acme' }),
    { 'X-Act-As-Tenant': 'acme' });
});

/* ---------------- session ---------------- */

test('role is the precedence the nav renders from', () => {
  signedIn(ALL);
  assert.equal(readSession().role, 'superadmin');
  signedIn({ tenant: 'T', user: 'U' });
  assert.equal(readSession().role, 'tenant');
  signedIn({ user: 'U' });
  assert.equal(readSession().role, 'user');
  signedIn({});
  assert.equal(readSession().role, 'anonymous');
});

test('storage that throws reads as signed out rather than crashing the page', () => {
  signedIn(ALL);
  storageThrows = true;
  try {
    assert.deepEqual(readSession(), { admin: '', tenant: '', user: '', role: 'anonymous' });
    signOut();                                     // must not throw either
  } finally {
    storageThrows = false;
  }
});

/* ---------------- transports ---------------- */

/** Answer the next fetch with this, and record what was sent — including WHERE, which is how
 *  the API base is pinned end to end rather than only at `apiBase()`. */
let lastInit: RequestInit | undefined;
let lastUrl = '';
function serve(make: () => Response | Promise<Response> | never): void {
  (globalThis as unknown as { fetch: unknown }).fetch = async (u: string, init: RequestInit) => {
    lastUrl = u;
    lastInit = init;
    return make();
  };
}

test('204 is a success with no body, not a broken response', async () => {
  signedIn({ tenant: 'T' });
  serve(() => new Response(null, { status: 204 }));
  // POST /v1/chat/feedback and DELETE /v1/chat/conversations/{ref} are both status_code=204.
  // Throwing err.badresp here would toast an error at every fire-and-forget feedback click.
  assert.equal(await apiSend<void>('POST', '/v1/chat/feedback', { a: 1 }, { scope: 'tenant' }),
    undefined);
  assert.equal(await apiSend<void>('DELETE', '/v1/chat/conversations/x', undefined,
    { scope: 'tenant' }), undefined);
});

test('a 200 that is not JSON is still reported, not returned as null', async () => {
  signedIn({ admin: 'A' });
  serve(() => new Response('<html>nginx</html>', { status: 200 }));
  await assert.rejects(apiGet('/admin/usage/tenants', { scope: 'admin' }), (e: unknown) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.i18nKey, 'err.badresp');
    return true;
  });
});

test("the server's own words survive; status 0 means nothing answered", async () => {
  signedIn({ admin: 'A' });
  serve(() => new Response(JSON.stringify({ detail: 'That slug is taken.' }), { status: 409 }));
  await assert.rejects(apiGet('/admin/tenants', { scope: 'admin' }), (e: unknown) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 409);
    assert.equal(e.detail, 'That slug is taken.');
    return true;
  });

  serve(() => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(apiGet('/admin/tenants', { scope: 'admin' }), (e: unknown) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 0);                     // keep the token, offer a retry
    assert.equal(e.i18nKey, 'err.unavailable');
    return true;
  });
});

test('an abort is re-thrown untouched, never dressed up as an outage', async () => {
  signedIn({ admin: 'A' });
  serve(() => { throw new DOMException('The user aborted a request.', 'AbortError'); });
  await assert.rejects(apiGet('/admin/tenants', { scope: 'admin' }), (e: unknown) => {
    // A page consuming two transports must not have to ask which one aborted.
    assert.ok(e instanceof DOMException);
    assert.equal(e.name, 'AbortError');
    assert.ok(!(e instanceof ApiError));
    return true;
  });
});

test('the scope reaches the wire, and JSON bodies get their Content-Type', async () => {
  signedIn({ admin: 'A', tenant: 'T' });
  serve(() => new Response('{}', { status: 200 }));
  await apiSend('PUT', '/scoring/config', { x: 1 }, { scope: 'operator', actAs: 'acme' });
  assert.deepEqual(lastInit?.headers, {
    Authorization: 'Bearer T',                     // the both-tokens case, end to end
    'Content-Type': 'application/json',
  });
  assert.equal(lastInit?.body, '{"x":1}');
});

/* ---------------- acting as a workspace ----------------

   The other half of the same leak. The table above pins which credential leaves the browser;
   this pins WHICH WORKSPACE it is about. An operator's `X-Admin-Token` reaches the customer's
   own routes, and there the selector is the only thing standing between "this workspace's
   documents" and every workspace's — `/admin/*` is not involved, so nothing else narrows it.

   Both directions are load-bearing and both are silent when wrong: a missing selector reads as
   a full-tenant listing that looks like a very busy workspace, and a selector on `/admin/*`
   reads as an expired console session (the principal becomes tenant-shaped and the route
   refuses it) on the very request that populates the picker. */

test('an acting operator scopes the customer routes', () => {
  signedIn({ admin: 'A' });
  setActingTenant('11111111-2222-3333-4444-555555555555');
  // `tenant` — the scope name the component contract exposes, so a Workbench mounted in the
  // operator console does not have to know it is in the operator console.
  assert.deepEqual(scopedHeaders('tenant'), {
    'X-Admin-Token': 'A',
    'X-Act-As-Tenant': '11111111-2222-3333-4444-555555555555',
  });
  // A slug is equally valid — the backend resolves either.
  setActingTenant('acme');
  assert.deepEqual(scopedHeaders('tenant'), { 'X-Admin-Token': 'A', 'X-Act-As-Tenant': 'acme' });
  assert.deepEqual(scopedHeaders('operator'), { 'X-Admin-Token': 'A', 'X-Act-As-Tenant': 'acme' });
  // And a per-call override still wins over the session's selection.
  assert.deepEqual(scopedHeaders('tenant', 'other'),
    { 'X-Admin-Token': 'A', 'X-Act-As-Tenant': 'other' });
});

test('adminOnlyHeaders NEVER carries the selector, however it was set', () => {
  signedIn({ admin: 'A' });
  setActingTenant('acme');
  // GET /admin/tenants, made from a console that is already looking at a workspace: the normal
  // state of the page, and the one where folding this into `scopedHeaders('operator')` breaks.
  assert.deepEqual(adminOnlyHeaders(), { 'X-Admin-Token': 'A' });
  assert.deepEqual(adminOnlyHeaders({ 'Content-Type': 'application/json' }),
    { 'X-Admin-Token': 'A', 'Content-Type': 'application/json' });
  assert.deepEqual(scopedHeaders('admin'), { 'X-Admin-Token': 'A' });
  // Still refuses an explicit one — the module's selection is not an explicit one.
  assert.throws(() => scopedHeaders('admin', 'acme'), /cannot act as a tenant/);
});

test('clearing the workspace removes the header', () => {
  signedIn({ admin: 'A' });
  setActingTenant('acme');
  setActingTenant(null);
  assert.equal(getActingTenant(), null);
  // `tenant` sends nothing rather than a bare admin token; `operator` may send the bare token,
  // because its extra job is the pre-selection screen.
  assert.deepEqual(scopedHeaders('tenant'), {});
  assert.deepEqual(scopedHeaders('operator'), { 'X-Admin-Token': 'A' });
  // '' is the same decision spelled the way an unselected <select> spells it, and it must NOT
  // fall back to the session's selection.
  setActingTenant('acme');
  assert.deepEqual(scopedHeaders('tenant', ''), {});
  setActingTenant('');
  assert.equal(getActingTenant(), null);
});

test('the workspace survives a reload, under the key tenant.html already uses', async () => {
  signedIn({ admin: 'A' });
  setActingTenant('acme');
  // The point of persisting at all: an operator working one account should not have to re-pick
  // it after every refresh. Written under `cq_console_tenant` so the un-ported `tenant.html`
  // restores the same workspace.
  assert.equal(store.get('cq_console_tenant'), 'acme');

  // A SECOND MODULE INSTANCE is what a reload actually is: fresh module state, same storage.
  // The query string is a cache-buster — Node keys the ESM cache by URL — and the specifier is
  // held in a variable because `tsc` types a non-literal dynamic import as `any` and so does
  // not try to resolve `../session.ts?reloaded=1` as a path.
  const reloaded = '../session.ts?reloaded=1';
  const fresh = await import(reloaded);
  assert.equal(fresh.getActingTenant(), 'acme');
  assert.deepEqual(fresh.scopedHeaders('tenant'), { 'X-Admin-Token': 'A', 'X-Act-As-Tenant': 'acme' });

  signedIn({ admin: 'A' });                        // clears this module's copy, not the other's
  assert.equal(store.get('cq_console_tenant'), undefined);
});

test('a customer session ignores the selector entirely', () => {
  // Belt and braces for the header being inert for everyone else: a tenant Bearer with a
  // workspace id left over in this tab must not ask the server to act as anybody, and the
  // public surface must stay bare.
  signedIn({ tenant: 'T', user: 'U' });
  setActingTenant('acme');
  assert.deepEqual(scopedHeaders('tenant'), { Authorization: 'Bearer T' });
  assert.deepEqual(scopedHeaders('operator'), { Authorization: 'Bearer T' });
  assert.deepEqual(scopedHeaders('user'), { Authorization: 'Bearer U' });
  assert.deepEqual(scopedHeaders('public'), {});
});

test('signing out forgets the workspace too', () => {
  signedIn({ admin: 'A' });
  setActingTenant('acme');
  signOut();
  assert.equal(getActingTenant(), null);
  assert.equal(store.get('cq_console_tenant'), undefined);
});

test('storage that throws still tracks the workspace for this page view', () => {
  signedIn({ admin: 'A' });
  storageThrows = true;
  try {
    setActingTenant('acme');                       // must not throw in private mode
    assert.equal(getActingTenant(), 'acme');       // in memory, just not persisted
  } finally {
    storageThrows = false;
    setActingTenant(null);
  }
});

/* ---------------- where the API lives ---------------- */

/** Pretend the page is being viewed at this address. Node has no `location`; the export sniffs
 *  one when there is one, and must not need one when there is not (`next build` in Node). */
function viewedAt(url: string | null): void {
  const g = globalThis as unknown as { location?: unknown };
  if (url === null) { delete g.location; return; }
  const u = new URL(url);
  g.location = { protocol: u.protocol, hostname: u.hostname, port: u.port };
}

test('the API base is /api on the default port and the :8000 form on a dev server', () => {
  // Production, both schemes. `location.port` is '' for a default port in every browser; the
  // explicit spellings are what a proxy or a port-forward produces.
  viewedAt('http://217.147.236.219/tenant.html');
  assert.equal(apiBase(), '/api');
  viewedAt('https://ai.communiq.ge/workspace');
  assert.equal(apiBase(), '/api');
  viewedAt('http://ai.communiq.ge:80/workspace');
  assert.equal(apiBase(), '/api');
  viewedAt('https://ai.communiq.ge:443/workspace');
  assert.equal(apiBase(), '/api');

  // `npm run dev`. THE HAZARD: '/api' here is http://localhost:3000/api, which 404s every call
  // — including on `/usage` and `/ai-config`, which shipped against the hardcoded const.
  viewedAt('http://localhost:3000/workspace');
  assert.equal(apiBase(), 'http://localhost:8000');
  viewedAt('http://192.168.1.20:3000/workspace');
  assert.equal(apiBase(), 'http://192.168.1.20:8000');

  // `next build` prerendering in Node: no location at all, and no host to bake into a bundle
  // that nginx will serve at a different address anyway.
  viewedAt(null);
  assert.equal(apiBase(), '/api');
});

test('the base reaches the wire', async () => {
  signedIn({ admin: 'A' });
  serve(() => new Response('{}', { status: 200 }));
  viewedAt('http://localhost:3000/usage');
  await apiGet('/admin/usage/tenants', { scope: 'admin' });
  assert.equal(lastUrl, 'http://localhost:8000/admin/usage/tenants');
  viewedAt('http://217.147.236.219/usage');
  await apiGet('/admin/usage/tenants', { scope: 'admin' });
  assert.equal(lastUrl, '/api/admin/usage/tenants');
  viewedAt(null);
});

/* ---------------- apiGetOrNull ---------------- */

test('null means the request failed; [] means the workspace genuinely has none', async () => {
  signedIn({ tenant: 'T' });

  // The shipped QA bug: a dead proxy told the customer their knowledge base was empty.
  serve(() => { throw new TypeError('Failed to fetch'); });
  assert.equal(await apiGetOrNull('/kb/documents', { scope: 'tenant' }), null);
  serve(() => new Response(JSON.stringify({ detail: 'nope' }), { status: 500 }));
  assert.equal(await apiGetOrNull('/kb/documents', { scope: 'tenant' }), null);
  serve(() => new Response('<html>502 Bad Gateway</html>', { status: 200 }));
  assert.equal(await apiGetOrNull('/kb/documents', { scope: 'tenant' }), null);

  // And the value that must NOT come back as null, which is the whole reason for the function:
  // an empty list the server really sent. `deepEqual` on [] would also pass for null under
  // `assert.equal`, so the emptiness is asserted through the array itself.
  serve(() => new Response('[]', { status: 200 }));
  const docs = await apiGetOrNull<unknown[]>('/kb/documents', { scope: 'tenant' });
  assert.ok(Array.isArray(docs));
  assert.equal(docs.length, 0);

  serve(() => new Response('{"documents":[],"total":0}', { status: 200 }));
  assert.deepEqual(await apiGetOrNull('/kb/documents', { scope: 'tenant' }),
    { documents: [], total: 0 });
});

test('apiGetOrNull does not swallow an abort', async () => {
  signedIn({ tenant: 'T' });
  serve(() => { throw new DOMException('The user aborted a request.', 'AbortError'); });
  // An operator switching workspace mid-flight aborts the old load. Rendering "could not load"
  // for it would be the same lie as rendering "none" for a failure.
  await assert.rejects(apiGetOrNull('/kb/documents', { scope: 'tenant' }), (e: unknown) => {
    assert.ok(e instanceof DOMException);
    assert.equal(e.name, 'AbortError');
    return true;
  });
});

test('apiGetOrNull sends the same scoped headers as apiGet', async () => {
  signedIn({ admin: 'A' });
  setActingTenant('acme');
  serve(() => new Response('[]', { status: 200 }));
  await apiGetOrNull('/kb/documents', { scope: 'tenant' });
  assert.deepEqual(lastInit?.headers, { 'X-Admin-Token': 'A', 'X-Act-As-Tenant': 'acme' });
  setActingTenant(null);
});
