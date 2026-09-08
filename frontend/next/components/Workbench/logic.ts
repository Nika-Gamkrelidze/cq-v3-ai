/* The workbench's decisions, with no React, no DOM and no dictionary in them.
   =========================================================================
   Everything in this file is a pure function over a payload the backend sent, or over the
   file queue. It is separate from the components for two reasons, and only one of them is
   taste:

     * `lib/__tests__/workbench.test.mts` runs under `node --experimental-strip-types`, whose
       ESM resolver needs a real extension on every import. An app file cannot write
       `../../lib/format.ts` (the root tsconfig has `allowImportingTsExtensions` off), so a
       module that is to be tested must have NO imports at all. That is why the helpers here
       that would otherwise reach for `lib/format` or `lib/aiShapes` take what they need as an
       argument instead — `lanesFor` is handed the band function and the lane names rather
       than importing `scoreBand` and a `t`.
     * The parts of workbench.js that were actually WRONG once are all in here (the verdict
       map, the level merge, the file-queue limits, the score-lane colour), and a test is
       cheaper than a re-read of a 1,200-line file.

   Types are erased before Node sees the file, so DOM types (`File`, `Blob`) are free to
   appear in a signature — as long as nothing here CALLS a DOM API. Nothing does. */

/* ------------------------------------------------------------------ shared shapes */

/** The four colour levels, shared by the score bands, the tone words and the timeline lanes. */
export type Level = 'good' | 'mid' | 'bad' | 'none';

/** A transcript turn, as `GET /recordings/{id}` returns it (design-v2 §2). */
export interface Segment {
  i: number;
  speaker: string;
  start: number | null;
  end: number | null;
  text: string;
}

/** One finding placed on the timeline (design-v2 §3). Structurally the `Span` the Timeline
    component takes — kept as a local declaration rather than an import so this file stays
    import-free; TypeScript matches them by shape. */
export interface Span {
  segments?: number[];
  start?: number | null;
  end?: number | null;
  level?: Level;
  score?: number | null;
  label?: string;
  detail?: string;
}

export interface Lane {
  id: string;
  name: string;
  color?: string | null;
  spans: Span[];
}

export const FEATURES = ['factcheck', 'score', 'semantic', 'summarise'] as const;
export type Feature = (typeof FEATURES)[number];

/** The three analysers that produce timeline lanes. `summarise` has none: it is about the
    calls as wholes, not about moments inside one. */
export const LANE_KINDS = ['factcheck', 'score', 'semantic'] as const;
export type LaneKind = (typeof LANE_KINDS)[number];

/* ------------------------------------------------------------------ result payloads */

export interface FactCheckEvidence {
  title?: string | null;
  doc_type?: string | null;
  snippet?: string | null;
  score?: number | null;
}

export interface FactCheckClaim {
  claim?: string | null;
  verdict?: string | null;
  rationale?: string | null;
  speaker?: string | null;
  category?: string | null;
  /** 0..1 from the model. */
  confidence?: number | null;
  evidence?: FactCheckEvidence | null;
  segments?: number[];
  start?: number | null;
  end?: number | null;
}

export interface FactCheckResult {
  accuracy_score?: number | null;
  counts?: {
    supported?: number;
    partially_supported?: number;
    contradicted?: number;
    not_in_kb?: number;
    total?: number;
  } | null;
  claims?: FactCheckClaim[] | null;
  spans?: Span[] | null;
}

export interface ScoreEvidence {
  quote?: string | null;
  text?: string | null;
  segments?: number[];
  start?: number | null;
  end?: number | null;
}

export interface ScoreDimension {
  key: string;
  name: string;
  score?: number | null;
  weight?: number | null;
  contribution?: number | null;
  rationale?: string | null;
  evidence?: (string | ScoreEvidence)[] | null;
  /** Set by `apply_manual_scores` when a reviewer overrode the model. */
  edited?: boolean;
  ai_score?: number | null;
}

export interface ScoreLaneRow {
  key?: string;
  name?: string;
  spans?: Span[] | null;
}

