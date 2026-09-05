/* The audio editor: layered timeline, waveform, selection, playback.
   ==================================================================
   WHY THERE IS NO WAVEFORM LIBRARY HERE.

   wavesurfer.js is the standard answer for waveforms on the web and it is a good library —
   it was evaluated for this and rejected for THIS job. It is built around "load media, draw
   it, play it": the picture comes from a decoded media element. An editor mutates audio
   constantly, so every cut, gain and undo would mean re-encoding the whole recording to a
   blob and handing it back; on the half-hour calls this product exists for, that is hundreds
   of megabytes of encode per keystroke. It also has no concept of several layers sharing one
   timeline, which is the point of this file. What it actually buys is peak drawing and a
   drag rectangle: peaks we compute in audio-edit-core.js (a scan, not a decode), and the
   rest is below. The frontend keeps having no third-party JavaScript, which for a product
   whose promise is that recordings never leave the deployment is worth the afternoon.

   THE MODEL. A `layer` is {buffer, name, offset, gain, muted, solo}. The timeline is the
   layers mixed together; playback and export are `CQAudio.mixdown` of exactly what is on
   screen. Editing operations apply to ONE layer — the selected one — because "cut" across
   layers that start at different offsets has no single honest meaning, and an editor that
   guesses is worse than one that asks.

   SELECTION. Two ways, because dragging is wrong for the common case. Drag across the
   waveform when you can SEE the part you want. When you cannot — you have to hear it — play
   the timeline and press Mark in, keep listening, press Mark out. Both write the same
   {from,to}, so nothing downstream knows or cares which was used. */
