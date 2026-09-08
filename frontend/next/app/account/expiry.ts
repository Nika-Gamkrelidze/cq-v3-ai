/* How long a stored batch has left.
   ================================
   A batch's remaining life is read from its OWN `expires_at`, never from the format
   catalogue's TTL constant — that number is the ANONYMOUS two hours and would be a lie on an
   account whose batches live for days.

   Takes `t` rather than importing it: this file has no language of its own, and the same rule
   `lib/session.ts` follows for its error keys applies one level down. */

type T = (key: string, vars?: Record<string, string | number>) => string;

/** '' when there is no deadline to report — the caller renders nothing at all, rather than a
    row that claims something about a batch the server said nothing about. */
export function expiryLabel(iso: string | null | undefined, t: T, now = Date.now()): string {
  if (!iso) return '';
  const ms = new Date(iso).getTime() - now;
  if (!Number.isFinite(ms)) return '';
  if (ms <= 0) return t('ac.hist.expired');
  const h = Math.floor(ms / 3600000);
  if (h >= 24) return t('ac.hist.left.d', { n: Math.floor(h / 24) });
  if (h >= 1) return t('ac.hist.left.h', { n: h });
  // Never "0 minutes left": a batch that is still live has at least a minute of life to
  // report, and rounding a live one down to zero reads as expired.
  return t('ac.hist.left.m', { n: Math.max(1, Math.round(ms / 60000)) });
}
