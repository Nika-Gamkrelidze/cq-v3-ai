import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_BANDS, VERDICT_CLASS, bandBarClass, bandVar, bandsFrom, contradictedClaims,
  evidenceQuotes, normalizeVerdict, scoreBand, toStringList, verdictClass, verdictCounts,
  verdictLabelKey, type Claim, type FactCheck,
} from '../aiShapes.ts';

/* The verdict map is a compliance surface, not a styling detail.
   PARTIALLY_SUPPORTED once shared a class with NOT_IN_KB, which told a reviewer the knowledge
   base had nothing to say about a claim it in fact contradicted in part. These tests exist so
   that collapse cannot be reintroduced by someone tidying up a four-line object literal. */

test('PARTIALLY_SUPPORTED is distinct from NOT_IN_KB', () => {
  assert.notEqual(VERDICT_CLASS.PARTIALLY_SUPPORTED, VERDICT_CLASS.NOT_IN_KB);
  assert.equal(VERDICT_CLASS.PARTIALLY_SUPPORTED, 'partial');
  assert.equal(VERDICT_CLASS.NOT_IN_KB, 'notinkb');
});

test('all four verdicts map to four different classes', () => {
  const classes = Object.values(VERDICT_CLASS);
  assert.equal(classes.length, 4);
  assert.equal(new Set(classes).size, 4);
});

test('verdictClass covers the whole backend enum', () => {
  assert.equal(verdictClass('SUPPORTED'), 'supported');
  assert.equal(verdictClass('PARTIALLY_SUPPORTED'), 'partial');
  assert.equal(verdictClass('CONTRADICTED'), 'contradicted');
  assert.equal(verdictClass('NOT_IN_KB'), 'notinkb');
});

test('a loosely-written verdict still keeps its own class', () => {
  // Mirrors the backend's _norm_verdict: a model that answers "partially supported" must not
  // be filed under "the KB said nothing".
  assert.equal(verdictClass('partially supported'), 'partial');
  assert.equal(verdictClass('Partially-Supported'), 'partial');
  assert.equal(verdictClass('  contradicted  '), 'contradicted');
});

test('only a genuinely unknown verdict falls through to notinkb', () => {
  assert.equal(verdictClass('WHO KNOWS'), 'notinkb');
  assert.equal(verdictClass(null), 'notinkb');
  assert.equal(verdictClass(undefined), 'notinkb');
  assert.equal(verdictClass(42), 'notinkb');
  assert.equal(normalizeVerdict('nonsense'), 'NOT_IN_KB');
});

test('verdictLabelKey names a dictionary key per verdict', () => {
  assert.equal(verdictLabelKey('PARTIALLY_SUPPORTED'), 'fc.partial');
  assert.equal(verdictLabelKey('NOT_IN_KB'), 'fc.notinkb');
});

const claims: Claim[] = [
  { claim: 'Transfers are free', verdict: 'SUPPORTED' },
  { claim: 'Cards ship in 2 days', verdict: 'PARTIALLY_SUPPORTED' },
  { claim: 'There is no fee at all', verdict: 'CONTRADICTED' },
  { claim: 'The branch opens at six', verdict: 'NOT_IN_KB' },
];

test('verdictCounts counts each verdict on its own line', () => {
  assert.deepEqual(verdictCounts({ accuracy_score: 50, claims }), {
    supported: 1, partially_supported: 1, contradicted: 1, not_in_kb: 1, total: 4,
  });
});

test('the counter pills cannot contradict the section under them', () => {
  // A stored record whose `counts` disagree with its `claims` — older or hand-edited data.
  // The claims win, because the misinformation section below the pills is built from them:
  // reading the stored 0 here would print "0 contradicted" over a populated list.
  const fc: FactCheck = {
    accuracy_score: 75,
    counts: { supported: 3, partially_supported: 1, contradicted: 0, not_in_kb: 2, total: 6 },
    claims,
  };
  assert.equal(verdictCounts(fc).contradicted, contradictedClaims(fc).length);
  assert.equal(verdictCounts(fc).supported, 1);
  assert.equal(verdictCounts(fc).total, 4);
});

test('verdictCounts falls back to the stored counts when there are no claims to count', () => {
  const fc: FactCheck = {
    accuracy_score: 75,
    counts: { supported: 3, partially_supported: 1, contradicted: 0, not_in_kb: 2, total: 6 },
    claims: [],
  };
  assert.deepEqual(verdictCounts(fc), {
    supported: 3, partially_supported: 1, contradicted: 0, not_in_kb: 2, total: 6,
  });
});