export interface ScoreResult {
  weighted_total?: number | null;
  max_total?: number | null;
  dimensions?: ScoreDimension[] | null;
  lanes?: ScoreLaneRow[] | null;
  version?: number | null;
  is_default?: boolean;
  manually_edited?: boolean;
  edited_by?: string | null;
}

export interface ScoreRevision {
  revision: number;
  scoring?: ScoreResult | null;
  edited_by?: string | null;
  created_at?: string | null;
  note?: string | null;
}

export interface SemanticSpeakerText {
  overall?: string | null;
  politeness?: number | null;
  flags?: unknown;
  rationale?: string | null;
}

export interface SemanticSpeakerVoice {
  voice?: string | null;
  share_good?: number | null;
  share_bad?: number | null;
}

export interface SemanticSpeaker {
  speaker?: string | null;
  role?: string | null;
  text?: SemanticSpeakerText | null;
  voice?: SemanticSpeakerVoice | null;
}

export interface SemanticSegment {
  i?: number | null;
  speaker?: string | null;
  start?: number | null;
  end?: number | null;
  text?: string | null;
  text_tone?: string | null;
  text_level?: string | null;
  text_note?: string | null;
  voice_label?: string | null;
  voice_level?: string | null;
  voice_confidence?: number | null;
}

export interface SemanticResult {
  modes?: string[] | null;
  language?: string | null;
  speakers?: SemanticSpeaker[] | null;
  segments?: SemanticSegment[] | null;
  spans?: { text?: Span[] | null; voice?: Span[] | null } | null;
  summary?: string | null;
  voice_available?: boolean;
  /** WHY there is no voice half — see `noVoiceKey`. */
  voice_status?: string | null;
}

export interface SummaryParticipant {
  label?: string | null;
  role?: string | null;
  appears_in?: number[] | null;
}

export interface SummaryCallCard {
  index?: number | null;
  title?: string | null;
  filename?: string | null;
  summary?: string | null;
  outcome?: string | null;
}

export interface SummaryBody {
  short_summary?: string | null;
  key_points?: unknown;
  action_items?: unknown;
  participants?: SummaryParticipant[] | null;
  calls?: SummaryCallCard[] | null;
  language?: string | null;
}

/** `POST /summaries` / `GET /summaries/{id}`: the model's summary plus the calls it read. */
export interface SummaryResult extends SummaryBody {
  id?: string;
  summary?: SummaryBody | string | null;
  calls?: RecordingRow[] | null;
}

/** A row from `POST /recordings`, `GET /recordings/{id}`, or a summary's `calls[]`. */
export interface RecordingRow {
  id?: string;
  job_id?: string;
  filename?: string | null;
  language?: string | null;
  duration_s?: number | null;
  transcript?: string | null;
  segments?: Segment[] | null;
  audio_url?: string | null;
  has_audio?: boolean | null;
  source?: string | null;
  kb_check?: FactCheckResult | null;
  scoring?: ScoreResult | null;
  semantic?: SemanticResult | null;
}

/** One call the panel is working on. The results of all three analysers coexist on it. */
export interface Call {
  id: string;
  filename: string;
  language: string;
  duration: number | null;
  segments: Segment[];
  transcript: string;
  source: 'audio' | 'text';
  /** Whether stored audio exists for it — false once a fetch for it has failed. */
  hasAudio: boolean;
  audioUrl: string | null;
  /** The bytes, once fetched. Handed to the Timeline as-is: it owns the object URL, so the
      panel never has one to leak. */
  blob: Blob | null;
  /** Speaker id → role, refined by a sentiment run. Labels are DERIVED from this per render,
      so a language switch relabels every chip. */
  roles: Record<string, string>;
  results: {
    factcheck: FactCheckResult | null;
    score: ScoreResult | null;
    semantic: SemanticResult | null;
  };
  /** The dictionary KEY of the sentence explaining a missing player — `wb.noaudio` when the
      retention purge has taken it, `wb.audiofail` when the fetch failed. A key rather than the
      sentence, so switching language relabels it; the legacy panel stored the rendered text
      and left the first language's words on screen. */
  noteKey: string;
}

