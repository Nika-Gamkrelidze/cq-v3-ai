/* What the AI layers return, and the derivations every renderer of them needs.
   ===========================================================================
   Types and pure functions only — the four result surfaces (analysis, scorecard, fact-check,
   sentiment) are built on top of this, and they are built more than once: the workspace
   portal, the operator playground and the account page all render the same payloads and used
   to each own a copy of the logic. brand.js already unified the markup; this unifies the
   part of it that can be wrong rather than merely ugly.

   The shapes are defensive on purpose. `analysis`, `kb_check` and `scoring` are jsonb columns
   filled by a model over several schema generations, so a field is `unknown` here whenever the
   database can still hold an older form of it, and the narrowing lives in one function instead
   of at every call site. */

/* ------------------------------------------------------------------ shared */

/** Whatever the model returned, as a clean list of non-empty strings (brand.js `_arr`).

    An object entry is joined rather than stringified because `String({})` is
    "[object Object]" on the page — a model that answers `[{point: "..."}]` instead of
    `["..."]` should degrade to readable text, not to a bug report. */
export function toStringList(value: unknown): string[] {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) {
    return value
      .filter(x => x !== null && x !== undefined)
      .map(x => (typeof x === 'object' ? joinValues(x as Record<string, unknown>) : String(x)))
      .filter(s => s.trim() !== '');
  }
  if (typeof value === 'object') {
    return Object.values(value as Record<string, unknown>).filter(Boolean).map(String);
  }
  const s = String(value).trim();
  return s ? [s] : [];
}

function joinValues(obj: Record<string, unknown>): string {
  return Object.values(obj).filter(Boolean).join(' — ');
}

/* ------------------------------------------------------------- score bands */

/** Where a 0-100 score sits. `none` is an UNSCORED dimension — grey, never red: the model
    declining to score something is not the same as scoring it zero. */
export type ScoreBand = 'good' | 'mid' | 'bad' | 'none';

/** The two boundaries between red, amber and green, as `GET /scoring/bands` returns them. */
export interface ScoreBands {
  amber_from: number;
  green_from: number;
}

/* The thresholds belong to the WORKSPACE, not to the app. They are a row in `score_bands` per
   owner, served by `GET /scoring/bands`, with a customer-facing card behind them ("Score
   colours" in tenant.html). 50/80 is only what an unconfigured workspace gets — the same
   fallback the backend keeps in `scoring_store.DEFAULT_BANDS` — which is why it is a default
   ARGUMENT below and not a constant: a workspace that set 60/85 has to see 60/85 on the
   scorecard number, on the bar under it and on the fact-check accuracy figure. workbench.js
   threads its one fetched `BANDS` object through all three for exactly that reason, and
   hardcoding them here would take that back for every renderer built on this module.

   Not to be confused with the backend's GOOD_MIN/MID_MIN (70/40) in `services/scoring.py`.
   Those stamp a `level` onto each timeline span as it is built, but workbench.js re-derives
   the level from the score against the workspace's bands whenever the span carries a score
   (`sp.score == null ? lvl(sp.level) : scoreLevel(num(sp.score))`), precisely so a dimension
   cannot be red in the card and olive on the timeline. The server's pair only survives for a
   span with no score of its own. */
export const DEFAULT_BANDS: ScoreBands = { amber_from: 50, green_from: 80 };

/* One place, three former call sites. brand.js had the comparison written out in `band` (which
   picked a CSS colour variable), in `barcls` (which picked a bar class) and again in
   `factcheckHTML`'s accuracy figure — so a change to "what counts as good" had to be made
   three times or the number and the bar under it disagreed. */
export function scoreBand(value: number | null | undefined, bands: ScoreBands = DEFAULT_BANDS): ScoreBand {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'none';
  const v = Number(value);
  return v >= bands.green_from ? 'good' : v >= bands.amber_from ? 'mid' : 'bad';
}

