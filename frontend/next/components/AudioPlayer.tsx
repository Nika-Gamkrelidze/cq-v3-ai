'use client';
import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState,
} from 'react';
import { duration as clock } from '@/lib/format';

/* The transport bar, and the page-wide Space key that drives it.
   =============================================================
   A port of `brand.js`'s `player()` (the ▶ / seek / clock / ⭳ row) and of the document-level
   Space handler that `timeline.js` grew beside it. The two halves are in ONE file on purpose:
   they are the same feature seen from two components. `timeline.js` owned the keyboard and
   `brand.js` owned the bar, and each carried its own idea of which player the key belonged to
   — two registries meant Space could drive a waveform that had scrolled off screen while the
   visible play bar sat idle. Here there is a single registry, exported, and `Timeline`
   registers with it exactly as this component does.

   ONE PLAYER PER SURFACE. The imperative `load(url)` is the whole point of the ref handle:
   a page mounts this once and re-points it, because mounting a second one leaves a second
   play bar on the page — a shipped bug that was fixed once already (docs/MIGRATION.md,
   "Deliberate decisions"). Nothing here should ever be rendered in a list. */

/* ---------------------------------------------------------------------------------------
   The Space registry — shared with Timeline.
   --------------------------------------------------------------------------------------- */

/** What the page-wide Space key needs to know about a player, whichever component owns it.

    `root` is read at press time rather than captured, so a component may expose it as a
    getter over its own DOM ref (both this file and `Timeline` do). */
export interface SpaceTarget {
  /** The element that has to be on screen for this target to be the one Space drives. */
  root: HTMLElement | null;
  /** False while there is nothing loaded — an empty player never swallows the key. */
  playable(): boolean;
  toggle(): void;
}

const LIVE = new Set<SpaceTarget>();
let lastActive: SpaceTarget | null = null;

/** Does this element already own the Space key?

    Space is the activation key for a button, a link and a `<summary>`, and a literal space in
    a text box — the pasted transcript, a rubric's guidance textarea. Taking it from any of
    them breaks the control, so the player only ever gets the leftovers. */
export function isTypingTarget(el: EventTarget | null): boolean {
  if (!el || !(el instanceof HTMLElement)) return false;
  if (el === document.body || el === document.documentElement) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return tag === 'BUTTON' || tag === 'A' || tag === 'SUMMARY' || el.getAttribute('role') === 'button';
}

function onScreen(el: HTMLElement | null): boolean {
  if (!el || !el.isConnected || el.offsetParent === null) return false;
  const r = el.getBoundingClientRect();
  return r.bottom > 0 && r.top < (window.innerHeight || 0) && r.width > 0;
}

/* With several players on the page (the workbench's summarise tab mounts one per call) the
   key goes to the one the reader is actually looking at: the last one they touched if it is
   still on screen, otherwise the first visible one. */
function pick(): SpaceTarget | null {
  if (lastActive && LIVE.has(lastActive) && lastActive.playable() && onScreen(lastActive.root)) return lastActive;
  for (const inst of LIVE) if (inst.playable() && onScreen(inst.root)) return inst;
  return null;
}

function docSpace(e: KeyboardEvent): void {
  if (e.key !== ' ' && e.key !== 'Spacebar') return;
  if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.defaultPrevented) return;
  if (isTypingTarget(e.target)) return;
  const inst = pick();
  if (!inst) return;
  e.preventDefault();
  lastActive = inst;
  inst.toggle();
}

/** Join the page-wide Space handler; the returned function leaves it again.

    The listener exists only while at least one player does, so a page with no audio on it has
    no keydown handler at all. Registering twice is a no-op, which is what React's dev-mode
    double-mount does. */
export function registerSpaceTarget(target: SpaceTarget): () => void {
  if (!LIVE.size) document.addEventListener('keydown', docSpace);
  LIVE.add(target);
  return () => {
    LIVE.delete(target);
    if (lastActive === target) lastActive = null;
    if (!LIVE.size) document.removeEventListener('keydown', docSpace);
  };
}

