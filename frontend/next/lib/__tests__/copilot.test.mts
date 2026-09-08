/* Unit tests for the copilot demo's wire reducers (`app/copilot/logic.ts`).
   ========================================================================
   Runs with the rest of the suite — see `__tests__/README.md` for the one command and for why
   these files are `.mts`.

   What is being defended here is ONE thing above all: the envelope level. `docs/MIGRATION.md`
   defect 5 is that the page read `grounding` / `tier1` / `citations` / `suggestions` / `reply`
   / `usage` off the top level of `{state, turn: <envelope>}`, where none of them exist. Every
   read was guarded by an `if`, so the bug never threw — it rendered an empty panel, which is
   indistinguishable from a server that had nothing to say. A test is the only thing that can
   tell those two apart, which is why the fix lives in a pure function instead of inline in a
   fetch handler.

   The payloads below are copied from `docs/CHAT_INTEGRATION.md` §5.2–5.4, trimmed to the
   fields the page renders. */

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  BLANK_TURN, absorbAnswer, absorbTurn, appendDelta, blockedKey, citationNumbers, envelopeOf,
  retryDelay, spentTokens, stateOf, upsertVariant,
  type TurnState, type Wrapper,
} from '../../app/copilot/logic.ts';

/* The warm read, exactly as §5.4 documents it: state beside the turn, everything else inside. */
const READY: Wrapper = {
  client_id: 'c1',
  suggest_ref: 'sg_d1a7',
  state: 'ready',
  turn: {
    proto: 1,
    suggest_ref: 'sg_d1a7',
    grounding: { grounded: true, reason: 'ok', method: 'vector', top_score: 0.63, hit_count: 5, kb_present: true },
    citations: [{ n: 1, document_id: 'd1', chunk_id: 'k1', title: 'Transfer fees', score: 0.63 }],
    tier1: [{ n: 1, title: 'Transfer fees', snippet: 'A transfer to another bank costs…', chunk_id: 'k1' }],
    suggestions: [
      { index: 0, kind: 'answer', text: 'A transfer costs 1.5 GEL [1]', citations: [1] },
      { index: 1, kind: 'clarify', text: 'Which bank are you sending to?', citations: [] },
    ],
    reply: null,
    handoff: { recommended: false, reason: null, summary: null },
    usage: { input_tokens: null, output_tokens: null, model: 'claude', latency_ms: { retrieval: 170, gate: 0, llm: 1900, total: 2100 } },
  },
};

test('the warm read is unwrapped: the envelope is under `turn`, never beside it', () => {
  const envelope = envelopeOf(READY);
  assert.ok(envelope, 'the wrapper hides an envelope');
  assert.equal(envelope!.grounding?.method, 'vector');
  assert.equal(envelope!.suggestions?.length, 2);

  // THE REGRESSION. Reading the wrapper as if it were the envelope — what the legacy page did
  // — finds nothing at all, and the guarded `if`s turn that into an empty rail rather than an
  // error. Both halves are asserted so a "simplification" back to `d.grounding` fails here.
  const wrapper = READY as unknown as Record<string, unknown>;
  assert.equal(wrapper.grounding, undefined);
  assert.equal(wrapper.suggestions, undefined);

  const next = absorbTurn(BLANK_TURN, envelope);
  assert.equal(next.grounding?.top_score, 0.63);
  assert.equal(next.tier1.length, 1);
  assert.equal(next.citations.length, 1);
  assert.equal(next.variants.length, 2);
  assert.equal(next.variants[1].kind, 'clarify');
  assert.deepEqual(next.srvStages, { retrieval: 170, gate: 0, llm: 1900, total: 2100 });
});

test('a poll that is still running carries no envelope, and is not mistaken for an empty one', () => {
  const running: Wrapper = { client_id: 'c1', suggest_ref: 'sg_d1a7', state: 'running', retry_after_ms: 350 };
  assert.equal(envelopeOf(running), null);
  assert.equal(stateOf(running, null), 'running');
  // Absorbing "nothing yet" must leave the screen alone — not blank a rail the stream filled.
  const seeded = absorbTurn(BLANK_TURN, envelopeOf(READY));
  assert.equal(absorbTurn(seeded, envelopeOf(running)), seeded);
});

