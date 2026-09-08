/* "Jump to this moment" — the one interaction every result renderer shares.
   ========================================================================
   A claim card, an evidence quote, a turn in the sentiment list and a line of transcript are
   all the same control: click (or Enter/Space) and the player moves there. The legacy panel
   spelled that as `data-seek` / `data-start` / `data-seg` / `data-call` attributes read by one
   delegated listener, so the renderers could stay pure html-string functions.

   Here the renderers are components and can hold a callback, so the target is passed as a
   value instead of encoded into the DOM. `data-seek` stays on the element because
   `[data-seek] { cursor:pointer }` is a real style rule and because it keeps the ported
   markup greppable against the original. */

import type { KeyboardEvent, MouseEvent } from 'react';

export interface SeekTarget {
  /** Seconds into the recording. Null for a finding the model placed by segment only. */
  start?: number | null;
  /** Segment index, for a text-mode highlight and for placing a start-less finding. */
  seg?: number | null;
  /** Which call of a multi-call summary. Null means "the one on screen". */
  call?: number | null;
}

export interface SeekProps {
  'data-seek': '1';
  /** The segment this control cites, when it cites one. Kept in the DOM because a click on a
      timeline SPAN has to find the card that made it — see `onSpanClick` in `index.tsx`. */
  'data-seg'?: number;
  role: 'button';
  tabIndex: 0;
  title: string;
  onClick: (e: MouseEvent<HTMLElement>) => void;
  onKeyDown: (e: KeyboardEvent<HTMLElement>) => void;
}

/** The props that make an element a seek control.

    The keyboard handler fires only when the element is itself the event target: these
    controls nest (a claim card contains an evidence quote), and without the check one Enter
    would seek twice, to two different places. */
export function seekProps(
  onSeek: (target: SeekTarget) => void,
  target: SeekTarget,
  title: string,
): SeekProps {
  return {
    'data-seek': '1',
    ...(target.seg == null ? {} : { 'data-seg': target.seg }),
    role: 'button',
    tabIndex: 0,
    title,
    onClick: (e: MouseEvent<HTMLElement>) => { e.stopPropagation(); onSeek(target); },
    onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      if (e.target !== e.currentTarget) return;
      e.preventDefault();
      onSeek(target);
    },
  };
}