/** A `GET /scoring/bands` body → a usable pair, or the fallback.

    The guard is workbench.js's: an unordered or non-numeric pair is discarded rather than
    rendered, because two thresholds the wrong way round would paint every score one colour.
    Failing back to 50/80 is silent and harmless — it is what the workspace would have had if
    the request had never been made. */
export function bandsFrom(payload: unknown): ScoreBands {
  const p = (payload ?? {}) as Partial<Record<keyof ScoreBands, unknown>>;
  const amber = Number(p.amber_from);
  const green = Number(p.green_from);
  if (!Number.isFinite(amber) || !Number.isFinite(green) || amber >= green) return DEFAULT_BANDS;
  return { amber_from: amber, green_from: green };
}

/** The band as a CSS custom-property NAME, for `color: var(--<name>)`. */
export function bandVar(band: ScoreBand): 'ok' | 'pending' | 'alert' | 'muted' {
  return band === 'good' ? 'ok' : band === 'mid' ? 'pending' : band === 'bad' ? 'alert' : 'muted';
}

/** The band as the modifier class `.sc-bar` already carries in brand.css. An unscored
    dimension gets no class at all, which leaves the bar its neutral fill. */
export function bandBarClass(band: ScoreBand): '' | 'good' | 'mid' | 'bad' {
  return band === 'none' ? '' : band;
}

/* ---------------------------------------------------------------- analysis */

export interface KbHit {
  title?: string | null;
  doc_type?: string | null;
  score?: number | null;
}

export interface Analysis {
  language?: string | null;
  sentiment?: string | null;
  quality_score?: number | null;
  topics?: unknown;
  summary?: string | null;
  key_points?: unknown;
  action_items?: unknown;
}

export interface AnalyzeResult {
  analysis?: Analysis | null;
  transcript?: string | null;
  language?: string | null;
  kb_used?: KbHit[] | null;
  kb_check?: FactCheck | null;
  scoring?: Scorecard | null;
  semantic?: Sentiment | null;
}

/** The analysis card's fields, with the list-shaped ones already normalised.

    `language` falls back to the job's own detected language: the analysis tool may omit it,
    and the STT layer always knows. */
export interface AnalysisView {
  language: string;
  sentiment: string;
  qualityScore: number | null;
  topics: string[];
  summary: string;
  keyPoints: string[];
  actionItems: string[];
  kbUsed: KbHit[];
}

export function analysisView(result: AnalyzeResult | null | undefined): AnalysisView {
  const a = result?.analysis || {};
  return {
    language: str(a.language) || str(result?.language),
    sentiment: str(a.sentiment),
    qualityScore: num(a.quality_score),
    topics: toStringList(a.topics),
    summary: str(a.summary),
    keyPoints: toStringList(a.key_points),
    actionItems: toStringList(a.action_items),
    kbUsed: result && Array.isArray(result.kb_used) ? result.kb_used.filter(Boolean) : [],
  };
}

/* --------------------------------------------------------------- scorecard */

/** One evidence quote. The workbench's scoring returns objects so a finding can be placed on
    the timeline; jobs scored before that returned plain strings, and BOTH are in the database
    — every reader has to accept either. */
export interface EvidenceObject {
  quote?: string | null;
  segments?: number[] | null;
  start?: number | null;
  end?: number | null;
}
export type Evidence = string | EvidenceObject;

export interface ScoreDimension {
  key: string;
  name: string;
  weight: number;
  score: number | null;
  max?: number;
  contribution?: number;
  rationale?: string | null;
  evidence?: Evidence[] | Evidence | null;
  spans?: unknown[];
  /** Set by `apply_manual_scores` when a reviewer overrode the model's number. */
  edited?: boolean;
  ai_score?: number | null;
}

export interface Scorecard {
  config_version?: number | string | null;
  operator_speaker?: string | null;
  dimensions: ScoreDimension[];
  weighted_total: number | null;
  max_total?: number | null;
  lanes?: unknown[];
  manually_edited?: boolean;
  edited_by?: string | null;
}

/** A scorecard worth rendering. An empty `dimensions` is not an empty card, it is no card:
    the rubric never ran, and a heading over nothing reads as a failure of the rubric. */
