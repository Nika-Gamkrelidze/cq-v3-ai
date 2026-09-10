# Prompt for Claude Code in the chat-service repository

> **Historical — consumed by swiftchat-server `ca1a3aa` / swiftchat-suite `47f2539` on 2026-09-08.**
> The chat side was built from this prompt; the file is kept for the record and is **not**
> maintained (the 429 codes, channel names and check order below are already behind
> `CHAT_INTEGRATION.md`). Edit `CHAT_INTEGRATION.md` and the consumer's `src/services/cq/`
> instead — never this file.

Paste everything below the line into Claude Code, opened at the root of the **chat-service**
repository. Replace every `<ANGLE_BRACKET>` placeholder first. The full contract lives in the CQ
repository as `docs/CHAT_INTEGRATION.md`; this prompt inlines what the chat side needs and does
not assume access to that repository.

---

## Context

This repository is the backend of a multi-tenant customer-chat product: end customers write to a
business (the *tenant*) over web chat and social channels, and the tenant's human operators reply
from an operator console. We are adding an AI layer supplied by a separate service, **CQ**
(CommuniQ AI, `https://ai.communiq.ge/api`). CQ owns the tenant's knowledge base, the prompts, the
model, the AI-disclosure wording, the refusal wording and the token metering. Our side owns the
conversations, the messages, the operator queue and the decision of *when* to call CQ. The
integration is server-to-server HTTP with JSON; Server-Sent Events are optional; there is no
WebSocket.

## Goal

Make the CQ bot the **first layer** of every conversation for tenants that enable it: the bot
greets, answers grounded questions, and — the moment CQ recommends a handoff or the CQ call fails
— routes the conversation to a human operator and never answers that conversation again. After
the handoff, keep feeding every message to CQ so the operator gets **copilot drafts**, and report
what the operator did with them. Persist CQ's identifiers and envelopes, stay idempotent under
retries, fail safe to the operator queue, and never build what CQ already owns.

---

## The CQ contract (everything you need)

**Base URL:** `CQ_BASE_URL = https://ai.communiq.ge/api`. Paths below are relative to it.
**Locales:** `ka` | `ru` | `en`.

### Headers

| Header | When | Value / behaviour |
|---|---|---|
| `X-CQ-Key` | every call except `GET /v1/chat/stream` and `GET /v1/chat/health` | `cqi_<key_id>.<secret>` — the one server secret. Must be the **only** credential header (no `Authorization`, `X-API-Key`, `X-Admin-Token`) or CQ returns 400. |
| `X-CQ-Tenant` | with `X-CQ-Key` | the tenant's CQ `client_id` (uuid). Ungranted/unknown/missing → **401**, indistinguishable from a bad key. |
| `X-CQ-Expect-Tenant` | every `POST` and `DELETE` (send on every call) | the same `client_id`. Missing → **400**; mismatch → **403** (a mapping bug on our side). |
| `X-CQ-End-User` | `POST /turns`, `POST /answer` | an **opaque, stable** end-user id (never email/phone). Per-end-user rate cap. |
| `Idempotency-Key` | optional on `/turns`, `/answer` | second replay key; `turn_ref` is the primary one. |
| `Content-Type` | JSON bodies | `application/json` |

Every response echoes the resolved `client_id`. Assert it equals what we sent.

### Endpoints (in flow order)

**`GET /v1/chat/health`** — unauthenticated probe → `{"status":"ok","transport":"chat"}`.

**`GET /v1/chat/config`** → the CQ-side switch and the static copy:
```json
{"client_id":"…","version":3,"persona":"…",
 "greeting":{"ka":"…","ru":"…","en":"…"},"refusal_copy":{"ka":"…","ru":"…","en":"…"},
 "languages":["ka","ru","en"],"canned":[],"autopilot_enabled":true,"scopes":["chat:turn","chat:suggest","chat:answer","chat:sync"]}
```
`greeting` / `refusal_copy` may be `{}`. `languages[0]` is CQ's default locale for the tenant.

