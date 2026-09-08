/* Reading `POST /convert`'s answer.
   ================================
   Small enough to inline, and deliberately not inlined: this is where docs/MIGRATION.md's
   defect 1 lived, and the shape of the answer is the whole reason the old code was wrong.

   THE BUG. `editor.html` did `POST /api/convert` and then `await r.blob()`, saving the result
   as `<name>-edited.zip`. But the non-stream route (`backend/app/routers/convert.py`) answers
   with a JSON SUMMARY — `{token, download_path, expires_at, format, total, converted, failed,
   files:[…], quota_refusal}` — never with an archive. What landed on the user's disk was a
   JSON body wearing a `.zip` extension, which their unzipper refuses. The archive is behind a
   SECOND request, `GET /convert/{token}/download`, and the summary carries its path.

   TWO THINGS THE SUMMARY MAKES EASY TO GET WRONG, hence these two functions:

     * A BATCH THAT CONVERTED NOTHING IS STILL A 200. The route says so in as many words: only
       refusals about the REQUEST (bad format, too many files, no quota at all) are HTTP
       statuses; "which files failed and why" is the answer to a question the caller asked, so
       it comes back as a successful body with `converted: 0` and `token: null`. A client that
       tests `r.ok` alone downloads nothing and reports success.
     * THE REASON IS PER FILE. `detail` is not in this body. The thing worth showing is the
       file's own error ("no audio track"), or the quota refusal that truncated the batch. */

export interface ConvertFileResult {
  index: number;
  name: string;
  output: string | null;
  bytes: number;
  ok: boolean;
  error: string | null;
}

export interface ConvertSummary {
  token: string | null;
  /** API-relative, e.g. `/convert/<token>/download` — the backend builds it that way on
      purpose, because an absolute URL would have to guess the scheme and host behind nginx. */
  download_path: string | null;
  expires_at: string | null;
  format: string;
  total: number;
  converted: number;
  failed: number;
  files: ConvertFileResult[];
  quota_refusal: string | null;
}

/** Where the archive is, or null when this batch produced none.

    Both halves of the test matter. `converted` is the count the server's own `_finish` keys
    the token on — zero means the batch was DISCARDED, so there is nothing behind any path —
    and `download_path` is null in exactly that case. Testing one without the other trusts a
    field the other contradicts. */
export function archivePath(summary: ConvertSummary | null | undefined): string | null {
  if (!summary || !summary.converted || !summary.download_path) return null;
  return summary.download_path;
}

/** Why a batch produced nothing, in the server's own words, or null if it did not say.

    The first failed file's reason first: it is the specific one, and for the editor — which
    always sends exactly one file — it is the only one. `quota_refusal` is the fallback,
    because a batch truncated by the daily allowance reports that at the batch level and
    leaves the untouched files' `error` set to the same sentence anyway. */
export function refusal(summary: ConvertSummary | null | undefined): string | null {
  if (!summary) return null;
  const bad = (summary.files || []).find(f => f && !f.ok && f.error);
  return (bad && bad.error) || summary.quota_refusal || null;
}