test('an SSE `done` frame wraps the same envelope under `turn`, and a bare envelope still works', () => {
  assert.equal(envelopeOf({ turn: READY.turn, seq: 9 })?.suggestions?.length, 2);
  assert.equal(envelopeOf(READY.turn)?.suggestions?.length, 2);   // legacy `d.turn || d` tolerance
  assert.equal(envelopeOf(null), null);
  assert.equal(envelopeOf({ seq: 3 }), null);
});

test('state falls back to "ready" only when the ENVELOPE holds drafts', () => {
  // A server that answers without `state`. Looking for `suggestions` at the top level (the
  // legacy fallback) says "running" forever and the loop polls to its own 20 s deadline.
  const noState = { turn: READY.turn };
  assert.equal(stateOf(noState, envelopeOf(noState)), 'ready');
  assert.equal(stateOf({}, null), 'running');
  assert.equal(stateOf({ state: 'refused' }, null), 'refused');
});

test('the blocking answer is the same wrapper, and its reply reaches the thread', () => {
  const blocking = {
    state: 'ready',
    turn: {
      suggest_ref: 'an_c0de',
      grounding: { grounded: true, reason: 'ok', method: 'vector', top_score: 0.71, hit_count: 4, kb_present: true },
      citations: [{ n: 1, title: 'Fees', chunk_id: 'k9' }],
      reply: { text: 'It costs 1.5 GEL [1]', citations: [{ n: 1, title: 'Fees' }], answered_from_kb: true },
      handoff: { recommended: false },
      usage: { latency_ms: { retrieval: 180, gate: 0, llm: 2400, total: 2600 } },
    },
  };
  const { next, botReply } = absorbAnswer(BLANK_TURN, envelopeOf(blocking));
  assert.equal(botReply, 'It costs 1.5 GEL [1]');
  assert.equal(next.answer?.answered_from_kb, true);
  assert.equal(next.suggestRef, 'an_c0de');
  assert.equal(next.citations.length, 1);
  assert.equal(spentTokens(next.srvStages), true);
});

test('a gate refusal spends nothing, and says so from the SERVER’s stage timings', () => {
  const refusal = {
    state: 'refused',
    turn: {
      grounding: { grounded: false, reason: 'no_hits', method: 'vector', top_score: 0.11, hit_count: 0, kb_present: true },
      reply: { text: 'I don’t have that in my knowledge base…', citations: [], answered_from_kb: false },
      handoff: { recommended: true, reason: 'no_hits', summary: 'Customer asked about parking.' },
      usage: { latency_ms: { kill_switch: 1, retrieval: 140, gate: 0, total: 150 } },
    },
  };
  const { next } = absorbAnswer(BLANK_TURN, envelopeOf(refusal));
  assert.equal(next.answer?.answered_from_kb, false);
  assert.equal(next.handoff?.recommended, true);
  // No `llm` and no `handoff_summary` stage ⇒ no model call. This is the zero-token badge, and
  // it is derived rather than asserted precisely so it cannot lie.
  assert.equal(spentTokens(next.srvStages), false);
});

test('autopilot_off / autopilot_killed become the configuration panel, not a refusal bubble', () => {
  for (const reason of ['autopilot_off', 'autopilot_killed']) {
    const { next } = absorbAnswer(BLANK_TURN, { grounding: { grounded: false, reason } });
    assert.deepEqual(next.blocked, { status: 0, detail: reason });
  }
  const { next } = absorbAnswer(BLANK_TURN, { grounding: { grounded: false, reason: 'no_hits' } });
  assert.equal(next.blocked, null);
});