**`POST /v1/chat/answer`** (optionally `?stream=1`) — the bot answers one customer message.
```json
{"conversation_ref":"<our thread id>","turn_ref":"<our message id>","content":"…",
 "channel":"web","locale":"ka","customer_ref":"<opaque end-user id>",
 "display_name":"<optional>","subject":null,"attachment":null}
```
`content` is required, ≤ 8000 chars. **200:**
```json
{"client_id":"…","conversation_id":"<CQ uuid>","conversation_ref":"…","turn_id":"…","turn_ref":"…",
 "suggest_ref":"an_<turn_id>","state":"ready|refused","idempotent_replay":false,
 "turn":{ …Turn envelope… }}
```
Streaming (`?stream=1`) returns `text/event-stream` with frames `open` → `grounding` → `delta`*
(→ `error {"fatal":false}`) → `done {"turn": …}`; refusals are `open` → (`grounding`) → `done`;
`: ping` every 15 s; no `id:`/resume. Deltas are **raw** model text — the authoritative text is
`done.turn.reply.text`.

**`POST /v1/chat/turns`** → **202** — copilot ingest, one call per message after handoff.
```json
{"conversation_ref":"…","turn_ref":"<our message id>","content":"…","role":"customer|operator|bot",
 "channel":"web","locale":"ka","customer_ref":"…","mode":"assist","precompute":true}
→ {"client_id":"…","conversation_id":"…","conversation_ref":"…","turn_id":"…","turn_ref":"…",
   "suggest_ref":"sg_<turn_id>"|null,"precompute":true,"idempotent_replay":false,"retry_after_ms":350}
```
Only `role: "customer"` produces a `suggest_ref`; `operator` and `bot` are mirrored for history
only. `mode` must be `"assist"` (anything else is a 422).

**`GET /v1/chat/suggestions/{suggest_ref}`** — the warm read (one SELECT, poll at 350 ms):
`{"state":"running","retry_after_ms":350}` · `{"state":"error","detail":"…","code":"generation_failed"}` ·
`{"state":"ready"|"refused","turn":{ …envelope with tier1 + suggestions… }}` · **404** `not_found`.
`refused` is a success: one `kind: "escalate"` card carrying the refusal copy, `handoff.recommended: true`.

**`POST /v1/chat/stream-tickets`** `{"suggest_ref":"…"}` →
`{"ticket":"…","expires_in":60,"url":"/api/v1/chat/stream?ticket=…"}` (origin-relative URL; 60 s,
single-use). **`GET /v1/chat/stream?ticket=…`** (no `X-CQ-Key`) → frames `open` → `grounding` →
`tier1` → `suggestion`* → `done`. The copilot stream has **no** `delta` frames. Optional — only if
the operator UI streams drafts.

**`POST /v1/chat/regenerate`** `{"suggest_ref":"…","transform":"shorter|warmer|formal|to_ru|to_ka|null"}`
→ **202** with a **new** `suggest_ref` (`rg_…`).

**`POST /v1/chat/feedback`** → **204**, fire-and-forget:
`{"suggest_ref":"…","action":"shown|inserted|edited_sent|sent_asis|ignored","variant_index":0,"final_text":"…"}`

**`POST /v1/chat/conversations:sync`** — bulk mirror of threads CQ never saw:
`{"conversations":[{"client_id":"<cq_client_id>","external_ref":"<thread id>","channel":"web","locale":"ka","customer_ref":"…","turns":[{"role":"customer","content":"…","turn_ref":"<msg id>","lang":"ka"}]}]}`
→ `{"client_id":"…","conversations":1,"turns":N,"replays":M}`. ≤ 100 conversations, ≤ 200 turns
each; every item's `client_id` must be the resolved tenant or the **whole batch** is rejected.

**`DELETE /v1/chat/conversations/{external_ref}`** → **204** always (idempotent GDPR purge).

### The Turn envelope (`turn` in the answer body, `done.turn` on SSE, `turn` on suggestions)

