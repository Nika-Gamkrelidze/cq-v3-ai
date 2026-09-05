/* The audio editor: waveform, selection, playback, and the wiring to CQAudio's operations.
   ======================================================================================
   WHY THERE IS NO WAVEFORM LIBRARY HERE.

   wavesurfer.js is the standard answer for waveforms on the web and it is a good library —
   it was evaluated for this and rejected for THIS job specifically. It is built around
   "load media, draw it, play it": the picture comes from a decoded media element. An editor
   mutates the audio constantly, so every cut, gain and undo means re-encoding the whole
   recording to a blob and handing it back. On the half-hour stereo calls this product exists
   to work with, that is hundreds of megabytes of encode per keystroke, and undo becomes the
   slowest button on the page.

   What it actually buys is drawing peaks and a drag-to-select rectangle. Peaks we already
   compute in audio-edit-core.js (a scan, no decode), and the rest is the code below. So the
   waveform is drawn straight from the live AudioBuffer, an edit costs one peak scan, and the
   app keeps a frontend with no third-party JavaScript at all — which for a product whose
   whole promise is that call recordings never leave the deployment is worth more than a
   saved afternoon.

   Playback is Web Audio (AudioBufferSourceNode), for the same reason: it plays exactly the
   buffer on screen, including unsaved edits, with no media element to keep in sync. */
