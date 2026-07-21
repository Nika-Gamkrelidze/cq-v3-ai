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

**2. The ladder exists because the cold path is slow.** Tier 1 is the top-3 KB passage
cards, built from retrieval hits with no model involved, emitted as its own event before any
token exists (~300 ms). Tier 2 is the drafts (~2 s). An operator whose prefetch missed sees
something useful immediately, and still sees it when the LLM call times out.

**3. Query building has no LLM hop.** `build_query()` is string manipulation. A query-rewrite
call costs 300-600 ms on the one path the product owner called latency-critical, and both
strings it returns go to `retrieve_ranked` in a *single* embedding batch.

This module never touches the database. Persistence belongs to `chat_store.py` and the
routers; the engine is given its config and its history and hands back events. That keeps
the "never hold a pool connection across an LLM await" rule true by construction here.
"""
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from . import chat_prompts, llm
from .retrieval import retrieve_ranked

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
}

# Gate outcomes. Stable strings — they are stored on the turn row, drive the curation
# harvest ("everything the bot could not ground" is an index scan), and are shown to
# operators. Renaming one silently reclassifies history.
REASON_OK = "ok"
REASON_KB_EMPTY = "kb_empty"          # the tenant has no KB at all — a different message
REASON_NO_HITS = "no_hits"            # KB exists, nothing matched
REASON_LOW_SCORE = "low_score"        # matched, but not well enough to speak
REASON_KEYWORD_ONLY = "keyword_only"  # trigram fallback only — see `strict` below
REASON_DISABLED = "autopilot_off"     # kill switch / never enabled


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
    conversation_ref: str | None = None
    # The raw inbound envelope (display_name, attachment, channel, text). Quarantined
    # wholesale by chat_prompts.wrap_untrusted — see that module for why it is not just text.
    envelope: dict = field(default_factory=dict)


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
                        model: str | None = None) -> dict:
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


def _handoff(reason: str, ctx: ChatContext) -> dict:
    last = ""
    for m in reversed(ctx.messages or []):
        if str(m.get("role") or "").lower() == "customer":
            last = str(m.get("content") or "").strip()[:400]
            break
    return {"recommended": True, "reason": reason, "summary": last or None}


def _envelope_for(ctx: ChatContext, **kw) -> dict:
    return build_turn_envelope(
        client_id=ctx.client_id, suggest_ref=ctx.suggest_ref,
        conversation_ref=ctx.conversation_ref or ctx.conversation_id,
        turn_ref=ctx.turn_ref, channel=ctx.channel, locale=ctx.locale,
        model=ctx.model, **kw)


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
        return [], {"method": "none", "top_score": None, "kb_present": False, "hits": []}, \
            False, REASON_NO_HITS

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
            cache_system=True)
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
    because it is very likely a cached config row shared by concurrent turns."""
    return ChatContext(
        client_id=ctx.client_id, conversation_id=ctx.conversation_id,
        suggest_ref=ctx.suggest_ref, locale=ctx.locale, messages=ctx.messages, cfg=cfg,
        api_key=ctx.api_key, model=ctx.model, mode=ctx.mode,
        integration_id=ctx.integration_id, channel=ctx.channel, turn_ref=ctx.turn_ref,
        conversation_ref=ctx.conversation_ref, envelope=ctx.envelope)


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


# --- run_answer: the public autopilot -----------------------------------------

