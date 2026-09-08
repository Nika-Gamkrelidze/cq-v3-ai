'use client';
import { useEffect, useLayoutEffect, type RefObject } from 'react';

/* Auto-growing textareas (brand.js `autogrow` / `autogrowBind`).
   =============================================================
   A scoring dimension's guidance is the text the model actually reads when scoring, and AI
   rubric import fills it with that section's complete criteria verbatim — thousands of
   characters for a real call-centre standard. A fixed 54px box showed two lines of it, so the
   field people most need to READ was the one they could see least of.

   Capped rather than unbounded: seven dimensions each grown to full height would push Save
   several screens down, so past the cap the textarea scrolls internally instead. */

/** Size one textarea to its content, up to `cap` px (default: 45% of the viewport, min 240).

    Imperative twin of `useAutogrow`, for code that holds an element rather than a ref — a
    freshly rendered list, an effect that just pasted a value in. Safe to call on `null`. */
export function autogrow(el: HTMLTextAreaElement | null | undefined, cap?: number): void {
  if (!el) return;
  const limit = cap || Math.max(240, Math.round(window.innerHeight * 0.45));
  /* Inside a hidden panel a textarea measures scrollHeight 0 — sizing it there would collapse
     it to nothing. Leave it; it gets sized when its tab is shown, which under React means the
     next render after the tab state flips, and the layout effect below runs on every one. */
  if (!el.offsetHeight && !el.getClientRects().length) return;
  el.style.height = 'auto';
  const natural = el.scrollHeight;          // read BEFORE clamping, or the cap hides it
  el.style.height = Math.min(natural, limit) + 'px';
  el.style.overflowY = natural > limit ? 'auto' : 'hidden';
}

/* `useLayoutEffect` warns when React renders on the server, and this app is prerendered at
   BUILD time by the static export. The branch is resolved once, at module load, so it is not
   a conditional hook: a given environment always runs the same one. */
const useIsomorphicLayoutEffect = typeof window !== 'undefined' ? useLayoutEffect : useEffect;

/** Keep a textarea sized to its content for as long as the component is mounted.

    Deliberately has NO dependency array: it re-measures after every render of the host
    component, which is what covers a CONTROLLED textarea whose value React changed (loading a
    rubric from the server, an AI import filling seven boxes at once). The `input` listener
    covers the other half — an uncontrolled textarea being typed into, where the component
    does not re-render at all. Measuring is a style write plus one `scrollHeight` read on a
    single element, so paying it per render is cheaper than the state it would take to avoid. */
export function useAutogrow(ref: RefObject<HTMLTextAreaElement | null>, cap?: number): void {
  useIsomorphicLayoutEffect(() => { autogrow(ref.current, cap); });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const on = () => autogrow(el, cap);
    el.addEventListener('input', on);
    return () => el.removeEventListener('input', on);
  }, [ref, cap]);
}
