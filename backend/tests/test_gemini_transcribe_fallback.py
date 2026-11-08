"""Google's 2026-10-07 refusal of gemini-3.5-transcribe must not stop transcription.

From about 08:27 UTC that day Google answered every request to the Transcribe model — the bare
documented one too, and with thinking explicitly off — with

    400 INVALID_ARGUMENT: Thinking is not enabled for this model

We never send a thinking setting, so no request of ours can avoid it. On exactly that refusal a
recording is transcribed by a chat model on the same key; any other error still fails, and
Test connection never falls back, because it is how an operator sees that Google has fixed it.
"""
import pytest

from app.services import llm, voice
from app.services.ai_resolve import Resolved
from app.services.providers import stt_gemini, voice_base

from tests.test_voice import (ORIGINAL, _first, _gemini_reply, gemini_stt, resolver,  # noqa: F401
                              run, wire)

THINKING_400 = (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                "message": "Thinking is not enabled for this model"}})
SEGMENTS = {"language_code": "ka", "segments": [
    {"speaker": "speaker_0", "start": 0, "end": 1.5, "text": "გამარჯობა"}]}


@pytest.fixture
def usage(monkeypatch):
    rows = []
    monkeypatch.setattr(llm, "record_usage", lambda **kw: rows.append(kw))
    return rows


def _paths(wire):  # noqa: F811
    return [s["path"] for s in wire["sent"]]


def test_a_refused_transcribe_model_falls_back_to_a_chat_model(wire, resolver, usage):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    _first(wire, "/interactions", THINKING_400)
    _first(wire, ":generateContent", _gemini_reply(SEGMENTS))
    out = run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert out["text"] == "გამარჯობა"
    assert out["model"] == stt_gemini.FALLBACK_CHAT_MODEL
    assert "refusing requests on Google's side" in out["detail"]
    sent = _paths(wire)
    assert sent[0].endswith("/interactions")
    assert sent[-1].endswith(f"/models/{stt_gemini.FALLBACK_CHAT_MODEL}:generateContent")
    # billed to the model that ran, once
    assert [r["model"] for r in usage] == [stt_gemini.FALLBACK_CHAT_MODEL]
    assert usage[0]["ok"] is True


def test_the_connection_can_name_the_stand_in(wire, resolver, usage):  # noqa: F811
    resolver["stt"] = Resolved("stt", "gemini", "gemini-3.5-transcribe", "AIza-test", None,
                               settings={"fallback_model": "gemini-2.5-pro"})
    _first(wire, "/interactions", THINKING_400)
    _first(wire, ":generateContent", _gemini_reply(SEGMENTS))
    out = run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert out["model"] == "gemini-2.5-pro"
    assert _paths(wire)[-1].endswith("/models/gemini-2.5-pro:generateContent")


def test_a_transcribe_id_is_never_its_own_stand_in():
    res = Resolved("stt", "gemini", "gemini-3.5-transcribe", "k", None,
                   settings={"fallback_model": "gemini-3.5-transcribe"})
    assert stt_gemini.fallback_model(res) == stt_gemini.FALLBACK_CHAT_MODEL


@pytest.mark.parametrize("reply", [
    (400, {"error": {"code": 400, "message": "Audio is longer than the model accepts."}}),
    (402, {"error": {"code": 402, "message": "Your prepayment credits are depleted."}}),
    (500, {"error": {"code": 500, "message": "Internal error"}}),
])
def test_any_other_error_still_fails(wire, resolver, usage, reply):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    _first(wire, "/interactions", reply)
    with pytest.raises(voice_base.VoiceError):
        run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert not any(":generateContent" in p for p in _paths(wire))


def test_test_connection_reports_the_refusal_and_does_not_fall_back(wire, resolver):  # noqa: F811
    _first(wire, "/interactions", THINKING_400)
    _first(wire, ":generateContent", _gemini_reply(SEGMENTS))
    out = run(voice.probe(gemini_stt("gemini-3.5-transcribe")))
    assert out["ok"] is False
    assert "Thinking is not enabled" in out["detail"]
    assert "Google-side fault" in out["detail"]
    assert not any(":generateContent" in p for p in _paths(wire))


def test_a_healthy_transcribe_model_never_touches_the_stand_in(wire, resolver, usage):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    _first(wire, "/interactions", (200, {"status": "completed", "steps": [
        {"type": "model_output", "content": [{"type": "text", "text": "hello"}]}]}))
    out = run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert out["model"] == "gemini-3.5-transcribe"
    assert [r["model"] for r in usage] == ["gemini-3.5-transcribe"]