async def run_answer(ctx: ChatContext) -> AsyncIterator[ChatEvent]:
    """Answer the customer directly. No human in the loop, so every default is the strict one.

    Differences from `run_suggest`, all of them deliberate:
      * `visibility='public'` — only KB documents a human explicitly published are visible.
      * `strict` defaults to True — a trigram-only match is not grounding out here.
      * `autopilot_enabled` is checked first and is OFF by default; it is the kill switch,
        read from the tenant's config so disabling it never requires a redeploy (every deploy
        also blanket-errors in-flight audio jobs).
      * The reply streams, so this is the one path using `llm.stream_text` rather than a
        forced tool. That is *less* model capability, not more — the ADR's "no tools beyond a
        terminal submit_answer" bar is met by giving it no tools at all; retrieval already
        happened in code above.
    """
    ev = _Seq()
    started = time.monotonic()
    stages: dict = {}
    yield ev("open", {"suggest_ref": ctx.suggest_ref, "mode": ctx.mode,
                      "conversation_ref": ctx.conversation_ref or ctx.conversation_id,
                      "locale": chat_prompts.normalize_locale(ctx.locale), "proto": PROTO})

    refusal = chat_prompts.refusal_text(ctx.cfg, ctx.locale)

    if not bool(_cfg(ctx.cfg, "autopilot_enabled")):
        stages["total"] = _ms(started)
        yield ev("done", _envelope_for(
            ctx, retrieval=None, grounded=False, reason=REASON_DISABLED,
            reply={"text": refusal, "citations": [], "answered_from_kb": False},
            handoff=_handoff(REASON_DISABLED, ctx), stages=stages))
        return

    cfg = dict(ctx.cfg or {})
    cfg.setdefault("strict", True)
    ctx = _with_cfg(ctx, cfg)

    queries, r, grounded, reason = await _ground(ctx, visibility="public", stages=stages)
    hits = r.get("hits") or []
    yield ev("grounding", {"grounded": grounded, "reason": reason,
                           "method": r.get("method"), "top_score": r.get("top_score"),
                           "hit_count": len(hits), "kb_present": bool(r.get("kb_present"))})

    if not grounded:
        # The zero-token refusal. Note there is no tier1 event here: passage cards are an
        # operator affordance, and dumping raw KB excerpts at a customer is not an answer.
        stages["total"] = _ms(started)
        yield ev("done", _envelope_for(
            ctx, retrieval=r, grounded=False, reason=reason,
            reply={"text": refusal, "citations": [], "answered_from_kb": False},
            handoff=_handoff(reason, ctx), stages=stages))
        return

    context_hits = hits[:int(_num(_cfg(ctx.cfg, "context_hits"), DEFAULTS["context_hits"]))]
    system = chat_prompts.build_system(ctx.cfg, mode="autopilot", locale=ctx.locale)
    user = chat_prompts.build_user(
        hits=context_hits, messages=ctx.messages, envelope=_inbound(ctx),
        directive="Reply to the customer in one short message. Cite the passages you used "
                  "with inline [n] markers.")

    t = time.monotonic()
    chunks: list[str] = []
    try:
        async for delta in llm.stream_text(
                feature="autopilot", client_id=ctx.client_id, integration_id=ctx.integration_id,
                api_key=ctx.api_key, model=ctx.model, system=system, user=user,
                opts=llm.ANSWER,
                max_tokens=int(_num(_cfg(ctx.cfg, "max_tokens"), DEFAULTS["max_tokens"]))):
            chunks.append(delta)
            # Deltas are raw model text. They are a progressive-rendering nicety; the
            # authoritative, sanitized, citation-resolved text is the one on `done`, and a
            # consumer that shows deltas must replace them with it.
            yield ev("delta", {"text": delta})
    except llm.LLMError as exc:
        stages["llm"] = _ms(t)
        stages["total"] = _ms(started)
        log.warning("autopilot answer failed (client=%s): %s", ctx.client_id, exc)
        yield ev("error", {"code": "llm_busy" if isinstance(exc, llm.LLMBusyError) else "llm_error",
                           "message": str(exc), "fatal": False})
        # Falls back to the refusal rather than silence: the customer is waiting, and a
        # handoff is a correct outcome for an outage.
        yield ev("done", _envelope_for(
            ctx, retrieval=r, grounded=True, reason=reason,
            reply={"text": refusal, "citations": [], "answered_from_kb": False},
            handoff=_handoff("llm_error", ctx), stages=stages))
        return
    stages["llm"] = _ms(t)

    text = chat_prompts.sanitize_output("".join(chunks), context_hits)
    text, cites = chat_prompts.resolve_citations(text, context_hits)
    if not text:
        text, cites = refusal, []

    handoff = {"recommended": False, "reason": None, "summary": None}
    if chat_prompts.looks_like_commitment(text):
        # Commitment-shaped output forces a handoff even when perfectly grounded. The deltas
        # have already left the building, so this cannot retract the text — it flags the turn
        # for a human, which is the actionable half. Suppressing before the first delta would
        # need a non-streaming autopilot; if a tenant wants that guarantee, that is the trade.
        handoff = _handoff("commitment", ctx)

    stages["total"] = _ms(started)
    yield ev("done", _envelope_for(
        ctx, retrieval=r, grounded=True, reason=reason,
        reply={"text": text, "citations": cites, "answered_from_kb": True},
        handoff=handoff, stages=stages))
