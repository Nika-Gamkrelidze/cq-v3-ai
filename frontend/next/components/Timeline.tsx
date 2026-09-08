'use client';
/* The call player + findings timeline (design contract §13.1).
   ===========================================================
   One recording, one player, every analyser's findings as a toggleable lane on the same time
   axis, and the transcript underneath carrying the same colours on its left strip. With no
   audio (`src` null or absent) the whole transport disappears and the transcript IS the
   result — that is TEXT MODE, and it is how a pasted transcript is read.

   Ported from `frontend/public/timeline.js` (1,032 lines). Four things about that port are
   deliberate and are the ones a later edit is most likely to undo:

   1. **React owns the DOM; only the canvas and the playhead are written by hand.** The legacy
      module cleared its container and rebuilt everything imperatively, which in React deletes
      nodes React believes it owns. Everything here is JSX — except the waveform canvas (a
      canvas is a drawing surface by definition) and the two values that change 60 times a
      second while a call plays: `--tl-pos` and the current-time clock. Those are written
      through refs, on properties React never renders, precisely so that a playing call does
      not re-render a 400-paragraph transcript every frame. Everything else — spans, marks,
      lanes, the legend, the transcript — is state.

   2. **Every listener, rAF, AudioContext and object URL is torn down.** `reactStrictMode` is
      on, so every effect mounts, unmounts and mounts again in development: anything not
      cleaned up doubles on the spot. The object URL is revoked by the effect that made it, the
      decoding context is closed in a `finally`, and the page-wide Space handler is removed
      when the last timeline on the page unregisters.

   3. **Language comes from `useI18n()`, not from a listener.** `cq:lang` is dispatched on
      `window` by the new stack and was listened for on `document` by the legacy module —
      window events do not propagate down to document, so a hand-rolled listener here would
      silently stop re-translating. Reading `t` from the hook makes a language change an
      ordinary re-render.

   4. **`computePeaks` yields with `MessageChannel`, and only after holding the main thread for
      more than 8ms.** See `timeline/util.ts`; both halves are load-bearing and neither is a
      modernisation opportunity.

   Colour rules (§3) live in `timeline/util.ts::levelColor`, and the CSS in
   `Timeline.module.css` — which is where the legacy module's injected `<style>` went, because
   `globals.css` has never carried a single `.tl-` rule. */

