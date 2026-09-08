/* The call timeline's arithmetic — no DOM, no React, no dictionary.
   ================================================================
   Everything `Timeline.tsx` can compute without a browser lives here, for the same reason
   `lib/tipPlace.ts` exists: this is the half of the module that is possible to get wrong and
   the only half that can be tested without one. Ported function for function from
   `frontend/public/timeline.js`; where a rule looks arbitrary the comment says which
   behaviour it protects, because several of them were bug fixes.

   The public shapes (`Segment`, `Span`, `Lane`) are the component's props contract and are
   re-exported from `Timeline.tsx`; the `Norm*` shapes are what the component works with after
   normalisation, and every field a renderer reads is non-optional there. */

export type Level = 'good' | 'mid' | 'bad' | 'none';

/** One transcript line. `start`/`end` are null for a pasted transcript (§2, text mode). */
export interface Segment {
  i: number;
  speaker: string;
  start: number | null;
  end: number | null;
  text: string;
}

/** One analyser finding: a stretch of the recording, of transcript lines, or both. */
export interface Span {
  segments?: number[];
  start?: number | null;
  end?: number | null;
  level?: Level;
  score?: number | null;
  label?: string;
  detail?: string;
}

/** One row on the axis: an analyser, or one dimension of a rubric. */
export interface Lane {
  id: string;
  name: string;
  color?: string | null;
  spans: Span[];
}

export interface NormSpan {
  segments: number[];
  start: number | null;
  end: number | null;
  level?: Level;
  score: number | null;
  label?: string;
  detail?: string;
}

export interface NormLane {
  id: string;
  name: string;
  color: string | null;
  spans: NormSpan[];
}

/** A transcript line's coloured strip, as contributed by ONE lane. */
export interface Mark {
  i: number;
  level?: Level;
  score: number | null;
  title: string;
}

/* ---------------- coercion ---------------- */

/** §2 says floats, but a backend that serialises times as JSON strings must not silently lose
    them: a numeric string is a number here. Everything else (null, '', true, 'x') is null. */