```jsonc
{
  "proto": 1,
  "turn_ref": "<our message id | null on copilot>",
  "suggest_ref": "an_… | sg_… | rg_…",              // the join key
  "conversation_ref": "<ours on answers; CQ's conversation_id on copilot envelopes>",
  "client_id": "<echoed>", "channel": "web", "locale": "ka|ru|en",
  "grounding": {"grounded": true, "reason": "ok|kb_empty|no_hits|low_score|keyword_only|escalation|autopilot_off|autopilot_killed|llm_error",
                "method": "vector|keyword|none", "top_score": 0.71, "hit_count": 4, "kb_present": true},
  "citations": [{"n": 1, "document_id": "…", "chunk_id": "…", "title": "…", "score": 0.71}],
  "tier1":       [ {"n": 1, "title": "…", "snippet": "…", "chunk_id": "…", "document_id": "…", "score": 0.7} ],   // copilot only, [] on answers
  "suggestions": [ {"index": 0, "kind": "answer|clarify|escalate", "text": "…", "citations": [1, 2]} ],          // copilot only, [] on answers
  "reply": {"text": "… [1]\n\n(AI-disclosure line, appended by CQ)", "citations": [ {"n": 1, "…": "…"} ], "answered_from_kb": true} , // answers only, null on copilot
  "handoff": {"recommended": false, "reason": null, "summary": null /*, "goal": "…" */},
  "usage": {"input_tokens": null, "output_tokens": null, "model": "…", "latency_ms": {"retrieval": 180, "llm": 2400, "total": 2600}}
}
```

`handoff.reason` values: a grounding reason, `escalation:<keyword|legal_threat|complaint|distress>`,
`commitment:<label>`, `ungrounded_answer`, `llm_error`, copilot `commitment_or_model_flagged`.
`handoff.summary` is what the operator should see (a model summary or the last customer message).
**`reply.text` is never empty on a 200 and already contains the AI disclosure — never add one.**
Live `[n]` markers in the text index `citations`.

### Status codes and the required reaction

| Status | `code` | Reaction |
|---|---|---|
| 200 answer | | send `turn.reply.text`; if `turn.handoff.recommended` → hand off. |
| 202 turns/regenerate | | store `suggest_ref`; poll suggestions at `retry_after_ms`. |
| 204 | | nothing. |
| 400 | `empty_content` / missing `X-CQ-Expect-Tenant` / two credential headers / bad uuid | **our bug** — alert, do not retry. |
| 401 | *(none)* | wrong or rotated key, **or tenant not granted**: open the tenant circuit, alert; bot mode → operator. |
| 403 | *(none)* | `X-CQ-Expect-Tenant` mismatch = **our mapping bug** — alert loudly, do not retry, → operator. |
| 404 | `not_found` | unknown suggestion — show no draft. |
| 409 | `autopilot_not_enabled` | CQ-side switch off → operator; invalidate the config cache; stop offering the bot for the tenant. |
| 409 | `answer_in_flight` | duplicate delivery of the same `turn_ref` — **retry** the identical request with backoff (0.5 s, 1 s, 2 s …, ≤ 90 s), never hand off for this. |
| 413 | `content_too_large` (> 8000 chars) | bot mode → operator; copilot → mirror a truncated copy. |
| 422 | *(FastAPI list `detail`)* | **our bug** — alert, do not retry. |
| 429 | *(none, rate cap)* or `llm_busy` | caps: **60 answers/min per tenant, 60/hour per end user** (copilot: 120/120). → operator; do not retry the same message; back off the tenant. |
| 502 | `answer_failed` / `generation_failed` | → operator. A failed `turn_ref` replays as `generation_failed` forever — never loop. |
| 503 | `autopilot_disabled` | **kill switch** → operator; open the tenant circuit; half-open probe with `GET /config` every 30–60 s. |
| other 5xx / timeout / connection error | | bot mode: one safe retry (same `turn_ref`), then → operator. Copilot: retry the mirror later. |