/** Mark this the player Space drives — call it when the visitor touches one.

    Someone comparing two calls expects the keyboard to follow their hands, so any pointer
    interaction with a player claims the key. */
export function touchSpaceTarget(target: SpaceTarget | null | undefined): void {
  if (target && LIVE.has(target)) lastActive = target;
}

/* ---------------------------------------------------------------------------------------
   The player.
   --------------------------------------------------------------------------------------- */

export interface PlayerHandle {
  /** Point the player at a new source and start it. See `LoadOptions` for `own`. */
  load(url: string, name?: string, opts?: LoadOptions): void;
  toggle(): void;
  seek(seconds: number): void;
  pause(): void;
}

export interface LoadOptions {
  /** Whether the player owns this URL and should `URL.revokeObjectURL` it when it is replaced
      or the player unmounts. Defaults to true for a `blob:` URL and false for anything else,
      which is right for the common case — a page that fetched an mp3 and handed the object URL
      straight over. Pass `own: false` when the CALLER keeps the URL (admin.html caches TTS
      previews per voice and replays them), or the second play would 404 on a revoked blob. */
  own?: boolean;
}

/* Download glyph as SVG, not a character. It used to be U+2B73 (⭳), which is absent from the
   Georgian and Russian font stacks this app ships — so on a Georgian page the download control
   rendered as an empty tofu box. An inline SVG has no font dependency at all and inherits
   currentColor, so it follows the theme like the text around it. */
function IconDownload() {
  return (
    <svg
      className="cq-i" viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" focusable="false"
      fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
    >
      <path d="M8 2.5v7.5m0 0L5.2 7.2M8 10l2.8-2.8" />
      <path d="M2.8 12.2v.8a1.2 1.2 0 0 0 1.2 1.2h8a1.2 1.2 0 0 0 1.2-1.2v-.8" />
    </svg>
  );
}

