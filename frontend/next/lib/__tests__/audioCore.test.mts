import test from 'node:test';
import assert from 'node:assert/strict';
import {
  blit, channelGain, clock, cut, dbToGain, extractChannel, fade, gain, idx, insertSilence,
  invert, make, mixdown, muteChannel, normalize, peak, peaks, peaksBetween, PcmBuffer, reverse,
  silence, span, swapChannels, toMono, toStereo, toWavBytes, trim,
  type AudioBufferLike,
} from '../../components/audio/core.ts';

/* The audio editor's arithmetic, which is the most testable code in the migration and was
   until now the least tested: `audio-edit-core.js` needed an `OfflineAudioContext` to allocate
   anything, so it could only ever be exercised by hand in a browser, on a real recording,
   by ear.

   The port allocates a plain `PcmBuffer` instead — same Float32 samples, no audio context —
   which is what lets every operation below run under `node --test`. What is asserted here is
   the part where being wrong is inaudible until it is expensive: an off-by-one at a splice, a
   clamp that wraps instead of clipping, a mix that quietly halves a layer, and the two
   docs/MIGRATION.md decisions that a rewrite is most likely to "fix" into a regression. */

/* ------------------------------------------------------------------ helpers */

/** A buffer whose sample values are the ones you pass, per channel. */
function buf(channels: number[][], rate = 8000): PcmBuffer {
  const b = new PcmBuffer(channels.length, channels[0].length, rate);
  channels.forEach((data, c) => b.getChannelData(c).set(Float32Array.from(data)));
  return b;
}

/** A ramp, so an off-by-one at a splice shows up as a wrong VALUE rather than a wrong length.
    The step is 1/1024 rather than a decimal so every sample is exact in float32 and the
    assertions can be `equal` rather than "near enough", which is what an off-by-one hides in. */
const STEP = 1 / 1024;
function ramp(n: number, rate = 8000): PcmBuffer {
  const b = new PcmBuffer(1, n, rate);
  const d = b.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = i * STEP;
  return b;
}

const ch = (b: AudioBufferLike, c = 0): number[] => Array.from(b.getChannelData(c));

/* ------------------------------------------------------------------ PcmBuffer */

test('PcmBuffer reports duration from length and rate, and never has zero length', () => {
  const b = new PcmBuffer(2, 4000, 8000);
  assert.equal(b.numberOfChannels, 2);
  assert.equal(b.length, 4000);
  assert.equal(b.duration, 0.5);
  // The legacy allocator floored the length at 1 because `new OfflineAudioContext(ch, 0, rate)`
  // throws; an operation that lands on an empty result must still return a buffer.
  assert.equal(new PcmBuffer(1, 0, 8000).length, 1);
});

test('PcmBuffer refuses a channel it does not have, like AudioBuffer', () => {
  const b = new PcmBuffer(1, 4, 8000);
  assert.throws(() => b.getChannelData(1), RangeError);
});

/* ------------------------------------------------------------------ indexing */

test('idx clamps into the buffer instead of reading past either end', () => {
  const b = ramp(100);                      // 100 samples at 8 kHz = 12.5 ms
  assert.equal(idx(b, 0), 0);
  assert.equal(idx(b, -5), 0);              // a selection dragged before the start
  assert.equal(idx(b, 1000), 100);          // and one dragged past the end
  assert.equal(idx(b, 0.005), 40);
});

test('span orders its bounds, so a backwards drag is still a range', () => {
  const b = ramp(100);
  const forward = span(b, 0.002, 0.006);
  const backward = span(b, 0.006, 0.002);
  assert.deepEqual(forward, backward);
  assert.equal(forward.len, forward.b - forward.a);
});

test('blit copies only as many channels as both sides have', () => {
  const src = buf([[1, 2, 3, 4], [5, 6, 7, 8]]);
  const dst = make(src, 4, 1);
  blit(src, dst, 1, 2, 0);
  assert.deepEqual(ch(dst).slice(0, 2), [2, 3]);
});

/* ------------------------------------------------------------------ structural edits */

