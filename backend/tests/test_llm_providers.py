"""The text-model adapters: what each provider is SENT, and one result shape back.

There are no provider keys in this tree and never will be in CI, so nothing here talks to a
network. The two HTTP adapters (OpenAI, Gemini) are driven through `httpx.MockTransport`, which
lets a test hold the exact request the provider would have received and hand back a canned
answer; the Anthropic adapter is driven with a fake SDK client, the way
`test_scoring_import_progress.py` already does it. Every test goes through the REAL
`llm.call_tool` / `llm.stream_text`, because the truncation check, the usage row and the
exception vocabulary live there — patching those would only test the patch.

What is pinned, and why:

  * **The tool is forced and the schema is what strict mode accepts.** OpenAI strict mode 400s a
    schema with a single object missing `additionalProperties: false` or an unlisted property;
    Gemini 400s a schema with `additionalProperties` present at all. A translation that misses
    one nested level would pass every other test and fail on the first real call.
  * **A registry-less deployment sends Anthropic the request it always sent.** The refactor is
    invisible to the fifteen call sites only if the bytes on the wire are unchanged.
  * **Truncation and "busy" mean the same thing on every provider.** `finish_reason: length`,
    `finishReason: MAX_TOKENS` and `stop_reason: max_tokens` all become LLMTruncatedError — after
    the usage row is written, because a cut-off answer was still paid for. 429 / 5xx become
    LLMBusyError, which routers already turn into a 429 for the client.
  * **A key never appears in a URL.** Gemini's documented `?key=` form is exactly the thing that
    ends up in access logs; the header form is used instead.
"""
import asyncio
import copy
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.services import llm
from app.services.ai_resolve import Resolved
from app.services.providers import llm_base, llm_gemini, llm_openai
from app.services.providers.llm_base import translate_schema

# conftest's autouse `no_llm` fixture swaps `llm.call_tool` / `llm.stream_text` for detonators
# during every test. The genuine coroutines are captured at IMPORT time (collection happens
# before any fixture runs, and the patch is undone after each test).
_REAL_CALL_TOOL = llm.call_tool
_REAL_STREAM_TEXT = llm.stream_text


# --------------------------------------------------------------------------- #
# One real-shaped house tool: nested objects, an optional property, an enum, array bounds
# --------------------------------------------------------------------------- #
TOOL = {
    "name": "submit",
    "description": "Return the structured analysis.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "language": {"type": "string", "description": "Primary language."},
            "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
            "topics": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "speakers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "speaker": {"type": "string"},
                        "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    },
                    "required": ["speaker"],
                    "additionalProperties": False,
                },
            },
            "note": {"type": "string", "description": "Optional remark."},
        },
        "required": ["language", "sentiment", "topics", "speakers"],
        "additionalProperties": False,
    },
}

ANSWER = {"language": "ka", "sentiment": "positive", "topics": ["refund"],
          "speakers": [{"speaker": "agent", "score": 90}], "note": None}


def _resolved(provider: str, *, model: str = "m", api_key: str = "k",
              base_url: str | None = None, **kw) -> Resolved:
    return Resolved("llm", provider, model, api_key, base_url, **kw)


def _pin_resolver(monkeypatch, res: Resolved) -> list[tuple]:
    """Make the resolver answer `res`, and record what llm.py asked it."""
    seen: list[tuple] = []

    async def fake(client_id, capability, *, api_key=None, model=None):
        seen.append((client_id, capability, api_key, model))
        return res

    monkeypatch.setattr(llm.ai_resolve, "resolve", fake)
    return seen


def _records(monkeypatch) -> list[dict]:
    """Neutralise usage accounting (which wants a pool) and keep what it was handed."""
    recorded: list[dict] = []
    monkeypatch.setattr(llm, "_record", lambda **kw: recorded.append(kw))
    return recorded


