/* Formatting primitives: no DOM, no React, no dictionary.
   ======================================================
   Everything here is a pure function over a value the backend sent. They live in one file
   because the legacy stack had the same helpers copy-pasted across `brand.js`, `account.html`
   and `index.html`, and the copies had already drifted. The drift that mattered is in the
   duration formatter: brand.js's `fmt` floors and turns a missing length into `0:00`, while
   account.html's `fmtDur` rounds and returns `—`. Both are kept below, under separate names,
   because they answer different questions and only one of them is right per surface — see
   `duration` and `durationOrEmpty`. Of the capitalisers, only `workbench.js`'s `capFirst` knew
   about Georgian.

   Nothing here emits a translatable string. The em dash is the app's "no value" glyph in all
   three languages, so it is a symbol rather than a dictionary key. */

/** The placeholder every renderer shows for an absent value. */
export const EMPTY = '—';

/** Seconds → `m:ss`, the PLAYER'S CLOCK (brand.js `fmt`).

    Deliberately NOT `h:mm:ss`: a 90-minute recording reads as `90:00`, which is what the
    legacy player has always shown and what an operator scrubbing a call transcript expects to
    match against the timestamps in the transcript itself. Non-finite and negative inputs are
    clamped rather than rendered: `audio.duration` is NaN until metadata loads, and the legacy
    version turned a negative `currentTime` into the nonsense `-1:-1`.

    That clamp is why this one is wrong for a table: a clock that reads `0:00` for one frame
    before metadata arrives is right, but a recordings row that reads `0:00` because the column
    is null is a claim that the recording is empty. Use `durationOrEmpty` there. */
export function duration(seconds: number | null | undefined): string {
  const s = Number(seconds);
  const whole = Number.isFinite(s) && s > 0 ? Math.floor(s) : 0;
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

/** Seconds → `m:ss`, or the placeholder for a length that was never measured
    (account.html `fmtDur`, for the recordings table's nullable `duration_s`).

    A real zero is unreachable through this path — a stored recording of no length is a failed
    upload, not a zero-second call — so `<= 0` joins null and NaN in the empty state, as the
    legacy version has it. It floors where `fmtDur` rounds: rounding the seconds independently
    of the minutes prints 119.7s as `1:60`. */
export function durationOrEmpty(seconds: number | null | undefined): string {
  const s = Number(seconds);
  return Number.isFinite(s) && s > 0 ? duration(s) : EMPTY;
}

/** Byte count → `1.4 MB` / `812 KB` / `43 B` (account.html + index.html `human`).

    The fractional digit disappears above 10 MB: at that size the tenth of a megabyte is noise
    next to the number it decorates, and the column stops jittering as a batch uploads. */
export function bytes(n: number | null | undefined): string {
  const v = Number(n) || 0;
  if (v >= 1048576) return `${(v / 1048576).toFixed(v >= 10485760 ? 0 : 1)} MB`;
  if (v >= 1024) return `${Math.round(v / 1024)} KB`;
  return `${Math.round(v)} B`;
}

/** Integer → grouped digits in the visitor's locale. */
export function count(n: number | null | undefined): string {
  return (Number(n) || 0).toLocaleString();
}

/** An ISO timestamp → the visitor's local date and time, or the placeholder.

    An unparseable value becomes the placeholder rather than "Invalid Date": the string comes
    from a jsonb column that has held whatever a past import wrote into it. */
export function dateTime(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === '') return EMPTY;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? EMPTY : d.toLocaleString();
}

/** A 0..1 model confidence → a whole percent, or null when there is nothing to show. */
export function percent(value: number | null | undefined): number | null {
  // Not `Number(value)` alone: `Number(null)` is 0, which would render a missing confidence as
  // a confident 0% rather than as nothing at all.
  if (value === null || value === undefined) return null;
  const v = Number(value);
  return Number.isFinite(v) ? Math.round(v * 100) : null;
}

/* Georgian is unicameral. `toUpperCase()` maps Mkhedruli to MTAVRULI, so "ოპერატორი" would
   render as "Ოპერატორი" — one Mtavruli letter followed by Mkhedruli, a mixed-script word the
   orthography never produces. This is not an edge case here: a pasted transcript keeps its own
   speaker label, so it is the NORMAL path for a Georgian call. Latin and Cyrillic ids still
   capitalise. The ranges are Georgian (Asomtavruli + Mkhedruli), Georgian Extended (Mtavruli)
   and Georgian Supplement (Nuskhuri), written as escapes so the guard cannot be broken by a
   file that gets re-saved in the wrong encoding. */
const GEORGIAN = /[\u10A0-\u10FF\u1C90-\u1CBF\u2D00-\u2D2F]/;

/** Title-case the first character, but only where the script actually has case.

    `u.length !== 1` covers the other trap: German ß upper-cases to the two characters "SS",
    which would lengthen the word rather than capitalise it. */
export function capFirst(value: unknown): string {
  const str = String(value === null || value === undefined ? '' : value);
  const c = str.charAt(0);
  const u = c.toUpperCase();
  if (!c || u === c || u.length !== 1 || GEORGIAN.test(c)) return str;
  return u + str.slice(1);
}
