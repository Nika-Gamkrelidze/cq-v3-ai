/* CQ.Timeline — the call player + findings timeline (design contract §13.1).

   One recording, one player, every analyser's findings as a toggleable lane on the same
   time axis. Self-contained: injects its own <style id="cq-timeline-css">, registers its own
   en/ka/ru strings with CQ.extendDict (keys `tl.*`), and depends only on brand.js (CQ.t,
   CQ.extendDict, CQ.toast, CQ.applyI18n) and the brand.css tokens. No third-party code.

   Usage:
     const tl = CQ.Timeline(container, {
       src: File|Blob|string|null,   // audio; null = text mode (transcript only)
       duration: seconds|null,       // server duration, used until <audio> reports its own
       segments: [{i, speaker, start, end, text}],          // §2
       speakerLabels: {speaker_0: 'Agent'},                 // optional
       lanes: [{id, name, color?, spans: [...]}],           // §3 spans
       fetchInit: {headers: ...},    // passed to fetch() when src is a url
       filename: 'call.mp3',         // download name (defaults to the File's name)
       onSeek(t), onSpanClick(lane, span), onSegment(i)
     });
     tl.setLanes(lanes); tl.addLane(lane); tl.removeLane(id); tl.toggleLane(id, on);
     tl.seek(t); tl.play(); tl.pause(); tl.highlightSegment(i); tl.setSpeakerLabels(map);
     tl.markSegments(laneId, [{i, level|score, title}]); tl.destroy();

   Colour rules (§3): level good/mid/bad/none → --ready/--pending/--contradicted/--muted
   (brand.css names them --ok/--pending/--alert/--muted; the module accepts either, the
   contract's names win when a page defines them); a numeric `score` uses the hue gradient
   hsl(score*1.2, S, L) instead, S/L per theme (65%/45% dark, 60%/30% light) so a span keeps
   ≥3:1 against its track — the light theme also darkens the three level tokens for the same
   reason. Every span also carries `segments` — those indices are mirrored onto the transcript
   paragraphs' left strip, which is how text-mode results are read; when several visible lanes
   mark one paragraph the strip is divided between their colours (none is overwritten), and
   markSegments() replaces the mirrored marks of the lane it names.

   Playback is a plain <audio> on an object URL. The waveform is decoded separately
   (OfflineAudioContext.decodeAudioData → ~800 min/max buckets) AFTER the transport has
   painted; a file the decoder rejects gets a flat bar and still seeks. A url src is fetched
   ONCE with fetchInit (the endpoint needs auth headers a bare <audio src> cannot send) and
   that blob feeds both the player and the decoder. Object URLs are revoked in destroy(). */
