'use client';
import { useEffect, useRef, useState, type JSX } from 'react';

/* Toasts (brand.js `toast`).
   =========================
   The transient bottom-right notices every page uses for "Settings saved" and "Something went
   wrong". Same markup, same classes, same timings as the legacy helper — `#cq-toasts` on the
   body, `.cq-toast` per notice, `.leaving` for the 260ms fade-out — so `globals.css` styles
   them without a line of new CSS.

   Two things are different, and both are forced by the move to client-side routing:

   1. **The host is a React component, mounted once in `app/layout.tsx`.** The legacy version
      creates `#cq-toasts` lazily on the body and never removes it, which is harmless only
      because every legacy navigation is a full page load. Under the App Router the page
      changes without the document changing, so anything parked on the body outlives the page
      that made it. Everything here is torn down when the host unmounts, timers included.

   2. **`toast()` is callable from anywhere** — a plain async function in a `lib/` module, an
      event handler, a catch block — not just from inside a component, because that is how
      every legacy call site uses it. So it is a module-level function talking to the mounted
      host through the tiny emitter below, rather than a hook. */

export type ToastKind = 'ok' | 'err' | 'info';

interface Notice {
  id: number;
  message: string;
  kind: ToastKind;
  ms: number;
}

type Sink = (n: Notice) => void;

/* ONE sink, not a set of them. Two mounted hosts would each render every toast, which is a
   duplicated notice on screen rather than a crash — the kind of bug that survives review. The
   layout mounts exactly one; if a second ever mounts it takes over, and the first's
   unsubscribe is a no-op because it is no longer the current sink. That ordering is also what
   makes React's development double-mount (mount → unmount → mount) a non-event. */
let sink: Sink | null = null;
let seq = 0;
let warned = false;

function subscribe(next: Sink): () => void {
  sink = next;
  return () => { if (sink === next) sink = null; };
}

/** Show a toast. Callable from anywhere, including outside React.

    `ms` is not in the shared component contract but is kept from the legacy signature: a few
    call sites hold a message on screen longer than the 3.6s default. With no host mounted this
    logs once and returns — a failed notification must never be the thing that breaks the
    action it was reporting on. */
export function toast(message: string, kind: ToastKind = 'info', ms = 3600): void {
  if (!sink) {
    if (!warned) {
      warned = true;
      console.warn('[cq] toast() called with no <ToastHost/> mounted; message dropped:', message);
    }
    return;
  }
  sink({ id: ++seq, message, kind, ms });
}

/** The single toast host. Mounted once, in `app/layout.tsx`. */
export function ToastHost(): JSX.Element {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [leaving, setLeaving] = useState<number[]>([]);
  // Every pending timer, so a route change (or React's dev double-mount) cannot leave one
  // running against a setState on an unmounted host.
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const pending = timers.current;
    const unsubscribe = subscribe(n => {
      setNotices(prev => [...prev, n]);
      const fade = setTimeout(() => {
        pending.delete(fade);
        setLeaving(prev => [...prev, n.id]);
        const drop = setTimeout(() => {
          pending.delete(drop);
          setNotices(prev => prev.filter(x => x.id !== n.id));
          setLeaving(prev => prev.filter(id => id !== n.id));
        }, 260);                                  // matches the `toastOut` animation
        pending.add(drop);
      }, n.ms);
      pending.add(fade);
    });
    return () => {
      unsubscribe();
      pending.forEach(clearTimeout);
      pending.clear();
    };
  }, []);

  /* `aria-live` is the one addition to the legacy markup. A toast is often the ONLY feedback
     that a save succeeded, and the legacy version announced nothing at all; `polite` queues it
     behind whatever the user is doing rather than interrupting. The region is present from
     first render because a live region only announces nodes added AFTER it is in the
     accessibility tree — creating it together with its first toast announces nothing. */
  return (
    <div id="cq-toasts" role="status" aria-live="polite">
      {notices.map(n => (
        <div
          key={n.id}
          className={
            'cq-toast'
            + (n.kind === 'ok' ? ' ok' : n.kind === 'err' ? ' err' : '')
            + (leaving.includes(n.id) ? ' leaving' : '')
          }
        >
          {n.message}
        </div>
      ))}
    </div>
  );
}
