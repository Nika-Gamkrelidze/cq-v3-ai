"""The chat site's server-to-server surface (ADR-001, endpoints 1-10).

Everything the chat product calls lives under ONE prefix so exactly one nginx location block
exists per config file. Read `docs/ADR-001-conversational-ai.md` before changing anything here;
the two ideas this file exists to implement are easy to undo by accident:

1. **The work happens on message ARRIVAL, not when the operator asks.** `POST /turns` writes the
   mirror row, claims a suggestion, fires a BackgroundTask and returns 202 in ~15 ms. The
   latency-critical read (`GET /suggestions/{ref}`) is then ONE indexed SELECT — it never touches
   the LLM, the embedder or Anthropic. That is the entire latency architecture. Anything that
   makes the read path do real work deletes the product promise, and no amount of prompt or model
   tuning gets it back.
2. **Handlers receive no tenant identifier other than `p.client_id`.** There is deliberately no
   `client_id` path/query/body parameter that a handler is allowed to scope by. `X-CQ-Tenant`
   selected the tenant during credential resolution (narrowing inside the grant table);
   `X-CQ-Expect-Tenant` is an *assertion* checked by `assert_expected_tenant` on every write and
   is never used to pick a row. Every response echoes the resolved `client_id` so the chat site
   can detect its own mapping bug rather than discovering it by reading somebody else's customers.

Auth: a scoped integration credential (`X-CQ-Key` + `X-CQ-Tenant`). `Principal.is_tenant` is
False for it, so nothing in this file is reachable with a tenant key and nothing in `/kb`,
`/analyze` or `/scoring` is reachable with a chat key.

`POST /v1/chat/answer` (ADR endpoint 7, the public autopilot) is at the bottom. It is the same
machinery as the copilot with different *policy*: which engine runs is decided by the suggestion
row's `mode`, so there is exactly one persistence helper, one SSE framer and one envelope
producer for both surfaces. `/v1/curation/*` (endpoint 11) is P2 and lives elsewhere.

Errors keep FastAPI's exact `{"detail": "<string>"}` shape because `CQ.readResp` (brand.js:191)
requires `detail` to be a string; a machine-readable `code` is only ever added as a SIBLING key.
"""
import asyncio
import hashlib
import json
import logging
import time
import uuid

from fastapi import (APIRouter, BackgroundTasks, Depends, Header, HTTPException,
                     Query, Request)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from ..services import chat, chat_store, limits, llm, settings_store
from ..services.auth import (Principal, assert_expected_tenant, make_token,
                             resolve_principal, verify_token)

log = logging.getLogger("cq")

router = APIRouter(prefix="/v1/chat", tags=["chat"])

# ---- tunables --------------------------------------------------------------
MAX_TURN_CHARS = 8_000            # one inbound message; longer is a paste bomb, not a question
MAX_SYNC_CONVERSATIONS = 100      # ADR endpoint 8
MAX_SYNC_TURNS = 200              # per conversation
HISTORY_TURNS = 8                 # window handed to the engine
SUGGEST_RETRY_MS = 350            # what a `running` read tells the caller to wait
STREAM_PING_S = 15.0              # `: ping` cadence — nginx read timeout is 3600s, browsers less
STREAM_POLL_MS = 250              # attach-to-in-flight poll interval
STREAM_DEADLINE_S = 90.0          # hard stop, so a stuck generation cannot pin a connection
TICKET_TTL_S = 60

# The one value that selects the public engine. Stored on the suggestion row by
# `claim_suggestion`, and read back by `_drive` — see `_runner_for`.
MODE_AUTOPILOT = "autopilot"

# What POST /turns — the copilot ingest, authorised by `chat:turn` — is allowed to write into
# that column. Autopilot is deliberately NOT in it: see `TurnIn.mode`.
TURN_MODES = ("assist",)

# Default caps, overridable per tenant via `chat_configs.settings.limits`.
DEFAULT_TENANT_PER_MINUTE = 120
DEFAULT_ENDUSER_PER_HOUR = 120
# The public bot is metered on its own dimension: an autopilot turn is a full answer plus a
# possible handoff summary, it is not reviewed by anybody, and it is the surface an abusive
# end user reaches directly. Sharing `chat_turns`' counter would let copilot traffic mask a
# runaway public bot (and vice versa) in exactly the incident where the numbers matter.
DEFAULT_ANSWER_PER_MINUTE = 60
DEFAULT_ANSWER_ENDUSER_PER_HOUR = 60

# Suggestions whose generation is already running IN THIS PROCESS. Its only job is to stop
# `GET /stream` from starting a second, duplicate generation for a suggest_ref that a
# BackgroundTask (or another stream) is already producing — the stream attaches by polling the
# row instead. Correct ONLY because the API runs a single uvicorn worker (no --workers in
# backend/Dockerfile); with more than one worker this becomes per-worker and two workers could
# both generate. Same constraint as every other in-process cache in the codebase — see the
# "What becomes harder" section of ADR-001.
_INFLIGHT: set[tuple[str, str]] = set()

# Consumed stream tickets. Tickets are single-use (ADR endpoint 3) and live 60 s, so this stays
# tiny and is pruned opportunistically. Single-worker caveat as above; the failure mode of a
# miss is a ticket usable twice within its 60 s window, not a credential leak.
_USED_TICKETS: dict[str, float] = {}


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def require_chat(*scopes: str):
    """Dependency factory: an ACTIVE integration credential holding any of `scopes`.

    Mirrors `partner.require_tenant`, but scope-checked — the whole point of the credential is
    that it is not a skeleton key. `kb:write` / `kb:delete` / `scoring:write` / `admin:*` are
    never issuable (chat_credentials.validate_scopes), so no route here can be widened into the
    KB write path by handing out a broader key.
    """
    async def dep(p: Principal = Depends(resolve_principal)) -> Principal:
        if p.kind == "integration" and any(s in p.scopes for s in scopes):
            return p
        # One message for every failure mode (wrong kind, missing scope). A caller must not be
        # able to probe which scopes a key holds.
        raise HTTPException(status_code=401,
                            detail="A scoped CQ integration key is required.")
    return dep


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _err(status: int, detail: str, code: str) -> JSONResponse:
    """FastAPI's error body, plus a machine key as a SIBLING (never nested in `detail`)."""
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