test('cut removes the selection and closes the gap', () => {
  const b = ramp(100);
  const out = cut(b, 0.00125, 0.00375);     // samples 10..30
  assert.equal(out.length, 80);
  const d = ch(out);
  assert.equal(d[9], 9 * STEP);             // last sample before the cut
  assert.equal(d[10], 30 * STEP);           // first sample after it — no duplicate, no gap
  assert.equal(d[79], 99 * STEP);
});

test('trim keeps only the selection', () => {
  const b = ramp(100);
  const out = trim(b, 0.00125, 0.00375);
  assert.equal(out.length, 20);
  assert.equal(ch(out)[0], 10 * STEP);
  assert.equal(ch(out)[19], 29 * STEP);
});

test('silence blanks the range without shifting the timeline', () => {
  const b = buf([[1, 1, 1, 1, 1, 1, 1, 1]]);
  const out = silence(b, 2 / 8000, 5 / 8000);
  assert.equal(out.length, b.length);       // redacting a card number must not move the call
  assert.deepEqual(ch(out), [1, 1, 0, 0, 0, 1, 1, 1]);
});

test('insertSilence pushes the tail later by exactly the pad', () => {
  const b = buf([[1, 2, 3, 4]]);
  const out = insertSilence(b, 2 / 8000, 2 / 8000);
  assert.equal(out.length, 6);
  assert.deepEqual(ch(out), [1, 2, 0, 0, 3, 4]);
  // Zero seconds is a no-op, and returns the SAME buffer — `setActiveBuffer` tests identity to
  // decide whether anything changed, so a defensive copy here would push an empty undo entry.
  assert.equal(insertSilence(b, 0, 0), b);
});

test('an empty range leaves the buffer alone, by identity', () => {
  const b = ramp(50);
  assert.equal(cut(b, 0.001, 0.001), b);
  assert.equal(trim(b, 0.001, 0.001), b);
  assert.equal(silence(b, 0.001, 0.001), b);
});

/* ------------------------------------------------------------------ level */

test('gain clips at full scale rather than wrapping', () => {
  // A float32 buffer will happily hold 3.0, sound fine in the tab, and then wrap into loud
  // digital noise the moment it is encoded to 16 bits. Clipping is audible and honest.
  const out = gain(buf([[0.5, -0.5, 0.9, -0.9]]), 4);
  assert.deepEqual(ch(out), [1, -1, 1, -1]);
});

test('gain of exactly 1 is a no-op, by identity', () => {
  const b = ramp(20);
  assert.equal(gain(b, 1), b);
});

test('gain with no bounds means the whole buffer', () => {
  const whole = gain(buf([[0.1, 0.2, 0.3, 0.4]]), 2);
  assert.deepEqual(ch(whole).map(v => Math.round(v * 10) / 10), [0.2, 0.4, 0.6, 0.8]);
});

test('dbToGain is the usual 20*log10 relation', () => {
  assert.equal(dbToGain(0), 1);
  assert.ok(Math.abs(dbToGain(6) - 2) < 0.01);
  assert.ok(Math.abs(dbToGain(-6) - 0.5) < 0.01);
});

test('peak is the largest magnitude across every channel of the range', () => {
  const b = buf([[0.1, 0.2, -0.7], [0.3, 0.9, 0.1]]);
  assert.ok(Math.abs(peak(b) - 0.9) < 1e-6);
  assert.ok(Math.abs(peak(b, 0, 1 / 8000) - 0.3) < 1e-6);
});

test('normalize lifts the loudest sample to the target and leaves silence alone', () => {
  const out = normalize(buf([[0.2, -0.1, 0.05]]));
  assert.ok(Math.abs(peak(out) - 0.891) < 1e-5);       // ~-1 dBFS of headroom
  // Digital zero has no peak to divide by; scaling it is still zero, and dividing by it is a
  // division by zero. The silent buffer comes back unchanged, by identity.
  const quiet = buf([[0, 0, 0]]);
  assert.equal(normalize(quiet), quiet);
});

