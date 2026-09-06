import test from 'node:test';
import assert from 'node:assert/strict';
import {
  WEIGHT_TOLERANCE, normalizeScore, normalizeWeights, previewFromRubric, previewFromScorecard,
  round1, roundHalfEven, validateRubric, weightTotal, weightsBalanced,
} from '../rubricMath.ts';

/* The expected numbers below are not hand-computed: they are what
   backend/app/services/scoring.py's build_result and apply_manual_scores actually return for
   these inputs. This file is the contract between the client-side preview and the figure the
   server sends back one request later. */

test('round1 follows Python round(), not Math.round', () => {
  // 0.15 is really 0.1499…, so the correct answer is 0.1. Math.round(0.15 * 10) / 10 is 0.2,
  // because the multiply lands exactly on 1.5 — the divergence this function exists to avoid.
  assert.equal(round1(0.15), 0.1);
  assert.equal(round1(0.35), 0.3);
  assert.equal(round1(0.05), 0.1);          // 0.05 is really 0.0500…0277, so it rounds up
  assert.equal(round1(62.35), 62.4);        // and 62.35 is really 62.3500…14
});

test('roundHalfEven settles an exact tie on the even digit', () => {
  assert.equal(roundHalfEven(2.5), 2);
  assert.equal(roundHalfEven(3.5), 4);
  assert.equal(roundHalfEven(-2.5), -2);
  assert.equal(roundHalfEven(0.25, 1), 0.2);
  assert.equal(roundHalfEven(0.75, 1), 0.8);
});

test('normalizeScore: integer, clamped, and null is not zero', () => {
  assert.equal(normalizeScore(83.4), 83);
  assert.equal(normalizeScore(200), 100);
  assert.equal(normalizeScore(-5), 0);
  assert.equal(normalizeScore(null), null);
  assert.equal(normalizeScore(''), null);
  assert.equal(normalizeScore('not a number'), null);
});

test('preview matches build_result: ordinary weights', () => {
  const p = previewFromRubric([{ key: 'a', weight: 30, score: 80 }, { key: 'b', weight: 70, score: 55 }]);
  assert.deepEqual(p.dimensions.map(d => [d.weight, d.contribution]), [[30, 24], [70, 38.5]]);
  assert.equal(p.weightedTotal, 62.5);
});

test('preview matches build_result: weights that do not divide evenly', () => {
  const p = previewFromRubric([{ key: 'a', weight: 1, score: 33 }, { key: 'b', weight: 2, score: 77 }]);
  assert.deepEqual(p.dimensions.map(d => [d.weight, d.contribution]), [[33.3, 11], [66.7, 51.3]]);
  assert.equal(p.weightedTotal, 62.3);
});

test('preview matches build_result: an all-zero rubric is an equal split, not a divide by zero', () => {
  const p = previewFromRubric([{ key: 'a', weight: 0, score: 100 }, { key: 'b', weight: 0, score: 0 }]);
  assert.deepEqual(p.dimensions.map(d => [d.weight, d.contribution]), [[50, 50], [50, 0]]);
  assert.equal(p.weightedTotal, 50);
});

test('preview matches build_result: an unscored dimension is left out of the total', () => {
  const p = previewFromRubric([{ key: 'a', weight: 50, score: null }, { key: 'b', weight: 50, score: 80 }]);
  assert.deepEqual(p.dimensions.map(d => [d.score, d.contribution]), [[null, 0], [80, 40]]);
  assert.equal(p.weightedTotal, 40);        // 40, not 40 out of a halved rubric and not 80
});

test('preview matches build_result: three equal weights', () => {
  const p = previewFromRubric([
    { key: 'a', weight: 3, score: 61 }, { key: 'b', weight: 3, score: 62 }, { key: 'c', weight: 3, score: 63 },
  ]);
  assert.deepEqual(p.dimensions.map(d => [d.weight, d.contribution]), [[33.3, 20.3], [33.3, 20.7], [33.3, 21]]);
  assert.equal(p.weightedTotal, 62);
});

test("preview matches apply_manual_scores: a reviewer's own number over the model's", () => {
  const dims = [
    { key: 'a', weight: 33.3, score: 70 },
    { key: 'b', weight: 33.3, score: 50 },
    { key: 'c', weight: 33.4, score: 90 },
  ];
  const p = previewFromScorecard(dims, { b: 80 });
  assert.deepEqual(p.dimensions.map(d => [d.score, d.contribution]), [[70, 23.3], [80, 26.6], [90, 30.1]]);
  assert.equal(p.weightedTotal, 80);
});

test('preview matches apply_manual_scores: stored weights of zero fall back to an equal split', () => {
  const p = previewFromScorecard([{ key: 'a', weight: 0, score: 70 }, { key: 'b', weight: 0, score: 50 }]);
  assert.deepEqual(p.dimensions.map(d => d.contribution), [35, 25]);
  assert.equal(p.weightedTotal, 60);
});

test('weightTotal and the save-time tolerance', () => {
  assert.equal(weightTotal([{ weight: 33 }, { weight: 33 }, { weight: 34 }]), 100);
  assert.equal(weightTotal([]), 0);
  assert.equal(weightTotal([{ weight: -20 }, { weight: 60 }]), 60);   // a negative weight is zero
  assert.equal(WEIGHT_TOLERANCE, 0.5);
  assert.equal(weightsBalanced([{ weight: 99.6 }]), true);
  assert.equal(weightsBalanced([{ weight: 99.4 }]), false);
});

test('normalizeWeights rescales to exactly 100, drift on the last dimension', () => {
  assert.deepEqual(normalizeWeights([{ weight: 1 }, { weight: 1 }, { weight: 1 }]).map(d => d.weight),
    [33, 33, 34]);
  assert.deepEqual(normalizeWeights([{ weight: 20 }, { weight: 60 }]).map(d => d.weight), [25, 75]);
  assert.deepEqual(normalizeWeights([{ weight: 0 }, { weight: 0 }, { weight: 0 }]).map(d => d.weight),
    [33, 33, 34]);
  assert.deepEqual(normalizeWeights([]), []);
});

test('normalizeWeights keeps the rest of the dimension', () => {
  const [first] = normalizeWeights([{ key: 'tone', name: 'Tone', weight: 5 }]);
  assert.equal(first.key, 'tone');
  assert.equal(first.name, 'Tone');
  assert.equal(first.weight, 100);
});

test('validateRubric reports one problem at a time, in the order the operator can fix them', () => {
  assert.deepEqual(validateRubric([]), { ok: false, problem: 'no-dimensions', total: 0 });
  assert.deepEqual(validateRubric([{ name: '', weight: 100 }]),
    { ok: false, problem: 'unnamed-dimension', total: 100 });
  assert.deepEqual(validateRubric([{ name: '   ', weight: 100 }]),
    { ok: false, problem: 'unnamed-dimension', total: 100 });
  assert.deepEqual(validateRubric([{ name: 'Tone', weight: 90 }]),
    { ok: false, problem: 'weights', total: 90 });
  assert.deepEqual(validateRubric([{ name: 'Tone', weight: 60 }, { name: 'Accuracy', weight: 40 }]),
    { ok: true, total: 100 });
});