test('a copilot `done` never erases what the stream already delivered', () => {
  // The stream sends `grounding` and `tier1` as separate frames; the terminal envelope may omit
  // either. Writing them unconditionally would blank a populated rail at the last moment.
  const seeded = absorbTurn(BLANK_TURN, envelopeOf(READY));
  const thin = absorbTurn(seeded, { suggestions: [{ index: 0, text: 'redraft' }] });
  assert.equal(thin.grounding?.method, 'vector');
  assert.equal(thin.tier1.length, 1);
  assert.equal(thin.variants[0].text, 'redraft');
});

test('the refusal text is read only when no drafts arrived — order, not coincidence', () => {
  const withBoth = absorbTurn(BLANK_TURN, {
    suggestions: [{ index: 0, kind: 'escalate', text: 'Let me pass you to a colleague.' }],
    reply: { text: 'refusal copy' },
  });
  assert.equal(withBoth.refusal, null, 'a draft is sendable in one click; the refusal panel is not shown');

  const replyOnly = absorbTurn(BLANK_TURN, { reply: { text: 'refusal copy' } });
  assert.equal(replyOnly.refusal, 'refusal copy');
});

test('drafts keep their server index when they arrive out of order', () => {
  let list = upsertVariant([], { index: 1, kind: 'clarify', text: 'b', citations: [], streaming: false });
  list = upsertVariant(list, { index: 0, kind: 'answer', text: 'a', citations: [], streaming: false });
  assert.deepEqual(list.map(v => v.index), [0, 1]);
  // A merge, not a replace: a `suggestion` frame following deltas must not drop the kind.
  list = upsertVariant(list, { index: 0, kind: 'answer', text: 'a!', citations: [1], streaming: false });
  assert.equal(list[0].text, 'a!');
  assert.deepEqual(list[0].citations, [1]);
});

test('a delta that arrives before its card creates one rather than being dropped', () => {
  const created = appendDelta([], 2, 'Hel');
  assert.equal(created.length, 1);
  assert.equal(created[0].index, 2);
  assert.equal(created[0].streaming, true);
  assert.equal(appendDelta(created, 2, 'lo')[0].text, 'Hello');
});

test('citation chips resolve both wire shapes', () => {
  // Copilot drafts cite integers; autopilot replies cite objects (CHAT_INTEGRATION §5.4).
  assert.deepEqual(citationNumbers([1, 2]), [1, 2]);
  assert.deepEqual(citationNumbers([{ n: 3, title: 'x' }, { n: 4 }]), [3, 4]);
  // A forged `[n]` with no `n` at all can only vanish — never resolve to some other passage.
  assert.deepEqual(citationNumbers([{ title: 'no n' }, null as never, 5]), [5]);
  assert.deepEqual(citationNumbers(undefined), []);
});

test('a block is explained by the server’s own detail, with 403 meaning scope', () => {
  assert.equal(blockedKey({ status: 409, detail: 'Tenant has no public documents' }), 'cd.blocked.nopublic');
  assert.equal(blockedKey({ status: 503, detail: 'autopilot_disabled by kill switch' }), 'cd.blocked.killed');
  assert.equal(blockedKey({ status: 403, detail: 'forbidden' }), 'cd.blocked.scope');
  assert.equal(blockedKey({ status: 409, detail: 'autopilot_not_enabled' }), 'cd.blocked.off');
});

test('the poll interval is clamped the way the legacy loop clamped it', () => {
  assert.equal(retryDelay({ retry_after_ms: 350 }), 350);
  assert.equal(retryDelay({ retry_after_ms: 5 }), 60);       // never busier than 60ms
  assert.equal(retryDelay({ retry_after_ms: 99999 }), 1000); // never slower than 1s
  assert.equal(retryDelay({}), 150);                          // the server said nothing
});

test('BLANK_TURN is not shared by reference between turns', () => {
  const seeded: TurnState = absorbTurn(BLANK_TURN, envelopeOf(READY));
  assert.equal(seeded.tier1.length, 1);
  assert.equal(BLANK_TURN.tier1.length, 0, 'the reset value must survive being absorbed into');
});
