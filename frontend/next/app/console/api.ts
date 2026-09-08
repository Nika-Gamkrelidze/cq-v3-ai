/* Every request this console makes, and the one rule they all share.
   =================================================================
   `admin.html` wrapped `fetch` in `adminFetch` for a single reason, spelled out in its own
   comment: "Central 401 interception: an expired admin token lands on the sign-in page instead
   of silently no-op'ing every Refresh and turning saves into 'Save failed'." That is the whole
   job of this file — the transports themselves already live in `lib/session.ts`.

   EVERY route here is `/admin/*`, so every call runs at scope `'admin'`, which is
   `adminOnlyHeaders()` by another name (`scopedHeaders('admin')`, see lib/session.ts). That is
   not a detail: act-as-tenant trades the superadmin principal for a tenant-shaped one and every
   `/admin/*` route then REFUSES it, so a selector on one of these calls would read as an
   expired console session. Nothing on this page may carry `X-Act-As-Tenant`.

   The one exception is the voice preview in Integrations, which posts to the PUBLIC `/tts` with
   no credential at all — see `IntegrationsTab.tsx` for why that is deliberate. */

import { ApiError, apiGet, apiSend } from '@/lib/session';

/* Where an expired console session goes. Two halves, and both are load-bearing:

     * `/tenant.html` is still the unified sign-in gate; it has not been ported yet.
     * `?next=` is that page's post-login redirect, and it is checked against a hardcoded
       allowlist there — `NEXT_OK = ['admin.html', 'kb-admin.html']`, which does not know this
       route exists. So today `/console` is not in the list and the operator lands on
       `admin.html`, the console they came from, which is correct while both pages are served.
       It starts resolving here the moment the gate learns the new address, which is why the
       value is the NEW route rather than the old one it currently degrades to.

   docs/MIGRATION.md lists that allowlist as one of the three places `admin.html`'s address is
   hardcoded; the other two are this constant and the header's nav link. */
export const SIGN_IN = '/tenant.html?next=/console';

/** Thrown after a 401 has already started the redirect. A `catch` that sees one must return
    without touching state or showing a message: the page is on its way to the sign-in screen
    and a toast about it would be the last thing the operator sees, blaming the wrong thing. */
export class SessionExpired extends Error {
  constructor() {
    super('unauthorized');
    this.name = 'SessionExpired';
  }
}

/* Ten panels can be in flight at once at boot, and an expired token 401s all ten. Without this
   latch each of them calls `location.replace` — harmless in most browsers, but it also clears
   the token ten times and races the navigation. The first one wins; the rest just throw. */
let leaving = false;

function expire(): never {
  if (!leaving) {
    leaving = true;
    try { sessionStorage.removeItem('cq_admin_token'); } catch { /* private mode */ }
    location.replace(SIGN_IN);
  }
  throw new SessionExpired();
}

function rethrow(e: unknown): never {
  if (e instanceof ApiError && e.status === 401) expire();
  throw e;
}

export async function adminGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  try {
    return await apiGet<T>(path, { scope: 'admin', signal });
  } catch (e) {
    rethrow(e);
  }
}

export async function adminSend<T>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<T> {
  try {
    return await apiSend<T>(method, path, body, { scope: 'admin' });
  } catch (e) {
    rethrow(e);
  }
}

type Translate = (key: string, vars?: Record<string, string | number>) => string;

/** What to put in a `.msg err` for a failed call.

    THE SERVER'S OWN WORDS COME FIRST, exactly as `d.detail || t('toast.error')` did on every
    one of the legacy save handlers: the backend writes sentences an operator can act on
    ("Slug 'acme' already exists", "Password must be at least 6 characters"), and replacing
    them with a generic failure throws away the only part of the response that says what to do
    next.

    `byStatus` covers the handful of places where a bare status means something specific — a
    404 from `/admin/integrations` is "this server is older than this console", not "something
    went wrong". It is consulted only when there is no `detail`, which is the order the legacy
    `credCall` used.

    One thing is deliberately lost: a 422 carries pydantic's LIST of field errors, and
    `ApiError` keeps `detail` only when it is a string, so that list renders as the fallback
    instead of being joined into a sentence. The legacy code joined it; nothing this console can
    submit produces one (every body it sends is already the shape the model declares), and a
    "[object Object]" was the alternative the legacy default-bot handler guarded against by
    hand. */