export function hasScorecard(sc: Scorecard | null | undefined): sc is Scorecard {
  return !!sc && Array.isArray(sc.dimensions) && sc.dimensions.length > 0;
}

/** A dimension's evidence as plain quotes, from either stored shape.

    Mirrors the backend's `evidence_text`. `toStringList` alone cannot do this: it would join
    an evidence object's fields and print the segment indices next to the quote. */
export function evidenceQuotes(dim: ScoreDimension | Evidence[] | Evidence | null | undefined): string[] {
  const raw = dim && typeof dim === 'object' && 'evidence' in dim
    ? (dim as ScoreDimension).evidence
    : (dim as Evidence[] | Evidence | null | undefined);
  const items: Evidence[] = Array.isArray(raw) ? raw : raw === null || raw === undefined ? [] : [raw];
  return items
    .map(e => (e !== null && typeof e === 'object' ? str(e.quote) : str(e)))
    .map(q => q.split(/\s+/).filter(Boolean).join(' '))   // the server collapses whitespace; match it
    .filter(Boolean);
}

/** The scorecard's headline, out of `max_total` (100 unless a rubric says otherwise). */
export function totalOutOf(sc: Scorecard): number {
  return num(sc.max_total) ?? 100;
}

/* -------------------------------------------------------------- fact-check */

export type Verdict = 'SUPPORTED' | 'PARTIALLY_SUPPORTED' | 'CONTRADICTED' | 'NOT_IN_KB';
export type VerdictClass = 'supported' | 'partial' | 'contradicted' | 'notinkb';

/* PARTIALLY_SUPPORTED is a REAL verdict, not an unknown one: the substance of the claim is
   right but a detail is wrong. It once shared a class with NOT_IN_KB, which told a reviewer
   the knowledge base had nothing to say about a claim it in fact contradicted in part — a
   correctness bug in a compliance feature, and the reason this map is a named constant with a
   test rather than an object literal inside a renderer. */
export const VERDICT_CLASS: Record<Verdict, VerdictClass> = {
  SUPPORTED: 'supported',
  PARTIALLY_SUPPORTED: 'partial',
  CONTRADICTED: 'contradicted',
  NOT_IN_KB: 'notinkb',
};

/** Normalise a stored verdict the way the backend's `_norm_verdict` does, so
    `partially supported` and `partially-supported` still land on their own verdict rather
    than falling through to the unknown one. */
export function normalizeVerdict(value: unknown): Verdict {
  const v = String(value ?? '').trim().toUpperCase().replace(/[\s-]+/g, '_');
  return (v in VERDICT_CLASS ? v : 'NOT_IN_KB') as Verdict;
}

export function verdictClass(value: unknown): VerdictClass {
  return VERDICT_CLASS[normalizeVerdict(value)];
}

/** The dictionary key for a verdict's label — `fc.supported`, `fc.partial`, … */
export function verdictLabelKey(value: unknown): string {
  return `fc.${verdictClass(value)}`;
}

export interface ClaimEvidence {
  title?: string | null;
  doc_type?: string | null;
  snippet?: string | null;
  score?: number | null;
}

export interface Claim {
  claim: string;
  verdict: Verdict;
  rationale?: string | null;
  speaker?: string | null;
  category?: string | null;
  /** 0..1 from the model, or null. */
  confidence?: number | null;
  evidence?: ClaimEvidence | null;
  segments?: number[];
}

export interface VerdictCounts {
  supported: number;
  partially_supported: number;
  contradicted: number;
  not_in_kb: number;
  total: number;
}

/** Verdict → its counter, matching the backend's `_KEY`. Written out rather than derived by
    lower-casing the verdict, so the same mistake cannot be made twice in one file. */
const VERDICT_COUNT_KEY: Record<Verdict, keyof VerdictCounts> = {
  SUPPORTED: 'supported',
  PARTIALLY_SUPPORTED: 'partially_supported',
  CONTRADICTED: 'contradicted',
  NOT_IN_KB: 'not_in_kb',
};

