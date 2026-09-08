"""What every text-model adapter looks like, and the pieces they share.

`services/llm.py` is the front door the fifteen call sites use; it resolves WHICH provider a
tenant runs on (`services/ai_resolve.py`) and hands the call to one adapter here. The adapter's
job is narrow: turn the house forced-tool-use call into that provider's wire format, and turn
the answer back into one shape — `ToolResult` — so llm.py can meter, check for truncation and
return the tool input exactly as it always has, whoever answered.

Contract, in one place (llm.py enforces it, adapters honour it):

- An adapter NEVER raises a provider SDK's exception. Everything is translated into the
  `LLMError` family below, so the call sites' `except llm.LLMError` keeps working unchanged:
    * `LLMBusyError`      — the provider said "later" (HTTP 429 / 5xx from an HTTP adapter), or
                            admission control did (raised by llm.py, never by an adapter).
    * `LLMTruncatedError` — raised by llm.py from `ToolResult.stop_reason == MAX_TOKENS`; the
                            adapter only NORMALISES the provider's own vocabulary into that
                            constant. Usage is recorded before the raise, so a cut-off answer
                            is still paid for in the ledger.
    * `LLMError`          — everything else, with the provider's own message when it gave one.
- `ToolResult.input` is the parsed tool arguments, or None when the model did not call the
  tool (or its arguments were unparseable, which is what a truncated call looks like). llm.py
  checks truncation FIRST, then complains about a missing tool call — in that order, because a
  budget-cut answer is a different failure from a model that ignored the tool.
- Token counts follow Anthropic's convention, because `llm_usage` was designed around it:
  `input_tokens` is the UNCACHED input. OpenAI's `prompt_tokens` and Gemini's `promptTokenCount`
  are totals that include the cached part, so those adapters subtract `cache_read_tokens` out —
  otherwise a later cost calculation would bill cached tokens twice.
- `stream_text` is an async generator; usage cannot travel through `yield`, so it lands in the
  `StreamUsage` holder the caller passed, once the stream has finished.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

import httpx

from ..ai_resolve import Resolved

log = logging.getLogger("cq")


# --------------------------------------------------------------------------- #
# Errors — the ONLY exceptions an adapter may let out
# --------------------------------------------------------------------------- #
class LLMError(RuntimeError):
    """Any failure talking to a text-model provider, or a malformed/absent structured result."""


class LLMBusyError(LLMError):
    """Come back later: admission control rejected the call (the service is at its concurrency
    ceiling), or the provider itself answered 429 / 5xx. Routers turn this into a 429."""


class LLMTruncatedError(LLMError):
    """The model hit max_tokens mid-answer. A forced tool call cut off at the budget comes
    back HTTP 200 with a PARTIAL tool input — treating it as success silently loses data,
    so callers must either shrink the work and retry, or fail loudly."""


# --------------------------------------------------------------------------- #
# Output sizing, in ONE place
# --------------------------------------------------------------------------- #
# Byte-level BPE splits scripts outside its merge vocabulary far harder than Latin text:
# measured on cl100k, Georgian runs ~0.53 chars/token against ~6.2 for English, a ~12x
# difference that no single chars-per-token constant can express.
#
# Two callers share it and they MUST share it: `scoring_import.estimate_output_tokens` sizes
# the budget a document needs to come back verbatim, and the adapters' progress meters measure
# what has actually come back. Those two numbers become the denominator and the numerator of a
# progress bar — measure them with different yardsticks and the percentage is fiction, even
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


# --------------------------------------------------------------------------- #
# The one result shape
# --------------------------------------------------------------------------- #
class Usage(TypedDict):
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None


class ToolResult(TypedDict):
    input: dict | None          # the tool arguments; None = the model did not call the tool
    usage: Usage
    stop_reason: str            # TOOL_USE | MAX_TOKENS | END_TURN | the provider's own word
    model: str                  # the model that actually answered (providers may alias)


# Normalised stop reasons. Anthropic's vocabulary, because the house code already speaks it.
TOOL_USE = "tool_use"
MAX_TOKENS = "max_tokens"
END_TURN = "end_turn"


def usage(*, input_tokens: int | None = None, output_tokens: int | None = None,
          cache_read_tokens: int | None = None,
          cache_creation_tokens: int | None = None) -> Usage:
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens,
                 cache_read_tokens=cache_read_tokens,
                 cache_creation_tokens=cache_creation_tokens)


def usage_of(message: Any) -> Usage:
    """Usage from an Anthropic-SDK-shaped response (`message.usage.input_tokens`...).

    Also what `llm._record(message=...)` reads for a caller still passing the raw response."""
    u = getattr(message, "usage", None)
    return usage(
        input_tokens=getattr(u, "input_tokens", None),
        output_tokens=getattr(u, "output_tokens", None),
        # Only present when prompt caching is in play; older/other responses omit them.
        cache_read_tokens=getattr(u, "cache_read_input_tokens", None),
        cache_creation_tokens=getattr(u, "cache_creation_input_tokens", None),
    )


@dataclass
class StreamUsage:
    """Filled in by `stream_text` once the stream has ended — the caller reads it after."""
    model: str | None = None
    usage: Usage | None = None
    stop_reason: str | None = None


# --------------------------------------------------------------------------- #
# Progress: cumulative output tokens -> somebody's UI, guarded
# --------------------------------------------------------------------------- #
class ProgressMeter:
    """Turns streamed fragments into a rising token count for `on_progress`.

    `on_progress` is somebody's UI, not part of the call: it is only ever handed a growing
    integer, and if it raises, it is dropped (`dead`) and the call carries on without a bar.
    Adapters check `dead` to stop hand-counting once nobody is listening.

    `exact()` folds in a provider's own cumulative figure when one arrives early, taking
    whichever source has seen more so the number can never run backwards.
    """

    def __init__(self, on_progress: Callable[[int], None] | None) -> None:
        self._cb = on_progress
        self.tokens = 0.0
        self._sent = 0
        self.dead = on_progress is None

    def add_text(self, chunk: str | None) -> None:
        if self.dead or not chunk:
            return
        self.tokens += estimate_tokens(chunk)
        self._emit()

    def exact(self, count: Any) -> None:
        if self.dead or not isinstance(count, int) or count <= self.tokens:
            return
        self.tokens = float(count)
        self._emit()

    def _emit(self) -> None:
        count = int(self.tokens)
        if count <= self._sent:
            return
        self._sent = count
        try:
            self._cb(count)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 — a broken bar must not fail the call
            log.warning("call_tool on_progress failed; progress dropped: %s", exc)
            self.dead = True


# --------------------------------------------------------------------------- #
# The probe every adapter answers: one tiny forced tool call
# --------------------------------------------------------------------------- #
PROBE_TOOL = {
    "name": "connection_check",
    "description": "Acknowledge that this connection works.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean", "description": "Always true."}},
        "required": ["ok"],
        "additionalProperties": False,
    },
}
PROBE_SYSTEM = "You are a connectivity check for an API integration."
PROBE_USER = 'Call the connection_check tool with {"ok": true}. Do not write anything else.'
# Generous for a one-field answer, and deliberately not tiny: reasoning models spend hidden
# tokens before the visible call, and a budget of a few dozen would report them as truncated.
PROBE_MAX_TOKENS = 512
PROBE_OPTS: dict = dict(timeout=httpx.Timeout(20.0, connect=3.0), max_retries=0)


# --------------------------------------------------------------------------- #
# The adapter protocol
# --------------------------------------------------------------------------- #
class LLMAdapter(ABC):
    provider: str = ""      # the catalog id: anthropic | openai | gemini
    label: str = ""         # for error text a human reads

    @abstractmethod
    async def call_tool(self, res: Resolved, *, system: str, user: str, tool: dict,
                        max_tokens: int, cache_system: bool, stream: bool,
                        on_progress: Callable[[int], None] | None,
                        opts: dict) -> ToolResult:
        """One forced tool call. `tool` is in the house (Anthropic) shape —
        {name, description, input_schema, strict?} — and the adapter translates it."""

    @abstractmethod
    def stream_text(self, res: Resolved, *, system: str, user: str, max_tokens: int,
                    opts: dict, usage: StreamUsage) -> AsyncIterator[str]:
        """Yield plain text deltas; fill `usage` when the stream is over."""

    async def probe(self, res: Resolved, *, opts: dict | None = None) -> dict:
        """Does this provider/model/key answer a forced tool call at all?

        Returns {"ok", "detail", "result"} — `detail` is plain English either way, and
        `result` is the ToolResult on a completed call (for accounting) or None.
        """
        started = time.monotonic()
        try:
            result = await self.call_tool(
                res, system=PROBE_SYSTEM, user=PROBE_USER, tool=PROBE_TOOL,
                max_tokens=PROBE_MAX_TOKENS, cache_system=False, stream=False,
                on_progress=None, opts=opts or PROBE_OPTS)
        except LLMError as exc:
            return {"ok": False, "detail": str(exc), "result": None}
        ms = int((time.monotonic() - started) * 1000)
        if result["stop_reason"] == MAX_TOKENS:
            detail = (f"{self.label} answered, but ran out of output budget before completing "
                      f"a one-field tool call — this model is probably not suitable.")
            return {"ok": False, "detail": detail, "result": result}
        if not isinstance(result["input"], dict):
            detail = (f"{self.label} answered with {result['model']}, but did not call the tool "
                      f"it was told to — this model may not support forced tool use.")
            return {"ok": False, "detail": detail, "result": result}
        if result["input"].get("ok") is not True:
            detail = (f"{self.label} called the tool but with the wrong payload "
                      f"({json.dumps(result['input'])[:80]}) — this model may not follow a "
                      f"strict schema.")
            return {"ok": False, "detail": detail, "result": result}
        return {"ok": True, "result": result,
                "detail": f"{self.label} answered with {result['model']} in {ms} ms."}


# --------------------------------------------------------------------------- #
# Schema translation: the house JSON Schema -> what each provider's strict mode accepts
# --------------------------------------------------------------------------- #
DIALECTS = ("anthropic", "openai", "gemini")

# OpenAI strict mode: a JSON-Schema subset. These keywords are rejected outright (or were,
# in some API versions — dropping a validation hint costs nothing, a 400 costs the call).
_OPENAI_DROP = frozenset({
    "$schema", "$id", "$comment", "default", "examples", "example",
    "minLength", "maxLength", "uniqueItems", "contains", "minContains", "maxContains",
    "minProperties", "maxProperties", "patternProperties", "propertyNames",
    "unevaluatedProperties", "unevaluatedItems", "dependentRequired", "dependentSchemas",
    "if", "then", "else", "not",
})

# Gemini's `Schema` is an OpenAPI 3.0 subset: an ALLOWLIST, because it rejects unknown fields
# with a 400 and the list of what it knows is short and documented.
_GEMINI_KEEP = frozenset({
    "type", "format", "description", "nullable", "enum", "items", "properties", "required",
    "anyOf", "minItems", "maxItems", "minimum", "maximum", "pattern", "minLength", "maxLength",
    "propertyOrdering",
})
# `format` is only meaningful for a handful of values; anything else (uri, email...) is a 400.
_GEMINI_FORMATS = frozenset({"float", "double", "int32", "int64", "enum", "date-time"})


def translate_schema(schema: dict, dialect: str) -> dict:
    """A deep copy of `schema` in the given provider's dialect. The input is never mutated.

    anthropic — as-is: the house schemas are written for it.
    openai    — strict mode: `additionalProperties: false` on every object, every property
                listed in `required`; a property that was optional becomes nullable instead
                (that is how strict mode spells "optional"). `oneOf` becomes `anyOf`.
    gemini    — the OpenAPI subset: types upper-cased, `["T","null"]` and `anyOf`-with-null
                become `nullable: true`, and every keyword Gemini does not know is dropped
                (`additionalProperties`, `$schema`, `title`, `default`, ...).
    """
    if dialect == "anthropic":
        return copy.deepcopy(schema)
    if dialect == "openai":
        return _to_openai(copy.deepcopy(schema))
    if dialect == "gemini":
        return _to_gemini(copy.deepcopy(schema))
    raise ValueError(f"unknown schema dialect: {dialect!r}")


def _is_object(node: dict) -> bool:
    t = node.get("type")
    return t == "object" or (isinstance(t, list) and "object" in t) or "properties" in node


def _to_openai(node: Any) -> Any:
    if isinstance(node, list):
        return [_to_openai(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {k: v for k, v in node.items() if k not in _OPENAI_DROP}
    if "oneOf" in out:
        out["anyOf"] = out.pop("oneOf")
    for key in ("anyOf", "allOf"):
        if key in out:
            out[key] = [_to_openai(x) for x in out[key]]
    if "items" in out:
        out["items"] = _to_openai(out["items"])
    for key in ("$defs", "definitions"):
        if isinstance(out.get(key), dict):
            out[key] = {k: _to_openai(v) for k, v in out[key].items()}
    if _is_object(out):
        props = out.get("properties") or {}
        required = set(out.get("required") or [])
        translated = {}
        for name, sub in props.items():
            sub = _to_openai(sub)
            if name not in required:
                sub = _openai_nullable(sub)
            translated[name] = sub
        out["properties"] = translated
        out["required"] = list(props.keys())
        out["additionalProperties"] = False
    return out


def _openai_nullable(sub: Any) -> Any:
    """Strict mode has no optional properties; "may be absent" becomes "may be null"."""
    if not isinstance(sub, dict):
        return sub
    t = sub.get("type")
    if isinstance(t, str):
        if t != "null":
            sub["type"] = [t, "null"]
    elif isinstance(t, list):
        if "null" not in t:
            sub["type"] = [*t, "null"]
    elif "anyOf" in sub:
        members = sub["anyOf"]
        if not any(isinstance(m, dict) and m.get("type") == "null" for m in members):
            sub["anyOf"] = [*members, {"type": "null"}]
    if "enum" in sub and None not in sub["enum"]:
        sub["enum"] = [*sub["enum"], None]
    return sub


def _to_gemini(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    out = {k: v for k, v in node.items() if k in _GEMINI_KEEP}
    nullable = bool(out.pop("nullable", False))

    t = out.get("type")
    if isinstance(t, list):
        kinds = [x for x in t if x != "null"]
        nullable = nullable or len(kinds) < len(t)
        if len(kinds) == 1:
            t = kinds[0]
        elif kinds:
            # Several concrete types on one node has no OpenAPI spelling except a union.
            out.pop("type")
            out["anyOf"] = [*out.get("anyOf", []), *({"type": k} for k in kinds)]
            t = None
        else:
            out.pop("type")
            t = None
    if isinstance(t, str):
        out["type"] = t.upper()

    if "anyOf" in out:
        members = [m for m in out["anyOf"]
                   if not (isinstance(m, dict) and m.get("type") == "null")]
        nullable = nullable or len(members) < len(out["anyOf"])
        members = [_to_gemini(m) for m in members]
        if len(members) == 1 and "type" not in out:
            out.pop("anyOf")
            out.update(members[0])
        else:
            out["anyOf"] = members

    if "format" in out and out["format"] not in _GEMINI_FORMATS:
        out.pop("format")
    if "enum" in out:
        values = list(out["enum"])
        if None in values:
            nullable = True
            values = [v for v in values if v is not None]
        out["enum"] = values
    if "items" in out:
        items = out["items"]
        out["items"] = _to_gemini(items[0] if isinstance(items, list) and items else items)
    if "properties" in out:
        out["properties"] = {k: _to_gemini(v) for k, v in out["properties"].items()}
        if "required" in out:
            out["required"] = [r for r in out["required"] if r in out["properties"]]
    elif "required" in out:
        out.pop("required")
    if nullable:
        out["nullable"] = True
    return out


# --------------------------------------------------------------------------- #
# Shared httpx plumbing for the providers we talk to without an SDK
# --------------------------------------------------------------------------- #
# Statuses worth one more try. 408/409 are the SDKs' convention; 429 and 5xx are "later".
_RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})


def http_timeout(opts: dict | None) -> httpx.Timeout:
    """The call sites pass `anthropic.Timeout` profiles (see llm.py for why NOT httpx's own);
    the HTTP adapters need the same budget as an httpx.Timeout. Both expose the same four
    attributes, so this reads them off whichever it was handed."""
    t = (opts or {}).get("timeout")
    if t is None:
        return httpx.Timeout(60.0, connect=2.0)
    if isinstance(t, (int, float)):
        return httpx.Timeout(float(t), connect=2.0)
    return httpx.Timeout(connect=getattr(t, "connect", 2.0), read=getattr(t, "read", 60.0),
                         write=getattr(t, "write", 60.0), pool=getattr(t, "pool", 60.0))


def _backoff(attempt: int) -> float:
    return min(0.5 * (2 ** attempt), 4.0)


def provider_message(body: str) -> str:
    """The human-readable part of an error body, whatever envelope the provider used."""
    text = (body or "").strip()
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return text[:300]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("status") or err.get("code")
            if msg:
                return str(msg)[:300]
        if isinstance(err, str) and err:
            return err[:300]
        if data.get("message"):
            return str(data["message"])[:300]
    return text[:300]


def http_error(label: str, status: int, body: str) -> LLMError:
    """An HTTP failure as plain English a superadmin can act on from the Test button."""
    msg = provider_message(body)
    tail = f": {msg}" if msg else "."
    if status in (401, 403):
        return LLMError(f"{label} rejected the API key (HTTP {status}){tail}")
    if status == 404:
        return LLMError(f"{label} does not know this model or endpoint (HTTP 404){tail}")
    if status == 429:
        return LLMBusyError(f"{label} is rate-limiting this key (HTTP 429){tail}")
    if status >= 500:
        return LLMBusyError(f"{label} is having trouble right now (HTTP {status}){tail}")
    return LLMError(f"{label} rejected the request (HTTP {status}){tail}")


async def sse_events(resp: httpx.Response) -> AsyncIterator[dict]:
    """The JSON payloads of a text/event-stream response, in order. Stops at `[DONE]`."""
    data: list[str] = []

    def flush() -> dict | None:
        if not data:
            return None
        payload = "\n".join(data).strip()
        data.clear()
        if not payload or payload == "[DONE]":
            return None
        try:
            return json.loads(payload)
        except ValueError:
            log.warning("sse: dropping an event that is not JSON: %.80s", payload)
            return None

    async for line in resp.aiter_lines():
        if line == "":
            if data and "\n".join(data).strip() == "[DONE]":
                return
            event = flush()
            if event is not None:
                yield event
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        # `event:` / `id:` / `:` comment lines carry nothing either provider needs.
    if data and "\n".join(data).strip() == "[DONE]":
        return
    event = flush()
    if event is not None:
        yield event


class HttpJsonAdapter(LLMAdapter):
    """Base for the adapters that speak plain HTTPS + JSON (OpenAI, Gemini).

    Clients are memoized per timeout profile for the process lifetime, exactly like the
    Anthropic SDK clients in llm.py — a connection pool per profile, never closed. `transport`
    exists so a test can hand in `httpx.MockTransport` and assert on the request shape.
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._clients: dict[str, httpx.AsyncClient] = {}

    def _client(self, opts: dict | None) -> httpx.AsyncClient:
        timeout = http_timeout(opts)
        key = repr(timeout)
        inst = self._clients.get(key)
        if inst is None:
            inst = httpx.AsyncClient(timeout=timeout, transport=self._transport)
            self._clients[key] = inst
        return inst

    @staticmethod
    def _retries(opts: dict | None) -> int:
        try:
            return max(0, int((opts or {}).get("max_retries") or 0))
        except (TypeError, ValueError):
            return 0

    def _unreachable(self, exc: Exception) -> LLMError:
        return LLMError(f"{self.label} could not be reached ({type(exc).__name__}: {exc}).")

    async def _post(self, url: str, *, headers: dict, body: dict, opts: dict | None) -> dict:
        client = self._client(opts)
        retries = self._retries(opts)
        for attempt in range(retries + 1):
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.TransportError as exc:
                if attempt >= retries:
                    raise self._unreachable(exc) from exc
                await asyncio.sleep(_backoff(attempt))
                continue
            if resp.status_code in _RETRY_STATUSES and attempt < retries:
                await asyncio.sleep(_backoff(attempt))
                continue
            if resp.status_code >= 400:
                raise http_error(self.label, resp.status_code, resp.text)
            try:
                return resp.json()
            except ValueError as exc:
                raise LLMError(f"{self.label} returned a response that is not JSON.") from exc
        raise LLMError(f"{self.label} did not answer.")  # unreachable; keeps the type checker honest

    async def _post_sse(self, url: str, *, headers: dict, body: dict,
                        opts: dict | None) -> AsyncIterator[dict]:
        """POST and yield the SSE events. Retries only BEFORE the first event has been
        yielded — replaying a half-consumed stream would duplicate what the caller saw."""
        client = self._client(opts)
        retries = self._retries(opts)
        for attempt in range(retries + 1):
            started = False
            try:
                async with client.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code in _RETRY_STATUSES and attempt < retries:
                        await resp.aread()
                    elif resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", "replace")
                        raise http_error(self.label, resp.status_code, text)
                    else:
                        async for event in sse_events(resp):
                            started = True
                            yield event
                        return
            except httpx.TransportError as exc:
                if started or attempt >= retries:
                    raise self._unreachable(exc) from exc
            await asyncio.sleep(_backoff(attempt))