def _json(value):
    """asyncpg hands jsonb back as `str` unless a codec is registered — normalize both."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _limit(cfg: dict, key: str, default: int) -> int:
    """A per-tenant cap out of the chat config, tolerant of where it was stored."""
    for holder in (cfg.get("limits"), (cfg.get("settings") or {}).get("limits")):
        if isinstance(holder, dict) and holder.get(key) is not None:
            try:
                return int(holder[key])
            except (TypeError, ValueError):
                log.warning("chat config has a non-numeric limits.%s: %r", key, holder[key])
    return default


def _enduser_scope(p: Principal, end_user: str | None) -> str | None:
    """Metering key for one end customer. Hashed: `usage_counters.scope_key` is long-lived
    plaintext and an end-user id is the chat site's identifier, not ours. NEVER authorization."""
    if not (end_user or "").strip():
        return None
    digest = hashlib.sha256(end_user.strip().encode()).hexdigest()[:16]
    return f"enduser:{p.integration_id or p.client_id}:{digest}"


async def _reserve(p: Principal, cfg: dict, end_user: str | None, *, kind: str,
                   minute_key: str, minute_default: int,
                   hour_key: str, hour_default: int) -> None:
    """The two metering dimensions ADR-001 §Security-bar item 4 requires on ingest.

    `reserve_counter` counts even when uncapped, so cost is visible from the first turn rather
    than from whenever somebody remembers to configure a limit. It raises 429 itself.

    Tenant-per-minute catches a runaway integration; end-user-per-hour catches one abusive
    customer without taking the tenant's whole bot down with them. Both dimensions are needed:
    either one alone has an obvious bypass.
    """
    await limits.reserve_counter(f"tenant:{p.client_id}", kind,
                                 _limit(cfg, minute_key, minute_default), bucket="minute")
    scope = _enduser_scope(p, end_user)
    if scope:
        await limits.reserve_counter(scope, kind,
                                     _limit(cfg, hour_key, hour_default), bucket="hour")


async def _reserve_turn(p: Principal, cfg: dict, end_user: str | None) -> None:
    """Meter one copilot ingest."""
    await _reserve(p, cfg, end_user, kind="chat_turns",
                   minute_key="tenant_per_minute", minute_default=DEFAULT_TENANT_PER_MINUTE,
                   hour_key="enduser_per_hour", hour_default=DEFAULT_ENDUSER_PER_HOUR)


async def _reserve_answer(p: Principal, cfg: dict, end_user: str | None) -> None:
    """Meter one public autopilot answer, on its own `chat_answer` kind."""
    await _reserve(p, cfg, end_user, kind="chat_answer",
                   minute_key="answer_tenant_per_minute",
                   minute_default=DEFAULT_ANSWER_PER_MINUTE,
                   hour_key="answer_enduser_per_hour",
                   hour_default=DEFAULT_ANSWER_ENDUSER_PER_HOUR)


def _envelope(row: dict) -> dict:
    """The Turn envelope for a finished suggestion.

    Single call site on purpose: the blocking JSON body, the terminal SSE `done` payload and a
    future WS `turn.completed` frame must be byte-identical, and they are only byte-identical if
    exactly one function builds them. That function is the engine's `build_turn_envelope`; this
    router never assembles an envelope of its own.

    Which is why the normal path here is a *read*, not a rebuild: `finish_suggestion` stored the
    engine's envelope verbatim, so returning it is byte-identity by construction. The rebuild
    below is the fallback for a row written before that column existed (or by an older process
    mid-deploy) — it is lossy exactly where the columns are (no turn_ref, no reply), and that is
    the honest failure rather than a fabricated envelope.
    """
    stored = _json(row.get("envelope")) or {}
    if stored:
        return stored
    env = chat.build_turn_envelope(
        client_id=str(row.get("client_id") or ""),
        suggest_ref=str(row.get("suggest_ref") or ""),
        conversation_ref=str(row.get("conversation_id") or "") or None,
        locale=row.get("locale") or "en",
        tier1=_json(row.get("tier1")) or [],
        suggestions=_json(row.get("suggestions")) or [],
        handoff=_json(row.get("handoff")) or None,
        stages=_json(row.get("stages")) or None,
        model=row.get("model"))
    env["grounding"] = {**env["grounding"], **(_json(row.get("grounding")) or {})}
    env["citations"] = _json(row.get("citations")) or []
    return env


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class TurnIn(BaseModel):
    conversation_ref: str = Field(min_length=1, max_length=200)
    content: str
    turn_ref: str | None = Field(default=None, max_length=200)
    role: str = "customer"                 # customer | operator | bot
    channel: str = "web"                   # opaque — a fourth channel is zero CQ work
    locale: str | None = None              # ka | ru | en
    # COPILOT MODES ONLY. `mode` is written onto the suggestion row and read back by
    # `_runner_for`, so accepting an arbitrary string here made this endpoint a second door
    # into the public answer engine: a credential holding only `chat:turn` could post
    # `mode: "autopilot"` and get `run_answer` — defeating the `chat:answer` scope, skipping
    # this router's 503/409 pre-checks, and billing the run to the `chat_turns` meter, which
    # is exactly the "copilot traffic masks a runaway public bot" case the separate
    # `chat_answer` counter exists to prevent. The public bot has its own endpoint; asking for
    # it here is a validation error (422), not a silent upgrade.
    mode: str = "assist"                   # assist — human in the loop (see POST /answer)
    customer_ref: str | None = None        # opaque end-user id, metering only
    precompute: bool = True                # false => the caller will drive GET /stream instead

    @field_validator("mode")
    @classmethod
    def _copilot_mode_only(cls, v: str) -> str:
        v = (v or "assist").strip().lower()
        if v not in TURN_MODES:
            raise ValueError(
                "mode must be 'assist'; the public bot is POST /v1/chat/answer, which "
                "requires the chat:answer scope.")
        return v


class AnswerIn(BaseModel):
    # Shaped like TurnIn minus the knobs that make no sense with no human in the loop: there is
    # no `role` (an autopilot answer is always a reply to a CUSTOMER — accepting a role would
    # let the caller have the bot answer its own operator), no `mode` (this endpoint IS the
    # mode) and no `precompute` (the caller is waiting for the answer).
    conversation_ref: str = Field(min_length=1, max_length=200)
    content: str
    turn_ref: str | None = Field(default=None, max_length=200)
    channel: str = "web"                   # web | whatsapp | messenger | …
    locale: str | None = None
    customer_ref: str | None = None
    # Envelope fields that are attacker-controlled on social channels exactly as much as the
    # message body is. They are accepted so `chat_prompts.wrap_untrusted` can quarantine them
    # explicitly, which is strictly safer than the chat site concatenating them into `content`.
    display_name: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=200)
    attachment: str | None = Field(default=None, max_length=200)