test('verdictCounts on an empty record is four zeroes, not four NaNs', () => {
  assert.deepEqual(verdictCounts({ accuracy_score: null }), {
    supported: 0, partially_supported: 0, contradicted: 0, not_in_kb: 0, total: 0,
  });
  assert.equal(verdictCounts(null).total, 0);
});

test('the misinformation section is only the contradicted claims', () => {
  // Recomputed from `claims`, so a stored result written before the server sent its own
  // `contradicted` list still shows the section rather than silently omitting it.
  const found = contradictedClaims({ accuracy_score: 50, claims });
  assert.equal(found.length, 1);
  assert.equal(found[0].claim, 'There is no fee at all');
});

test('score bands: the default pair is the one an unconfigured workspace gets', () => {
  assert.equal(scoreBand(100), 'good');
  assert.equal(scoreBand(80), 'good');
  assert.equal(scoreBand(79.9), 'mid');
  assert.equal(scoreBand(50), 'mid');
  assert.equal(scoreBand(49), 'bad');
  assert.equal(scoreBand(0), 'bad');
  assert.equal(scoreBand(null), 'none');
  assert.equal(scoreBand(undefined), 'none');
});

test('a workspace that configured its own thresholds gets its own colours', () => {
  // The whole point of the second argument: on 60/85 a 82 is amber, not green. Before the
  // bands were a parameter this module answered 'good' for every workspace alike, and the
  // customers who noticed were exactly the ones who had bothered to configure them.
  const strict = { amber_from: 60, green_from: 85 };
  assert.equal(scoreBand(82, strict), 'mid');
  assert.equal(scoreBand(85, strict), 'good');
  assert.equal(scoreBand(59, strict), 'bad');
  assert.equal(scoreBand(82), 'good');            // …and the default is untouched by it
});

test('bandsFrom keeps a usable pair and discards the rest', () => {
  assert.deepEqual(bandsFrom({ amber_from: 60, green_from: 85, is_default: false }),
    { amber_from: 60, green_from: 85 });
  // An unordered, partial or absent pair falls back rather than painting every score one
  // colour — the same silent, harmless failure workbench.js takes on a failed fetch.
  assert.deepEqual(bandsFrom({ amber_from: 90, green_from: 20 }), DEFAULT_BANDS);
  assert.deepEqual(bandsFrom({ amber_from: 50 }), DEFAULT_BANDS);
  assert.deepEqual(bandsFrom({ amber_from: 'x', green_from: 'y' }), DEFAULT_BANDS);
  assert.deepEqual(bandsFrom(null), DEFAULT_BANDS);
  assert.deepEqual(bandsFrom(undefined), DEFAULT_BANDS);
});

test('an unscored dimension is grey, not red', () => {
  assert.equal(bandVar(scoreBand(null)), 'muted');
  assert.equal(bandBarClass(scoreBand(null)), '');
  assert.equal(bandVar(scoreBand(30)), 'alert');
  assert.equal(bandBarClass(scoreBand(30)), 'bad');
  assert.equal(bandVar(scoreBand(90)), 'ok');
  assert.equal(bandBarClass(scoreBand(90)), 'good');
});

test('evidenceQuotes accepts both stored shapes', () => {
  assert.deepEqual(evidenceQuotes({ key: 'a', name: 'A', weight: 50, score: 80, evidence: ['old string'] }),
    ['old string']);
  assert.deepEqual(evidenceQuotes({
    key: 'a', name: 'A', weight: 50, score: 80,
    evidence: [{ quote: 'placed  on\nthe timeline', segments: [3, 4], start: 12, end: 15 }],
  }), ['placed on the timeline']);
  assert.deepEqual(evidenceQuotes({ key: 'a', name: 'A', weight: 50, score: null }), []);
});

test('toStringList never renders [object Object]', () => {
  assert.deepEqual(toStringList(['a', '', null, 'b']), ['a', 'b']);
  assert.deepEqual(toStringList([{ point: 'be kind', why: 'policy' }]), ['be kind — policy']);
  assert.deepEqual(toStringList('one'), ['one']);
  assert.deepEqual(toStringList(null), []);
});