### Idempotency

`turn_ref` = our message id (unique per tenant across customer, operator and bot messages). A
write CQ has already stored is a **replay**: same identifiers back, `idempotent_replay: true`, body
ignored, nothing regenerated or re-metered. An `/answer` replay returns the **same stored answer**
(200), `answer_in_flight` (409) while still generating, or `generation_failed` (502) if the first
attempt failed. Metering happens after the replay check, so retries never burn the tenant's cap.

### The flow we are implementing

1. New conversation for tenant T: if our `ai_bot_enabled(T)` **and** CQ `config.autopilot_enabled`
   → state `bot`; send `greeting[locale]` from the config (static text, no CQ call, **not** mirrored
   to CQ). Otherwise the conversation starts `handed_off` and goes to the operator queue.
2. Customer message in `bot`: `POST /answer` with `turn_ref` = message id → send `reply.text` →
   mirror the sent bot reply with `POST /turns {role:"bot", turn_ref:<bot message id>}` → if
   `handoff.recommended` → `handed_off`, show `summary`/`reason`/`goal` to the operator queue.
   On 503 / 409 `autopilot_not_enabled` / 429 / 5xx / timeout: send `refusal_copy[locale]` from the
   cached config as our own message, → `handed_off` with reason `http_<status>:<code>`.
3. In `handed_off`: every customer message → `POST /turns {role:"customer"}` → `suggest_ref` →
   operator console polls `GET /suggestions/{suggest_ref}` (or streams via a ticket) and shows
   `tier1` cards + `suggestions`; every operator message → `POST /turns {role:"operator"}` (mirror);
   when the operator shows / inserts / edits / sends a draft → `POST /feedback`.
4. `handed_off` never returns to `bot`. `closed` calls nothing further; GDPR erasure →
   `DELETE /v1/chat/conversations/{external_ref}`. Threads that never touched CQ and that we want
   mirrored → `conversations:sync` in batches of ≤ 100.

---

## Deliverables (in order — stop and report after 1 before writing any code)

1. **Discover this codebase first and report before coding.** Find and summarise, with file paths:
   the inbound message pipeline (where a customer message becomes a stored message and where
   outbound messages are sent per channel); the tenant model and how per-tenant settings are
   stored; the operator queue / assignment model and the operator console's data source for a
   conversation; how configuration and secrets are loaded; the HTTP client, retry and job/queue
   conventions already in use; the test framework and how integration tests mock external HTTP;
   and how per-tenant admin toggles are exposed in the admin UI. Propose where each deliverable
   below should live and which existing abstractions it should reuse. **Wait for my confirmation.**

2. **Configuration.** `CQ_BASE_URL` (default `https://ai.communiq.ge/api`); `CQ_API_KEY` loaded as
   a **server secret** through this repo's existing secrets mechanism — never in client bundles,
   logs, URLs or the repository; per-tenant `cq_client_id` (uuid, nullable) and `ai_bot_enabled`
   (bool, default false) with an **admin toggle** in the existing tenant admin UI, and a per-tenant
   cached copy of `GET /v1/chat/config` (`version`, `autopilot_enabled`, `greeting`, `refusal_copy`,
   `languages`, `fetched_at`; TTL ~60 s, invalidated on 409 `autopilot_not_enabled` / 503). A tenant
   without `cq_client_id` must never produce a CQ call. A single `CqClient` module wraps every
   endpoint above, sets the headers (`X-CQ-Tenant` **and** `X-CQ-Expect-Tenant` = `cq_client_id`,
   `X-CQ-End-User` = our opaque end-user id), asserts the echoed `client_id`, and maps every status
   in the table to a typed result — no other code touches HTTP for CQ.

