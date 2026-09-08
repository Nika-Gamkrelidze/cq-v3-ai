/* The audio editor's engine: layered timeline, waveform, selection, playback.
   ===========================================================================
   The port of `frontend/public/audio-editor.js`. No React, no JSX, no dictionary — the page
   above owns all of that. What is left here is the two things that genuinely cannot be
   expressed as markup: the canvas pixels and the Web Audio graph.

   WHY THERE IS NO WAVEFORM LIBRARY HERE.

   wavesurfer.js is the standard answer for waveforms on the web and it is a good library — it
   was evaluated for this and rejected for THIS job. It is built around "load media, draw it,
   play it": the picture comes from a decoded media element. An editor mutates audio
   constantly, so every cut, gain and undo would mean re-encoding the whole recording to a blob
   and handing it back; on the half-hour calls this product exists for, that is hundreds of
   megabytes of encode per keystroke. It also has no concept of several layers sharing one
   timeline, which is the point of this file. What it actually buys is peak drawing and a drag
   rectangle: peaks we compute in `core.ts` (a scan, not a decode), and the rest is below.

   THE MODEL. A `layer` is {buffer, name, offset, gain, muted, solo}. The timeline is the layers
   mixed together; playback and export are `mixdown` of exactly what is on screen. Editing
   operations apply to ONE layer — the selected one — because "cut" across layers that start at
   different offsets has no single honest meaning, and an editor that guesses is worse than one
   that asks.

   SELECTION. Two ways, because dragging is wrong for the common case. Drag across the waveform
   when you can SEE the part you want. When you cannot — you have to hear it — play the
   timeline and press Mark in, keep listening, press Mark out. Both write the same {from,to},
   so nothing downstream knows or cares which was used.

   WHAT THE PORT FIXED (docs/MIGRATION.md, "pre-existing defects"):

     * ONE AudioContext (defect 3). `start()` used to construct a new one on every play and
       nothing ever closed them. Browsers cap live contexts per document — historically about
       six in Chrome — so playback died silently after a handful of plays. There is now exactly
       one for the life of the editor, `close()`d by `destroy()`, and `decode()` uses it too
       (it already resampled to a live context's rate, so this changes nothing about the audio).
     * NO ALLOCATION IN THE DRAW LOOP (defect 4). `draw()` runs inside the playback rAF loop and
       called `trim()` per layer per frame, which built an `OfflineAudioContext` and copied the
       slice. It now reads the slice in place (`peaksBetween`) and caches the columns per layer,
       so a frame that changed nothing computes nothing.

   AND WHAT IT KEEPS. The legacy module wrote `host.innerHTML` and registered window and
   document listeners with no teardown — dropped into React unchanged that deletes nodes React
   owns, and `reactStrictMode` doubles every leaked listener on the second mount. So the markup
   is JSX in `../../app/editor/page.tsx`, this file is handed the two elements it paints into,
   and every listener it registers is undone by `destroy()`. */

import {
  clock, cut as opCut, dbToGain, fade as opFade, gain as opGain, insertSilence as opInsertSilence,
  invert as opInvert, mixdown, muteChannel as opMuteChannel, normalize as opNormalize,
  extractChannel as opExtractChannel, peaksBetween, reverse as opReverse, silence as opSilence,
  swapChannels as opSwapChannels, toMono as opToMono, toStereo as opToStereo, toWav,
  trim as opTrim, type AudioBufferLike, type PeakPair,
} from './core';

export interface Layer {
  id: number;
  name: string;
  buffer: AudioBufferLike;
  offset: number;
  gain: number;
  muted: boolean;
  solo: boolean;
}

/** One layer as the UI sees it — a flat, immutable row, never the live `Layer`. */
export interface LayerView {
  id: number;
  name: string;
  offset: number;
  gain: number;
  muted: boolean;
  solo: boolean;
  active: boolean;
  duration: number;
  channels: number;
}

