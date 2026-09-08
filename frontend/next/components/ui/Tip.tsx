'use client';
import { useEffect, useId, useRef, useState, type JSX } from 'react';
import { createPortal } from 'react-dom';
import { useI18n } from '@/lib/useI18n';
import { placeTip } from '@/lib/tipPlace';

/* The ⓘ tip (brand.js `wireTip` / `mountTips` / `tip`).
   ====================================================
   The pages carry no standing prose any more, so this is where a description that is still
   worth having goes: attached to the control it is about, out of the way until someone asks.
   It deliberately opens THREE ways — hover, keyboard focus and tap — because hover-only would
   have shipped a hint that no phone and no keyboard can reach, and half this app's staff are
   on a phone.

   Two decisions carried over unchanged from `brand.js`, both of which look like accidents:

   * **The bubble is a single element parked on `<body>`, positioned `fixed`, and there is
     exactly ONE of it for the whole app** — never a child of the row it explains. Inside a
     card it is clipped by `.table-wrap`'s overflow, trapped under the next `.card` (which uses
     `backdrop-filter`, and that makes a stacking context no `z-index` on a child can escape),
     and worst of all it pushes its neighbours down on open: a row of inputs that jumps when
     you ask what a field means is not a hint, it is a trap. So the bubble is imperative DOM
     rather than React state — it belongs to no page, survives every one of them, and is
     removed when the last `<Tip>` in the app unmounts.
   * **The sentence ALSO lives in a clipped `.cq-sr` span** wired up with `aria-describedby`,
     so a screen reader reads it on focus whether or not the bubble ever paints. The bubble
     itself is `aria-hidden` — it never carries anything the accessibility tree does not
     already have. The span is rendered through a PORTAL to `<body>` rather than as a sibling
     of the ⓘ, which is the React answer to the DOM walk `wireTip` does: everything inside a
     `<label>` becomes part of the accessible NAME of the control it labels, so a span left
     there makes a screen reader announce the Voice field as "Voice, More information, Georgian
     uses the eleven_v3 model…" on every focus — the whole paragraph read as the field's name,
     which is exactly the noise a tip exists to avoid. `aria-describedby` resolves by id
     anywhere in the document, and a portal is disposed with its component, so the pair still
     comes and goes together.

   The one difference from the legacy version: the sentence arrives as a PROP that the caller
   has already translated (`<Tip text={t('bot.general.risk')} />`), so there is no `data-i18n`
   span for `applyI18n` to rewrite and no `cq:lang` listener. A language switch re-renders the
   caller, the prop changes, and the effect below repaints an open bubble. */

/* ---- The one bubble, and the listeners that only exist while it is open ---------------- */

let bubble: HTMLDivElement | null = null;
let owner: HTMLElement | null = null;
let source: '' | 'hover' | 'focus' | 'click' = '';
let reflowRaf = 0;
/* How many <Tip>s are mounted anywhere in the app. The bubble and its listeners are shared, so
   they are torn down by refcount rather than by any single instance — the legacy version never
   tore them down at all, which is harmless only under full-page navigation. */
let mounted = 0;

function ensureBubble(): HTMLDivElement {
  if (bubble && bubble.isConnected) return bubble;
  const el = document.createElement('div');
  el.id = 'cq-tip';
  el.setAttribute('aria-hidden', 'true');   // the text is already on the trigger
  document.body.appendChild(el);
  bubble = el;
  return el;
}

function place(): void {
  if (!owner || !bubble) return;
  const r = owner.getBoundingClientRect();
  const b = bubble.getBoundingClientRect();
  const at = placeTip(r, b, window.innerWidth, window.innerHeight);
  // Trigger scrolled out of its container, off screen, or inside a `display:none` subtree: a
  // bubble pointing at nothing is worse than no bubble.
  if (!at) { hideTip(); return; }
  bubble.style.top = at.top + 'px';
  bubble.style.left = at.left + 'px';
  bubble.dataset.place = at.place;
  bubble.style.setProperty('--cq-tip-ax', at.arrow + 'px');
}

function showTip(el: HTMLElement, text: string, src: 'hover' | 'focus' | 'click'): void {
  const body = text.trim();
  if (!body) return;
  if (owner && owner !== el) owner.classList.remove('on');
  const box = ensureBubble();
  box.textContent = body;
  owner = el;
  source = src;
  el.classList.add('on');
  box.classList.add('open');
  place();
  listen(true);
}

function hideTip(): void {
  if (owner) owner.classList.remove('on');
  if (bubble) bubble.classList.remove('open');
  owner = null;
  source = '';
  listen(false);
}

/* Global listeners live only while a tip is open, and there is at most one tip open, so this
   is one shared set — not one per <Tip> on the page. `addEventListener` de-dupes an identical
   (fn, capture) pair, so re-binding on every show is safe. */