test('fade ramps linearly from end to end of the range', () => {
  const flat = buf([[1, 1, 1, 1, 1]]);
  const inn = ch(fade(flat, 0, 5 / 8000, 'in'));
  assert.equal(inn[0], 0);
  assert.equal(inn[4], 1);
  const out = ch(fade(flat, 0, 5 / 8000, 'out'));
  assert.equal(out[0], 1);
  assert.equal(out[4], 0);
  // A single sample has no ramp to draw: (len - 1) would be a division by zero.
  const one = buf([[1]]);
  assert.equal(fade(one, 0, 1 / 8000, 'in'), one);
});

test('invert flips the sign, and twice is the identity', () => {
  const b = buf([[0.5, -0.25, 0]]);
  assert.deepEqual(ch(invert(b)), [-0.5, 0.25, -0]);
  assert.deepEqual(ch(invert(invert(b))), ch(b));
});

test('reverse turns the range around and leaves the rest in place', () => {
  const b = buf([[1, 2, 3, 4, 5, 6]]);
  assert.deepEqual(ch(reverse(b, 1 / 8000, 5 / 8000)), [1, 5, 4, 3, 2, 6]);
});

/* ------------------------------------------------------------------ channels */

test('extractChannel gives one side as its own mono buffer', () => {
  const b = buf([[1, 2], [3, 4]]);
  const right = extractChannel(b, 1);
  assert.equal(right.numberOfChannels, 1);
  assert.deepEqual(ch(right), [3, 4]);
  // Out of range clamps to the last channel rather than throwing at the caller.
  assert.deepEqual(ch(extractChannel(b, 9)), [3, 4]);
});

test('toMono averages every channel, and is a no-op on mono', () => {
  const b = buf([[1, 0], [0, 1]]);
  assert.deepEqual(ch(toMono(b)), [0.5, 0.5]);
  const mono = buf([[1, 2]]);
  assert.equal(toMono(mono), mono);
});

test('toStereo duplicates mono, and leaves anything wider alone', () => {
  const mono = buf([[1, 2]]);
  const wide = toStereo(mono);
  assert.equal(wide.numberOfChannels, 2);
  assert.deepEqual(ch(wide, 0), ch(wide, 1));
  const stereo = buf([[1], [2]]);
  assert.equal(toStereo(stereo), stereo);
});

test('swapChannels exchanges the two sides without aliasing them', () => {
  const b = buf([[1, 2], [3, 4]]);
  const out = swapChannels(b);
  assert.deepEqual(ch(out, 0), [3, 4]);
  assert.deepEqual(ch(out, 1), [1, 2]);
  assert.deepEqual(ch(b, 0), [1, 2]);        // the input is never mutated
  const mono = buf([[1, 2]]);
  assert.equal(swapChannels(mono), mono);    // nothing to swap, so nothing is copied
});

test('channelGain touches one channel only, and clamps it', () => {
  const b = buf([[0.5, 0.5], [0.5, 0.5]]);
  const out = channelGain(b, 0, 4);
  assert.deepEqual(ch(out, 0), [1, 1]);
  assert.deepEqual(ch(out, 1), [0.5, 0.5]);
  assert.deepEqual(ch(muteChannel(b, 1), 1), [0, 0]);
});

/* ------------------------------------------------------------------ WAV */

test('toWavBytes writes a RIFF header the sizes agree with', () => {
  const b = buf([[0, 0], [0, 0]], 16000);
  const bytes = toWavBytes(b);
  const view = new DataView(bytes);
  const tag = (at: number) => String.fromCharCode(
    view.getUint8(at), view.getUint8(at + 1), view.getUint8(at + 2), view.getUint8(at + 3));

  assert.equal(bytes.byteLength, 44 + 2 * 2 * 2);
  assert.equal(tag(0), 'RIFF');
  assert.equal(view.getUint32(4, true), bytes.byteLength - 8);
  assert.equal(tag(8), 'WAVE');
  assert.equal(tag(12), 'fmt ');
  assert.equal(view.getUint32(16, true), 16);        // PCM fmt chunk size
  assert.equal(view.getUint16(20, true), 1);         // format 1 = PCM
  assert.equal(view.getUint16(22, true), 2);         // channels
  assert.equal(view.getUint32(24, true), 16000);     // sample rate
  assert.equal(view.getUint32(28, true), 16000 * 2 * 2);   // byte rate
  assert.equal(view.getUint16(32, true), 4);         // block align
  assert.equal(view.getUint16(34, true), 16);        // bits per sample
  assert.equal(tag(36), 'data');
  assert.equal(view.getUint32(40, true), 2 * 2 * 2);
});

