import test from 'node:test';
import assert from 'node:assert/strict';
import { placeTip, TIP_EDGE, TIP_GAP } from '../tipPlace.ts';

/* The tip bubble's placement, which is the only part of the tip system that is arithmetic
   rather than DOM. Two of these cases are regressions waiting to happen in a rewrite: the
   caret offset (a naive port pins it at 50% and it points at empty space on a phone) and the
   0x0 rectangle (which passes an off-screen bounds test because a zero rect at the origin is
   technically on screen). */

const TRIGGER = { top: 100, left: 600, width: 16, height: 16 };   // an ⓘ mid-page
const BUBBLE = { width: 320, height: 80 };

test('prefers below the trigger when there is room', () => {
  const at = placeTip(TRIGGER, BUBBLE, 1200, 800);
  assert.ok(at);
  assert.equal(at.place, 'below');
  assert.equal(at.top, TRIGGER.top + TRIGGER.height + TIP_GAP);
  // Centred on the trigger, so the caret sits at the bubble's middle.
  assert.equal(at.left, 608 - BUBBLE.width / 2);
  assert.equal(at.arrow, BUBBLE.width / 2);
});

test('flips above only when below does not fit and above does', () => {
  const at = placeTip(TRIGGER, BUBBLE, 1200, 200);
  assert.ok(at);
  assert.equal(at.place, 'above');
  assert.equal(at.top, TRIGGER.top - TIP_GAP - BUBBLE.height);
});

test('below wins the tie when neither side fits, and the bubble is clamped on screen', () => {
  const vh = 140;
  const at = placeTip({ top: 60, left: 600, width: 16, height: 16 }, BUBBLE, 1200, vh);
  assert.ok(at);
  // Not 'above': a bubble that flips sides on a few pixels of scroll is worse than one that
  // sits low, so 'above' has to earn the swap by actually fitting.
  assert.equal(at.place, 'below');
  assert.equal(at.top, vh - BUBBLE.height - TIP_EDGE);
  assert.ok(at.top >= TIP_EDGE);
});

test('the caret follows the trigger when the bubble is clamped to a screen edge', () => {
  // 375px viewport, ⓘ on a right-hand field: the bubble is ALWAYS clamped here.
  const trigger = { top: 100, left: 340, width: 16, height: 16 };
  const at = placeTip(trigger, BUBBLE, 375, 800);
  assert.ok(at);
  assert.equal(at.left, 375 - BUBBLE.width - TIP_EDGE);
  // The caret's absolute position is still the trigger's centre — which is the whole point.
  assert.equal(at.left + at.arrow, trigger.left + trigger.width / 2);
});

test('the caret never reaches either rounded corner', () => {
  const near = placeTip({ top: 100, left: 4, width: 16, height: 16 }, BUBBLE, 375, 800);
  assert.ok(near);
  assert.equal(near.left, TIP_EDGE);        // clamped to the left edge
  assert.equal(near.arrow, 12);             // trigger centre is 2px in; the caret stops at 12

  const far = placeTip({ top: 100, left: 372, width: 16, height: 16 }, BUBBLE, 375, 800);
  assert.ok(far);
  assert.equal(far.arrow, BUBBLE.width - 12);
});

test('a 0x0 trigger is hidden, not placed at the origin', () => {
  const zero = { top: 0, left: 0, width: 0, height: 0 };
  // The disguise: this rect passes every bounds test below, because a zero rect at the origin
  // is "on screen". It is what an element inside a display:none subtree reports.
  assert.ok(zero.top <= 800 && zero.left <= 1200 && zero.top + zero.height >= 0);
  assert.equal(placeTip(zero, BUBBLE, 1200, 800), null);
});

test('a trigger scrolled out of view is hidden', () => {
  assert.equal(placeTip({ top: -50, left: 600, width: 16, height: 16 }, BUBBLE, 1200, 800), null);
  assert.equal(placeTip({ top: 900, left: 600, width: 16, height: 16 }, BUBBLE, 1200, 800), null);
  assert.equal(placeTip({ top: 100, left: -40, width: 16, height: 16 }, BUBBLE, 1200, 800), null);
  assert.equal(placeTip({ top: 100, left: 1300, width: 16, height: 16 }, BUBBLE, 1200, 800), null);
});

test('every coordinate is a whole pixel', () => {
  const at = placeTip({ top: 100.4, left: 600.6, width: 15.3, height: 16.7 }, { width: 321.5, height: 80.2 }, 1200, 800);
  assert.ok(at);
  for (const v of [at.top, at.left, at.arrow]) assert.equal(v, Math.round(v));
});
