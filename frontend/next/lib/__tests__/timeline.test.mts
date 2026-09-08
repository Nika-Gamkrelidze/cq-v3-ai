import test from 'node:test';
import assert from 'node:assert/strict';
import {
  autoMarks, computePeaks, drawable, formatClock, int, levelClass, levelColor, markGradient,
  markOrder, normLanes, normMarks, normSegments, num, percentOf, segmentAt, segmentsDuration,
  spanWidth, stripMarks, yieldNow, type Mark, type NormLane,
} from '../../components/timeline/util.ts';

/* The timeline's arithmetic, without a browser.
   ============================================
   Three of these guard decisions that a reader of the code would plausibly "simplify":
   the score/level precedence in `levelColor` (which encodes whose thresholds win), the
   `drawable` test (which decides whether a nonsense span becomes a click target that lies),
   and the yield gate in `computePeaks` (which is the difference between a waveform appearing
   and a background tab stalling for minutes). */

/* ---------------- coercion ---------------- */

test('num: a numeric string is a number, everything else is null', () => {
  // A backend that serialises times as JSON strings must not silently lose them.
  assert.equal(num('12.5'), 12.5);
  assert.equal(num(0), 0);
  assert.equal(num(null), null);
  assert.equal(num(''), null);
  assert.equal(num('  '), null);
  assert.equal(num(true), null);
  assert.equal(num('x'), null);
  assert.equal(num(NaN), null);
  assert.equal(num(Infinity), null);
});

test('int: only whole numbers survive, because these are array keys', () => {
  assert.equal(int('3'), 3);
  assert.equal(int(3.5), null);
  assert.equal(int('3.5'), null);
  assert.equal(int(0), 0);
});

/* ---------------- the clock ---------------- */

test('formatClock: m:ss, and h:mm:ss once there is an hour', () => {
  assert.equal(formatClock(0), '0:00');
  assert.equal(formatClock(9.9), '0:09');          // floored, never rounded up past the frame
  assert.equal(formatClock(61), '1:01');
  assert.equal(formatClock(599), '9:59');
  assert.equal(formatClock(3600), '1:00:00');
  assert.equal(formatClock(3661), '1:01:01');
  assert.equal(formatClock(7325), '2:02:05');
});

test('formatClock: a missing or negative time is 0:00, not "-1:-1"', () => {
  // audio.duration is NaN until metadata loads and currentTime can go slightly negative on a
  // scrub to the very start; both used to render as nonsense.
  assert.equal(formatClock(NaN), '0:00');
  assert.equal(formatClock(null), '0:00');
  assert.equal(formatClock(undefined), '0:00');
  assert.equal(formatClock(-5), '0:00');
});

/* ---------------- §3 colour ---------------- */

test('levelColor: a numeric score takes the hue ramp, 0 red … 100 green', () => {
  assert.equal(levelColor(undefined, 0), 'hsl(0 var(--tl-sat, 65%) var(--tl-lig, 45%))');
  assert.equal(levelColor(undefined, 50), 'hsl(60 var(--tl-sat, 65%) var(--tl-lig, 45%))');
  assert.equal(levelColor(undefined, 100), 'hsl(120 var(--tl-sat, 65%) var(--tl-lig, 45%))');
});

test('levelColor: S and L stay as custom properties so a theme switch re-resolves them', () => {
  // Baking 65%/45% in would leave a score span at 1.5:1 on the light track — the one place
  // where the colour IS the information.
  assert.match(levelColor(undefined, 42), /var\(--tl-sat, 65%\) var\(--tl-lig, 45%\)/);
});

test('levelColor: the score is clamped, not wrapped', () => {
  assert.equal(levelColor(undefined, -20), levelColor(undefined, 0));
  assert.equal(levelColor(undefined, 250), levelColor(undefined, 100));
});

test('levelColor: a score of null falls back to the LEVEL, which is how a rubric keeps its own bands', () => {
  // The workbench passes score:null on a scored lane on purpose, having already resolved the
  // workspace's configured thresholds into a level. Reading the score whenever it is present
  // would overrule the tenant's own bands with a continuous ramp.
  assert.equal(levelColor('good', null), 'var(--tl-good)');
  assert.equal(levelColor('mid', null), 'var(--tl-mid)');
  assert.equal(levelColor('bad', null), 'var(--tl-bad)');
  assert.equal(levelColor('none', null), 'var(--tl-none)');
  assert.equal(levelColor(undefined, null), 'var(--tl-none)');
});

