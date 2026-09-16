"""The turn engine: one generator, three transports.

`run_suggest()` (operator copilot) and `run_answer()` (public autopilot) are async
generators of `ChatEvent`. The SSE endpoint serialises them, the blocking endpoint drains
them and returns the last one, and a future WebSocket adapter will forward them as frames.
**Nothing in a `ChatEvent` encodes the transport** — no `data:` prefixes, no frame types,
no HTTP status codes. That single property is what makes WebSocket a purely additive change
later instead of a second implementation of the product logic (ADR-001, "What becomes
easier": the Turn envelope is byte-identical across all three transports).

Three things worth understanding before changing anything here.

**1. The gate runs before Claude, in code.** `gate()` is deterministic, pure, and total: it
looks at hit count, top score and the vector-vs-keyword method flag, and decides whether we
are allowed to answer at all. When it says no, the refusal copy and a handoff are emitted
and **no Anthropic call is made** — not a call with a cautious prompt, not a cheap model,
none. "I don't know" is therefore a property of the system rather than a hope pinned on
prompt wording, it cannot be argued out of by an injected instruction, and it costs exactly
zero tokens. That last part is verifiable: after a refusal, `llm_usage` has no new row.
(The public bot now also has a TRIAGE step for messages the documents do not settle — one
small, passage-free classification call; see `run_answer`. The gate still decides, in code,
whether passages may reach a model at all.)

**2. The ladder exists because the cold path is slow.** Tier 1 is the top-3 KB passage
cards, built from retrieval hits with no model involved, emitted as its own event before any
token exists (~300 ms). Tier 2 is the drafts (~2 s). An operator whose prefetch missed sees
something useful immediately, and still sees it when the LLM call times out.

**3. Query building has no LLM hop.** `build_query()` is string manipulation. A query-rewrite
call costs 300-600 ms on the one path the product owner called latency-critical, and both
strings it returns go to `retrieve_ranked` in a *single* embedding batch.

This module owns no persistence. Conversations, turns and configs belong to `chat_store.py`
and the routers; the engine is given its config and its history and hands back events. There
is exactly one read from `app_settings` — the autopilot kill switch in `run_answer`, which is
cached for 5 s, acquires and releases its connection before any model call, and lives here
rather than in the router on purpose: a brake that a future route can forget to pull is not a
brake. The rule that actually matters — never hold a pool connection across an LLM or
embedding await — remains true by construction.
"""
import dataclasses
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

from . import chat_prompts, chat_safety, llm, settings_store
from .retrieval import retrieve_ranked, unavailable_ranked

log = logging.getLogger("cq")

PROTO = 1

# Engine defaults. Read via `_cfg()`, which checks the config row's top level first and then
# its `settings` jsonb, so a tenant can tune any of these without a schema change.
DEFAULTS = {
    # Grounding gate. `min_score` (0.45) is deliberately ABOVE retrieval's own 0.35 floor:
    # retrieval decides what is worth putting in front of a human as a passage card, the gate
    # decides whether we are confident enough to let a model speak. Two different questions,
    # and collapsing them would mean either weak answers or blank tier-1 cards.
    "min_hits": 1,
    "min_score": 0.45,
    # Reading is the operator's bottleneck, not typing — two functionally distinct cards beat
    # four rewordings. Tone changes are a `regenerate` transform, not extra cards.
    "suggestion_count": 2,
    "top_k": 8,
    # Passages actually sent to the model. Fewer than top_k: the tail of the ranked list is
    # what dilutes an answer, while the head is what grounds it.
    "context_hits": 5,
    "max_tokens": 900,
    # Public-answer length cap, enforced in python after generation (chat_safety) as well as
    # asked for in the directive — a model that ignores the instruction still cannot ship a
    # 4 KB wall of text to a WhatsApp thread.
    "max_reply_chars": chat_safety.DEFAULT_MAX_REPLY_CHARS,
    # ADR-001 open decision #1, resolved as configuration: `kb_only` (the default) gives a
    # question inside the business's field that the documents do not answer the refusal and a
    # colleague; `general` lets triage answer it from general knowledge; `open` also answers an
    # off-topic question briefly, still counted toward the warning and cut-off. `chat_prompts.
    # answer_policy` also reads the legacy `allow_general_knowledge` boolean for older rows.
    "answer_policy": "kb_only",
    "handoff_summary": True,
    # The public bot skips triage only when the customer's OWN message scores at least this
    # against the published documents (and the gate passed). Retrieval's floor is 0.35 and
    # unrelated same-language text scores ~0.30-0.45 under BGE-M3 (see retrieval.py), so the
    # min_score of 0.35 most stored configs carry could not tell a planet question from a price
    # question. Below this a message costs one small triage call, not a refusal — which is why
    # it can err high.
    "direct_min_score": 0.5,
    # Off-topic questions per conversation before a warning / before the bot stops answering
    # anything the documents do not clearly cover. 0 switches either off.
    "off_topic_warn_after": 3,
    "off_topic_cutoff_after": 5,
    # False: a price or deadline a given passage states does not force a handoff
    # (chat_safety.detect_commitment). True restores "every commitment goes to a human".
    "handoff_on_kb_commitments": False,
}

# Gate outcomes. Stable strings — they are stored on the turn row, drive the curation
# harvest ("everything the bot could not ground" is an index scan), and are shown to
# operators. Renaming one silently reclassifies history.
REASON_OK = "ok"
REASON_KB_EMPTY = "kb_empty"          # the tenant has no KB at all — a different message
REASON_NO_HITS = "no_hits"            # KB exists, nothing matched
REASON_LOW_SCORE = "low_score"        # matched, but not well enough to speak
REASON_KEYWORD_ONLY = "keyword_only"  # trigram fallback only — see `strict` below
REASON_DISABLED = "autopilot_off"     # the tenant never enabled the public bot
REASON_KILLED = "autopilot_killed"    # the operator brake — settings_store kill switch
REASON_ESCALATE = "escalation"        # the customer asked for a human (or tripped a marker)
REASON_LLM_ERROR = "llm_error"        # a model call failed, or produced nothing usable
REASON_WEAK_MATCH = "weak_match"      # the gate passed, but the customer's own words scored below
                                      # the direct threshold (`direct_match`) — typically a
                                      # follow-up window match — and the turn was not answered
                                      # from the documents