export interface FactCheck {
  /** Share of VERIFIABLE claims that were right, a partial counting half; null when nothing
      in the call could be checked at all. NOT_IN_KB is out of the denominator server-side. */
  accuracy_score: number | null;
  counts?: Partial<VerdictCounts> | null;
  claims?: Claim[] | null;
  contradicted?: Claim[] | null;
  spans?: unknown[];
  segments_available?: boolean;
}

export function factCheckClaims(fc: FactCheck | null | undefined): Claim[] {
  return fc && Array.isArray(fc.claims) ? fc.claims.filter(Boolean) : [];
}

/** The claims that need a reviewer's eyes, recomputed from `claims` rather than read from the
    server's own `contradicted` list — an older stored result has the claims but not the list,
    and a card that silently omits the misinformation section is the worst possible failure
    mode for this feature. */
export function contradictedClaims(fc: FactCheck | null | undefined): Claim[] {
  return factCheckClaims(fc).filter(c => normalizeVerdict(c.verdict) === 'CONTRADICTED');
}

/** The four counters — counted from `claims`, and read from the stored `counts` only when
    there are no claims to count.

    Same policy as `contradictedClaims` above, and for the same reason: the counter pills sit
    directly above the misinformation section, so on a record whose `counts` and `claims`
    disagree — the older or hand-edited data that function is written for — trusting the stored
    number here would print "0 contradicted" over a populated list. The server derives `counts`
    from the very claims it sends (`factcheck.py` counts `out_claims`), so on a healthy record
    recomputing changes nothing; it only ever removes a contradiction. The stored counts still
    matter for a payload that carries the summary WITHOUT the claims, which is why they are the
    fallback rather than deleted. */
export function verdictCounts(fc: FactCheck | null | undefined): VerdictCounts {
  const stored = fc?.counts;
  const claims = factCheckClaims(fc);
  if (!claims.length && stored && typeof stored === 'object') {
    return {
      supported: num(stored.supported) ?? 0,
      partially_supported: num(stored.partially_supported) ?? 0,
      contradicted: num(stored.contradicted) ?? 0,
      not_in_kb: num(stored.not_in_kb) ?? 0,
      total: num(stored.total) ?? 0,
    };
  }
  const out: VerdictCounts = {
    supported: 0, partially_supported: 0, contradicted: 0, not_in_kb: 0, total: claims.length,
  };
  for (const c of claims) out[VERDICT_COUNT_KEY[normalizeVerdict(c.verdict)]] += 1;
  return out;
}

/* --------------------------------------------------------------- sentiment */

export type Polarity = 'positive' | 'neutral' | 'negative';
export type Agreement = 'agree' | 'partial' | 'conflict' | 'text_only' | 'prosody_only' | 'unknown';

/** One half of the sentiment read: the words, or the voice. */
export interface SentimentHalf {
  label?: string | null;
  polarity?: Polarity | string | null;
  /** 0..1, prosody only — the text judge has no meter. */
  arousal?: number | null;
  valence?: number | null;
  source?: string | null;
}

export interface Sentiment {
  overall: Polarity | null;
  agreement: Agreement;
  text: SentimentHalf | null;
  prosody: SentimentHalf | null;
}

/** The two halves are NOT averaged into one number. When they disagree — positive words in a
    negative voice — that disagreement IS the finding a reviewer wants, and a mean would hide
    exactly the call they should listen to first. */
export function isConflict(sn: Sentiment | null | undefined): boolean {
  return sn?.agreement === 'conflict';
}

export function hasSentiment(sn: Sentiment | null | undefined): sn is Sentiment {
  return !!sn && !!(sn.text || sn.prosody);
}

/** Polarity as the pill's modifier class; neutral and unknown get none. */
export function polarityClass(polarity: string | null | undefined): 'ok' | 'bad' | '' {
  return polarity === 'positive' ? 'ok' : polarity === 'negative' ? 'bad' : '';
}

/* ------------------------------------------------------------------ narrow */

function str(v: unknown): string {
  return v === null || v === undefined ? '' : String(v);
}

function num(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