/* ------------------------------------------------------------------ narrowing */

export function numOrNull(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v);
  return null;
}

export function asArray<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

/** A 0-100 percentage for a bar's width: clamped, rounded, and never NaN. */
export function pct(v: unknown): number {
  return Math.max(0, Math.min(100, Math.round(numOrNull(v) ?? 0)));
}

/* ------------------------------------------------------------------ levels */

const LEVELS: readonly Level[] = ['good', 'mid', 'bad', 'none'];

/** Anything the server called a level, as one of the four. */
export function levelOf(value: unknown): Level {
  return LEVELS.includes(value as Level) ? (value as Level) : 'none';
}

const RANK: Record<Level, number> = { none: 0, good: 1, mid: 2, bad: 3 };

/** The worse of two levels — "worse" meaning the one a reviewer must look at first, so an
    unjudged half (`none`) never outranks a judged one. */
export function worstLevel(a: unknown, b: unknown): Level {
  const x = levelOf(a);
  const y = levelOf(b);
  return RANK[y] > RANK[x] ? y : x;
}

/** How a WORD tone reads as a colour. `neutral` is deliberately `good`: a neutral turn is a
    perfectly fine turn, and painting it amber would flag every ordinary call. */
export function toneLevel(word: unknown): Level {
  const map: Record<string, Level> = {
    polite: 'good', neutral: 'good', curt: 'mid',
    impolite: 'bad', rude: 'bad', aggressive: 'bad',
  };
  return map[String(word ?? '').toLowerCase()] ?? 'none';
}

/** How a VOICE verdict reads as a colour. */
export function voiceVerdictLevel(word: unknown): Level {
  const map: Record<string, Level> = { patient: 'good', calm: 'good', tense: 'mid', aggressive: 'bad' };
  return map[String(word ?? '').toLowerCase()] ?? 'none';
}

/* ------------------------------------------------------------------ fact-check verdicts */

/** The verdict CLASS (`lib/aiShapes`'s `verdictClass`, which normalises the stored spelling)
    → the dictionary key of the pill's label.

    PARTIALLY_SUPPORTED KEEPS ITS OWN ROW. It once shared one with NOT_IN_KB, which told a
    reviewer the knowledge base had nothing to say about a claim it in fact partly
    contradicted — a correctness bug in a compliance feature, and the reason this is a named
    constant with a test rather than an object literal inside a renderer.

    `partial` deliberately reads `wb.fc.partial` ("partially supported") and not brand.js's
    own `fc.partial` ("partly correct"): the workbench is judging whether the KNOWLEDGE BASE
    supports the claim, where the analysis card is judging the claim itself. The two sentences
    are different on purpose and both are in the dictionary. */
export const VERDICT_LABEL_KEY: Record<string, string> = {
  supported: 'fc.supported',
  partial: 'wb.fc.partial',
  contradicted: 'fc.contradicted',
  notinkb: 'fc.notinkb',
};

/* ------------------------------------------------------------------ voice status */

/** WHY there is no voice half, as a dictionary key.

    The backend NAMES the cause (`voice_status`), and each cause gets its own sentence,
    because "the model is still warming up" is worth retrying and "this recording has no
    per-turn timings" never is. Both used to read identically, which sent a reviewer — and
    us — hunting through logs for a sidecar that was simply still loading.

    A result stored before `voice_status` existed carries none, so a missing or unrecognised
    status falls back to the generic line. The caller checks that the returned key actually
    resolves (a key that renders as itself is a key this build has no sentence for) and falls
    back to `wb.sem.novoice` too. */
export function noVoiceKey(status: unknown): string {
  const st = String(status ?? '').trim();
  if (!st || st === 'ok') return 'wb.sem.novoice';
  return `wb.novoice.${st}`;
}

/* ------------------------------------------------------------------ lanes */

