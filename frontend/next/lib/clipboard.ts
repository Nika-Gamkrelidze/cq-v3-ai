/* Copy to clipboard, on an origin that is not secure.
   ==================================================
   Production is reachable over plain HTTP (no domain, no TLS yet — see the root CLAUDE.md), and
   `navigator.clipboard` does not exist outside a secure context. So the textarea fallback here
   is not a nicety for old browsers: it is the path that ACTUALLY RUNS on the server, and a
   generated password an operator cannot copy is a password they retype wrong.

   THE BRANCH ORDER IS LOAD-BEARING. `document.execCommand('copy')` only works inside the
   click's own user gesture, and an `await` spends that gesture — by the time an awaited promise
   resolves, the browser no longer considers the code user-initiated and the fallback silently
   copies nothing. So the insecure-origin case takes the synchronous path FIRST, before this
   function has awaited anything, instead of discovering it needs the fallback after the async
   clipboard has already rejected.

   Which is also why `copyText` starts the async write WITHOUT awaiting it: on a secure origin
   where permission is denied, the rejection arrives after the gesture is gone, and the second
   `copyFallback` is a best effort that usually fails. Getting the order wrong makes it the
   only effort, and it fails every time. Do not "clean this up" into a try/await/catch.

   `document.execCommand` is deprecated, and deliberately used anyway. It is the only
   synchronous clipboard write the platform has ever had. */

/** The synchronous path: a hidden textarea, selected, copied, removed. Returns whether the
    browser reported success. Must be called from inside the click handler itself. */
export function copyFallback(text: string): boolean {
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    // Fixed + transparent rather than off-screen: scrolling the page to a -9999px element is
    // visible, and iOS refuses to select an element it does not consider laid out.
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

/** Copy `text`, by whichever route this origin allows. Resolves to whether it worked — show a
    toast on true, and on false leave the value on screen so it can be selected by hand. */
export async function copyText(text: string): Promise<boolean> {
  let pending: Promise<void> | null = null;
  try {
    if (navigator.clipboard && window.isSecureContext) pending = navigator.clipboard.writeText(text);
  } catch { /* a hostile permissions policy can throw on access alone */ }
  if (!pending) return copyFallback(text);      // still inside the gesture
  try {
    await pending;
    return true;
  } catch { /* denied, or the document lost focus mid-write */ }
  return copyFallback(text);                    // a denied permission on a secure origin
}
