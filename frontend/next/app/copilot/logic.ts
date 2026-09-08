/* The copilot demo's wire shapes, and the pure reducers over them.
   ===============================================================
   Everything here is the part of `copilot-demo.html` that has nothing to do with React: what
   `/v1/chat/*` returns, and how one turn's state is folded out of it. It lives in its own file
   because it is the half of the page that was WRONG (docs/MIGRATION.md, defect 5) and a bug
   that was invisible in a 1,200-line inline script should be pinned by a test — see
   `lib/__tests__/copilot.test.mts`.

   ── THE DEFECT, AND THE FIX ────────────────────────────────────────────────────────────────
   `GET /v1/chat/suggestions/{ref}` and the blocking `POST /v1/chat/answer` both answer with a
   WRAPPER around the turn:

       {"client_id":…, "suggest_ref":…, "state":"ready", "turn": { …the envelope… }}

   and everything the rail renders — `grounding`, `tier1`, `citations`, `suggestions`, `reply`,
   `handoff`, `usage` — lives inside `turn`, never beside it (docs/CHAT_INTEGRATION.md §5.2,
   §5.4). The legacy page read them off the TOP level in `pollSuggestion`, `absorbTurn` and the
   blocking branch of `absorbAnswer`, where none of them exist. Every read was guarded by an
   `if`, so nothing threw: the warm path and the non-streaming answer path just rendered an
   empty rail, and the demo looked like a backend that had returned nothing. Only the SSE
   `done` handlers were right, because they happened to say `d.turn || d`.

   So the wrapper is opened ONCE, here, by `envelopeOf`, and the reducers below only ever see
   an envelope. That is the shape of the fix: not "add `.turn` at four call sites" — that is
   how it was got wrong the first time — but one boundary function with one test.

   `envelopeOf` keeps the legacy `d.turn || d` tolerance (an SSE `done` frame is
   `{turn:…, seq}`, but a replay adapter that hands the envelope over bare still works). What
   it will not do is treat a `{state:"running", retry_after_ms}` poll as an envelope full of
   `undefined`s — that is exactly the read that produced the bug. */

/* ---------------------------------------------------------------------------------------
   Wire types. Every field is optional: this is a demo harness pointed at whatever the server
   is running today, and a shape assertion here would be a crash rather than an empty panel.
   --------------------------------------------------------------------------------------- */

export interface Grounding {
  grounded?: boolean;
  reason?: string | null;
  method?: string | null;
  top_score?: number | null;
  hit_count?: number | null;
  kb_present?: boolean | null;
}

export interface Citation {
  n?: number;
  document_id?: string | null;
  chunk_id?: string | null;
  title?: string | null;
  score?: number | null;
}

/** A tier-1 KB passage card — retrieval's own output, rendered before any model call. */
export interface Passage extends Citation {
  snippet?: string | null;
  content?: string | null;
}

/** A citation as it appears on a draft or a reply. The copilot sends integers (an `n` into
    `citations`), autopilot sends the objects themselves — documented divergence, not drift
    (docs/CHAT_INTEGRATION.md §5.4). Both resolve through `citationNumbers`. */
export type CiteRef = number | Citation;

export interface Suggestion {
  index?: number;
  kind?: string;
  text?: string;
  citations?: CiteRef[];
}

export interface Reply {
  text?: string;
  citations?: Citation[];
  answered_from_kb?: boolean;
}

export interface Handoff {
  recommended?: boolean;
  reason?: string | null;
  summary?: string | null;
  goal?: string | null;
}

export interface Usage {
  input_tokens?: number | null;
  output_tokens?: number | null;
  model?: string | null;
  latency_ms?: Record<string, number> | null;
}

/** The Turn envelope — the one object the whole page renders. Identical on the blocking
    answer, the streamed `done` frame and the warm read, which is the reason there is exactly
    one consumer of it here. */
export interface Envelope {
  proto?: number;
  turn_ref?: string | null;
  suggest_ref?: string | null;
  conversation_ref?: string | null;
  client_id?: string | null;
  channel?: string | null;
  locale?: string | null;
  grounding?: Grounding | null;
  citations?: Citation[] | null;
  tier1?: Passage[] | null;
  suggestions?: Suggestion[] | null;
  reply?: Reply | null;
  handoff?: Handoff | null;
  usage?: Usage | null;
  /** Not in the contract; read because the legacy page did, so a server that grows a flat
      stage map keeps working without a client change. `usage.latency_ms` is the real one. */
  stages?: Record<string, number> | null;
}

/** The wrapper `GET /suggestions/{ref}` and the blocking `POST /answer` reply with. */
export interface Wrapper {
  client_id?: string | null;
  suggest_ref?: string | null;
  state?: string;
  detail?: string | null;
  code?: string | null;
  retry_after_ms?: number | null;
  turn?: Envelope | null;
}

/* ---------------------------------------------------------------------------------------
   Page state for one turn.
   --------------------------------------------------------------------------------------- */