test('toWavBytes scales the two directions differently, because int16 is not symmetric', () => {
  // int16 runs -32768..32767. Using 32768 for both would clip every full-scale POSITIVE peak
  // into a wrap; using 32767 for both would leave the negative rail unreachable.
  const view = new DataView(toWavBytes(buf([[1, -1, 0, 2, -2]], 8000)));
  assert.equal(view.getInt16(44, true), 32767);
  assert.equal(view.getInt16(46, true), -32768);
  assert.equal(view.getInt16(48, true), 0);
  assert.equal(view.getInt16(50, true), 32767);      // out-of-range input is clamped first
  assert.equal(view.getInt16(52, true), -32768);
});

test('toWavBytes interleaves the channels frame by frame', () => {
  const view = new DataView(toWavBytes(buf([[1, 0], [0, -1]], 8000)));
  assert.deepEqual(
    [view.getInt16(44, true), view.getInt16(46, true), view.getInt16(48, true), view.getInt16(50, true)],
    [32767, 0, 0, -32768]);
});

/* ------------------------------------------------------------------ mixdown */

test('mixdown returns null when nothing is audible', () => {
  assert.equal(mixdown([]), null);
  assert.equal(mixdown(null), null);
  assert.equal(mixdown([{ buffer: buf([[1]]), muted: true }]), null);
  assert.equal(mixdown([{ buffer: null }]), null);
});

test('mixdown places each layer at its own offset', () => {
  const a = buf([[1, 1]], 8000);                       // 2 samples
  const b = buf([[0.5, 0.5]], 8000);
  const out = mixdown([{ buffer: a, offset: 0 }, { buffer: b, offset: 2 / 8000 }])!;
  assert.equal(out.length, 4);
  assert.deepEqual(ch(out), [1, 1, 0.5, 0.5]);
});

test('mixdown RESAMPLES UP to the highest input rate, never down', () => {
  /* docs/MIGRATION.md: resampling down to the telephony capture's rate would quietly destroy
     the music bed above it, which is exactly the case this editor exists for (a jingle over an
     8 kHz call). The rule is "highest wins" unless the caller names a rate. */
  const phone = buf([[0.5, 0.5, 0.5, 0.5], [0.5, 0.5, 0.5, 0.5]], 8000);
  const music = buf([[0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25]], 16000);
  const out = mixdown([{ buffer: phone }, { buffer: music }])!;
  assert.equal(out.sampleRate, 16000);
  assert.equal(out.numberOfChannels, 2);               // and the widest channel count wins too
  // Both layers are half a millisecond of constant level, so every frame is the same sum.
  assert.ok(out.length >= 8);
  assert.ok(Math.abs(out.getChannelData(0)[2] - 0.75) < 1e-6);
});

test('mixdown scales by 1/peak ONLY when the sum actually clips', () => {
  const loud = buf([[0.8, 0.8, 0.8, 0.8]]);
  const clipped = mixdown([{ buffer: loud }, { buffer: loud }])!;
  // 0.8 + 0.8 = 1.6, so the whole mix is scaled by 1/1.6 and lands exactly at full scale —
  // not at some fixed headroom, and not by dividing by the number of layers.
  assert.ok(Math.abs(peak(clipped) - 1) < 1e-6);
});

