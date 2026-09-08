'use client';
import { useCallback, useEffect, useRef, useState,
  type CSSProperties, type JSX, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { useI18n } from '@/lib/useI18n';

/* Dialogs (brand.js `confirm`, plus the `showModal` helper each page had its own copy of).
   ======================================================================================
   **Never a native `confirm()` or `prompt()`.** That is a rule in `docs/MIGRATION.md`, not a
   preference: a native dialog cannot be translated, cannot be styled, and cannot hold a copy
   button — and a native `prompt()` leaking into the KB bulk-retag flow was a shipped QA bug
   that had to be fixed once already. Everything on this surface is a promise-returning brand
   modal.

   `admin.html`, `tenant.html`, `account.html` and `copilot-demo.html` each grew their OWN
   `showModal(inner, max)` — four copies of the same nine lines, and only `brand.js`'s confirm
   ever bothered with focus. They collapse into one host here, and the port pays off the
   difference: focus moves into the dialog on open and back to the trigger on close, Tab is
   trapped while it is open, and Escape closes it. None of the four legacy copies did any of
   that, so a keyboard user could Tab straight out of an open dialog into the page behind it.

   Like the toasts, the host is a React component mounted once in `app/layout.tsx` rather than
   a node appended to the body and left there: under client-side routing an orphaned backdrop
   survives the navigation that abandoned it. */

interface Dialog {
  id: number;
  render: (close: (value?: unknown) => void) => ReactNode;
  sizing?: CSSProperties;
  resolve: (value: unknown) => void;
}

type Sink = (d: Dialog) => void;

let sink: Sink | null = null;
let seq = 0;
let warned = false;

/** Show an arbitrary dialog. Resolves with whatever `close(value)` was handed — `undefined`
    when it was dismissed with Escape or a backdrop click.

    Callable from anywhere, including outside React, because that is how every legacy call site
    uses it: `if (!(await confirmDialog(…))) return;` inside a plain event handler. */
export function showModal(
  render: (close: (value?: unknown) => void) => ReactNode,
  opts: { maxWidth?: string } = {},
): Promise<unknown> {
  /* 520px and the scroll affordance are `admin.html`'s defaults, which the other three copies
     varied only to widen (`tenant.html` used 680px for the curation diff). A dialog taller
     than the viewport scrolls INSIDE itself; without `max-height` the actions row lands below
     the fold on a laptop and the dialog cannot be dismissed except by Escape. */
  const sizing: CSSProperties = { maxWidth: opts.maxWidth || '520px', maxHeight: '86vh', overflowY: 'auto' };
  return open(render, sizing);
}

/** The confirm dialog: `true` if the visitor confirmed, `false` for every other ending.

    `false` on dismissal is the safe default and the reason this is not just `showModal`:
    every call site reads it as "did they say yes", and each one guards something destructive
    (delete a tenant, rotate a key, unpublish a document). */
export function confirmDialog(
  message: string,
  opts: { ok?: string; cancel?: string; danger?: boolean } = {},
): Promise<boolean> {
  // No `sizing`: `.cq-modal`'s own 400px is narrower than showModal's default and is what the
  // legacy confirm has always been.
  return open(close => (
    <ConfirmBody
      message={message}
      ok={opts.ok}
      cancel={opts.cancel}
      danger={opts.danger !== false}
      close={close}
    />
  )).then(v => v === true);
}

function open(render: Dialog['render'], sizing?: CSSProperties): Promise<unknown> {
  if (!sink) {
    if (!warned) {
      warned = true;
      console.warn('[cq] showModal()/confirmDialog() called with no <ModalHost/> mounted; dismissing.');
    }
    // Resolve rather than reject or hang: a caller that awaits a confirm it never got must
    // fall through to "not confirmed", never into an unhandled rejection or a dead await.
    return Promise.resolve(undefined);
  }
  return new Promise(resolve => { sink!({ id: ++seq, render, sizing, resolve }); });
}

/** The single dialog host. Mounted once, in `app/layout.tsx`. */
export function ModalHost(): JSX.Element {
  const [stack, setStack] = useState<Dialog[]>([]);
  // Read by the Escape handler and by the unmount cleanup, neither of which may capture a
  // stale stack: the listener is registered once and outlives many opens and closes.
  const live = useRef<Dialog[]>([]);
  useEffect(() => { live.current = stack; }, [stack]);

  /* Resolving happens HERE and not inside the `setStack` updater. React invokes an updater
     twice in development to surface impure ones, and settling a promise from inside it is
     exactly that impurity; filtering `live.current` first is also what makes a second close
     for the same dialog (Escape landing in the same tick as a backdrop click) a no-op before
     the re-render has had a chance to remove it. */
  const close = useCallback((id: number, value: unknown) => {
    const hit = live.current.find(d => d.id === id);
    if (!hit) return;
    live.current = live.current.filter(d => d !== hit);
    hit.resolve(value);
    setStack(prev => prev.filter(d => d.id !== id));
  }, []);

  useEffect(() => {
    const unsubscribe = subscribe(d => setStack(prev => [...prev, d]));
    /* ONE document listener for every dialog that will ever open, rather than one per dialog:
       the alternative leaks a listener per open under client-side routing. Bubble phase, not
       capture, so a control INSIDE the dialog that owns Escape itself (an open custom select)
       can handle it and `stopPropagation()` before the dialog does — React dispatches from the
       root container, which is below `document`. */
    const onEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      const top = live.current[live.current.length - 1];
      if (!top) return;
      /* Escape belongs to the innermost open thing. `Select` answers it from a CAPTURE-phase
         document listener without stopping propagation, so without this guard one key press
         would close the dropdown AND the dialog it lives in — and the visitor loses the form
         they were filling in because they dismissed a menu. The open dropdown is read off the
         DOM rather than through a shared registry so neither component has to import the
         other; `.cq-select.open` is a globals.css class, not a private detail. */
      if (document.querySelector('.cq-select.open')) return;
      e.stopPropagation();
      close(top.id, undefined);
    };
    document.addEventListener('keydown', onEsc);
    return () => {
      unsubscribe();
      document.removeEventListener('keydown', onEsc);
      // Anything still open when the host goes away resolves as dismissed. Leaving it would
      // strand every `await confirmDialog(...)` in the app on a promise that can never settle.
      live.current.forEach(d => d.resolve(undefined));
    };
  }, [close]);

  return (
    <>
      {stack.map(d => (
        <Frame key={d.id} dialog={d} onClose={value => close(d.id, value)} />
      ))}
    </>
  );
}