export function errText(
  e: unknown,
  t: Translate,
  fallback = 'toast.error',
  byStatus: Record<number, string> = {},
): string {
  if (e instanceof ApiError) {
    if (e.detail) return e.detail;
    const key = byStatus[e.status];
    if (key) return t(key);
  }
  return t(fallback);
}

/* ---------------- the shapes the console reads ---------------- */

export interface Tenant {
  id: string;
  slug: string;
  name: string;
  industry: string | null;
  region: string | null;
  is_active: boolean;
  users: number;
  documents: number;
}

export interface TenantUser {
  id: string;
  username: string;
  role: string;
  is_active: boolean;
  created_at?: string;
}

export interface AppUser {
  id: string;
  email: string;
  display_name: string | null;
  is_active: boolean;
  created_at: string | null;
  last_login_at: string | null;
  limits: Record<string, number>;
  used: { analyses?: number; tts?: number; conversions?: number };
}

export interface Voice {
  voice_id: string;
  name?: string;
  category?: string;
  preview_url?: string | null;
  selected?: boolean;
  system?: boolean;
}

export interface VoicesPayload {
  mode: string;
  voice_ids: string[];
  voices: Voice[];
  missing: string[];
  error?: string | null;
}

export interface Grant {
  client_id: string;
  slug?: string | null;
  name?: string | null;
  scopes?: string[];
  is_active: boolean;
}

export interface Secret {
  key_id: string;
  label?: string | null;
  created_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
}

export interface Integration {
  id: string;
  name: string | null;
  kind?: string;
  scopes?: string[];
  is_active: boolean;
  created_at: string | null;
  grants: Grant[];
  secrets: Secret[];
}

/** What a create or a rotate hands back — the one and only time the key exists in the clear. */
export interface RevealedKey {
  api_key?: string;
  warning?: string;
  overlap_days?: number;
}

/* ---------------- the AI provider registry ---------------- */

/** The three things a workspace's AI is resolved for, independently of one another. The list
    itself (`CAPABILITIES`) lives in `logic.ts`, which the tests import and which therefore
    cannot import a value from this file — see the note there. */
export type Capability = 'llm' | 'stt' | 'tts';

/** One provider's entry in `GET /admin/ai/providers`. `known_models` is "known, not
    exhaustive": the form offers them first and accepts any id typed over them. */
export interface AiProviderEntry {
  label: string;
  known_models: string[];
  allows_base_url: boolean;
  /** Extra per-connection settings keys the provider takes (`voice_id` for a TTS provider). */
  fields: string[];
}

export type AiCatalog = Partial<Record<Capability, Record<string, AiProviderEntry>>>;

export interface AiLastTest {
  ok: boolean;
  at?: string | null;
  detail?: string | null;
}

/** A row of `GET /admin/ai/connections`. The key itself is NEVER in here — only whether one is
    stored and the masked hint — which is why a form cannot pre-fill it and "save with an empty
    box" cannot mean "clear it". */
export interface AiConnection {
  id: string;
  name: string;
  capability: Capability;
  provider: string;
  model: string | null;
  base_url: string | null;
  has_key: boolean;
  key_hint: string | null;
  settings: Record<string, unknown> | null;
  is_active: boolean;
  is_default: boolean;
  last_test: AiLastTest | null;
  updated_at: string | null;
  updated_by: string | null;
}

/** What a workspace is REALLY running on for one capability, as the resolver would answer. */
export interface AiEffective {
  /** byo | assigned | default | legacy — which layer of the chain answered. */
  source: string;
  provider: string;
  model: string | null;
  connection: { id: string; name: string } | null;
}

export interface AiAssignment {
  connection_id: string | null;
  effective: AiEffective;
}

/** `GET /admin/ai/assignments/{tenant_id}` — one entry per capability. */
export type AiAssignments = Partial<Record<Capability, AiAssignment>>;

/** The additive part of `GET /health` this console reads: whether provider keys are encrypted
    at rest, and which connection is the default for each capability. */
export interface HealthAi {
  secrets?: 'encrypted' | 'plaintext' | string;
  ai?: {
    connections?: number;
    defaults?: Partial<Record<Capability, string | null>>;
  };
}
