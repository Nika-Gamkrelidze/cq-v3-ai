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
from collections.abc import AsyncIterator

import anthropic
import httpx

from ..config import settings
from ..db import pool

log = logging.getLogger("cq")

# Per-feature timeout/retry profiles. Pass one of these as `opts=`.
# connect is short everywhere (a slow TCP/TLS handshake is a dead upstream, not a slow model);
# the read budget is what differs — batch work may wait, an interactive turn may not.
ANALYSIS = dict(timeout=httpx.Timeout(60.0, connect=2.0), max_retries=1)
COPILOT = dict(timeout=httpx.Timeout(6.0, connect=1.0), max_retries=0)
ANSWER = dict(timeout=httpx.Timeout(25.0, connect=1.0), max_retries=1)
CURATE = dict(timeout=httpx.Timeout(60.0, connect=2.0), max_retries=1)

# How long a caller waits for an admission slot before being told to come back later.
ADMIT_TIMEOUT_S = 1.0


class LLMError(RuntimeError):
    """Any failure talking to Anthropic, or a malformed/absent structured result."""


class LLMBusyError(LLMError):
    """Admission control rejected the call — the service is at its concurrency ceiling."""


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
async def _admit(feature: str):
    try:
        await asyncio.wait_for(_LLM_SEM.acquire(), timeout=ADMIT_TIMEOUT_S)
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


def _wrap(exc: anthropic.APIError) -> LLMError:
    # Same message shape the call sites already produced, so their error text is unchanged.
    return LLMError(getattr(exc, "message", None) or str(exc))


async def call_tool(*, feature: str, client_id: str | None, api_key: str, model: str,
                    system: str, user: str, tool: dict, opts: dict,
                    max_tokens: int = 4096, cache_system: bool = False,
                    integration_id: str | None = None) -> dict:
    """The house forced-tool-use pattern: one tool, tool_choice pinned to it, strict schema.

    Returns the tool_use block's input as a plain dict. Raises LLMError if the model answered
    without calling the tool (which `tool_choice` makes very unlikely, but never impossible).
    """
    cl = client(api_key, **opts)
    started = time.monotonic()
    async with _admit(feature):
        try:
            message = await cl.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=_system_param(system, cache_system),
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as exc:
            _record(feature=feature, client_id=client_id, integration_id=integration_id,
                    model=model, message=None,
                    latency_ms=int((time.monotonic() - started) * 1000), ok=False)
            raise _wrap(exc) from exc

    _record(feature=feature, client_id=client_id, integration_id=integration_id,
            model=model, message=message,
            latency_ms=int((time.monotonic() - started) * 1000), ok=True)

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