export interface LaneNames {
  factcheck: string;
  words: string;
  voice: string;
}

export interface LaneOptions {
  names: LaneNames;
  /** The WORKSPACE's bands, as a function — `scoreBand` from `lib/aiShapes` bound to the
      pair fetched from `GET /scoring/bands`. Injected rather than imported so this file stays
      testable under Node, and so the scorecard number, its bar and the lane it paints cannot
      disagree about where amber starts. */
  scoreLevel: (value: number | null) => Level;
}

/** The lanes one analyser's result contributes to the timeline. */
export function lanesFor(kind: LaneKind, data: unknown, o: LaneOptions): Lane[] {
  if (!data) return [];

  if (kind === 'factcheck') {
    return [{ id: 'factcheck', name: o.names.factcheck, spans: asArray<Span>((data as FactCheckResult).spans) }];
  }

  if (kind === 'score') {
    return asArray<ScoreLaneRow>((data as ScoreResult).lanes).map((l, i) => ({
      id: `score:${l.key || i}`,
      name: l.name || l.key || '',
      /* `score: null` ON PURPOSE. It makes the timeline colour the span by its `level`, i.e.
         by the workspace's own three bands, instead of by a continuous hue ramp of its own —
         which is how a dimension came to be red on the card and olive on the timeline. */
      spans: asArray<Span>(l.spans).map(sp => ({
        ...sp,
        level: sp.score == null ? levelOf(sp.level) : o.scoreLevel(numOrNull(sp.score)),
        score: null,
      })),
    }));
  }

  const sm = data as SemanticResult;
  const spans = sm.spans || {};
  const modes = asArray<string>(sm.modes);
  const out: Lane[] = [];
  if (modes.includes('text') || asArray<Span>(spans.text).length) {
    out.push({ id: 'semantic:text', name: o.names.words, spans: asArray<Span>(spans.text) });
  }
  // The voice lane appears for a run that ASKED for voice and got it. `voice_available:false`
  // means the sidecar could not answer, and an empty lane in the legend is a promise the
  // panel cannot keep — the reason is shown in the pane instead.
  if (asArray<Span>(spans.voice).length || (modes.includes('voice') && sm.voice_available !== false)) {
    out.push({ id: 'semantic:voice', name: o.names.voice, spans: asArray<Span>(spans.voice) });
  }
  return out;
}

/** One line of transcript, marked by one lane's findings (the Timeline's `markSegments`). */
export interface SegmentMark {
  i: number;
  level?: Level;
  score?: number | null;
  title?: string;
}

/** A lane's spans → ONE mark per segment.

    Two spans of the same lane can cite the same segment — a CONTRADICTED and a NOT_IN_KB
    claim about the same sentence, say — and the timeline keeps the LAST mark it is given for
    an index, which would paint a misinformation hit grey. So they are merged instead: the
    worst level wins, the lowest score wins, and every label joins the tooltip.

    A score survives only when EVERY span that cited the segment had one. A lane that mixes
    scored and unscored findings has no meaningful number for the line, and a half-derived one
    would colour it by an average nobody computed — the level decides in that case. */
export function marksOf(spans: unknown): SegmentMark[] {
  interface Acc { i: number; level: Level; score: number | null; scored: boolean; titles: string[] }
  const by = new Map<number, Acc>();

  for (const sp of asArray<Span>(spans)) {
    const level = levelOf(sp.level);
    const score = numOrNull(sp.score);
    const title = sp.label || '';
    for (const i of asArray<number>(sp.segments)) {
      let m = by.get(i);
      if (!m) {
        m = { i, level, score, scored: score != null, titles: [] };
        by.set(i, m);
      } else {
        m.level = worstLevel(m.level, level);
        m.scored = m.scored && score != null;
        if (score != null && (m.score == null || score < m.score)) m.score = score;
      }
      if (title && !m.titles.includes(title)) m.titles.push(title);
    }
  }

  return Array.from(by.values()).map(m => {
    const out: SegmentMark = { i: m.i, level: m.level, title: m.titles.join(' · ') };
    if (m.scored && m.score != null) out.score = m.score;
    return out;
  });
}

