/* The review queue's diff. Pure text in, runs out — no DOM, so it is unit-tested.
   ==============================================================================
   A reviewer approving a curation proposal is deciding between two whole texts, so the panel
   shows them side by side rather than as one merged stream of struck-through and inserted
   words. This file produces the alignment; `HealthTab.tsx` renders it. */

export type DiffOp = '=' | '-' | '+';

export interface DiffRun {
  op: DiffOp;
  text: string;
}

export interface Diff {
  /** The identical prefix and suffix, trimmed off before any alignment work. */
  head: string;
  tail: string;
  /** The changed middle, as adjacent same-op characters already coalesced into runs. */
  runs: DiffRun[];
}

/* Granularity is chosen by COST, not just by length. The table below is n*m cells, so a long
   chunk diffed per character is what would actually hang the tab: 20k characters against 20k
   is 400M cells, about 1.6 GB. Step down — characters, then words, then lines — until the
   table fits the budget, and if even lines do not fit, say the whole block was replaced rather
   than trying. A coarser diff is still readable; a frozen browser is not, and this panel is the
   one place a reviewer cannot skip. */
const CELL_BUDGET = 4e6;                        // ~16 MB of Uint32, comfortably fast

const byChar = (x: string) => x.split('');
const byWord = (x: string) => x.match(/\s+|\S+/g) || [];
const byLine = (x: string) => x.match(/[^\n]*\n|[^\n]+/g) || [];

/** Character-level diff, trimmed to the changed middle so the LCS matrix stays small.
    A long middle falls back to word tokens — a 4000x4000 character matrix would hang the tab. */
export function diffParts(a: string, b: string): { head: string; tail: string; parts: DiffRun[] } {
  a = a || '';
  b = b || '';
  const min = Math.min(a.length, b.length);
  let s = 0;
  while (s < min && a[s] === b[s]) s++;
  let e = 0;
  while (e < min - s && a[a.length - 1 - e] === b[b.length - 1 - e]) e++;
  const head = a.slice(0, s);
  const tail = e ? a.slice(a.length - e) : '';
  const am = a.slice(s, a.length - e);
  const bm = b.slice(s, b.length - e);

  let split = am.length > 800 || bm.length > 800 ? byWord : byChar;
  if (split(am).length * split(bm).length > CELL_BUDGET) split = byWord;
  if (split(am).length * split(bm).length > CELL_BUDGET) split = byLine;

  const A = split(am);
  const B = split(bm);
  const n = A.length;
  const m = B.length;
  if (n * m > CELL_BUDGET) {
    // Beyond any useful alignment: report it honestly as a wholesale replacement.
    return { head, tail, parts: [{ op: '-', text: am }, { op: '+', text: bm }] };
  }

  const dp = new Uint32Array((n + 1) * (m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * (m + 1) + j] = A[i] === B[j]
        ? dp[(i + 1) * (m + 1) + j + 1] + 1
        : Math.max(dp[(i + 1) * (m + 1) + j], dp[i * (m + 1) + j + 1]);
    }
  }

  const parts: DiffRun[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { parts.push({ op: '=', text: A[i] }); i++; j++; }
    else if (dp[(i + 1) * (m + 1) + j] >= dp[i * (m + 1) + j + 1]) parts.push({ op: '-', text: A[i++] });
    else parts.push({ op: '+', text: B[j++] });
  }
  while (i < n) parts.push({ op: '-', text: A[i++] });
  while (j < m) parts.push({ op: '+', text: B[j++] });
  return { head, tail, parts };
}

/** `diffParts` with adjacent same-op pieces merged, which is what a renderer wants: one <del>
    around a deleted phrase rather than one per character. */
export function diff(a: string, b: string): Diff {
  const d = diffParts(a, b);
  const runs: DiffRun[] = [];
  for (const p of d.parts) {
    const last = runs[runs.length - 1];
    if (last && last.op === p.op) last.text += p.text;
    else runs.push({ op: p.op, text: p.text });
  }
  return { head: d.head, tail: d.tail, runs };
}

/** One side of the split view: the unchanged text plus only the runs that side shows.
    `'old'` keeps what would be REMOVED, `'new'` keeps what would be ADDED, so each pane reads
    as continuous prose and can be judged on its own. */
export function side(d: Diff, which: 'old' | 'new'): DiffRun[] {
  const keep: DiffOp = which === 'old' ? '-' : '+';
  const out: DiffRun[] = [];
  const push = (op: DiffOp, text: string) => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += text;
    else out.push({ op, text });
  };
  push('=', d.head);
  for (const r of d.runs) {
    if (r.op === '=') push('=', r.text);
    else if (r.op === keep) push(r.op, r.text);
  }
  push('=', d.tail);
  return out;
}
