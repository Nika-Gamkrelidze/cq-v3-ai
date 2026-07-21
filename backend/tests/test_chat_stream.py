"""The three properties of the chat transport that are invisible from the outside.

Everything here runs with no database and no network: `_drive`, `_stream_body` and
`_terminal_payloads` are driven directly with a stubbed engine, because each of these defects
shipped once already and none of them is visible in a status code.

  1. **A finished generation is stored.** The engine never touches the database by design, so
     if the router does not file the terminal envelope the row stays 'running' until the
     reaper errors it out — every read returns `{"state":"running"}` forever and the entire
     precompute product promise is silently absent while every endpoint returns 200.
  2. **Exactly one `open`, one monotonic sequence.** The router and the engine both
     legitimately announce a stream start; emitting both put two `open` frames and a restarted
     counter on the wire.
  3. **The idle keepalive actually fires.** Every generation on this build finishes in
     milliseconds, so the 15-second branch is never exercised by an end-to-end run — it can
     only be shown to work by compressing the interval, which is what this does.
"""
import asyncio
import json

import pytest


class _Req:
    """The two things `_stream_body` asks of a Request."""

    async def is_disconnected(self) -> bool:
        return False


def _collect(agen_factory):
    async def _run():
        return [chunk async for chunk in agen_factory()]
    return asyncio.run(_run())


REFUSAL_ENVELOPE = {
    "proto": 1,
    "turn_ref": "t-1",
    "suggest_ref": "sg_1",
    "conversation_ref": "conv-1",
    "client_id": "11111111-1111-1111-1111-111111111111",
    "channel": "web",
    "locale": "ru",
    "grounding": {"grounded": False, "reason": "no_hits", "method": "none",
                  "top_score": None, "hit_count": 0, "kb_present": True},
    "citations": [],
    "tier1": [{"n": 1, "title": "Refunds", "text": "…"}],
    "suggestions": [{"index": 0, "kind": "escalate", "text": "sorry", "citations": []}],
    "reply": None,
    "handoff": {"recommended": True, "reason": "no_hits", "summary": "…"},
    "usage": {"input_tokens": None, "output_tokens": None, "model": "m",
              "latency_ms": {"total": 4}},
}


def _stub_engine(monkeypatch, envelope=REFUSAL_ENVELOPE):
    from app.services import chat as engine

    async def _run(ctx):
        yield engine.ChatEvent("open", 1, {"suggest_ref": "sg_1", "proto": 1})
        yield engine.ChatEvent("grounding", 2, dict(envelope["grounding"]))
        yield engine.ChatEvent("tier1", 3, {"cards": envelope["tier1"]})
        yield engine.ChatEvent("done", 4, envelope)

    monkeypatch.setattr(engine, "run_suggest", _run)


def _capture_store(monkeypatch):
    from app.routers import chat as chat_router

    stored: dict = {}

    async def _finish(client_id, suggest_ref, *, envelope, state="ready", latency_ms=None):
        stored.update({"client_id": client_id, "suggest_ref": suggest_ref,
                       "envelope": envelope, "state": state})

    async def _fail(client_id, suggest_ref, error):
        stored.update({"client_id": client_id, "suggest_ref": suggest_ref,
                       "state": "error", "error": error})

    monkeypatch.setattr(chat_router.chat_store, "finish_suggestion", _finish)
    monkeypatch.setattr(chat_router.chat_store, "fail_suggestion", _fail)
    return stored


# --------------------------------------------------------------------------- #
# 1. Persistence
# --------------------------------------------------------------------------- #
def test_drive_stores_the_terminal_envelope(monkeypatch):
    """The regression that made the whole warm path non-functional: nothing called
    finish_suggestion, so no suggestion ever left 'running'."""
    from app.routers import chat as chat_router

    _stub_engine(monkeypatch)
    stored = _capture_store(monkeypatch)

    events = _collect(lambda: chat_router._drive("cid", "sg_1", object()))

    assert stored["envelope"] == REFUSAL_ENVELOPE
    assert stored["suggest_ref"] == "sg_1"
    # A gate refusal is a deliberate, successful outcome — not an error state.
    assert stored["state"] == "refused"
    assert events[-1][0] == "done"


def test_drive_marks_a_crashed_generation_terminal(monkeypatch):
    from app.routers import chat as chat_router
    from app.services import chat as engine

    async def _boom(ctx):
        yield engine.ChatEvent("open", 1, {})
        raise RuntimeError("retrieval exploded")

    monkeypatch.setattr(engine, "run_suggest", _boom)
    stored = _capture_store(monkeypatch)

    with pytest.raises(RuntimeError):
        _collect(lambda: chat_router._drive("cid", "sg_1", object()))
    assert stored["state"] == "error"


# --------------------------------------------------------------------------- #
# 2. Wire shape
# --------------------------------------------------------------------------- #
def test_live_stream_emits_one_open_and_a_monotonic_sequence(monkeypatch):
    from app.routers import chat as chat_router

    _stub_engine(monkeypatch)
    _capture_store(monkeypatch)

    async def _live(client_id, suggest_ref, row):
        async for item in chat_router._drive(client_id, suggest_ref, object()):
            yield item

    monkeypatch.setattr(chat_router, "_engine_events", _live)

    chunks = _collect(lambda: chat_router._stream_body(
        _Req(), "cid", "sg_1", {"state": "running"}, True))
    body = "".join(chunks)

    assert body.count("event: open") == 1, body
    names = [line[len("event: "):] for line in body.splitlines() if line.startswith("event: ")]
    assert names == ["open", "grounding", "tier1", "done"], names
    seqs = [json.loads(block.split("\n", 1)[0])["seq"] for block in body.split("data: ")[1:]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs


def test_replay_reproduces_the_live_frames_including_the_refusal_reason():
    """A reconnecting client re-reads the row. It must see the same events — in particular
    `grounding.reason`, which explains the refusal and has no column of its own."""
    from app.routers import chat as chat_router

    row = {"state": "refused", "envelope": REFUSAL_ENVELOPE}
    payloads = chat_router._terminal_payloads(row, seq=1)
    by_name = {name: data for name, data in payloads}

    assert by_name["grounding"]["reason"] == "no_hits"
    assert by_name["tier1"]["cards"] == REFUSAL_ENVELOPE["tier1"]
    # Byte-identical to what the blocking read returns, which is the ADR's transport claim.
    assert by_name["done"]["turn"] == chat_router._envelope(row) == REFUSAL_ENVELOPE


# --------------------------------------------------------------------------- #
# 3. Keepalive
# --------------------------------------------------------------------------- #
def test_idle_stream_emits_a_ping_comment(monkeypatch):
    from app.routers import chat as chat_router

    monkeypatch.setattr(chat_router, "STREAM_PING_S", 0.05)

    async def _slow(client_id, suggest_ref):
        await asyncio.sleep(0.2)
        yield ("done", {"turn": REFUSAL_ENVELOPE, "seq": 1})

    monkeypatch.setattr(chat_router, "_poll_events", _slow)

    chunks = _collect(lambda: chat_router._stream_body(
        _Req(), "cid", "sg_1", {"state": "running"}, False))

    assert ": ping\n\n" in chunks, chunks
    assert chunks[-1].startswith("event: done"), chunks[-1]
