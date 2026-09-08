/* The account's session token, and nothing else.
   =============================================
   `sessionStorage`, per tab, under the key the legacy pages use — so a person moving between
   `/account` and a page that has not been ported yet stays signed in. `lib/session.ts` reads
   the same key to build the `Authorization` header; this module exists because the page also
   has to WRITE it (sign-in, registration) and DROP it (an expired session), and `signOut()`
   is the wrong tool for that: it clears all three tokens, and a workspace or operator session
   sitting in the same tab is not this page's to end.

   Every accessor is guarded: a browser in private mode throws on `sessionStorage`, and a page
   that cannot remember a token must still work for the length of one visit. */

const KEY = 'cq_user_token';

export function readUserToken(): string {
  try { return sessionStorage.getItem(KEY) || ''; } catch { return ''; }
}

export function storeUserToken(token: string): void {
  try { sessionStorage.setItem(KEY, token); } catch { /* private mode: this visit only */ }
}

export function dropUserToken(): void {
  try { sessionStorage.removeItem(KEY); } catch { /* nothing to clear */ }
}

/** Where a workspace or operator credential that signed in HERE puts its token, so the page
    it is about to be sent to finds a session waiting for it. Same keys `lib/session.ts` reads. */
export function storeForeignToken(scope: 'admin' | 'tenant', token: string): void {
  try { sessionStorage.setItem(scope === 'admin' ? 'cq_admin_token' : 'cq_tenant_token', token); }
  catch { /* private mode: the destination page will ask again */ }
}
