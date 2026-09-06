/* The rubric's arithmetic, client side.
   ====================================
   Two audiences, one file: the rubric EDITOR (admin, workspace and account all ship the same
   dimensions/weights form) and the scorecard's manual-score preview.

   THE SERVER OWNS THE REAL NUMBER. `backend/app/services/scoring.py` computes the weighted
   total in code — deliberately, so a score someone is assessed on is auditable and does not
   depend on what a model felt like returning. Everything here is a PREVIEW of that number, and
   a preview that disagrees with the figure the page shows one request later is worse than no
   preview at all. So this mirrors `build_result` and `apply_manual_scores` step for step,
   including where they round.

   Which is why `round1` is not `Math.round(x * 10) / 10`. Python rounds half to EVEN and
   rounds the double's own decimal value; JS rounds half away from zero after a multiply that
   has already moved the value. They disagree on ordinary inputs, not just on contrived ties:
   Python gives `round(0.15, 1) == 0.1` (0.15 is really 0.1499…), while `Math.round(0.15*10)/10`
   is 0.2, because the multiply lands exactly on 1.5. One tenth of a point is enough to show a
   reviewer 79.9 next to the server's 80.0 — on either side of the band boundary. */

/** A dimension as the editor holds it: weights are the operator's own numbers on any scale,
    not yet percentages. */
export interface RubricDimension {
  key?: string;
  name: string;
  description?: string;
  guidance?: string;
  weight: number;
}

/** A dimension carrying a score, for the preview. Structurally a subset of the scorecard's. */
export interface WeightedScore {
  key?: string;
  weight: number;
  score: number | null;
}

/** Per-dimension preview output, named as the server names it. */
export interface PreviewDimension {
  key?: string;
  /** The dimension's share of the rubric, 0-100. */
  weight: number;
  score: number | null;
  /** What this dimension puts into the total. */
  contribution: number;
}

export interface Preview {
  dimensions: PreviewDimension[];
  weightedTotal: number;
  maxTotal: number;
}

/** How far the weights may drift from 100 and still save. Half a point, because the editor
    lets an operator type tenths and three dimensions cannot be split into exact thirds. */
export const WEIGHT_TOLERANCE = 0.5;

/* ------------------------------------------------------------------ rounding */

/** Round half to even at `digits` decimal places, over the double's exact decimal value —
    Python's `round()`, which is what produced every number the server sends.

    `toFixed(20)` is the double's own expansion rather than a re-rounded one, so the tie test
    below asks the same question CPython asks: is the remainder exactly one half, or merely
    close to it? */
export function roundHalfEven(value: number, digits = 0): number {
  if (!Number.isFinite(value)) return 0;
  const negative = value < 0;
  const s = Math.abs(value).toFixed(20);
  const dot = s.indexOf('.');
  if (dot < 0) return value;                                 // 1e21 and up: toFixed gives up, and
                                                             // no score or weight ever gets there

  const kept = s.slice(0, dot) + s.slice(dot + 1, dot + 1 + digits);
  const tail = s.slice(dot + 1 + digits);
  let n = Number(kept);
  const first = tail.charAt(0);
  const restNonZero = /[1-9]/.test(tail.slice(1));
  if (first > '5' || (first === '5' && restNonZero)) n += 1;
  else if (first === '5' && n % 2 === 1) n += 1;              // exact tie: settle on the even one
  const out = n / Math.pow(10, digits);
  return negative ? -out : out;
}

/** One decimal place, the precision every stored scoring figure carries. */
export function round1(value: number): number {
  return roundHalfEven(value, 1);
}

/* -------------------------------------------------------------- editor maths */

const weightOf = (d: { weight?: number | null }): number => {
  const w = Number(d?.weight);
  return Number.isFinite(w) && w > 0 ? w : 0;      // `normalize_dimensions` floors at zero too
};

/** The weights as typed, summed and shown next to the editor's ✓ / ✗ flag. */
export function weightTotal(dims: readonly { weight?: number | null }[]): number {
  return round1(dims.reduce((a, d) => a + weightOf(d), 0));
}

export function weightsBalanced(dims: readonly { weight?: number | null }[]): boolean {
  return Math.abs(weightTotal(dims) - 100) <= WEIGHT_TOLERANCE;
}

/** Rescale every weight proportionally so they total exactly 100.

    Integers, with the rounding drift pushed onto the last dimension — the operator asked for
    round numbers, and three dimensions that read 33 / 33 / 34 are honest about which one
    absorbed the third. An all-zero rubric is split evenly instead of divided by zero.
    Deliberately `Math.round`, not `round1`: this is the operator's own convenience button on
    numbers they are about to look at, not a figure that has to agree with the server. */
