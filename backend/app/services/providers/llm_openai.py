"""OpenAI (GPT) — Chat Completions over plain httpx, no SDK.

The house pattern is forced tool use with a strict schema, and Chat Completions has an exact
equivalent: ONE function in `tools`, `tool_choice` pinned to it, `strict: true`. Strict mode
is what makes the answer shape reliable, and it has two requirements the house schemas do not
state because Anthropic does not need them — `additionalProperties: false` on every object and
every property listed in `required` — which `translate_schema('openai')` adds.

`base_url` is honoured (the catalog allows one for OpenAI): a corporate gateway, a region pin,
or any OpenAI-compatible endpoint. Everything is relative to it, so `https://gw.example/v1`
becomes `https://gw.example/v1/chat/completions`.

Two things to know when picking a model for a connection:
- `max_completion_tokens` is sent (the current name; `max_tokens` is refused by the reasoning
  models). On a reasoning model that budget ALSO covers the hidden reasoning tokens, so a
  tight house budget can come back `finish_reason: "length"` — which is reported honestly
  as LLMTruncatedError rather than as a smaller answer.
- Prompt caching is automatic on OpenAI's side (no flag), so `cache_system` is accepted and
  ignored; cached input shows up in `usage.prompt_tokens_details.cached_tokens`, which is
  reported as `cache_read_tokens` and subtracted from `input_tokens` (see llm_base).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

from ..ai_resolve import Resolved
from .llm_base import (
    END_TURN,
    MAX_TOKENS,
    TOOL_USE,
    HttpJsonAdapter,
    LLMError,
    ProgressMeter,
    StreamUsage,
    ToolResult,
    Usage,
    translate_schema,
    usage,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _usage(block: dict | None) -> Usage:
    block = block or {}
    prompt = block.get("prompt_tokens")
    details = block.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if isinstance(prompt, int) and isinstance(cached, int):
        prompt = max(prompt - cached, 0)
    return usage(input_tokens=prompt, output_tokens=block.get("completion_tokens"),
                 cache_read_tokens=cached, cache_creation_tokens=None)


def _stop(finish_reason: str | None, found: dict | None) -> str:
    if finish_reason == "length":
        return MAX_TOKENS
    if found is not None:
        return TOOL_USE
    return finish_reason or END_TURN


def _parse_arguments(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


class OpenAIAdapter(HttpJsonAdapter):
    provider = "openai"
    label = "OpenAI"

    @staticmethod
    def _url(res: Resolved) -> str:
        return f"{(res.base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"

    def _headers(self, res: Resolved) -> dict:
        if not res.api_key:
            raise LLMError(f"No API key is configured for {self.label}.")
        return {"Authorization": f"Bearer {res.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _messages(system: str, user: str) -> list[dict]:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": user})
        return messages

    def tool_request(self, res: Resolved, *, system: str, user: str, tool: dict,
                     max_tokens: int, stream: bool) -> dict:
        body = {
            "model": res.model,
            "messages": self._messages(system, user),
            "tools": [{
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "parameters": translate_schema(tool["input_schema"], "openai"),
                    "strict": True,
                },
            }],
            "tool_choice": {"type": "function", "function": {"name": tool["name"]}},
            "max_completion_tokens": max_tokens,
        }
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    async def call_tool(self, res: Resolved, *, system: str, user: str, tool: dict,
                        max_tokens: int, cache_system: bool, stream: bool,
                        on_progress: Callable[[int], None] | None,
                        opts: dict) -> ToolResult:
        headers = self._headers(res)
        body = self.tool_request(res, system=system, user=user, tool=tool,
                                 max_tokens=max_tokens, stream=stream)
        if not stream:
            data = await self._post(self._url(res), headers=headers, body=body, opts=opts)
            return self._parse(data, tool, res)

        # Same call over SSE: the tool arguments arrive as JSON fragments in
        # choices[0].delta.tool_calls[*].function.arguments; usage rides the final chunk
        # (stream_options.include_usage), which has an empty `choices`.
        meter = ProgressMeter(on_progress)
        fragments: list[str] = []
        finish: str | None = None
        model: str | None = None
        usage_block: dict | None = None
        async for event in self._post_sse(self._url(res), headers=headers, body=body, opts=opts):
            model = event.get("model") or model
            if event.get("usage"):
                usage_block = event["usage"]
            for choice in event.get("choices") or []:
                finish = choice.get("finish_reason") or finish
                delta = choice.get("delta") or {}
                for call in delta.get("tool_calls") or []:
                    piece = (call.get("function") or {}).get("arguments")
                    if piece:
                        fragments.append(piece)
                        meter.add_text(piece)
        found = _parse_arguments("".join(fragments))
        return ToolResult(input=found, usage=_usage(usage_block), stop_reason=_stop(finish, found),
                          model=model or res.model or "")

    def _parse(self, data: dict, tool: dict, res: Resolved) -> ToolResult:
        choices = data.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        found: dict | None = None
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            if fn.get("name") == tool["name"]:
                found = _parse_arguments(fn.get("arguments"))
                break
        finish = choice.get("finish_reason")
        return ToolResult(input=found, usage=_usage(data.get("usage")),
                          stop_reason=_stop(finish, found),
                          model=data.get("model") or res.model or "")

    async def stream_text(self, res: Resolved, *, system: str, user: str, max_tokens: int,
                          opts: dict, usage: StreamUsage) -> AsyncIterator[str]:
        headers = self._headers(res)
        body = {
            "model": res.model,
            "messages": self._messages(system, user),
            "max_completion_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        finish: str | None = None
        usage_block: dict | None = None
        model: str | None = None
        async for event in self._post_sse(self._url(res), headers=headers, body=body, opts=opts):
            model = event.get("model") or model
            if event.get("usage"):
                usage_block = event["usage"]
            for choice in event.get("choices") or []:
                finish = choice.get("finish_reason") or finish
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text
        usage.model = model or res.model
        usage.usage = _usage(usage_block)
        usage.stop_reason = MAX_TOKENS if finish == "length" else (finish or END_TURN)
