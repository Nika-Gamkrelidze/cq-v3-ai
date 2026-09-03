"""The rubric-import progress bar: the percentage must be honest, and the quiet path must not move.

A bar that invents its own number is worse than no bar at all — it teaches the uploader that
the number lies, and after that nothing on the page can reassure them. So the properties these
tests pin are not "a bar appears"; they are the four claims the bar makes about reality:

  1. **The denominator is real.** It is `estimate_output_tokens(text)` — the tokens this
     specific document needs in order to come back with its criteria verbatim — measured with
     the same script-aware yardstick (`llm.estimate_tokens`) that counts the tokens streaming
     in. A Georgian scorecard and an English one of identical length must therefore get very
     different denominators; a single chars-per-token constant would make one of them fiction.
  2. **The number only ever rises, and never reaches 100 before the draft does.** The estimate
     is deliberately conservative, so a real import finishes in the eighties: `draft` is the
     terminal state, not the percentage.
  3. **Extraction has no percentage at all**, because pypdf/openpyxl/python-docx offer no
     progress signal and inventing one is exactly the lie above.
  4. **Nothing about it can break the import.** A failure after the stream has opened can no
     longer be a status code, so it must arrive as an `error` event carrying the SAME text the
     blocking route would have returned; and a callback that raises must cost the user their
     bar, not their upload.

Everything here runs with no database, no Anthropic key and no network. Two tests drive the
REAL `llm.call_tool` against a fake Anthropic client, because the on_progress contract lives
inside it — monkeypatching `call_tool` there would only test the monkeypatch.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import scoring as scoring_router
from app.services import llm
from app.services import scoring_import as si
from app.services.auth import Principal

# conftest's autouse `no_llm` fixture swaps `llm.call_tool` for a detonator during every test.
# The genuine coroutine is captured at IMPORT time (collection happens before any fixture runs,
# and the patch is undone after each test), so the two tests that need the real thing can put
# it back deliberately rather than reaching around the guard.
_REAL_CALL_TOOL = llm.call_tool


# --------------------------------------------------------------------------- #
# Fixtures: one real-shaped Georgian scorecard, one model answer
# --------------------------------------------------------------------------- #
GEORGIAN_ROW = ("A1 (1/-3): თანამშრომელმა უპასუხა ზარს სტანდარტული ფრაზით და მიესალმა "
                "მომხმარებელს, დაუდასტურა დახმარებისთვის მზადყოფნა.\n")
ENGLISH_ROW = ("A1 (1/-3): The employee answered the call with the standard phrase and "
               "greeted the customer, confirming readiness to help.\n")

KA_TEXT = GEORGIAN_ROW * 40          # 4,800 chars -> ~9,900 output tokens, well inside budget
KA_NEEDED = si.estimate_output_tokens(KA_TEXT)
KA_HUGE = GEORGIAN_ROW * 200         # 24,000 chars -> ~49,500 tokens: over the 32k budget

RAW = {"general_instructions": "1/-3 ნიშნავს +1 შესრულებისას, -3 დარღვევისას",
       "dimensions": [
           {"name": "კონტაქტის დამყარება", "description": "დ1",
            "guidance": "A1 (1/-3): ...", "max_points": 10},
           {"name": "კომუნიკაცია", "description": "დ2",
            "guidance": "B1 (1/-2): ...", "max_points": 22}]}

UPLOAD = b"binary-scorecard-bytes"
CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _Req:
    """The one thing `_rubric_stream` asks of a Request: has the uploader walked away?"""

    async def is_disconnected(self) -> bool:
        return False


class _Upload:
    """The three things `_import_rubric` asks of an UploadFile."""

    def __init__(self, data: bytes = UPLOAD, filename: str = "standard.xlsx") -> None:
        self._data, self.filename, self.content_type = data, filename, CT

    async def read(self) -> bytes:
        return self._data


async def _fake_settings():
    return {"anthropic_api_key": "k", "llm_model": "m"}


def _install(monkeypatch, call_tool, *, text: str = KA_TEXT):
    """Wire the whole import path to fakes: no key lookup, no file parsing, no Anthropic."""
    monkeypatch.setattr(si.settings_store, "get_effective", _fake_settings)
    monkeypatch.setattr(si.llm, "call_tool", call_tool)
    monkeypatch.setattr(scoring_router.kb_ingest, "extract_text",
                        lambda filename, content_type, data: text)


async def _collect(agen) -> list[str]:
    return [chunk async for chunk in agen]


def _frames(chunks: list[str]) -> list[tuple[str, dict | None]]:
    """SSE text -> [(event name, payload)]; a `: ping` comment becomes ('ping', None)."""
    out: list[tuple[str, dict | None]] = []
    for chunk in chunks:
        if chunk.startswith(":"):
            out.append(("ping", None))
            continue
        head, _, rest = chunk.partition("\n")
        out.append((head[len("event: "):], json.loads(rest[len("data: "):].strip())))
    return out


async def _stream(monkeypatch, call_tool, *, text: str = KA_TEXT, throttle: float | None = 0.0):
    _install(monkeypatch, call_tool, text=text)
    if throttle is not None:
        monkeypatch.setattr(scoring_router, "PROGRESS_MIN_INTERVAL_S", throttle)
    return _frames(await _collect(scoring_router._rubric_stream(
        UPLOAD, "standard.xlsx", CT, "c1", _Req())))


# --------------------------------------------------------------------------- #
# A fake Anthropic transport, for the two tests that drive the real call_tool
# --------------------------------------------------------------------------- #
def _delta(fragment: str):
    """`content_block_delta` carrying tool-input JSON — the only per-token signal a forced
    tool call emits, and therefore the one `_stream_progress` counts."""
    return SimpleNamespace(type="content_block_delta",
                           delta=SimpleNamespace(partial_json=fragment, text=None))


def _final_message(payload: dict, stop_reason: str = "tool_use"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=11, output_tokens=22),
        content=[SimpleNamespace(type="tool_use", name=si.RUBRIC_TOOL["name"], input=payload)])


class _FakeStream:
    def __init__(self, events, message):
        self._events, self._message = list(events), message
        self.iterated = 0
        self.final_calls = 0

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


class _FakeAnthropic:
    def __init__(self, stream: _FakeStream):
        self.messages = SimpleNamespace(stream=self._stream, create=self._create)
        self._st, self.kwargs = stream, None

    def _stream(self, **kw):
        self.kwargs = kw
        return self._st

    async def _create(self, **kw):
        self.kwargs = kw
        return self._st._message


def _fake_anthropic(monkeypatch, *, fragments=(), payload=RAW, stop_reason="tool_use"):
    """Install a fake Anthropic client and neutralise usage accounting (which wants a pool)."""
    st = _FakeStream([_delta(f) for f in fragments], _final_message(payload, stop_reason))
    fake = _FakeAnthropic(st)
    recorded: list[dict] = []
    monkeypatch.setattr(llm, "client", lambda api_key, **opts: fake)
    monkeypatch.setattr(llm, "_record", lambda **kw: recorded.append(kw))
    return fake, st, recorded


# --------------------------------------------------------------------------- #
# 1. The path with no progress bar must be byte-for-byte what it always was
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_import_without_a_progress_callback_is_unchanged(monkeypatch):
    """`rubric_from_text` with no `on_progress` must pass `on_progress=None` down and return
    exactly what it returned before the bar existed."""
    seen = {}

    async def fake_call_tool(**kw):
        seen.update(kw)
        return RAW

    _install(monkeypatch, fake_call_tool)
    draft = await si.rubric_from_text(KA_TEXT, client_id="c1")

    assert seen["on_progress"] is None, "no callback must reach llm when the caller gave none"
    assert seen["stream"] is True and seen["feature"] == "scoring_import"
    assert draft["rubric"] == RAW["general_instructions"]
    assert [d["name"] for d in draft["dimensions"]] == ["კონტაქტის დამყარება", "კომუნიკაცია"]
    assert [d["weight"] for d in draft["dimensions"]] == [31.25, 68.75]
    assert all("max_points" not in d for d in draft["dimensions"])


@pytest.mark.asyncio
async def test_call_tool_without_a_callback_never_hand_drains_the_stream(monkeypatch):
    """THE regression to fear: `call_tool` is shared with chat, curation, analysis and
    factcheck. With no `on_progress` it must run the code path it has always run — the SDK
    consumes the stream inside `get_final_message()` and nothing here touches the events."""
    fake, st, recorded = _fake_anthropic(monkeypatch, fragments=("ქართული ტექსტი" * 20,))

    out = await _REAL_CALL_TOOL(
        feature="scoring_import", client_id="c1", api_key="k", model="m",
        system="s", user="u", tool=si.RUBRIC_TOOL, opts=llm.RESTRUCTURE,
        max_tokens=si.MAX_OUTPUT_TOKENS, stream=True)

    assert out == RAW
    assert st.iterated == 0, "the stream was hand-drained with no callback asking for it"
    assert st.final_calls == 1
    assert fake.kwargs["tool_choice"] == {"type": "tool", "name": si.RUBRIC_TOOL["name"]}
    assert recorded and recorded[0]["ok"] is True and recorded[0]["feature"] == "scoring_import"


@pytest.mark.asyncio
async def test_truncation_still_raises_with_no_callback(monkeypatch):
    """The max_tokens check is the reason a partial tool input is not silently accepted; a
    progress callback must not be what switches it on."""
    _fake_anthropic(monkeypatch, stop_reason="max_tokens")

    with pytest.raises(llm.LLMTruncatedError):
        await _REAL_CALL_TOOL(
            feature="scoring_import", client_id="c1", api_key="k", model="m",
            system="s", user="u", tool=si.RUBRIC_TOOL, opts=llm.RESTRUCTURE,
            max_tokens=si.MAX_OUTPUT_TOKENS, stream=True)


# --------------------------------------------------------------------------- #
# 2. Honesty: monotonic, and never 100 before the draft
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_progress_never_runs_backwards_and_never_reaches_100_before_the_draft(monkeypatch):
    """Token counts arrive jumpy, repeated and — if a `message_delta` corrects an estimate —
    out of order. None of that may show as a bar that stalls, twitches backwards, or sits at
    100% while the user waits. `draft` is what says finished; the number never does."""
    async def fake_call_tool(**kw):
        for n in (0, 800, 800, 400, 2500, 2400, 999_999):
            kw["on_progress"](n)
        return RAW

    _install(monkeypatch, fake_call_tool)
    seen: list[dict] = []
    draft = await si.rubric_from_text(KA_TEXT, client_id="c1", on_progress=seen.append)

    pcts = [d["pct"] for d in seen]
    tokens = [d["tokens"] for d in seen]
    assert pcts == sorted(pcts), f"the bar ran backwards: {pcts}"
    assert tokens == sorted(tokens), f"the token count ran backwards: {tokens}"
    assert max(pcts) == 99, "a count past the estimate must clamp to 99, not overflow it"
    assert all(p < 100 for p in pcts), "100% may only be implied by the draft arriving"
    assert seen[0] == {"stage": "analyzing", "tokens": 0, "expected": KA_NEEDED, "pct": 0}, (
        "the analyzing stage must be announced BEFORE the model call, not on first token — "
        "the quiet seconds while it reads are exactly the wait this feature removes")
    assert [d["name"] for d in draft["dimensions"]] == ["კონტაქტის დამყარება", "კომუნიკაცია"]


@pytest.mark.asyncio
async def test_the_wire_carries_the_same_guarantee(monkeypatch):
    """Same property one layer up: `stage extracting` (no number at all), `stage analyzing`
    (the denominator), rising `progress`, then `draft`."""
    async def fake_call_tool(**kw):
        for n in (0, 800, 400, 2500, 999_999):
            kw["on_progress"](n)
        return RAW

    frames = await _stream(monkeypatch, fake_call_tool)
    names = [n for n, _ in frames]

    assert [n for n in names if n in ("stage", "draft", "error")] == ["stage", "stage", "draft"]
    assert frames[0] == ("stage", {"stage": "extracting"}), (
        "extraction is one opaque pypdf/openpyxl call — a percentage here would be invented")
    assert frames[1] == ("stage", {"stage": "analyzing", "expected": KA_NEEDED})
    progress = [d for n, d in frames if n == "progress"]
    pcts = [d["pct"] for d in progress]
    assert pcts == sorted(pcts) and len(set(pcts)) == len(pcts), f"not monotonic: {pcts}"
    assert all(0 < p < 100 for p in pcts), pcts
    assert all(d["expected"] == KA_NEEDED and d["stage"] == "analyzing" for d in progress)
    assert names[-1] == "draft", "the draft is the terminal state — not the percentage"


@pytest.mark.asyncio
async def test_a_burst_of_updates_is_throttled_off_the_wire(monkeypatch):
    """The throttle: at most one frame per 200 ms, so ~180 rising callbacks in one instant
    reach the browser as a handful. Dropping frames is free precisely because the percentage
    only ever rises — the next frame carries it, and `draft` carries the truth."""
    async def fake_call_tool(**kw):
        for n in range(0, 9_000, 50):        # ~180 callbacks, all inside one instant
            kw["on_progress"](n)
        return RAW

    frames = await _stream(monkeypatch, fake_call_tool, throttle=None)   # real 0.2 s interval
    assert len([n for n, _ in frames if n == "progress"]) <= 2
    assert frames[-1][0] == "draft"


# --------------------------------------------------------------------------- #
# 3. The denominator is script-aware, or the percentage is fiction
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_denominator_is_the_script_aware_estimate(monkeypatch):
    """Two documents of IDENTICAL character length. Georgian must cost several times more to
    write back — that difference is the whole reason the bar can be honest, and a single
    chars-per-token constant cannot express it."""
    ka = GEORGIAN_ROW * 40
    en = (ENGLISH_ROW * (len(ka) // len(ENGLISH_ROW) + 1))[:len(ka)]
    assert len(ka) == len(en)

    assert si.estimate_output_tokens(ka) > 4 * si.estimate_output_tokens(en), (
        "same length, same denominator would mean one of the two bars is lying")

    async def fake_call_tool(**kw):
        return RAW

    expected: list[int] = []
    for text in (ka, en):
        _install(monkeypatch, fake_call_tool, text=text)
        seen: list[dict] = []
        await si.rubric_from_text(text, client_id="c1", on_progress=seen.append)
        expected.append(seen[0]["expected"])

    assert expected == [si.estimate_output_tokens(ka), si.estimate_output_tokens(en)], (
        "the bar must divide by the same estimate the oversize guard is computed from")
    assert expected[0] > 4 * expected[1]


# --------------------------------------------------------------------------- #
# 4. Once the stream is open a failure can only be an event, and must say the same thing
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_oversize_arrives_as_an_error_event_with_the_blocking_route_s_words(monkeypatch):
    """The 200 is already on the wire, so there is no status left to fail with. The uploader
    must still read the message the blocking route would have handed them."""
    async def never(**kw):
        raise AssertionError("the oversize guard must fire before any model call")

    _install(monkeypatch, never, text=KA_HUGE)
    with pytest.raises(scoring_router._ImportFailed) as exc:
        await scoring_router._rubric_draft(UPLOAD, "standard.xlsx", CT, "c1")
    blocking = exc.value

    frames = await _stream(monkeypatch, never, text=KA_HUGE)
    assert [n for n, _ in frames] == ["stage", "error"]
    assert frames[-1][1] == {"detail": blocking.detail}
    assert blocking.status == 422
    assert "too long" in blocking.detail and f"{si.MAX_OUTPUT_TOKENS:,}" in blocking.detail


@pytest.mark.asyncio
async def test_truncation_mid_stream_arrives_as_an_error_event(monkeypatch):
    """A failure that lands AFTER progress has started — the case that cannot be a status
    code — still carries the blocking route's exact text."""
    async def truncated(**kw):
        cb = kw.get("on_progress")
        if cb:
            cb(1_500)
        raise si.llm.LLMTruncatedError("budget")

    _install(monkeypatch, truncated)
    with pytest.raises(scoring_router._ImportFailed) as exc:
        await scoring_router._rubric_draft(UPLOAD, "standard.xlsx", CT, "c1")
    blocking = exc.value

    frames = await _stream(monkeypatch, truncated)
    names = [n for n, _ in frames]
    assert names[:2] == ["stage", "stage"], "the failure landed after streaming had begun"
    assert names[-1] == "error" and "draft" not in names
    assert frames[-1][1] == {"detail": blocking.detail}
    assert "output budget" in blocking.detail