REASON_RISKY = "risky"                # handoff: triage saw an emergency or an advice request
REASON_RELATED_NOT_IN_KB = "related_not_in_kb"   # handoff: an in-field question the documents
                                                 # do not answer, under the kb_only policy


@dataclass(slots=True)
class ChatEvent:
    """open | grounding | tier1 | delta | suggestion | done | error."""
    name: str
    seq: int
    data: dict


@dataclass
class ChatContext:
    client_id: str
    conversation_id: str
    suggest_ref: str
    locale: str
    messages: list[dict]
    cfg: dict
    api_key: str
    model: str
    mode: str
    integration_id: str | None = None
    # Additive, all defaulted: the routers have this context and the envelope needs it, but
    # nothing upstream is required to supply it.
    channel: str = "web"
    turn_ref: str | None = None
    # `chat_turns.id` of the message this generation answers. Only for usage attribution: every
    # model call below names it, so a token bill can be traced to the question that caused it.
    turn_id: str | None = None
    conversation_ref: str | None = None
    # The raw inbound envelope (display_name, attachment, channel, text). Quarantined
    # wholesale by chat_prompts.wrap_untrusted — see that module for why it is not just text.
    envelope: dict = field(default_factory=dict)
    # The public bot's per-conversation context, loaded by the router (the engine reads no
    # tables). Defaulted: the copilot and every older construction site pass none of it.
    business_name: str | None = None
    industry: str | None = None
    off_topic_count: int = 0
    # The business clock's "now". None means the real clock; tests pin it.
    now: datetime | None = None


def _cfg(cfg: dict, key: str):
    """Tenant value for `key`, from the config row's top level, then `settings`, then default."""
    cfg = cfg or {}
    if cfg.get(key) is not None:
        return cfg[key]
    settings = cfg.get("settings")
    if isinstance(settings, dict) and settings.get(key) is not None:
        return settings[key]
    return DEFAULTS.get(key)