import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import type { CSSProperties, ForwardedRef, KeyboardEvent as ReactKeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { placeTip, type TipPlacement } from '@/lib/tipPlace';
import { useI18n } from '@/lib/useI18n';
import { toast } from '@/components/ui/Toast';
import { registerSpaceTarget, touchSpaceTarget, type SpaceTarget } from '@/components/AudioPlayer';
import s from './Timeline.module.css';
import {
  autoMarks, computePeaks, drawable, formatClock, levelClass, levelColor, markGradient, markOrder,
  normLanes, normMarks, normSegments, num, percentOf, segmentAt, segmentsDuration, spanWidth,
  stripMarks, type Lane, type Level, type Mark, type NormLane, type Peaks,
  type Segment, type Span, type StripMarks,
} from './timeline/util';

export type { Lane, Level, Segment, Span } from './timeline/util';

/** What `markSegments` accepts: the caller's own per-line verdicts for one lane. */
export interface SegmentMark {
  i: number;
  level?: Level;
  score?: number | null;
  title?: string;
}

export interface TimelineHandle {
  /** Replace every lane. Clears the marks set through `markSegments`, exactly as the legacy
      module does — a new set of findings must not leave the previous one's marks behind. */
  setLanes(lanes: Lane[]): void;
  setSpeakerLabels(m: Record<string, string>): void;
  seek(seconds: number): void;
  /** Play if paused, pause if playing. Silent in text mode. */
  toggle(): void;
  /** Is there actually a loaded recording to play? Checked at press time, never at mount. */
  playable(): boolean;
  /* --- beyond the phase-2 contract; the workbench drives all of these ------------------- */
  play(): void;
  pause(): void;
  addLane(lane: Lane): void;
  removeLane(id: string): void;
  toggleLane(id: string, on?: boolean): void;
  highlightSegment(i: number): void;
  markSegments(laneId: string, marks: SegmentMark[]): void;
  currentTime(): number;
  duration(): number;
}

export interface TimelineProps {
  /** The recording. A url is fetched once and the blob feeds both the player and the decoder;
      a `Blob`/`File` is used as it is. **Null or absent means TEXT MODE**: no player, no axis,
      the transcript alone. A url that needs auth headers cannot be given them by a bare
      `<audio src>`, which is what `fetchInit` is for. */
  src?: string | Blob | null;
  segments: Segment[];
  /** The server's duration, used until `<audio>` or the decoder reports a real one. */
  duration?: number | null;
  /** Lanes as a prop are for a caller that renders them declaratively. A caller that drives
      the handle instead (`setLanes`) should leave this UNDEFINED: a defined value is re-applied
      whenever its identity changes, which would overwrite what the handle just set. */
  lanes?: Lane[];
  speakerLabels?: Record<string, string>;
  onSeek?: (seconds: number) => void;
  /* --- beyond the phase-2 contract ------------------------------------------------------ */
  onSpanClick?: (lane: Lane, span: Span) => void;
  onSegment?: (index: number) => void;
  /** Passed to `fetch()` when `src` is a url — the recordings endpoint is authenticated. */
  fetchInit?: RequestInit;
  /** Download filename; defaults to the File's own name, then the url's last segment. */
  filename?: string;
}

/* ---- Space toggles playback anywhere on the page ---------------------------------------
   A reviewer listens with one hand on the keyboard and the other on a notepad; making them
   click into the waveform first to regain Space is friction in the one place the product is
   meant to feel like a player. So the key is handled on the DOCUMENT, not just the widget.

   The registry itself lives in `AudioPlayer.tsx` and is SHARED, which is the fix for a real
   split: `timeline.js` owned the document keydown handler and `brand.js`'s player owned the
   play bar, each with its own notion of which player the key belonged to. Two registries mean
   Space can drive a waveform that has scrolled off screen while the visible play bar sits
   idle — so there is exactly one here, and this component joins it like any other player. */

/* ---------------- small DOM helpers ---------------- */

/** Scroll `el` into `box`'s visible area WITHOUT moving the page — which `scrollIntoView()`
    does, yanking the whole workbench around every time the playhead crosses a turn. */
function scrollWithin(box: HTMLElement | null, el: HTMLElement | null): void {
  if (!box || !el) return;
  const top = el.offsetTop - box.offsetTop;
  const bottom = top + el.offsetHeight;
  if (top < box.scrollTop + 8) box.scrollTop = Math.max(0, top - 8);
  else if (bottom > box.scrollTop + box.clientHeight - 8) box.scrollTop = bottom - box.clientHeight + 8;
}

/** Custom properties are not in `CSSProperties`; this is the one cast, in one place. */
const vars = (o: Record<string, string>): CSSProperties => o as CSSProperties;

const DOWNLOAD_ICON = (
  <svg className="cq-i" viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" focusable="false"
    fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 2.5v7.5m0 0L5.2 7.2M8 10l2.8-2.8" />
    <path d="M2.8 12.2v.8a1.2 1.2 0 0 0 1.2 1.2h8a1.2 1.2 0 0 0 1.2-1.2v-.8" />
  </svg>
);

const RATES = [1, 1.5, 2];

interface TipState {
  el: HTMLElement;
  label: string;
  detail: string;
}

function TimelineImpl(props: TimelineProps, ref: ForwardedRef<TimelineHandle>) {
  const { src = null, duration = null } = props;
  const { t } = useI18n();
  const textMode = src == null;

  const segments = useMemo(() => normSegments(props.segments), [props.segments]);

  /* ---- state ---- */
  const [lanes, setLaneState] = useState<NormLane[]>(() => normLanes(props.lanes));
  const [labels, setLabels] = useState<Record<string, string>>(() => ({ ...(props.speakerLabels || {}) }));
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [marks, setMarks] = useState<ReadonlyMap<string, ReadonlyMap<number, Mark>>>(() => new Map());
  const [cur, setCur] = useState(-1);
  const [hl, setHl] = useState(-1);
  const [audioDur, setAudioDur] = useState(0);
  const [decodedDur, setDecodedDur] = useState(0);
  const [objUrl, setObjUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [busy, setBusy] = useState(!textMode);
  const [mediaFail, setMediaFail] = useState(false);   // the file cannot be demuxed
  const [loadFail, setLoadFail] = useState(false);     // the file never arrived
  const [noteKey, setNoteKey] = useState<string | null>(null);
  const [tip, setTip] = useState<TipState | null>(null);
  const [tipPos, setTipPos] = useState<TipPlacement | null>(null);
  const [mounted, setMounted] = useState(false);

  /* ---- refs: what the 60fps path and the stable callbacks read ---- */
  const rootRef = useRef<HTMLDivElement>(null);
  const waveRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const txRef = useRef<HTMLDivElement>(null);
  const clockRef = useRef<HTMLElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  const posRef = useRef(0);
  const curRef = useRef(-1);
  const dRef = useRef(0);
  const segRef = useRef<Segment[]>(segments);
  const ariaSecRef = useRef(-1);
  const draggingRef = useRef(false);
  const pendingSeekRef = useRef<number | null>(null);
  const peaksRef = useRef<Peaks | null>(null);
  const flatRef = useRef(false);
  const mediaFailRef = useRef(false);
  const objUrlRef = useRef<string | null>(null);
  const tickRaf = useRef(0);
  const drawRaf = useRef(0);

  // One ref holding the latest callbacks, so nothing that reads them has to list them as a
  // dependency. That matters most for the source effect: `t` in its dependency list would
  // re-fetch and re-decode the whole recording every time the visitor switches language.
  const liveRef = useRef({ ...props, t });
  useLayoutEffect(() => { liveRef.current = { ...props, t }; });

  /* ---- derived ---- */
  const speakers = useMemo(() => {
    const out: string[] = [];
    for (const seg of segments) if (!out.includes(seg.speaker)) out.push(seg.speaker);
    return out;
  }, [segments]);

  /* Duration precedence: what <audio> reports, else the decoded buffer, else the server, else
     the last segment. The server's number is a guess (it is whatever the uploader claimed);
     the file itself is the authority as soon as it can speak for itself. */
  const D = audioDur || decodedDur || num(duration) || segmentsDuration(segments) || 0;

  const auto = useMemo(() => {
    const m = new Map<string, ReadonlyMap<number, Mark>>();
    for (const lane of lanes) m.set(lane.id, autoMarks(lane));
    return m;
  }, [lanes]);

  const order = useMemo(() => markOrder(lanes, marks), [lanes, marks]);

  const strips = useMemo(() => {
    const m = new Map<number, StripMarks>();
    for (const seg of segments) m.set(seg.i, stripMarks(seg.i, order, hidden, marks, auto));
    return m;
  }, [segments, order, hidden, marks, auto]);

  const spClass = useCallback(
    (sp: string) => 's' + (Math.max(0, speakers.indexOf(sp)) % 6),
    [speakers],
  );
  const spLabel = useCallback(
    (sp: string) => labels[sp] || t('tl.speaker', { n: Math.max(0, speakers.indexOf(sp)) + 1 }),
    [labels, speakers, t],
  );

  /* ---- the 60fps path: written by hand, on purpose (see the header) ---- */
  const setCurrent = useCallback((i: number) => {
    if (i === curRef.current) return;
    curRef.current = i;
    setCur(i);
    liveRef.current.onSegment?.(i);
  }, []);

  const writePos = useCallback((v: number) => {
    posRef.current = v;
    const d = dRef.current;
    rootRef.current?.style.setProperty('--tl-pos', percentOf(v, d));
    if (clockRef.current) clockRef.current.textContent = formatClock(v);
    const wave = waveRef.current;
    const sec = Math.round(v);
    if (wave && sec !== ariaSecRef.current) {
      ariaSecRef.current = sec;
      wave.setAttribute('aria-valuenow', String(sec));
      wave.setAttribute('aria-valuetext', `${formatClock(v)} / ${formatClock(d)}`);
    }
    // Text mode has no playhead, so nothing there advances the current line: it changes only
    // when the reader clicks a paragraph, exactly as in the legacy module.
    if (!textMode) setCurrent(segmentAt(segRef.current, v));
  }, [setCurrent, textMode]);

  // Every render: the refs the imperative path reads, then re-place the playhead — the
  // percentage moves whenever the duration does, without the position itself changing.
  useLayoutEffect(() => {
    segRef.current = segments;
    dRef.current = D;
    ariaSecRef.current = -1;
    writePos(posRef.current);
  }, [segments, D, writePos]);

  /* ---- seeking ---- */
  const seek = useCallback((value: number) => {
    if (textMode) return;
    const audio = audioRef.current;
    const d = dRef.current;
    let v = Math.max(0, Number(value) || 0);
    if (d > 0) v = Math.min(d, v);
    if (audio && audio.readyState >= 1) {
      try { audio.currentTime = v; } catch { pendingSeekRef.current = v; }
    } else {
      pendingSeekRef.current = v;
    }
    writePos(v);
    liveRef.current.onSeek?.(v);
  }, [textMode, writePos]);

  const play = useCallback(() => {
    const audio = audioRef.current;
    if (audio && audio.src) void audio.play().catch(() => { /* autoplay policy, or a dead src */ });
  }, []);
  const pause = useCallback(() => { audioRef.current?.pause(); }, []);
  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) play(); else audio.pause();
  }, [play]);

  const goSegment = useCallback((i: number) => {
    const seg = segRef.current.find(x => x.i === i);
    if (!seg) return;
    if (seg.start !== null && !textMode) seek(seg.start);
    else setCurrent(i);
  }, [seek, setCurrent, textMode]);

  /* ---- lanes ---- */
  const setLanes = useCallback((next: Lane[]) => {
    setLaneState(normLanes(next));
    setMarks(new Map());
  }, []);

  const addLane = useCallback((lane: Lane) => {
    if (!lane || lane.id == null) return;
    setLaneState(prev => {
      const norm = normLanes([lane]);
      const idx = prev.findIndex(l => l.id === norm[0].id);
      if (idx < 0) return [...prev, norm[0]];
      const out = prev.slice();
      out[idx] = norm[0];                       // same id → replace in place, keeping its row
      return out;
    });
  }, []);

  const removeLane = useCallback((id: string) => {
    const key = String(id);
    setLaneState(prev => prev.filter(l => l.id !== key));
    setMarks(prev => { if (!prev.has(key)) return prev; const m = new Map(prev); m.delete(key); return m; });
    setHidden(prev => { if (!prev.has(key)) return prev; const h = new Set(prev); h.delete(key); return h; });
  }, []);

  const toggleLane = useCallback((id: string, on?: boolean) => {
    const key = String(id);
    setHidden(prev => {
      const next = new Set(prev);
      const show = on === undefined ? next.has(key) : on;
      if (show) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  const markSegments = useCallback((laneId: string, list: SegmentMark[]) => {
    const key = String(laneId);
    setMarks(prev => { const m = new Map(prev); m.set(key, normMarks(list)); return m; });
  }, []);

  const highlightSegment = useCallback((i: number) => {
    setHl(Number.isInteger(i) ? i : -1);
  }, []);

  const setSpeakerLabels = useCallback((map: Record<string, string>) => {
    setLabels({ ...(map || {}) });
  }, []);

  /* Props that mirror imperative setters are applied only when the CALLER changes them: a
     re-applied `lanes` prop on every parent render would overwrite whatever `setLanes` /
     `markSegments` just put there. A caller driving the handle leaves the prop undefined. */
  const seenLanes = useRef(props.lanes);
  useEffect(() => {
    if (props.lanes === undefined || seenLanes.current === props.lanes) return;
    seenLanes.current = props.lanes;
    setLanes(props.lanes);
  }, [props.lanes, setLanes]);

  const seenLabels = useRef(props.speakerLabels);
  useEffect(() => {
    if (props.speakerLabels === undefined || seenLabels.current === props.speakerLabels) return;
    seenLabels.current = props.speakerLabels;
    setSpeakerLabels(props.speakerLabels);
  }, [props.speakerLabels, setSpeakerLabels]);

  /* ---- the waveform canvas ---- */
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const wave = waveRef.current;
    const root = rootRef.current;
    if (!canvas || !wave || !root) return;
    const w = wave.clientWidth;
    const h = wave.clientHeight;
    if (!w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    // Read the ink from the live cascade rather than a constant: this is also how a theme
    // switch repaints, via the MutationObserver below.
    const ink = (getComputedStyle(root).getPropertyValue('--mist') || '#b8c6d0').trim();
    const mid = h / 2;
    ctx.fillStyle = ink;

    const peaks = peaksRef.current;
    if (!peaks) {                                 // flat bar: nothing decoded (yet, or ever)
      ctx.globalAlpha = flatRef.current ? .55 : .3;
      ctx.fillRect(0, mid - 1, w, 2);
      ctx.globalAlpha = 1;
      return;
    }
    const { mins, maxs, gain } = peaks;
    const n = mins.length;
    const amp = mid - 3;
    ctx.globalAlpha = .85;
    const step = 3;
    const bw = 2;                                 // 2px bars with a 1px gap
    for (let x = 0; x < w; x += step) {
      const b0 = Math.floor(x / w * n);
      const b1 = Math.max(b0 + 1, Math.floor((x + step) / w * n));
      let lo = 1;
      let hi = -1;
      for (let b = b0; b < b1 && b < n; b++) {
        if (mins[b] < lo) lo = mins[b];
        if (maxs[b] > hi) hi = maxs[b];
      }
      if (hi < lo) { lo = 0; hi = 0; }
      const top = mid - Math.max(0, hi) * gain * amp;
      const bot = mid - Math.min(0, lo) * gain * amp;
      ctx.fillRect(x, Math.min(top, mid - .5), bw, Math.max(1, bot - top));
    }
    ctx.globalAlpha = 1;
  }, []);

  const scheduleDraw = useCallback(() => {
    if (drawRaf.current) return;
    drawRaf.current = requestAnimationFrame(() => { drawRaf.current = 0; draw(); });
  }, [draw]);

  const flatBar = useCallback((key: string | null) => {
    flatRef.current = true;
    setBusy(false);
    // "cannot be played" outranks "no waveform": both fire for a file nothing can read.
    let msg = key;
    if (msg === 'tl.playfail') mediaFailRef.current = true;
    else if (mediaFailRef.current) msg = null;
    if (msg) setNoteKey(msg);
    draw();
  }, [draw]);

  /* ---- decode + load ---- */
  const decode = useCallback(async (blob: Blob, cancelled: () => boolean) => {
    try {
      const ab = await blob.arrayBuffer();
      if (cancelled()) return;
      const w = window as unknown as {
        OfflineAudioContext?: typeof OfflineAudioContext;
        webkitOfflineAudioContext?: typeof OfflineAudioContext;
        AudioContext?: typeof AudioContext;
        webkitAudioContext?: typeof AudioContext;
      };
      const OAC = w.OfflineAudioContext || w.webkitOfflineAudioContext;
      const AC = w.AudioContext || w.webkitAudioContext;
      // An OfflineAudioContext decodes without the autoplay-policy warning a live one logs.
      const ctx: (BaseAudioContext & { close?: () => Promise<void> }) | null =
        OAC ? new OAC(1, 1, 44100) : (AC ? new AC() : null);
      if (!ctx) throw new Error('Web Audio unavailable');
      let buffer: AudioBuffer;
      try {
        buffer = await new Promise<AudioBuffer>((res, rej) => {
          const p = ctx.decodeAudioData(ab, res, rej);
          if (p && typeof p.then === 'function') p.then(res, rej);
        });
      } finally {
        // ALWAYS, and on the failure path too. Browsers cap live audio contexts at around six
        // per document; leaking one per recording kills playback for the rest of the session
        // with no error — the exact trap the audio editor is still sitting in.
        if (typeof ctx.close === 'function') void ctx.close().catch(() => {});
      }
      if (cancelled()) return;
      const chans: Float32Array[] = [];
      for (let c = 0; c < buffer.numberOfChannels; c++) chans.push(buffer.getChannelData(c));
      const peaks = await computePeaks(chans, buffer.length, 800, cancelled);
      if (!peaks || cancelled()) return;
      peaksRef.current = peaks;
      if (Number.isFinite(buffer.duration) && buffer.duration > 0) setDecodedDur(buffer.duration);
      setBusy(false);
      // Synchronous on purpose: rAF is throttled in a background tab, and the peaks would then
      // wait for the reader to come back before the waveform appeared.
      draw();
    } catch {
      flatBar('tl.decodefail');
    }
  }, [draw, flatBar]);

  /* A NEW recording on a mounted timeline starts from nothing. The legacy module could not hit
     this — a page threw the whole widget away and built another — so everything a previous file
     established (its duration, its "cannot be played" note, its peaks) would otherwise be
     described as belonging to the new one. Layout effect, so it lands before the load below. */
  useLayoutEffect(() => {
    peaksRef.current = null;
    flatRef.current = false;
    mediaFailRef.current = false;
    pendingSeekRef.current = null;
    ariaSecRef.current = -1;
    posRef.current = 0;
    setAudioDur(0);
    setDecodedDur(0);
    setMediaFail(false);
    setLoadFail(false);
    setNoteKey(null);
    setBusy(src != null);
    writePos(0);
  }, [src, writePos]);

  useEffect(() => {
    if (src == null) return;
    let cancelled = false;
    let url: string | null = null;
    const isCancelled = () => cancelled;

    (async () => {
      try {
        let blob: Blob;
        if (typeof src === 'string') {
          const r = await fetch(src, liveRef.current.fetchInit);
          if (!r.ok) throw new Error('HTTP ' + r.status);
          blob = await r.blob();
        } else {
          blob = src;
        }
        if (cancelled) return;
        if (!(blob instanceof Blob)) throw new Error('unsupported src');
        // <audio> trusts the blob's MIME: an endpoint that says application/octet-stream would
        // be refused by some engines, so leave the type blank there and let the demuxer sniff.
        if (!/^(audio|video)\//i.test(blob.type || '')) blob = new Blob([blob], { type: '' });
        url = URL.createObjectURL(blob);
        objUrlRef.current = url;
        setObjUrl(url);
        void decode(blob, isCancelled);
      } catch {
        if (cancelled) return;
        toast(liveRef.current.t('tl.loadfail'), 'err');
        setLoadFail(true);
        flatBar(null);
      }
    })();

    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
      objUrlRef.current = null;
      peaksRef.current = null;
      setObjUrl(null);
    };
  }, [src, decode, flatBar]);

  /* ---- observers: size and theme both change what the canvas should contain ---- */
  useEffect(() => {
    if (textMode) return;
    const wave = waveRef.current;
    draw();
    const ro = wave && typeof ResizeObserver === 'function' ? new ResizeObserver(scheduleDraw) : null;
    if (ro && wave) ro.observe(wave);
    // A theme switch changes --mist, which the canvas has already baked into its pixels.
    const mo = typeof MutationObserver === 'function' ? new MutationObserver(() => draw()) : null;
    mo?.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => {
      ro?.disconnect();
      mo?.disconnect();
      if (drawRaf.current) { cancelAnimationFrame(drawRaf.current); drawRaf.current = 0; }
    };
  }, [textMode, draw, scheduleDraw]);

  /* ---- playback rate, applied to the element React does not own this property of ---- */
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.playbackRate = rate;
    audio.defaultPlaybackRate = rate;
  }, [rate, objUrl]);

  /* ---- page-wide Space ---- */
  const spaceRef = useRef<SpaceTarget | null>(null);
  if (!spaceRef.current) {
    spaceRef.current = {
      // A getter, not a captured node: the registry reads it at PRESS time, and at mount there
      // is nothing to capture anyway.
      get root() { return rootRef.current; },
      // Likewise checked at press time: a timeline in the summarise tab whose audio has not
      // loaded yet, or a text-mode one with no player at all, simply is not a candidate.
      playable: () => !!(audioRef.current && objUrlRef.current),
      toggle: () => {
        const audio = audioRef.current;
        if (!audio) return;
        if (audio.paused) void audio.play().catch(() => {}); else audio.pause();
      },
    };
  }
  useEffect(() => registerSpaceTarget(spaceRef.current!), []);

  /* ---- unmount: stop the clock, drop the media ---- */
  useEffect(() => () => {
    if (tickRaf.current) cancelAnimationFrame(tickRaf.current);
    tickRaf.current = 0;
    const audio = audioRef.current;
    if (audio) { try { audio.pause(); } catch { /* already gone */ } }
  }, []);

  useEffect(() => { setMounted(true); }, []);

  /* ---- the current line, and the highlighted one, scrolled into view ---- */
  useEffect(() => {
    if (cur < 0) return;
    const box = txRef.current;
    scrollWithin(box, box?.querySelector<HTMLElement>(`.tl-p[data-i="${cur}"]`) ?? null);
  }, [cur]);

  useEffect(() => {
    if (hl < 0) return;
    const box = txRef.current;
    scrollWithin(box, box?.querySelector<HTMLElement>(`.tl-p[data-i="${hl}"]`) ?? null);
  }, [hl]);

  /* ---- the tip: measured, placed, then shown ---- */
  useLayoutEffect(() => {
    if (!tip) { setTipPos(null); return; }
    const measure = () => {
      const box = tipRef.current;
      if (!box) return;
      const p = placeTip(
        tip.el.getBoundingClientRect(),
        { width: box.offsetWidth, height: box.offsetHeight },
        window.innerWidth, window.innerHeight,
      );
      // null means the trigger is gone or off screen — a bubble pointing at nothing is worse
      // than no bubble, so the whole tip goes rather than being left floating.
      if (!p) setTip(null); else setTipPos(p);
    };
    measure();
    let raf = 0;
    const reflow = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; measure(); });
    };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setTip(null); };
    window.addEventListener('scroll', reflow, true);
    window.addEventListener('resize', reflow);
    document.addEventListener('keydown', esc, true);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('scroll', reflow, true);
      window.removeEventListener('resize', reflow);
      document.removeEventListener('keydown', esc, true);
    };
  }, [tip]);

  const showTip = useCallback((el: HTMLElement, label: string, detail: string) => {
    if (!label && !detail) return;
    setTip(prev => (prev && prev.el === el ? prev : { el, label, detail }));
  }, []);
  const hideTip = useCallback((el: HTMLElement) => {
    setTip(prev => (prev && prev.el !== el ? prev : null));
  }, []);

  /* ---- the handle ---- */
  useImperativeHandle(ref, (): TimelineHandle => ({
    setLanes,
    setSpeakerLabels,
    seek,
    toggle,
    playable: () => !!(audioRef.current && objUrlRef.current),
    play,
    pause,
    addLane,
    removeLane,
    toggleLane,
    highlightSegment,
    markSegments,
    currentTime: () => posRef.current,
    duration: () => dRef.current,
  }), [setLanes, setSpeakerLabels, seek, toggle, play, pause, addLane, removeLane, toggleLane,
    highlightSegment, markSegments]);

  /* ---- audio element events ---- */
  const onLoadedMetadata = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (Number.isFinite(audio.duration) && audio.duration > 0) setAudioDur(audio.duration);
    if (pendingSeekRef.current !== null) {
      const v = pendingSeekRef.current;
      pendingSeekRef.current = null;
      try { audio.currentTime = v; } catch { /* not seekable yet */ }
    }
  };

  const onDurationChange = () => {
    const audio = audioRef.current;
    if (audio && Number.isFinite(audio.duration) && audio.duration > 0) setAudioDur(audio.duration);
  };

  const tick = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || audio.paused) { tickRaf.current = 0; return; }
    writePos(audio.currentTime);
    tickRaf.current = requestAnimationFrame(tick);
  }, [writePos]);

  /* The blob arrived but the media element cannot demux it: the waveform note alone left a
     live-looking play button that did nothing. Same treatment as a failed fetch — play off, a
     note saying so — except the file is here, so the download stays. Seeking still works
     (§13.1): the playhead, the segments and the transcript are driven by state, not by audio. */
  const onError = () => {
    if (!objUrlRef.current) return;               // src cleared on teardown, not a real failure
    setMediaFail(true);
    flatBar('tl.playfail');
  };

  const seekAt = (clientX: number) => {
    const wave = waveRef.current;
    if (!wave) return;
    const r = wave.getBoundingClientRect();
    if (!r.width || !dRef.current) return;
    seek((clientX - r.left) / r.width * dRef.current);
  };

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (textMode) return;
    const tag = (e.target as HTMLElement).tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.altKey || e.ctrlKey || e.metaKey) return;
    // Space is handled document-wide (see docSpace); leave it alone here so the two do not
    // both fire and cancel each other out.
    if (e.key === ' ' || e.key === 'Spacebar') return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); seek(posRef.current - 5); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); seek(posRef.current + 5); }
    else if (e.key === 'Home') { e.preventDefault(); seek(0); }
    else if (e.key === 'End') { e.preventDefault(); seek(dRef.current); }
  };

  const downloadName = props.filename
    || (typeof src === 'object' && src && 'name' in src ? String((src as File).name) : '')
    || (typeof src === 'string' ? src.split(/[?#]/)[0].split('/').pop() || '' : '')
    || 'recording';

  /* ---------------- render ---------------- */
  return (
    <div
      ref={rootRef}
      className={`${s.host} tl${textMode ? ' tl-text' : ''}`}
      onKeyDown={onKeyDown}
      // Touching a player makes it the one Space drives, which is what someone comparing two
      // calls side by side expects.
      onPointerDownCapture={() => touchSpaceTarget(spaceRef.current)}
    >
      {!textMode && (
        <>
          <audio
            ref={audioRef}
            preload="metadata"
            src={objUrl ?? undefined}
            onLoadedMetadata={onLoadedMetadata}
            onDurationChange={onDurationChange}
            onTimeUpdate={() => { if (!draggingRef.current) writePos(audioRef.current?.currentTime ?? 0); }}
            onSeeked={() => writePos(audioRef.current?.currentTime ?? 0)}
            onPlay={() => { setPlaying(true); if (!tickRaf.current) tickRaf.current = requestAnimationFrame(tick); }}
            onPause={() => setPlaying(false)}
            onEnded={() => { setPlaying(false); writePos(audioRef.current?.duration || posRef.current); }}
            onError={onError}
            onRateChange={() => { const r = audioRef.current?.playbackRate; if (r) setRate(r); }}
          />

          <div className="tl-transport">
            <button
              type="button"
              className="cq-play tl-play"
              aria-label={t(playing ? 'tl.pause' : 'tl.play')}
              disabled={mediaFail || loadFail}
              onClick={toggle}
            >
              {playing ? '❚❚' : '▶'}
            </button>
            <span className="tl-time">
              {/* The clock is written through the ref, not rendered: it changes 60 times a
                  second while a call plays. The `0:00` below is a CONSTANT child, so React
                  writes it once at mount and never diffs it again — it exists so the exported
                  HTML shows a clock before hydration, exactly as the legacy skeleton did. */}
              <b className="tl-cur" ref={clockRef}>0:00</b>
              {' / '}
              <span className="tl-dur">{formatClock(D)}</span>
            </span>
            <div className="tl-speed" role="group" aria-label={t('tl.speed')}>
              {RATES.map(r => (
                <button
                  key={r}
                  type="button"
                  className={rate === r ? 'on' : undefined}
                  disabled={mediaFail}
                  onClick={() => setRate(r)}
                >
                  {`${r}×`}
                </button>
              ))}
            </div>
            <a
              className="tl-dl cq-dl icon-btn"
              href={objUrl ?? '#'}
              aria-disabled={objUrl ? undefined : 'true'}
              download={downloadName}
              title={t('tl.download')}
              aria-label={t('tl.download')}
            >
              {DOWNLOAD_ICON}
            </a>
          </div>

          <div
            ref={waveRef}
            className={`tl-wave${busy ? ' tl-busy' : ''}`}
            role="slider"
            tabIndex={0}
            aria-valuemin={0}
            aria-valuemax={Math.round(D)}
            // Constant, for the same reason as the clock above: a slider without a valuenow is
            // broken for a screen reader, and React never rewrites an attribute whose rendered
            // value has not changed — so the imperative updates in writePos survive re-renders.
            aria-valuenow={0}
            aria-label={t('tl.position')}
            title={t('tl.keyhint')}
            data-busy={t('tl.loading')}
            onPointerDown={e => {
              if (e.pointerType === 'mouse' && e.button !== 0) return;
              draggingRef.current = true;
              try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* older engines */ }
              seekAt(e.clientX);
            }}
            onPointerMove={e => { if (draggingRef.current) seekAt(e.clientX); }}
            onPointerUp={e => {
              if (!draggingRef.current) return;
              draggingRef.current = false;
              try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
            }}
            onPointerCancel={e => {
              if (!draggingRef.current) return;
              draggingRef.current = false;
              try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
            }}
          >
            <canvas className="tl-canvas" ref={canvasRef} aria-hidden="true" />
            <div className="tl-played" />
            <div className="tl-playhead" />
          </div>

          <div className="tl-note" hidden={!noteKey}>{noteKey ? t(noteKey) : ''}</div>

          <div className="tl-rows">
            <div className="tl-row tl-speakers">
              <div className="tl-rowname"><span>{t('tl.speakers')}</span></div>
              <div className="tl-track">
                {segments.map((seg, k) => {
                  if (seg.start === null || seg.end === null) return null;
                  if (!(D > 0)) return null;
                  return (
                    <div
                      key={k}
                      className={`tl-seg ${spClass(seg.speaker)}${seg.i === cur ? ' now' : ''}${seg.i === hl ? ' hl' : ''}`}
                      data-i={seg.i}
                      title={`${spLabel(seg.speaker)} · ${formatClock(seg.start)}–${formatClock(seg.end)}`}
                      style={{
                        left: percentOf(seg.start, D),
                        width: Math.max(0, (seg.end - seg.start) / D * 100).toFixed(3) + '%',
                      }}
                      onClick={() => goSegment(seg.i)}
                    />
                  );
                })}
              </div>
            </div>

            {lanes.map(lane => (
              <div key={lane.id} className="tl-row tl-lane" data-lane={lane.id} hidden={hidden.has(lane.id)}>
                <div className="tl-rowname" title={lane.name}>
                  <i className="tl-sw" style={lane.color ? vars({ '--tl-sw': lane.color }) : undefined} />
                  <span>{lane.name}</span>
                </div>
                <div className="tl-track">
                  {lane.spans.map((sp, k) => {
                    if (!drawable(sp, D)) return null;
                    const label = sp.label || lane.name || '';
                    return (
                      <button
                        key={k}
                        type="button"
                        className={`tl-span ${levelClass(sp.level)}`}
                        data-k={k}
                        aria-label={(label ? label + ' · ' : '') + formatClock(sp.start)}
                        style={{
                          left: percentOf(sp.start as number, D),
                          width: spanWidth(sp, D),
                          // A span with a numeric score is coloured by the §3 ramp; one without
                          // keeps the level class's token (see levelColor for why the caller,
                          // not this component, decides which of the two applies).
                          ...(sp.score !== null ? { background: levelColor(sp.level, sp.score) } : null),
                        }}
                        onClick={() => {
                          seek(sp.start as number);
                          liveRef.current.onSpanClick?.(lane, sp as Span);
                        }}
                        onMouseEnter={e => showTip(e.currentTarget, label, sp.detail || '')}
                        onMouseLeave={e => hideTip(e.currentTarget)}
                        onFocus={e => showTip(e.currentTarget, label, sp.detail || '')}
                        onBlur={e => hideTip(e.currentTarget)}
                      />
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="tl-legend" hidden={!lanes.length}>
        {lanes.map(lane => {
          const on = !hidden.has(lane.id);
          return (
            <label key={lane.id} className={on ? undefined : 'off'}>
              <input
                type="checkbox"
                data-lane={lane.id}
                checked={on}
                onChange={e => toggleLane(lane.id, e.target.checked)}
              />
              <i className="tl-sw" style={lane.color ? vars({ '--tl-sw': lane.color }) : undefined} />
              <span>{lane.name}</span>
            </label>
          );
        })}
      </div>

      <div className="tl-h">{t('res.transcript')}</div>
      <div className="tl-transcript" ref={txRef}>
        {!segments.length
          ? <div className="tl-empty">{t('tl.nosegments')}</div>
          : segments.map((seg, k) => {
            const strip = strips.get(seg.i) ?? { colors: [], titles: [] };
            const style = strip.colors.length === 1
              ? vars({ '--tl-mark': strip.colors[0] })
              : strip.colors.length > 1
                ? vars({ '--tl-marks': markGradient(strip.colors) })
                : undefined;
            const title = strip.titles.length
              ? strip.titles.join(' · ')
              : (seg.start !== null && !textMode ? t('tl.goto', { t: formatClock(seg.start) }) : undefined);
            const cls = 'tl-p'
              + (seg.i === cur ? ' now' : '')
              + (seg.i === hl ? ' hl' : '')
              + (strip.colors.length ? ' marked' : '')
              + (strip.colors.length > 1 ? ' tl-multi' : '');
            return (
              <p
                key={k}
                className={cls}
                data-i={seg.i}
                tabIndex={0}
                style={style}
                title={title}
                onClick={() => goSegment(seg.i)}
                onKeyDown={e => {
                  if (e.key !== 'Enter' && e.key !== ' ') return;
                  e.preventDefault();
                  goSegment(seg.i);
                }}
              >
                <span className={`tl-chip ${spClass(seg.speaker)}`}>{spLabel(seg.speaker)}</span>
                {seg.start !== null ? <span className="tl-ts">{formatClock(seg.start)}</span> : null}
                <span className="tl-txt">{seg.text}</span>
              </p>
            );
          })}
      </div>

      {mounted && tip && createPortal(
        <div
          ref={tipRef}
          className={`${s.tip}${tipPos ? ' ' + s.open : ''}`}
          role="tooltip"
          aria-hidden="true"
          data-place={tipPos?.place}
          style={tipPos
            ? vars({ top: `${tipPos.top}px`, left: `${tipPos.left}px`, '--cq-tip-ax': `${tipPos.arrow}px` })
            : undefined}
        >
          {tip.label ? <b>{tip.label}</b> : null}
          {tip.detail ? <span>{tip.detail}</span> : null}
        </div>,
        document.body,
      )}
    </div>
  );
}

export const Timeline = forwardRef<TimelineHandle, TimelineProps>(TimelineImpl);
Timeline.displayName = 'Timeline';

export default Timeline;