(function (global) {
  'use strict';

  const A = global.CQAudio;

  function Editor(host, opts) {
    opts = opts || {};
    const t = opts.t || (k => k);
    const on = opts.onChange || function () {};

    // ---- state ----------------------------------------------------------
    let buf = null;                 // the audio as it now stands
    let name = 'audio';
    let undo = [], redo = [];       // stacks of whole buffers; see the note in the core
    let sel = null;                 // {from,to} in seconds, or null for "everything"
    let view = { from: 0, to: 0 };  // the visible span in seconds (zoom/scroll)
    let play = null;                // {src, ctx, startedAt, offset}
    let raf = 0;

    const MAX_UNDO = 30;            // whole buffers: bounded so a long session cannot OOM

    // ---- dom ------------------------------------------------------------
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

    // ---- helpers --------------------------------------------------------
    const dur = () => (buf ? buf.duration : 0);
    const viewLen = () => Math.max(1e-6, view.to - view.from);
    const xToTime = x => view.from + (x / cv.clientWidth) * viewLen();
    const timeToX = s => ((s - view.from) / viewLen()) * cv.clientWidth;
    const clampSec = s => Math.max(0, Math.min(dur(), s));

    function fmt(s) {
      if (!isFinite(s)) return '0:00.0';
      const m = Math.floor(s / 60), r = s - m * 60;
      return m + ':' + (r < 10 ? '0' : '') + r.toFixed(1);
    }

    /** The range an operation applies to: the selection, or the whole file when there is
        none. Returned as [from,to] so every caller treats "no selection" identically. */
    function range() {
      if (sel && Math.abs(sel.to - sel.from) > 1e-4) {
        return [Math.min(sel.from, sel.to), Math.max(sel.from, sel.to)];
      }
      return [0, dur()];
    }

    function commit(next) {
      if (!next || next === buf) return;
      undo.push(buf);
      if (undo.length > MAX_UNDO) undo.shift();
      redo = [];
      const lengthChanged = next.length !== buf.length;
      buf = next;
      /* A selection that outlived the audio it pointed at is worse than none. Anything that
         changes the LENGTH — cut, trim, insert — shifts every sample after it, so the same
         seconds now address different sound; keeping the highlight there would invite the
         next operation to be applied to the wrong place while looking correct. Operations
         that preserve length (silence, gain, fades, channel work) leave it meaning exactly
         what it did, so those keep it. */
      if (lengthChanged || (sel && (sel.to > dur() || sel.from > dur()))) sel = null;
      if (view.to > dur() || view.to <= view.from) view = { from: 0, to: dur() };
      stop();
      draw(); on(state());
    }

    // ---- drawing --------------------------------------------------------
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

    function draw() {
      empty.style.display = buf ? 'none' : '';
      if (!buf) { g.clearRect(0, 0, cv.clientWidth, cv.clientHeight); timeEl.textContent = ''; return; }
      const w = cv.clientWidth, h = cv.clientHeight;
      g.clearRect(0, 0, w, h);

      const chans = buf.numberOfChannels;
      const laneH = h / chans;
      // Peaks are computed over the VISIBLE span only, so zooming in costs the same as
      // zooming out and a long recording does not scan itself for every repaint.
      const sliced = sliceForView();

      for (let c = 0; c < chans; c++) {
        const pk = A.peaks(sliced, Math.max(1, Math.floor(w)), c);
        const mid = laneH * c + laneH / 2;
        const amp = (laneH / 2) * 0.86;

        g.fillStyle = css('--input-bg', 'rgba(0,0,0,.2)');
        g.fillRect(0, laneH * c, w, laneH - 1);

        g.strokeStyle = css('--hairline', '#345');
        g.beginPath(); g.moveTo(0, mid); g.lineTo(w, mid); g.stroke();

        g.fillStyle = css('--beam', '#fa3b3c');
        for (let x = 0; x < pk.length; x++) {
          const y1 = mid - pk[x].max * amp, y2 = mid - pk[x].min * amp;
          g.fillRect(x, y1, 1, Math.max(1, y2 - y1));
        }
        if (chans > 1) {
          g.fillStyle = css('--muted', '#8aa');
          g.font = '11px system-ui, sans-serif';
          g.fillText(t('ed.channel') + ' ' + (c + 1), 6, laneH * c + 14);
        }
      }

      if (sel) {
        const a = timeToX(Math.min(sel.from, sel.to)), b = timeToX(Math.max(sel.from, sel.to));
        g.fillStyle = 'rgba(250,59,60,.18)';
        g.fillRect(a, 0, Math.max(1, b - a), h);
        g.strokeStyle = css('--beam', '#fa3b3c');
        g.beginPath(); g.moveTo(a, 0); g.lineTo(a, h); g.moveTo(b, 0); g.lineTo(b, h); g.stroke();
      }
      if (play) {
        const x = timeToX(playhead());
        g.strokeStyle = css('--paper', '#fff');
        g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke();
      }
      const [f, to] = range();
      timeEl.textContent = sel
        ? `${t('ed.selection')}: ${fmt(f)} – ${fmt(to)}  (${fmt(to - f)})`
        : `${t('ed.length')}: ${fmt(dur())} · ${buf.sampleRate} Hz · ${chans} ch`;
    }

    /** The visible span as its own buffer, so peaks() draws only what is on screen. */
    function sliceForView() {
      if (view.from <= 0 && view.to >= dur()) return buf;
      return A.trim(buf, view.from, view.to);
    }

    // ---- selection ------------------------------------------------------
    let dragging = false, dragFrom = 0;
    cv.addEventListener('pointerdown', e => {
      if (!buf) return;
      cv.setPointerCapture(e.pointerId);
      dragging = true;
      dragFrom = clampSec(xToTime(e.offsetX));
      sel = { from: dragFrom, to: dragFrom };
      draw();
    });
    cv.addEventListener('pointermove', e => {
      if (!dragging || !buf) return;
      sel = { from: dragFrom, to: clampSec(xToTime(e.offsetX)) };
      draw(); on(state());
    });
    const endDrag = () => {
      if (!dragging) return;
      dragging = false;
      // A click (no drag) clears the selection rather than leaving a zero-width one, which
      // would read as "a selection exists" to every operation while meaning nothing.
      if (sel && Math.abs(sel.to - sel.from) < 1e-3) sel = null;
      draw(); on(state());
    };
    cv.addEventListener('pointerup', endDrag);
    cv.addEventListener('pointercancel', endDrag);

    // ---- playback -------------------------------------------------------
    function playhead() {
      if (!play) return view.from;
      const el = play.ctx.currentTime - play.startedAt;
      return Math.min(play.until, play.offset + el);
    }

    function stop() {
      if (play) { try { play.src.stop(); } catch (e) {} play = null; }
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      draw(); on(state());
    }

    function start() {
      if (!buf) return;
      stop();
      const C = global.AudioContext || global.webkitAudioContext;
      const actx = new C();
      const src = actx.createBufferSource();
      src.buffer = buf;
      src.connect(actx.destination);
      const [f, to] = range();
      play = { src, ctx: actx, startedAt: actx.currentTime, offset: f, until: to };
      src.start(0, f, Math.max(0.01, to - f));
      src.onended = () => { if (play && play.src === src) { play = null; draw(); on(state()); } };
      const tick = () => { if (!play) return; draw(); raf = requestAnimationFrame(tick); };
      tick();
      on(state());
    }

    // ---- zoom -----------------------------------------------------------
    function zoom(factor, centreSec) {
      if (!buf) return;
      const c = centreSec == null ? (view.from + view.to) / 2 : centreSec;
      const len = Math.max(0.02, Math.min(dur(), viewLen() / factor));
      let from = c - len / 2, to = c + len / 2;
      if (from < 0) { to -= from; from = 0; }
      if (to > dur()) { from -= (to - dur()); to = dur(); }
      view = { from: Math.max(0, from), to: Math.min(dur(), to) };
      draw(); on(state());
    }
    cv.addEventListener('wheel', e => {
      if (!buf || !e.ctrlKey) return;      // plain scroll stays page scroll
      e.preventDefault();
      zoom(e.deltaY < 0 ? 1.25 : 0.8, xToTime(e.offsetX));
    }, { passive: false });

    // ---- public surface -------------------------------------------------
    function state() {
      const [f, to] = range();
      return {
        loaded: !!buf, name,
        duration: dur(), channels: buf ? buf.numberOfChannels : 0,
        sampleRate: buf ? buf.sampleRate : 0,
        hasSelection: !!sel, from: f, to: to,
        canUndo: undo.length > 0, canRedo: redo.length > 0,
        playing: !!play,
        zoomed: !!buf && (view.from > 0 || view.to < dur()),
      };
    }

    /** Decode a File/Blob into the editor. Rejects with a translated message. */
    async function load(file) {
      const C = global.AudioContext || global.webkitAudioContext;
      const actx = new C();
      try {
        const bytes = await file.arrayBuffer();
        // decodeAudioData handles whatever the browser can: wav, mp3, m4a, ogg/opus, flac.
        // Anything it refuses (amr, gsm, sln) is a real answer, not a bug — those are the
        // telephony formats the SERVER's ffmpeg exists for, so the message says so.
        const decoded = await actx.decodeAudioData(bytes);
        buf = decoded; undo = []; redo = []; sel = null;
        name = (file.name || 'audio').replace(/\.[^.]+$/, '');
        view = { from: 0, to: decoded.duration };
        resize(); draw(); on(state());
        return state();
      } finally {
        try { actx.close(); } catch (e) {}
      }
    }

    const apply = fn => commit(fn(buf, ...range()));

    const api = {
      load, state, draw, resize,
      play: start, stop, toggle: () => (play ? stop() : start()),
      zoomIn: () => zoom(1.6), zoomOut: () => zoom(1 / 1.6),
      zoomFit: () => { view = { from: 0, to: dur() }; draw(); on(state()); },
      zoomSelection: () => { if (sel) { const [f, to] = range(); view = { from: f, to }; draw(); on(state()); } },
      selectAll: () => { sel = null; draw(); on(state()); },

      cut: () => apply(A.cut),
      trim: () => apply(A.trim),
      silence: () => apply(A.silence),
      insertSilence: secs => commit(A.insertSilence(buf, range()[0], secs)),
      fadeIn: () => apply((b, f, to) => A.fade(b, f, to, 'in')),
      fadeOut: () => apply((b, f, to) => A.fade(b, f, to, 'out')),
      normalize: () => apply((b, f, to) => A.normalize(b, null, f, to)),
      gainDb: db => commit(A.gain(buf, A.dbToGain(db), ...range())),
      reverse: () => apply(A.reverse),
      invert: () => apply(A.invert),

      toMono: () => commit(A.toMono(buf)),
      toStereo: () => commit(A.toStereo(buf)),
      swapChannels: () => commit(A.swapChannels(buf)),
      extractChannel: c => commit(A.extractChannel(buf, c)),
      channelGainDb: (c, db) => commit(A.channelGain(buf, c, A.dbToGain(db))),
      muteChannel: c => commit(A.muteChannel(buf, c)),

      undo: () => { if (!undo.length) return; redo.push(buf); buf = undo.pop(); sel = null; view = { from: 0, to: dur() }; stop(); draw(); on(state()); },
      redo: () => { if (!redo.length) return; undo.push(buf); buf = redo.pop(); sel = null; view = { from: 0, to: dur() }; stop(); draw(); on(state()); },

      /** The edited audio as a WAV Blob, plus a filename stem. */
      wav: () => ({ blob: A.toWav(buf), name }),
      buffer: () => buf,
    };

    global.addEventListener('resize', () => { resize(); draw(); });
    document.addEventListener('cq:theme', () => draw());
    resize(); draw();
    return api;
  }

  global.CQEditor = Editor;
})(window);