class RegenerateIn(BaseModel):
    suggest_ref: str = Field(min_length=1, max_length=200)
    transform: str | None = None           # shorter | warmer | formal | to_ru | to_ka


class FeedbackIn(BaseModel):
    suggest_ref: str = Field(min_length=1, max_length=200)
    action: str                            # shown|inserted|edited_sent|sent_asis|ignored
    variant_index: int | None = None
    final_text: str | None = None


class TicketIn(BaseModel):
    suggest_ref: str = Field(min_length=1, max_length=200)


class SyncTurn(BaseModel):
    role: str
    content: str
    turn_ref: str | None = None
    lang: str | None = None


class SyncConversation(BaseModel):
    # Mandatory and per-conversation on purpose. A chat-site partitioning bug would otherwise
    # write tenant B's corpus under tenant A while every CQ handler still satisfies its own
    # client_id invariant — and P2's curation would then turn that into a human-approved,
    # permanent cross-tenant KB contamination. The whole batch is rejected on any mismatch.
    client_id: str
    external_ref: str = Field(min_length=1, max_length=200)
    channel: str = "web"
    locale: str | None = None
    customer_ref: str | None = None
    turns: list[SyncTurn] = Field(default_factory=list)


class SyncIn(BaseModel):
    conversations: list[SyncConversation]


# --------------------------------------------------------------------------- #
# 0. Transport probe (P0). Path must not move — nginx's /api/v1/chat/ location and the
#    post-deploy smoke both hit it, and it is the only thing proving the proxy streams before
#    any product depends on it. Deliberately trivial and unauthenticated.
# --------------------------------------------------------------------------- #
@router.get("/health", include_in_schema=False)
async def chat_transport_health():
    return {"status": "ok", "transport": "chat"}