3. **Bot-first flow as an explicit conversation state machine** with states `bot` | `handed_off` |
   `closed` persisted on the conversation. Two-sided enable check at conversation start; greeting
   from config with fallback `greeting[locale]` → `greeting["en"]` → `greeting[languages[0]]` → none;
   locale mapping from our channel/customer locale to `ka|ru|en` (default the tenant's
   `languages[0]`); the `/answer` call per customer message; sending `reply.text` verbatim (strip or
   footnote `[n]` markers per channel; never add a disclosure); mirroring the sent bot reply as
   `role: "bot"`; the handoff transition on `handoff.recommended` **and** on 503 / 409 / 429 / 5xx /
   timeout with the operator shown `summary`, `reason`, `goal` (or the HTTP reason). The transition
   out of `bot` is one-way. Use the blocking call by default; use `?stream=1` only for a web widget
   that can replace already-rendered text, and never on channels that cannot edit a sent message.

4. **Copilot after handoff.** `POST /turns` for every customer (`role: "customer"`) and operator
   (`role: "operator"`) message with `turn_ref` = message id; store `suggest_ref` on the customer
   message; the operator console reads `GET /suggestions/{suggest_ref}` (poll at `retry_after_ms`,
   give up after ~20 s) and renders `tier1` cards, `suggestions` (with `kind`), and the `handoff`
   block; a regenerate action with the five transforms; `POST /feedback` on `shown`, `inserted`,
   `edited_sent`, `sent_asis`, `ignored` with `variant_index` and `final_text`. Stream tickets only
   if the console already streams.

5. **Persistence of CQ refs and envelopes.** On the conversation: CQ state, CQ `conversation_id`,
   handoff record (at, reason, summary, goal, source). On each customer message: `turn_ref`, CQ
   `turn_id`, `suggest_ref`, HTTP status + `code`, latency, `idempotent_replay`, and the full
   envelope JSON. On each bot reply: the `suggest_ref` it came from and whether it was a refusal.
   On each operator action: the feedback payload sent. Migrations follow this repo's conventions.

6. **Idempotency, timeouts, retries, circuit breaker.** `turn_ref` = our message id everywhere;
   `Idempotency-Key` = the same id. Timeouts: `/answer` ~45 s, `/turns` 5 s, reads 3–5 s,
   `/config` 10 s. Retries: `/answer` once on timeout/connection error with the same `turn_ref`,
   then hand off; `answer_in_flight` with backoff up to 90 s; never retry other 4xx, 429 or 502 on
   the same conversation; `/turns`, `/feedback`, `:sync`, `DELETE` retried with backoff (idempotent).
   A **per-tenant circuit breaker** opens on 401/403/503/5xx bursts; while open, new conversations
   go straight to the operator queue and bot conversations hand off on their next message;
   half-open probe = `GET /config`; close when `/answer` succeeds. All failure paths end in the
   operator queue, never in a silent customer.

7. **Logging of usage and grounding.** No metering code (CQ meters tokens per tenant itself). Log
   one structured line per envelope keyed by `suggest_ref`: `usage.model`, `usage.latency_ms`,
   `grounding.grounded`, `grounding.reason`, `grounding.method`, `grounding.top_score`,
   `grounding.hit_count`, `handoff.recommended`, `handoff.reason`, HTTP status/code, and our
   tenant + conversation ids — for reconciliation with CQ's ledger and for "why did the bot refuse".
   Never log `X-CQ-Key`, stream tickets, or message content at info level.

8. **Tests.** Unit tests for `CqClient` (headers, `client_id` assertion, every status → typed
   result, timeout/retry policy, SSE frame parsing including `: ping`, the two `error` shapes, and
   replacing deltas with `done.turn.reply.text`) and for the state machine (every transition in the
   flow above, including 503/409/429/5xx/timeout handoffs, `answer_in_flight` retry, greeting
   fallback order, one-way handoff, no CQ call for an unmapped tenant, no greeting mirrored).
   Integration tests against a **staging tenant** with CQ **mocked** (recorded fixtures of the
   responses above), covering: bot answers → customer receives the text once; refusal → handoff →
   operator sees summary → next message produces a copilot draft → feedback sent; kill switch (503)
   → handoff + circuit open + recovery; duplicate delivery → single customer message.

