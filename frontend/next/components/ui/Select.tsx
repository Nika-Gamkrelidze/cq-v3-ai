'use client';

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, JSX, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import styles from './Select.module.css';

/* This page is prerendered at BUILD time, where `useLayoutEffect` is a no-op React warns
   about. Everything it is used for here (measuring, positioning, listeners) only matters
   once there is a browser, so on the server it degrades to the effect that does not warn. */
const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/* The custom dropdown — the port of `select()` / `enhanceSelects()` / `syncSelect()` from
   brand.js (~1900-1975).

   Two things about it are load-bearing and are the reason it is a component at all rather
   than a styled <select>:

   1. THE PANEL ESCAPES ITS STACKING CONTEXT. `.card` uses `backdrop-filter`, and a
      backdrop-filtered element is a stacking context, which TRAPS its children's z-index
      inside it. No z-index on the menu can lift it over the next card — that is what the
      reported "workspace picker hidden behind Knowledge base health" bug was. brand.js
      escapes by raising the whole host card (`.card.cq-sel-open { z-index:70 }`) while a
      menu inside it is open; that works, but it only reaches ONE ancestor, so a select
      inside a modal, a `.table-wrap` (which clips with `overflow`) or any other transformed
      or filtered wrapper is still stuck. Here the open panel is PORTALLED onto <body> and
      positioned `fixed` from the trigger's rect, which has no ancestor to be trapped by or
      clipped against at all. `.card.cq-sel-open` is therefore unused by this component; it
      stays in globals.css because the un-ported legacy pages still rely on it.

      The price of a portal is that the panel no longer moves with the page, so it is
      repositioned on scroll (capturing, so a scrolling ancestor counts) and on resize, and
      "click outside" has to test the panel as well as the trigger.

   2. `disabled` ACTUALLY DISABLES IT. The legacy control is a <button> painted over a
      still-live native <select>, and nothing propagated the native's disabled state to the
      button — so a read-only workspace rendered a picker you could still open and change.
      Here the trigger IS the control and carries the real `disabled` attribute, which also
      takes it out of the tab order and kills its key handling.

   Controlled, always: `value` in, `onChange` out, no internal copy of the selection. */

