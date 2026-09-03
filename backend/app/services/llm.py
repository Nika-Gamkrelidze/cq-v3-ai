"""One front door for every Anthropic call: bounded, memoized, and metered.

Why this file exists — three problems that every call site had independently:

1. **No timeout.** `claude.py`, `factcheck.py` and `scoring.py` each built a bare
   `anthropic.AsyncAnthropic(api_key=...)`, whose SDK defaults are a 600 s read timeout with
   `max_retries=2`. One hung upstream call could therefore pin the single uvicorn worker for
   ~20 minutes. Timeouts here are explicit and per-feature: an interactive copilot turn is not
   allowed to wait as long as a batch analysis.
2. **A fresh client per call.** Each call also did `await client.close()` in a `finally`, so
   every analysis paid a new TLS handshake. Clients are now memoized for the process lifetime.
3. **No accounting.** `message.usage` was read nowhere in the repo. Chat is 100-1000x the
   request volume of audio, so starting to record it only once chat ships would leave a
   permanently blind period on cost. Every call through here writes an `llm_usage` row.

Plus admission control: `_LLM_SEM` caps in-flight Anthropic calls, and a caller that cannot get
a slot within a second gets an error (routers turn `LLMBusyError` into a 429) rather than
queueing behind an unbounded backlog and timing out anyway.
"""
import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable

import anthropic

from ..config import settings
from ..db import pool

log = logging.getLogger("cq")

# Per-feature timeout/retry profiles. Pass one of these as `opts=`.
# connect is short everywhere (a slow TCP/TLS handshake is a dead upstream, not a slow model);
# the read budget is what differs — batch work may wait, an interactive turn may not.
#
# anthropic.Timeout, NOT httpx.Timeout: since the SDK vendored its HTTP stack (httpx2), a
# Timeout built from the app-level httpx is a foreign object inside it, and every request
# dies in the connect phase as APIConnectionError('Connection error.') — which silently took
# every Claude feature down at once on the first image rebuild after the SDK upgrade.
ANALYSIS = dict(timeout=anthropic.Timeout(60.0, connect=2.0), max_retries=1)
COPILOT = dict(timeout=anthropic.Timeout(6.0, connect=1.0), max_retries=0)
ANSWER = dict(timeout=anthropic.Timeout(25.0, connect=1.0), max_retries=1)
CURATE = dict(timeout=anthropic.Timeout(60.0, connect=2.0), max_retries=1)
# Background import work: nobody is staring at a spinner, and one segment can be 12k chars
# of scorecard rows that all have to come back out as entries — give it a long read budget.
RESTRUCTURE = dict(timeout=anthropic.Timeout(180.0, connect=2.0), max_retries=1)

# How long a caller waits for an admission slot before being told to come back later.
ADMIT_TIMEOUT_S = 1.0

# Output sizing, in ONE place. Byte-level BPE splits scripts outside its merge vocabulary far
# harder than Latin text: measured on cl100k, Georgian runs ~0.53 chars/token against ~6.2 for
# English, a ~12x difference that no single chars-per-token constant can express.
#
# Two callers share it and they MUST share it: `scoring_import.estimate_output_tokens` sizes
# the budget a document needs to come back verbatim, and `_stream_progress` below measures
# what has actually come back. Those two numbers become the denominator and the numerator of
# a progress bar — measure them with different yardsticks and the percentage is fiction, even
# though both halves would look individually reasonable.
WIDE_TOKENS_PER_CHAR = 2.0     # Georgian, Armenian, CJK...  (measured ~1.9, rounded up)
NARROW_TOKENS_PER_CHAR = 0.25  # Latin, digits, punctuation  (measured ~0.16, rounded up)


def estimate_tokens(text: str) -> float:
    """Rough token count for `text`, counting the two script populations apart.

    Float rather than int on purpose: a stream is measured chunk by chunk, and rounding every
    one- or two-character fragment to a whole token would throw most of the count away.
    """
    wide = sum(1 for ch in text if ord(ch) > 0x02FF)
    return wide * WIDE_TOKENS_PER_CHAR + (len(text) - wide) * NARROW_TOKENS_PER_CHAR


class LLMError(RuntimeError):
    """Any failure talking to Anthropic, or a malformed/absent structured result."""


class LLMBusyError(LLMError):
    """Admission control rejected the call — the service is at its concurrency ceiling."""


class LLMTruncatedError(LLMError):
    """The model hit max_tokens mid-answer. A forced tool call cut off at the budget comes
    back HTTP 200 with a PARTIAL tool input — treating it as success silently loses data,
    so callers must either shrink the work and retry, or fail loudly."""