def _mock(monkeypatch, provider: str, handler) -> list[httpx.Request]:
    """Install an HTTP adapter whose transport is `handler`; return the requests it saw."""
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    cls = llm_openai.OpenAIAdapter if provider == "openai" else llm_gemini.GeminiAdapter
    monkeypatch.setitem(llm._ADAPTERS, provider, cls(transport=httpx.MockTransport(transport)))
    return requests


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def _sse(events: list[dict], *, done: bool) -> bytes:
    text = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    if done:
        text += "data: [DONE]\n\n"
    return text.encode()


def _nodes(schema):
    """Every dict node of a schema, depth first — so "at every level" is really every level."""
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from _nodes(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from _nodes(item)


async def _call(**overrides) -> dict:
    kw = dict(feature="analysis", client_id="c1", api_key="legacy-key", model="legacy-model",
              system="sys", user="usr", tool=TOOL, opts=llm.ANALYSIS)
    kw.update(overrides)
    return await _REAL_CALL_TOOL(**kw)


async def _stream(**overrides) -> list[str]:
    kw = dict(feature="autopilot", client_id="c1", api_key="legacy-key", model="legacy-model",
              system="sys", user="usr", opts=llm.ANSWER)
    kw.update(overrides)
    return [chunk async for chunk in _REAL_STREAM_TEXT(**kw)]


# =========================================================================== #
# 1. translate_schema
# =========================================================================== #
def test_openai_dialect_makes_every_object_strict():
    out = translate_schema(TOOL["input_schema"], "openai")
    objects = [n for n in _nodes(out) if n.get("type") == "object" or "properties" in n]
    assert len(objects) == 2, "the root and the speaker item — both must be strict"
    for node in objects:
        assert node["additionalProperties"] is False
        assert node["required"] == list(node["properties"].keys()), (
            "strict mode: every property listed in required, in declaration order")


def test_openai_dialect_spells_optional_as_nullable():
    out = translate_schema(TOOL["input_schema"], "openai")
    assert out["properties"]["note"]["type"] == ["string", "null"], "optional -> nullable"
    assert out["properties"]["language"]["type"] == "string", "required stays as it was"
    score = out["properties"]["speakers"]["items"]["properties"]["score"]
    assert score["type"] == ["integer", "null"], "optional at a nested level too"
    assert score["minimum"] == 0 and score["maximum"] == 100, "numeric bounds are kept"
    assert out["properties"]["sentiment"]["enum"] == ["positive", "neutral", "negative"]
    assert out["properties"]["topics"]["minItems"] == 1


def test_openai_dialect_nullable_enum_admits_null():
    schema = {"type": "object", "properties": {"tone": {"type": "string", "enum": ["a", "b"]}},
              "required": []}
    out = translate_schema(schema, "openai")
    assert out["properties"]["tone"] == {"type": ["string", "null"], "enum": ["a", "b", None]}


def test_gemini_dialect_strips_what_gemini_rejects():
    src = copy.deepcopy(TOOL["input_schema"])
    src["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    src["title"] = "Analysis"
    src["properties"]["language"]["default"] = "ka"
    src["properties"]["language"]["format"] = "uri"
    out = translate_schema(src, "gemini")
    for node in _nodes(out):
        for banned in ("additionalProperties", "$schema", "title", "default"):
            assert banned not in node, f"{banned!r} survived translation: {node}"
    assert out["type"] == "OBJECT" and out["properties"]["topics"]["type"] == "ARRAY"
    assert out["properties"]["speakers"]["items"]["properties"]["score"]["type"] == "INTEGER"
    assert "format" not in out["properties"]["language"], "an unknown format is a 400"
    assert out["properties"]["sentiment"]["enum"] == ["positive", "neutral", "negative"]
    assert out["properties"]["topics"]["minItems"] == 1
    assert out["required"] == ["language", "sentiment", "topics", "speakers"]


def test_gemini_dialect_spells_nullable_the_openapi_way():
    schema = {"type": "object", "properties": {
        "a": {"type": ["string", "null"]},
        "b": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "c": {"type": "string", "enum": ["x", None]},
        "d": {"type": "integer", "format": "int64"},
    }, "required": ["a"]}
    out = translate_schema(schema, "gemini")["properties"]
    assert out["a"] == {"type": "STRING", "nullable": True}
    assert out["b"] == {"type": "INTEGER", "nullable": True}
    assert out["c"] == {"type": "STRING", "enum": ["x"], "nullable": True}
    assert out["d"] == {"type": "INTEGER", "format": "int64"}, "a known format is kept"


def test_anthropic_dialect_is_an_untouched_copy():
    before = copy.deepcopy(TOOL["input_schema"])
    out = translate_schema(TOOL["input_schema"], "anthropic")
    assert out == before and out is not TOOL["input_schema"]
    translate_schema(TOOL["input_schema"], "openai")
    translate_schema(TOOL["input_schema"], "gemini")
    assert TOOL["input_schema"] == before, "translation must never mutate the house schema"


def test_unknown_dialect_is_refused():
    with pytest.raises(ValueError):
        translate_schema(TOOL["input_schema"], "cohere")


# =========================================================================== #
# 2. OpenAI
# =========================================================================== #
def _openai_answer(args: dict | None = ANSWER, *, finish: str = "tool_calls",
                   usage: dict | None = None, name: str = "submit") -> dict:
    message = {"role": "assistant", "content": None}
    if args is not None:
        message["tool_calls"] = [{"id": "call_1", "type": "function", "function": {
            "name": name, "arguments": json.dumps(args)}}]
    return {"id": "chatcmpl-1", "model": "gpt-5-2026", "choices": [
        {"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20,
                           "prompt_tokens_details": {"cached_tokens": 40}}}


async def test_openai_request_is_a_forced_strict_function_call(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai", model="gpt-5", api_key="sk-test"))
    _records(monkeypatch)
    requests = _mock(monkeypatch, "openai", lambda r: httpx.Response(200, json=_openai_answer()))

    out = await _call(max_tokens=777)

    assert out == ANSWER
    [req] = requests
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test"
    body = _body(req)
    assert body["model"] == "gpt-5"
    assert body["messages"] == [{"role": "system", "content": "sys"},
                                {"role": "user", "content": "usr"}]
    assert body["max_completion_tokens"] == 777
    assert "stream" not in body
    [tool] = body["tools"]
    assert tool["type"] == "function" and tool["function"]["name"] == "submit"
    assert tool["function"]["strict"] is True
    assert body["tool_choice"] == {"type": "function", "function": {"name": "submit"}}
    params = tool["function"]["parameters"]
    for node in _nodes(params):
        if node.get("type") == "object" or "properties" in node:
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"].keys())
    assert params["properties"]["note"]["type"] == ["string", "null"]


async def test_openai_base_url_is_honoured(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai", base_url="https://gw.example/openai/v1/"))
    _records(monkeypatch)
    requests = _mock(monkeypatch, "openai", lambda r: httpx.Response(200, json=_openai_answer()))
    await _call()
    assert str(requests[0].url) == "https://gw.example/openai/v1/chat/completions"


async def test_openai_success_records_provider_connection_and_uncached_input(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai", byo=True, connection_id="conn-1",
                                         source="byo"))
    recorded = _records(monkeypatch)
    _mock(monkeypatch, "openai", lambda r: httpx.Response(200, json=_openai_answer()))

    await _call()

    [row] = recorded
    assert row["ok"] is True and row["feature"] == "analysis" and row["client_id"] == "c1"
    assert row["provider"] == "openai" and row["connection_id"] == "conn-1" and row["byo"] is True
    assert row["model"] == "m"
    assert row["usage"] == {"input_tokens": 60, "output_tokens": 20,
                            "cache_read_tokens": 40, "cache_creation_tokens": None}, (
        "prompt_tokens includes the cached part; the ledger counts uncached input + cache reads")


async def test_openai_length_is_truncation_after_usage_is_recorded(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai"))
    recorded = _records(monkeypatch)
    # A cut-off call: the arguments JSON is incomplete, exactly as the API sends it.
    answer = _openai_answer(finish="length")
    answer["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"language": "ka", "top'
    _mock(monkeypatch, "openai", lambda r: httpx.Response(200, json=answer))

    with pytest.raises(llm.LLMTruncatedError) as exc:
        await _call(max_tokens=300)
    assert "300 tokens" in str(exc.value)
    assert recorded and recorded[0]["ok"] is True, "the tokens were spent; the row says so"


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_openai_later_statuses_are_busy(monkeypatch, status):
    _pin_resolver(monkeypatch, _resolved("openai"))
    recorded = _records(monkeypatch)
    _mock(monkeypatch, "openai", lambda r: httpx.Response(
        status, json={"error": {"message": "slow down", "type": "rate_limit"}}))

    with pytest.raises(llm.LLMBusyError) as exc:
        await _call(opts=llm.COPILOT)     # max_retries=0: the first answer is the answer
    assert "slow down" in str(exc.value) and f"HTTP {status}" in str(exc.value)
    assert recorded and recorded[0]["ok"] is False and recorded[0]["provider"] == "openai"


async def test_openai_rejected_key_is_plain_english(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai"))
    _records(monkeypatch)
    _mock(monkeypatch, "openai", lambda r: httpx.Response(
        401, json={"error": {"message": "Incorrect API key provided: sk-***"}}))
    with pytest.raises(llm.LLMError) as exc:
        await _call()
    assert not isinstance(exc.value, llm.LLMBusyError)
    assert "rejected the API key" in str(exc.value) and "Incorrect API key" in str(exc.value)


async def test_openai_missing_key_never_reaches_the_network(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai", api_key=""))
    _records(monkeypatch)
    requests = _mock(monkeypatch, "openai", lambda r: httpx.Response(200, json=_openai_answer()))
    with pytest.raises(llm.LLMError) as exc:
        await _call()
    assert "No API key" in str(exc.value) and requests == []


async def test_openai_no_tool_call_is_a_plain_error(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai"))
    _records(monkeypatch)
    _mock(monkeypatch, "openai", lambda r: httpx.Response(200, json=_openai_answer(None, finish="stop")))
    with pytest.raises(llm.LLMError) as exc:
        await _call()
    assert "did not return a submit result" in str(exc.value)
    assert not isinstance(exc.value, llm.LLMTruncatedError)


async def test_openai_transports_the_same_call_over_sse_with_progress(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai"))
    _records(monkeypatch)
    args = json.dumps(ANSWER)
    cut = len(args) // 2
    chunks = [
        {"model": "gpt-5-2026", "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "submit", "arguments": ""}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": args[:cut]}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": args[cut:]}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 7}},
    ]
    requests = _mock(monkeypatch, "openai", lambda r: httpx.Response(
        200, content=_sse(chunks, done=True), headers={"content-type": "text/event-stream"}))
    seen: list[int] = []

    out = await _call(stream=True, on_progress=seen.append)

    assert out == ANSWER
    body = _body(requests[0])
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert seen and seen == sorted(seen) and len(set(seen)) == len(seen), seen


async def test_openai_stream_text_yields_deltas_and_reports_usage(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai", connection_id="conn-9"))
    recorded = _records(monkeypatch)
    chunks = [
        {"model": "gpt-5-2026", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}},
    ]
    requests = _mock(monkeypatch, "openai", lambda r: httpx.Response(
        200, content=_sse(chunks, done=True), headers={"content-type": "text/event-stream"}))

    assert await _stream() == ["Hel", "lo"]
    body = _body(requests[0])
    assert body["stream"] is True and "tools" not in body
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    [row] = recorded
    assert row["ok"] is True and row["connection_id"] == "conn-9" and row["model"] == "gpt-5-2026"
    assert row["usage"]["input_tokens"] == 12 and row["usage"]["output_tokens"] == 2


async def test_openai_probe_reports_ok_and_meters_it(monkeypatch):
    recorded = _records(monkeypatch)
    requests = _mock(monkeypatch, "openai", lambda r: httpx.Response(
        200, json=_openai_answer({"ok": True}, name=llm_base.PROBE_TOOL["name"])))
    out = await llm.probe(_resolved("openai", model="gpt-5-mini", connection_id="conn-2"))
    assert out["ok"] is True and "gpt-5" in out["detail"] and "ms" in out["detail"]
    body = _body(requests[0])
    assert body["tool_choice"]["function"]["name"] == llm_base.PROBE_TOOL["name"]
    [row] = recorded
    assert row["feature"] == "probe" and row["provider"] == "openai" and row["connection_id"] == "conn-2"


async def test_openai_probe_explains_a_rejected_key(monkeypatch):
    recorded = _records(monkeypatch)
    _mock(monkeypatch, "openai", lambda r: httpx.Response(
        401, json={"error": {"message": "Incorrect API key provided"}}))
    out = await llm.probe(_resolved("openai"))
    assert out == {"ok": False, "detail": out["detail"]}
    assert "rejected the API key" in out["detail"]
    assert recorded[0]["ok"] is False


async def test_probe_notices_a_model_that_ignores_the_tool(monkeypatch):
    _records(monkeypatch)
    _mock(monkeypatch, "openai", lambda r: httpx.Response(200, json=_openai_answer(None, finish="stop")))
    out = await llm.probe(_resolved("openai"))
    assert out["ok"] is False and "did not call the tool" in out["detail"]


# =========================================================================== #
# 3. Gemini
# =========================================================================== #
def _gemini_answer(args: dict | None = ANSWER, *, finish: str = "STOP",
                   meta: dict | None = None, name: str = "submit") -> dict:
    parts = [{"functionCall": {"name": name, "args": args}}] if args is not None else [{"text": "…"}]
    return {"candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": finish,
                            "index": 0}],
            "usageMetadata": meta or {"promptTokenCount": 100, "candidatesTokenCount": 20,
                                      "cachedContentTokenCount": 30, "thoughtsTokenCount": 5,
                                      "totalTokenCount": 125},
            "modelVersion": "gemini-2.5-flash-001"}


async def test_gemini_request_is_a_forced_function_call(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("gemini", model="gemini-2.5-flash", api_key="AIza-test"))
    _records(monkeypatch)
    requests = _mock(monkeypatch, "gemini", lambda r: httpx.Response(200, json=_gemini_answer()))

    out = await _call(max_tokens=555)

    assert out == ANSWER
    [req] = requests
    assert str(req.url) == ("https://generativelanguage.googleapis.com/v1beta/models/"
                            "gemini-2.5-flash:generateContent")
    assert "AIza-test" not in str(req.url), "a key in the query string is a key in the logs"
    assert req.headers["x-goog-api-key"] == "AIza-test"
    body = _body(req)
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "usr"}]}]
    assert body["generationConfig"] == {"maxOutputTokens": 555}
    assert body["toolConfig"] == {"functionCallingConfig": {
        "mode": "ANY", "allowedFunctionNames": ["submit"]}}
    [decl] = body["tools"][0]["functionDeclarations"]
    assert decl["name"] == "submit"
    for node in _nodes(decl["parameters"]):
        assert "additionalProperties" not in node, node
        assert "$schema" not in node
    assert decl["parameters"]["type"] == "OBJECT"
    assert decl["parameters"]["properties"]["speakers"]["items"]["type"] == "OBJECT"


async def test_gemini_success_records_uncached_input_and_thinking_output(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("gemini", connection_id="conn-g"))
    recorded = _records(monkeypatch)
    _mock(monkeypatch, "gemini", lambda r: httpx.Response(200, json=_gemini_answer()))
    await _call()
    [row] = recorded
    assert row["provider"] == "gemini" and row["connection_id"] == "conn-g" and row["ok"] is True
    assert row["usage"] == {"input_tokens": 70, "output_tokens": 25,
                            "cache_read_tokens": 30, "cache_creation_tokens": None}


async def test_gemini_max_tokens_is_truncation(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("gemini"))
    recorded = _records(monkeypatch)
    _mock(monkeypatch, "gemini", lambda r: httpx.Response(200, json=_gemini_answer(None, finish="MAX_TOKENS")))
    with pytest.raises(llm.LLMTruncatedError):
        await _call()
    assert recorded[0]["ok"] is True


@pytest.mark.parametrize("status", [429, 503])
async def test_gemini_later_statuses_are_busy(monkeypatch, status):
    _pin_resolver(monkeypatch, _resolved("gemini"))
    _records(monkeypatch)
    _mock(monkeypatch, "gemini", lambda r: httpx.Response(status, json={"error": {
        "code": status, "message": "Resource has been exhausted", "status": "RESOURCE_EXHAUSTED"}}))
    with pytest.raises(llm.LLMBusyError) as exc:
        await _call(opts=llm.COPILOT)
    assert "Resource has been exhausted" in str(exc.value)


async def test_gemini_blocked_prompt_is_a_plain_error(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("gemini"))
    _records(monkeypatch)
    _mock(monkeypatch, "gemini", lambda r: httpx.Response(200, json={
        "promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 9}}))
    with pytest.raises(llm.LLMError) as exc:
        await _call()
    assert "SAFETY" in str(exc.value)


async def test_gemini_stream_text_uses_sse_and_reports_the_final_usage(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("gemini", model="models/gemini-2.5-pro"))
    recorded = _records(monkeypatch)
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "Hel"}], "role": "model"}, "index": 0}],
         "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1}},
        {"candidates": [{"content": {"parts": [{"text": "lo"}], "role": "model"},
                         "finishReason": "STOP", "index": 0}],
         "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "totalTokenCount": 7},
         "modelVersion": "gemini-2.5-pro-001"},
    ]
    requests = _mock(monkeypatch, "gemini", lambda r: httpx.Response(
        200, content=_sse(chunks, done=False), headers={"content-type": "text/event-stream"}))

    assert await _stream() == ["Hel", "lo"]
    assert str(requests[0].url) == ("https://generativelanguage.googleapis.com/v1beta/models/"
                                    "gemini-2.5-pro:streamGenerateContent?alt=sse"), (
        "a `models/` prefix on the id is folded away rather than doubled")
    assert "tools" not in _body(requests[0])
    [row] = recorded
    assert row["usage"]["output_tokens"] == 2 and row["model"] == "gemini-2.5-pro-001"


async def test_gemini_probe_ok(monkeypatch):
    _records(monkeypatch)
    _mock(monkeypatch, "gemini", lambda r: httpx.Response(
        200, json=_gemini_answer({"ok": True}, name=llm_base.PROBE_TOOL["name"])))
    out = await llm.probe(_resolved("gemini", model="gemini-2.5-flash"))
    assert out["ok"] is True and "Gemini" in out["detail"]


# =========================================================================== #
# 4. Anthropic — a fake SDK client, the way test_scoring_import_progress does it
# =========================================================================== #
def _delta(fragment: str):
    return SimpleNamespace(type="content_block_delta",
                           delta=SimpleNamespace(partial_json=fragment, text=None))


def _message(payload: dict | None, stop_reason: str = "tool_use"):
    content = ([SimpleNamespace(type="tool_use", name="submit", input=payload)]
               if payload is not None else [SimpleNamespace(type="text", text="no")])
    return SimpleNamespace(stop_reason=stop_reason, model="claude-x",
                           usage=SimpleNamespace(input_tokens=11, output_tokens=22,
                                                 cache_read_input_tokens=3),
                           content=content)


class _FakeStream:
    def __init__(self, events, message, text: list[str] | None = None):
        self._events, self._message = list(events), message
        self.iterated = 0
        self.final_calls = 0
        self.text_stream = self._text(text or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        self.iterated += 1
        return self._events.pop(0)

    async def get_final_message(self):
        self.final_calls += 1
        return self._message

    @staticmethod
    async def _text(chunks):
        for c in chunks:
            yield c


class _FakeAnthropic:
    def __init__(self, stream: _FakeStream, error: Exception | None = None):
        self.messages = SimpleNamespace(stream=self._stream, create=self._create)
        self._st, self._error, self.kwargs = stream, error, None

    def _stream(self, **kw):
        self.kwargs = kw
        if self._error:
            raise self._error
        return self._st

    async def _create(self, **kw):
        self.kwargs = kw
        if self._error:
            raise self._error
        return self._st._message


def _fake_anthropic(monkeypatch, *, fragments=(), payload=ANSWER, stop_reason="tool_use",
                    text=None, error=None):
    st = _FakeStream([_delta(f) for f in fragments], _message(payload, stop_reason), text)
    fake = _FakeAnthropic(st, error)
    factory_calls: list[tuple] = []

    def factory(api_key, **opts):
        factory_calls.append((api_key, opts))
        return fake

    monkeypatch.setattr(llm, "client", factory)
    return fake, st, factory_calls


async def test_a_legacy_resolution_sends_anthropic_the_request_it_always_sent(monkeypatch):
    """No registry rows: the resolver answers with the caller's own key and model, and the
    SDK sees byte-identical kwargs to the pre-registry code."""
    seen = _pin_resolver(monkeypatch, Resolved("llm", "anthropic", "legacy-model", "legacy-key",
                                               None, source="legacy"))
    recorded = _records(monkeypatch)
    fake, _, factory_calls = _fake_anthropic(monkeypatch)

    out = await _call()

    assert out == ANSWER
    assert seen == [("c1", "llm", "legacy-key", "legacy-model")], (
        "the legacy settings the call site read are handed down as the bottom of the chain")
    assert fake.kwargs == {
        "model": "legacy-model",
        "max_tokens": 4096,
        "system": "sys",
        "tools": [TOOL],
        "tool_choice": {"type": "tool", "name": "submit"},
        "messages": [{"role": "user", "content": "usr"}],
    }
    [(api_key, opts)] = factory_calls
    assert api_key == "legacy-key" and opts["base_url"] is None
    assert opts["timeout"] is llm.ANALYSIS["timeout"] and opts["max_retries"] == 1
    [row] = recorded
    assert row["provider"] == "anthropic" and row["connection_id"] is None and row["byo"] is False
    assert row["usage"] == {"input_tokens": 11, "output_tokens": 22,
                            "cache_read_tokens": 3, "cache_creation_tokens": None}


async def test_anthropic_empty_system_and_cache_block(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("anthropic", base_url="https://gw.example"))
    _records(monkeypatch)
    fake, _, factory_calls = _fake_anthropic(monkeypatch)

    await _call(system="", cache_system=True)
    assert fake.kwargs["system"] is anthropic.NOT_GIVEN
    assert factory_calls[0][1]["base_url"] == "https://gw.example"

    await _call(cache_system=True)
    assert fake.kwargs["system"] == [{"type": "text", "text": "sys",
                                      "cache_control": {"type": "ephemeral"}}]


async def test_anthropic_stream_reports_progress_and_stops_when_the_bar_dies(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("anthropic"))
    _records(monkeypatch)
    _, st, _ = _fake_anthropic(monkeypatch, fragments=("ქართული" * 10, "ტექსტი" * 10, "დასასრული" * 10))
    seen: list[int] = []

    def flaky(n: int) -> None:
        seen.append(n)
        if len(seen) > 1:
            raise RuntimeError("the bar exploded")

    out = await _call(stream=True, on_progress=flaky)
    assert out == ANSWER
    assert len(seen) == 2, "progress stops at the first raise, and the call still completes"
    assert st.final_calls == 1


async def test_anthropic_max_tokens_is_truncation_after_usage_is_recorded(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("anthropic"))
    recorded = _records(monkeypatch)
    _fake_anthropic(monkeypatch, stop_reason="max_tokens")
    with pytest.raises(llm.LLMTruncatedError):
        await _call(stream=True)
    assert recorded and recorded[0]["ok"] is True


async def test_anthropic_api_error_is_an_llm_error_and_a_failed_row(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("anthropic"))
    recorded = _records(monkeypatch)
    _fake_anthropic(monkeypatch, error=anthropic.APIConnectionError(request=None))
    with pytest.raises(llm.LLMError) as exc:
        await _call()
    assert "Connection error" in str(exc.value)
    assert recorded[0]["ok"] is False and recorded[0]["provider"] == "anthropic"


async def test_anthropic_stream_text(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("anthropic"))
    recorded = _records(monkeypatch)
    fake, _, _ = _fake_anthropic(monkeypatch, text=["Hel", "lo"])
    assert await _stream() == ["Hel", "lo"]
    assert fake.kwargs == {"model": "m", "max_tokens": 1024, "system": "sys",
                           "messages": [{"role": "user", "content": "usr"}]}
    assert recorded[0]["usage"]["output_tokens"] == 22 and recorded[0]["model"] == "claude-x"


# =========================================================================== #
# 5. The front door itself
# =========================================================================== #
async def test_a_resolver_failure_never_loses_the_call(monkeypatch):
    """The rule the old overlay kept, enforced at the chokepoint: if the registry cannot be
    read at all, the call runs on the legacy layer the caller already holds."""
    async def boom(*a, **kw):
        raise RuntimeError("Database pool is not initialised")

    monkeypatch.setattr(llm.ai_resolve, "resolve", boom)
    recorded = _records(monkeypatch)
    fake, _, factory_calls = _fake_anthropic(monkeypatch)

    assert await _call() == ANSWER
    assert factory_calls[0][0] == "legacy-key" and fake.kwargs["model"] == "legacy-model"
    assert recorded[0]["provider"] == "anthropic"


async def test_an_unknown_provider_is_a_plain_error(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("cohere"))
    _records(monkeypatch)
    with pytest.raises(llm.LLMError) as exc:
        await _call()
    assert "cohere" in str(exc.value)


async def test_a_missing_model_is_a_plain_error(monkeypatch):
    _pin_resolver(monkeypatch, _resolved("openai", model=None))
    _records(monkeypatch)
    with pytest.raises(llm.LLMError) as exc:
        await _call()
    assert "No model" in str(exc.value)


async def test_probe_never_raises(monkeypatch):
    _records(monkeypatch)
    assert (await llm.probe(_resolved("cohere")))["ok"] is False
    out = await llm.probe(_resolved("openai", model=None))
    assert out["ok"] is False and "No model" in out["detail"]


def test_the_error_family_is_one_set_of_classes():
    """Adapters raise llm_base's classes; call sites catch llm's. They must be the same objects."""
    assert llm.LLMError is llm_base.LLMError
    assert llm.LLMBusyError is llm_base.LLMBusyError
    assert llm.LLMTruncatedError is llm_base.LLMTruncatedError
    assert issubclass(llm.LLMTruncatedError, llm.LLMError)
    assert llm.estimate_tokens is llm_base.estimate_tokens


def test_record_writes_provider_and_connection(monkeypatch):
    captured = {}

    async def capture(row):
        captured["row"] = row

    monkeypatch.setattr(llm, "_write_usage", capture)

    async def go():
        llm._record(feature="analysis", client_id="c1", integration_id=None, model="m",
                    usage={"input_tokens": 1, "output_tokens": 2}, latency_ms=3, ok=True,
                    provider="gemini", connection_id="conn-1")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(go())
    row = captured["row"]
    assert row[-2:] == ("gemini", "conn-1")
    assert row[4:6] == (1, 2)