export interface Variant {
  index: number;
  kind: string;
  text: string;
  citations: CiteRef[];
  /** True while deltas are still arriving — drives the blinking caret. */
  streaming: boolean;
}

export interface Answer {
  text: string;
  citations: Citation[];
  answered_from_kb: boolean;
  streaming: boolean;
}

/** A 4xx (or an engine reason) that means "this bot may not speak at all". It is a
    configuration answer the operator can act on, not a crash, so it gets its own panel. */
export interface Blocked {
  status: number;
  detail: string;
}

/** Everything one turn produces. Cleared in ONE place (`BLANK_TURN`) so a mode switch or a new
    conversation cannot leave a stale answer sitting under a fresh question — including
    `suggestRef`, which belongs to the turn that produced it: keeping it across a mode switch
    would leave the copilot rail rendering an autopilot turn's ref, and the reverse. */
export interface TurnState {
  suggestRef: string | null;
  /** Client-measured milliseconds since the send, per stage. */
  marks: Record<string, number>;
  /** The SERVER's own stage timings, from `usage.latency_ms`. */
  srvStages: Record<string, number> | null;
  grounding: Grounding | null;
  tier1: Passage[];
  citations: Citation[];
  variants: Variant[];
  usage: Usage | null;
  handoff: Handoff | null;
  refusal: string | null;
  answer: Answer | null;
  blocked: Blocked | null;
  shownSent: boolean;
  editing: number | null;
  err: string;
}

export const BLANK_TURN: TurnState = {
  suggestRef: null,
  marks: {},
  srvStages: null,
  grounding: null,
  tier1: [],
  citations: [],
  variants: [],
  usage: null,
  handoff: null,
  refusal: null,
  answer: null,
  blocked: null,
  shownSent: false,
  editing: null,
  err: '',
};

/* ---------------------------------------------------------------------------------------
   The boundary: opening the wrapper.
   --------------------------------------------------------------------------------------- */

const ENVELOPE_KEYS = ['proto', 'grounding', 'citations', 'tier1', 'suggestions', 'reply', 'handoff', 'usage'] as const;

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** The Turn envelope inside a response body, or `null` when the body carries none.

    Three bodies reach this, and all three are handled by the two branches:
      * `{state, turn:{…}}`      — the warm read and the blocking answer   -> `.turn`
      * `{turn:{…}, seq}`        — an SSE `done` frame                     -> `.turn`
      * `{state:"running", …}`   — a poll that is not finished yet         -> `null`
    The second branch (a body that IS an envelope) exists only because the legacy handlers
    wrote `d.turn || d`, and a replay adapter that hands the envelope over bare is cheap to
    keep working. It is deliberately keyed on envelope FIELDS rather than on "not a wrapper":
    a running poll has `client_id` and `suggest_ref` too, and treating it as an envelope is
    the original bug. */
export function envelopeOf(body: unknown): Envelope | null {
  if (!isObject(body)) return null;
  if (isObject(body.turn)) return body.turn as Envelope;
  if (ENVELOPE_KEYS.some(k => body[k] != null)) return body as Envelope;
  return null;
}

/** What the warm read says about the generation: `running` | `ready` | `refused` | `error`.

    `refused` is a SUCCESS — the KB had nothing usable, so the drafts are one `escalate` card
    carrying the refusal copy (docs/CHAT_INTEGRATION.md §5.4). Only `running` means poll again.
    The fallback, for a server that answers without a `state`, is the legacy one: drafts
    present ⇒ ready. It now looks for them in the envelope rather than at the top level, which
    is the same defect in its third disguise — without this the loop polled until its own 20 s
    deadline on a response that was ready the whole time. */
export function stateOf(body: unknown, envelope: Envelope | null): string {
  const raw = isObject(body) && typeof body.state === 'string' ? body.state : '';
  if (raw) return raw;
  return envelope?.suggestions?.length ? 'ready' : 'running';
}

/** The poll interval the server asked for, clamped exactly as the legacy loop clamped it. */
export function retryDelay(body: unknown): number {
  const raw = isObject(body) && typeof body.retry_after_ms === 'number' ? body.retry_after_ms : 0;
  return Math.max(60, Math.min(1000, raw || 150));
}

/* ---------------------------------------------------------------------------------------
   Reducers. Pure: envelope in, next state out.
   --------------------------------------------------------------------------------------- */

function normalizeVariants(rows: Suggestion[]): Variant[] {
  return rows.map((s, i) => ({
    index: s.index ?? i,
    kind: s.kind || 'answer',
    text: s.text || '',
    citations: Array.isArray(s.citations) ? s.citations : [],
    streaming: false,
  }));
}

/** Fold a copilot envelope into the turn.

    Every field is written only when the envelope actually carries it, because this runs over
    the STREAM's terminal frame as well as the warm read: the stream has already delivered
    `grounding` and the tier-1 cards, and a `done` frame that omitted one must not erase what
    is on screen. The one ordering that matters is `refusal`, which is set only when no drafts
    arrived — so it is read AFTER `suggestions`, exactly as the legacy code did. */
