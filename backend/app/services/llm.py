"""One front door for every text-model call: resolved, bounded, memoized, and metered.

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

Plus admission control: `_LLM_SEM` caps in-flight model calls, and a caller that cannot get a
slot within a second gets an error (routers turn `LLMBusyError` into a 429) rather than
queueing behind an unbounded backlog and timing out anyway.

And now a fourth: **which provider.** A tenant may run on Anthropic, OpenAI or Gemini — a
superadmin-assigned connection, or a key of their own — and the fifteen call sites must not
know or care. `call_tool` and `stream_text` keep the signatures they always had; they ask
`ai_resolve.resolve()` whose provider/model/key this call runs on, then hand the call to that
provider's adapter (`providers/llm_*.py`). The adapters speak the wire formats; this file owns
everything a call site can observe — the exceptions, the truncation check, the usage row.
Anything that reads a key from settings and talks to a model WITHOUT going through here would
silently bypass a tenant's configuration, so new AI code goes through `llm.py`.
"""
import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable

import anthropic

from ..config import settings
from ..db import pool
from . import ai_resolve, attribution
from .ai_resolve import Resolved
from .providers import llm_anthropic, llm_gemini, llm_openai
from .providers.llm_base import (  # noqa: F401 — re-exported: call sites use llm.LLMError etc.
    MAX_TOKENS,
    NARROW_TOKENS_PER_CHAR,
    WIDE_TOKENS_PER_CHAR,
    LLMAdapter,
    LLMBusyError,
    LLMError,
    LLMTruncatedError,
    StreamUsage,
    ToolResult,
    estimate_tokens,
    usage_of,
)

log = logging.getLogger("cq")

# Per-feature timeout/retry profiles. Pass one of these as `opts=`.
# connect is short everywhere (a slow TCP/TLS handshake is a dead upstream, not a slow model);
# the read budget is what differs — batch work may wait, an interactive turn may not.
#
# anthropic.Timeout, NOT httpx.Timeout: since the SDK vendored its HTTP stack (httpx2), a
# Timeout built from the app-level httpx is a foreign object inside it, and every request
# dies in the connect phase as APIConnectionError('Connection error.') — which silently took
# every Claude feature down at once on the first image rebuild after the SDK upgrade. The
# HTTP adapters (OpenAI, Gemini) read the same four fields off it into their own httpx.
ANALYSIS = dict(timeout=anthropic.Timeout(60.0, connect=2.0), max_retries=1)
COPILOT = dict(timeout=anthropic.Timeout(6.0, connect=1.0), max_retries=0)
ANSWER = dict(timeout=anthropic.Timeout(25.0, connect=1.0), max_retries=1)
CURATE = dict(timeout=anthropic.Timeout(60.0, connect=2.0), max_retries=1)
# Background import work: nobody is staring at a spinner, and one segment can be 12k chars
# of scorecard rows that all have to come back out as entries — give it a long read budget.
RESTRUCTURE = dict(timeout=anthropic.Timeout(180.0, connect=2.0), max_retries=1)
# The registry's "Test connection" button: a human is waiting, and a retry would only hide
# the very failure they are trying to see.
PROBE = dict(timeout=anthropic.Timeout(20.0, connect=3.0), max_retries=0)

# How long a caller waits for an admission slot before being told to come back later.
ADMIT_TIMEOUT_S = 1.0


# Memoized Anthropic SDK clients, keyed by (api_key, timeout, max_retries, base_url). Never
# closed: they are process-lifetime connection pools, and closing one mid-flight would break
# another caller. This is safe ONLY because the API runs a single uvicorn worker (no --workers
# in backend/Dockerfile) — with more than one worker each would hold its own copy, which is
# still correct but multiplies the real concurrency ceiling below.
_clients: dict[tuple, anthropic.AsyncAnthropic] = {}

# Admission control. One worker means this semaphore IS the service's model concurrency,
# whichever provider answers.
_LLM_SEM = asyncio.Semaphore(settings.llm_max_concurrency)

# Strong refs to in-flight accounting tasks — asyncio only weakly references tasks, so
# without this a usage write can be garbage-collected before it runs.
_usage_tasks: set[asyncio.Task] = set()


def client(api_key: str, *, timeout, max_retries: int,
           base_url: str | None = None) -> anthropic.AsyncAnthropic:
    key = (api_key, repr(timeout), max_retries, base_url)
    inst = _clients.get(key)
    if inst is None:
        inst = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout,
                                        max_retries=max_retries,
                                        **({"base_url": base_url} if base_url else {}))
        _clients[key] = inst
    return inst