export const AudioPlayer = forwardRef<PlayerHandle, { className?: string }>(
  function AudioPlayer({ className }, ref) {
    const rootRef = useRef<HTMLDivElement | null>(null);
    /* A detached `new Audio()` rather than a rendered <audio>: the element then survives every
       re-render and every conditional branch of the page around it, which is the mechanism
       behind "one player per surface". A rendered element is only as stable as the JSX above
       it, and the JSX above it is what keeps changing. */
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const ownedRef = useRef<string | null>(null);
    const targetRef = useRef<SpaceTarget | null>(null);
    const seekingRef = useRef(false);
    const dragRef = useRef<number | null>(null);

    const [src, setSrc] = useState<string | null>(null);
    const [name, setName] = useState('audio');
    const [playing, setPlaying] = useState(false);
    const [pos, setPos] = useState(0);
    const [dur, setDur] = useState(0);
    const [drag, setDrag] = useState<number | null>(null);

    /* Built on first use, never during render: a static export prerenders this file at BUILD
       time, where `Audio` does not exist. */
    const ensure = useCallback(() => {
      let a = audioRef.current;
      if (!a) {
        a = new Audio();
        a.preload = 'metadata';
        a.addEventListener('play', () => setPlaying(true));
        a.addEventListener('pause', () => setPlaying(false));
        const meta = () => setDur(Number.isFinite(a!.duration) ? a!.duration : 0);
        a.addEventListener('loadedmetadata', meta);
        a.addEventListener('durationchange', meta);
        a.addEventListener('timeupdate', () => { if (!seekingRef.current) setPos(a!.currentTime || 0); });
        a.addEventListener('ended', () => { setPlaying(false); setPos(0); });
        audioRef.current = a;
      }
      return a;
    }, []);

    const play = useCallback((a: HTMLAudioElement) => {
      // Autoplay policy rejects this on a page the visitor has not interacted with yet. That
      // is not an error worth showing: the bar is on screen with ▶ waiting to be pressed.
      void a.play().catch(() => {});
    }, []);

    const load = useCallback((url: string, label?: string, opts?: LoadOptions) => {
      const a = ensure();
      const own = opts?.own ?? url.startsWith('blob:');
      const previous = ownedRef.current;
      if (previous && previous !== url) URL.revokeObjectURL(previous);
      ownedRef.current = own ? url : null;
      seekingRef.current = false;
      dragRef.current = null;
      setDrag(null);
      setPos(0);
      setDur(0);
      setName(label || 'audio');
      setSrc(url);
      a.src = url;
      play(a);
    }, [ensure, play]);

    const toggle = useCallback(() => {
      const a = audioRef.current;
      if (!a || !a.src) return;
      touchSpaceTarget(targetRef.current);
      if (a.paused) play(a); else a.pause();
    }, [play]);

    const seek = useCallback((seconds: number) => {
      const a = audioRef.current;
      if (!a || !a.src) return;
      const d = a.duration;
      const to = Number.isFinite(d) && d > 0 ? Math.max(0, Math.min(seconds, d)) : Math.max(0, seconds);
      a.currentTime = to;
      setPos(to);
    }, []);

    const pause = useCallback(() => { audioRef.current?.pause(); }, []);

    useImperativeHandle(ref, () => ({ load, toggle, seek, pause }), [load, toggle, seek, pause]);

    // Join the page-wide Space key for as long as this player exists.
    useEffect(() => {
      const target: SpaceTarget = {
        get root() { return rootRef.current; },
        playable: () => !!(audioRef.current && audioRef.current.src),
        toggle: () => {
          const a = audioRef.current;
          if (!a || !a.src) return;
          if (a.paused) void a.play().catch(() => {}); else a.pause();
        },
      };
      targetRef.current = target;
      return registerSpaceTarget(target);
    }, []);

    /* Teardown. A static export keeps the document alive across client-side navigation, so an
       element left holding a source keeps PLAYING after the page that owned it is gone —
       audible, with no visible control to stop it. Pause, drop the source, and hand back any
       object URL this player adopted. */
    useEffect(() => () => {
      const a = audioRef.current;
      if (a) {
        a.pause();
        a.removeAttribute('src');
        a.load();
      }
      audioRef.current = null;
      if (ownedRef.current) { URL.revokeObjectURL(ownedRef.current); ownedRef.current = null; }
    }, []);

    if (!src) return null;

    const pct = dur > 0 ? Math.max(0, Math.min(100, (pos / dur) * 100)) : 0;
    const time = dur > 0 ? (pos > 0 ? `${clock(pos)} / ${clock(dur)}` : clock(dur)) : '0:00';

    /* The seek bar is a range input, and the legacy one committed on `change` rather than on
       every `input` — you drag the thumb across a 40-minute call and the audio jumps once, at
       the end, instead of stuttering through forty seek requests. React has no separate
       committed event for a range, so the release is caught explicitly and the thumb follows
       `drag` until then. */
    const commit = () => {
      if (!seekingRef.current) return;
      seekingRef.current = false;
      const a = audioRef.current;
      const v = dragRef.current;
      dragRef.current = null;
      setDrag(null);
      if (a && v != null && Number.isFinite(a.duration) && a.duration > 0) {
        a.currentTime = (v / 100) * a.duration;
        setPos(a.currentTime);
      }
    };

    return (
      <div
        className={className ? `cq-player ${className}` : 'cq-player'}
        ref={rootRef}
        onPointerDown={() => touchSpaceTarget(targetRef.current)}
      >
        <button className="cq-play" type="button" aria-label="Play/pause" onClick={toggle}>
          {playing ? '❚❚' : '▶'}
        </button>
        <input
          className="cq-seek" type="range" min={0} max={100} step={0.1}
          value={drag ?? pct}
          aria-label="Seek"
          onChange={(e) => {
            seekingRef.current = true;
            const v = Number(e.target.value);
            dragRef.current = v;
            setDrag(v);
          }}
          onPointerUp={commit}
          onPointerCancel={commit}
          onMouseUp={commit}
          onTouchEnd={commit}
          onKeyUp={commit}
          onBlur={commit}
        />
        <span className="cq-time">{time}</span>
        <a className="cq-dl icon-btn" title="Download" download={name} href={src}>
          <IconDownload />
        </a>
      </div>
    );
  },
);