# --------------------------------------------------------------------------- #
# 1. Ingest — the latency architecture
# --------------------------------------------------------------------------- #
@router.post("/turns", status_code=202)
async def post_turn(
    body: TurnIn,
    bg: BackgroundTasks,
    p: Principal = Depends(require_chat("chat:turn")),
    x_cq_expect_tenant: str | None = Header(default=None, alias="X-CQ-Expect-Tenant"),
    x_cq_end_user: str | None = Header(default=None, alias="X-CQ-End-User"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Called for EVERY inbound message, on every channel, autopilot or human-staffed.

    Returns 202 with a `suggest_ref` the operator UI can read back later. Generation happens in
    a BackgroundTask so this handler stays ~15 ms — by the time a human has read the customer's
    message (2-5 s) the suggestion is already a row in Postgres.

    Idempotent on `(client_id, turn_ref)` and on `(client_id, Idempotency-Key)`: a replay
    returns the same identifiers and does NOT fire a second generation (precompute roughly
    doubles LLM calls by design; doubling it again on every retry is not a rounding error).
    """
    assert_expected_tenant(p, x_cq_expect_tenant)
    content = (body.content or "").strip()
    if not content:
        return _err(400, "content must not be empty.", "empty_content")
    if len(content) > MAX_TURN_CHARS:
        return _err(413, f"content exceeds {MAX_TURN_CHARS} characters.", "content_too_large")

    cfg = await chat_store.get_chat_config(p.client_id)

    conversation_id = await chat_store.upsert_conversation(
        p.client_id, body.conversation_ref, channel=body.channel, locale=body.locale,
        mode=body.mode, customer_ref=x_cq_end_user or body.customer_ref)
    turn_ref = body.turn_ref or idempotency_key or uuid.uuid4().hex
    turn_id, turn_is_new = await chat_store.append_turn(
        p.client_id, conversation_id, turn_ref=turn_ref, idempotency_key=idempotency_key,
        role=body.role, content=content, lang=body.locale, source="live")

    # Metered AFTER idempotency is resolved, so one logical message costs one unit no matter how
    # many times the chat site retries it. Reserving first (the obvious ordering) let an
    # aggressive retry loop 429 the tenant on turns that were already stored — a self-inflicted
    # outage. The mirror write that precedes it is idempotent and does not spend money; the
    # generation below, which does, is still gated.
    if turn_is_new:
        await _reserve_turn(p, cfg, x_cq_end_user or body.customer_ref)

    # Only a customer message is worth suggesting a reply to; an operator's own message is
    # mirrored for curation and history only.
    suggest_ref = None
    precomputing = False
    if body.role == "customer":
        # Derived from turn_id, so a replay of the same turn deterministically claims the same
        # suggestion row and `is_new` (not a second lookup) is what suppresses the duplicate
        # generation. Its own namespace — sharing turn_ref's would collide with :sync.
        suggest_ref = f"sg_{turn_id}"
        _, claim_is_new = await chat_store.claim_suggestion(
            p.client_id, suggest_ref, conversation_id, turn_id,
            locale=body.locale, mode=body.mode, integration_id=p.integration_id)
        if claim_is_new and body.precompute:
            key = (str(p.client_id), suggest_ref)
            # Marked here, not inside the task: a client fast enough to open the stream before
            # the task is scheduled must still see it as in-flight and attach rather than
            # generate a second time.
            _INFLIGHT.add(key)
            bg.add_task(_precompute, str(p.client_id), conversation_id, suggest_ref,
                        body.locale, body.mode, p.integration_id, cfg)
            precomputing = True

    return {
        "client_id": str(p.client_id),
        "conversation_id": str(conversation_id),
        "conversation_ref": body.conversation_ref,
        "turn_id": str(turn_id),
        "turn_ref": turn_ref,
        "suggest_ref": suggest_ref,
        "precompute": precomputing,
        "idempotent_replay": not turn_is_new,
        "retry_after_ms": SUGGEST_RETRY_MS if precomputing else 0,
    }


async def _precompute(client_id: str, conversation_id: str, suggest_ref: str,
                      locale: str | None, mode: str, integration_id: str | None,
                      cfg: dict) -> None:
    """Drain the generation for its side effects. Runs AFTER the 202 has been sent.

    The engine deliberately never touches the database (see services/chat.py), so persistence
    is the ROUTER's job and lives in `_drive` — shared with the live SSE path so a suggestion
    lands in exactly the same way whichever transport produced it. This wrapper only has to
    consume the events and make sure a crash marks the row instead of leaving it `running`
    forever — the worker's reaper would eventually sweep it, but a caller polling in the
    meantime deserves a terminal state now.
    """
    key = (str(client_id), suggest_ref)
    try:
        ctx = await _build_context(client_id, conversation_id, suggest_ref, locale, mode,
                                   integration_id, cfg)
        async for _ in _drive(str(client_id), suggest_ref, ctx):
            pass
    except llm.LLMBusyError:
        log.info("precompute admission-rejected suggest_ref=%s", suggest_ref)
        await _safe_fail(client_id, suggest_ref, "busy")
    except Exception as exc:  # noqa: BLE001 — a background task must never raise into the loop
        log.warning("precompute failed suggest_ref=%s: %s", suggest_ref, exc)
        await _safe_fail(client_id, suggest_ref, str(exc))
    finally:
        _INFLIGHT.discard(key)


def _runner_for(mode: str | None):
    """Which engine a suggestion row belongs to.

    Derived from the row's `mode` rather than passed down from the handler on purpose. The
    stream endpoint can be asked to drive a row whose originating request is long gone (a
    ticket opened after a restart, `precompute: false`), and if the runner were a handler
    argument that path would default to the copilot — running `run_suggest`, which retrieves
    with `visibility=None`, over a row whose output is going to a member of the public. The
    mode is written once, by `claim_suggestion`, and every later driver reads it.
    """
    return chat.run_answer if (mode or "") == MODE_AUTOPILOT else chat.run_suggest


async def _drive(client_id: str, suggest_ref: str, ctx: chat.ChatContext):
    """Run the engine, PERSIST its result, and yield `(event_name, payload)` for the wire.

    The one place a generation is turned into a row, and therefore the one place that decides
    whether the product works at all: the engine yields a terminal `done` carrying the whole
    Turn envelope and then forgets it, so if nobody files it here the suggestion sits in
    'running' until the reaper errors it out — which is precisely what the warm-path promise
    is made of.

    Two details are load-bearing:

      * The engine's own `open` is dropped and the sequence is re-numbered from the router's
        `open` (seq 0). Both layers legitimately announce a stream start; emitting both put two
        `open` frames and a restarted counter on the wire, which is a client bug generator.
      * The row is written BEFORE `done` is yielded. A client that disconnects the instant it
        reads `done` must not be the reason the suggestion was never stored.
    """
    n = 0
    envelope: dict | None = None
    try:
        async for ev in _runner_for(ctx.mode)(ctx):
            if ev.name == "open":
                continue
            n += 1
            if ev.name == "done":
                envelope = ev.data
                await _persist(client_id, suggest_ref, envelope)
                yield ("done", {"turn": envelope, "seq": n})
                continue
            yield (ev.name, {**ev.data, "seq": n})
    except Exception as exc:  # noqa: BLE001 — every exit path must leave a terminal row
        await _safe_fail(client_id, suggest_ref,
                         "Service is busy; retry shortly."
                         if isinstance(exc, llm.LLMBusyError) else str(exc))
        raise
    if envelope is None:
        # The engine returned without a terminal event. Not reachable today (every branch of
        # run_suggest ends in `done`), but leaving the row 'running' on an unknown future path
        # is a 120-second lie to the operator.
        await _safe_fail(client_id, suggest_ref, "Generation produced no result.")


def _state_for(envelope: dict) -> str:
    """'ready' | 'refused'. One definition, because the stored row's state and the state the
    blocking answer response reports must never be able to disagree."""
    return "ready" if bool((envelope.get("grounding") or {}).get("grounded")) else "refused"


async def _persist(client_id: str, suggest_ref: str, envelope: dict) -> None:
    """File the finished envelope. 'refused' is a SUCCESS state: the gate declined before any
    Claude call, the refusal card is real output, and calling that an error would make every
    ungrounded question look like an outage."""
    try:
        await chat_store.finish_suggestion(client_id, suggest_ref, envelope=envelope,
                                           state=_state_for(envelope))
    except Exception as exc:  # noqa: BLE001 — a storage failure must not swallow the answer
        log.warning("could not store suggestion %s: %s", suggest_ref, exc)


async def _safe_fail(client_id: str, suggest_ref: str, error: str) -> None:
    try:
        await chat_store.fail_suggestion(client_id, suggest_ref, error)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not mark suggestion %s failed: %s", suggest_ref, exc)


async def _build_context(client_id: str, conversation_id: str, suggest_ref: str,
                         locale: str | None, mode: str, integration_id: str | None,
                         cfg: dict, *, conversation_ref: str | None = None,
                         turn_ref: str | None = None, channel: str = "web",
                         envelope: dict | None = None) -> chat.ChatContext:
    """Everything the engine needs, assembled OFF the request path.

    The model id is read from `settings_store` — never hardcoded, because the admin panel owns
    it (root CLAUDE.md §4).

    The trailing keyword arguments are the inbound envelope, and only a handler that still has
    the request can supply them: `copilot_suggestions` has no column for `channel`,
    `display_name` or the chat site's own refs, so a driver reconstructing a context from the
    row (a ticket stream) legitimately passes none of them. They are additive and defaulted for
    that reason. Everything inside `envelope` is attacker-controlled and is quarantined by
    `chat_prompts.wrap_untrusted` — it is never read as configuration here.
    """
    messages = await chat_store.recent_turns(client_id, conversation_id, HISTORY_TURNS)
    app_cfg = await settings_store.get_effective()
    # Last-resort locale: the tenant's own first configured language, not a hardcoded one. A
    # caller that sends `locale` always wins; this only covers a turn that arrived without one.
    fallback = str((cfg.get("languages") or ["ka"])[0] or "ka")
    return chat.ChatContext(
        client_id=str(client_id), conversation_id=str(conversation_id),
        suggest_ref=suggest_ref, locale=locale or fallback,
        messages=messages, cfg=cfg, api_key=app_cfg["anthropic_api_key"],
        model=app_cfg["llm_model"], mode=mode, integration_id=integration_id,
        conversation_ref=conversation_ref, turn_ref=turn_ref, channel=channel,
        envelope=envelope or {})


# --------------------------------------------------------------------------- #
# 2. The warm path — ONE indexed SELECT
# --------------------------------------------------------------------------- #
@router.get("/suggestions/{suggest_ref}")
async def get_suggestion(suggest_ref: str,
                         p: Principal = Depends(require_chat("chat:suggest"))):
    """The read the operator's composer makes. Target ~25 ms p50.

    Nothing here embeds, retrieves or calls Anthropic — if it ever does, the precompute
    contract has been broken somewhere upstream and the product promise is gone.
    """
    row = await chat_store.get_suggestion(p.client_id, suggest_ref)
    if row is None:
        return _err(404, "No such suggestion.", "not_found")
    state = row.get("state")
    if state == "running":
        return {"client_id": str(p.client_id), "suggest_ref": suggest_ref,
                "state": "running", "retry_after_ms": SUGGEST_RETRY_MS}
    if state == "error":
        return {"client_id": str(p.client_id), "suggest_ref": suggest_ref,
                "state": "error", "detail": row.get("error") or "Generation failed",
                "code": "generation_failed"}
    return {"client_id": str(p.client_id), "suggest_ref": suggest_ref,
            "state": state, "turn": _envelope(row)}


# --------------------------------------------------------------------------- #
# 3. Stream tickets — EventSource cannot set headers
# --------------------------------------------------------------------------- #
@router.post("/stream-tickets")
async def create_stream_ticket(body: TicketIn,
                               p: Principal = Depends(require_chat("chat:suggest"))):
    """Mint a 60 s single-use ticket for `GET /chat/stream`.

    `EventSource` cannot send headers, so without this the integration credential would travel
    in a query string — straight into nginx access logs and browser history. The ticket is
    scoped to ONE suggest_ref and carries the resolved client_id, so it is useless for anything
    else. Reuses the existing HMAC signer; the same primitive a browser WS handshake will need.
    """
    ticket = make_token({"scope": "chat_stream", "client_id": str(p.client_id),
                         "suggest_ref": body.suggest_ref,
                         "integration_id": p.integration_id, "jti": uuid.uuid4().hex},
                        ttl_hours=TICKET_TTL_S / 3600)
    return {"client_id": str(p.client_id), "suggest_ref": body.suggest_ref,
            "ticket": ticket, "expires_in": TICKET_TTL_S,
            "url": f"/api/v1/chat/stream?ticket={ticket}"}


def _consume_ticket(ticket: str) -> dict | None:
    """Verify + burn. Returns the payload, or None for invalid/expired/already-used."""
    payload = verify_token(ticket)
    if not payload or payload.get("scope") != "chat_stream":
        return None
    if not payload.get("client_id") or not payload.get("suggest_ref"):
        return None
    now = time.monotonic()
    for jti, expiry in list(_USED_TICKETS.items()):   # opportunistic prune; the set is tiny
        if expiry < now:
            _USED_TICKETS.pop(jti, None)
    jti = payload.get("jti") or ticket[-32:]
    if jti in _USED_TICKETS:
        return None
    _USED_TICKETS[jti] = now + TICKET_TTL_S
    return payload


# --------------------------------------------------------------------------- #
# 4. SSE
# --------------------------------------------------------------------------- #
def _sse(name: str, data: dict) -> str:
    # No `id:` field — there is deliberately NO resume protocol (ADR endpoint 4). Emitting ids
    # would invite browsers to send Last-Event-ID on auto-reconnect and imply a replay buffer
    # that does not exist; a reconnecting client re-GETs endpoint 2 and reads the finished row.
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'), default=str)}\n\n"


def _sse_response(body) -> StreamingResponse:
    """The SSE headers, in ONE place. The ticket stream and the answer stream must not be able
    to drift apart on buffering: `X-Accel-Buffering: no` is what decides whether tokens reach
    the customer as they are produced or arrive in one lump at the end (belt and braces — the
    nginx location sets it too, and only one of the two is in this repo's deploy)."""
    return StreamingResponse(body, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@router.get("/stream")
async def stream(request: Request, ticket: str = Query(...)):
    """`text/event-stream` for one suggestion: open -> grounding -> tier1 -> delta -> suggestion
    -> done, with `: ping` every 15 s.

    Ticket auth ONLY — no `X-CQ-Key` on this route, because the browser that opens it cannot
    send one and must never hold one. Everything is scoped by the client_id inside the signed
    ticket.

    Two sources, one wire format: if generation is already in flight (a BackgroundTask from
    `/turns`, or another stream), we ATTACH by polling the row rather than starting a second,
    duplicate, separately-billed generation. Otherwise we drive the engine live, which is what
    `POST /turns {"precompute": false}` is for.
    """
    payload = _consume_ticket(ticket)
    if payload is None:
        return _err(401, "Invalid, expired, or already-used stream ticket.", "bad_ticket")
    client_id = str(payload["client_id"])
    suggest_ref = str(payload["suggest_ref"])

    row = await chat_store.get_suggestion(client_id, suggest_ref)
    if row is None:
        return _err(404, "No such suggestion.", "not_found")

    key = (client_id, suggest_ref)
    live = row.get("state") == "running" and key not in _INFLIGHT
    if live:
        _INFLIGHT.add(key)

    return _sse_response(_stream_body(request, client_id, suggest_ref, row, live))


async def _stream_body(request: Request, client_id: str, suggest_ref: str,
                       row: dict, live: bool, ctx: chat.ChatContext | None = None):
    """The ONE SSE framer: `open`, then either a replay of a finished row or a pumped source.

    `ctx` is the answer endpoint handing over the context it already built from the live
    request (which carries `turn_ref`, `channel` and the inbound envelope — things the row has
    no columns for). Without it a driver rebuilds from the row, as the ticket path always has.
    """
    yield _sse("open", {"client_id": client_id, "suggest_ref": suggest_ref,
                        "state": row.get("state"), "seq": 0})

    if row.get("state") != "running":                    # already finished — replay and go
        for chunk in _terminal_events(row, seq=1):
            yield chunk
        return

    queue: asyncio.Queue = asyncio.Queue()
    if live:
        source = _engine_events(client_id, suggest_ref, row, ctx)
    else:
        source = _poll_events(client_id, suggest_ref)
    pump = asyncio.create_task(_pump(source, queue))
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=STREAM_PING_S)
            except asyncio.TimeoutError:
                # A comment line keeps the connection (and any intermediary) alive without
                # being an event the client has to understand.
                yield ": ping\n\n"
                if await request.is_disconnected():
                    break
                continue
            if item is None:
                break
            yield _sse(item[0], item[1])
    finally:
        pump.cancel()
        if live:
            _INFLIGHT.discard((client_id, suggest_ref))


async def _pump(source, queue: asyncio.Queue) -> None:
    """Feed the wire from a source iterator.

    A separate task, not `wait_for` around `__anext__`, on purpose: `wait_for` CANCELS the
    awaitable it times out on, which would throw CancelledError into the running engine
    generator on every 15-second ping and abort the Anthropic call mid-flight. Cancelling a
    `queue.get()` is free.
    """
    try:
        async for item in source:
            await queue.put(item)
    except llm.LLMBusyError:
        await queue.put(("error", {"detail": "Service is busy; retry shortly.",
                                   "code": "llm_busy"}))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("stream source failed: %s", exc)
        await queue.put(("error", {"detail": str(exc), "code": "stream_failed"}))
    finally:
        await queue.put(None)


async def _engine_events(client_id: str, suggest_ref: str, row: dict,
                         ctx: chat.ChatContext | None = None):
    """Drive the engine live and translate its ChatEvents onto the wire.

    When the caller has no context to hand over, the request that created this suggestion is
    long gone, so locale/mode/integration_id come off the row `claim_suggestion` wrote — not
    from a config default, which would answer a Russian customer in Georgian on every
    `precompute: false` turn. `mode` in particular decides which engine runs (`_runner_for`).
    """
    if ctx is None:
        cfg = await chat_store.get_chat_config(client_id)
        ctx = await _build_context(client_id, str(row.get("conversation_id")), suggest_ref,
                                   row.get("locale"), row.get("mode") or "assist",
                                   row.get("integration_id"), cfg)
    async for item in _drive(client_id, suggest_ref, ctx):
        yield item


async def _poll_events(client_id: str, suggest_ref: str):
    """Attach to a generation already running in this process.

    Deltas belong to whoever is driving the engine, so an attached reader gets the terminal
    events only — which is exactly the ADR's position that the copilot barely needs streaming
    (the warm read is 25 ms and tier-1 is a pre-rendered card list).
    """
    deadline = time.monotonic() + STREAM_DEADLINE_S
    while time.monotonic() < deadline:
        await asyncio.sleep(STREAM_POLL_MS / 1000)
        row = await chat_store.get_suggestion(client_id, suggest_ref)
        if row is None:
            yield ("error", {"detail": "Suggestion disappeared.", "code": "not_found"})
            return
        if row.get("state") != "running":
            for name, data in _terminal_payloads(row, seq=1):
                yield (name, data)
            return
    yield ("error", {"detail": "Timed out waiting for the suggestion.", "code": "timeout"})


def _terminal_payloads(row: dict, seq: int) -> list[tuple[str, dict]]:
    """grounding -> tier1 -> suggestion* -> done, replayed from the stored row.

    Every payload is projected out of the ONE stored envelope rather than out of the columns
    beside it, so a reconnecting client sees the same frames the live stream emitted — including
    `grounding.reason`, which is the whole explanation of a refusal and has no column of its own.
    """
    out: list[tuple[str, dict]] = []
    if row.get("state") == "error":
        return [("error", {"detail": row.get("error") or "Generation failed",
                           "code": "generation_failed", "seq": seq})]
    env = _envelope(row)
    out.append(("grounding", {**(env.get("grounding") or {}), "seq": seq}))
    seq += 1
    out.append(("tier1", {"cards": env.get("tier1") or [], "seq": seq}))
    seq += 1
    for variant in (env.get("suggestions") or []):
        out.append(("suggestion", {**variant, "seq": seq}))
        seq += 1
    # The terminal event carries the ENTIRE Turn envelope, byte-identically to what the
    # blocking read returns. That is what makes a future WebSocket frame purely additive.
    out.append(("done", {"turn": env, "seq": seq}))
    return out


def _terminal_events(row: dict, seq: int):
    for name, data in _terminal_payloads(row, seq):
        yield _sse(name, data)


# --------------------------------------------------------------------------- #
# 5. Regenerate
# --------------------------------------------------------------------------- #
@router.post("/regenerate", status_code=202)
async def regenerate(body: RegenerateIn, bg: BackgroundTasks,
                     p: Principal = Depends(require_chat("chat:suggest")),
                     x_cq_expect_tenant: str | None = Header(default=None,
                                                             alias="X-CQ-Expect-Tenant")):
    """Re-run generation for the same turn, optionally with a tone transform.

    A transform, not extra cards: the operator's READING cost is the bottleneck, so `shorter`,
    `warmer`, `formal`, `to_ru`, `to_ka` apply to a chosen card rather than adding a fourth one
    to scan. Returns a NEW suggest_ref — the original stays readable, which is what makes the
    edit-distance curation signal comparable.
    """
    assert_expected_tenant(p, x_cq_expect_tenant)
    src = await chat_store.get_suggestion(p.client_id, body.suggest_ref)
    if src is None:
        return _err(404, "No such suggestion.", "not_found")
    conversation_id = src.get("conversation_id")
    if not conversation_id:
        return _err(409, "That suggestion has no conversation to regenerate from.",
                    "no_conversation")

    cfg = await chat_store.get_chat_config(p.client_id)
    await _reserve_turn(p, cfg, src.get("end_user_ref") or src.get("customer_ref"))
    if body.transform:
        # Carried on the config copy rather than on ChatContext: the engine already reads its
        # behaviour out of `cfg`, and widening the dataclass for a per-request knob would make
        # every other track's construction site wrong.
        cfg = {**cfg, "transform": body.transform}

    suggest_ref = f"rg_{uuid.uuid4().hex}"
    await chat_store.claim_suggestion(p.client_id, suggest_ref, str(conversation_id),
                                      src.get("turn_id"), locale=src.get("locale"),
                                      mode=src.get("mode") or "assist",
                                      integration_id=p.integration_id)
    _INFLIGHT.add((str(p.client_id), suggest_ref))
    bg.add_task(_precompute, str(p.client_id), str(conversation_id), suggest_ref,
                src.get("locale"), src.get("mode") or "assist", p.integration_id, cfg)
    return {"client_id": str(p.client_id), "suggest_ref": suggest_ref,
            "source_suggest_ref": body.suggest_ref, "transform": body.transform,
            "precompute": True, "retry_after_ms": SUGGEST_RETRY_MS}


# --------------------------------------------------------------------------- #
# 6. Feedback
# --------------------------------------------------------------------------- #
@router.post("/feedback", status_code=204)
async def feedback(body: FeedbackIn,
                   p: Principal = Depends(require_chat("chat:suggest")),
                   x_cq_expect_tenant: str | None = Header(default=None,
                                                           alias="X-CQ-Expect-Tenant")):
    """`shown | inserted | edited_sent | sent_asis | ignored` (+ the operator's final text).

    Simultaneously the ROI metric and the highest-quality curation signal in the system: a high
    edit distance between the suggested text and `final_text` is a human correcting the AI, and
    P2 mines exactly that. 204, so the chat site can fire-and-forget it from the composer.
    """
    assert_expected_tenant(p, x_cq_expect_tenant)
    await chat_store.record_feedback(p.client_id, body.suggest_ref,
                                     variant_index=body.variant_index,
                                     action=body.action, final_text=body.final_text)
    return None


# --------------------------------------------------------------------------- #
# 8. Bulk mirror
# --------------------------------------------------------------------------- #
@router.post("/conversations:sync")
async def sync_conversations(body: SyncIn,
                             p: Principal = Depends(require_chat("chat:sync")),
                             x_cq_expect_tenant: str | None = Header(
                                 default=None, alias="X-CQ-Expect-Tenant")):
    """Mirror threads CQ never served (pure operator<->customer, social DMs) — where the *best*
    answers live, and therefore the richest curation corpus.

    Every conversation carries its own `client_id` and the WHOLE BATCH is rejected on any
    mismatch, in a pre-pass BEFORE a single row is written. Rejecting per-item instead would let
    a chat-site partitioning bug land half a batch under the wrong tenant, and P2 would then
    promote it into a human-approved, permanent KB contamination.
    """
    assert_expected_tenant(p, x_cq_expect_tenant)
    convs = body.conversations or []
    if not convs:
        return {"client_id": str(p.client_id), "conversations": 0, "turns": 0, "replays": 0}
    if len(convs) > MAX_SYNC_CONVERSATIONS:
        return _err(413, f"At most {MAX_SYNC_CONVERSATIONS} conversations per batch.",
                    "batch_too_large")
    for c in convs:                              # pre-pass: all-or-nothing, no partial writes
        assert_expected_tenant(p, c.client_id)
        if len(c.turns) > MAX_SYNC_TURNS:
            return _err(413, f"At most {MAX_SYNC_TURNS} turns per conversation.",
                        "conversation_too_large")

    written = replays = 0
    for c in convs:
        conversation_id = await chat_store.upsert_conversation(
            p.client_id, c.external_ref, channel=c.channel, locale=c.locale,
            mode="mirror", customer_ref=c.customer_ref)
        for t in c.turns:
            content = (t.content or "")[:MAX_TURN_CHARS]
            _, is_new = await chat_store.append_turn(
                p.client_id, conversation_id, turn_ref=t.turn_ref or uuid.uuid4().hex,
                role=t.role, content=content, lang=t.lang or c.locale, source="sync")
            written += int(is_new)
            replays += int(not is_new)
    return {"client_id": str(p.client_id), "conversations": len(convs),
            "turns": written, "replays": replays}


# --------------------------------------------------------------------------- #
# 9. GDPR purge
# --------------------------------------------------------------------------- #
@router.delete("/conversations/{external_ref}", status_code=204)
async def purge_conversation(external_ref: str,
                             p: Principal = Depends(require_chat("chat:sync")),
                             x_cq_expect_tenant: str | None = Header(
                                 default=None, alias="X-CQ-Expect-Tenant")):
    """Delete this tenant's mirror of one thread. The chat site remains the system of record, so
    a purge here is a purge of a derived copy — idempotent, and silent when there is nothing to
    delete (a caller retrying a GDPR erasure must not get a 404)."""
    assert_expected_tenant(p, x_cq_expect_tenant)
    removed = await chat_store.purge_conversation(p.client_id, external_ref)
    log.info("purged chat conversation client=%s ref=%s rows=%s",
             p.client_id, external_ref, removed)
    return None


# --------------------------------------------------------------------------- #
# 10. Config
# --------------------------------------------------------------------------- #
@router.get("/config")
async def get_config(p: Principal = Depends(require_chat("chat:turn", "chat:suggest",
                                                          "chat:answer", "chat:sync"))):
    """Persona, greeting, refusal copy, languages, canned snippets, `autopilot_enabled`.

    Readable with any chat scope — it is what the widget renders before a single message is
    sent, and gating it behind one specific scope would just make onboarding fail confusingly.
    `autopilot_enabled` is false unless a human turned it on: the public bot is the only surface
    where a model speaks to an end customer with no human in the loop.
    """
    cfg = await chat_store.get_chat_config(p.client_id)
    return {
        "client_id": str(p.client_id),
        "version": cfg.get("version"),
        "persona": cfg.get("persona"),
        "greeting": _json(cfg.get("greeting")) or {},
        "refusal_copy": _json(cfg.get("refusal_copy")) or {},
        "languages": cfg.get("languages") or [],
        "canned": _json(cfg.get("canned")) or [],
        "autopilot_enabled": bool(cfg.get("autopilot_enabled")),
        "scopes": p.scopes,
    }


# --------------------------------------------------------------------------- #
# 7. POST /chat/answer — the public autopilot
#
# The only endpoint in this file whose output reaches a member of the public with no human in
# between, so it is the only one with two independent off switches in front of it. Both are
# checked HERE as well as inside `chat.run_answer`: the engine's copies are the ones that
# actually protect the customer (a future WebSocket adapter gets them for free), while these
# exist to give the chat site an unambiguous HTTP status — 503 "the operator stopped all bots"
# and 409 "this tenant never enabled one" are different operational events, and a caller that
# only ever sees a polite refusal string cannot tell them apart or page anybody about them.
# --------------------------------------------------------------------------- #
@router.post("/answer")
async def answer(
    request: Request,
    body: AnswerIn,
    # Aliased rather than named `stream`, which is the GET /stream handler in this module.
    as_stream: int = Query(default=0, alias="stream", ge=0, le=1),
    p: Principal = Depends(require_chat("chat:answer")),
    x_cq_expect_tenant: str | None = Header(default=None, alias="X-CQ-Expect-Tenant"),
    x_cq_end_user: str | None = Header(default=None, alias="X-CQ-End-User"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Answer a customer directly. Blocking by default; `?stream=1` returns SSE.

    One code path, two transports: both build the same `ChatContext`, both go through `_drive`,
    and the terminal `done` payload is the same envelope the blocking body returns — which is
    only true because neither of them assembles an envelope of its own.

    The turn is mirrored and the answer is filed through the ordinary store functions, with the
    row's `mode` set to `autopilot`. The P2 curation miner and the operator's history therefore
    see a bot answer exactly like a suggestion; making autopilot conversations invisible to
    curation would mean the surface with no human review is also the one nobody can learn from.

    Idempotent on `(client_id, turn_ref)` like `/turns`: a retried delivery (every social
    webhook retries) re-reads the finished answer instead of paying for a second one and
    sending the customer a second, differently-worded reply.
    """
    assert_expected_tenant(p, x_cq_expect_tenant)
    content = (body.content or "").strip()
    if not content:
        return _err(400, "content must not be empty.", "empty_content")
    if len(content) > MAX_TURN_CHARS:
        return _err(413, f"content exceeds {MAX_TURN_CHARS} characters.", "content_too_large")

    # 1. The operator brake. Before the mirror write, before metering, before anything: when a
    #    bot is misbehaving, "stop" has to mean stop, and a 503 the chat site can fall back to
    #    a human queue on is more useful than a refusal string it will render as the bot's own
    #    words. `get_autopilot_kill_switch` is 5 s-cached and never raises.
    kill = await settings_store.get_autopilot_kill_switch()
    if settings_store.autopilot_killed(kill, str(p.client_id)):
        log.warning("answer refused by kill switch client=%s", p.client_id)
        return _err(503, "Automated answering is temporarily disabled.", "autopilot_disabled")

    # 2. The tenant's own opt-in — false until a human turned it on, and not turnable on at all
    #    until the tenant has published a `visibility='public'` document (enforced on the config
    #    write, not here; this endpoint only has to refuse to speak).
    cfg = await chat_store.get_chat_config(p.client_id)
    if not bool(cfg.get("autopilot_enabled")):
        return _err(409, "Automated answering is not enabled for this tenant.",
                    "autopilot_not_enabled")

    conversation_id = await chat_store.upsert_conversation(
        p.client_id, body.conversation_ref, channel=body.channel, locale=body.locale,
        mode=MODE_AUTOPILOT, customer_ref=x_cq_end_user or body.customer_ref)
    turn_ref = body.turn_ref or idempotency_key or uuid.uuid4().hex
    turn_id, turn_is_new = await chat_store.append_turn(
        p.client_id, conversation_id, turn_ref=turn_ref, idempotency_key=idempotency_key,
        role="customer", content=content, lang=body.locale, source="live")

    # Metered after idempotency is resolved, for the reason spelled out in `post_turn`: one
    # logical message costs one unit however many times the channel redelivers it.
    if turn_is_new:
        await _reserve_answer(p, cfg, x_cq_end_user or body.customer_ref)

    suggest_ref = f"an_{turn_id}"
    _, claim_is_new = await chat_store.claim_suggestion(
        p.client_id, suggest_ref, conversation_id, turn_id, locale=body.locale,
        mode=MODE_AUTOPILOT, integration_id=p.integration_id)

    key = (str(p.client_id), suggest_ref)
    if not claim_is_new:
        # Somebody already owns this answer. If it finished, hand back the stored envelope —
        # that is the replay, and it is byte-identical because it is literally the same object.
        row = await chat_store.get_suggestion(str(p.client_id), suggest_ref)
        if row is None:
            return _err(409, "That answer is already being generated.", "answer_in_flight")
        if row.get("state") == "running":
            return _err(409, "That answer is already being generated.", "answer_in_flight")
        if row.get("state") == "error":
            return _err(502, row.get("error") or "Generation failed.", "generation_failed")
        if as_stream:
            return _sse_response(_stream_body(request, str(p.client_id), suggest_ref,
                                              row, live=False))
        return _answer_body(p, suggest_ref, conversation_id, turn_id, turn_ref,
                            _envelope(row), state=row.get("state"), replay=True)

    ctx = await _build_context(
        str(p.client_id), str(conversation_id), suggest_ref, body.locale, MODE_AUTOPILOT,
        p.integration_id, cfg, conversation_ref=body.conversation_ref, turn_ref=turn_ref,
        channel=body.channel,
        envelope={"channel": body.channel, "text": content,
                  "display_name": body.display_name, "subject": body.subject,
                  "attachment": body.attachment})

    _INFLIGHT.add(key)
    if as_stream:
        # `_INFLIGHT` is discarded by `_stream_body`'s own finally, exactly as on the ticket
        # path — the generator owns the marker from here on.
        row = await chat_store.get_suggestion(str(p.client_id), suggest_ref) or {
            "state": "running"}
        return _sse_response(_stream_body(request, str(p.client_id), suggest_ref, row,
                                          live=True, ctx=ctx))

    try:
        envelope: dict | None = None
        async for name, data in _drive(str(p.client_id), suggest_ref, ctx):
            if name == "done":
                envelope = data.get("turn")
    except llm.LLMBusyError:
        # `_drive` has already marked the row; the customer gets a retry, not a lie.
        return _err(429, "Service is busy; retry shortly.", "llm_busy")
    except Exception as exc:  # noqa: BLE001 — the row is terminal either way (see `_drive`)
        log.warning("answer failed client=%s suggest_ref=%s: %s", p.client_id, suggest_ref, exc)
        return _err(502, "Could not generate an answer.", "answer_failed")
    finally:
        _INFLIGHT.discard(key)

    if envelope is None:
        return _err(502, "Could not generate an answer.", "answer_failed")
    return _answer_body(p, suggest_ref, conversation_id, turn_id, turn_ref, envelope,
                        state=_state_for(envelope), replay=False)


def _answer_body(p: Principal, suggest_ref: str, conversation_id, turn_id, turn_ref: str,
                 envelope: dict, *, state: str | None, replay: bool) -> dict:
    """The blocking answer response. `turn` is the engine's envelope verbatim — the same object
    the SSE `done` frame carries and the same one `GET /suggestions/{ref}` will hand back."""
    return {
        "client_id": str(p.client_id),
        "conversation_id": str(conversation_id),
        "conversation_ref": envelope.get("conversation_ref"),
        "turn_id": str(turn_id),
        "turn_ref": turn_ref,
        "suggest_ref": suggest_ref,
        "state": state or "refused",
        "idempotent_replay": replay,
        "turn": envelope,
    }