@pytest.mark.asyncio
async def test_a_doomed_import_still_answers_200_on_the_stream_transport(monkeypatch):
    """It is the transport, not the outcome, that the status line describes."""
    async def never(**kw):
        raise AssertionError("no model call expected")

    _install(monkeypatch, never, text=KA_HUGE)
    resp = await scoring_router._import_rubric(_Req(), _Upload(), "c1", 1)

    assert resp.status_code == 200
    assert resp.media_type == "text/event-stream"
    assert resp.headers["x-accel-buffering"] == "no"


# --------------------------------------------------------------------------- #
# 5. The two transports must not drift
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_draft_event_is_byte_identical_to_the_blocking_body(monkeypatch):
    """Two editors consume this draft. If the streamed payload and the blocking one can
    differ by so much as a key order, one of the two consumers is being lied to."""
    async def fake_call_tool(**kw):
        return json.loads(json.dumps(RAW))      # a fresh dict per call, as the SDK gives

    _install(monkeypatch, fake_call_tool)
    blocking = await scoring_router._rubric_draft(UPLOAD, "standard.xlsx", CT, "c1")

    chunks = await _collect(scoring_router._rubric_stream(
        UPLOAD, "standard.xlsx", CT, "c1", _Req()))
    draft_chunk = [c for c in chunks if c.startswith("event: draft")]
    assert len(draft_chunk) == 1, chunks

    assert draft_chunk[0] == "event: draft\ndata: " + json.dumps(
        blocking, separators=(",", ":"), default=str) + "\n\n"


