import test from 'node:test';
import assert from 'node:assert/strict';
import { diff, diffParts, side } from '../../app/workspace/diff.ts';

/* The review queue's side-by-side diff. What is being pinned here is not "a diff works" but the
   three properties the panel's correctness rests on:
     1. head/tail are the untouched context and appear in BOTH panes,
     2. the old pane never shows an insertion and the new pane never shows a deletion,
     3. either pane, read on its own, reconstructs that version of the text exactly —
        which is the whole reason the panel is two panes instead of one merged stream. */

const text = (runs: { text: string }[]) => runs.map(r => r.text).join('');

test('identical texts produce no runs at all', () => {
  const d = diff('same text', 'same text');
  assert.equal(d.head, 'same text');
  assert.equal(d.runs.length, 0);
  assert.equal(text(side(d, 'old')), 'same text');
  assert.equal(text(side(d, 'new')), 'same text');
});

test('the shared prefix and suffix are trimmed off before aligning', () => {
  const d = diffParts('the red door', 'the blue door');
  assert.equal(d.head, 'the ');
  assert.equal(d.tail, ' door');
  // Only the middle is aligned, so nothing outside it can be marked as changed.
  assert.ok(d.parts.every(p => !p.text.includes('door')));
});

test('each pane reconstructs its own version exactly', () => {
  const a = 'Refunds are processed within 14 days.';
  const b = 'Refunds are processed within 30 days, excluding weekends.';
  const d = diff(a, b);
  assert.equal(text(side(d, 'old')), a);
  assert.equal(text(side(d, 'new')), b);
});

test('the old pane never carries an insertion, the new pane never a deletion', () => {
  const d = diff('alpha beta gamma', 'alpha delta gamma');
  assert.ok(side(d, 'old').every(r => r.op !== '+'));
  assert.ok(side(d, 'new').every(r => r.op !== '-'));
});

test('adjacent same-op characters coalesce into one run', () => {
  // One <del> around a deleted phrase, not one per character — otherwise a 400-character
  // deletion renders 400 elements and the strike-through breaks between every letter.
  const d = diff('hello world', 'hello brave world');
  assert.ok(d.runs.length < 6, `expected coalesced runs, got ${d.runs.length}`);
  assert.equal(text(side(d, 'new')), 'hello brave world');
});

test('an empty side is handled as a whole insertion or deletion', () => {
  const added = diff('', 'brand new policy');
  assert.equal(text(side(added, 'old')), '');
  assert.equal(text(side(added, 'new')), 'brand new policy');
  const removed = diff('old policy', '');
  assert.equal(text(side(removed, 'old')), 'old policy');
  assert.equal(text(side(removed, 'new')), '');
});

test('a long changed middle steps down to word tokens instead of hanging', () => {
  // Over the 800-character threshold the aligner switches granularity — the point is that it
  // still returns a correct diff rather than attempting a 1000x1000 character matrix.
  const a = 'x'.repeat(900) + ' tail';
  const b = 'y'.repeat(900) + ' tail';
  const d = diff(a, b);
  assert.equal(text(side(d, 'old')), a);
  assert.equal(text(side(d, 'new')), b);
});

test('two texts too large to align at all are reported as a wholesale replacement', () => {
  // Past the cell budget even line granularity does not fit; the honest answer is "this whole
  // block was replaced", not a frozen tab.
  const a = Array.from({ length: 3000 }, (_, i) => `line ${i}\n`).join('');
  const b = Array.from({ length: 3000 }, (_, i) => `LINE ${i}\n`).join('');
  const d = diff(a, b);
  assert.equal(text(side(d, 'old')), a);
  assert.equal(text(side(d, 'new')), b);
});