export function normalizeWeights<T extends { weight: number }>(dims: readonly T[]): T[] {
  const n = dims.length;
  if (!n) return [];
  const total = dims.reduce((a, d) => a + weightOf(d), 0);
  const out = dims.map(d => ({
    ...d,
    weight: total <= 0 ? Math.round(100 / n) : Math.round((weightOf(d) / total) * 100),
  }));
  out[n - 1].weight += 100 - out.reduce((a, d) => a + d.weight, 0);
  return out;
}

/** Why a rubric cannot be saved. The caller maps these to `sc.needone` / `sc.needname` /
    `sc.mustbe100` — the message is the page's to translate, not this file's to invent. */
export type RubricProblem = 'no-dimensions' | 'unnamed-dimension' | 'weights';

export interface RubricValidation {
  ok: boolean;
  problem?: RubricProblem;
  /** The weight total, so `sc.mustbe100`'s `{total}` can be filled in without recomputing it. */
  total: number;
}

/** The save button's guard, in the legacy order: a rubric with no dimensions, then a dimension
    with no name, then the weights. One problem at a time — an operator fixes the first one and
    presses save again, and a wall of three messages does not help them do it faster. */
export function validateRubric(dims: readonly { name?: string; weight?: number | null }[]): RubricValidation {
  const total = weightTotal(dims);
  if (!dims.length) return { ok: false, problem: 'no-dimensions', total };
  if (dims.some(d => !String(d.name ?? '').trim())) return { ok: false, problem: 'unnamed-dimension', total };
  if (Math.abs(total - 100) > WEIGHT_TOLERANCE) return { ok: false, problem: 'weights', total };
  return { ok: true, total };
}

/* ------------------------------------------------------------------- preview */

/** A model score as the server stores it: rounded to an integer, clamped, or null when the
    dimension went unscored. An unscored dimension is NOT a zero — it is left out of the total
    entirely, so a rubric the model could not judge does not read as a failed call. */
export function normalizeScore(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.max(0, Math.min(100, roundHalfEven(n)));
}

/** The preview of `build_result`: raw rubric weights in, percentages and a total out.

    `total_weight` falls back to the dimension COUNT when every weight is zero, and each weight
    then counts as 1 — an unweighted rubric scores as an equal split rather than as zero out
    of zero. */
export function previewFromRubric(dims: readonly WeightedScore[]): Preview {
  const anyWeight = dims.some(d => weightOf(d) > 0);
  const totalWeight = dims.reduce((a, d) => a + weightOf(d), 0) || dims.length;
  let weightedTotal = 0;
  const out = dims.map(d => {
    const w = anyWeight ? weightOf(d) : 1;
    const score = normalizeScore(d.score);
    if (score !== null) weightedTotal += (score * w) / totalWeight;
    return {
      key: d.key,
      weight: totalWeight ? round1((100 * w) / totalWeight) : 0,
      score,
      contribution: totalWeight ? round1(((score ?? 0) * w) / totalWeight) : 0,
    };
  });
  return { dimensions: out, weightedTotal: round1(weightedTotal), maxTotal: 100 };
}

/** The preview of `apply_manual_scores`: a reviewer's own numbers over a scorecard whose
    weights are ALREADY percentages.

    Not the same maths as `previewFromRubric`, and not foldable into it. Here a zero weight
    means a dimension the rubric genuinely gives no share of the total — unless every weight is
    zero, in which case the fallback split applies and each counts as 1, exactly as the server
    decides it (`total_weight == len(dims)` is how it recognises that case). */
export function previewFromScorecard(
  dims: readonly WeightedScore[],
  edits: Readonly<Record<string, number | null>> = {},
): Preview {
  const totalWeight = dims.reduce((a, d) => a + weightOf(d), 0) || dims.length || 1;
  const equalSplit = totalWeight === dims.length;
  let weightedTotal = 0;
  const out = dims.map(d => {
    const key = d.key === undefined ? undefined : String(d.key);
    const score = key !== undefined && key in edits ? normalizeScore(edits[key]) : normalizeScore(d.score);
    const w = weightOf(d) || (equalSplit ? 1 : 0);
    if (score !== null) weightedTotal += (score * w) / totalWeight;
    return { key: d.key, weight: d.weight, score, contribution: round1(((score ?? 0) * w) / totalWeight) };
  });
  return { dimensions: out, weightedTotal: round1(weightedTotal), maxTotal: 100 };
}
