import test from 'node:test';
import assert from 'node:assert/strict';
import { SILENCE_MS, backoffMs, liveIndicator } from '../../app/noc/live.ts';

/* The pure parts of the live NOC feed: when to reconnect, and what the indicator says. */

test('backoffMs: 1, 2, 5, 10 s and then it stays at 10 s', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 50].map(backoffMs), [1000, 2000, 5000, 10_000, 10_000, 10_000]);
});

test('backoffMs: a nonsense attempt number falls back to the first wait', () => {
  assert.equal(backoffMs(-3), 1000);
  assert.equal(backoffMs(Number.NaN), 1000);
});

test('liveIndicator: each state has its own colour, live is green', () => {
  assert.deepEqual(liveIndicator({ state: 'live', ageSeconds: 3 }), { key: 'live', color: 'var(--ok)' });
  assert.equal(liveIndicator({ state: 'connecting', ageSeconds: null }).color, 'var(--pending)');
  assert.equal(liveIndicator({ state: 'retrying', ageSeconds: 12 }).color, 'var(--pending)');
  assert.equal(liveIndicator({ state: 'paused', ageSeconds: 600 }).color, 'var(--muted)');
  assert.equal(liveIndicator({ state: 'polling', ageSeconds: 4 }).color, 'var(--beam)');
});

test('liveIndicator: "live" with nothing received for longer than the silence limit reads as reconnecting', () => {
  const limit = SILENCE_MS / 1000;
  assert.equal(liveIndicator({ state: 'live', ageSeconds: limit }).key, 'live');
  assert.equal(liveIndicator({ state: 'live', ageSeconds: limit + 1 }).key, 'retrying');
  // Only a live feed goes stale: a paused tab is old on purpose.
  assert.equal(liveIndicator({ state: 'paused', ageSeconds: limit + 100 }).key, 'paused');
});
