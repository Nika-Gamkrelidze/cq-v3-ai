'use client';
import type { JSX, ReactNode } from 'react';

/* The two shapes every panel on this page repeats.
   ===============================================
   `admin.html` had one `<div class="msg" id="…Msg">` per card and a pair of lines to paint it
   (`m.className='msg err'; m.textContent = …`). Twelve copies of two lines is a component. */

/** An inline result line under a card's actions: the server's words, or a success key. */
export interface Note {
  kind: 'ok' | 'err';
  text: string;
}

export function Msg({ note }: { note: Note | null }): JSX.Element | null {
  if (!note) return null;
  return <div className={`msg ${note.kind}`}>{note.text}</div>;
}

/** A checkbox with its label to the right, and optionally a ⓘ after it.

    NO inline `width:auto`, although half the legacy markup carries one. `globals.css` already
    sizes a checkbox (`input[type=checkbox] { width:auto }`), and it ALSO widens it to 20px on a
    touch screen — an inline style would beat that media query and leave the phone with the
    desktop's small tap target, which is the opposite of what the override was for. */
export function CheckRow({
  checked, onChange, children, style, disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  children: ReactNode;
  style?: React.CSSProperties;
  disabled?: boolean;
}): JSX.Element {
  return (
    <label className="inline" style={{ gap: 8, ...style }}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={e => onChange(e.target.checked)}
      />
      {children}
    </label>
  );
}

/** The spinner a button wears while its request is in flight, in place of its label. */
export function Busy({ busy, children }: { busy: boolean; children: ReactNode }): JSX.Element {
  return busy ? <span className="spinner" /> : <>{children}</>;
}
