'use client';
/* What every tab of the workspace portal needs to know, and the four calls it makes.
   =================================================================================
   ONE PAGE, TWO CONSOLES. A customer signs in and sees their own workspace; a superadmin
   signs in, picks a workspace from a header selector, and then issues LITERALLY THE SAME
   REQUESTS the customer does — `lib/session.ts` turns the selection into `X-Act-As-Tenant`
   beside the admin token, and the backend hands back a tenant-shaped principal for that one
   workspace. That is why there is no second set of paths here to keep in step, and why the
   operator's view cannot answer differently from the customer's.

   The selector grants nothing. Every route re-verifies the admin token server-side, the header
   is inert for anyone else, and `/admin/*` (in practice just the workspace list) refuses it —
   which is why that one call uses `adminOnlyHeaders()` and everything else uses scope
   'tenant'. Same split as `tenant.html`'s `authH()` / `adminOnlyH()`. */

import { createContext, useContext } from 'react';
import {
  ApiError, apiGet, apiMessage, apiSend, type Scope,
} from '@/lib/session';

export type T = (key: string, vars?: Record<string, string | number>) => string;

export type TabName = 'kb' | 'analyze' | 'rubric' | 'health' | 'bot' | 'history';

/** Every tenant-scoped call this page makes runs at this scope. Named once so a call site
 *  cannot quietly pick a different credential — see `lib/session.ts` §Scope. */
export const SCOPE: Scope = 'tenant';

/** The result of a mutation, as `tenant.html`'s `kbSend` shaped it: a rejected fetch (proxy
 *  down, connection dropped) becomes an ordinary not-ok answer the caller already knows how to
 *  report, rather than an unhandled rejection that leaves a spinner spinning. */
export interface Sent<D = unknown> {
  ok: boolean;
  /** HTTP status, or 0 when nothing answered at all. */
  status: number;
  data: D | null;
  /** Already rendered in the visitor's language: the server's own words when it sent any. */
  error: string;
}

export interface Ws {
  t: T;
  /** Operator mode — an admin token and NO tenant session. A superadmin who deliberately
      signed into a workspace stays that workspace's user, exactly as before. */
  operator: boolean;
  /** The workspace an operator is looking at. '' for a customer, whose token names it. */
  tid: string;
  /** False only while an operator has picked no workspace: nothing may fetch, because an
      unscoped superadmin request to `/recordings` lists EVERY tenant's calls. */
  ready: boolean;
  /** Mirrors the server's `may_configure_workspace`: an operator acting on a workspace has the
      same authority over its settings as the account's own owner. */
  canConfigure: boolean;
  /** The generation the caller was issued in. Switching workspace bumps it, and a reply from an
      older generation is dropped — whichever load lands last would otherwise win the DOM, and
      an operator who switches quickly can read workspace A's documents under workspace B's
      name. `tenant.html`'s SCOPE_GEN, unchanged. */
  gen: () => number;
  /** Read JSON defensively. THREE VALUES, and the third is the point:
        - the parsed body,
        - `null` — the request FAILED, so say "could not load",
        - `STALE` — the answer belongs to a workspace the operator has since left.
      Collapsing `null` into `[]` tells a customer their knowledge base is empty while the
      server is merely down. That was a shipped QA bug; MIGRATION.md lists it under the
      decisions the port must preserve. */
  json: <D>(path: string) => Promise<D | null | typeof STALE>;
  /** POST/PUT/PATCH/DELETE. Never throws — see `Sent`. */
  send: <D = unknown>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown) => Promise<Sent<D>>;
  /** The 401 funnel: one gate, one message, instead of every feature quietly failing in its
      own way. Handed the thrown value so a caller can pass its `catch` straight in; anything
      that is not a 401 passes through untouched. */
  funnel: (e: unknown) => void;
  /** The same funnel, called directly. The workbench's XHR uploads never produce an `ApiError`
      for this page to inspect — they report a 401 through `onUnauthorized` instead — so the
      action has to be reachable without one to hand in. */
  unauthorized: () => void;
  /** Jump to another tab — History opens a row in Analyse, the bot's 409 explainer points at
      the knowledge base, and the workbench's "edit the rubric" is a link to a TAB. */
  showTab: (tab: TabName) => void;
  /** Open a stored recording (or summary) in the Analyse tab's workbench. */
  openRecording: (id: string) => void;
  openSummary: (id: string) => void;
}

/** The reply belongs to a workspace that is no longer on screen. A distinct value rather than
 *  `null` so a stale answer renders NOTHING at all, where a failed one renders "could not
 *  load" — a panel that says the request failed because the operator simply switched
 *  workspaces mid-flight is the same lie in the other direction. */
export const STALE = Symbol('stale');

export const WsContext = createContext<Ws | null>(null);

export function useWs(): Ws {
  const ws = useContext(WsContext);
  if (!ws) throw new Error('useWs() outside the workspace page');
  return ws;
}

/* ---------------------------------------------------------------- the transports

   These are `apiGet` / `apiSend` at scope 'tenant' with the page's 401 funnel spliced in.
   `apiGetOrNull` is exactly `json()` minus that funnel — it swallows the ApiError, so the 401
   inside it can never reach the gate — and an expired token that leaves every panel reading
   "could not load" forever is precisely what the funnel exists to prevent. The three-value
   contract it documents is the one implemented here. */

export function makeJson(funnel: (e: unknown) => void, gen: () => number) {
  return async function json<D>(path: string): Promise<D | null | typeof STALE> {
    const at = gen();
    try {
      const body = await apiGet<D>(path, { scope: SCOPE });
      return at === gen() ? body : STALE;
    } catch (e) {
      // An abort is the caller's own doing and must not read as an outage.
      if (e instanceof DOMException && e.name === 'AbortError') return STALE;
      funnel(e);
      return at === gen() ? null : STALE;
    }
  };
}

export function makeSend(funnel: (e: unknown) => void, t: T) {
  return async function send<D = unknown>(
    method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
    path: string,
    body?: unknown,
  ): Promise<Sent<D>> {
    try {
      const data = await apiSend<D>(method, path, body, { scope: SCOPE });
      return { ok: true, status: 200, data, error: '' };
    } catch (e) {
      funnel(e);
      const status = e instanceof ApiError ? e.status : 0;
      return { ok: false, status, data: null, error: apiMessage(e, t) };
    }
  };
}

/** The one line every failed mutation says, in the order the legacy page said it: the server's
 *  own words when there are any, and the generic toast when there are not. */
export function failMessage(r: Sent, t: T): string {
  return r.error || t('toast.error');
}