export function num(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Same leniency for segment indices, which are array keys everywhere else in the module. */
export function int(v: unknown): number | null {
  const n = num(v);
  return n !== null && Number.isInteger(n) ? n : null;
}

/* ---------------- time ---------------- */

/** Seconds → `m:ss`, or `h:mm:ss` once there is an hour of it.

    NOT `lib/format.ts`'s `duration()`, and the difference is deliberate on both sides: a
    recordings TABLE shows `90:00` because that column is compared against other durations,
    while a PLAYER next to an hour-long call has to agree with the wall clock the reviewer is
    reading timestamps from. Same flooring and same clamp as the legacy module. */
export function formatClock(seconds: unknown): string {
  const raw = Number(seconds);
  const s = Math.max(0, Math.floor(Number.isFinite(raw) ? raw : 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const x = s % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : String(m)) + ':' + String(x).padStart(2, '0');
}

/* ---------------- colour (§3) ---------------- */

/** A numeric score → the hue gradient 0=red … 120=green; otherwise the level token.

    Saturation and lightness come from `--tl-sat`/`--tl-lig` rather than being baked in, so the
    colour re-resolves on a theme switch without recomputing anything: 65%/45% on dark, 60%/30%
    on light, where 45% is invisible against the near-white track.

    The caller decides which arm applies. The workbench passes `score: null` on a rubric lane
    ON PURPOSE, so a scored dimension is coloured by the WORKSPACE'S configured bands (resolved
    into `level` before it gets here) instead of by this continuous ramp. Reading `score`
    whenever it is present would quietly overrule the tenant's own thresholds. */
export function levelColor(level: Level | undefined, score: unknown): string {
  const v = num(score);
  if (v !== null) {
    const hue = Math.round(Math.max(0, Math.min(100, v)) * 1.2);
    return `hsl(${hue} var(--tl-sat, 65%) var(--tl-lig, 45%))`;
  }
  if (level === 'good') return 'var(--tl-good)';
  if (level === 'mid') return 'var(--tl-mid)';
  if (level === 'bad') return 'var(--tl-bad)';
  return 'var(--tl-none)';
}

export function levelClass(level: Level | undefined): string {
  return 'lv-' + (level === 'good' || level === 'mid' || level === 'bad' ? level : 'none');
}

/** Two or more lanes marking one transcript line: the 3px strip is divided between their
    colours top to bottom, in lane order, instead of the last lane silently overwriting the
    others. Returns the `--tl-marks` value; the single-colour case uses `--tl-mark` instead. */
export function markGradient(colors: string[]): string {
  const n = colors.length;
  return 'linear-gradient(to bottom,' + colors.map((c, k) =>
    `${c} ${(k * 100 / n).toFixed(2)}% ${((k + 1) * 100 / n).toFixed(2)}%`).join(',') + ')';
}

/* ---------------- geometry ---------------- */

/** A time as a percentage of the recording, clamped into it. `0%` when nothing has a length
    yet — every span then stacks at the left edge rather than dividing by zero. */
export function percentOf(v: number, d: number): string {
  if (!(d > 0)) return '0%';
  return (Math.max(0, Math.min(d, v)) / d * 100).toFixed(3) + '%';
}

/** Can this span be drawn on the axis at all?

    A span the recording cannot contain — reversed (end < start), starting at or after the end,
    or ending before it begins — would render as a 4px sliver pinned to an edge: a click target
    that means nothing. It is dropped from the axis; its transcript marks survive. The test is
    re-run on every layout, so a span becomes visible if the duration grows (the server's
    duration is a guess until `<audio>` reports its own). */
export function drawable(sp: NormSpan | undefined, d: number): sp is NormSpan {
  return !!sp && d > 0 && sp.start !== null && sp.end !== null
    && sp.end >= sp.start && sp.start < d && sp.end >= 0;
}

/** The drawn width of a span, clipped to the recording. */
export function spanWidth(sp: NormSpan, d: number): string {
  if (!(d > 0) || sp.start === null || sp.end === null) return '0%';
  return Math.max(0, (Math.min(d, sp.end) - Math.max(0, sp.start)) / d * 100).toFixed(3) + '%';
}

/** The end of the last segment that has one — the last resort for a duration. */
export function segmentsDuration(segments: readonly Segment[]): number {
  return segments.reduce((m, s) => (s.end !== null && s.end > m ? s.end : m), 0);
}

/** Which segment is playing at `v`, or -1.

    The window is deliberately loose at both ends: `start - 0.05` because a seek lands a few
    milliseconds before the word it targets, and `end + 0.25` because the gap between two
    diarised turns would otherwise flash the transcript back to "nothing playing". */
export function segmentAt(segments: readonly Segment[], v: number): number {
  for (const s of segments) {
    if (s.start !== null && s.end !== null && v >= s.start - 0.05 && v < s.end + 0.25) return s.i;
  }
  return -1;
}

/* ---------------- normalisation ---------------- */

export function normSegments(input: unknown): Segment[] {
  const arr = Array.isArray(input) ? input : [];
  return arr.map((raw, k) => {
    const s = (raw ?? {}) as Record<string, unknown>;
    const i = int(s.i);
    return {
      i: i !== null ? i : k,
      speaker: typeof s.speaker === 'string' && s.speaker ? s.speaker : 'speaker_0',
      start: num(s.start),
      end: num(s.end),
      text: s.text != null ? String(s.text) : '',
    };
  });
}

export function normLane(lane: Lane): NormLane {
  const spans = (Array.isArray(lane.spans) ? lane.spans : []).map((sp): NormSpan => ({
    ...sp,
    start: num(sp.start),
    end: num(sp.end),
    score: num(sp.score),
    segments: Array.isArray(sp.segments)
      ? sp.segments.map(int).filter((i): i is number => i !== null)
      : [],
  }));
  return {
    id: String(lane.id),
    name: lane.name != null ? String(lane.name) : String(lane.id),
    color: lane.color || null,
    spans,
  };
}

export function normLanes(input: readonly Lane[] | null | undefined): NormLane[] {
  return (Array.isArray(input) ? input : []).filter(l => l && l.id != null).map(normLane);
}

/** Mirror each span's `segments` onto the transcript, so a finding is visible while reading
    even before the caller asks for marks. `markSegments()` replaces these for its own lane.

    Two spans of one lane can cite the same line; the LAST one wins here, exactly as the legacy
    module has it. That is why the workbench merges its own marks (worst level, lowest score)
    and pushes them through `markSegments` rather than relying on this. */
export function autoMarks(lane: NormLane): Map<number, Mark> {
  const m = new Map<number, Mark>();
  for (const sp of lane.spans) {
    for (const i of sp.segments) {
      m.set(i, { i, level: sp.level, score: sp.score, title: sp.label || lane.name });
    }
  }
  return m;
}

export function normMarks(marks: unknown): Map<number, Mark> {
  const m = new Map<number, Mark>();
  for (const raw of Array.isArray(marks) ? marks : []) {
    const mk = (raw ?? {}) as Record<string, unknown>;
    const i = int(mk.i);
    if (i === null) continue;
    m.set(i, {
      i,
      level: mk.level as Level | undefined,
      score: num(mk.score),
      title: mk.title ? String(mk.title) : '',
    });
  }
  return m;
}

/** The order lanes contribute a colour band in: the lanes on the axis first, then any lane
    that only ever existed as transcript marks (a `markSegments` call for an id with no row). */
export function markOrder(lanes: readonly NormLane[], marks: ReadonlyMap<string, unknown>): string[] {
  const ids = lanes.map(l => l.id);
  for (const id of marks.keys()) if (!ids.includes(id)) ids.push(id);
  return ids;
}

export interface StripMarks {
  colors: string[];
  titles: string[];
}

/** What one transcript line's left strip shows: one colour per DISTINCT verdict, in lane
    order, and every finding's title for the tooltip.

    Two lanes agreeing produce one band rather than two identical ones, which is why the
    colours are deduplicated rather than counted. A hidden lane contributes nothing — the
    legend checkbox is the reader's filter for the transcript as much as for the axis. */
export function stripMarks(
  i: number,
  order: readonly string[],
  hidden: ReadonlySet<string>,
  marks: ReadonlyMap<string, ReadonlyMap<number, Mark>>,
  auto: ReadonlyMap<string, ReadonlyMap<number, Mark>>,
): StripMarks {
  const colors: string[] = [];
  const titles: string[] = [];
  for (const id of order) {
    if (hidden.has(id)) continue;
    const m = marks.has(id) ? marks.get(id) : auto.get(id);
    const mk = m && m.get(i);
    if (!mk) continue;
    const c = levelColor(mk.level, mk.score);
    if (!colors.includes(c)) colors.push(c);
    if (mk.title) titles.push(mk.title);
  }
  return { colors, titles };
}

/* ---------------- the waveform ---------------- */

export interface Peaks {
  mins: Float32Array;
  maxs: Float32Array;
  /** Normalising gain, capped: a near-silent recording is lifted, but not to pure noise. */
  gain: number;
}

/** A task-queue yield.

    `setTimeout` is clamped to 1s+ in a background tab (and far worse under intensive
    throttling), which used to strand the waveform for minutes on a tab the reviewer had
    switched away from; `postMessage` is not clamped. `requestAnimationFrame` is worse still —
    it does not fire in a hidden tab AT ALL, so the decode would never finish. Neither is a
    modernisation opportunity. */
export const yieldNow = (): Promise<void> => new Promise<void>(res => {
  if (typeof MessageChannel === 'function') {
    const ch = new MessageChannel();
    ch.port1.onmessage = () => { ch.port1.close(); res(); };
    ch.port2.postMessage(0);
  } else {
    setTimeout(res, 0);
  }
});

const defaultNow = (): number =>
  (typeof performance !== 'undefined' && typeof performance.now === 'function') ? performance.now() : 0;

/** Decoded samples → `n` min/max buckets, yielding to the event loop when it has held it.

    The yield is gated on having actually blocked for a frame rather than on a bucket count: a
    short recording finishes without yielding at all, and a fixed yield every 64 buckets costs
    MINUTES in a background tab (800 yields at a throttled task rate) and buys nothing. When
    there is no `performance` at all the gate is never open and the loop runs straight through,
    which is the right answer for the environments that lack it.

    `cancelled` is polled after each yield — and therefore only there — so an unmount stops the
    work instead of finishing it into a component that no longer exists; it returns null in
    that case. A recording short enough never to yield is never cancelled, and does not need to
    be: it is already done.

    `nowFn` exists so that gate can be tested at all. It is the one seam in this file: the rule
    it guards ("yield only after holding the thread for a frame") is exactly the thing a later
    edit would replace with a bucket count, and a test of it that depends on how fast the
    machine running it happens to be would be quietly deleted the first time it flaked. */
export async function computePeaks(
  channels: readonly ArrayLike<number>[],
  length: number,
  n: number,
  cancelled?: () => boolean,
  nowFn: () => number = defaultNow,
): Promise<Peaks | null> {
  const mins = new Float32Array(n);
  const maxs = new Float32Array(n);
  const per = length / n;
  let peak = 0;
  let last = nowFn();

  for (let b = 0; b < n; b++) {
    if ((b & 63) === 63 && last && nowFn() - last > 8) {
      await yieldNow();
      if (cancelled && cancelled()) return null;
      last = nowFn();
    }
    const s0 = Math.floor(b * per);
    const s1 = Math.min(length, Math.max(s0 + 1, Math.floor((b + 1) * per)));
    let lo = 1;
    let hi = -1;
    for (const d of channels) {
      for (let s = s0; s < s1; s++) {
        const v = d[s];
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    // No samples in this bucket (a recording shorter than the bucket count): flat, not ±1.
    if (hi < lo) { lo = 0; hi = 0; }
    mins[b] = lo;
    maxs[b] = hi;
    peak = Math.max(peak, -lo, hi);
  }

  return { mins, maxs, gain: peak > 0 ? Math.min(1 / peak, 6) : 1 };
}