(function (global) {
  'use strict';

  const A = global.CQAudio;
  let nextId = 1;

  function Editor(host, opts) {
    opts = opts || {};
    const t = opts.t || (k => k);
    const on = opts.onChange || function () {};

    // ---- state ----------------------------------------------------------
    let layers = [];                // [{id,name,buffer,offset,gain,muted,solo}]
    let active = 0;                 // index of the layer edits apply to
    let undo = [], redo = [];       // snapshots of the whole layer list
    let sel = null;                 // {from,to} in TIMELINE seconds
    let cursor = 0;                 // where playback starts, and where marks land
    let view = { from: 0, to: 0 };
    let play = null;
    let raf = 0;
    let mixCache = null;            // mixdown of the current layers, invalidated on change

    const MAX_UNDO = 30;

    host.innerHTML = `
      <div class="ed-wrap">
        <canvas class="ed-canvas" tabindex="0" aria-label="${t('ed.canvas.aria')}"></canvas>
        <div class="ed-empty" data-role="empty">${t('ed.empty')}</div>
      </div>
      <div class="ed-time" data-role="time"></div>`;
    const wrap = host.querySelector('.ed-wrap');
    const cv = host.querySelector('.ed-canvas');
    const empty = host.querySelector('[data-role=empty]');
    const timeEl = host.querySelector('[data-role=time]');
    const g = cv.getContext('2d');

    // ---- geometry -------------------------------------------------------
    // Never returns undefined for an out-of-range index: the whole edit surface keys off
    // this, and "no active layer" must mean "do nothing visibly", not "throw".
    const cur = () => layers[active] || layers[0] || null;
    const total = () => layers.reduce((m, l) => Math.max(m, l.offset + l.buffer.duration), 0);
    const viewLen = () => Math.max(1e-6, view.to - view.from);
    const xToTime = x => view.from + (x / Math.max(1, cv.clientWidth)) * viewLen();
    const timeToX = s => ((s - view.from) / viewLen()) * cv.clientWidth;
    const clampSec = s => Math.max(0, Math.min(total(), s));

    function fmt(s) {
      if (!isFinite(s)) return '0:00.0';
      const m = Math.floor(s / 60), r = Math.max(0, s - m * 60);
      return m + ':' + (r < 10 ? '0' : '') + r.toFixed(1);
    }

    /** The timeline range an operation covers: the selection, else everything. */
    function range() {
      if (sel && Math.abs(sel.to - sel.from) > 1e-4) {
        return [Math.min(sel.from, sel.to), Math.max(sel.from, sel.to)];
      }
      return [0, total()];
    }

    /** The same range expressed in the ACTIVE layer's own time, since that is what the
        operations take. A selection that misses the layer entirely returns null, and the
        caller does nothing rather than editing a span the user never pointed at. */
    function localRange() {
      const l = cur();
      if (!l) return null;
      const [f, to] = range();
      const a = Math.max(0, f - l.offset), b = Math.min(l.buffer.duration, to - l.offset);
      return b > a ? [a, b] : null;
    }

    // ---- history ---------------------------------------------------------
    const snapshot = () => layers.map(l => Object.assign({}, l));

    function commit(mutate) {
      if (!layers.length) return;
      const before = snapshot();
      const changed = mutate();
      if (changed === false) return;
      undo.push(before);
      if (undo.length > MAX_UNDO) undo.shift();
      redo = [];
      afterChange();
    }

    function afterChange() {
      mixCache = null;
      if (sel && (sel.from > total() || sel.to > total())) sel = null;
      if (view.to > total() || view.to <= view.from) view = { from: 0, to: total() };
      cursor = clampSec(cursor);
      stop();
      draw(); on(state());
    }

    /** Replace the active layer's buffer. Length changes invalidate the selection: every
        sample after the edit has moved, so the same seconds now address different sound and
        the next operation would land somewhere the user never chose. */
    function setActiveBuffer(next) {
      const l = cur();
      if (!l || !next || next === l.buffer) return false;
      const lengthChanged = next.length !== l.buffer.length;
      l.buffer = next;
      if (lengthChanged) sel = null;
      return true;
    }

    // ---- drawing ---------------------------------------------------------
    function resize() {
      const dpr = global.devicePixelRatio || 1;
      const w = wrap.clientWidth, h = wrap.clientHeight;
      if (!w || !h) return;
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
      cv.style.width = w + 'px'; cv.style.height = h + 'px';
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function css(v, fallback) {
      const s = getComputedStyle(document.documentElement).getPropertyValue(v).trim();
      return s || fallback;
    }

    function laneRect(i) {
      const h = cv.clientHeight, n = Math.max(1, layers.length);
      const lh = h / n;
      return { y: i * lh, h: lh };
    }

    function draw() {
      empty.style.display = layers.length ? 'none' : '';
      const w = cv.clientWidth, h = cv.clientHeight;
      g.clearRect(0, 0, w, h);
      if (!layers.length) { timeEl.textContent = ''; return; }

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

        // Only the visible slice of THIS layer is scanned, so a long timeline costs the same
        // to redraw zoomed in as zoomed out.
        const from = Math.max(0, view.from - l.offset);
        const to = Math.min(l.buffer.duration, view.to - l.offset);
        if (to > from) {
          const slice = (from <= 0 && to >= l.buffer.duration) ? l.buffer : A.trim(l.buffer, from, to);
          const x0 = Math.max(0, timeToX(l.offset + Math.max(0, from)));
          const x1 = Math.min(w, timeToX(l.offset + to));
          const cols = Math.max(1, Math.floor(x1 - x0));
          const pk = A.peaks(slice, cols);
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

      const [f, to] = range();
      timeEl.textContent = sel
        ? `${t('ed.selection')}: ${fmt(f)} – ${fmt(to)}  (${fmt(to - f)})`
        : `${t('ed.length')}: ${fmt(total())} · ${t('ed.at')} ${fmt(play ? playhead() : cursor)}`;
    }

    // ---- pointer ---------------------------------------------------------
    let dragging = false, dragFrom = 0, moved = false;
    cv.addEventListener('pointerdown', e => {
      if (!layers.length) return;
      cv.setPointerCapture(e.pointerId);
      // Clicking a lane also selects that layer: with several stacked, "which one am I about
      // to cut" must be answerable by pointing at it.
      const n = Math.max(1, layers.length);
      // Guarded: a pointer event without a usable offsetY would make this NaN, and a NaN
      // index means NO layer is active — every edit would then quietly do nothing while the
      // buttons still looked enabled. Fall back to whatever was selected before.
      const lane = Math.floor(e.offsetY / (cv.clientHeight / n));
      const i = Number.isFinite(lane) ? Math.max(0, Math.min(n - 1, lane)) : active;
      if (i !== active) { active = i; on(state()); }
      dragging = true; moved = false;
      dragFrom = clampSec(xToTime(e.offsetX));
      draw();
    });
    cv.addEventListener('pointermove', e => {
      if (!dragging) return;
      const at = clampSec(xToTime(e.offsetX));
      if (Math.abs(at - dragFrom) > 0.01) { moved = true; sel = { from: dragFrom, to: at }; }
      draw(); on(state());
    });
    const endDrag = () => {
      if (!dragging) return;
      dragging = false;
      // A click that did not drag is a CURSOR move, not an empty selection: it is how you
      // say "start playing here" and where the next Mark in will land.
      if (!moved) { cursor = dragFrom; sel = null; }
      draw(); on(state());
    };
    cv.addEventListener('pointerup', endDrag);
    cv.addEventListener('pointercancel', endDrag);

    // ---- playback --------------------------------------------------------
    function mix() {
      if (!mixCache) mixCache = A.mixdown(layers.map(l => ({
        buffer: l.buffer, offset: l.offset, gain: l.gain,
        muted: l.muted || (layers.some(x => x.solo) && !l.solo),
      })));
      return mixCache;
    }

    function playhead() {
      if (!play) return cursor;
      return Math.min(play.until, play.offset + (play.ctx.currentTime - play.startedAt));
    }

    function stop() {
      if (play) {
        // Keep the cursor where the ear stopped: pressing play again resumes from there,
        // which is what makes listen-then-mark practical.
        cursor = clampSec(playhead());
        try { play.src.stop(); } catch (e) {}
        play = null;
      }
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      draw(); on(state());
    }

    /** Play from `at` (default: the cursor) to `until` (default: the end of the timeline).
        Playing to the END rather than to the end of a selection is what lets someone hold
        the mouse off the keyboard, listen, and mark the out-point when they hear it. */
    function start(at, until) {
      const buf = mix();
      if (!buf) return;
      stop();
      const C = global.AudioContext || global.webkitAudioContext;
      const actx = new C();
      const src = actx.createBufferSource();
      src.buffer = buf;
      src.connect(actx.destination);
      const from = clampSec(at == null ? cursor : at);
      const to = Math.min(buf.duration, until == null ? buf.duration : until);
      if (to - from < 0.01) return;
      play = { src, ctx: actx, startedAt: actx.currentTime, offset: from, until: to };
      src.start(0, from, to - from);
      src.onended = () => {
        if (play && play.src === src) { cursor = clampSec(to); play = null; draw(); on(state()); }
      };
      const tick = () => { if (!play) return; draw(); raf = requestAnimationFrame(tick); };
      tick();
      on(state());
    }

    // ---- zoom ------------------------------------------------------------
    function zoom(factor, centreSec) {
      if (!layers.length) return;
      const c = centreSec == null ? (view.from + view.to) / 2 : centreSec;
      const len = Math.max(0.02, Math.min(total(), viewLen() / factor));
      let from = c - len / 2, to = c + len / 2;
      if (from < 0) { to -= from; from = 0; }
      if (to > total()) { from -= (to - total()); to = total(); }
      view = { from: Math.max(0, from), to: Math.min(total(), to) };
      draw(); on(state());
    }
    cv.addEventListener('wheel', e => {
      if (!layers.length || !e.ctrlKey) return;
      e.preventDefault();
      zoom(e.deltaY < 0 ? 1.25 : 0.8, xToTime(e.offsetX));
    }, { passive: false });

    // ---- state -----------------------------------------------------------
    function state() {
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
        canUndo: undo.length > 0, canRedo: redo.length > 0,
        playing: !!play,
      };
    }

    async function decode(file) {
      const C = global.AudioContext || global.webkitAudioContext;
      const actx = new C();
      try { return await actx.decodeAudioData(await file.arrayBuffer()); }
      finally { try { actx.close(); } catch (e) {} }
    }

    /** Add a file as a new layer. The first one defines the view; later ones land at the
        cursor, which is how you place a jingle or a bed exactly where you were listening. */
    async function add(file, atCursor) {
      const buffer = await decode(file);
      const before = snapshot();
      layers.push({
        id: nextId++, buffer, offset: atCursor && layers.length ? cursor : 0,
        name: (file.name || 'layer').replace(/\.[^.]+$/, ''),
        gain: 1, muted: false, solo: false,
      });
      active = layers.length - 1;
      if (layers.length === 1) { view = { from: 0, to: total() }; cursor = 0; }
      undo.push(before); redo = [];
      afterChange();
      return state();
    }

    const editActive = fn => commit(() => {
      const l = cur(), r = localRange();
      if (!l || !r) return false;
      return setActiveBuffer(fn(l.buffer, r[0], r[1]));
    });

    const api = {
      add, state, draw, resize,

      // transport
      play: () => start(), stop,
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
      zoomIn: () => zoom(1.6), zoomOut: () => zoom(1 / 1.6),
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
        l.gain = Math.max(0, Math.min(4, (l.gain || 1) * A.dbToGain(db))); return true;
      }),
      toggleMute: i => commit(() => { const l = layers[i]; if (!l) return false; l.muted = !l.muted; return true; }),
      toggleSolo: i => commit(() => { const l = layers[i]; if (!l) return false; l.solo = !l.solo; return true; }),
      renameLayer: (i, n) => commit(() => { const l = layers[i]; if (!l) return false; l.name = n || l.name; return true; }),

      /** Flatten every layer into one. The mix is what export and playback already use, so
          this only makes visible what the ear was hearing — and gives the per-sample tools
          (fades, normalise) something single to work on. */
      flatten: () => commit(() => {
        const m = mix(); if (!m || layers.length < 2) return false;
        layers = [{ id: nextId++, buffer: m, offset: 0, gain: 1, muted: false, solo: false,
                    name: t('ed.mixname') }];
        active = 0;
        return true;
      }),

      // edits on the active layer
      cut: () => editActive(A.cut),
      trim: () => editActive(A.trim),
      silence: () => editActive(A.silence),
      insertSilence: secs => commit(() => {
        const l = cur(), r = localRange(); if (!l) return false;
        return setActiveBuffer(A.insertSilence(l.buffer, r ? r[0] : 0, secs));
      }),
      fadeIn: () => editActive((b, f, to) => A.fade(b, f, to, 'in')),
      fadeOut: () => editActive((b, f, to) => A.fade(b, f, to, 'out')),
      normalize: () => editActive((b, f, to) => A.normalize(b, null, f, to)),
      gainDb: db => editActive((b, f, to) => A.gain(b, A.dbToGain(db), f, to)),
      reverse: () => editActive(A.reverse),
      invert: () => editActive(A.invert),

      toMono: () => commit(() => setActiveBuffer(A.toMono(cur().buffer))),
      toStereo: () => commit(() => setActiveBuffer(A.toStereo(cur().buffer))),
      swapChannels: () => commit(() => setActiveBuffer(A.swapChannels(cur().buffer))),
      extractChannel: c => commit(() => setActiveBuffer(A.extractChannel(cur().buffer, c))),
      muteChannel: c => commit(() => setActiveBuffer(A.muteChannel(cur().buffer, c))),

      undo: () => { if (!undo.length) return; redo.push(snapshot()); layers = undo.pop(); active = Math.min(active, layers.length - 1); sel = null; afterChange(); },
      redo: () => { if (!redo.length) return; undo.push(snapshot()); layers = redo.pop(); active = Math.min(active, layers.length - 1); sel = null; afterChange(); },

      /** The finished timeline: every layer mixed, as a WAV blob. */
      wav: () => {
        const m = mix();
        return m ? { blob: A.toWav(m), name: (layers[0] && layers[0].name) || 'audio' } : null;
      },
      mixdown: mix,
    };

    global.addEventListener('resize', () => { resize(); draw(); });
    document.addEventListener('cq:theme', () => draw());
    resize(); draw();
    return api;
  }

  global.CQEditor = Editor;
})(window);
