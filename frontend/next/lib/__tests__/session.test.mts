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
  apiGet, apiSend, ApiError,
} = await import('../session.ts');

/** Put exactly these tokens in the tab. */
function signedIn(t: { admin?: string; tenant?: string; user?: string } = {}): void {
  store.clear();
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

/** Answer the next fetch with this, and record what was sent. */
let lastInit: RequestInit | undefined;
function serve(make: () => Response | Promise<Response> | never): void {
  (globalThis as unknown as { fetch: unknown }).fetch = async (_u: string, init: RequestInit) => {
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
