/* Who is signed in, and how to call the API as them.
   ==================================================
   Reads the SAME sessionStorage keys the legacy pages use, so a person moving between a
   migrated page and one that has not moved yet stays signed in. That compatibility is the
   whole reason these key names are not "improved" here — they can be renamed on the day the
   last legacy page is deleted, and not before.

   Nothing in this file is a permission. It decides what to SHOW and which header to send;
   every route re-checks the credential server-side. */

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

/* Same-origin: nginx proxies /api/ to the FastAPI container, so the browser never learns the
   backend's address and there is no CORS to configure. */
export const API = '/api';

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const s = readSession();
  if (s.admin) return { 'X-Admin-Token': s.admin, ...extra };
  if (s.tenant) return { Authorization: `Bearer ${s.tenant}`, ...extra };
  if (s.user) return { Authorization: `Bearer ${s.user}`, ...extra };
  return { ...extra };
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** Read the body of an API response.

    Errors carry the server's own `detail` when it sent one: the backend is careful to write
    messages a person can act on, and replacing them with "Request failed" throws that work
    away. A non-JSON body (an nginx 502 page, an HTML error) becomes the status rather than a
    JSON parse error. */
async function parse<T>(r: Response): Promise<T> {
  const text = await r.text().catch(() => '');
  let data: unknown = null;
  const trimmed = text.trimStart();
  if (trimmed && (trimmed[0] === '{' || trimmed[0] === '[')) {
    try { data = JSON.parse(trimmed); } catch { /* fall through to the status */ }
  }
  if (!r.ok) {
    const detail = (data as { detail?: string } | null)?.detail;
    throw new ApiError(typeof detail === 'string' && detail ? detail : `HTTP ${r.status}`, r.status);
  }
  return data as T;
}

export async function apiGet<T>(path: string): Promise<T> {
  return parse<T>(await fetch(`${API}${path}`, { headers: authHeaders() }));
}

/** PUT/POST/DELETE JSON. */
export async function apiSend<T>(method: 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<T> {
  return parse<T>(await fetch(`${API}${path}`, {
    method,
    headers: authHeaders(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    body: body === undefined ? undefined : JSON.stringify(body),
  }));
}