function subscribe(next: Sink): () => void {
  sink = next;
  return () => { if (sink === next) sink = null; };
}

/* Everything focusable a dialog is likely to contain. `[tabindex="-1"]` is excluded on
   purpose: it marks a node that can be focused programmatically but must not appear in the
   Tab order, which is exactly what the dialog container itself is. */
const FOCUSABLE = [
  'a[href]', 'area[href]', 'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])', 'select:not([disabled])', 'textarea:not([disabled])',
  'iframe', 'object', 'embed', '[contenteditable]', '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
    // A hidden button is still a match for the selector and would swallow a Tab stop.
    .filter(el => !el.hasAttribute('hidden') && el.getClientRects().length > 0);
}

function Frame({ dialog, onClose }: { dialog: Dialog; onClose: (value?: unknown) => void }): JSX.Element {
  const bg = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  // Where the pointer went DOWN. A backdrop click that began inside the dialog is a text
  // selection dragged past the edge, not a dismissal — closing there throws away whatever the
  // visitor was in the middle of reading or copying.
  const downOnBackdrop = useRef(false);

  useEffect(() => {
    const restore = document.activeElement as HTMLElement | null;
    const el = box.current;
    if (el) {
      // `[data-autofocus]` first so a dialog can name its own landing spot — the confirm marks
      // its confirm button, which is what brand.js focused.
      const wanted = el.querySelector<HTMLElement>('[data-autofocus]') || focusables(el)[0] || el;
      wanted.focus();
    }
    return () => {
      // Focus goes back where it came from, but only if that element is still in the document:
      // the trigger is often a row button whose table the dialog's own action just re-rendered.
      if (restore && restore.isConnected && typeof restore.focus === 'function') restore.focus();
    };
  }, []);

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab') return;
    const el = box.current;
    if (!el) return;
    const stops = focusables(el);
    if (!stops.length) { e.preventDefault(); return; }   // nothing to move to: stay put
    const first = stops[0], last = stops[stops.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (!active || !el.contains(active)) { e.preventDefault(); first.focus(); return; }
    if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
  };

  return (
    <div
      ref={bg}
      className="cq-modal-bg"
      onMouseDown={e => { downOnBackdrop.current = e.target === e.currentTarget; }}
      onClick={e => { if (e.target === e.currentTarget && downOnBackdrop.current) onClose(undefined); }}
      onKeyDown={onKeyDown}
    >
      <div ref={box} className="cq-modal" role="dialog" aria-modal="true" tabIndex={-1} style={dialog.sizing}>
        {dialog.render(onClose)}
      </div>
    </div>
  );
}

/* The confirm's body. A component rather than markup built inside `confirmDialog` so that the
   Cancel label comes from `useI18n` and re-translates while the dialog is open, instead of
   being frozen at the language the caller happened to be in.

   The message is ALWAYS plain text — tenant names, document titles and usernames flow into it
   — and JSX interpolation escapes it, which is the guarantee brand.js had to get by hand with
   `textContent`. Never render it through `dangerouslySetInnerHTML`. */
function ConfirmBody(
  { message, ok, cancel, danger, close }:
  { message: string; ok?: string; cancel?: string; danger: boolean; close: (value?: unknown) => void },
): JSX.Element {
  const { t } = useI18n();
  return (
    <>
      <p>{message}</p>
      <div className="actions">
        <button type="button" className="ghost" onClick={() => close(false)}>{cancel || t('btn.cancel')}</button>
        <button
          type="button"
          className={danger ? 'danger' : 'primary'}
          data-autofocus
          onClick={() => close(true)}
        >
          {ok || 'Confirm'}
        </button>
      </div>
    </>
  );
}
