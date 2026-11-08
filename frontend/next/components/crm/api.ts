/* How the CRM components reach the server — supplied by the page that mounts them.
   ===============================================================================
   The same editor runs in the workspace portal (scope 'tenant', where an operator's selection
   becomes `X-Act-As-Tenant`) and in the console's default-form tab (scope 'admin', which must
   never carry that header). Each page already has its own 401 handling — the workspace's
   funnel, the console's SessionExpired redirect — so the page builds this object around its
   own transports and the components never pick a credential themselves.

   Every method THROWS on failure (an `ApiError` from `lib/session.ts`), so a component can show
   the server's own sentence and, for a structured refusal, read `ApiError.body`. */

import { ApiError } from '@/lib/session';
import type { Problem } from '@/lib/crmForm';

export interface CrmApi {
  get<T>(path: string): Promise<T>;
  send<T>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T>;
  upload<T>(path: string, fd: FormData): Promise<T>;
  download(path: string, fallbackName: string): Promise<void>;
}

/** The problem list of a 422 `{message, errors}`, or [] for any other failure. */
export function problemsOf(e: unknown): Problem[] {
  const body = e instanceof ApiError ? e.body : null;
  const errors = body && typeof body === 'object' ? (body as { errors?: unknown }).errors : null;
  return Array.isArray(errors) ? errors.filter((p): p is Problem => !!p && typeof p === 'object') : [];
}

/** The field keys of a 422 `{message, fields}` (a review the server refused), or []. */
export function badFieldsOf(e: unknown): string[] {
  const body = e instanceof ApiError ? e.body : null;
  const fields = body && typeof body === 'object' ? (body as { fields?: unknown }).fields : null;
  return Array.isArray(fields) ? fields.map(String) : [];
}

/** Save a draft definition as a JSON file, entirely in the browser — what "Export" does for
    a form that has unsaved edits, so the file always matches what is on screen. */
export function downloadJson(name: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Delayed: revoking straight after click() cancels the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