# --------------------------------------------------------------------------- #
# 6. A broken bar costs the bar, not the upload
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_raising_progress_callback_cannot_break_call_tool(monkeypatch):
    """`on_progress` is somebody's UI, not part of the call. If it throws, the tokens keep
    flowing and the answer still comes back — the user loses a bar, not a multi-minute import."""
    _, st, _ = _fake_anthropic(monkeypatch, fragments=("ქართული" * 10, "ტექსტი" * 10))

    def boom(_tokens):
        raise RuntimeError("the bar exploded")

    out = await _REAL_CALL_TOOL(
        feature="scoring_import", client_id="c1", api_key="k", model="m",
        system="s", user="u", tool=si.RUBRIC_TOOL, opts=llm.RESTRUCTURE,
        max_tokens=si.MAX_OUTPUT_TOKENS, stream=True, on_progress=boom)

    assert out == RAW
    assert st.final_calls == 1, "the stream must still be drained to its final message"


@pytest.mark.asyncio
async def test_a_bar_that_dies_mid_import_still_yields_the_draft(monkeypatch):
    """End to end over the real `call_tool`: the transport's emitter starts throwing once
    tokens are flowing. Progress stops; the import does not.

    (The guard lives at the `llm` boundary — it covers every callback made from inside the
    stream loop, which is every callback a transport receives while the model is writing.)"""
    monkeypatch.setattr(llm, "call_tool", _REAL_CALL_TOOL)
    _fake_anthropic(monkeypatch, fragments=("ქართული" * 10, "ტექსტი" * 10, "დასასრული" * 10))
    monkeypatch.setattr(si.settings_store, "get_effective", _fake_settings)

    seen: list[dict] = []

    def flaky(payload: dict) -> None:
        seen.append(payload)
        if len(seen) > 1:
            raise RuntimeError("the bar exploded")

    draft = await si.rubric_from_text(KA_TEXT, client_id="c1", on_progress=flaky)

    assert [d["name"] for d in draft["dimensions"]] == ["კონტაქტის დამყარება", "კომუნიკაცია"]
    assert len(seen) == 2, "progress must stop at the first raise, not keep retrying it"