test('levelColor: score 0 is a score, not a missing one', () => {
  assert.match(levelColor('good', 0), /^hsl\(0 /);
});

test('levelClass: an unknown level is none, never a class that has no rule', () => {
  assert.equal(levelClass('good'), 'lv-good');
  assert.equal(levelClass(undefined), 'lv-none');
});

test('markGradient: the strip is divided evenly, in lane order', () => {
  assert.equal(
    markGradient(['red', 'blue']),
    'linear-gradient(to bottom,red 0.00% 50.00%,blue 50.00% 100.00%)',
  );
});

/* ---------------- geometry ---------------- */

test('percentOf: clamped into the recording, and 0% before anything has a length', () => {
  assert.equal(percentOf(30, 120), '25.000%');
  assert.equal(percentOf(-5, 120), '0.000%');
  assert.equal(percentOf(500, 120), '100.000%');
  assert.equal(percentOf(30, 0), '0%');
});

const span = (start: number | null, end: number | null) =>
  ({ segments: [], start, end, score: null });

test('drawable: a span the recording cannot contain is dropped from the axis', () => {
  assert.equal(drawable(span(10, 20), 60), true);
  assert.equal(drawable(span(20, 10), 60), false);   // reversed
  assert.equal(drawable(span(60, 70), 60), false);   // starts at or after the end
  assert.equal(drawable(span(-9, -1), 60), false);   // ends before it begins
  assert.equal(drawable(span(null, 20), 60), false);
  assert.equal(drawable(span(10, 20), 0), false);    // nothing has a length yet
  assert.equal(drawable(undefined, 60), false);
});

test('drawable: a zero-length span at a valid time still draws (min-width makes it clickable)', () => {
  assert.equal(drawable(span(10, 10), 60), true);
});

test('spanWidth: clipped to the recording rather than overflowing it', () => {
  assert.equal(spanWidth(span(0, 30), 60), '50.000%');
  assert.equal(spanWidth(span(30, 120), 60), '50.000%');
  assert.equal(spanWidth(span(-30, 30), 60), '50.000%');
});

/* ---------------- segments ---------------- */

const segs = normSegments([
  { i: 0, speaker: 'speaker_0', start: 0, end: 4, text: 'hello' },
  { i: 1, speaker: 'speaker_1', start: 4.5, end: 9, text: 'hi' },
]);

test('normSegments: missing fields get the defaults the renderers assume', () => {
  const out = normSegments([{ text: 'a' }, { i: '7', speaker: 'agent', start: '1.5', end: null, text: 2 }]);
  assert.deepEqual(out[0], { i: 0, speaker: 'speaker_0', start: null, end: null, text: 'a' });
  assert.deepEqual(out[1], { i: 7, speaker: 'agent', start: 1.5, end: null, text: '2' });
  assert.deepEqual(normSegments(null), []);
});

test('segmentsDuration: the end of the last segment that has one', () => {
  assert.equal(segmentsDuration(segs), 9);
  assert.equal(segmentsDuration(normSegments([{ text: 'pasted' }])), 0);
});

test('segmentAt: the window is loose at both ends, deliberately', () => {
  assert.equal(segmentAt(segs, 0), 0);
  assert.equal(segmentAt(segs, -0.04), 0);   // a seek lands a few ms before the word
  assert.equal(segmentAt(segs, 4.1), 0);     // the gap between turns keeps the last line lit
  assert.equal(segmentAt(segs, 4.5), 1);
  assert.equal(segmentAt(segs, 30), -1);
  assert.equal(segmentAt(normSegments([{ text: 'pasted' }]), 0), -1);
});

/* ---------------- lanes and marks ---------------- */

const lane = (id: string, spans: unknown[]): NormLane => normLanes([{ id, name: id, spans } as never])[0];

test('normLanes: a lane keeps its id as a string and drops unusable segment indices', () => {
  const l = lane('fc', [{ segments: [0, '2', 1.5, null], start: '1', end: 2, score: '80' }]);
  assert.equal(l.id, 'fc');
  assert.deepEqual(l.spans[0].segments, [0, 2]);
  assert.equal(l.spans[0].start, 1);
  assert.equal(l.spans[0].score, 80);
});

test('autoMarks: every cited line is marked with its span label', () => {
  const l = lane('fc', [{ segments: [0, 1], level: 'bad', label: 'Wrong rate' }]);
  const m = autoMarks(l);
  assert.equal(m.size, 2);
  assert.equal(m.get(1)?.title, 'Wrong rate');
  assert.equal(m.get(1)?.level, 'bad');
});

test('autoMarks: an unlabelled span falls back to the lane name', () => {
  assert.equal(autoMarks(lane('fc', [{ segments: [0] }])).get(0)?.title, 'fc');
});

test('markOrder: lanes first, then a lane that only ever existed as marks', () => {
  const marks = new Map<string, unknown>([['ghost', null], ['a', null]]);
  assert.deepEqual(markOrder([lane('a', []), lane('b', [])], marks), ['a', 'b', 'ghost']);
});

const marksOf = (...pairs: [string, Mark[]][]) =>
  new Map(pairs.map(([id, list]) => [id, new Map(list.map(m => [m.i, m]))]));

test('stripMarks: two lanes agreeing paint ONE band, not two identical ones', () => {
  const auto = marksOf(
    ['a', [{ i: 0, level: 'bad', score: null, title: 'A' }]],
    ['b', [{ i: 0, level: 'bad', score: null, title: 'B' }]],
  );
  const out = stripMarks(0, ['a', 'b'], new Set(), new Map(), auto);
  assert.deepEqual(out.colors, ['var(--tl-bad)']);
  assert.deepEqual(out.titles, ['A', 'B']);
});

test('stripMarks: two lanes disagreeing divide the strip, in lane order', () => {
  const auto = marksOf(
    ['a', [{ i: 0, level: 'good', score: null, title: 'A' }]],
    ['b', [{ i: 0, level: 'bad', score: null, title: 'B' }]],
  );
  const out = stripMarks(0, ['a', 'b'], new Set(), new Map(), auto);
  assert.deepEqual(out.colors, ['var(--tl-good)', 'var(--tl-bad)']);
  // A misinformation hit must never be silently overwritten by an unrelated lane's verdict.
  assert.equal(markGradient(out.colors).startsWith('linear-gradient(to bottom,var(--tl-good) 0.00% 50.00%'), true);
});

test('stripMarks: a hidden lane contributes nothing — the legend filters the transcript too', () => {
  const auto = marksOf(['a', [{ i: 0, level: 'bad', score: null, title: 'A' }]]);
  const out = stripMarks(0, ['a'], new Set(['a']), new Map(), auto);
  assert.deepEqual(out.colors, []);
  assert.deepEqual(out.titles, []);
});

test('stripMarks: an explicit markSegments() replaces that lane\'s automatic marks', () => {
  const auto = marksOf(['a', [{ i: 0, level: 'bad', score: null, title: 'auto' }]]);
  const explicit = marksOf(['a', [{ i: 0, level: 'good', score: null, title: 'explicit' }]]);
  const out = stripMarks(0, ['a'], new Set(), explicit, auto);
  assert.deepEqual(out.colors, ['var(--tl-good)']);
  assert.deepEqual(out.titles, ['explicit']);
});

test('normMarks: a mark without a usable index is dropped, not stored under NaN', () => {
  const m = normMarks([{ i: 0, title: 'a' }, { i: 'x', title: 'b' }, null, { i: '3', score: '90' }]);
  assert.deepEqual([...m.keys()], [0, 3]);
  assert.equal(m.get(3)?.score, 90);
  assert.equal(m.get(0)?.title, 'a');
});

/* ---------------- the waveform ---------------- */

const ramp = (n: number, f: (i: number) => number) => Float32Array.from({ length: n }, (_, i) => f(i));

test('computePeaks: one bucket per slice, carrying that slice\'s min and max', async () => {
  const data = ramp(8, i => (i % 2 ? 0.5 : -0.25));
  const p = await computePeaks([data], 8, 4);
  assert.ok(p);
  assert.deepEqual([...p.mins], [-0.25, -0.25, -0.25, -0.25]);
  assert.deepEqual([...p.maxs], [0.5, 0.5, 0.5, 0.5]);
});

test('computePeaks: gain normalises to the loudest sample', async () => {
  const p = await computePeaks([ramp(4, () => 0.25)], 4, 2);
  assert.equal(p?.gain, 4);
});

test('computePeaks: gain is capped at 6, so near-silence is lifted but not to pure noise', async () => {
  const p = await computePeaks([ramp(4, () => 0.001)], 4, 2);
  assert.equal(p?.gain, 6);
});

test('computePeaks: digital silence keeps gain 1 rather than dividing by zero', async () => {
  const p = await computePeaks([ramp(4, () => 0)], 4, 2);
  assert.equal(p?.gain, 1);
  assert.deepEqual([...p!.mins], [0, 0]);
});

test('computePeaks: a bucket with no samples is flat, not the ±1 sentinels', async () => {
  // An empty decode: every bucket runs its inner loop zero times and must not keep lo=1/hi=-1,
  // which would draw a full-height bar out of nothing.
  const p = await computePeaks([new Float32Array(0)], 0, 4);
  assert.ok(p);
  assert.deepEqual([...p.mins], [0, 0, 0, 0]);
  assert.deepEqual([...p.maxs], [0, 0, 0, 0]);
  assert.equal(p.gain, 1);
});

test('computePeaks: more buckets than samples still gives every bucket a real sample', async () => {
  const p = await computePeaks([ramp(2, () => 0.5)], 2, 8);
  assert.ok(p);
  assert.equal([...p.maxs].every(v => Math.abs(v - 0.5) < 1e-6), true);
});

test('computePeaks: every channel is folded into the same bucket', async () => {
  const p = await computePeaks([ramp(4, () => 0.2), ramp(4, () => -0.9)], 4, 1);
  assert.ok(Math.abs((p?.mins[0] ?? 0) + 0.9) < 1e-6);
  assert.ok(Math.abs((p?.maxs[0] ?? 0) - 0.2) < 1e-6);
});

/* The yield gate, driven by an injected clock rather than by how fast this machine is. All
   three of these describe ONE rule — "yield when, and only when, this run has actually held
   the main thread for a frame" — and the reason it is worth three tests is that the obvious
   simplification (yield every N buckets) passes the first two and fails the third by costing
   minutes in a background tab. */

test('computePeaks: the cancel is observed only at a yield', async () => {
  let checks = 0;
  let clock = 0;
  const p = await computePeaks(
    [ramp(1024, () => 0.5)], 1024, 640,
    () => { checks++; return false; },
    () => (clock += 10),          // every reading is 10ms later: the gate is always open
  );
  assert.ok(p);
  assert.equal(checks, 10);       // 640 buckets → a check at b = 63, 127, … 639
});

test('computePeaks: under 8ms it never yields, so a cancelled short decode still completes', async () => {
  let checks = 0;
  let clock = 0;
  const p = await computePeaks(
    [ramp(1024, () => 0.5)], 1024, 640,
    () => { checks++; return true; },
    // Half a millisecond per reading, and `last` moves only when a yield actually happens —
    // so the gate measures time since the last PAUSE, not since the last check. Ten checks in,
    // this run still has not held the thread for a frame.
    () => (clock += 0.5),
  );
  assert.ok(p);                   // a run that never yields is never cancelled — it is done
  assert.equal(checks, 0);
});

test('computePeaks: a cancelled decode returns null instead of finishing into a dead component', async () => {
  let clock = 0;
  const out = await computePeaks(
    [ramp(1024, () => 0.5)], 1024, 640,
    () => true,
    () => (clock += 10),
  );
  assert.equal(out, null);
});

test('computePeaks: with no clock at all the gate stays shut rather than yielding blindly', async () => {
  // `performance.now` missing reads as 0, which is falsy — the legacy guard, kept: an
  // environment that cannot measure the pause must not pay for 800 of them.
  let checks = 0;
  const p = await computePeaks(
    [ramp(1024, () => 0.5)], 1024, 640,
    () => { checks++; return true; },
    () => 0,
  );
  assert.ok(p);
  assert.equal(checks, 0);
});

test('yieldNow: resolves, and does not keep the event loop alive', async () => {
  // MessageChannel and not setTimeout: setTimeout is clamped to 1s+ in a background tab, where
  // this loop runs 800 times. The port is closed so the channel cannot outlive the yield.
  const started = Date.now();
  await yieldNow();
  assert.ok(Date.now() - started < 1000);
});
