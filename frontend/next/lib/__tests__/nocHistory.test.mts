import test from 'node:test';
import assert from 'node:assert/strict';
import {
  CELL_MS, bucketSamples, dayBounds, daysStrip, incidents, localDay, momentFor, nearestAtOrBefore, parseBad,
  shiftDay, toPoints,
} from '../../app/noc/historyLib.ts';
import type { HistorySample, Status } from '../../app/noc/types.ts';

// A zone with daylight saving, so the "a day is not always 24 h" cases are real.
process.env.TZ = 'Europe/Berlin';

const at = (y: number, m: number, d: number, h = 0, min = 0, s = 0) => new Date(y, m - 1, d, h, min, s).getTime();
const sample = (ms: number, status: Status, bad: string[] = []): HistorySample => ({
  ts: new Date(ms).toISOString(), status, crit: status === 'crit' ? 1 : 0, warn: status === 'warn' ? 1 : 0, bad,
});

test('dayBounds: local midnight to the next local midnight', () => {
  const b = dayBounds('2026-11-20')!;
  assert.equal(b.from, at(2026, 11, 20));
  assert.equal(b.to, at(2026, 11, 21));
  assert.equal((b.to - b.from) / 3_600_000, 24);
});

test('dayBounds: a day with a clock change is 23 or 25 hours long, not 24', () => {
  const spring = dayBounds('2026-03-29')!;
  const autumn = dayBounds('2026-10-25')!;
  assert.equal((spring.to - spring.from) / 3_600_000, 23);
  assert.equal((autumn.to - autumn.from) / 3_600_000, 25);
  assert.equal(bucketSamples([], spring.from, spring.to).length, 23 * 12);
  assert.equal(bucketSamples([], autumn.from, autumn.to).length, 25 * 12);
});

test('dayBounds: not a date -> null', () => {
  assert.equal(dayBounds('yesterday'), null);
  assert.equal(dayBounds('2026-1-2'), null);
});

test('shiftDay and localDay: calendar arithmetic across month, year and clock change', () => {
  assert.equal(shiftDay('2026-03-01', -1), '2026-02-28');
  assert.equal(shiftDay('2026-12-31', 1), '2027-01-01');
  assert.equal(shiftDay('2026-03-28', 1), '2026-03-29');
  assert.equal(shiftDay('2026-03-29', 1), '2026-03-30');
  assert.equal(localDay(new Date(2026, 10, 5, 23, 59)), '2026-11-05');
});

test('bucketSamples: a normal day has 288 cells; each takes the worst status inside it', () => {
  const { from, to } = dayBounds('2026-11-20')!;
  const cells = bucketSamples([
    sample(from + 60_000, 'ok'),
    sample(from + 120_000, 'crit', ['crit:check:disk:/']),
    sample(from + 180_000, 'warn', ['warn:ai:Anthropic main']),
    sample(from + CELL_MS + 1000, 'ok'),
    sample(from + 3 * CELL_MS, 'warn', ['warn:probe:api.anthropic.com']),
  ], from, to);
  assert.equal(cells.length, 288);
  assert.equal(cells[0].status, 'crit');
  assert.equal(cells[0].n, 3);
  assert.deepEqual(cells[0].bad, ['crit:check:disk:/', 'warn:ai:Anthropic main']);
  assert.equal(cells[1].status, 'ok');
  assert.equal(cells[2].status, null);
  assert.equal(cells[3].status, 'warn');
});

test('bucketSamples: ignores samples outside the day; the last instant belongs to the next day', () => {
  const { from, to } = dayBounds('2026-11-20')!;
  const cells = bucketSamples([sample(from - 1000, 'crit'), sample(to, 'crit'), sample(to - 1000, 'ok')], from, to);
  assert.equal(cells.filter(c => c.status !== null).length, 1);
  assert.equal(cells[287].status, 'ok');
});

test('bucketSamples: unknown never outranks ok', () => {
  const { from, to } = dayBounds('2026-11-20')!;
  const cells = bucketSamples([sample(from, 'unknown'), sample(from + 1000, 'ok')], from, to);
  assert.equal(cells[0].status, 'ok');
});

test('parseBad: only the first two colons separate; the name keeps its own', () => {
  assert.deepEqual(parseBad('crit:check:disk:/'), { status: 'crit', kind: 'check', name: 'disk:/' });
  assert.deepEqual(parseBad('warn:ai:Anthropic main'), { status: 'warn', kind: 'ai', name: 'Anthropic main' });
  assert.deepEqual(parseBad('garbage'), { status: 'unknown', kind: '', name: 'garbage' });
});