# Catalog id -> adapter. Adding a provider is one module under providers/ plus one line here.
# The Anthropic adapter borrows `client()` above rather than owning a pool of its own, and it
# is handed a lambda rather than the function so a test that fakes the SDK (by patching
# `llm.client`) is honoured — the name is looked up at call time, not at import.
_ADAPTERS: dict[str, LLMAdapter] = {
    "anthropic": llm_anthropic.AnthropicAdapter(lambda api_key, **kw: client(api_key, **kw)),
    "openai": llm_openai.OpenAIAdapter(),
    "gemini": llm_gemini.GeminiAdapter(),
}


def _adapter(res: Resolved) -> LLMAdapter:
    adapter = _ADAPTERS.get(res.provider)
    if adapter is None:
        raise LLMError(f"No text-AI adapter for provider {res.provider!r}.")
    return adapter


async def _resolve(client_id: str | None, api_key: str, model: str) -> Resolved:
    """Whose provider, model and key this call runs on.

    `api_key` and `model` are what the call site read from the legacy deployment settings;
    they are the bottom of the resolution chain (see ai_resolve) and are only used when no
    registry layer sets a value. Doing it at this one chokepoint is why the call sites do
    not each have to remember to.

    A CONFIG LOOKUP MUST NEVER BREAK AN AI CALL — the rule the old per-tenant overlay kept,
    enforced here rather than trusted: if the resolver cannot be read at all (no pool, a
    registry table missing mid-migration), the call runs on the legacy layer the caller
    already holds — Anthropic, with the deployment key and model — and the failure is logged.
    """
    try:
        res = await ai_resolve.resolve(client_id, "llm", api_key=api_key, model=model)
    except Exception:  # noqa: BLE001 — see docstring
        log.exception("AI resolution failed for %s; running on the legacy settings", client_id)
        legacy = getattr(ai_resolve, "LEGACY_PROVIDER", {}).get("llm", "anthropic")
        res = Resolved("llm", legacy, model or None, api_key or "", None)
    if not res.model:
        raise LLMError(f"No model is configured for the {res.provider} text AI.")
    return res


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


