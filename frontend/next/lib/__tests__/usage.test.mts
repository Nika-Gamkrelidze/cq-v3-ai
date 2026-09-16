import test from 'node:test';
import assert from 'node:assert/strict';
import { duration, nextSort, pageRange, sortRows, usageQuery } from '../../app/usage/logic.ts';
import { EMPTY_FILTERS, type Filters } from '../../app/usage/types.ts';

/* The usage page's pure half. Each of these decides what the operator is shown as a number
   (which range was asked for, which row is "biggest", how long a recording ran), and each has
   an edge a tidy-up would plausibly flatten: a stale custom date narrowing a preset, a null
   topping a biggest-first list, 59.6 seconds printed as 0:60. */

const params = (q: string) => new URLSearchParams(q.replace(/^\?/, ''));
const f = (patch: Partial<Filters> = {}): Filters => ({ ...EMPTY_FILTERS, ...patch });

/* ------------------------------------------------------------------ usageQuery */

test('usageQuery: the default filters send only the 30-day window', () => {
  assert.equal(usageQuery(EMPTY_FILTERS), '?window=30d');
});

test('usageQuery: a preset ignores dates left over from a custom range', () => {
  const p = params(usageQuery(f({ window: '7d', from: '2026-01-01', to: '2026-01-31' })));
  assert.equal(p.get('window'), '7d');
  assert.equal(p.has('from'), false);
  assert.equal(p.has('to'), false);
});

test('usageQuery: a custom range sends from/to and no window', () => {
  const p = params(usageQuery(f({ window: 'custom', from: '2026-08-01', to: '2026-08-31' })));
  assert.equal(p.get('from'), '2026-08-01');
  assert.equal(p.get('to'), '2026-08-31');
  assert.equal(p.has('window'), false);
});

test('usageQuery: a half-filled custom range sends the half it has', () => {
  const p = params(usageQuery(f({ window: 'custom', from: '2026-08-01' })));
  assert.equal(p.get('from'), '2026-08-01');
  assert.equal(p.has('to'), false);
  assert.equal(p.has('window'), false);
});

test('usageQuery: an empty custom range falls back to the default window, never "custom"', () => {
  const p = params(usageQuery(f({ window: 'custom' })));
  assert.equal(p.get('window'), '30d');
});

test('usageQuery: empty filters are omitted, set ones are sent', () => {
  const p = params(usageQuery(f({
    client_id: '5f0c1a2e-0000-4000-8000-000000000001', group: 'factcheck', provider: '', model: 'm-1',
    capability: '', status: 'failed',
  })));
  assert.equal(p.get('client_id'), '5f0c1a2e-0000-4000-8000-000000000001');
  assert.equal(p.get('group'), 'factcheck');
  assert.equal(p.get('model'), 'm-1');
  assert.equal(p.get('status'), 'failed');
  for (const k of ['provider', 'capability']) assert.equal(p.has(k), false, k);
});

test('usageQuery: extras are added, stringified, and skipped when null, undefined or empty', () => {
  const p = params(usageQuery(EMPTY_FILTERS, { q: 'hello world', limit: 50, offset: 0, sort: '', dir: null, x: undefined }));
  assert.equal(p.get('q'), 'hello world');
  assert.equal(p.get('limit'), '50');
  // Zero is a value (the first page), not an absence.
  assert.equal(p.get('offset'), '0');
  for (const k of ['sort', 'dir', 'x']) assert.equal(p.has(k), false, k);
});

test('usageQuery: values are URL-encoded', () => {
  const q = usageQuery(f({ model: 'a/b c&d' }), { q: '100%' });
  assert.equal(params(q).get('model'), 'a/b c&d');
  assert.equal(params(q).get('q'), '100%');
  assert.ok(!q.includes('&d'), q);
});

/* ------------------------------------------------------------------ nextSort */

test('nextSort: the same column flips direction both ways', () => {
  assert.deepEqual(nextSort({ key: 'calls', dir: 'desc' }, 'calls'), { key: 'calls', dir: 'asc' });
  assert.deepEqual(nextSort({ key: 'calls', dir: 'asc' }, 'calls'), { key: 'calls', dir: 'desc' });
});

test('nextSort: a new column starts descending unless told otherwise', () => {
  assert.deepEqual(nextSort({ key: 'calls', dir: 'asc' }, 'total_tokens'), { key: 'total_tokens', dir: 'desc' });
  assert.deepEqual(nextSort({ key: 'calls', dir: 'desc' }, 'name', 'asc'), { key: 'name', dir: 'asc' });
});

