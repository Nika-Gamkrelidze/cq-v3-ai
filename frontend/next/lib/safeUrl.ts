/* Every href the app renders passes through here.
   ==============================================
   JSX escapes TEXT, which is why brand.js's `esc()` does not come across with it — but JSX does
   NOT sanitise an ATTRIBUTE. `<a href={u}>` with `u = "javascript:fetch('/api/...')"` is a live
   script that runs with the reader's session, and the URLs on these pages are not ours: a
   citation link comes back from the curation API, which is fed by tenant-supplied documents.

   Note also what is deliberately NOT ported: brand.js's `esc()` escapes & < > " but not the
   apostrophe, so it is safe only inside double-quoted attributes. Reintroducing it next to JSX
   would be a half-applied escaper that reads like protection, which is worse than none. */

/** `rel` for any link this app does not control. `noopener` denies the opened page a handle on
    ours (`window.opener.location = ...` is a one-line phishing redirect); `noreferrer` keeps
    the workspace's URL, which carries the tenant it belongs to, out of the target's logs. */
export const EXTERNAL_REL = 'noopener noreferrer';

/** The URL if it is an absolute http(s) one, otherwise null.

    Allow-list, not block-list: `javascript:`, `data:`, `vbscript:` and `blob:` are all script
    or content-injection vectors, and the next scheme a browser invents would be permitted by
    anything written the other way round. Relative paths are rejected too — this guards
    OUTBOUND links, and an in-app destination is a literal in our own source, never a value. */
export function safeUrl(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    // `new URL` strips the tab/newline characters browsers also strip, so "java\nscript:alert(1)"
    // parses to the javascript: scheme here exactly as it would in the address bar — and is
    // then rejected by protocol, not by a regex that the same trick walks straight past.
    const url = new URL(trimmed);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
  } catch {
    return null;                                 // not an absolute URL at all
  }
}

export function isSafeUrl(raw: unknown): boolean {
  return safeUrl(raw) !== null;
}

/** Props for an outbound `<a>`, or null when there is nothing safe to link to — in which case
    render the label as plain text rather than a dead anchor. */
export function externalLink(raw: unknown, { newTab = true }: { newTab?: boolean } = {}):
  { href: string; rel: string; target?: '_blank' } | null {
  const href = safeUrl(raw);
  if (!href) return null;
  return newTab ? { href, rel: EXTERNAL_REL, target: '_blank' } : { href, rel: EXTERNAL_REL };
}
