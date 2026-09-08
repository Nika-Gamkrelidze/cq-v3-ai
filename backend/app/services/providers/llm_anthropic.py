"""Anthropic (Claude) — the house provider, moved here from services/llm.py unchanged in
behaviour: the same request kwargs, prompt caching via the block-form system prompt, and the
streaming transport that long outputs need.

The SDK client comes from a factory the front door hands in (`llm.client`), because llm.py owns
the memoized, process-lifetime client pool and because a test that fakes the SDK patches that
one name. This module never builds an `AsyncAnthropic` itself.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import anthropic

from ..ai_resolve import Resolved
from .llm_base import (
    END_TURN,
    MAX_TOKENS,
    TOOL_USE,
    LLMAdapter,
    LLMError,
    ProgressMeter,
    StreamUsage,
    ToolResult,
    usage_of,
)

ClientFactory = Callable[..., anthropic.AsyncAnthropic]


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


def _sdk_timeout(value):
    """The SDK must be handed ITS OWN Timeout type.

    Since the SDK vendored its HTTP stack (httpx2), a Timeout built from the app-level httpx
    is a foreign object inside it, and every request dies in the connect phase as
    APIConnectionError('Connection error.') — which silently took every Claude feature down
    at once on the first image rebuild after the SDK upgrade. The call sites pass
    `anthropic.Timeout` profiles already; this converts anything else (llm_base's provider-
    neutral PROBE_OPTS, a bare number) rather than trusting the caller.
    """
    if value is None or isinstance(value, anthropic.Timeout):
        return value
    if isinstance(value, (int, float)):
        return anthropic.Timeout(float(value), connect=2.0)
    return anthropic.Timeout(connect=getattr(value, "connect", 2.0),
                             read=getattr(value, "read", 60.0),
                             write=getattr(value, "write", 60.0),
                             pool=getattr(value, "pool", 60.0))


async def _stream_progress(st, meter: ProgressMeter) -> None:
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

    Returns as soon as the meter reports its callback dead — `get_final_message()` drains
    whatever is left, so a broken bar costs the bar and not the call.
    """
    async for event in st:
        etype = getattr(event, "type", None)
        if etype == "content_block_delta":
            delta = getattr(event, "delta", None)
            meter.add_text(getattr(delta, "partial_json", None) or getattr(delta, "text", None))
        elif etype == "message_delta":
            meter.exact(getattr(getattr(event, "usage", None), "output_tokens", None))
        if meter.dead:
            return


def _wrap(exc: anthropic.APIError) -> LLMError:
    # Same message shape the call sites always produced, so their error text is unchanged.
    return LLMError(getattr(exc, "message", None) or str(exc))


class AnthropicAdapter(LLMAdapter):
    provider = "anthropic"
    label = "Anthropic"

    def __init__(self, client_factory: ClientFactory) -> None:
        self._client_factory = client_factory

    def _client(self, res: Resolved, opts: dict) -> anthropic.AsyncAnthropic:
        opts = dict(opts or {})
        if "timeout" in opts:
            opts["timeout"] = _sdk_timeout(opts["timeout"])
        return self._client_factory(res.api_key, base_url=res.base_url, **opts)

    async def call_tool(self, res: Resolved, *, system: str, user: str, tool: dict,
                        max_tokens: int, cache_system: bool, stream: bool,
                        on_progress: Callable[[int], None] | None,
                        opts: dict) -> ToolResult:
        """`stream=True` transports the SAME call over SSE and collects the final message —
        the result is identical. It exists because Anthropic drops long NON-streaming
        requests ("Request timed out or interrupted... long-requests"): a big model writing
        thousands of tokens of dense Georgian guidance takes minutes, which only a stream
        survives. With no `on_progress` the deltas are consumed by the SDK and discarded —
        the code path every caller has always run."""
        cl = self._client(res, opts)
        kwargs = dict(
            model=res.model,
            max_tokens=max_tokens,
            system=_system_param(system, cache_system),
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": user}],
        )
        try:
            if stream:
                async with cl.messages.stream(**kwargs) as st:
                    if on_progress is not None:
                        await _stream_progress(st, ProgressMeter(on_progress))
                    message = await st.get_final_message()
            else:
                message = await cl.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise _wrap(exc) from exc

        stop = getattr(message, "stop_reason", None)
        found: dict | None = None
        for block in getattr(message, "content", None) or []:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool["name"]:
                found = dict(block.input)
                break
        if stop == "max_tokens":
            stop = MAX_TOKENS
        elif found is not None:
            stop = TOOL_USE
        else:
            stop = stop or END_TURN
        return ToolResult(input=found, usage=usage_of(message), stop_reason=stop,
                          model=getattr(message, "model", None) or res.model or "")

    async def stream_text(self, res: Resolved, *, system: str, user: str, max_tokens: int,
                          opts: dict, usage: StreamUsage) -> AsyncIterator[str]:
        cl = self._client(res, opts)
        try:
            async with cl.messages.stream(
                model=res.model,
                max_tokens=max_tokens,
                system=_system_param(system, False),
                messages=[{"role": "user", "content": user}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
                message = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise _wrap(exc) from exc
        usage.model = getattr(message, "model", None) or res.model
        usage.usage = usage_of(message)
        usage.stop_reason = getattr(message, "stop_reason", None)