test('mixdown does NOT divide by the layer count', () => {
  /* The other half of the same MIGRATION.md decision: a blanket /n would make a single quiet
     layer inexplicably quieter just because a second, silent one exists. */
  const quiet = buf([[0.4, 0.4, 0.4, 0.4]]);
  const empty = buf([[0, 0, 0, 0]]);
  const out = mixdown([{ buffer: quiet }, { buffer: empty }])!;
  assert.ok(Math.abs(peak(out) - 0.4) < 1e-6);
  const alone = mixdown([{ buffer: quiet }])!;
  assert.ok(Math.abs(peak(alone) - 0.4) < 1e-6);
});

test('mixdown puts a mono layer on every channel and keeps a stereo layer on its own sides', () => {
  const mono = buf([[0.5, 0.5]]);
  const stereo = buf([[0.25, 0.25], [0, 0]]);
  const out = mixdown([{ buffer: mono }, { buffer: stereo }])!;
  assert.equal(out.numberOfChannels, 2);
  assert.ok(Math.abs(out.getChannelData(0)[0] - 0.75) < 1e-6);
  assert.ok(Math.abs(out.getChannelData(1)[0] - 0.5) < 1e-6);
});

test('mixdown applies a layer gain of 0 as silence, not as the default 1', () => {
  const out = mixdown([{ buffer: buf([[1, 1]]), gain: 0 }, { buffer: buf([[0.5, 0.5]]) }])!;
  assert.ok(Math.abs(peak(out) - 0.5) < 1e-6);
});

/* ------------------------------------------------------------------ peaks */

test('peaks returns exactly `width` min/max pairs', () => {
  const pk = peaks(ramp(1000), 37);
  assert.equal(pk.length, 37);
  assert.ok(pk.every(p => p.min <= p.max));
});

test('peaksBetween draws the same picture the old trim-then-scan did', () => {
  /* docs/MIGRATION.md defect 4: `draw()` ran in the playback rAF loop and got a zoomed-in
     layer's columns with `peaks(trim(buffer, from, to), cols)` — building an
     OfflineAudioContext and copying the slice sixty times a second, per layer. Reading the
     range in place is only a valid fix if it produces the identical picture, so that is what
     is asserted: for a spread of ranges and widths, column for column. */
  const b = new PcmBuffer(2, 4000, 8000);
  const l = b.getChannelData(0), r = b.getChannelData(1);
  for (let i = 0; i < 4000; i++) {
    l[i] = Math.sin(i / 13) * 0.8;
    r[i] = Math.cos(i / 7) * 0.4;
  }
  for (const [from, to] of [[0, 0.5], [0.1, 0.4], [0.0625, 0.0703125], [0.25, 0.5], [0, 0.001]]) {
    for (const w of [1, 7, 60, 512]) {
      assert.deepEqual(
        peaksBetween(b, from, to, w),
        peaks(trim(b, from, to), w),
        `range ${from}..${to} at width ${w}`);
    }
  }
});

test('peaksBetween honours a single channel the same way peaks does', () => {
  const b = buf([[1, -1, 1, -1, 1, -1, 1, -1], [0, 0, 0, 0, 0, 0, 0, 0]]);
  assert.deepEqual(peaksBetween(b, 0, 1, 1, 1), [{ min: 0, max: 0 }]);
  assert.deepEqual(peaksBetween(b, 0, 1, 1, 0), [{ min: -1, max: 1 }]);
});

/* ------------------------------------------------------------------ the clock */

test('clock reads in tenths, because that is what a mark is placed against', () => {
  assert.equal(clock(0), '0:00.0');
  assert.equal(clock(5.44), '0:05.4');
  assert.equal(clock(65.44), '1:05.4');
  assert.equal(clock(600), '10:00.0');
});

test('clock never prints a sixtieth second', () => {
  // The legacy version formatted the remainder on its own — `(s - m*60).toFixed(1)` — so
  // 119.97 s came out as `1:60.0`. Rounding the whole value first is the fix.
  assert.equal(clock(119.97), '2:00.0');
  assert.equal(clock(59.99), '1:00.0');
  assert.equal(clock(59.9), '0:59.9');
});

test('clock survives the values a fresh timeline hands it', () => {
  assert.equal(clock(NaN), '0:00.0');
  assert.equal(clock(Infinity), '0:00.0');
  assert.equal(clock(-1), '0:00.0');
});
