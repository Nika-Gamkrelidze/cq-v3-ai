/* Audio editing operations, as pure functions over Web Audio AudioBuffers.
   =========================================================================
   Deliberately UI-free and library-free: no wavesurfer, no DOM, no fetch. wavesurfer draws
   waveforms and handles the drag-to-select UI; it has no editing primitives, so everything
   that actually changes audio lives here. Keeping the two apart means the editing rules can
   be reasoned about (and tested) without a canvas, and swapping the waveform library later
   touches none of this.

   Every operation returns a NEW AudioBuffer and never mutates its input — that is what makes
   undo a stack of references rather than a diff, and it is why the caller can hold a history
   without copying defensively.

   All of it runs in the browser. A customer's call recording is decoded, edited and encoded
   locally; the only thing that ever reaches the server is the finished file the user chooses
   to convert, and only when they pick a format we do not encode ourselves. */
(function (global) {
  'use strict';

  /* An OfflineAudioContext is the only portable way to MAKE an AudioBuffer, and Safari still
     wants the prefixed constructor. Created per call: these are cheap, and holding one open
     keeps an audio thread alive for no reason. */
  function ctx(channels, length, rate) {
    const C = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    // length must be >= 1 even for an empty result, or the constructor throws.
    return new C(channels, Math.max(1, length | 0), rate);
  }

  function make(like, length, channels) {
    const ch = channels || like.numberOfChannels;
    return ctx(ch, length, like.sampleRate).createBuffer(ch, Math.max(1, length | 0), like.sampleRate);
  }

  /** Copy a span of every channel from `src` into `dst` at `at`. */
  function blit(src, dst, srcStart, count, at) {
    const n = Math.min(dst.numberOfChannels, src.numberOfChannels);
    for (let c = 0; c < n; c++) {
      const from = src.getChannelData(c).subarray(srcStart, srcStart + count);
      dst.getChannelData(c).set(from, at);
    }
  }

  /** Seconds -> a sample index clamped inside the buffer. Every op funnels through this, so
      a selection dragged past the end (or a float that lands a sample beyond it) can never
      produce a negative length or read out of bounds. */
  function idx(buf, seconds) {
    const i = Math.round((seconds || 0) * buf.sampleRate);
    return Math.max(0, Math.min(buf.length, i));
  }

  function span(buf, from, to) {
    let a = idx(buf, from), b = idx(buf, to);
    if (b < a) { const t = a; a = b; b = t; }
    return { a, b, len: b - a };
  }

  // ---- structural edits ----------------------------------------------------

  /** Everything OUTSIDE [from,to) — the selection is removed and the audio closes up. */
  function cut(buf, from, to) {
    const { a, b, len } = span(buf, from, to);
    if (!len) return buf;
    const out = make(buf, buf.length - len);
    blit(buf, out, 0, a, 0);
    blit(buf, out, b, buf.length - b, a);
    return out;
  }

  /** Only [from,to) — everything else is discarded. */
  function trim(buf, from, to) {
    const { a, len } = span(buf, from, to);
    if (!len) return buf;
    const out = make(buf, len);
    blit(buf, out, a, len, 0);
    return out;
  }

  /** [from,to) replaced by silence of the same length: the timeline does not shift, which is
      what you want when redacting a card number out of a call. */
  function silence(buf, from, to) {
    const { a, b, len } = span(buf, from, to);
    if (!len) return buf;
    const out = make(buf, buf.length);
    blit(buf, out, 0, buf.length, 0);
    for (let c = 0; c < out.numberOfChannels; c++) out.getChannelData(c).fill(0, a, b);
    return out;
  }

  /** Insert `seconds` of silence at `at`, pushing the rest later. */
  function insertSilence(buf, at, seconds) {
    const pad = Math.max(0, Math.round((seconds || 0) * buf.sampleRate));
    if (!pad) return buf;
    const a = idx(buf, at);
    const out = make(buf, buf.length + pad);
    blit(buf, out, 0, a, 0);
    blit(buf, out, a, buf.length - a, a + pad);
    return out;
  }

  // ---- level -------------------------------------------------------------

  /** Multiply [from,to) by `gain`. `null` bounds mean the whole buffer.

      Samples are CLAMPED to [-1,1] rather than left to wrap: a float32 buffer will happily
      hold 3.0, sound fine in this tab, and then wrap into loud digital noise the moment it is
      encoded to a 16-bit format. Clipping is audible and honest; wrapping is neither. */
  function gain(buf, factor, from, to) {
    const { a, b } = (from == null && to == null)
      ? { a: 0, b: buf.length } : span(buf, from, to);
    if (b <= a || factor === 1) return buf;
    const out = make(buf, buf.length);
    blit(buf, out, 0, buf.length, 0);
    for (let c = 0; c < out.numberOfChannels; c++) {
      const d = out.getChannelData(c);
      for (let i = a; i < b; i++) {
        const v = d[i] * factor;
        d[i] = v > 1 ? 1 : v < -1 ? -1 : v;
      }
    }
    return out;
  }

  const dbToGain = db => Math.pow(10, (db || 0) / 20);

  /** Peak of [from,to) (or the whole buffer), across all channels. */
  function peak(buf, from, to) {
    const { a, b } = (from == null && to == null)
      ? { a: 0, b: buf.length } : span(buf, from, to);
    let m = 0;
    for (let c = 0; c < buf.numberOfChannels; c++) {
      const d = buf.getChannelData(c);
      for (let i = a; i < b; i++) { const v = Math.abs(d[i]); if (v > m) m = v; }
    }
    return m;
  }

  /** Scale so the loudest sample hits `target` (default -1 dBFS of headroom).

      Peak normalisation, not loudness (LUFS): it is what "make this louder without clipping"
      means to someone looking at a waveform, and it is exactly reversible. Silence is left
      alone — scaling a digital-zero region by anything is still zero, and dividing by its
      peak would be a division by zero. */
  function normalize(buf, targetPeak, from, to) {
    const target = targetPeak == null ? 0.891 : targetPeak;   // ~-1 dBFS
    const p = peak(buf, from, to);
    if (!p) return buf;
    return gain(buf, target / p, from, to);
  }

  /** Linear ramp across [from,to): 'in' rises 0->1, 'out' falls 1->0. */
  function fade(buf, from, to, dir) {
    const { a, b, len } = span(buf, from, to);
    if (len < 2) return buf;
    const out = make(buf, buf.length);
    blit(buf, out, 0, buf.length, 0);
    for (let c = 0; c < out.numberOfChannels; c++) {
      const d = out.getChannelData(c);
      for (let i = a; i < b; i++) {
        const t = (i - a) / (len - 1);
        d[i] *= (dir === 'out' ? 1 - t : t);
      }
    }
    return out;
  }

  /** Flip the sign of every sample in the range. Cheap, and the standard way to test whether
      two takes are phase-cancelling each other. */
  function invert(buf, from, to) {
    const { a, b } = (from == null && to == null)
      ? { a: 0, b: buf.length } : span(buf, from, to);
    const out = make(buf, buf.length);
    blit(buf, out, 0, buf.length, 0);
    for (let c = 0; c < out.numberOfChannels; c++) {
      const d = out.getChannelData(c);
      for (let i = a; i < b; i++) d[i] = -d[i];
    }
    return out;
  }

  /** Reverse the range in place-of-copy. */
  function reverse(buf, from, to) {
    const { a, b } = (from == null && to == null)
      ? { a: 0, b: buf.length } : span(buf, from, to);
    const out = make(buf, buf.length);
    blit(buf, out, 0, buf.length, 0);
    for (let c = 0; c < out.numberOfChannels; c++) {
      const d = out.getChannelData(c);
      for (let i = a, j = b - 1; i < j; i++, j--) { const t = d[i]; d[i] = d[j]; d[j] = t; }
    }
    return out;
  }

  // ---- channels ----------------------------------------------------------
  /* Call recordings are very often two-channel with the agent on one side and the customer
     on the other, so per-channel work is the point of this section, not a flourish. */

  /** One channel as its own mono buffer — "give me just the customer's side". */
  function extractChannel(buf, channel) {
    const out = make(buf, buf.length, 1);
    out.getChannelData(0).set(buf.getChannelData(Math.min(channel, buf.numberOfChannels - 1)));
    return out;
  }

  /** Average every channel down to one. */
  function toMono(buf) {
    if (buf.numberOfChannels === 1) return buf;
    const out = make(buf, buf.length, 1);
    const d = out.getChannelData(0);
    for (let c = 0; c < buf.numberOfChannels; c++) {
      const s = buf.getChannelData(c);
      for (let i = 0; i < buf.length; i++) d[i] += s[i] / buf.numberOfChannels;
    }
    return out;
  }

  /** Duplicate a mono buffer to stereo (no-op for anything already multi-channel). */
  function toStereo(buf) {
    if (buf.numberOfChannels >= 2) return buf;
    const out = make(buf, buf.length, 2);
    out.getChannelData(0).set(buf.getChannelData(0));
    out.getChannelData(1).set(buf.getChannelData(0));
    return out;
  }

  function swapChannels(buf, x, y) {
    if (buf.numberOfChannels < 2) return buf;
    const a = x == null ? 0 : x, b = y == null ? 1 : y;
    const out = make(buf, buf.length);
    blit(buf, out, 0, buf.length, 0);
    const t = Float32Array.from(out.getChannelData(a));
    out.getChannelData(a).set(out.getChannelData(b));
    out.getChannelData(b).set(t);
    return out;
  }

  /** Gain on ONE channel only — the usual fix for a recording where the agent's mic is hot
      and the caller is barely audible. */
  function channelGain(buf, channel, factor) {
    const out = make(buf, buf.length);
    blit(buf, out, 0, buf.length, 0);
    const d = out.getChannelData(Math.min(channel, out.numberOfChannels - 1));
    for (let i = 0; i < d.length; i++) {
      const v = d[i] * factor;
      d[i] = v > 1 ? 1 : v < -1 ? -1 : v;
    }
    return out;
  }

  const muteChannel = (buf, channel) => channelGain(buf, channel, 0);

  // ---- encoding ----------------------------------------------------------

  /** AudioBuffer -> 16-bit PCM WAV Blob.

      WAV is encoded HERE and every other format is not: a RIFF header plus interleaved
      int16 is a page of arithmetic, while mp3/opus/gsm mean either a multi-megabyte wasm
      encoder in the page or a second implementation of what the server's ffmpeg already
      does. So the editor produces WAV locally, and anything else is the existing converter's
      job — one implementation, one format catalogue, one quota.

      Dithering is deliberately omitted: this is speech destined for transcription and QA,
      where the honest 16-bit truncation is inaudible and a noise floor would be a lie. */
  function toWav(buf) {
    const channels = buf.numberOfChannels, rate = buf.sampleRate, frames = buf.length;
    const bytes = 44 + frames * channels * 2;
    const view = new DataView(new ArrayBuffer(bytes));
    let p = 0;
    const str = s => { for (let i = 0; i < s.length; i++) view.setUint8(p++, s.charCodeAt(i)); };
    const u32 = v => { view.setUint32(p, v, true); p += 4; };
    const u16 = v => { view.setUint16(p, v, true); p += 2; };

    str('RIFF'); u32(bytes - 8); str('WAVE');
    str('fmt '); u32(16); u16(1); u16(channels);
    u32(rate); u32(rate * channels * 2); u16(channels * 2); u16(16);
    str('data'); u32(frames * channels * 2);

    const data = [];
    for (let c = 0; c < channels; c++) data.push(buf.getChannelData(c));
    for (let i = 0; i < frames; i++) {
      for (let c = 0; c < channels; c++) {
        let v = data[c][i];
        v = v > 1 ? 1 : v < -1 ? -1 : v;
        // Asymmetric on purpose: int16 runs -32768..32767, so the two directions scale by
        // different amounts. Using 32768 for both would clip every full-scale positive peak.
        view.setInt16(p, v < 0 ? v * 0x8000 : v * 0x7fff, true);
        p += 2;
      }
    }
    return new Blob([view.buffer], { type: 'audio/wav' });
  }

  /** Peak pairs per pixel column, for drawing. Returns [{min,max}] of length `width`.
      Computed from the buffer rather than re-decoding, so redrawing after an edit costs a
      scan and not another decode of a thirty-minute call. */
  function peaks(buf, width, channel) {
    const w = Math.max(1, width | 0);
    const out = new Array(w);
    const chans = channel == null
      ? Array.from({ length: buf.numberOfChannels }, (_, c) => buf.getChannelData(c))
      : [buf.getChannelData(Math.min(channel, buf.numberOfChannels - 1))];
    const step = buf.length / w;
    for (let x = 0; x < w; x++) {
      const a = Math.floor(x * step), b = Math.min(buf.length, Math.floor((x + 1) * step));
      let mn = 0, mx = 0;
      for (const d of chans) {
        for (let i = a; i < b; i++) { const v = d[i]; if (v < mn) mn = v; else if (v > mx) mx = v; }
      }
      out[x] = { min: mn, max: mx };
    }
    return out;
  }

  global.CQAudio = {
    cut, trim, silence, insertSilence,
    gain, dbToGain, peak, normalize, fade, invert, reverse,
    extractChannel, toMono, toStereo, swapChannels, channelGain, muteChannel,
    toWav, peaks,
    _internals: { idx, span, make, blit },
  };
})(window);