export interface EditorState {
  loaded: boolean;
  layers: LayerView[];
  active: number;
  activeName: string;
  channels: number;
  duration: number;
  cursor: number;
  hasSelection: boolean;
  from: number;
  to: number;
  canUndo: boolean;
  canRedo: boolean;
  playing: boolean;
}

export interface EditorOptions {
  /** The waveform canvas. The engine owns its bitmap, its pointer listeners and its wheel
      listener (which must be non-passive to zoom, and React's `onWheel` is not). */
  canvas: HTMLCanvasElement;
  /** The canvas's sized parent — the element whose client box decides how big the bitmap is.
      The canvas cannot be its own reference: `resize()` pins an explicit pixel width on it. */
  wrap: HTMLElement;
  /** Where the "Selection: 0:01.2 – 0:04.8" line goes. Written with `textContent` from inside
      the rAF loop, sixty times a second — which is exactly why it is NOT React state. The page
      renders this element with no children of its own, so nothing here fights the reconciler. */
  timeEl: HTMLElement | null;
  /** The page's `t`, read through a getter so a language switch does not rebuild the engine. */
  t: (key: string) => string;
  /** Called on every DISCRETE change (an edit, a click, play/stop) — never per frame. */
  onChange: (state: EditorState) => void;
}

export interface Editor {
  state(): EditorState;
  draw(): void;
  resize(): void;
  add(file: File, atCursor?: boolean): Promise<EditorState>;

  play(): void;
  stop(): void;
  toggle(): void;
  playSelection(): void;
  seek(seconds: number): void;

  markIn(): void;
  markOut(): void;
  clearSelection(): void;
  selectLayer(i: number): void;

  zoomIn(): void;
  zoomOut(): void;
  zoomFit(): void;
  zoomSelection(): void;

  removeLayer(i: number): void;
  moveLayer(i: number, seconds: number): void;
  layerGainDb(i: number, db: number): void;
  toggleMute(i: number): void;
  toggleSolo(i: number): void;
  renameLayer(i: number, name: string): void;
  flatten(): void;

  cut(): void;
  trim(): void;
  silence(): void;
  insertSilence(seconds: number): void;
  fadeIn(): void;
  fadeOut(): void;
  normalize(): void;
  gainDb(db: number): void;
  reverse(): void;
  invert(): void;

  toMono(): void;
  toStereo(): void;
  swapChannels(): void;
  extractChannel(channel: number): void;
  muteChannel(channel: number): void;

  undo(): void;
  redo(): void;

  wav(): { blob: Blob; name: string } | null;
  destroy(): void;
}

let nextId = 1;

const MAX_UNDO = 30;

/** Columns already computed for one layer, and everything that would invalidate them.
    `buffer` is compared by IDENTITY, which is exactly right because every operation in
    `core.ts` returns a new buffer and never mutates one. */
interface PeakCache {
  buffer: AudioBufferLike;
  from: number;
  to: number;
  cols: number;
  pk: PeakPair[];
}

