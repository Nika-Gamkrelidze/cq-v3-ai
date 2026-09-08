/* Where the one tip bubble goes — pure arithmetic, no DOM.
   =======================================================
   Split out of the component for one reason: this is the part of the tip system that is
   actually possible to get wrong, and the only part that can be tested without a browser.
   `Tip.tsx` measures two rectangles and hands them here; everything below is arithmetic over
   numbers, ported line for line from `brand.js:tipPlace`.

   The three behaviours that are load-bearing, and why:

   * **Below by default, above only when below does not fit.** A tip that flips sides on a
     tiny scroll delta is worse than one that occasionally sits low, so `below` wins ties and
     `above` has to earn the swap by having room the other does not.
   * **Clamped to the viewport, then the CARET is moved to compensate.** At 375px a tip on a
     right-hand field is ALWAYS clamped, so a caret pinned at 50% would point at empty space
     several fields away. `arrow` is the caret's offset from the bubble's own left edge —
     `#cq-tip::after` reads it as `--cq-tip-ax` — kept 12px clear of both corners so it never
     draws over the rounded border.
   * **A zero-sized or off-screen trigger returns `null`, meaning HIDE.** A bubble pointing at
     nothing is worse than no bubble. The zero case is not the same as the off-screen one
     wearing a disguise: an element inside a `display:none` subtree reports a 0x0 rect at the
     origin, which passes the bounds test below because a zero rect at (0,0) is technically
     on screen. It is checked first for exactly that reason. */

export interface Box { width: number; height: number }

/** The trigger's viewport rectangle — a `DOMRect` satisfies this. */
export interface TriggerRect { top: number; left: number; width: number; height: number }

export interface TipPlacement {
  /** Viewport coordinates for `position: fixed`. */
  top: number;
  left: number;
  /** Which side of the trigger the bubble landed on; drives the caret's border in CSS. */
  place: 'above' | 'below';
  /** Caret offset from the bubble's left edge, in px (`--cq-tip-ax`). */
  arrow: number;
}

/** Gap between the trigger and the bubble. */
export const TIP_GAP = 8;
/** Smallest distance the bubble is allowed to come to any viewport edge. */
export const TIP_EDGE = 10;

/** Place the bubble against `trigger`, or return `null` when it should not be shown at all. */
export function placeTip(trigger: TriggerRect, bubble: Box, vw: number, vh: number): TipPlacement | null {
  if (!trigger.width && !trigger.height) return null;
  const bottom = trigger.top + trigger.height;
  const right = trigger.left + trigger.width;
  if (bottom < 0 || trigger.top > vh || right < 0 || trigger.left > vw) return null;

  const place: 'above' | 'below' =
    bottom + TIP_GAP + bubble.height <= vh - TIP_EDGE ? 'below'
    : trigger.top - TIP_GAP - bubble.height >= TIP_EDGE ? 'above'
    : 'below';

  let top = place === 'below' ? bottom + TIP_GAP : trigger.top - TIP_GAP - bubble.height;
  top = Math.max(TIP_EDGE, Math.min(top, vh - bubble.height - TIP_EDGE));

  const centre = trigger.left + trigger.width / 2;
  const left = Math.max(TIP_EDGE, Math.min(centre - bubble.width / 2, vw - bubble.width - TIP_EDGE));
  const arrow = Math.max(12, Math.min(centre - left, bubble.width - 12));

  return { top: Math.round(top), left: Math.round(left), place, arrow: Math.round(arrow) };
}