(function () {
  'use strict';
  // brand.js declares `const CQ` at script top level: a global lexical binding, not a
  // window property, so it is reached by name and never through window.CQ.
  if (typeof CQ === 'undefined' || typeof CQ.extendDict !== 'function') {
    console.error('timeline.js: brand.js must be loaded first');
    return;
  }

  /* ---------------- strings ---------------- */
  CQ.extendDict({
    en: {
      'tl.play': 'Play',
      'tl.pause': 'Pause',
      'tl.speed': 'Playback speed',
      'tl.download': 'Download recording',
      'tl.position': 'Playback position',
      'tl.keyhint': 'Space plays or pauses, ← and → skip 5 seconds, Home and End jump to the start or the end.',
      'tl.speakers': 'Speakers',
      'tl.speaker': 'Speaker {n}',
      'tl.layers': 'Layers',
      'tl.loading': 'Loading waveform…',
      'tl.decodefail': 'Waveform unavailable — seeking still works.',
      'tl.playfail': 'The recording cannot be played in this browser. You can still read the transcript and download the file.',
      'tl.loadfail': 'Could not load the recording.',
      'tl.nosegments': 'The transcript is empty.',
      'tl.goto': 'Go to {t}',
    },
    ka: {
      'tl.play': 'დაკვრა',
      'tl.pause': 'პაუზა',
      'tl.speed': 'დაკვრის სიჩქარე',
      'tl.download': 'ჩანაწერის ჩამოტვირთვა',
      'tl.position': 'დაკვრის პოზიცია',
      'tl.keyhint': 'Space — დაკვრა ან პაუზა, ← და → — 5 წამით უკან ან წინ, Home და End — დასაწყისში ან დასასრულში გადასვლა.',
      'tl.speakers': 'მოსაუბრეები',
      'tl.speaker': 'მოსაუბრე {n}',
      'tl.layers': 'ფენები',
      'tl.loading': 'ტალღის ფორმა იტვირთება…',
      'tl.decodefail': 'ტალღის ფორმა მიუწვდომელია — გადახვევა მაინც მუშაობს.',
      'tl.playfail': 'ჩანაწერის დაკვრა ამ ბრაუზერში ვერ ხერხდება. ტრანსკრიფციის წაკითხვა და ფაილის ჩამოტვირთვა კვლავ შესაძლებელია.',
      'tl.loadfail': 'ჩანაწერის ჩატვირთვა ვერ მოხერხდა.',
      'tl.nosegments': 'ტრანსკრიფცია ცარიელია.',
      'tl.goto': 'გადასვლა: {t}',
    },
    ru: {
      'tl.play': 'Воспроизвести',
      'tl.pause': 'Пауза',
      'tl.speed': 'Скорость воспроизведения',
      'tl.download': 'Скачать запись',
      'tl.position': 'Позиция воспроизведения',
      'tl.keyhint': 'Пробел — воспроизведение или пауза, ← и → — на 5 секунд назад или вперёд, Home и End — в начало или в конец.',
      'tl.speakers': 'Говорящие',
      'tl.speaker': 'Говорящий {n}',
      'tl.layers': 'Слои',
      'tl.loading': 'Загрузка формы волны…',
      'tl.decodefail': 'Форма волны недоступна — перемотка по-прежнему работает.',
      'tl.playfail': 'Запись не воспроизводится в этом браузере. Расшифровку можно читать, а файл — скачать.',
      'tl.loadfail': 'Не удалось загрузить запись.',
      'tl.nosegments': 'Расшифровка пуста.',
      'tl.goto': 'Перейти к {t}',
    },
  });

  /* ---------------- styles (injected once) ----------------
     Tokens: the contract names --ready/--contradicted/--card/--line/--accent/--text; brand.css
     ships --ok/--alert/--card-bg/--hairline/--beam/--paper. var(a, var(b)) takes whichever
     exists, contract name first. Everything below is prefixed tl- and scoped to .tl.
     --tl-sat/--tl-lig are the S and L of the §3 score gradient; they are theme-scoped so a
     score colour stays readable on the near-white light track (see the light block below). */
  const SHEET = `
.tl { --tl-good: var(--ready, var(--ok)); --tl-mid: var(--mid, var(--pending)); --tl-bad: var(--contradicted, var(--alert));
  --tl-none: var(--muted); --tl-line: var(--line, var(--hairline)); --tl-card: var(--card, var(--card-bg));
  --tl-accent: var(--accent, var(--beam)); --tl-text: var(--text, var(--paper)); --tl-pos: 0%;
  --tl-sat: 65%; --tl-lig: 45%;
  position: relative; font-family: var(--font-sans); color: var(--tl-text); }
/* Light theme: the brand level tokens and the §3 gradient both sit at 1.5–2.6:1 against the
   light lane track (rgb(217,218,218)) — unreadable as the only carrier of a verdict. Darkened
   here to ≥3.4:1 (levels) and ≥3.2:1 (gradient, worst case score 50) while keeping each hue. */
[data-theme="light"] .tl { --tl-good: #1a804f; --tl-mid: #eab308; --tl-bad: #e20607;
  --tl-sat: 60%; --tl-lig: 30%; }
.tl *, .tl *::before, .tl *::after { box-sizing: border-box; }
.tl:focus-visible { outline: none; }

/* transport */
.tl-transport { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.tl-transport .cq-play { margin: 0; transition: transform .16s var(--ease); }
.tl-transport .cq-play:disabled { opacity: .5; cursor: default; transform: none; }
.tl-time { font-size: 12.5px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.tl-time b { color: var(--tl-text); font-weight: 600; }
.tl-speed { display: inline-flex; gap: 4px; margin-left: auto; }
.tl-speed button { font-family: inherit; border: 1px solid var(--tl-line); background: transparent; color: var(--mist);
  border-radius: 999px; padding: 5px 10px; font-size: 11.5px; font-weight: 700; cursor: pointer; line-height: 1.2;
  transition: all .16s var(--ease); }
.tl-speed button:hover { color: var(--tl-text); border-color: color-mix(in oklab, var(--tl-accent) 55%, transparent); }
.tl-speed button.on { background: color-mix(in oklab, var(--tl-accent) 16%, transparent);
  border-color: color-mix(in oklab, var(--tl-accent) 55%, transparent); color: var(--tl-accent); }
.tl-speed button:disabled { opacity: .45; cursor: default; }
.tl-speed button:disabled:hover { color: var(--mist); border-color: var(--tl-line); }
.tl-dl { margin: 0; }
.tl-dl[aria-disabled="true"] { opacity: .45; pointer-events: none; }

/* waveform + playhead */
.tl-wave { position: relative; height: 72px; border-radius: var(--r-md); border: 1px solid var(--tl-line);
  background: var(--input-bg); overflow: hidden; cursor: pointer; touch-action: none; user-select: none; }
.tl-wave:focus-visible { outline: 2px solid var(--tl-accent); outline-offset: 2px; }
.tl-canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
.tl-played { position: absolute; top: 0; bottom: 0; left: 0; width: var(--tl-pos);
  background: color-mix(in oklab, var(--tl-accent) 14%, transparent); pointer-events: none; }
.tl-playhead { position: absolute; top: 0; bottom: 0; left: var(--tl-pos); width: 2px; margin-left: -1px;
  background: var(--tl-accent); box-shadow: 0 0 8px color-mix(in oklab, var(--tl-accent) 70%, transparent); pointer-events: none; }
.tl-wave.tl-busy::after { content: attr(data-busy); position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; font-size: 12px; color: var(--muted); pointer-events: none; }
.tl-note { font-size: 11.5px; color: var(--muted); margin-top: 5px; }

/* rows: speaker strip + one thin lane per analyser dimension */
.tl-rows { margin-top: 10px; display: flex; flex-direction: column; gap: 5px; }
.tl-row { display: grid; grid-template-columns: 132px minmax(0, 1fr); align-items: center; gap: 10px; }
.tl-row[hidden] { display: none; }
.tl-rowname { font-size: 11.5px; color: var(--muted); font-weight: 600; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; display: flex; align-items: center; gap: 6px; min-width: 0; }
.tl-rowname > span { overflow: hidden; text-overflow: ellipsis; }
.tl-sw { flex: none; width: 9px; height: 9px; border-radius: 50%; background: var(--tl-sw, var(--muted)); }
.tl-track { position: relative; height: 14px; border-radius: 4px; overflow: hidden;
  background: color-mix(in oklab, var(--muted) 14%, transparent); }
.tl-speakers .tl-track { height: 20px; }
.tl-track::after { content: ''; position: absolute; top: 0; bottom: 0; left: var(--tl-pos); width: 1px;
  background: var(--tl-accent); opacity: .75; pointer-events: none; }

/* speaker blocks */
.tl .s0 { --tl-sp: var(--mist); } .tl .s1 { --tl-sp: var(--tl-accent); } .tl .s2 { --tl-sp: var(--navy-400); }
.tl .s3 { --tl-sp: var(--coral); } .tl .s4 { --tl-sp: var(--muted); } .tl .s5 { --tl-sp: var(--tl-text); }
.tl-seg { position: absolute; top: 2px; bottom: 2px; min-width: 3px; border-radius: 3px; cursor: pointer;
  background: color-mix(in oklab, var(--tl-sp) 38%, transparent);
  border: 1px solid color-mix(in oklab, var(--tl-sp) 70%, transparent); }
.tl-seg.now { background: color-mix(in oklab, var(--tl-sp) 70%, transparent); border-color: var(--tl-text); }
.tl-seg.hl { outline: 2px solid var(--tl-accent); outline-offset: -1px; }

/* spans — fully opaque on purpose: a translucent fill blends the track back in and costs
   contrast exactly where the colour IS the information. */
.tl-span { position: absolute; top: 2px; bottom: 2px; min-width: 4px; border: 0; padding: 0; margin: 0;
  border-radius: 3px; cursor: pointer; background: var(--tl-none); }
.tl-span.lv-good { background: var(--tl-good); } .tl-span.lv-mid { background: var(--tl-mid); }
.tl-span.lv-bad { background: var(--tl-bad); } .tl-span.lv-none { background: var(--tl-none); }
.tl-span:hover, .tl-span:focus-visible { outline: 2px solid var(--tl-text); outline-offset: -1px; z-index: 1; }
/* A navy hairline separates two adjacent spans of the same colour on the light track. */
[data-theme="light"] .tl-span { box-shadow: inset 0 0 0 1px rgba(7, 38, 60, .38); }

/* legend */
.tl-legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 10px; }
.tl-legend label { display: inline-flex; align-items: center; gap: 7px; margin: 0; padding: 2px 0;
  font-size: 12px; color: var(--mist); cursor: pointer; font-weight: 500; }
.tl-legend input { width: auto; margin: 0; padding: 0; accent-color: var(--tl-accent); }
.tl-legend label.off { color: var(--muted); }
.tl-h { font-size: 11px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: var(--muted);
  margin: 14px 0 6px; }

/* transcript */
.tl-transcript { max-height: 320px; overflow: auto; border: 1px solid var(--tl-line); border-radius: var(--r-md);
  background: var(--input-bg); padding: 4px 0; scroll-behavior: smooth; }
.tl-p { margin: 0; padding: 7px 12px; border-left: 3px solid var(--tl-mark, transparent); font-size: 13px;
  line-height: 1.5; color: var(--mist); cursor: pointer; display: flex; gap: 8px; flex-wrap: wrap;
  align-items: baseline; transition: background-color .12s;
  background-origin: border-box; background-repeat: no-repeat; background-size: 3px 100%;
  background-position: left top; }
/* Marked by two or more visible lanes: the 3px strip is split between their colours instead of
   the last lane silently overwriting the others (background-color stays free for now/hover). */
.tl-p.tl-multi { background-image: var(--tl-marks); }
.tl-p:hover { background-color: color-mix(in oklab, var(--surface-2) 45%, transparent); }
.tl-p:focus-visible { outline: 2px solid var(--tl-accent); outline-offset: -2px; border-radius: 4px; }
.tl-p.now { background-color: color-mix(in oklab, var(--tl-accent) 11%, transparent); color: var(--tl-text); }
.tl-p.hl { box-shadow: inset 0 0 0 2px var(--tl-accent); }
.tl-chip { flex: none; font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 999px; line-height: 1.5;
  color: var(--tl-text); background: color-mix(in oklab, var(--tl-sp, var(--muted)) 18%, transparent);
  border: 1px solid color-mix(in oklab, var(--tl-sp, var(--muted)) 45%, transparent); }
.tl-ts { flex: none; font-size: 11.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
.tl-txt { flex: 1 1 100%; min-width: 0; }
.tl-empty { padding: 18px 12px; text-align: center; color: var(--muted); font-size: 13px; }
@media (min-width: 641px) { .tl-txt { flex: 1 1 0; } }

/* tooltip: one fixed element on <body>, same recipe as #cq-tip in brand.css; never in the
   row it explains, so opening it moves nothing. */
#cq-tl-tip { position: fixed; top: 0; left: 0; z-index: 120; visibility: hidden; opacity: 0; pointer-events: none;
  max-width: min(320px, calc(100vw - 24px)); padding: 9px 12px; border-radius: var(--r-md);
  background: var(--card-solid); border: 1px solid var(--hairline); box-shadow: var(--shadow);
  color: var(--mist); font-family: var(--font-sans); font-size: 12.5px; line-height: 1.5;
  white-space: normal; overflow-wrap: anywhere; text-align: left;
  transition: opacity .14s var(--ease), visibility .14s var(--ease); }
#cq-tl-tip.open { visibility: visible; opacity: 1; }
#cq-tl-tip b { display: block; color: var(--paper); font-weight: 600; }
#cq-tl-tip b + span { display: block; margin-top: 2px; }
#cq-tl-tip::after { content: ''; position: absolute; width: 9px; height: 9px; left: var(--cq-tip-ax, 50%);
  margin-left: -5px; background: var(--card-solid); border: 1px solid var(--hairline); transform: rotate(45deg); }
#cq-tl-tip[data-place="below"]::after { top: -5px; border-right: 0; border-bottom: 0; }
#cq-tl-tip[data-place="above"]::after { bottom: -5px; border-left: 0; border-top: 0; }

/* phone: lane names sit above their row, controls grow to thumb size */
@media (max-width: 640px) {
  .tl-row { grid-template-columns: minmax(0, 1fr); gap: 3px; }
  .tl-rowname { font-size: 11px; }
  .tl-wave { height: 64px; }
  .tl-speed { margin-left: 0; }
  .tl-speed button { padding: 10px 13px; font-size: 12.5px; min-height: 44px; }
  .tl-transport .cq-dl { width: 44px; height: 44px; }
  .tl-track { height: 16px; }
  .tl-speakers .tl-track { height: 22px; }
  .tl-p { padding: 9px 12px; }
  /* a legend row is a checkbox you hit with a thumb, not a mouse: 44px tall, 20px box */
  .tl-legend { gap: 0 14px; }
  .tl-legend label { min-height: 44px; padding: 10px 0; font-size: 12.5px; }
  .tl-legend input { width: 20px; height: 20px; }
}
@media (prefers-reduced-motion: reduce) { .tl-transcript { scroll-behavior: auto; } }
`;
  if (!document.getElementById('cq-timeline-css')) {
    const s = document.createElement('style');
    s.id = 'cq-timeline-css';
    s.textContent = SHEET;
    document.head.appendChild(s);
  }

  /* ---------------- helpers ---------------- */
  const t = (k) => CQ.t(k);
  const esc = (s) => (s == null ? '' : String(s)).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  /* §2 says floats, but a backend that serialises times as JSON strings must not silently lose
     them: a numeric string is a number here. Everything else (null, '', true, 'x') stays null. */
  function num(v) {
    if (typeof v === 'number') return isFinite(v) ? v : null;
    if (typeof v === 'string' && v.trim() !== '') { const n = Number(v); return isFinite(n) ? n : null; }
    return null;
  }
  /* Same leniency for segment indices, which are array keys everywhere else in the module. */
  function int(v) { const n = num(v); return n !== null && Number.isInteger(n) ? n : null; }
  /* One escaper for the attribute selectors built from lane ids. CSS.escape must be reached
     through `window` — the module's own SHEET/const names are what shadowing bugs are made of. */
  const cssEsc = (s) => (window.CSS && typeof window.CSS.escape === 'function'
    ? window.CSS.escape(String(s)) : String(s).replace(/["\\]/g, '\\$&'));
  /* A task-queue yield: setTimeout is clamped to 1s+ (and far worse under intensive throttling)
     in a background tab, which used to strand the waveform for minutes. postMessage is not. */
  const yieldNow = () => new Promise((res) => {
    if (typeof MessageChannel === 'function') {
      const ch = new MessageChannel();
      ch.port1.onmessage = () => { ch.port1.close(); res(); };
      ch.port2.postMessage(0);
    } else setTimeout(res, 0);
  });
  function fmt(s) {
    s = Math.max(0, Math.floor(+s || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(x).padStart(2, '0');
  }
  /* §3: numeric score → hue gradient 0=red … 120=green; otherwise the level token. Saturation
     and lightness come from --tl-sat/--tl-lig so the value re-resolves on a theme switch —
     65%/45% on dark, 60%/30% on light, where 45% is invisible against the track. */
  function levelColor(level, score) {
    const v = num(score);
    if (v !== null) return `hsl(${Math.round(Math.max(0, Math.min(100, v)) * 1.2)} var(--tl-sat, 65%) var(--tl-lig, 45%))`;
    return { good: 'var(--tl-good)', mid: 'var(--tl-mid)', bad: 'var(--tl-bad)' }[level] || 'var(--tl-none)';
  }
  const levelClass = (level) => 'lv-' + (['good', 'mid', 'bad'].indexOf(level) >= 0 ? level : 'none');
  /* Scroll `el` into `box`'s visible area without moving the page (scrollIntoView would). */
  function scrollWithin(box, el) {
    if (!box || !el) return;
    const top = el.offsetTop - box.offsetTop, bottom = top + el.offsetHeight;
    if (top < box.scrollTop + 8) box.scrollTop = Math.max(0, top - 8);
    else if (bottom > box.scrollTop + box.clientHeight - 8) box.scrollTop = bottom - box.clientHeight + 8;
  }

  /* ---------------- shared span tooltip ---------------- */
  let tipEl = null, tipOwner = null, tipRaf = 0;
  function tipBox() {
    if (tipEl) return tipEl;
    tipEl = document.createElement('div');
    tipEl.id = 'cq-tl-tip';
    tipEl.setAttribute('role', 'tooltip');
    tipEl.setAttribute('aria-hidden', 'true');
    document.body.appendChild(tipEl);
    return tipEl;
  }
  function tipPlace() {
    if (!tipOwner || !tipEl) return;
    const r = tipOwner.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    if ((!r.width && !r.height) || r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) { tipHide(); return; }
    const b = tipEl.getBoundingClientRect();
    const GAP = 8, EDGE = 10;
    const place = (r.bottom + GAP + b.height <= vh - EDGE) ? 'below' : (r.top - GAP - b.height >= EDGE) ? 'above' : 'below';
    let top = place === 'below' ? r.bottom + GAP : r.top - GAP - b.height;
    top = Math.max(EDGE, Math.min(top, vh - b.height - EDGE));
    const left = Math.max(EDGE, Math.min(r.left + r.width / 2 - b.width / 2, vw - b.width - EDGE));
    tipEl.style.top = Math.round(top) + 'px';
    tipEl.style.left = Math.round(left) + 'px';
    tipEl.dataset.place = place;
    tipEl.style.setProperty('--cq-tip-ax', Math.round(Math.max(12, Math.min(r.left + r.width / 2 - left, b.width - 12))) + 'px');
  }
  function tipReflow() { if (tipRaf) return; tipRaf = requestAnimationFrame(() => { tipRaf = 0; tipPlace(); }); }
  function tipListen(on) {
    const m = on ? 'addEventListener' : 'removeEventListener';
    window[m]('scroll', tipReflow, true);
    window[m]('resize', tipReflow);
    document[m]('keydown', tipEsc, true);
  }
  function tipEsc(e) { if (e.key === 'Escape') tipHide(); }

  /* ---- Space toggles playback anywhere on the page --------------------------------------
     A reviewer listens with one hand on the keyboard and the other on a notepad; making them
     click into the waveform first to regain Space is friction in the one place the product is
     meant to feel like a player. So the key is handled on the DOCUMENT, not just the widget.

     It must never steal Space from something that already owns it: a text box (the pasted
     transcript, the rubric guidance), a button/link/checkbox (where Space IS the activation
     key), or anything contenteditable. With several players on the page (the summarise tab
     mounts one per call) the key goes to the one the reader is actually looking at — the last
     one they touched if it is still on screen, otherwise the first visible one. */
  const LIVE = new Set();
  let lastActive = null;

  function isTypingTarget(el) {
    if (!el || el === document.body || el === document.documentElement) return false;
    if (el.isContentEditable) return true;
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
    // Space is the activation key for these; taking it would break the control.
    return tag === 'BUTTON' || tag === 'A' || tag === 'SUMMARY' || el.getAttribute('role') === 'button';
  }
  function onScreen(el) {
    if (!el || !el.isConnected || el.offsetParent === null) return false;
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < (window.innerHeight || 0) && r.width > 0;
  }
  function spaceTarget() {
    if (lastActive && LIVE.has(lastActive) && lastActive.playable() && onScreen(lastActive.root)) return lastActive;
    for (const inst of LIVE) if (inst.playable() && onScreen(inst.root)) return inst;
    return null;
  }
  function docSpace(e) {
    if (e.key !== ' ' && e.key !== 'Spacebar') return;
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.defaultPrevented) return;
    if (isTypingTarget(e.target)) return;
    const inst = spaceTarget();
    if (!inst) return;
    e.preventDefault();
    lastActive = inst;
    inst.toggle();
  }
  function register(inst) {
    if (!LIVE.size) document.addEventListener('keydown', docSpace);
    LIVE.add(inst);
  }
  function unregister(inst) {
    LIVE.delete(inst);
    if (lastActive === inst) lastActive = null;
    if (!LIVE.size) document.removeEventListener('keydown', docSpace);
  }
  function tipShow(el, label, detail) {
    const box = tipBox();
    box.innerHTML = (label ? `<b>${esc(label)}</b>` : '') + (detail ? `<span>${esc(detail)}</span>` : '');
    if (!box.textContent.trim()) { tipHide(); return; }
    tipOwner = el;
    box.classList.add('open');
    tipPlace();
    tipListen(true);
  }
  function tipHide(owner) {
    if (owner && tipOwner !== owner) return;
    if (tipEl) tipEl.classList.remove('open');
    tipOwner = null;
    tipListen(false);
  }

  /* ---------------- the component ---------------- */
  function Timeline(container, opts) {
    opts = opts || {};
    const textMode = opts.src == null;
    const state = {
      segments: [], speakers: [], labels: Object.assign({}, opts.speakerLabels || {}),
      lanes: [], hidden: new Set(), marks: new Map(), auto: new Map(),
      t: 0, cur: -1, hl: -1, audioDur: 0, decodedDur: 0,
      blob: null, objUrl: null, peaks: null, flat: false, mediaFail: false, pendingSeek: null,
      dragging: false, raf: 0, ariaSec: -1, destroyed: false,
    };
    const normSegs = (arr) => (Array.isArray(arr) ? arr : []).map((s, k) => ({
      i: s && int(s.i) !== null ? int(s.i) : k,
      speaker: (s && s.speaker) || 'speaker_0',
      start: num(s && s.start), end: num(s && s.end),
      text: (s && s.text) != null ? String(s.text) : '',
    }));
    state.segments = normSegs(opts.segments);
    state.speakers = [];
    state.segments.forEach((s) => { if (state.speakers.indexOf(s.speaker) < 0) state.speakers.push(s.speaker); });
    const spClass = (sp) => 's' + (Math.max(0, state.speakers.indexOf(sp)) % 6);
    const spLabel = (sp) => state.labels[sp] || t('tl.speaker').replace('{n}', String(Math.max(0, state.speakers.indexOf(sp)) + 1));
    const segsDur = () => state.segments.reduce((m, s) => (s.end !== null && s.end > m ? s.end : m), 0);
    /* Duration precedence: what <audio> reports, else the decoded buffer, else the server, else the last segment. */
    const D = () => state.audioDur || state.decodedDur || num(opts.duration) || segsDur() || 0;
    const pct = (v) => (D() > 0 ? (Math.max(0, Math.min(D(), v)) / D() * 100).toFixed(3) + '%' : '0%');

    /* ---- skeleton (painted synchronously; no decoding has happened yet) ---- */
    container.innerHTML = '';
    const root = document.createElement('div');
    root.className = 'tl' + (textMode ? ' tl-text' : '');
    root.innerHTML = (textMode ? '' : `
      <div class="tl-transport">
        <button type="button" class="cq-play tl-play" aria-label="${esc(t('tl.play'))}" data-i18n-aria="tl.play">▶</button>
        <span class="tl-time"><b class="tl-cur">0:00</b> / <span class="tl-dur">${fmt(D())}</span></span>
        <div class="tl-speed" role="group" aria-label="${esc(t('tl.speed'))}" data-i18n-aria="tl.speed">
          <button type="button" class="on" data-rate="1">1×</button>
          <button type="button" data-rate="1.5">1.5×</button>
          <button type="button" data-rate="2">2×</button>
        </div>
        <a class="tl-dl cq-dl icon-btn" href="#" aria-disabled="true" download title="${esc(t('tl.download'))}"
           data-i18n-title="tl.download" aria-label="${esc(t('tl.download'))}" data-i18n-aria="tl.download">${(typeof CQ !== 'undefined' && CQ.ICON_DL) || ''}</a>
      </div>
      <div class="tl-wave tl-busy" role="slider" tabindex="0" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"
           aria-label="${esc(t('tl.position'))}" data-i18n-aria="tl.position"
           title="${esc(t('tl.keyhint'))}" data-i18n-title="tl.keyhint" data-busy="${esc(t('tl.loading'))}">
        <canvas class="tl-canvas" aria-hidden="true"></canvas>
        <div class="tl-played"></div><div class="tl-playhead"></div>
      </div>
      <div class="tl-note" hidden></div>
      <div class="tl-rows">
        <div class="tl-row tl-speakers"><div class="tl-rowname"><span data-i18n="tl.speakers">${esc(t('tl.speakers'))}</span></div><div class="tl-track"></div></div>
      </div>`) + `
      <div class="tl-legend" hidden></div>
      <div class="tl-h" data-i18n="res.transcript">${esc(t('res.transcript'))}</div>
      <div class="tl-transcript"></div>`;
    container.appendChild(root);

    const q = (sel) => root.querySelector(sel);
    const playBtn = q('.tl-play'), curEl = q('.tl-cur'), durEl = q('.tl-dur'), speedEl = q('.tl-speed'),
      dlEl = q('.tl-dl'), wave = q('.tl-wave'), canvas = q('.tl-canvas'), noteEl = q('.tl-note'),
      rowsEl = q('.tl-rows'), spTrack = q('.tl-speakers .tl-track'), legendEl = q('.tl-legend'),
      txEl = q('.tl-transcript');
    const audio = textMode ? null : new Audio();
    if (audio) { audio.preload = 'metadata'; }

    /* ---- speaker strip ---- */
    function renderSpeakers() {
      if (!spTrack) return;
      spTrack.innerHTML = state.segments.filter((s) => s.start !== null && s.end !== null).map((s) =>
        `<div class="tl-seg ${spClass(s.speaker)}" data-i="${s.i}" title="${esc(spLabel(s.speaker))} · ${fmt(s.start)}–${fmt(s.end)}"></div>`).join('');
      layoutSpeakers();
    }
    function layoutSpeakers() {
      if (!spTrack) return;
      const d = D();
      spTrack.querySelectorAll('.tl-seg').forEach((el) => {
        const s = segById(+el.dataset.i);
        if (!s || !d) { el.style.display = 'none'; return; }
        el.style.display = '';
        el.style.left = pct(s.start);
        el.style.width = Math.max(0, (s.end - s.start) / d * 100).toFixed(3) + '%';
      });
    }
    const segById = (i) => state.segments.find((s) => s.i === i) || null;

    /* ---- lanes ---- */
    function laneRow(lane) {
      const row = document.createElement('div');
      row.className = 'tl-row tl-lane';
      row.dataset.lane = lane.id;
      row.hidden = state.hidden.has(lane.id);
      const sw = lane.color ? ` style="--tl-sw:${esc(lane.color)}"` : '';
      row.innerHTML = `<div class="tl-rowname" title="${esc(lane.name)}"><i class="tl-sw"${sw}></i><span>${esc(lane.name)}</span></div><div class="tl-track"></div>`;
      const track = row.querySelector('.tl-track');
      (lane.spans || []).forEach((sp, k) => {
        if (num(sp.start) === null || num(sp.end) === null) return;   // text-only span: transcript marks only
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'tl-span ' + levelClass(sp.level);
        b.dataset.k = k;
        const label = sp.label || lane.name || '';
        b.setAttribute('aria-label', (label ? label + ' · ' : '') + fmt(sp.start));
        if (num(sp.score) !== null) b.style.background = levelColor(sp.level, sp.score);
        track.appendChild(b);
      });
      return row;
    }
    /* A span the recording cannot contain — reversed (end < start), starting at or after the
       end, or ending before it begins — would render as a 4px sliver pinned to an edge: a
       click target that means nothing. Drop it from the axis; its transcript marks survive,
       and the test is re-run on every layout so a span becomes visible if D() grows. */
    const drawable = (sp, d) => !!sp && d > 0 && sp.start !== null && sp.end !== null
      && sp.end >= sp.start && sp.start < d && sp.end >= 0;
    function layoutLane(row, lane) {
      const d = D();
      row.querySelectorAll('.tl-span').forEach((el) => {
        const sp = lane.spans[+el.dataset.k];
        if (!drawable(sp, d)) { el.style.display = 'none'; return; }
        el.style.display = '';
        el.style.left = pct(sp.start);
        el.style.width = Math.max(0, (Math.min(d, sp.end) - Math.max(0, sp.start)) / d * 100).toFixed(3) + '%';
      });
    }
    function layoutAll() {
      layoutSpeakers();
      if (rowsEl) state.lanes.forEach((lane) => { const row = rowsEl.querySelector(`.tl-lane[data-lane="${cssEsc(lane.id)}"]`); if (row) layoutLane(row, lane); });
    }
    function normLane(lane) {
      const spans = (Array.isArray(lane.spans) ? lane.spans : []).map((sp) => Object.assign({}, sp, {
        start: num(sp.start), end: num(sp.end), score: num(sp.score),
        segments: Array.isArray(sp.segments) ? sp.segments.map(int).filter((i) => i !== null) : [],
      }));
      return { id: String(lane.id), name: lane.name != null ? String(lane.name) : String(lane.id), color: lane.color || null, spans };
    }
    /* Mirror each span's `segments` onto the transcript so a finding is visible while reading
       even before the caller asks for marks; markSegments() replaces these for its lane. */
    function autoMarks(lane) {
      const m = new Map();
      lane.spans.forEach((sp) => sp.segments.forEach((i) => m.set(i, { i, level: sp.level, score: sp.score, title: sp.label || lane.name })));
      state.auto.set(lane.id, m);
    }
    function mountLane(lane) {
      const idx = state.lanes.findIndex((l) => l.id === lane.id);
      if (idx >= 0) {                                  // same id → replace in place
        state.lanes[idx] = lane;
        const old = rowsEl && rowsEl.querySelector(`.tl-lane[data-lane="${cssEsc(lane.id)}"]`);
        if (old) { const row = laneRow(lane); old.replaceWith(row); layoutLane(row, lane); }
      } else {
        state.lanes.push(lane);
        if (rowsEl) { const row = laneRow(lane); rowsEl.appendChild(row); layoutLane(row, lane); }
      }
      autoMarks(lane);
    }
    function renderLegend() {
      legendEl.hidden = !state.lanes.length;
      legendEl.innerHTML = state.lanes.map((lane) => {
        const on = !state.hidden.has(lane.id);
        const sw = lane.color ? ` style="--tl-sw:${esc(lane.color)}"` : '';
        return `<label class="${on ? '' : 'off'}"><input type="checkbox" data-lane="${esc(lane.id)}"${on ? ' checked' : ''}><i class="tl-sw"${sw}></i><span>${esc(lane.name)}</span></label>`;
      }).join('');
    }
    legendEl.addEventListener('change', (e) => {
      const cb = e.target.closest('input[data-lane]');
      if (cb) api.toggleLane(cb.dataset.lane, cb.checked);
    });

    /* ---- transcript ---- */
    function renderTranscript() {
      if (!state.segments.length) { txEl.innerHTML = `<div class="tl-empty" data-i18n="tl.nosegments">${esc(t('tl.nosegments'))}</div>`; return; }
      txEl.innerHTML = state.segments.map((s) => {
        const time = s.start !== null ? `<span class="tl-ts">${fmt(s.start)}</span>` : '';
        return `<p class="tl-p" data-i="${s.i}" tabindex="0"><span class="tl-chip ${spClass(s.speaker)}">${esc(spLabel(s.speaker))}</span>${time}<span class="tl-txt">${esc(s.text)}</span></p>`;
      }).join('');
      applyMarks();
      markNow();
    }
    function relabel() {
      txEl.querySelectorAll('.tl-p').forEach((p) => { const s = segById(+p.dataset.i); const c = p.querySelector('.tl-chip'); if (s && c) c.textContent = spLabel(s.speaker); });
      txEl.querySelectorAll('.tl-p').forEach(pTitle);
      if (spTrack) spTrack.querySelectorAll('.tl-seg').forEach((el) => { const s = segById(+el.dataset.i); if (s) el.title = `${spLabel(s.speaker)} · ${fmt(s.start)}–${fmt(s.end)}`; });
    }
    function applyMarks() {
      const ids = [];
      state.lanes.forEach((l) => ids.push(l.id));
      state.marks.forEach((_, id) => { if (ids.indexOf(id) < 0) ids.push(id); });
      txEl.querySelectorAll('.tl-p').forEach((p) => {
        const i = +p.dataset.i;
        const colors = []; const titles = [];
        ids.forEach((id) => {
          if (state.hidden.has(id)) return;
          const m = state.marks.has(id) ? state.marks.get(id) : state.auto.get(id);
          const mk = m && m.get(i);
          if (!mk) return;
          const c = levelColor(mk.level, mk.score);
          if (colors.indexOf(c) < 0) colors.push(c);          // two lanes agreeing = one band
          if (mk.title) titles.push(mk.title);
        });
        /* One colour → the plain 3px border. Two or more → the strip is divided between them
           top to bottom, in lane order, so a score is never silently replaced by an unrelated
           lane's verdict. The titles list every finding either way. */
        if (colors.length === 1) p.style.setProperty('--tl-mark', colors[0]); else p.style.removeProperty('--tl-mark');
        if (colors.length > 1) {
          const n = colors.length;
          p.style.setProperty('--tl-marks', 'linear-gradient(to bottom,' + colors.map((c, k) =>
            `${c} ${(k * 100 / n).toFixed(2)}% ${((k + 1) * 100 / n).toFixed(2)}%`).join(',') + ')');
        } else p.style.removeProperty('--tl-marks');
        p.classList.toggle('tl-multi', colors.length > 1);
        if (titles.length) p.dataset.marks = titles.join(' · '); else delete p.dataset.marks;
        p.classList.toggle('marked', colors.length > 0);
        pTitle(p);
      });
    }
    /* Native tooltip on a paragraph: the findings that mark it when there are any, else the
       seek hint (audio mode only — a text-mode paragraph has nowhere to go). */
    function pTitle(p) {
      const s = segById(+p.dataset.i);
      if (p.dataset.marks) p.title = p.dataset.marks;
      else if (s && s.start !== null && !textMode) p.title = t('tl.goto').replace('{t}', fmt(s.start));
      else p.removeAttribute('title');
    }
    txEl.addEventListener('click', (e) => { const p = e.target.closest('.tl-p'); if (p) goSegment(+p.dataset.i); });
    txEl.addEventListener('keydown', (e) => {
      const p = e.target.closest('.tl-p');
      if (p && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); goSegment(+p.dataset.i); }
    });
    if (spTrack) spTrack.addEventListener('click', (e) => { const el = e.target.closest('.tl-seg'); if (el) goSegment(+el.dataset.i); });
    function goSegment(i) {
      const s = segById(i);
      if (!s) return;
      if (s.start !== null && !textMode) api.seek(s.start);
      else setCurrent(i);
    }

    /* ---- current segment / playhead ---- */
    function segAt(v) {
      for (let k = 0; k < state.segments.length; k++) {
        const s = state.segments[k];
        if (s.start !== null && s.end !== null && v >= s.start - 0.05 && v < s.end + 0.25) return s.i;
      }
      return -1;
    }
    function setCurrent(i) {
      if (i === state.cur) return;
      state.cur = i;
      markNow();
      if (typeof opts.onSegment === 'function') opts.onSegment(i);
    }
    function markNow() {
      root.querySelectorAll('.now').forEach((el) => el.classList.remove('now'));
      if (state.cur < 0) return;
      const p = txEl.querySelector(`.tl-p[data-i="${state.cur}"]`);
      if (p) { p.classList.add('now'); scrollWithin(txEl, p); }
      const b = spTrack && spTrack.querySelector(`.tl-seg[data-i="${state.cur}"]`);
      if (b) b.classList.add('now');
    }
    function setPos(v) {
      state.t = v;
      root.style.setProperty('--tl-pos', pct(v));
      if (curEl) curEl.textContent = fmt(v);
      const sec = Math.round(v);
      if (wave && sec !== state.ariaSec) {
        state.ariaSec = sec;
        wave.setAttribute('aria-valuenow', String(sec));
        wave.setAttribute('aria-valuetext', `${fmt(v)} / ${fmt(D())}`);
      }
      setCurrent(segAt(v));
    }
    function onDurationChange() {
      if (durEl) durEl.textContent = fmt(D());
      if (wave) wave.setAttribute('aria-valuemax', String(Math.round(D())));
      layoutAll();
      setPos(state.t);
    }

    /* ---- audio wiring ---- */
    const audioReady = () => !!audio && audio.readyState >= 1;
    function setPlayBtn() {
      if (!playBtn) return;
      const paused = !audio || audio.paused;
      playBtn.textContent = paused ? '▶' : '❚❚';
      playBtn.setAttribute('aria-label', t(paused ? 'tl.play' : 'tl.pause'));
      playBtn.setAttribute('data-i18n-aria', paused ? 'tl.play' : 'tl.pause');
    }
    function tick() {
      if (state.destroyed || !audio || audio.paused) { state.raf = 0; return; }
      setPos(audio.currentTime);
      state.raf = requestAnimationFrame(tick);
    }
    if (audio) {
      audio.addEventListener('loadedmetadata', () => {
        if (isFinite(audio.duration) && audio.duration > 0) state.audioDur = audio.duration;
        onDurationChange();
        if (state.pendingSeek !== null) { const v = state.pendingSeek; state.pendingSeek = null; try { audio.currentTime = v; } catch (_) { /* not seekable yet */ } }
      });
      audio.addEventListener('durationchange', () => {
        if (isFinite(audio.duration) && audio.duration > 0 && audio.duration !== state.audioDur) { state.audioDur = audio.duration; onDurationChange(); }
      });
      audio.addEventListener('timeupdate', () => { if (!state.dragging) setPos(audio.currentTime); });
      audio.addEventListener('seeked', () => setPos(audio.currentTime));
      audio.addEventListener('play', () => { setPlayBtn(); if (!state.raf) state.raf = requestAnimationFrame(tick); });
      audio.addEventListener('pause', setPlayBtn);
      audio.addEventListener('ended', () => { setPlayBtn(); setPos(audio.duration || state.t); });
      /* The blob arrived but the media element cannot demux it: the waveform note alone left a
         live-looking play button that did nothing. Same treatment as a failed fetch — play off,
         a note saying so — except the file is here, so the download stays. Seeking still works
         (§13.1): the playhead, the segments and the transcript are driven by state, not audio. */
      audio.addEventListener('error', () => {
        if (state.destroyed || !audio.src) return;
        if (playBtn) playBtn.disabled = true;
        if (speedEl) speedEl.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        flatBar('tl.playfail');
      });
      audio.addEventListener('ratechange', () => {
        speedEl.querySelectorAll('button').forEach((b) => b.classList.toggle('on', +b.dataset.rate === audio.playbackRate));
      });
      playBtn.addEventListener('click', () => { if (audio.paused) api.play(); else api.pause(); });
      speedEl.addEventListener('click', (e) => {
        const b = e.target.closest('button[data-rate]');
        if (!b) return;
        audio.playbackRate = audio.defaultPlaybackRate = +b.dataset.rate;
        speedEl.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      });

      /* click + drag to seek */
      const seekAt = (x) => { const r = wave.getBoundingClientRect(); if (!r.width || !D()) return; api.seek((x - r.left) / r.width * D()); };
      wave.addEventListener('pointerdown', (e) => {
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        state.dragging = true;
        try { wave.setPointerCapture(e.pointerId); } catch (_) { /* older engines */ }
        seekAt(e.clientX);
      });
      wave.addEventListener('pointermove', (e) => { if (state.dragging) seekAt(e.clientX); });
      const up = (e) => { if (!state.dragging) return; state.dragging = false; try { wave.releasePointerCapture(e.pointerId); } catch (_) { /* ignore */ } };
      wave.addEventListener('pointerup', up);
      wave.addEventListener('pointercancel', up);

      /* keyboard: on the whole component; Space only where it is not already a control's own key */
      root.addEventListener('keydown', (e) => {
        const tag = e.target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.altKey || e.ctrlKey || e.metaKey) return;
        // Space is handled document-wide (see docSpace); leave it alone here so the two do
        // not both fire and cancel each other out.
        if (e.key === ' ' || e.key === 'Spacebar') return;
        else if (e.key === 'ArrowLeft') { e.preventDefault(); api.seek(state.t - 5); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); api.seek(state.t + 5); }
        else if (e.key === 'Home') { e.preventDefault(); api.seek(0); }
        else if (e.key === 'End') { e.preventDefault(); api.seek(D()); }
      });
    }

    /* ---- span interaction (delegated: hover/focus → tooltip, click → seek + callback) ---- */
    function spanOf(el) {
      const b = el && el.closest ? el.closest('.tl-span') : null;
      if (!b) return null;
      const row = b.closest('.tl-lane');
      const lane = row && state.lanes.find((l) => l.id === row.dataset.lane);
      const span = lane && lane.spans[+b.dataset.k];
      return lane && span ? { el: b, lane, span } : null;
    }
    if (rowsEl) {
      rowsEl.addEventListener('click', (e) => {
        const hit = spanOf(e.target);
        if (!hit) return;
        api.seek(hit.span.start);
        if (typeof opts.onSpanClick === 'function') opts.onSpanClick(hit.lane, hit.span);
      });
      rowsEl.addEventListener('mouseover', (e) => {
        const hit = spanOf(e.target);
        if (hit && tipOwner !== hit.el) tipShow(hit.el, hit.span.label || hit.lane.name, hit.span.detail);
      });
      rowsEl.addEventListener('mouseout', (e) => {
        const hit = spanOf(e.target);
        if (hit && !(e.relatedTarget && hit.el.contains(e.relatedTarget))) tipHide(hit.el);
      });
      rowsEl.addEventListener('focusin', (e) => { const hit = spanOf(e.target); if (hit) tipShow(hit.el, hit.span.label || hit.lane.name, hit.span.detail); });
      rowsEl.addEventListener('focusout', (e) => { const hit = spanOf(e.target); if (hit) tipHide(hit.el); });
    }

    /* ---- waveform ---- */
    let drawRaf = 0;
    function draw() {
      if (!canvas || state.destroyed) return;
      const w = wave.clientWidth, h = wave.clientHeight;
      if (!w || !h) return;
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const cs = getComputedStyle(root);
      const ink = (cs.getPropertyValue('--mist') || '#b8c6d0').trim();
      const mid = h / 2;
      ctx.fillStyle = ink;
      if (!state.peaks) {                          // flat bar: nothing decoded (yet, or ever)
        ctx.globalAlpha = state.flat ? .55 : .3;
        ctx.fillRect(0, mid - 1, w, 2);
        ctx.globalAlpha = 1;
        return;
      }
      const { mins, maxs, gain } = state.peaks, n = mins.length, amp = mid - 3;
      ctx.globalAlpha = .85;
      const step = 3, bw = 2;                      // 2px bars with a 1px gap
      for (let x = 0; x < w; x += step) {
        const b0 = Math.floor(x / w * n), b1 = Math.max(b0 + 1, Math.floor((x + step) / w * n));
        let lo = 1, hi = -1;
        for (let b = b0; b < b1 && b < n; b++) { if (mins[b] < lo) lo = mins[b]; if (maxs[b] > hi) hi = maxs[b]; }
        if (hi < lo) { lo = 0; hi = 0; }
        const top = mid - Math.max(0, hi) * gain * amp, bot = mid - Math.min(0, lo) * gain * amp;
        ctx.fillRect(x, Math.min(top, mid - .5), bw, Math.max(1, bot - top));
      }
      ctx.globalAlpha = 1;
    }
    const scheduleDraw = () => { if (drawRaf) return; drawRaf = requestAnimationFrame(() => { drawRaf = 0; draw(); }); };
    function flatBar(msgKey) {
      state.flat = true;
      if (wave) { wave.classList.remove('tl-busy'); }
      // "cannot be played" outranks "no waveform": both fire for a file nothing can read.
      if (msgKey === 'tl.playfail') state.mediaFail = true;
      else if (state.mediaFail) msgKey = null;
      if (noteEl && msgKey) { noteEl.textContent = t(msgKey); noteEl.setAttribute('data-i18n', msgKey); noteEl.hidden = false; }
      draw();
    }
    async function computePeaks(buffer, n) {
      const chans = [];
      for (let c = 0; c < buffer.numberOfChannels; c++) chans.push(buffer.getChannelData(c));
      const len = buffer.length, per = len / n;
      const mins = new Float32Array(n), maxs = new Float32Array(n);
      let peak = 0, last = (window.performance && performance.now) ? performance.now() : 0;
      for (let b = 0; b < n; b++) {
        /* Yield only when this run has actually held the main thread for a frame — a short
           recording finishes without yielding at all. Fixed yields every 64 buckets cost
           minutes in a background tab and buy nothing here. */
        if ((b & 63) === 63 && last && performance.now() - last > 8) {
          await yieldNow();
          if (state.destroyed) return null;
          last = performance.now();
        }
        const s0 = Math.floor(b * per), s1 = Math.min(len, Math.max(s0 + 1, Math.floor((b + 1) * per)));
        let lo = 1, hi = -1;
        for (let c = 0; c < chans.length; c++) {
          const d = chans[c];
          for (let s = s0; s < s1; s++) { const v = d[s]; if (v < lo) lo = v; if (v > hi) hi = v; }
        }
        if (hi < lo) { lo = 0; hi = 0; }
        mins[b] = lo; maxs[b] = hi;
        peak = Math.max(peak, -lo, hi);
      }
      return { mins, maxs, gain: peak > 0 ? Math.min(1 / peak, 6) : 1 };
    }
    async function decode(blob) {
      try {
        const ab = await blob.arrayBuffer();
        if (state.destroyed) return;
        const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
        const AC = window.AudioContext || window.webkitAudioContext;
        // An OfflineAudioContext decodes without the autoplay-policy warning a live one logs.
        const ctx = OAC ? new OAC(1, 1, 44100) : (AC ? new AC() : null);
        if (!ctx) throw new Error('Web Audio unavailable');
        let buffer;
        try {
          buffer = await new Promise((res, rej) => { const p = ctx.decodeAudioData(ab, res, rej); if (p && p.then) p.then(res, rej); });
        } finally { if (ctx.close) ctx.close().catch(() => {}); }
        if (state.destroyed) return;
        const peaks = await computePeaks(buffer, 800);
        if (!peaks || state.destroyed) return;
        state.peaks = peaks;
        if (isFinite(buffer.duration) && buffer.duration > 0) { state.decodedDur = buffer.duration; onDurationChange(); }
        wave.classList.remove('tl-busy');
        draw();   // synchronous on purpose: rAF is throttled in a background tab and the peaks would wait for a foreground frame
      } catch (_) {
        flatBar('tl.decodefail');
      }
    }
    async function loadSource() {
      try {
        let blob;
        if (typeof opts.src === 'string') {
          const r = await fetch(opts.src, opts.fetchInit || undefined);
          if (!r.ok) throw new Error('HTTP ' + r.status);
          blob = await r.blob();
        } else blob = opts.src;
        if (state.destroyed) return;
        if (!(blob instanceof Blob)) throw new Error('unsupported src');
        // <audio> trusts the blob's MIME: an endpoint that says application/octet-stream would be
        // refused by some engines, so leave the type blank there and let the demuxer sniff.
        if (!/^(audio|video)\//i.test(blob.type || '')) blob = new Blob([blob], { type: '' });
        state.blob = blob;
        state.objUrl = URL.createObjectURL(blob);
        audio.src = state.objUrl;
        dlEl.href = state.objUrl;
        dlEl.setAttribute('download', opts.filename || (opts.src && opts.src.name) || 'recording');
        dlEl.removeAttribute('aria-disabled');
        decode(blob);
      } catch (_) {
        CQ.toast(t('tl.loadfail'), 'err');
        if (playBtn) playBtn.disabled = true;
        flatBar(null);
      }
    }

    /* ---- observers ---- */
    let ro = null, mo = null;
    if (wave && window.ResizeObserver) { ro = new ResizeObserver(scheduleDraw); ro.observe(wave); }
    if (wave && window.MutationObserver) {       // theme switch → repaint with the new --mist
      mo = new MutationObserver(draw);
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    }
    const onLang = () => { if (state.destroyed) return; CQ.applyI18n(root); relabel(); setPlayBtn(); if (wave) { wave.dataset.busy = t('tl.loading'); wave.setAttribute('aria-valuetext', `${fmt(state.t)} / ${fmt(D())}`); } };
    document.addEventListener('cq:lang', onLang);

    /* ---- public API ---- */
    const api = {
      el: root,
      audio,
      get duration() { return D(); },
      get currentTime() { return state.t; },
      setLanes(lanes) {
        state.lanes = []; state.auto.clear(); state.marks.clear();
        if (rowsEl) rowsEl.querySelectorAll('.tl-lane').forEach((r) => r.remove());
        (Array.isArray(lanes) ? lanes : []).forEach((l) => mountLane(normLane(l)));
        renderLegend(); applyMarks();
        return api;
      },
      addLane(lane) {
        if (!lane || lane.id == null) return api;
        mountLane(normLane(lane));
        renderLegend(); applyMarks();
        return api;
      },
      removeLane(id) {
        id = String(id);
        state.lanes = state.lanes.filter((l) => l.id !== id);
        state.auto.delete(id); state.marks.delete(id); state.hidden.delete(id);
        if (rowsEl) { const row = rowsEl.querySelector(`.tl-lane[data-lane="${cssEsc(id)}"]`); if (row) row.remove(); }
        renderLegend(); applyMarks();
        return api;
      },
      toggleLane(id, on) {
        id = String(id);
        if (on === undefined) on = state.hidden.has(id);
        if (on) state.hidden.delete(id); else state.hidden.add(id);
        if (rowsEl) { const row = rowsEl.querySelector(`.tl-lane[data-lane="${cssEsc(id)}"]`); if (row) row.hidden = !on; }
        const cb = legendEl.querySelector(`input[data-lane="${cssEsc(id)}"]`);
        if (cb) { cb.checked = !!on; cb.closest('label').classList.toggle('off', !on); }
        applyMarks();
        return api;
      },
      seek(v) {
        if (textMode) return api;
        const d = D();
        v = Math.max(0, +v || 0);
        if (d > 0) v = Math.min(d, v);
        if (audioReady()) { try { audio.currentTime = v; } catch (_) { state.pendingSeek = v; } }
        else state.pendingSeek = v;
        setPos(v);
        if (typeof opts.onSeek === 'function') opts.onSeek(v);
        return api;
      },
      play() { if (audio && audio.src) audio.play().catch(() => {}); return api; },
      pause() { if (audio) audio.pause(); return api; },
      highlightSegment(i) {
        root.querySelectorAll('.hl').forEach((el) => el.classList.remove('hl'));
        state.hl = Number.isInteger(i) ? i : -1;
        if (state.hl < 0) return api;
        const p = txEl.querySelector(`.tl-p[data-i="${state.hl}"]`);
        if (p) { p.classList.add('hl'); scrollWithin(txEl, p); }
        const b = spTrack && spTrack.querySelector(`.tl-seg[data-i="${state.hl}"]`);
        if (b) b.classList.add('hl');
        return api;
      },
      setSpeakerLabels(map) { state.labels = Object.assign({}, map || {}); relabel(); return api; },
      markSegments(laneId, marks) {
        const id = String(laneId);
        const m = new Map();
        (Array.isArray(marks) ? marks : []).forEach((mk) => {
          const i = mk ? int(mk.i) : null;
          if (i !== null) m.set(i, { i, level: mk.level, score: num(mk.score), title: mk.title || '' });
        });
        state.marks.set(id, m);
        applyMarks();
        return api;
      },
      destroy() {
        if (state.destroyed) return;
        state.destroyed = true;
        unregister(handle);
        document.removeEventListener('cq:lang', onLang);
        if (ro) ro.disconnect();
        if (mo) mo.disconnect();
        if (state.raf) cancelAnimationFrame(state.raf);
        if (drawRaf) cancelAnimationFrame(drawRaf);
        if (tipOwner && root.contains(tipOwner)) tipHide();
        if (audio) { try { audio.pause(); audio.removeAttribute('src'); audio.load(); } catch (_) { /* ignore */ } }
        if (state.objUrl) { URL.revokeObjectURL(state.objUrl); state.objUrl = null; }
        state.blob = null; state.peaks = null;
        root.remove();
      },
    };

    /* ---- first paint: everything synchronous, then the heavy work ---- */
    renderSpeakers();
    api.setLanes(opts.lanes || []);
    renderTranscript();
    if (wave) { wave.setAttribute('aria-valuemax', String(Math.round(D()))); wave.setAttribute('aria-valuetext', `0:00 / ${fmt(D())}`); }
    if (!textMode) { setPlayBtn(); scheduleDraw(); loadSource(); }

    /* Page-wide Space (see docSpace at the top of this module). `playable` is checked at press
       time, not at mount: a timeline in the summarise tab whose audio has not loaded yet, or a
       text-mode one with no player at all, simply is not a candidate. */
    const handle = {
      root,
      playable: () => !state.destroyed && !!(audio && audio.src),
      toggle: () => { if (audio.paused) api.play(); else api.pause(); },
    };
    register(handle);
    // Touching a player makes it the one Space drives, which is what someone comparing two
    // calls side by side expects.
    root.addEventListener('pointerdown', () => { lastActive = handle; }, true);

    return api;
  }

  CQ.Timeline = Timeline;
})();
