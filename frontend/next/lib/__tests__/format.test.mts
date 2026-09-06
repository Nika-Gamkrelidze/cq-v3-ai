import test from 'node:test';
import assert from 'node:assert/strict';
import { bytes, capFirst, dateTime, duration, durationOrEmpty, percent } from '../format.ts';

/* capFirst's Georgian guard is the reason this file exists.
   The failure it prevents is invisible to a developer running the UI in English: a default
   .toUpperCase() maps Mkhedruli to MTAVRULI and ships "Ოპერატორი" — one Mtavruli letter on a
   Mkhedruli word, a mixed-script form the orthography never produces — into the speaker
   labels of every Georgian call, which is the app's main market. */

const MTAVRULI = /[Ა-Ჿ]/;

test('capFirst: the guard is load-bearing — the engine really does case-map Mkhedruli', () => {
  // If this ever stops holding, the guard is dead code and the test above it proves nothing.
  assert.notEqual('ო'.toUpperCase(), 'ო');
  assert.match('ო'.toUpperCase(), MTAVRULI);
});

test('capFirst: Georgian is returned untouched', () => {
  assert.equal(capFirst('ოპერატორი'), 'ოპერატორი');
  assert.equal(capFirst('კლიენტი'), 'კლიენტი');
  assert.doesNotMatch(capFirst('ოპერატორი'), MTAVRULI);
});

test('capFirst: every Georgian script in the guard', () => {
  assert.equal(capFirst('ⴀⴁⴂ'), 'ⴀⴁⴂ');        // Nuskhuri
  assert.equal(capFirst('ႠႡႢ'), 'ႠႡႢ');        // Asomtavruli
  assert.equal(capFirst('ᲐᲑᲒ'), 'ᲐᲑᲒ');        // Mtavruli, already upper
});

test('capFirst: cased scripts still capitalise', () => {
  assert.equal(capFirst('operator'), 'Operator');
  assert.equal(capFirst('оператор'), 'Оператор');
  assert.equal(capFirst('Agent'), 'Agent');
});

test('capFirst: a one-to-many upper-casing is left alone', () => {
  // 'ß'.toUpperCase() is 'SS' — capitalising would lengthen the word, not title-case it.
  assert.equal(capFirst('ßeta'), 'ßeta');
});

test('capFirst: nothing to capitalise', () => {
  assert.equal(capFirst(''), '');
  assert.equal(capFirst(null), '');
  assert.equal(capFirst(undefined), '');
  assert.equal(capFirst('123'), '123');
  assert.equal(capFirst(7), '7');
});

test('duration: m:ss, floored, hours not carried', () => {
  assert.equal(duration(0), '0:00');
  assert.equal(duration(9.9), '0:09');
  assert.equal(duration(65), '1:05');
  assert.equal(duration(5400), '90:00');
});

test('duration: the clock clamps — a missing length is 0:00, never -1:-1', () => {
  // Right for the player, where `audio.duration` is NaN until metadata loads and a rewind can
  // hand you a negative `currentTime`.
  assert.equal(duration(null), '0:00');
  assert.equal(duration(undefined), '0:00');
  assert.equal(duration(NaN), '0:00');
  assert.equal(duration(-3), '0:00');
});

test('durationOrEmpty: a never-measured recording is not a zero-length one', () => {
  // The recordings table's `duration_s` is nullable. `0:00` there is a claim about the
  // recording; the placeholder is a statement about the column, which is what is true.
  assert.equal(durationOrEmpty(null), '—');
  assert.equal(durationOrEmpty(undefined), '—');
  assert.equal(durationOrEmpty(NaN), '—');
  assert.equal(durationOrEmpty(0), '—');
  assert.equal(durationOrEmpty(-3), '—');
});

test('durationOrEmpty: a measured length reads exactly like the clock', () => {
  assert.equal(durationOrEmpty(65), '1:05');
  assert.equal(durationOrEmpty(5400), '90:00');
  // Floored, not rounded: account.html's fmtDur rounds the seconds independently of the
  // minutes, which prints 119.7s as the impossible "1:60".
  assert.equal(durationOrEmpty(119.7), '1:59');
});

test('bytes: the tenth of a megabyte drops above 10 MB', () => {
  assert.equal(bytes(0), '0 B');
  assert.equal(bytes(900), '900 B');
  assert.equal(bytes(2048), '2 KB');
  assert.equal(bytes(1572864), '1.5 MB');
  assert.equal(bytes(52428800), '50 MB');
});

test('dateTime: an unparseable value is the placeholder, not "Invalid Date"', () => {
  assert.equal(dateTime(null), '—');
  assert.equal(dateTime(''), '—');
  assert.equal(dateTime('not a date'), '—');
  assert.equal(dateTime('2026-01-02T03:04:05Z'), new Date('2026-01-02T03:04:05Z').toLocaleString());
});

test('percent: 0..1 to whole percent, null when there is nothing to show', () => {
  assert.equal(percent(0.836), 84);
  assert.equal(percent(0), 0);
  assert.equal(percent(null), null);
  assert.equal(percent(undefined), null);
});