test('incidents: consecutive non-ok samples make one run; an ok sample ends it; newest first', () => {
  const t0 = at(2026, 11, 20, 10);
  const list = incidents([
    sample(t0, 'ok'),
    sample(t0 + 60_000, 'warn', ['warn:ai:Anthropic main']),
    sample(t0 + 120_000, 'crit', ['crit:check:disk:/', 'warn:ai:Anthropic main']),
    sample(t0 + 180_000, 'ok'),
    sample(t0 + 240_000, 'warn', ['warn:worker:curation_pass']),
  ]);
  assert.equal(list.length, 2);
  assert.equal(list[0].start, t0 + 240_000);
  assert.equal(list[0].worst, 'warn');
  assert.equal(list[1].start, t0 + 60_000);
  assert.equal(list[1].end, t0 + 120_000);
  assert.equal(list[1].worst, 'crit');
  assert.deepEqual(list[1].names.map(n => `${n.status}:${n.kind}:${n.name}`), ['crit:check:disk:/', 'warn:ai:Anthropic main']);
});

test('incidents: a name that got worse during the run keeps its worst status', () => {
  const t0 = at(2026, 11, 20, 10);
  const [i] = incidents([sample(t0, 'warn', ['warn:ai:A']), sample(t0 + 60_000, 'crit', ['crit:ai:A'])]);
  assert.deepEqual(i.names, [{ status: 'crit', kind: 'ai', name: 'A' }]);
});

test('incidents: a silence longer than 3 sample intervals splits a run', () => {
  const t0 = at(2026, 11, 20, 10);
  const split = incidents([sample(t0, 'warn'), sample(t0 + 4 * 60_000, 'warn')]);
  const joined = incidents([sample(t0, 'warn'), sample(t0 + 3 * 60_000, 'warn')]);
  assert.equal(split.length, 2);
  assert.equal(joined.length, 1);
});

test('incidents: nothing wrong, nothing listed', () => {
  assert.deepEqual(incidents([]), []);
  assert.deepEqual(incidents([sample(1, 'ok'), sample(2, 'unknown')]), []);
});

test('nearestAtOrBefore: newest sample not after the moment, else -1', () => {
  const t0 = at(2026, 11, 20, 10);
  const s = [sample(t0, 'ok'), sample(t0 + 60_000, 'ok'), sample(t0 + 120_000, 'ok')];
  assert.equal(nearestAtOrBefore(s, t0 - 1), -1);
  assert.equal(nearestAtOrBefore(s, t0), 0);
  assert.equal(nearestAtOrBefore(s, t0 + 90_000), 1);
  assert.equal(nearestAtOrBefore(s, t0 + 9e9), 2);
  assert.equal(nearestAtOrBefore([], t0), -1);
});

test('momentFor: the newest sample inside the cell, else the cell\'s last instant, never past the limit', () => {
  const { from, to } = dayBounds('2026-11-20')!;
  const s = [sample(from + 60_000, 'ok'), sample(from + 4 * 60_000, 'warn')];
  const cells = bucketSamples(s, from, to);
  assert.equal(momentFor(s, cells[0]), from + 4 * 60_000);
  // A sample from an earlier cell does not stand in for an empty one.
  assert.equal(momentFor(s, cells[2]), cells[2].end - 1);
  assert.equal(momentFor(s, cells[2], from + 11 * 60_000), from + 11 * 60_000);
});

test('daysStrip: 31 slots ending today, with a null row where nothing was stored', () => {
  const rows = [
    { day: '2026-11-18', status: 'warn' as Status, samples: 1380, crit: 0, warn: 14 },
    { day: '2026-11-20', status: 'ok' as Status, samples: 600, crit: 0, warn: 0 },
  ];
  const strip = daysStrip(rows, '2026-11-20');
  assert.equal(strip.length, 31);
  assert.equal(strip[30].day, '2026-11-20');
  assert.equal(strip[30].row?.status, 'ok');
  assert.equal(strip[29].row, null);
  assert.equal(strip[28].row?.samples, 1380);
  assert.equal(strip[0].day, '2026-10-21');
});

test('toPoints: parallel arrays become points; missing or non-finite values are gaps, never zeros', () => {
  assert.deepEqual(toPoints([1, 2, 3, 4], [5, null, Number.NaN]), [
    { t: 1, v: 5 }, { t: 2, v: null }, { t: 3, v: null }, { t: 4, v: null },
  ]);
  assert.deepEqual(toPoints([1], undefined), [{ t: 1, v: null }]);
});