def _record(*, feature: str, client_id: str | None, integration_id: str | None, model: str,
            message=None, usage: dict | None = None, latency_ms: int, ok: bool,
            byo: bool = False, actor: str | None = None, job_id: str | None = None,
            provider: str | None = None, connection_id: str | None = None) -> None:
    """Fire-and-forget one `llm_usage` row. Accounting must never fail a turn.

    Deliberately not awaited and deliberately not holding a pool connection across the LLM
    call itself: the write happens after the response is already in hand.

    Every argument is optional-with-a-default for a reason learned the hard way: this is
    called from an `except` path, so a signature mismatch here does not surface as a broken
    metric — it replaces the real API error with a TypeError and takes the whole feature down.

    `usage` is the adapter's normalised block (`ToolResult["usage"]`); `message` is the older
    spelling, an Anthropic-SDK-shaped response, still read when `usage` is not given.

    `actor` and `job_id` normally come from the request context rather than the call site
    (see `attribution`): the fifteen places that reach a model sit several frames below the
    request and would each have to thread two arguments they otherwise have no use for. An
    explicit argument still wins, for a caller that genuinely knows better.

    `provider` and `connection_id` say WHOSE money and WHICH registry connection — with
    `byo` they are what lets spend be grouped by provider and charged to the right party.
    """
    ctx_actor, ctx_job = attribution.current()
    actor = actor or ctx_actor
    job_id = job_id or ctx_job
    if usage is None and message is not None:
        usage = usage_of(message)
    usage = usage or {}
    row = (
        client_id,
        integration_id,
        feature,
        model,
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_read_tokens"),
        usage.get("cache_creation_tokens"),
        latency_ms,
        ok,
        actor,
        job_id,
        byo,
        provider,
        connection_id,
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
                                       cache_read_tokens, cache_creation_tokens, latency_ms, ok,
                                       actor, job_id, byo, provider, connection_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::uuid, $13, $14,
                        $15::uuid)
                """, *row)
    except Exception as exc:  # noqa: BLE001 — cost accounting can never break a turn
        log.warning("llm_usage write failed: %s", exc)


async def call_tool(*, feature: str, client_id: str | None, api_key: str, model: str,
                    system: str, user: str, tool: dict, opts: dict,
                    max_tokens: int = 4096, cache_system: bool = False,
                    integration_id: str | None = None,
                    admit_timeout_s: float = ADMIT_TIMEOUT_S,
                    stream: bool = False,
                    on_progress: Callable[[int], None] | None = None,
                    actor: str | None = None, job_id: str | None = None) -> dict:
    """The house forced-tool-use pattern: one tool, tool_choice pinned to it, strict schema.

    Returns the tool_use block's input as a plain dict. Raises LLMError if the model answered
    without calling the tool (which forcing makes very unlikely, but never impossible), and
    LLMTruncatedError if the answer hit max_tokens — a truncated tool input parses as a
    smaller-but-valid dict, so without this check the caller silently loses the cut-off tail.

    `tool` is written in the house (Anthropic) shape; each adapter translates it and its
    schema into what its provider's strict mode accepts (`llm_base.translate_schema`).

    `stream=True` transports the SAME call over SSE and collects the final result — identical
    output. It exists because Anthropic drops long NON-streaming requests ("Request timed out
    or interrupted... long-requests"): a big model writing thousands of tokens of dense
    Georgian guidance takes minutes, which only a stream survives. Callers whose outputs can
    be big (restructure, rubric import) must pass it; the other adapters honour it too.

    `on_progress(cumulative_output_tokens)` turns that same stream into a progress signal
    (`stream=True` only — a blocking call has nothing to say until it is over). Without it
    the deltas are consumed and discarded, which is what every other caller here still does:
    when it is None the adapter runs the code path it has always run.

    `api_key` and `model` are the DEPLOYMENT default; the registry (a default or assigned
    connection) or the tenant's own key is substituted here — see `_resolve`.
    """
    res = await _resolve(client_id, api_key, model)
    adapter = _adapter(res)
    started = time.monotonic()
    async with _admit(feature, timeout_s=admit_timeout_s):
        try:
            result = await adapter.call_tool(
                res, system=system, user=user, tool=tool, max_tokens=max_tokens,
                cache_system=cache_system, stream=stream, on_progress=on_progress, opts=opts)
        except LLMError:
            _record(feature=feature, client_id=client_id, integration_id=integration_id,
                    model=res.model, usage=None,
                    latency_ms=int((time.monotonic() - started) * 1000), ok=False,
                    actor=actor, job_id=job_id, byo=res.byo,
                    provider=res.provider, connection_id=res.connection_id)
            raise

    _record(feature=feature, client_id=client_id, integration_id=integration_id,
            model=res.model, usage=result["usage"],
            latency_ms=int((time.monotonic() - started) * 1000), ok=True,
            actor=actor, job_id=job_id, byo=res.byo,
            provider=res.provider, connection_id=res.connection_id)

    if result["stop_reason"] == MAX_TOKENS:
        raise LLMTruncatedError(
            f"The model ran out of output budget ({max_tokens} tokens) before finishing.")
    if result["input"] is None:
        raise LLMError(f"{adapter.label} did not return a {tool['name']} result.")
    return result["input"]


async def stream_text(*, feature: str, client_id: str | None, api_key: str, model: str,
                      system: str, user: str, opts: dict,
                      max_tokens: int = 1024,
                      integration_id: str | None = None) -> AsyncIterator[str]:
    """Yield plain text deltas. The admission slot is held for the whole stream, because an
    open stream is exactly as much upstream concurrency as a blocking call.

    Resolves the tenant's provider for the same reason `call_tool` does."""
    res = await _resolve(client_id, api_key, model)
    adapter = _adapter(res)
    holder = StreamUsage()
    started = time.monotonic()
    async with _admit(feature):
        try:
            async for text in adapter.stream_text(res, system=system, user=user,
                                                  max_tokens=max_tokens, opts=opts,
                                                  usage=holder):
                yield text
        except LLMError:
            _record(feature=feature, client_id=client_id, integration_id=integration_id,
                    model=res.model, usage=None,
                    latency_ms=int((time.monotonic() - started) * 1000), ok=False,
                    byo=res.byo, provider=res.provider, connection_id=res.connection_id)
            raise

    _record(feature=feature, client_id=client_id, integration_id=integration_id,
            model=holder.model or res.model, usage=holder.usage,
            latency_ms=int((time.monotonic() - started) * 1000), ok=True,
            byo=res.byo, provider=res.provider, connection_id=res.connection_id)


async def probe(res: Resolved, *, client_id: str | None = None) -> dict:
    """Does exactly this provider/model/key answer? For the registry's "Test connection".

    One tiny forced tool call ("reply with {ok: true}") on the adapter `res.provider` names,
    returning {"ok": bool, "detail": str} with plain English either way — never raising, so
    a router can hand the outcome straight back to the button. The call is admitted and
    metered like any other (feature "probe"), because it spends real tokens on somebody's key;
    `client_id` is only attribution for a tenant testing their own override.
    """
    try:
        adapter = _adapter(res)
    except LLMError as exc:
        return {"ok": False, "detail": str(exc)}
    if not res.model:
        return {"ok": False, "detail": "No model is set for this connection — pick one first."}
    started = time.monotonic()
    try:
        async with _admit("probe"):
            outcome = await adapter.probe(res, opts=PROBE)
    except LLMBusyError as exc:
        return {"ok": False, "detail": str(exc)}
    result = outcome.get("result")
    _record(feature="probe", client_id=client_id, integration_id=None, model=res.model,
            usage=result["usage"] if result else None,
            latency_ms=int((time.monotonic() - started) * 1000), ok=bool(outcome["ok"]),
            byo=res.byo, provider=res.provider, connection_id=res.connection_id)
    return {"ok": bool(outcome["ok"]), "detail": outcome["detail"]}
