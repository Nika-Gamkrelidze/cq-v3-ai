/* The one request shape `lib/session.ts` cannot make yet.
   ======================================================
   `apiSend` takes POST, PUT and DELETE. The manual score edit is a PATCH — deliberately, on
   the backend's side: `PATCH /recordings/{id}/score` amends the scorecard the model produced,
   where `POST /recordings/{id}/score` RUNS the rubric again. Sending the wrong verb here
   would silently re-score the call and throw away the reviewer's numbers.

   So this is a `fetch` with session.ts's own error semantics rebuilt on top of its exported
   pieces — `ApiError` and `keyForStatus` — rather than a second, subtly different way of
   explaining a 413. It is meant to be deleted: see the note in the port's open issues asking
   for `PATCH` on `apiSend`. */

import { ApiError, API, keyForStatus, scopedHeaders, type Scope } from '@/lib/session';

/** A 401 from anywhere in the panel.

    The panel reports its own expired sessions (`onUnauthorized`) because an XHR upload cannot
    go through a shared fetch wrapper — and once that callback exists, every 401 in here
    should reach it, not only the upload's, or the page tears down its session for one kind of
    request and not another. */
export function isUnauthorized(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}

export async function patchJson<T>(path: string, body: unknown, scope: Scope): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API}${path}`, {
      method: 'PATCH',
      headers: { ...scopedHeaders(scope), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    // Status 0 is session.ts's convention for "nothing answered at all" — an outage the page
    // may retry, as distinct from a refusal it must not.
    throw new ApiError({ status: 0, detail: null, i18nKey: 'err.unavailable' });
  }

  const text = await r.text().catch(() => '');
  const trimmed = text.trimStart();
  let data: unknown = null;
  if (trimmed && (trimmed[0] === '{' || trimmed[0] === '[')) {
    try { data = JSON.parse(trimmed); } catch { /* an nginx error page, not JSON */ }
  }

  if (!r.ok) {
    // Three spellings because FastAPI writes `detail` and a proxy may write `message` or
    // `error`; the server's own words are shown verbatim when there are any.
    const d = data as { detail?: unknown; message?: unknown; error?: unknown } | null;
    const raw = d ? (d.detail ?? d.message ?? d.error) : null;
    throw new ApiError({
      status: r.status,
      detail: typeof raw === 'string' && raw ? raw : null,
      ...keyForStatus(r.status),
    });
  }
  if (data === null) throw new ApiError({ status: r.status, detail: null, i18nKey: 'err.badresp' });
  return data as T;
}
