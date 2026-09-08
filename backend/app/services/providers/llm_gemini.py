"""Google Gemini — `generateContent` over plain httpx, no SDK.

Forced tool use is `toolConfig.functionCallingConfig = {mode: "ANY", allowedFunctionNames:
[name]}`: the model MUST answer with a call to that one function. The function's `parameters`
is Gemini's OpenAPI-3.0 subset, not JSON Schema — `translate_schema('gemini')` upper-cases the
types, turns `["T","null"]` into `nullable: true`, and drops every keyword Gemini rejects with a
400 (`additionalProperties` first among them).

The key travels in the `x-goog-api-key` header, never in the URL: a query-string key ends up
in access logs and proxy logs, and the whole point of the registry is that keys are handled
with care. There is no `base_url` (the catalog says so) — Gemini has no compatible-gateway
ecosystem, and a tenant-settable endpoint is exactly the thing the design forbids.

`cache_system` is accepted and ignored: Gemini's context caching is a separate resource API,
not a request flag. Implicit caching still reports `cachedContentTokenCount`, which becomes
`cache_read_tokens` and is subtracted from `input_tokens` (Anthropic's convention — llm_base).
`thoughtsTokenCount` (thinking models) is billed as output, so it is added to `output_tokens`.
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

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _usage(meta: dict | None) -> Usage:
    meta = meta or {}
    prompt = meta.get("promptTokenCount")
    cached = meta.get("cachedContentTokenCount")
    if isinstance(prompt, int) and isinstance(cached, int):
        prompt = max(prompt - cached, 0)
    out = meta.get("candidatesTokenCount")
    thoughts = meta.get("thoughtsTokenCount")
    if isinstance(out, int) and isinstance(thoughts, int):
        out += thoughts
    return usage(input_tokens=prompt, output_tokens=out, cache_read_tokens=cached,
                 cache_creation_tokens=None)


def _stop(finish_reason: str | None, found: dict | None) -> str:
    if finish_reason == "MAX_TOKENS":
        return MAX_TOKENS
    if found is not None:
        return TOOL_USE
    return (finish_reason or END_TURN).lower()


class GeminiAdapter(HttpJsonAdapter):
    provider = "gemini"
    label = "Google Gemini"

    @staticmethod
    def _model(res: Resolved) -> str:
        return (res.model or "").removeprefix("models/")

    def _url(self, res: Resolved, method: str) -> str:
        url = f"{BASE_URL}/models/{self._model(res)}:{method}"
        return f"{url}?alt=sse" if method == "streamGenerateContent" else url

    def _headers(self, res: Resolved) -> dict:
        if not res.api_key:
            raise LLMError(f"No API key is configured for {self.label}.")
        return {"x-goog-api-key": res.api_key, "Content-Type": "application/json"}

    @staticmethod
    def _body(system: str, user: str, max_tokens: int) -> dict:
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body

    def tool_request(self, *, system: str, user: str, tool: dict, max_tokens: int) -> dict:
        body = self._body(system, user, max_tokens)
        body["tools"] = [{"functionDeclarations": [{
            "name": tool["name"],
            "description": tool.get("description") or "",
            "parameters": translate_schema(tool["input_schema"], "gemini"),
        }]}]
        body["toolConfig"] = {"functionCallingConfig": {
            "mode": "ANY", "allowedFunctionNames": [tool["name"]]}}
        return body

    def _candidate(self, data: dict) -> dict:
        candidates = data.get("candidates") or []
        if candidates:
            return candidates[0]
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise LLMError(f"{self.label} blocked the request ({blocked}).")
        return {}

    @staticmethod
    def _function_args(candidate: dict, name: str) -> dict | None:
        for part in (candidate.get("content") or {}).get("parts") or []:
            call = part.get("functionCall")
            if isinstance(call, dict) and call.get("name") == name:
                args = call.get("args")
                return args if isinstance(args, dict) else {}
        return None

    async def call_tool(self, res: Resolved, *, system: str, user: str, tool: dict,
                        max_tokens: int, cache_system: bool, stream: bool,
                        on_progress: Callable[[int], None] | None,
                        opts: dict) -> ToolResult:
        headers = self._headers(res)
        body = self.tool_request(system=system, user=user, tool=tool, max_tokens=max_tokens)
        if not stream:
            data = await self._post(self._url(res, "generateContent"), headers=headers,
                                    body=body, opts=opts)
            candidate = self._candidate(data)
            found = self._function_args(candidate, tool["name"])
            return ToolResult(input=found, usage=_usage(data.get("usageMetadata")),
                              stop_reason=_stop(candidate.get("finishReason"), found),
                              model=data.get("modelVersion") or self._model(res))

        # Same call over SSE. A function call tends to arrive whole in one chunk (Gemini does
        # not fragment the args JSON the way Anthropic and OpenAI do), so the meter mostly
        # jumps once; text parts, if the model writes any, are counted as they come.
        meter = ProgressMeter(on_progress)
        found: dict | None = None
        finish: str | None = None
        meta: dict | None = None
        model: str | None = None
        async for event in self._post_sse(self._url(res, "streamGenerateContent"),
                                          headers=headers, body=body, opts=opts):
            model = event.get("modelVersion") or model
            if event.get("usageMetadata"):
                meta = event["usageMetadata"]
            candidate = self._candidate(event)
            finish = candidate.get("finishReason") or finish
            for part in (candidate.get("content") or {}).get("parts") or []:
                call = part.get("functionCall")
                if isinstance(call, dict) and call.get("name") == tool["name"] and found is None:
                    args = call.get("args")
                    found = args if isinstance(args, dict) else {}
                    meter.add_text(json.dumps(found, ensure_ascii=False))
                elif part.get("text"):
                    meter.add_text(part["text"])
        return ToolResult(input=found, usage=_usage(meta), stop_reason=_stop(finish, found),
                          model=model or self._model(res))

    async def stream_text(self, res: Resolved, *, system: str, user: str, max_tokens: int,
                          opts: dict, usage: StreamUsage) -> AsyncIterator[str]:
        headers = self._headers(res)
        body = self._body(system, user, max_tokens)
        finish: str | None = None
        meta: dict | None = None
        model: str | None = None
        async for event in self._post_sse(self._url(res, "streamGenerateContent"),
                                          headers=headers, body=body, opts=opts):
            model = event.get("modelVersion") or model
            if event.get("usageMetadata"):
                meta = event["usageMetadata"]
            candidate = self._candidate(event)
            finish = candidate.get("finishReason") or finish
            for part in (candidate.get("content") or {}).get("parts") or []:
                if part.get("text"):
                    yield part["text"]
        usage.model = model or self._model(res)
        usage.usage = _usage(meta)
        usage.stop_reason = _stop(finish, None)