export interface Option {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps {
  value: string;
  onChange: (v: string) => void;
  options: Option[];
  id?: string;
  disabled?: boolean;
  ariaLabel?: string;
  placeholder?: string;
  /* Presentational escape hatches, both optional and both on the WRAPPER (`.cq-select`),
     which is what the legacy pages sized: `<select style="min-width:190px">` is a real
     pattern in the code being ported and there is otherwise nowhere to put it. */
  className?: string;
  style?: CSSProperties;
}

const GAP = 6; // trigger-to-panel gap, matching `top:calc(100% + 6px)` in globals.css
const EDGE = 8; // keep-off-the-viewport-edge margin, as in brand.js's `- 8` checks
const CAP = 300; // the sheet's max-height, restated so the flip can be decided in JS
const TYPE_MS = 700; // typeahead buffer lifetime

export function Select({
  value,
  onChange,
  options,
  id,
  disabled,
  ariaLabel,
  placeholder,
  className,
  style,
}: SelectProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const typed = useRef({ s: '', at: 0 });

  const uid = useId();
  const panelId = `${uid}-panel`;
  const optId = (i: number) => `${uid}-opt-${i}`;

  const selected = options.findIndex(o => o.value === value);
  const label = selected >= 0 ? options[selected].label : (placeholder ?? '');
  // An options list that shrank while the menu was open must not leave the cursor past its end.
  const cursor = active >= 0 && active < options.length ? active : -1;

  /* ---- selection helpers (index arithmetic, disabled options skipped) ---- */

  const edge = useCallback(
    (dir: 1 | -1) => {
      for (let i = dir === 1 ? 0 : options.length - 1; i >= 0 && i < options.length; i += dir) {
        if (!options[i].disabled) return i;
      }
      return -1;
    },
    [options],
  );

  const step = useCallback(
    (from: number, dir: 1 | -1) => {
      if (!options.length) return -1;
      // Stops at the ends rather than wrapping, like a native listbox.
      for (let i = from + dir; i >= 0 && i < options.length; i += dir) {
        if (!options[i].disabled) return i;
      }
      return from >= 0 && from < options.length && !options[from].disabled ? from : edge(dir);
    },
    [options, edge],
  );

  /** Native-select typeahead: letters jump, and repeating ONE letter cycles the options that
      start with it. Returns the index to move to, or -1 for no match. */
  const typeahead = useCallback(
    (ch: string, from: number) => {
      const now = Date.now();
      const s = (now - typed.current.at < TYPE_MS ? typed.current.s : '') + ch.toLowerCase();
      typed.current = { s, at: now };
      const repeat = s.length > 1 && [...s].every(c => c === s[0]);
      const needle = repeat ? s[0] : s;
      // A growing buffer refines the CURRENT option (search includes it); a fresh letter, or
      // the same letter again, moves to the next match after it.
      const base = from < 0 ? -1 : from;
      const start = s.length > 1 && !repeat ? base : base + 1;
      for (let n = 0; n < options.length; n++) {
        const i = ((start + n) % options.length + options.length) % options.length;
        const o = options[i];
        if (!o.disabled && o.label.toLowerCase().startsWith(needle)) return i;
      }
      return -1;
    },
    [options],
  );

  /* ---- open / close / commit ---- */

  const openPanel = useCallback(
    (startAt?: number) => {
      if (disabled) return;
      const from = startAt ?? (selected >= 0 && !options[selected].disabled ? selected : edge(1));
      setActive(from);
      setOpen(true);
    },
    [disabled, selected, options, edge],
  );

  const close = useCallback((refocus = true) => {
    setOpen(false);
    setActive(-1);
    typed.current = { s: '', at: 0 };
    if (refocus) triggerRef.current?.focus();
  }, []);

  const commit = useCallback(
    (i: number) => {
      const o = options[i];
      if (!o || o.disabled) return;
      // Fired even when the same option is re-picked, as brand.js does (it assigns
      // `selectedIndex` and dispatches `change` unconditionally) — pages hang "reload this
      // panel" on it and a re-pick is a legitimate way to ask for that.
      onChange(o.value);
      close();
    },
    [options, onChange, close],
  );

  /* ---- positioning ----

     Read the trigger's rect, then place the fixed panel under it — or above it when there is
     not enough room below and more room above. brand.js guessed with constants (`r.bottom +
     300 > innerHeight && r.top > 320`) because an absolutely positioned panel is measured
     against its card; a portalled one can simply be measured, so this asks the real content
     height (`scrollHeight`, which is unaffected by the max-height already on the element and
     so needs no style thrash mid-scroll) and decides from that. Same outcome in the common
     case, correct in the ones the constants were approximating. */
  const position = useCallback(() => {
    const trigger = triggerRef.current;
    const panel = panelRef.current;
    if (!trigger || !panel) return;

    const r = trigger.getBoundingClientRect();
    // The menu is at least as wide as its trigger; globals.css lets content widen it from
    // there and caps it. `min-width:100%` cannot express that once the panel is `fixed`,
    // because its containing block is now the viewport.
    panel.style.minWidth = `${r.width}px`;

    const want = panel.scrollHeight + 2; // + the 1px border top and bottom
    const below = window.innerHeight - r.bottom - GAP - EDGE;
    const above = r.top - GAP - EDGE;
    const up = want > below && above > below;

    const room = Math.max(120, Math.floor(up ? above : below));
    panel.style.maxHeight = `min(${room}px, ${CAP}px, 52vh)`;

    const h = panel.offsetHeight;
    panel.style.top = `${up ? Math.max(EDGE, r.top - GAP - h) : Math.round(r.bottom + GAP)}px`;

    // Anchor to whichever edge keeps the menu on screen — it can be wider than the trigger.
    const w = panel.offsetWidth;
    const left = r.left + w > window.innerWidth - EDGE ? r.right - w : r.left;
    panel.style.left = `${Math.max(EDGE, Math.min(left, window.innerWidth - EDGE - w))}px`;
  }, []);

  // A control that goes read-only under an open menu (the workspace portal flips its pickers
  // when the operator's scope changes) must not leave that menu on screen, still clickable.
  useEffect(() => {
    if (disabled && open) close(false);
  }, [disabled, open, close]);

  useIsoLayoutEffect(() => {
    if (!open) return;
    position();
    const onMove = () => position();
    // Capturing, so scrolling ANY ancestor (a .table-wrap, a modal body) is heard, not just
    // the document — a fixed panel does not travel with the element it points at.
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [open, options, position]);

  // Keep the keyboard cursor visible. Layout effect, and declared after positioning, so the
  // panel is already where it belongs before anything is scrolled into view.
  useIsoLayoutEffect(() => {
    if (!open || cursor < 0) return;
    panelRef.current
      ?.querySelector<HTMLElement>(`[data-i="${cursor}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  }, [open, cursor]);

  /* Outside-click and a safety-net Escape. Focus normally stays on the trigger for the whole
     interaction (options `preventDefault` their mousedown), so its own key handler answers
     Escape — but clicking the panel's scrollbar can drop focus to <body>, and a menu that
     cannot then be dismissed by keyboard is a trap. Capturing pointerdown so the menu closes
     on press rather than release. */
  useIsoLayoutEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (wrapRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      close(false); // the user is already going somewhere else; do not yank focus back
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && document.activeElement !== triggerRef.current) close(false);
    };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open, close]);

  /* ---- keyboard ---- */

  const onKeyDown = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    const k = e.key;
    const printable = k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;

    if (!open) {
      if (k === 'Enter' || k === ' ' || k === 'ArrowDown') {
        e.preventDefault();
        openPanel();
      } else if (k === 'ArrowUp') {
        e.preventDefault();
        openPanel(selected >= 0 ? undefined : edge(-1));
      } else if (printable) {
        // Typing opens the menu on the match instead of committing it blind — the value only
        // ever changes on an explicit Enter or click.
        e.preventDefault();
        const i = typeahead(k, selected);
        openPanel(i >= 0 ? i : undefined);
      }
      return;
    }

    switch (k) {
      case 'Escape':
        e.preventDefault();
        close();
        return;
      case 'Tab':
        close(false); // let focus move on normally
        return;
      case 'Enter':
        e.preventDefault();
        commit(cursor);
        return;
      case ' ':
        // Space belongs to an in-flight typeahead ("north ca…"); otherwise it selects.
        if (Date.now() - typed.current.at < TYPE_MS && typed.current.s) break;
        e.preventDefault();
        commit(cursor);
        return;
      case 'ArrowDown':
        e.preventDefault();
        setActive(step(cursor, 1));
        return;
      case 'ArrowUp':
        e.preventDefault();
        setActive(step(cursor, -1));
        return;
      case 'Home':
        e.preventDefault();
        setActive(edge(1));
        return;
      case 'End':
        e.preventDefault();
        setActive(edge(-1));
        return;
      default:
        break;
    }
    if (printable) {
      e.preventDefault();
      const i = typeahead(k, cursor);
      if (i >= 0) setActive(i);
    }
  };

  /* ---- render ---- */

  const panel = (
    <div
      ref={panelRef}
      id={panelId}
      role="listbox"
      aria-label={ariaLabel}
      className={`cq-select-panel ${styles.panel}`}
    >
      {options.map((o, i) => (
        <div
          key={`${i}:${o.value}`}
          id={optId(i)}
          role="option"
          data-i={i}
          aria-selected={i === selected}
          aria-disabled={o.disabled || undefined}
          className={
            `cq-opt${i === selected ? ' sel' : ''}` +
            `${i === cursor ? ` ${styles.active}` : ''}` +
            `${o.disabled ? ` ${styles.disabled}` : ''}`
          }
          // Keeps focus on the trigger, which is what `aria-activedescendant` requires and
          // what makes the whole interaction survivable without a focus trap.
          onMouseDown={e => e.preventDefault()}
          onMouseEnter={() => { if (!o.disabled) setActive(i); }}
          onClick={() => commit(i)}
        >
          {o.label}
        </div>
      ))}
    </div>
  );

  return (
    <div ref={wrapRef} className={`cq-select${open ? ' open' : ''}${className ? ` ${className}` : ''}`} style={style}>
      <button
        ref={triggerRef}
        type="button"
        id={id}
        className={`cq-select-trigger ${styles.trigger}`}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-activedescendant={open && cursor >= 0 ? optId(cursor) : undefined}
        aria-label={ariaLabel}
        onClick={() => (open ? close() : openPanel())}
        onKeyDown={onKeyDown}
      >
        <span className="cq-select-label">{label}</span>
        <span className="cq-select-arrow" aria-hidden="true">
          ▾
        </span>
      </button>
      {open && typeof document !== 'undefined' ? createPortal(panel, document.body) : null}
    </div>
  );
}

export default Select;