test('nextSort: firstDir does not override flipping the current column', () => {
  assert.deepEqual(nextSort({ key: 'name', dir: 'asc' }, 'name', 'asc'), { key: 'name', dir: 'desc' });
});

/* ------------------------------------------------------------------ sortRows */

type Row = { name: string | null; n: number | null | undefined };
const get = (r: Row, k: string) => r[k as keyof Row];

test('sortRows: numbers compare numerically, not as strings', () => {
  const rows: Row[] = [{ name: 'a', n: 9 }, { name: 'b', n: 100 }, { name: 'c', n: 20 }];
  assert.deepEqual(sortRows(rows, { key: 'n', dir: 'desc' }, get).map(r => r.n), [100, 20, 9]);
  assert.deepEqual(sortRows(rows, { key: 'n', dir: 'asc' }, get).map(r => r.n), [9, 20, 100]);
});

test('sortRows: strings compare as text', () => {
  const rows: Row[] = [{ name: 'beta', n: 1 }, { name: 'Alpha', n: 2 }, { name: 'gamma', n: 3 }];
  assert.deepEqual(sortRows(rows, { key: 'name', dir: 'asc' }, get).map(r => r.name), ['Alpha', 'beta', 'gamma']);
  assert.deepEqual(sortRows(rows, { key: 'name', dir: 'desc' }, get).map(r => r.name), ['gamma', 'beta', 'Alpha']);
});

test('sortRows: null, undefined and empty values sink to the bottom in BOTH directions', () => {
  const rows: Row[] = [
    { name: 'x', n: null }, { name: 'y', n: 5 }, { name: '', n: undefined }, { name: 'z', n: 50 },
  ];
  assert.deepEqual(sortRows(rows, { key: 'n', dir: 'desc' }, get).slice(0, 2).map(r => r.n), [50, 5]);
  assert.deepEqual(sortRows(rows, { key: 'n', dir: 'asc' }, get).slice(0, 2).map(r => r.n), [5, 50]);
  for (const dir of ['asc', 'desc'] as const) {
    const byName = sortRows(rows, { key: 'name', dir }, get);
    assert.equal(byName[byName.length - 1].name, '', dir);
  }
});

test('sortRows: zero is a value, not an absence', () => {
  const rows: Row[] = [{ name: 'a', n: null }, { name: 'b', n: 0 }, { name: 'c', n: 3 }];
  assert.deepEqual(sortRows(rows, { key: 'n', dir: 'asc' }, get).map(r => r.n), [0, 3, null]);
});

test('sortRows: returns a new array and leaves the input alone', () => {
  const rows: Row[] = [{ name: 'a', n: 1 }, { name: 'b', n: 2 }];
  const out = sortRows(rows, { key: 'n', dir: 'desc' }, get);
  assert.notEqual(out, rows);
  assert.deepEqual(rows.map(r => r.n), [1, 2]);
});

/* ------------------------------------------------------------------ pageRange */

test('pageRange: an empty list is 0–0', () => {
  assert.deepEqual(pageRange(0, 50, 0), { from: 0, to: 0 });
});

test('pageRange: first, middle and a short last page', () => {
  assert.deepEqual(pageRange(0, 50, 312), { from: 1, to: 50 });
  assert.deepEqual(pageRange(50, 50, 312), { from: 51, to: 100 });
  assert.deepEqual(pageRange(300, 50, 312), { from: 301, to: 312 });
});

test('pageRange: never runs past the total, even from a stale offset', () => {
  assert.deepEqual(pageRange(400, 50, 312), { from: 312, to: 312 });
  assert.deepEqual(pageRange(0, 50, 7), { from: 1, to: 7 });
});

/* ------------------------------------------------------------------ duration */

test('duration: no audio is a dash', () => {
  for (const v of [null, undefined, 0, -5]) assert.equal(duration(v), '—', String(v));
});

test('duration: m:ss under an hour, h:mm:ss from an hour', () => {
  assert.equal(duration(5), '0:05');
  assert.equal(duration(65), '1:05');
  assert.equal(duration(600), '10:00');
  assert.equal(duration(3600), '1:00:00');
  assert.equal(duration(3725), '1:02:05');
});

test('duration: rounds to the second before splitting, so 59.6 s is 1:00 and not 0:60', () => {
  assert.equal(duration(59.6), '1:00');
  assert.equal(duration(0.4), '0:00');
  assert.equal(duration(3599.5), '1:00:00');
});