export function absorbTurn(prev: TurnState, envelope: Envelope | null): TurnState {
  if (!envelope) return prev;
  const next: TurnState = { ...prev };
  if (envelope.grounding) next.grounding = envelope.grounding;
  if (envelope.tier1?.length) next.tier1 = envelope.tier1;
  if (envelope.citations?.length) next.citations = envelope.citations;
  if (envelope.suggestions?.length) next.variants = normalizeVariants(envelope.suggestions);
  if (envelope.usage) next.usage = envelope.usage;
  if (envelope.handoff) next.handoff = envelope.handoff;
  if (envelope.reply?.text && !next.variants.length) next.refusal = envelope.reply.text;
  const stages = envelope.stages || envelope.usage?.latency_ms;
  if (stages) next.srvStages = stages;
  return next;
}

/** Fold an autopilot envelope into the turn, and say what the bot said.

    The reply is returned rather than pushed, so the caller owns the thread — the same reason
    the reducer is pure. `null` means the bot produced no text at all (an engine-side block),
    and nothing is appended.

    `srvStages` is ASSIGNED here rather than merged: the answer path's terminal envelope is
    authoritative about its own timings, and a missing `latency_ms` means "this turn spent
    nothing", which is precisely what the zero-token badge is derived from. */
export function absorbAnswer(
  prev: TurnState,
  envelope: Envelope | null,
): { next: TurnState; botReply: string | null } {
  if (!envelope) return { next: prev, botReply: null };
  const next: TurnState = { ...prev };
  if (envelope.suggest_ref) next.suggestRef = envelope.suggest_ref;
  if (envelope.grounding) next.grounding = envelope.grounding;
  if (Array.isArray(envelope.citations)) next.citations = envelope.citations;
  if (envelope.handoff) next.handoff = envelope.handoff;
  if (envelope.usage) next.usage = envelope.usage;
  next.srvStages = envelope.usage?.latency_ms || null;

  const reply = envelope.reply || null;
  next.answer = reply
    ? {
        text: reply.text || '',
        citations: Array.isArray(reply.citations) ? reply.citations : [],
        answered_from_kb: !!reply.answered_from_kb,
        streaming: false,
      }
    : null;

  // The two engine reasons that mean "this bot is not allowed to speak at all" deserve the
  // configuration panel, not a refusal bubble — the tenant has nothing to fix in their KB.
  const reason = envelope.grounding?.reason || '';
  if (reason === 'autopilot_off' || reason === 'autopilot_killed') {
    next.blocked = { status: 0, detail: reason };
  }
  return { next, botReply: next.answer?.text || null };
}

/** Insert or merge one streamed draft, keeping the list ordered by `index`. */
export function upsertVariant(list: Variant[], v: Variant): Variant[] {
  const i = list.findIndex(x => x.index === v.index);
  const out = i >= 0 ? list.map((x, j) => (j === i ? { ...x, ...v } : x)) : [...list, v];
  return out.sort((a, b) => a.index - b.index);
}

/** Append streamed text to one draft, creating it if the first delta arrives before its card. */
export function appendDelta(list: Variant[], index: number, text: string): Variant[] {
  const i = list.findIndex(x => x.index === index);
  if (i < 0) {
    return upsertVariant(list, { index, kind: 'answer', text, citations: [], streaming: true });
  }
  return list.map((x, j) => (j === i ? { ...x, text: x.text + text, streaming: true } : x));
}

/** The `[n]`s a draft or a reply cites, whichever of the two shapes it used. */
export function citationNumbers(refs: CiteRef[] | null | undefined): number[] {
  return (refs || [])
    .map(r => (typeof r === 'object' && r !== null ? r.n : r))
    .filter((n): n is number => typeof n === 'number');
}

/** Did this turn actually spend anything?

    Derived from the SERVER's own stage timings: the refusal path runs neither the answer call
    nor the handoff-summary call, so neither stage exists. `usage.input_tokens` is deliberately
    null on the wire (tokens are metered into `llm_usage` server-side), so counting stages is
    the honest signal, not a decoration. */
export function spentTokens(srvStages: Record<string, number> | null): boolean {
  const st = srvStages || {};
  return st.llm != null || st.handoff_summary != null;
}

/** Which sentence explains a block. Substring matching on the server's own `detail`, because
    the chat router's `code` is a sibling of `detail` on its own errors but absent on the ones
    the shared auth layer raises (docs/CHAT_INTEGRATION.md §5). */
export function blockedKey(blocked: Blocked): string {
  const d = (blocked.detail || '').toLowerCase();
  if (d.includes('public')) return 'cd.blocked.nopublic';
  if (d.includes('kill')) return 'cd.blocked.killed';
  if (d.includes('scope') || blocked.status === 403) return 'cd.blocked.scope';
  return 'cd.blocked.off';
}

/** The i18n key for an engine reason. Unknown reasons have no key, and the caller renders the
    raw string rather than a blank — a reason string the UI has not learned yet is still the
    most useful thing on screen. */
export function reasonKey(reason: string | null | undefined): string {
  return `cd.r.${reason || ''}`;
}