/** Whether a lane is worth DRAWING on the timeline.

    A lane the timeline cannot draw is noise: with audio, a lane whose spans all lack times
    would be an empty 18px row plus a legend entry promising findings it cannot show. It is
    kept out of `setLanes` — but still passed to `markSegments`, which works for a lane the
    timeline is not drawing, so the findings land on the transcript either way.

    In text mode there are no spans drawn at all, so every lane with findings keeps its legend
    entry: there it is the control that toggles the transcript marks. */
export function laneDrawable(lane: Lane, hasAudio: boolean): boolean {
  const spans = asArray<Span>(lane && lane.spans);
  return hasAudio ? spans.some(sp => numOrNull(sp.start) != null) : spans.length > 0;
}

/** Fact-check first, then the rubric, then tone — the order a reviewer reads them in, and
    stable regardless of which analyser was run last. */
export function sortLanes(lanes: Lane[]): Lane[] {
  const rank = (id: string) => {
    const i = (LANE_KINDS as readonly string[]).indexOf(id.split(':')[0]);
    return i < 0 ? LANE_KINDS.length : i;
  };
  return [...lanes].sort((a, b) => rank(a.id) - rank(b.id));
}

/* ------------------------------------------------------------------ speakers */

/** Speaker ids the sentiment run has identified, folded into the call's role map.

    Only `agent` and `customer` are taken: `unknown` and `other` carry no information the
    transcript's own label did not already have, and overwriting a pasted transcript's
    "ოპერატორი" with "Unknown" would lose a real name. */
export function rolesWithSpeakers(
  roles: Record<string, string>,
  sm: SemanticResult | null | undefined,
): Record<string, string> {
  if (!sm) return roles;
  const out = { ...roles };
  for (const s of asArray<SemanticSpeaker>(sm.speakers)) {
    if (s && s.speaker && (s.role === 'agent' || s.role === 'customer')) out[s.speaker] = s.role;
  }
  return out;
}

/* ------------------------------------------------------------------ adopting a row */

export interface CallExtra {
  blob?: Blob | null;
  source?: 'audio' | 'text';
  /** The pasted text, when the row came from `POST /recordings/text`. */
  text?: string;
}

/** A server row → the call the panel works on.

    Two details are load-bearing:
      * A transcript with no segments is split on blank lines into one segment per line, so a
        pasted transcript still has something to highlight and to cite.
      * A pasted transcript's own "Agent:" / "ოპერატორი:" labels arrive as the speaker id
        itself (§2). They are kept as ROLES so the chips read "Agent" rather than "Speaker 1";
        a sentiment run refines them later. */
export function callFromRow(row: RecordingRow | null | undefined, extra: CallExtra = {}): Call {
  const r = row || {};
  const source: 'audio' | 'text' = extra.source || (r.source === 'text' || r.source === 'audio' ? r.source : null) || (r.audio_url ? 'audio' : 'text');
  const hasAudio = source === 'audio'
    && (r.has_audio != null ? !!r.has_audio : !!(r.audio_url || extra.blob));

  const transcript = r.transcript || extra.text || '';
  let segments = asArray<Segment>(r.segments);
  if (!segments.length && transcript) {
    segments = transcript.split(/\n+/).map(s => s.trim()).filter(Boolean)
      .map((text, i) => ({ i, speaker: 'speaker_0', start: null, end: null, text }));
  }

  const roles: Record<string, string> = {};
  for (const s of segments) {
    const id = s && s.speaker;
    if (id && !/^speaker_\d+$/.test(id) && !roles[id]) roles[id] = String(id);
  }

  return {
    id: String(r.id || r.job_id || ''),
    filename: r.filename || (extra.blob as File | null)?.name || '',
    language: r.language || '',
    duration: numOrNull(r.duration_s),
    segments,
    transcript,
    source,
    hasAudio,
    audioUrl: r.audio_url || null,
    blob: extra.blob || null,
    roles,
    results: { factcheck: r.kb_check || null, score: r.scoring || null, semantic: r.semantic || null },
    noteKey: '',
  };
}