def _num(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


# --- the gate -----------------------------------------------------------------

def gate(r: dict, cfg: dict) -> tuple[bool, str]:
    """Deterministic grounding decision, made BEFORE any Claude call.

    `grounded = len(hits) >= min_hits and top_score >= min_score and method == "vector"`.

    The `method == "vector"` clause is policy, not arithmetic, and it is the one clause that
    differs between our two surfaces. The pg_trgm fallback matches on character trigrams, so
    for Georgian it will happily return a passage that merely shares morphology with the
    question. For the **public autopilot** that is not grounding and `strict` stays True. For
    the **copilot** it is perfectly acceptable — a human reads every word before it is sent,
    and a mediocre passage is still a better starting point than a blank composer. Hence a
    flag on the config rather than one hardcoded policy: `cfg["strict"]`, defaulting to True
    so anything that forgets to set it fails closed.

    Total and non-raising: `retrieve_ranked` never raises either, and a retrieval outage must
    degrade to a clean refusal, not a 500 in front of a customer.
    """
    r = r or {}
    if not r.get("kb_present"):
        return False, REASON_KB_EMPTY

    hits = r.get("hits") or []
    min_hits = int(_num(_cfg(cfg, "min_hits"), DEFAULTS["min_hits"]))
    if len(hits) < max(1, min_hits):
        return False, REASON_NO_HITS

    strict = _cfg(cfg, "strict")
    strict = True if strict is None else bool(strict)
    if strict and r.get("method") != "vector":
        return False, REASON_KEYWORD_ONLY

    top = r.get("top_score")
    min_score = _num(_cfg(cfg, "min_score"), DEFAULTS["min_score"])
    if top is None or float(top) < min_score:
        return False, REASON_LOW_SCORE

    return True, REASON_OK


# --- query building (no LLM hop) ----------------------------------------------

MAX_QUERY_CHARS = 512


def build_query(messages: list[dict]) -> list[str]:
    """`[raw last customer message, short window]` — two strings, one embedding batch.

    The raw message is what a follow-up like "და რა ღირს?" ("and how much is it?") cannot be
    retrieved from on its own; the window (last two customer turns plus the last agent turn)
    carries the referent. Fusing two retrievals with RRF gets most of the benefit of a
    rewritten query for ~0 ms and 0 tokens, which is the trade the latency budget requires.

    512 chars because TEI latency is roughly linear in tokens and a chat message is short —
    `retrieval.retrieve()` sends 4000, which is transcript-shaped and stays that way.
    """
    msgs = messages or []
    customer = [m for m in msgs if str(m.get("role") or "").lower() == "customer"]
    last = str((customer[-1] if customer else (msgs[-1] if msgs else {})).get("content") or "").strip()

    window_parts = []
    for m in customer[-2:]:
        text = str(m.get("content") or "").strip()
        if text:
            window_parts.append(text)
    for m in reversed(msgs):
        role = str(m.get("role") or "").lower()
        if role in ("operator", "bot"):
            text = str(m.get("content") or "").strip()
            if text:
                # Prepended: the agent's line usually names the subject the customer then
                # refers to with a pronoun.
                window_parts.insert(0, text)
            break

    queries = []
    for candidate in (last, " ".join(window_parts).strip()):
        candidate = candidate[:MAX_QUERY_CHARS].strip()
        if candidate and candidate not in queries:
            queries.append(candidate)
    return queries


# --- the Turn envelope ---------------------------------------------------------

def build_turn_envelope(*, client_id: str, suggest_ref: str,
                        conversation_ref: str | None = None, turn_ref: str | None = None,
                        channel: str = "web", locale: str = "en",
                        retrieval: dict | None = None, grounded: bool = False,
                        reason: str = "", tier1: list[dict] | None = None,
                        suggestions: list[dict] | None = None, reply: dict | None = None,
                        handoff: dict | None = None, stages: dict | None = None,
                        model: str | None = None, scope: dict | None = None) -> dict:
    """THE Turn object (ADR-001 "API contract").

    Built here and nowhere else, and deliberately free of any `ChatContext` argument. The warm
    read path — one indexed SELECT against `copilot_suggestions`, no engine run — does not
    rebuild it at all: `chat_store.finish_suggestion` files the object this function returned,
    verbatim, and the read hands that back. Byte-identity across the blocking read, the SSE
    replay and the live `done` frame is therefore a property of there being one producer, not of
    three reconstructions agreeing.

    `client_id` is echoed on purpose: the chat site is itself multi-tenant, and echoing the
    tenant CQ actually resolved is the only way for it to detect its own mapping bug.
    """
    r = retrieval or {}
    hits = r.get("hits") or []
    return {
        "proto": PROTO,
        "turn_ref": turn_ref,
        "suggest_ref": suggest_ref,
        "conversation_ref": conversation_ref,
        "client_id": client_id,
        "channel": channel,
        "locale": chat_prompts.normalize_locale(locale),
        "grounding": {
            "grounded": bool(grounded),
            "reason": reason or "",
            "method": r.get("method") or "none",
            "top_score": r.get("top_score"),
            "hit_count": len(hits),
            "kb_present": bool(r.get("kb_present")),
        },
        "citations": chat_prompts.build_citations(hits),
        "tier1": tier1 or [],
        "suggestions": suggestions or [],
        "reply": reply,
        "handoff": handoff or {"recommended": False, "reason": None, "summary": None},
        # The public bot's decision about what kind of message this was and how it was served
        # (see `run_answer`); null on copilot envelopes.
        "scope": scope,
        # Tokens are recorded by llm.py straight into `llm_usage` and are not returned to the
        # caller, so this carries the timing half only. `latency_ms` is the per-stage dict
        # that makes P4's tuning empirical instead of guessed.
        "usage": {"input_tokens": None, "output_tokens": None,
                  "model": model, "latency_ms": stages or {}},
    }


# --- run helpers ---------------------------------------------------------------

class _Seq:
    """Monotonic event sequence. The SSE client uses it to detect a gap; a WS client will
    use it to order frames. Per-run, starting at 1."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, name: str, data: dict) -> ChatEvent:
        self.n += 1
        return ChatEvent(name=name, seq=self.n, data=data)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _last_customer_text(ctx: ChatContext) -> str:
    for m in reversed(ctx.messages or []):
        if str(m.get("role") or "").lower() == "customer":
            return str(m.get("content") or "").strip()
    return ""


def _handoff(reason: str, ctx: ChatContext) -> dict:
    """The zero-cost handoff: last customer message as the summary, no model involved.

    Used on every path that must not spend a token — see `run_answer`'s refusal branches.
    """
    return {"recommended": True, "reason": reason,
            "summary": _last_customer_text(ctx)[:400] or None}


async def _handoff_with_summary(reason: str, ctx: ChatContext, stages: dict) -> dict:
    """A handoff a human can actually pick up: a short written summary of the thread so far.

    The operator inheriting this conversation should not have to open the transcript, and the
    customer should never be asked to repeat themselves — being handed to a human who starts
    with "hi, how can I help?" is the moment the AI layer visibly cost them time.

    The source is the mirror: `ctx.messages` is what `chat_store.recent_turns` returned for
    this conversation, so the summary sees the same window the model saw and this function
    still touches no database.

    **A handoff must never fail because a summary failed.** Every error path — LLM down,
    admission-rejected, malformed tool result, disabled by config — lands on the plain
    concatenation in `chat_prompts.fallback_handoff_summary`. It is also never called on the
    zero-token refusal path; "the gate said no" costs nothing, and that includes this.
    """
    handoff = _handoff(reason, ctx)
    messages = ctx.messages or []
    if not messages:
        return handoff

    fallback = chat_prompts.fallback_handoff_summary(messages)
    if not bool(_cfg(ctx.cfg, "handoff_summary")):
        handoff["summary"] = fallback or handoff["summary"]
        return handoff

    t = time.monotonic()
    try:
        raw = await llm.call_tool(
            feature="handoff", client_id=ctx.client_id, integration_id=ctx.integration_id,
            api_key=ctx.api_key, model=ctx.model,
            system=chat_prompts.build_handoff_system(ctx.locale),
            user=chat_prompts.build_handoff_user(messages, reason),
            tool=chat_prompts.HANDOFF_TOOL, opts=llm.ANSWER, max_tokens=400,
            conversation_id=ctx.conversation_id, turn_id=ctx.turn_id,
            suggest_ref=ctx.suggest_ref)
        summary = str(raw.get("summary") or "").strip()
        goal = str(raw.get("customer_goal") or "").strip()
        # The summary is model output derived from untrusted text and is read by a human in a
        # UI, so it goes through the same markup/URL scrubbing an answer does. No hits are
        # passed, which makes the URL allowlist empty: an internal note needs no links at all.
        summary = chat_safety.enforce_length(chat_safety.sanitize_output(summary, []), 600)
        if summary:
            handoff["summary"] = summary
        if goal:
            handoff["goal"] = chat_safety.enforce_length(goal, 120)
    except Exception as exc:  # noqa: BLE001 — see docstring: never fail a handoff
        log.warning("handoff summary failed (client=%s, reason=%s): %s",
                    ctx.client_id, reason, exc)
        handoff["summary"] = fallback or handoff["summary"]
    stages["handoff_summary"] = _ms(t)
    return handoff


def _envelope_for(ctx: ChatContext, **kw) -> dict:
    return build_turn_envelope(
        client_id=ctx.client_id, suggest_ref=ctx.suggest_ref,
        conversation_ref=ctx.conversation_ref or ctx.conversation_id,
        turn_ref=ctx.turn_ref, channel=ctx.channel, locale=ctx.locale,
        model=ctx.model, **kw)


def _disclosed(text: str, ctx: ChatContext) -> str:
    """Append the tenant's AI-disclosure line to a public answer, in code.

    Deterministic and unskippable by design (ADR-001 security bar item 9): the prompt also
    tells the model it is an AI, but a prompt rule is exactly the kind of thing a successful
    injection argues with, and "did the customer know they were talking to a bot" is not a
    property we are willing to leave to that.

    `disclosure_mode` decides how often: 'first' (default) puts it on the bot's opening reply
    only — an every-message footer trains customers to ignore it, and this thread already has
    it above — 'always' repeats it, 'off' relies on the channel's own chrome. The copy itself
    is per tenant and per channel; empty copy means the tenant discloses elsewhere.
    """
    text = (text or "").strip()
    mode = chat_prompts.disclosure_mode(ctx.cfg)
    if mode == "off":
        return text
    if mode == "first" and any(
            str(m.get("role") or "").lower() in ("bot", "operator") for m in (ctx.messages or [])):
        return text
    line = chat_prompts.disclosure_text(ctx.cfg, ctx.locale, ctx.channel)
    if not line or line in text:
        return text
    return f"{text}\n\n{line}" if text else line


def _inbound(ctx: ChatContext) -> dict:
    """The envelope handed to the quarantine wrapper: whatever the caller supplied, with the
    latest customer message filled in if it did not."""
    env = dict(ctx.envelope or {})
    env.setdefault("channel", ctx.channel)
    if not str(env.get("text") or "").strip():
        for m in reversed(ctx.messages or []):
            if str(m.get("role") or "").lower() == "customer":
                env["text"] = m.get("content")
                break
    return env


async def _ground(ctx: ChatContext, *, visibility: str | None,
                  stages: dict) -> tuple[list[str], dict, bool, str]:
    """Query build → retrieval → gate. The whole no-LLM prefix of both runs."""
    t = time.monotonic()
    queries = build_query(ctx.messages)
    if not queries:
        # Built by retrieval, not by hand: an envelope assembled here is one that does not
        # carry `confidence`, and a consumer reading `r["confidence"]` would KeyError on
        # exactly the degraded path. `kb_present=False` is passed explicitly to keep the
        # value gate() has always seen — nothing was retrieved, so nothing may be grounded.
        return [], unavailable_ranked(False), False, REASON_NO_HITS

    top_k = int(_num(_cfg(ctx.cfg, "top_k"), DEFAULTS["top_k"]))
    # Both query strings in ONE call so they are embedded in a single batch and fused with
    # RRF inside retrieval — two sequential calls would double the slowest stage in the run.
    r = await retrieve_ranked(ctx.client_id, queries[0], top_k=top_k,
                              visibility=visibility, extra_queries=queries[1:])
    stages["retrieval"] = _ms(t)

    t = time.monotonic()
    grounded, reason = gate(r, ctx.cfg)
    stages["gate"] = _ms(t)
    return queries, r, grounded, reason


# --- run_suggest: the operator copilot ----------------------------------------

async def run_suggest(ctx: ChatContext) -> AsyncIterator[ChatEvent]:
    """Draft replies for a human operator. Every word passes a person before a customer
    sees it, which is what makes `strict=False` (keyword hits are usable) safe here."""
    ev = _Seq()
    started = time.monotonic()
    stages: dict = {}
    yield ev("open", {"suggest_ref": ctx.suggest_ref, "mode": ctx.mode,
                      "conversation_ref": ctx.conversation_ref or ctx.conversation_id,
                      "locale": chat_prompts.normalize_locale(ctx.locale), "proto": PROTO})

    # The copilot sees the tenant's whole KB, `visibility=None` — publishability gates the
    # PUBLIC bot only, and an operator is already trusted with internal documents.
    cfg = dict(ctx.cfg or {})
    cfg.setdefault("strict", False)
    ctx = _with_cfg(ctx, cfg)

    queries, r, grounded, reason = await _ground(ctx, visibility=None, stages=stages)
    hits = r.get("hits") or []
    yield ev("grounding", {"grounded": grounded, "reason": reason,
                           "method": r.get("method"), "top_score": r.get("top_score"),
                           "hit_count": len(hits), "kb_present": bool(r.get("kb_present"))})

    # Tier 1 before tier 2, always — cards are on screen while the model is still thinking,
    # and they are the only thing the operator gets if the LLM call below fails.
    tier1 = chat_prompts.build_tier1(hits, queries[0] if queries else "")
    yield ev("tier1", {"cards": tier1})

    if not grounded:
        # Zero tokens spent. The refusal is still offered as a sendable card so the operator
        # can dispatch it with one click instead of composing an apology from scratch.
        refusal = chat_prompts.refusal_text(ctx.cfg, ctx.locale)
        stages["total"] = _ms(started)
        yield ev("done", _envelope_for(
            ctx, retrieval=r, grounded=False, reason=reason, tier1=tier1,
            suggestions=[{"index": 0, "kind": "escalate", "text": refusal, "citations": []}],
            handoff=_handoff(reason, ctx), stages=stages))
        return

    count = int(_num(_cfg(ctx.cfg, "suggestion_count"), DEFAULTS["suggestion_count"]))
    context_hits = hits[:int(_num(_cfg(ctx.cfg, "context_hits"), DEFAULTS["context_hits"]))]
    system = chat_prompts.build_system(ctx.cfg, mode="assist", locale=ctx.locale)
    user = chat_prompts.build_user(hits=context_hits, messages=ctx.messages,
                                   envelope=_inbound(ctx),
                                   directive=chat_prompts.build_kinds_directive(count))

    t = time.monotonic()
    try:
        raw = await llm.call_tool(
            feature="copilot", client_id=ctx.client_id, integration_id=ctx.integration_id,
            api_key=ctx.api_key, model=ctx.model, system=system, user=user,
            tool=chat_prompts.SUGGEST_TOOL, opts=llm.COPILOT,
            max_tokens=int(_num(_cfg(ctx.cfg, "max_tokens"), DEFAULTS["max_tokens"])),
            # The system prompt is tenant-stable across every turn, so it caches; the user
            # block is per-turn and never does.
            cache_system=True,
            conversation_id=ctx.conversation_id, turn_id=ctx.turn_id,
            suggest_ref=ctx.suggest_ref)
    except llm.LLMError as exc:
        stages["llm"] = _ms(t)
        stages["total"] = _ms(started)
        log.warning("copilot suggestion failed (client=%s): %s", ctx.client_id, exc)
        # NOT fatal to the turn: tier-1 cards already shipped and are genuinely useful, so the
        # consumer gets an error event AND a terminal envelope rather than a dangling stream.
        yield ev("error", {"code": "llm_busy" if isinstance(exc, llm.LLMBusyError) else "llm_error",
                           "message": str(exc), "fatal": False})
        yield ev("done", _envelope_for(ctx, retrieval=r, grounded=True, reason=reason,
                                       tier1=tier1, suggestions=[],
                                       handoff=_handoff("llm_error", ctx), stages=stages))
        return
    stages["llm"] = _ms(t)

    suggestions = _normalize_suggestions(raw.get("suggestions"), context_hits, count)
    for s in suggestions:
        yield ev("suggestion", s)

    handoff = {"recommended": False, "reason": None, "summary": None}
    if bool(raw.get("handoff_recommended")) or any(
            chat_prompts.looks_like_commitment(s["text"]) for s in suggestions):
        handoff = _handoff("commitment_or_model_flagged", ctx)

    stages["total"] = _ms(started)
    yield ev("done", _envelope_for(ctx, retrieval=r, grounded=True, reason=reason,
                                   tier1=tier1, suggestions=suggestions,
                                   handoff=handoff, stages=stages))


def _with_cfg(ctx: ChatContext, cfg: dict) -> ChatContext:
    """A shallow copy carrying an adjusted config — the caller's dict is never mutated,
    because it is very likely a cached config row shared by concurrent turns. `replace`, not a
    field-by-field copy, so a field added to ChatContext can never be silently dropped here."""
    return dataclasses.replace(ctx, cfg=cfg)


def _normalize_suggestions(raw, hits: list[dict], count: int) -> list[dict]:
    """Model output → the envelope's suggestion shape, sanitized and citation-resolved.

    Same defensive posture as `claude._as_str_list`: strict schemas make a wrong shape
    unlikely, never impossible, and the UI must always be able to `.map()` this.
    """
    items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = chat_prompts.sanitize_output(str(item.get("text") or ""), hits)
        text, cites = chat_prompts.resolve_citations(text, hits)
        if not text:
            continue
        kind = str(item.get("kind") or "answer").strip().lower()
        if kind not in ("answer", "clarify", "escalate"):
            kind = "answer"
        out.append({"index": len(out), "kind": kind, "text": text, "citations": cites})
        if len(out) >= max(1, count):
            break
    return out


# --- the public bot's scope decisions (pure) ------------------------------------

SCOPE_DIRECT = "direct"          # the customer's own words matched the published documents
SCOPE_TRIAGE = "triage"          # the small classification call decided
SCOPE_CUTOFF = "cutoff"          # past the off-topic cut-off: built-in copy, no model
SCOPE_REFUSAL = "refusal"        # the zero-token refusal (nothing published to read)
SCOPE_ESCALATION = "escalation"  # the customer asked for a person / tripped a marker
SCOPE_OFF = "off"                # kill switch or autopilot disabled

KIND_BUSINESS = chat_prompts.KIND_BUSINESS
KIND_RELATED = chat_prompts.KIND_RELATED
KIND_OFF_TOPIC = chat_prompts.KIND_OFF_TOPIC
KIND_RISKY = chat_prompts.KIND_RISKY


def own_score(r: dict) -> float | None:
    """Best vector score of the customer's message ALONE — retrieval's `query_top_scores[0]`.

    The fused `top_score` is the best score across the raw message AND the follow-up window,
    and the window prepends the bot's last line, which usually names the company. That is how
    "list me the planets", asked right after the bot introduced an internet provider, scored
    like a question about the internet provider. A result without per-query scores (a stub, an
    older retrieval) has only the fused number, which then stands in.
    """
    r = r or {}
    tops = r.get("query_top_scores")
    if isinstance(tops, list) and tops:
        return float(tops[0]) if tops[0] is not None else None
    if r.get("method") == "vector" and r.get("top_score") is not None:
        return float(r["top_score"])
    return None


def direct_match(r: dict, cfg: dict, grounded: bool) -> bool:
    """Whether the public bot may skip triage: the gate passed AND the customer's own words
    score at least max(min_score, direct_min_score)."""
    if not grounded:
        return False
    own = own_score(r)
    floor = max(_num(_cfg(cfg, "min_score"), DEFAULTS["min_score"]),
                _num(_cfg(cfg, "direct_min_score"), DEFAULTS["direct_min_score"]))
    return own is not None and own >= floor


def off_topic_limits(cfg: dict) -> tuple[int, int]:
    """(warn_after, cutoff_after), each 0 when switched off."""
    warn = int(_num(_cfg(cfg, "off_topic_warn_after"), DEFAULTS["off_topic_warn_after"]))
    cutoff = int(_num(_cfg(cfg, "off_topic_cutoff_after"), DEFAULTS["off_topic_cutoff_after"]))
    return max(0, warn), max(0, cutoff)


def off_topic_state(count: int, warn_after: int, cutoff_after: int) -> str:
    """'ok' | 'warned' | 'cut_off' for a conversation that has asked `count` off-topic questions."""
    if cutoff_after and count >= cutoff_after:
        return "cut_off"
    if warn_after and count >= warn_after:
        return "warned"
    return "ok"


def _scope(ctx: ChatContext, *, source: str, kind: str | None = None, answered: bool = False,
           count: int | None = None) -> dict:
    """The envelope's `scope` object. `count` is the conversation's off-topic total AFTER this
    turn; the router persists it when it moved (`routers/chat._record_off_topic`)."""
    warn_after, cutoff_after = off_topic_limits(ctx.cfg)
    n = ctx.off_topic_count if count is None else count
    return {
        "policy": chat_prompts.answer_policy(ctx.cfg),
        "source": source,
        "kind": kind,
        "answered": bool(answered),
        "off_topic": {"count": n, "warn_after": warn_after, "cutoff_after": cutoff_after,
                      "state": off_topic_state(n, warn_after, cutoff_after)},
    }


def _no_handoff() -> dict:
    return {"recommended": False, "reason": None, "summary": None}


def _llm_code(exc: Exception) -> str:
    return "llm_busy" if isinstance(exc, llm.LLMBusyError) else "llm_error"


def _free_text(text: str, max_chars: int) -> str:
    """Model text written WITHOUT passages, validated the way an answer is: markup stripped,
    every URL dropped (there is no passage to allowlist one against), length enforced, and any
    `[n]` marker removed (there is nothing for it to cite)."""
    text = chat_safety.strip_unsafe_markup(text or "")
    text = chat_safety.drop_foreign_urls(text, [])
    text = chat_safety.enforce_length(text, max_chars)
    text, _ = chat_safety.resolve_citations(text, [])
    return text.strip()


# --- run_answer: the public autopilot -----------------------------------------

async def run_answer(ctx: ChatContext) -> AsyncIterator[ChatEvent]:
    """Answer the customer directly. No human in the loop, so every default is the strict one.

    Same event vocabulary and the same shape as `run_suggest` — deliberately, so the two are
    diffable — with the answer in the envelope's `reply` instead of `suggestions`. What is
    different, and why:

      * **`visibility='public'`.** Only documents a human explicitly published are even
        retrieved. `visibility` DEFAULTS to 'internal' in `db/chat.sql`, so an internal
        pricing floor or escalation script cannot be quoted at a WhatsApp customer by
        accident — it has to be published by someone on purpose.
      * **`strict=True`, forced, not defaulted.** A pg_trgm hit is character-overlap, not
        understanding; for the copilot that is a usable starting point because a human reads
        it, out here it is not grounding. This overwrites whatever the tenant config says,
        because "the tenant turned strict off" must not be a way to get an ungrounded public
        bot.
      * **Two independent off switches**, checked before anything else: the tenant's own
        `autopilot_enabled` (OFF by default, forever) and the operator's kill switch in
        `app_settings` (see `settings_store.get_autopilot_kill_switch` — 5 s TTL, so a
        superadmin stops a misbehaving bot in seconds without a redeploy).
      * **No passage reaches a model the gate did not approve.** When the documents cannot
        ground a business question, the tenant's refusal copy goes out with
        `handoff.recommended=True` and the answer model is never called.
      * **Triage, for what the documents do not settle** (2026-09-14). A message whose own
        words do not strongly match the published documents (`direct_match`) gets ONE small
        forced-tool-use call (`feature="triage"`, no passages) that sorts it into business /
        related / chitchat / off_topic / risky and writes the short reply that kind allows.
        Business questions go on to the grounded answer or the refusal; the other kinds never
        see a passage. That is what lets the bot say what day it is, redirect a question about
        planets instead of spending a full answer on it (and warn, then stop, a conversation
        that keeps doing it), give an emergency the emergency number first, and — only under
        the `general` and `open` policies — tell a hospital's patient which kind of doctor
        treats a broken leg (`open` answers the planets too, briefly, and still counts them).
        The exits that still cost zero tokens: both off switches, a KB with nothing published,
        and the off-topic cut-off.
      * **The answer streams.** `llm.stream_text`, `opts=llm.ANSWER`, because a customer-facing
        answer is long enough that time-to-first-token is a product property (the copilot's
        short variants are not). Forced tool-use and token streaming do not combine, so the
        answer is plain text with inline `[n]` markers and citations are resolved AFTER the
        stream against the server-held hits list. That is *less* model capability than the
        copilot has, not more: this call is given no tools at all, which is how ADR-001's "no
        tools beyond a terminal submit_answer" bar is met. Retrieval already ran, in code,
        above. A successful injection can make this bot say something wrong. It can never make
        it act.
      * **Output validation is python, after generation** (`chat_safety`): markup stripped,
        foreign URLs dropped, length enforced, and a commitment that no passage the model was
        given states (or any legal/medical assurance) forced to a handoff.
    """
    ev = _Seq()
    started = time.monotonic()
    stages: dict = {}
    yield ev("open", {"suggest_ref": ctx.suggest_ref, "mode": ctx.mode,
                      "conversation_ref": ctx.conversation_ref or ctx.conversation_id,
                      "locale": chat_prompts.normalize_locale(ctx.locale), "proto": PROTO})

    refusal = chat_prompts.refusal_text(ctx.cfg, ctx.locale)
    max_chars = int(_num(_cfg(ctx.cfg, "max_reply_chars"), DEFAULTS["max_reply_chars"]))
    max_tokens = int(_num(_cfg(ctx.cfg, "max_tokens"), DEFAULTS["max_tokens"]))

    def _reply(text: str, citations: list | None = None, answered: bool = False) -> dict:
        """Every reply this engine can emit, refusals included, goes through here — so the AI
        disclosure is appended by python on the way out rather than requested in the prompt.
        See `chat_prompts.disclosure_text`: a prompt rule is something a successful injection
        can argue with, and disclosure is on ADR-001's security-bar list precisely because it
        must not be."""
        return {"text": _disclosed(text, ctx), "citations": citations or [],
                "answered_from_kb": answered}

    def _done(*, reason: str, text: str, handoff: dict, scope: dict,
              retrieval: dict | None = None, grounded: bool = False,
              citations: list | None = None, answered_from_kb: bool = False) -> ChatEvent:
        """The one terminal event of the public bot, so no exit can forget `scope`."""
        stages["total"] = _ms(started)
        return ev("done", _envelope_for(
            ctx, retrieval=retrieval, grounded=grounded, reason=reason,
            reply=_reply(text, citations, answered_from_kb), handoff=handoff,
            stages=stages, scope=scope))

    # 1. The operator brake, first — it must win over every tenant setting below it.
    t = time.monotonic()
    kill = await settings_store.get_autopilot_kill_switch()
    stages["kill_switch"] = _ms(t)
    if settings_store.autopilot_killed(kill, ctx.client_id):
        log.warning("autopilot suppressed by kill switch (client=%s)", ctx.client_id)
        yield _done(reason=REASON_KILLED, text=refusal, handoff=_handoff(REASON_KILLED, ctx),
                    scope=_scope(ctx, source=SCOPE_OFF))
        return

    # 2. The tenant's own opt-in. False for a tenant that has never configured chat.
    if not bool(_cfg(ctx.cfg, "autopilot_enabled")):
        yield _done(reason=REASON_DISABLED, text=refusal, handoff=_handoff(REASON_DISABLED, ctx),
                    scope=_scope(ctx, source=SCOPE_OFF))
        return

    # 3. "Get me a human" outranks any answer we could give. Checked on the CUSTOMER's text
    #    before the model is called: answering the literal question when someone has asked for
    #    a person (or is describing an emergency, or has said "lawyer") is the single most
    #    expensive way for this product to be technically correct.
    escalation = chat_safety.should_escalate(_last_customer_text(ctx), ctx.cfg)
    if escalation:
        handoff = await _handoff_with_summary(f"{REASON_ESCALATE}:{escalation}", ctx, stages)
        # These turns used to get the refusal copy — "I don't have that in my knowledge base"
        # said to someone who had just asked for a person, or described an emergency. They get
        # the handoff notice, and a distress marker gets the emergency number first.
        notice = (chat_prompts.safety_notice_text(ctx.cfg, ctx.locale)
                  if escalation == "distress" else chat_prompts.handoff_notice_text(ctx.locale))
        yield _done(reason=REASON_ESCALATE, text=notice, handoff=handoff,
                    scope=_scope(ctx, source=SCOPE_ESCALATION,
                                 kind="risky" if escalation == "distress" else None))
        return

    cfg = dict(ctx.cfg or {})
    cfg["strict"] = True  # forced, not setdefault — see the docstring
    ctx = _with_cfg(ctx, cfg)

    queries, r, grounded, reason = await _ground(ctx, visibility="public", stages=stages)
    hits = r.get("hits") or []
    direct = direct_match(r, ctx.cfg, grounded)
    yield ev("grounding", {"grounded": grounded, "reason": reason,
                           "method": r.get("method"), "top_score": r.get("top_score"),
                           "hit_count": len(hits), "kb_present": bool(r.get("kb_present")),
                           # Additive: the retrieval verdict above is decided before triage; this
                           # says whether the customer's own words were enough to skip it.
                           "direct": direct})

    policy = chat_prompts.answer_policy(ctx.cfg)
    warn_after, cutoff_after = off_topic_limits(ctx.cfg)
    context_hits = hits[:int(_num(_cfg(ctx.cfg, "context_hits"), DEFAULTS["context_hits"]))]
    # What a turn served WITHOUT the documents reports as its grounding reason: the gate's own
    # when the gate refused, or `weak_match` when it passed but the customer's own words were
    # below the direct threshold. `ok` next to `grounded: false` would read as a contradiction.
    weak_reason = reason if not grounded else REASON_WEAK_MATCH

    async def _grounded_answer(source: str, kind: str | None):
        """The KB-grounded, streamed answer: passages in, citations resolved, output validated.

        Forced tool-use and token streaming do not combine, so this call gets no tools at all
        (ADR-001's "no tools beyond a terminal submit_answer" bar, met by subtraction)."""
        system = chat_prompts.build_system(ctx.cfg, mode="autopilot", locale=ctx.locale)
        user = chat_prompts.build_user(
            hits=context_hits, messages=ctx.messages, envelope=_inbound(ctx),
            directive=chat_prompts.build_answer_directive(grounded=True, max_chars=max_chars),
            preamble=chat_prompts.clock_block(ctx.cfg, ctx.now))

        t = time.monotonic()
        chunks: list[str] = []
        try:
            async for delta in llm.stream_text(
                    feature="autopilot", client_id=ctx.client_id,
                    integration_id=ctx.integration_id, api_key=ctx.api_key, model=ctx.model,
                    system=system, user=user, opts=llm.ANSWER, max_tokens=max_tokens,
                    conversation_id=ctx.conversation_id, turn_id=ctx.turn_id,
                    suggest_ref=ctx.suggest_ref):
                chunks.append(delta)
                # Deltas are RAW model text — unsanitized, uncited, untruncated. They are a
                # progressive-rendering nicety; the authoritative text is the one on `done`, and
                # a consumer that renders deltas must replace them with it. A channel that cannot
                # replace what it has already sent (SMS, email) must not render deltas at all.
                yield ev("delta", {"text": delta})
        except llm.LLMError as exc:
            stages["llm"] = _ms(t)
            log.warning("autopilot answer failed (client=%s): %s", ctx.client_id, exc)
            yield ev("error", {"code": _llm_code(exc), "message": str(exc), "fatal": False})
            # Falls back to the refusal rather than silence: the customer is waiting, and a
            # handoff is a correct outcome for an outage. The summary stays on the cheap path —
            # a second model call immediately after one just failed is optimism, not design.
            yield _done(retrieval=r, reason=REASON_LLM_ERROR, text=refusal,
                        handoff=_handoff(REASON_LLM_ERROR, ctx),
                        scope=_scope(ctx, source=source, kind=kind))
            return
        stages["llm"] = _ms(t)

        # Validation order is load-bearing: markup and URLs first (they can contain digits that
        # look like markers), length next, citations LAST — so the returned citation list is
        # exactly what survived into the final text rather than a superset of it.
        t = time.monotonic()
        text = chat_safety.strip_unsafe_markup("".join(chunks))
        text = chat_safety.drop_foreign_urls(text, context_hits)
        text = chat_safety.enforce_length(text, max_chars)
        text, cites = chat_safety.resolve_citations(text, context_hits)
        stages["validate"] = _ms(t)

        if not text:
            # Nothing usable came back. The refusal copy promises a colleague, so the turn must
            # actually go to one — it used to be sent with no handoff at all.
            yield _done(retrieval=r, reason=REASON_LLM_ERROR, text=refusal,
                        handoff=_handoff(REASON_LLM_ERROR, ctx),
                        scope=_scope(ctx, source=source, kind=kind))
            return

        handoff = _no_handoff()
        # A commitment forces a handoff unless a passage the model was GIVEN states it (see
        # chat_safety.detect_commitment) — the tenant's own published installation time is not
        # a promise the bot made up. The deltas have already left the building, so this cannot
        # retract the text; it flags the turn for a human, which is the actionable half.
        passages = None if bool(_cfg(ctx.cfg, "handoff_on_kb_commitments")) else context_hits
        commitment = chat_safety.detect_commitment(text, passages)
        if commitment:
            handoff = await _handoff_with_summary(f"commitment:{commitment}", ctx, stages)
        yield _done(retrieval=r, grounded=True, reason=REASON_OK, text=text, citations=cites,
                    answered_from_kb=True, handoff=handoff,
                    scope=_scope(ctx, source=source, kind=kind, answered=True))

    # 5. Nothing published to read, or retrieval could not run: the zero-token refusal.
    if not r.get("kb_present"):
        yield _done(retrieval=r, reason=reason, text=refusal, handoff=_handoff(reason, ctx),
                    scope=_scope(ctx, source=SCOPE_REFUSAL))
        return

    # 6. The customer's own words match the published documents: straight to the answer.
    if direct:
        async for event in _grounded_answer(SCOPE_DIRECT, None):
            yield event
        return

    # 7. A conversation past the off-topic cut-off gets no model call for anything the documents
    #    do not clearly cover. No handoff and no closed chat: a customer who joked first and then
    #    asks about their plan in so many words still reaches step 6 above.
    if cutoff_after and ctx.off_topic_count >= cutoff_after:
        yield _done(retrieval=r, reason=weak_reason,
                    text=chat_prompts.off_topic_cutoff_text(ctx.cfg, ctx.locale),
                    handoff=_no_handoff(), scope=_scope(ctx, source=SCOPE_CUTOFF))
        return

    # 8. Triage: one small call, no passages, decides what kind of message this is — under
    #    EVERY policy. `kb_only` differs only in what a related question gets (the refusal and a
    #    colleague instead of a general answer), `open` only in what an off-topic one gets (a
    #    short general answer instead of the redirect). So a refusal now costs one small call
    #    where it used to cost none: that is the price of telling "what day is it?" from "what
    #    are your prices?", and a joke from a customer. The free exits that remain are the two
    #    off switches, an empty KB and the off-topic cut-off.
    t = time.monotonic()
    try:
        raw = await llm.call_tool(
            feature="triage", client_id=ctx.client_id, integration_id=ctx.integration_id,
            api_key=ctx.api_key, model=ctx.model,
            system=chat_prompts.build_triage_system(
                ctx.cfg, locale=ctx.locale, policy=policy,
                business_name=ctx.business_name, industry=ctx.industry),
            user=chat_prompts.build_triage_user(
                messages=ctx.messages, envelope=_inbound(ctx),
                clock=chat_prompts.clock_block(ctx.cfg, ctx.now),
                doc_titles=[h.get("title") for h in hits], max_chars=max_chars),
            tool=chat_prompts.TRIAGE_TOOL, opts=llm.ANSWER, max_tokens=max_tokens,
            # The system prompt is tenant-stable (the clock is in the user block), so it caches.
            cache_system=True,
            conversation_id=ctx.conversation_id, turn_id=ctx.turn_id,
            suggest_ref=ctx.suggest_ref)
    except llm.LLMError as exc:
        stages["triage"] = _ms(t)
        log.warning("autopilot triage failed (client=%s): %s", ctx.client_id, exc)
        yield ev("error", {"code": _llm_code(exc), "message": str(exc), "fatal": False})
        yield _done(retrieval=r, reason=REASON_LLM_ERROR, text=refusal,
                    handoff=_handoff(REASON_LLM_ERROR, ctx),
                    scope=_scope(ctx, source=SCOPE_TRIAGE))
        return
    stages["triage"] = _ms(t)

    raw = raw if isinstance(raw, dict) else {}
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in chat_prompts.TRIAGE_KINDS:
        # A strict schema makes this unlikely, never impossible. "business" is the reading that
        # can only end in the documents or a colleague.
        kind = KIND_BUSINESS
    model_text = _free_text(str(raw.get("reply") or ""), max_chars)

    if kind == KIND_BUSINESS:
        if grounded:
            async for event in _grounded_answer(SCOPE_TRIAGE, kind):
                yield event
            return
        yield _done(retrieval=r, reason=reason, text=refusal, handoff=_handoff(reason, ctx),
                    scope=_scope(ctx, source=SCOPE_TRIAGE, kind=kind))
        return

    if kind == KIND_RISKY:
        handoff = await _handoff_with_summary(REASON_RISKY, ctx, stages)
        yield _done(retrieval=r, reason=weak_reason,
                    text=model_text or chat_prompts.safety_notice_text(ctx.cfg, ctx.locale),
                    handoff=handoff, scope=_scope(ctx, source=SCOPE_TRIAGE, kind=kind))
        return

    if kind == KIND_OFF_TOPIC:
        count = ctx.off_topic_count + 1
        answered = False
        if cutoff_after and count >= cutoff_after:
            text = chat_prompts.off_topic_cutoff_text(ctx.cfg, ctx.locale)
        else:
            text = model_text
            if policy == "open" and text:
                # Under `open` the model's reply is an ANSWER, not a redirect, and it was written
                # without the documents — so a price, deadline or promise in it is unbacked by
                # definition. Nor is it worth a colleague's time (the customer asked about
                # something else entirely), so it is withheld rather than handed off.
                commitment = chat_safety.detect_commitment(text)
                if commitment:
                    log.info("autopilot off-topic answer withheld: commitment:%s (client=%s)",
                             commitment, ctx.client_id)
                    text = ""
                answered = bool(text)
            text = text or chat_prompts.off_topic_redirect_text(ctx.locale)
            if warn_after and count == warn_after:
                text = f"{text}\n\n{chat_prompts.off_topic_warning_text(ctx.cfg, ctx.locale)}"
        yield _done(retrieval=r, reason=weak_reason, text=text, handoff=_no_handoff(),
                    scope=_scope(ctx, source=SCOPE_TRIAGE, kind=kind, answered=answered,
                                 count=count))
        return

    if kind == KIND_RELATED and policy not in ("general", "open"):
        yield _done(retrieval=r, reason=weak_reason, text=refusal,
                    handoff=_handoff(REASON_RELATED_NOT_IN_KB, ctx),
                    scope=_scope(ctx, source=SCOPE_TRIAGE, kind=kind))
        return

    # Chitchat, or a related question under `general` or `open`: the model's own short reply.
    if not model_text:
        yield _done(retrieval=r, reason=REASON_LLM_ERROR, text=refusal,
                    handoff=_handoff(REASON_LLM_ERROR, ctx),
                    scope=_scope(ctx, source=SCOPE_TRIAGE, kind=kind))
        return
    handoff = _no_handoff()
    # No passages here, so ANY price, deadline or promise in a reply written without the
    # documents is unbacked by definition and goes to a human.
    commitment = chat_safety.detect_commitment(model_text)
    if commitment:
        handoff = await _handoff_with_summary(f"commitment:{commitment}", ctx, stages)
    yield _done(retrieval=r, reason=weak_reason, text=model_text, handoff=handoff,
                scope=_scope(ctx, source=SCOPE_TRIAGE, kind=kind, answered=True))