function listen(on: boolean): void {
  const m = on ? 'addEventListener' : 'removeEventListener';
  document[m]('pointerdown', outside, true);
  document[m]('keydown', escape_, true);
  window[m]('scroll', reflow, true);        // capture: also catches nested scroll containers
  window[m]('resize', reflow);
  if (!on && reflowRaf) { cancelAnimationFrame(reflowRaf); reflowRaf = 0; }
}
function outside(e: Event): void {
  if (owner && !owner.contains(e.target as Node)) hideTip();
}
// Typed as `Event` rather than `KeyboardEvent` because `document[m]` above resolves to the
// generic `addEventListener`, whose listener parameter is `Event`.
function escape_(e: Event): void {
  if ((e as KeyboardEvent).key !== 'Escape') return;
  hideTip();
  /* The tip is the topmost transient thing on screen (`#cq-tip` sits at z-index 120, one rung
     above the modal backdrop precisely so a tip inside a dialog clears it), so it consumes the
     Escape that dismissed it. Without this, one Escape closes the tip AND the dialog behind
     it: this listener is on `document` in the CAPTURE phase, and the modal host's is on
     `document` in the bubble phase, so both would fire for a single key press. */
  e.stopPropagation();
}
function reflow(): void {
  if (reflowRaf) return;
  reflowRaf = requestAnimationFrame(() => { reflowRaf = 0; place(); });
}

function retain(): void { mounted++; }
function release(): void {
  if (--mounted > 0) return;
  mounted = 0;
  hideTip();                                 // also drops the listeners
  if (bubble) { bubble.remove(); bubble = null; }
}

/* ---- The trigger ----------------------------------------------------------------------- */

export interface TipProps {
  /** The sentence, already translated by the caller. Empty hides the ⓘ entirely — which is
      what lets a tip come and go with the state it describes (the Georgian voice note only
      applies while Georgian is the selected language). */
  text: string;
  /** Ids of controls the warning is ABOUT, when the ⓘ merely sits beside them rather than
      hanging off their label — the admin Test buttons, which spend real ElevenLabs credit on
      every click. They get the same `aria-describedby`, so the cost note is announced on the
      control it costs money on rather than only on the ⓘ a keyboard user may never reach.
      (`data-tip-for` in brand.js.) */
  describes?: string[];
}

export function Tip({ text, describes }: TipProps): JSX.Element {
  const { t } = useI18n();
  const ref = useRef<HTMLButtonElement>(null);
  const rawId = useId();
  const id = 'cq-tip-d' + rawId;
  // The description span is portalled to <body>, which does not exist while the page is being
  // prerendered at build time. Rendering it only after mount keeps the exported markup and the
  // hydrated markup identical.
  const [ready, setReady] = useState(false);

  useEffect(() => {
    retain();
    setReady(true);
    const el = ref.current;
    return () => {
      // An unmounting trigger must not leave the shared bubble pointing at a detached node.
      if (owner === el) hideTip();
      release();
    };
  }, []);

  // A language switch (or any state change behind the sentence) repaints an open bubble; a
  // sentence that has just become empty closes it, since the ⓘ itself is about to disappear.
  useEffect(() => {
    const el = ref.current;
    if (!el || owner !== el) return;
    if (!text.trim()) hideTip();
    else showTip(el, text, source === '' ? 'focus' : source);
  }, [text]);

  const forIds = describes ? describes.join(' ') : '';
  useEffect(() => {
    if (!forIds || !text) return;
    const touched: HTMLElement[] = [];
    for (const target of forIds.split(' ')) {
      const el = target && document.getElementById(target);
      if (!el) continue;
      const on = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
      if (on.includes(id)) continue;
      on.push(id);
      el.setAttribute('aria-describedby', on.join(' '));
      touched.push(el);
    }
    return () => {
      for (const el of touched) {
        const on = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean).filter(x => x !== id);
        if (on.length) el.setAttribute('aria-describedby', on.join(' '));
        else el.removeAttribute('aria-describedby');
      }
    };
  }, [forIds, id, text]);

  const show = (src: 'hover' | 'focus' | 'click') => {
    if (ref.current) showTip(ref.current, text, src);
  };

  return (
    <>
      {/* Empty on purpose: the "i" glyph is drawn by CSS, because the ⓘ character is missing
          from Noto Sans Georgian and a Georgian page would have rendered a tofu box.
          `type="button"` so it never submits the form it sits in. */}
      <button
        ref={ref}
        type="button"
        className="tip"
        hidden={!text}
        aria-label={t('tip.label')}
        aria-describedby={ready ? id : undefined}
        onMouseEnter={() => show('hover')}
        onMouseLeave={() => { if (source === 'hover') hideTip(); }}
        // Focus opens it so a keyboard reaches the same text a mouse does.
        onFocus={() => { if (owner !== ref.current) show('focus'); }}
        onBlur={() => { if (owner === ref.current) hideTip(); }}
        /* Tap is the only way in on a touch screen, and there a click arrives with no focus
           before it. On a mouse the focus above already opened it, so the FIRST click must not
           close it again — only a click on an already-clicked tip toggles off. */
        onClick={e => {
          e.preventDefault();
          if (owner === ref.current && source === 'click') hideTip();
          else show('click');
        }}
      />
      {/* Never `display:none` — a hidden node is what several screen readers refuse to follow
          through aria-describedby, which is the whole point of this span. `.cq-sr` clips it. */}
      {ready && text ? createPortal(<span className="cq-sr" id={id}>{text}</span>, document.body) : null}
    </>
  );
}