9. **Staging rollout and a kill-switch drill.** A runbook (in this repo's docs) that: stores the
   credential; maps `<PILOT_TENANT>` to `<CQ_CLIENT_ID_PILOT>`; probes `GET /v1/chat/health` and
   `GET /v1/chat/config` from the staging host at `<STAGING_CHAT_URL>`; verifies the CQ operator has
   published KB documents and enabled autopilot for the pilot tenant; runs the three canonical
   probes (grounded question, off-KB question, "I want a human"); then performs the **kill-switch
   drill**: the CQ operator flips the switch → within ~5 s `/answer` returns 503 → we hand off,
   open the circuit, and nothing is sent as the bot → the switch is flipped back → the probe closes
   the circuit → the bot resumes for new conversations. Record timings and alert thresholds
   (401/403/400/422 = integration bug; 429 rate; 5xx/503; `handoff.reason` distribution).

10. **Explicitly do NOT build:** token metering or billing (CQ's `llm_usage` owns it); any knowledge
    base, retrieval, embeddings or document storage (CQ owns the KB — documents are shared with the
    bot inside CQ); prompts, personas, refusal or disclosure wording (CQ appends the disclosure);
    a WebSocket transport; a second copy of the CQ config beyond the cache in deliverable 2; any
    call to CQ endpoints outside `/v1/chat/`.

Secrets and ids only I have: `CQ_API_KEY = <CQ_API_KEY>` (server secret; ask me — do not invent),
`<CQ_CLIENT_ID_PILOT>` for `<PILOT_TENANT>`, staging chat backend `<STAGING_CHAT_URL>`.

---

## Acceptance criteria

- [ ] No CQ call is ever made for a tenant without `cq_client_id`, or with `ai_bot_enabled` off for the bot path.
- [ ] `X-CQ-Key` appears only in server-side HTTP headers; never in logs, URLs, client bundles or git.
- [ ] Every CQ call sends `X-CQ-Tenant` and `X-CQ-Expect-Tenant` = the tenant's `cq_client_id`; the echoed `client_id` is asserted; a 403 raises an alert and hands off.
- [ ] A new conversation gets the bot only when both flags are true; the greeting comes from `config.greeting` with the documented fallback and is not mirrored to CQ.
- [ ] In `bot`, each customer message produces exactly one `POST /answer` with `turn_ref` = message id; the customer receives `reply.text` verbatim (no extra disclosure); the sent bot reply is mirrored as `role: "bot"`.
- [ ] `handoff.recommended: true` → state `handed_off`, operator sees `summary`, `reason` (and `goal`); the bot never answers that conversation again.
- [ ] 503 `autopilot_disabled`, 409 `autopilot_not_enabled`, 429, 5xx and timeouts each hand off with the tenant's `refusal_copy[locale]` sent by us and the reason recorded; 409 `answer_in_flight` retries instead.
- [ ] After handoff, every customer and operator message is mirrored via `POST /turns`; the operator console shows `tier1` and `suggestions` from `GET /suggestions`; `POST /feedback` fires on every draft interaction.
- [ ] Duplicate deliveries and retries never produce a second customer message or a second CQ generation (idempotent on `turn_ref`).
- [ ] The per-tenant circuit breaker opens on 401/403/503/5xx bursts, sends new conversations to operators, half-opens on `GET /config`, and closes on a successful `/answer`.
- [ ] Envelopes, `suggest_ref`s, CQ `conversation_id`/`turn_id`, handoff records and feedback payloads are persisted; a structured usage/grounding log line exists per envelope.
- [ ] Deltas (if streamed) are only rendered in a web widget and are replaced by `done.turn.reply.text`.
- [ ] Unit tests cover the client and every state-machine transition; integration tests run against the staging tenant with CQ mocked; all green in CI.
- [ ] The staging runbook has been executed, including the kill-switch drill, with timings recorded.
- [ ] Nothing from deliverable 10 exists in the diff.