# Memoized clients, keyed by (api_key, timeout, max_retries). Never closed: they are
# process-lifetime connection pools, and closing one mid-flight would break another caller.
# This is safe ONLY because the API runs a single uvicorn worker (no --workers in
# backend/Dockerfile) — with more than one worker each would hold its own copy, which is
# still correct but multiplies the real concurrency ceiling below.
_clients: dict[tuple, anthropic.AsyncAnthropic] = {}

# Admission control. One worker means this semaphore IS the service's Anthropic concurrency.
_LLM_SEM = asyncio.Semaphore(settings.llm_max_concurrency)

# Strong refs to in-flight accounting tasks — asyncio only weakly references tasks, so
# without this a usage write can be garbage-collected before it runs.
_usage_tasks: set[asyncio.Task] = set()


def client(api_key: str, *, timeout, max_retries: int) -> anthropic.AsyncAnthropic:
    key = (api_key, repr(timeout), max_retries)
    inst = _clients.get(key)
    if inst is None:
        inst = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)
        _clients[key] = inst
    return inst


@contextlib.asynccontextmanager
async def _admit(feature: str, timeout_s: float = ADMIT_TIMEOUT_S):
    """The 1s default exists so interactive routes can 429 fast. Background callers
    (imports, batch work) pass a long timeout_s instead — the one caller that can afford
    to wait for a slot must not be the one that gives up after a second."""
    try:
        await asyncio.wait_for(_LLM_SEM.acquire(), timeout=timeout_s)
    except asyncio.TimeoutError:
        log.warning("llm admission rejected (feature=%s, limit=%s)", feature,
                    settings.llm_max_concurrency)
        raise LLMBusyError("The AI service is busy right now — please retry in a moment.") from None
    try:
        yield
    finally:
        _LLM_SEM.release()