/* ------------------------------------------------------------------ the file queue */

/* §8: 1..10 files, each <= 100 MB, <= 300 MB in one summary. Refused HERE as well as by the
   server, because the alternative is a 300 MB upload that ends in a 413. */
export const LIMITS = { maxFiles: 10, maxMb: 100, maxTotalMb: 300 } as const;

export interface QueueNotice {
  key: string;
  vars?: Record<string, string | number>;
  kind: 'info' | 'err';
}

export interface QueueResult<T> {
  files: T[];
  /** What to toast, in order. Returned rather than raised so this stays a pure function. */
  notices: QueueNotice[];
}

/** Adding files to the queue.

    `multi` is the SUMMARISE tab, not "more than one file was dropped": everywhere else the
    panel works on one recording, so a second file replaces the first and says so. Silently
    ignoring the extra files reads as a broken drop target. */
export function queueFiles<T extends { name: string; size: number }>(
  current: readonly T[],
  incoming: readonly T[],
  multi: boolean,
  limits: { maxFiles: number; maxMb: number; maxTotalMb: number } = LIMITS,
): QueueResult<T> {
  const files = incoming.filter(f => f && typeof f.size === 'number');
  if (!files.length) return { files: [...current], notices: [] };

  if (!multi) {
    const replaced = current.length > 0 || files.length > 1;
    return {
      files: [files[0]],
      notices: replaced ? [{ key: 'wb.onefile', kind: 'info' }] : [],
    };
  }

  const out = [...current];
  const notices: QueueNotice[] = [];
  let total = out.reduce((a, f) => a + (f.size || 0), 0);
  for (const f of files) {
    if (f.size > limits.maxMb * 1048576) {
      notices.push({ key: 'cv.toobig', vars: { name: f.name, max: `${limits.maxMb} MB` }, kind: 'err' });
      continue;
    }
    if (out.length >= limits.maxFiles) {
      notices.push({ key: 'wb.toomany', vars: { max: limits.maxFiles }, kind: 'err' });
      break;
    }
    // The server refuses the whole upload over the total: say so before the bytes go up the wire.
    if (total + f.size > limits.maxTotalMb * 1048576) {
      notices.push({ key: 'wb.toobig.total', vars: { max: `${limits.maxTotalMb} MB` }, kind: 'err' });
      continue;
    }
    out.push(f);
    total += f.size;
  }
  return { files: out, notices };
}

/** Whether a batch is over the total-size limit, checked once more at submit time: the queue
    can also be filled by a re-run that re-sends stored audio, which never passed through
    `queueFiles`. */
export function overTotalSize(
  files: readonly { size: number }[],
  limits: { maxTotalMb: number } = LIMITS,
): boolean {
  return files.reduce((a, f) => a + ((f && f.size) || 0), 0) > limits.maxTotalMb * 1048576;
}

/* ------------------------------------------------------------------ tabs */

/** The caller's feature order wins; unknown names are dropped and duplicates collapse.
    An empty or fully-unknown list falls back to all four, as the legacy component does. */
export function featureOrder(features: readonly string[] | null | undefined): Feature[] {
  const want = asArray<string>(features).filter(k => (FEATURES as readonly string[]).includes(k)) as Feature[];
  const list = want.length ? want : [...FEATURES];
  return list.filter((k, i, a) => a.indexOf(k) === i);
}

/** Which tab to open once a source has loaded: the first feature that already HAS a result,
    or none (leave the current tab) when nothing has been run yet. */
export function firstResultTab(
  order: readonly Feature[],
  call: Call | null,
  hasSummary: boolean,
): Feature | null {
  for (const k of order) {
    if (k === 'summarise' ? hasSummary : !!(call && call.results[k])) return k;
  }
  return null;
}