# --------------------------------------------------------------------------- #
# 7. Auth and the size gate still answer with a status, before anything streams
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_size_gate_runs_before_the_transport_is_chosen(monkeypatch):
    """`?stream=1` must not turn a rejected upload into a 200 with an `error` frame — the
    caller that sent 40 MB deserves a status line, and the AI is never reached."""
    started = []
    monkeypatch.setattr(scoring_router, "_sse_response",
                        lambda body: started.append(body))

    with pytest.raises(HTTPException) as empty:
        await scoring_router._import_rubric(_Req(), _Upload(b""), "c1", 1)
    assert empty.value.status_code == 400

    monkeypatch.setattr(scoring_router, "MAX_IMPORT_BYTES", 4)
    with pytest.raises(HTTPException) as big:
        await scoring_router._import_rubric(_Req(), _Upload(b"much too large"), "c1", 1)
    assert big.value.status_code == 413

    assert started == [], "a stream was opened for a request that had already been rejected"


@pytest.mark.asyncio
async def test_auth_rejections_precede_the_stream():
    """The route dependencies resolve before the body runs at all, so an unauthorized import
    is a plain 401/403/404 in both transports."""
    with pytest.raises(HTTPException) as anon:
        scoring_router._tenant_owner(Principal(kind="anonymous"))
    assert anon.value.status_code == 401

    with pytest.raises(HTTPException) as member:
        scoring_router._tenant_owner(Principal(kind="tenant", client_id="c1", role="member"))
    assert member.value.status_code == 403

    with pytest.raises(HTTPException) as tenant:
        await scoring_router._scope("t", Principal(kind="tenant", client_id="c1", role="owner"))
    assert tenant.value.status_code == 401

    with pytest.raises(HTTPException) as bad_uuid:
        await scoring_router._scope("not-a-uuid", Principal(kind="superadmin"))
    assert bad_uuid.value.status_code == 404, "and without touching the pool to find out"