def _system_param(system: str, cache_system: bool):
    """System prompt as-is, or as a single cacheable block.

    Prompt caching needs the block form; keep the plain string by default so every existing
    call site sends byte-identical requests to what it sent before.
    """
    if not system:
        return anthropic.NOT_GIVEN
    if not cache_system:
        return system
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _record(*, feature: str, client_id: str | None, integration_id: str | None, model: str,
            message, latency_ms: int, ok: bool) -> None:
    """Fire-and-forget one `llm_usage` row. Accounting must never fail a turn.

    Deliberately not awaited and deliberately not holding a pool connection across the LLM
    call itself: the write happens after the response is already in hand.
    """
    usage = getattr(message, "usage", None)
    row = (
        client_id,
        integration_id,
        feature,
        model,
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        # Only present when prompt caching is in play; older/other responses omit them.
        getattr(usage, "cache_read_input_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        latency_ms,
        ok,
    )
    try:
        task = asyncio.create_task(_write_usage(row))
    except RuntimeError:  # no running loop (shouldn't happen under uvicorn)
        return
    _usage_tasks.add(task)
    task.add_done_callback(_usage_tasks.discard)


async def _write_usage(row: tuple) -> None:
    try:
        async with pool().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_usage (client_id, integration_id, feature, model,
                                       input_tokens, output_tokens,
                                       cache_read_tokens, cache_creation_tokens, latency_ms, ok)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """, *row)
    except Exception as exc:  # noqa: BLE001 — cost accounting can never break a turn
        log.warning("llm_usage write failed: %s", exc)


async def _stream_progress(st, on_progress: Callable[[int], None]) -> None:
    """Drain a message stream, reporting cumulative output tokens as they are produced.

    WHICH EVENT, and why: a forced tool call writes its answer as `content_block_delta`
    events carrying `input_json_delta.partial_json` — fragments of the tool input's JSON, a
    few characters at a time. That is the only per-token signal this kind of call emits. The
    API's exact figure lives in `message_delta.usage.output_tokens` (cumulative, per the
    streaming docs), but the same docs promise only "one or more" `message_delta` events and
    a plain tool call sends one, after the last content block — exact, and far too late to
    move a progress bar with. So the fragments are measured with `estimate_tokens`, and the
    exact figure is folded in if it does arrive early, taking whichever source has seen more
    so the number can never run backwards.

    Matching the RAW event types also avoids double counting: the Python SDK's stream yields
    its own synthesized `text` / `input_json` events interleaved with the raw ones, and both
    describe the same bytes.

    `on_progress` is somebody's UI, not part of the call: it is only ever handed a growing
    integer, and if it raises, it is dropped and the import carries on without a bar.
    """
    tokens = 0.0
    sent = 0
    async for event in st:
        etype = getattr(event, "type", None)
        if etype == "content_block_delta":
            delta = getattr(event, "delta", None)
            chunk = getattr(delta, "partial_json", None) or getattr(delta, "text", None)
            if chunk:
                tokens += estimate_tokens(chunk)
        elif etype == "message_delta":
            exact = getattr(getattr(event, "usage", None), "output_tokens", None)
            if isinstance(exact, int) and exact > tokens:
                tokens = float(exact)
        else:
            continue
        count = int(tokens)
        if count > sent:
            sent = count
            try:
                on_progress(count)
            except Exception as exc:  # noqa: BLE001 — a broken bar must not fail the call
                log.warning("call_tool on_progress failed; progress dropped: %s", exc)
                return  # get_final_message() drains whatever is left


def _wrap(exc: anthropic.APIError) -> LLMError:
    # Same message shape the call sites already produced, so their error text is unchanged.
    return LLMError(getattr(exc, "message", None) or str(exc))


async def call_tool(*, feature: str, client_id: str | None, api_key: str, model: str,
                    system: str, user: str, tool: dict, opts: dict,
                    max_tokens: int = 4096, cache_system: bool = False,
                    integration_id: str | None = None,
                    admit_timeout_s: float = ADMIT_TIMEOUT_S,
                    stream: bool = False,
                    on_progress: Callable[[int], None] | None = None) -> dict:
    """The house forced-tool-use pattern: one tool, tool_choice pinned to it, strict schema.

    Returns the tool_use block's input as a plain dict. Raises LLMError if the model answered
    without calling the tool (which `tool_choice` makes very unlikely, but never impossible),
    and LLMTruncatedError if the answer hit max_tokens — a truncated tool input parses as a
    smaller-but-valid dict, so without this check the caller silently loses the cut-off tail.

    `stream=True` transports the SAME call over SSE and collects the final message — the
    result is identical. It exists because Anthropic drops long NON-streaming requests
    ("Request timed out or interrupted... long-requests"): a big model writing thousands of
    tokens of dense Georgian guidance takes minutes, which only a stream survives. Callers
    whose outputs can be big (restructure, rubric import) must pass it.

    `on_progress(cumulative_output_tokens)` turns that same stream into a progress signal
    (`stream=True` only — a blocking call has nothing to say until it is over). Without it
    the deltas are consumed by the SDK and discarded, which is what every other caller here
    still does: when it is None this function runs the code path it has always run.
    """
    cl = client(api_key, **opts)
    started = time.monotonic()
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=_system_param(system, cache_system),
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": user}],
    )
    async with _admit(feature, timeout_s=admit_timeout_s):
        try:
            if stream:
                async with cl.messages.stream(**kwargs) as st:
                    if on_progress is not None:
                        await _stream_progress(st, on_progress)
                    message = await st.get_final_message()
            else:
                message = await cl.messages.create(**kwargs)
        except anthropic.APIError as exc:
            _record(feature=feature, client_id=client_id, integration_id=integration_id,
                    model=model, message=None,
                    latency_ms=int((time.monotonic() - started) * 1000), ok=False)
            raise _wrap(exc) from exc

    _record(feature=feature, client_id=client_id, integration_id=integration_id,
            model=model, message=message,
            latency_ms=int((time.monotonic() - started) * 1000), ok=True)

    if getattr(message, "stop_reason", None) == "max_tokens":
        raise LLMTruncatedError(
            f"The model ran out of output budget ({max_tokens} tokens) before finishing.")
    for block in message.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            return dict(block.input)
    raise LLMError(f"Claude did not return a {tool['name']} result.")


async def stream_text(*, feature: str, client_id: str | None, api_key: str, model: str,
                      system: str, user: str, opts: dict,
                      max_tokens: int = 1024,
                      integration_id: str | None = None) -> AsyncIterator[str]:
    """Yield plain text deltas. The admission slot is held for the whole stream, because an
    open stream is exactly as much upstream concurrency as a blocking call."""
    cl = client(api_key, **opts)
    started = time.monotonic()
    async with _admit(feature):
        try:
            async with cl.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=_system_param(system, False),
                messages=[{"role": "user", "content": user}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                message = await stream.get_final_message()
        except anthropic.APIError as exc:
            _record(feature=feature, client_id=client_id, integration_id=integration_id,
                    model=model, message=None,
                    latency_ms=int((time.monotonic() - started) * 1000), ok=False)
            raise _wrap(exc) from exc

    _record(feature=feature, client_id=client_id, integration_id=integration_id,
            model=model, message=message,
            latency_ms=int((time.monotonic() - started) * 1000), ok=True)