export function createEditor(opts: EditorOptions): Editor {
  const { canvas: cv, wrap, timeEl } = opts;
  const t = (k: string) => opts.t(k);
  const on = (s: EditorState) => opts.onChange(s);

  // ---- state ----------------------------------------------------------
  let layers: Layer[] = [];
  let active = 0;                                    // index of the layer edits apply to
  let undoStack: Layer[][] = [], redoStack: Layer[][] = [];
  let sel: { from: number; to: number } | null = null;   // in TIMELINE seconds
  let cursor = 0;                                    // where playback starts, and marks land
  let view = { from: 0, to: 0 };
  let play: { src: AudioBufferSourceNode; startedAt: number; offset: number; until: number } | null = null;
  let raf = 0;
  let mixCache: AudioBufferLike | null = null;       // mixdown of the layers, cleared on change
  let playCache: { of: AudioBufferLike; buf: AudioBuffer } | null = null;
  const peakCache = new Map<number, PeakCache>();
  let dead = false;

  const g = cv.getContext('2d');

  // ---- the one audio context (defect 3) -------------------------------
  /* Created on first use — which is always inside a click or a file pick, so it starts running
     rather than suspended — and reused for every decode and every play until `destroy()`.
     Recreated only if something else closed it, so a stale reference cannot silently swallow
     playback. */
  let actx: AudioContext | null = null;
  function audio(): AudioContext {
    if (!actx || actx.state === 'closed') {
      const C = window.AudioContext
        || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      actx = new C!();
    }
    return actx;
  }

  // ---- geometry -------------------------------------------------------
  // Never returns undefined for an out-of-range index: the whole edit surface keys off this,
  // and "no active layer" must mean "do nothing visibly", not "throw".
  const cur = (): Layer | null => layers[active] || layers[0] || null;
  const total = () => layers.reduce((m, l) => Math.max(m, l.offset + l.buffer.duration), 0);
  const viewLen = () => Math.max(1e-6, view.to - view.from);
  const xToTime = (x: number) => view.from + (x / Math.max(1, cv.clientWidth)) * viewLen();
  const timeToX = (s: number) => ((s - view.from) / viewLen()) * cv.clientWidth;
  const clampSec = (s: number) => Math.max(0, Math.min(total(), s));

  /** The timeline range an operation covers: the selection, else everything.

      AN EMPTY SELECTION MEANS THE WHOLE TIMELINE, not nothing — docs/MIGRATION.md lists this
      under decisions the port must preserve. A drag that collapsed to a point, or a click, is
      how someone says "never mind"; answering it with "then this button does nothing" would
      make every operation below silently no-op after a stray click. */
  function range(): [number, number] {
    if (sel && Math.abs(sel.to - sel.from) > 1e-4) {
      return [Math.min(sel.from, sel.to), Math.max(sel.from, sel.to)];
    }
    return [0, total()];
  }

  /** The same range expressed in the ACTIVE layer's own time, since that is what the operations
      take. A selection that misses the layer entirely returns null, and the caller does nothing
      rather than editing a span the user never pointed at. */
  function localRange(): [number, number] | null {
    const l = cur();
    if (!l) return null;
    const [f, to] = range();
    const a = Math.max(0, f - l.offset), b = Math.min(l.buffer.duration, to - l.offset);
    return b > a ? [a, b] : null;
  }

  // ---- history ---------------------------------------------------------
  const snapshot = (): Layer[] => layers.map(l => ({ ...l }));

  function commit(mutate: () => boolean | void): void {
    if (!layers.length) return;
    const before = snapshot();
    const changed = mutate();
    if (changed === false) return;
    undoStack.push(before);
    if (undoStack.length > MAX_UNDO) undoStack.shift();
    redoStack = [];
    afterChange();
  }

  function afterChange(): void {
    mixCache = null;
    playCache = null;
    peakCache.clear();
    if (sel && (sel.from > total() || sel.to > total())) sel = null;
    if (view.to > total() || view.to <= view.from) view = { from: 0, to: total() };
    cursor = clampSec(cursor);
    stop();
    draw(); on(state());
  }

  /** Replace the active layer's buffer. Length changes invalidate the selection: every sample
      after the edit has moved, so the same seconds now address different sound and the next
      operation would land somewhere the user never chose. */
  function setActiveBuffer(next: AudioBufferLike | null): boolean {
    const l = cur();
    if (!l || !next || next === l.buffer) return false;
    const lengthChanged = next.length !== l.buffer.length;
    l.buffer = next;
    if (lengthChanged) sel = null;
    return true;
  }

  // ---- drawing ---------------------------------------------------------
  function resize(): void {
    const dpr = window.devicePixelRatio || 1;
    const w = wrap.clientWidth, h = wrap.clientHeight;
    if (!w || !h) return;
    const bw = Math.round(w * dpr), bh = Math.round(h * dpr);
    // Bail when nothing moved. Writing `canvas.width` RESETS the bitmap even to the same value,
    // so an unconditional resize would blank the canvas and throw away every cached column on
    // every `ResizeObserver` callback — including the one it fires just for being observed.
    if (cv.width === bw && cv.height === bh) return;
    cv.width = bw; cv.height = bh;
    cv.style.width = w + 'px'; cv.style.height = h + 'px';
    g?.setTransform(dpr, 0, 0, dpr, 0, 0);
    // The bitmap just changed width, so every cached column is the wrong width.
    peakCache.clear();
  }

  function css(v: string, fallback: string): string {
    const s = getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    return s || fallback;
  }

  function laneRect(i: number): { y: number; h: number } {
    const h = cv.clientHeight, n = Math.max(1, layers.length);
    const lh = h / n;
    return { y: i * lh, h: lh };
  }

  function playhead(): number {
    if (!play) return cursor;
    return Math.min(play.until, play.offset + (audio().currentTime - play.startedAt));
  }

  /** The visible columns for one layer, computed at most once per (buffer, range, width).

      This is defect 4's fix. `draw()` is called from the playback rAF loop, and the legacy
      version rebuilt an `OfflineAudioContext` and copied the visible slice of every layer on
      every frame. Nothing here changes while the transport runs — the buffers are immutable,
      the view only moves on an explicit zoom — so after the first frame this is a map lookup. */
  function columnsFor(l: Layer, from: number, to: number, cols: number): PeakPair[] {
    const hit = peakCache.get(l.id);
    if (hit && hit.buffer === l.buffer && hit.from === from && hit.to === to && hit.cols === cols) {
      return hit.pk;
    }
    const pk = peaksBetween(l.buffer, from, to, cols);
    peakCache.set(l.id, { buffer: l.buffer, from, to, cols, pk });
    return pk;
  }

  function draw(): void {
    if (!g) return;
    const w = cv.clientWidth, h = cv.clientHeight;
    g.clearRect(0, 0, w, h);
    if (!layers.length) { if (timeEl) timeEl.textContent = ''; return; }

    const beam = css('--beam', '#fa3b3c');
    const muted = css('--muted', '#8aa');
    const anySolo = layers.some(l => l.solo);

    layers.forEach((l, i) => {
      const { y, h: lh } = laneRect(i);
      const mid = y + lh / 2, amp = (lh / 2) * 0.82;
      const audible = !l.muted && (!anySolo || l.solo);

      g.fillStyle = css('--input-bg', 'rgba(0,0,0,.2)');
      g.fillRect(0, y, w, lh - 1);
      if (i === active) {
        g.strokeStyle = beam; g.lineWidth = 1;
        g.strokeRect(0.5, y + 0.5, w - 1, lh - 2);
      }
      g.strokeStyle = css('--hairline', '#345'); g.lineWidth = 1;
      g.beginPath(); g.moveTo(0, mid); g.lineTo(w, mid); g.stroke();

      // Only the visible slice of THIS layer is scanned, so a long timeline costs the same to
      // redraw zoomed in as zoomed out.
      const from = Math.max(0, view.from - l.offset);
      const to = Math.min(l.buffer.duration, view.to - l.offset);
      if (to > from) {
        const x0 = Math.max(0, timeToX(l.offset + Math.max(0, from)));
        const x1 = Math.min(w, timeToX(l.offset + to));
        const cols = Math.max(1, Math.floor(x1 - x0));
        const pk = columnsFor(l, from, to, cols);
        g.fillStyle = audible ? (i === active ? beam : muted) : css('--hairline', '#345');
        for (let x = 0; x < pk.length; x++) {
          const yTop = mid - pk[x].max * amp, yBot = mid - pk[x].min * amp;
          g.fillRect(x0 + x, yTop, 1, Math.max(1, yBot - yTop));
        }
      }

      g.fillStyle = css('--paper', '#fff');
      g.font = '11px system-ui, sans-serif';
      const badge = (l.muted ? '🔇 ' : l.solo ? '★ ' : '') + l.name
        + (l.buffer.numberOfChannels > 1 ? ' · ' + l.buffer.numberOfChannels + 'ch' : '');
      g.fillText(badge, 6, y + 13);
    });

    if (sel) {
      const a = timeToX(Math.min(sel.from, sel.to)), b = timeToX(Math.max(sel.from, sel.to));
      g.fillStyle = 'rgba(250,59,60,.18)';
      g.fillRect(a, 0, Math.max(1, b - a), h);
      g.strokeStyle = beam; g.lineWidth = 1;
      g.beginPath(); g.moveTo(a, 0); g.lineTo(a, h); g.moveTo(b, 0); g.lineTo(b, h); g.stroke();
    }

    // The cursor is where playback starts and where a mark lands, so it stays visible when
    // nothing is playing — otherwise "Mark in" would have no anchor the user can see.
    const px = timeToX(play ? playhead() : cursor);
    g.strokeStyle = css('--paper', '#fff'); g.lineWidth = play ? 2 : 1;
    g.beginPath(); g.moveTo(px, 0); g.lineTo(px, h); g.stroke();
    g.lineWidth = 1;

    if (timeEl) {
      const [f, to] = range();
      timeEl.textContent = sel
        ? `${t('ed.selection')}: ${clock(f)} – ${clock(to)}  (${clock(to - f)})`
        : `${t('ed.length')}: ${clock(total())} · ${t('ed.at')} ${clock(play ? playhead() : cursor)}`;
    }
  }

  // ---- pointer ---------------------------------------------------------
  let dragging = false, dragFrom = 0, moved = false;

  const onPointerDown = (e: PointerEvent) => {
    if (!layers.length) return;
    cv.setPointerCapture(e.pointerId);
    // Clicking a lane also selects that layer: with several stacked, "which one am I about to
    // cut" must be answerable by pointing at it.
    const n = Math.max(1, layers.length);
    // Guarded: a pointer event without a usable offsetY would make this NaN, and a NaN index
    // means NO layer is active — every edit would then quietly do nothing while the buttons
    // still looked enabled. Fall back to whatever was selected before.
    const lane = Math.floor(e.offsetY / (cv.clientHeight / n));
    const i = Number.isFinite(lane) ? Math.max(0, Math.min(n - 1, lane)) : active;
    if (i !== active) { active = i; on(state()); }
    dragging = true; moved = false;
    dragFrom = clampSec(xToTime(e.offsetX));
    draw();
  };

  const onPointerMove = (e: PointerEvent) => {
    if (!dragging) return;
    const at = clampSec(xToTime(e.offsetX));
    if (Math.abs(at - dragFrom) > 0.01) { moved = true; sel = { from: dragFrom, to: at }; }
    draw(); on(state());
  };

  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    // A click that did not drag is a CURSOR move, not an empty selection: it is how you say
    // "start playing here" and where the next Mark in will land.
    if (!moved) { cursor = dragFrom; sel = null; }
    draw(); on(state());
  };

  const onWheel = (e: WheelEvent) => {
    if (!layers.length || !e.ctrlKey) return;
    e.preventDefault();
    zoom(e.deltaY < 0 ? 1.25 : 0.8, xToTime(e.offsetX));
  };

  cv.addEventListener('pointerdown', onPointerDown);
  cv.addEventListener('pointermove', onPointerMove);
  cv.addEventListener('pointerup', endDrag);
  cv.addEventListener('pointercancel', endDrag);
  // Non-passive, and therefore hand-registered: React attaches `onWheel` passively at the root,
  // where `preventDefault()` is ignored and ctrl+wheel zooms the whole page instead.
  cv.addEventListener('wheel', onWheel, { passive: false });

  // ---- what makes the picture stale, other than the audio ---------------
  /* Two observers where the legacy module had two undisposed global listeners
     (`window.resize` and `document` `cq:theme`), and both are strict improvements that also
     happen to be disposable:

       * The BITMAP is sized from the wrap's client box, which changes without the window
         changing — a layer row wrapping onto a second line resizes the card under it. A
         `ResizeObserver` sees that; a window listener does not.
       * The COLOURS are read out of the live cascade every frame, so a theme switch has to
         repaint. `useTheme` in the new stack sets `data-theme` on <html> and dispatches
         nothing; brand.js sets the same attribute AND dispatches `cq:theme`. Watching the
         attribute is the one mechanism that hears both stacks. */
  const ro = typeof ResizeObserver === 'function'
    ? new ResizeObserver(() => { resize(); draw(); })
    : null;
  ro?.observe(wrap);
  const mo = typeof MutationObserver === 'function' ? new MutationObserver(() => draw()) : null;
  mo?.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  // ---- playback --------------------------------------------------------
  function mix(): AudioBufferLike | null {
    if (!mixCache) {
      mixCache = mixdown(layers.map(l => ({
        buffer: l.buffer, offset: l.offset, gain: l.gain,
        muted: l.muted || (layers.some(x => x.solo) && !l.solo),
      })));
    }
    return mixCache;
  }

  /** The mix as a real `AudioBuffer`, which is the one thing a `PcmBuffer` cannot be: an
      `AudioBufferSourceNode` will take nothing else. One copy per mix, cached beside it, so
      pressing play twice on an unchanged timeline copies nothing. */
  function playable(): AudioBuffer | null {
    const m = mix();
    if (!m) return null;
    if (playCache && playCache.of === m) return playCache.buf;
    try {
      const buf = audio().createBuffer(m.numberOfChannels, m.length, m.sampleRate);
      for (let c = 0; c < m.numberOfChannels; c++) buf.getChannelData(c).set(m.getChannelData(c));
      playCache = { of: m, buf };
      return buf;
    } catch {
      // A sample rate the device refuses is the only realistic way here — better a transport
      // that does nothing than an exception out of a click handler.
      return null;
    }
  }

  function stop(): void {
    if (play) {
      // Keep the cursor where the ear stopped: pressing play again resumes from there, which is
      // what makes listen-then-mark practical.
      cursor = clampSec(playhead());
      try { play.src.stop(); } catch { /* already ended */ }
      play = null;
    }
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    draw(); on(state());
  }

  /** Play from `at` (default: the cursor) to `until` (default: the end of the timeline).
      Playing to the END rather than to the end of a selection is what lets someone hold the
      mouse off the keyboard, listen, and mark the out-point when they hear it. */
  function start(at?: number, until?: number): void {
    const buf = playable();
    if (!buf) return;
    stop();
    const ac = audio();
    // A context created outside a gesture (or suspended by the tab going to the background)
    // never advances `currentTime`, so the playhead would freeze at the start with no sound.
    // The legacy code got away without this only because it built a fresh context inside the
    // click every single time — which is the leak this port is fixing.
    if (ac.state === 'suspended') void ac.resume();
    const src = ac.createBufferSource();
    src.buffer = buf;
    src.connect(ac.destination);
    const from = clampSec(at == null ? cursor : at);
    const to = Math.min(buf.duration, until == null ? buf.duration : until);
    if (to - from < 0.01) return;
    play = { src, startedAt: ac.currentTime, offset: from, until: to };
    src.start(0, from, to - from);
    src.onended = () => {
      if (play && play.src === src) { cursor = clampSec(to); play = null; draw(); on(state()); }
    };
    const tick = () => { if (!play) return; draw(); raf = requestAnimationFrame(tick); };
    tick();
    on(state());
  }

  // ---- zoom ------------------------------------------------------------
  function zoom(factor: number, centreSec?: number): void {
    if (!layers.length) return;
    const c = centreSec == null ? (view.from + view.to) / 2 : centreSec;
    const len = Math.max(0.02, Math.min(total(), viewLen() / factor));
    let from = c - len / 2, to = c + len / 2;
    if (from < 0) { to -= from; from = 0; }
    if (to > total()) { from -= (to - total()); to = total(); }
    view = { from: Math.max(0, from), to: Math.min(total(), to) };
    draw(); on(state());
  }

  // ---- state -----------------------------------------------------------
  function state(): EditorState {
    const l = cur();
    const [f, to] = range();
    return {
      loaded: layers.length > 0,
      layers: layers.map((x, i) => ({
        id: x.id, name: x.name, offset: x.offset, gain: x.gain,
        muted: !!x.muted, solo: !!x.solo, active: i === active,
        duration: x.buffer.duration, channels: x.buffer.numberOfChannels,
      })),
      active, activeName: l ? l.name : '',
      channels: l ? l.buffer.numberOfChannels : 0,
      duration: total(), cursor: play ? playhead() : cursor,
      hasSelection: !!sel, from: f, to,
      canUndo: undoStack.length > 0, canRedo: redoStack.length > 0,
      playing: !!play,
    };
  }

  async function decode(file: File): Promise<AudioBuffer> {
    // The editor's one context, not a throwaway. `decodeAudioData` resamples to the context's
    // rate either way, and that rate is the device's in both versions, so the decoded audio is
    // identical — this only stops a ten-file drop from opening ten contexts.
    return audio().decodeAudioData(await file.arrayBuffer());
  }

  /** Add a file as a new layer. The first one defines the view; later ones land at the cursor,
      which is how you place a jingle or a bed exactly where you were listening. */
  async function add(file: File, atCursor?: boolean): Promise<EditorState> {
    const buffer = await decode(file);
    // A file still decoding when the page unmounts must not push a layer onto an editor whose
    // canvas and audio context are already gone. Under `reactStrictMode`'s double-mount that is
    // not hypothetical: the first editor is destroyed while its first decode is in flight.
    if (dead) return state();
    const before = snapshot();
    layers.push({
      id: nextId++, buffer, offset: atCursor && layers.length ? cursor : 0,
      name: (file.name || 'layer').replace(/\.[^.]+$/, ''),
      gain: 1, muted: false, solo: false,
    });
    active = layers.length - 1;
    if (layers.length === 1) { view = { from: 0, to: total() }; cursor = 0; }
    undoStack.push(before); redoStack = [];
    afterChange();
    return state();
  }

  const editActive = (fn: (b: AudioBufferLike, from: number, to: number) => AudioBufferLike) =>
    commit(() => {
      const l = cur(), r = localRange();
      if (!l || !r) return false;
      return setActiveBuffer(fn(l.buffer, r[0], r[1]));
    });

  // ---- teardown --------------------------------------------------------
  /* Everything registered above, undone — plus the audio context, whose whole point was that
     there is exactly one of it. `dead` guards the async tail of `add()`: a file still decoding
     when the page unmounts must not push a layer onto an editor nobody can see. */
  function destroy(): void {
    dead = true;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    if (play) { try { play.src.stop(); } catch { /* already ended */ } play = null; }
    cv.removeEventListener('pointerdown', onPointerDown);
    cv.removeEventListener('pointermove', onPointerMove);
    cv.removeEventListener('pointerup', endDrag);
    cv.removeEventListener('pointercancel', endDrag);
    cv.removeEventListener('wheel', onWheel);
    ro?.disconnect();
    mo?.disconnect();
    layers = []; undoStack = []; redoStack = [];
    mixCache = null; playCache = null; peakCache.clear();
    if (actx) { const a = actx; actx = null; void a.close().catch(() => { /* already closed */ }); }
  }

  const api: Editor = {
    state, draw, resize, add,

    // transport
    play: () => start(),
    stop,
    toggle: () => (play ? stop() : start()),
    playSelection: () => { const [f, to] = range(); start(f, to); },
    seek: s => { cursor = clampSec(s); draw(); on(state()); },

    // selection
    markIn: () => {
      const at = play ? playhead() : cursor;
      sel = { from: at, to: sel ? Math.max(sel.to, at) : total() };
      draw(); on(state());
    },
    markOut: () => {
      const at = play ? playhead() : cursor;
      sel = { from: sel ? Math.min(sel.from, at) : 0, to: at };
      draw(); on(state());
    },
    clearSelection: () => { sel = null; draw(); on(state()); },
    selectLayer: i => { if (layers[i]) { active = i; draw(); on(state()); } },

    // zoom
    zoomIn: () => zoom(1.6),
    zoomOut: () => zoom(1 / 1.6),
    zoomFit: () => { view = { from: 0, to: total() }; draw(); on(state()); },
    zoomSelection: () => { if (sel) { const [f, to] = range(); view = { from: f, to }; draw(); on(state()); } },

    // layer management
    removeLayer: i => commit(() => {
      if (!layers[i]) return false;
      layers.splice(i, 1);
      active = Math.max(0, Math.min(active, layers.length - 1));
      return true;
    }),
    moveLayer: (i, seconds) => commit(() => {
      const l = layers[i]; if (!l) return false;
      l.offset = Math.max(0, seconds); return true;
    }),
    layerGainDb: (i, db) => commit(() => {
      const l = layers[i]; if (!l) return false;
      l.gain = Math.max(0, Math.min(4, (l.gain || 1) * dbToGain(db))); return true;
    }),
    toggleMute: i => commit(() => { const l = layers[i]; if (!l) return false; l.muted = !l.muted; return true; }),
    toggleSolo: i => commit(() => { const l = layers[i]; if (!l) return false; l.solo = !l.solo; return true; }),
    renameLayer: (i, n) => commit(() => { const l = layers[i]; if (!l) return false; l.name = n || l.name; return true; }),

    /** Flatten every layer into one. The mix is what export and playback already use, so this
        only makes visible what the ear was hearing — and gives the per-sample tools (fades,
        normalise) something single to work on. */
    flatten: () => commit(() => {
      const m = mix(); if (!m || layers.length < 2) return false;
      layers = [{
        id: nextId++, buffer: m, offset: 0, gain: 1, muted: false, solo: false,
        name: t('ed.mixname'),
      }];
      active = 0;
      return true;
    }),

    // edits on the active layer
    cut: () => editActive(opCut),
    trim: () => editActive(opTrim),
    silence: () => editActive(opSilence),
    insertSilence: secs => commit(() => {
      const l = cur(), r = localRange(); if (!l) return false;
      return setActiveBuffer(opInsertSilence(l.buffer, r ? r[0] : 0, secs));
    }),
    fadeIn: () => editActive((b, f, to) => opFade(b, f, to, 'in')),
    fadeOut: () => editActive((b, f, to) => opFade(b, f, to, 'out')),
    normalize: () => editActive((b, f, to) => opNormalize(b, null, f, to)),
    gainDb: db => editActive((b, f, to) => opGain(b, dbToGain(db), f, to)),
    reverse: () => editActive(opReverse),
    invert: () => editActive(opInvert),

    toMono: () => commit(() => { const l = cur(); return !!l && setActiveBuffer(opToMono(l.buffer)); }),
    toStereo: () => commit(() => { const l = cur(); return !!l && setActiveBuffer(opToStereo(l.buffer)); }),
    swapChannels: () => commit(() => { const l = cur(); return !!l && setActiveBuffer(opSwapChannels(l.buffer)); }),
    extractChannel: c => commit(() => { const l = cur(); return !!l && setActiveBuffer(opExtractChannel(l.buffer, c)); }),
    muteChannel: c => commit(() => { const l = cur(); return !!l && setActiveBuffer(opMuteChannel(l.buffer, c)); }),

    /* UNDO AND REDO RESTORE LAYERS ONLY. `sel` is dropped rather than restored — another
       decision docs/MIGRATION.md pins — because the selection is in timeline seconds and the
       samples those seconds addressed are exactly what just changed. Putting the old selection
       back would aim the next operation at audio the user never pointed at. */
    undo: () => {
      if (!undoStack.length) return;
      redoStack.push(snapshot());
      layers = undoStack.pop()!;
      active = Math.min(active, layers.length - 1);
      sel = null;
      afterChange();
    },
    redo: () => {
      if (!redoStack.length) return;
      undoStack.push(snapshot());
      layers = redoStack.pop()!;
      active = Math.min(active, layers.length - 1);
      sel = null;
      afterChange();
    },

    /** The finished timeline: every layer mixed, as a WAV blob. */
    wav: () => {
      const m = mix();
      return m ? { blob: toWav(m), name: (layers[0] && layers[0].name) || 'audio' } : null;
    },

    destroy,
  };

  resize(); draw();
  return api;
}
