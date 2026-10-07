"""A provider that answers with a JSON LIST where a JSON object is expected must not crash us.

Google's gateway wraps some error bodies — and the odd response — in a one-element array:
`[{"error": {"code": 400, "message": "...", "status": "INVALID_ARGUMENT"}}]`. The error
classifier read `resp.json().get("error")` straight off that, so the user never saw Google's
sentence; they saw

    Transcription failed: 'list' object has no attribute 'get'

instead, with no way to tell whether the key, the audio or the model was the problem. The
failure being masked was a real HTTP error; these tests pin that its own message comes through,
and that a 200 whose body is wrapped the same way still parses.
"""
import json

import httpx
import pytest

from app.services import voice
from app.services import elevenlabs as el
from app.services.ai_resolve import Resolved
from app.services.providers import voice_base

from tests.test_voice import ORIGINAL, gemini_stt, openai_stt, resolver, run, wire  # noqa: F401


GOOGLE_400 = [{"error": {"code": 400, "status": "INVALID_ARGUMENT",
                         "message": "Audio is longer than the model accepts."}}]


def test_a_list_wrapped_google_error_keeps_its_message(wire, resolver):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    wire["responses"] = {"/interactions": (400, GOOGLE_400), **wire["responses"]}
    with pytest.raises(voice_base.VoiceError) as exc:
        run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert "Audio is longer than the model accepts." in str(exc.value)
    assert exc.value.status == 400


@pytest.mark.parametrize("status, expect_code", [(401, "invalid_key"), (403, "missing_permission"),
                                                  (429, "quota")])
def test_a_list_wrapped_error_still_classifies_by_status(wire, resolver, status, expect_code):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    body = [{"error": {"code": status, "message": "nope", "status": "X"}}]
    wire["responses"] = {"/interactions": (status, body), **wire["responses"]}
    with pytest.raises(voice_base.VoiceError) as exc:
        run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert exc.value.code == expect_code


@pytest.mark.parametrize("status, sentence", [
    (402, "Your prepayment credits are depleted. Please go to AI Studio at "
          "https://ai.studio/projects to manage your project and billing. Learn more at "
          "https://ai.google.dev/gemini-api/docs/billing#prepay. "),  # the live body's tail
    (429, "Quota exceeded for metric: generate_requests_per_model_per_day, limit: 100"),
])
def test_a_quota_error_keeps_the_providers_sentence(wire, resolver, status, sentence):  # noqa: F811
    """Our own wording cannot say which billing account or which limit; Google's does."""
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    body = {"error": {"code": status, "status": "RESOURCE_EXHAUSTED", "message": sentence}}
    wire["responses"] = {"/interactions": (status, body), **wire["responses"]}
    with pytest.raises(voice_base.VoiceError) as exc:
        run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert sentence.strip().rstrip(".") in str(exc.value)
    assert ". ." not in str(exc.value) and ".." not in str(exc.value)
    assert exc.value.code == "quota"


@pytest.mark.parametrize("body", [[], [1, 2], "oops", 7, None, [None, "x"]])
def test_odd_error_bodies_never_crash_the_classifier(wire, resolver, body):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    wire["responses"] = {"/interactions": (500, body), **wire["responses"]}
    with pytest.raises(voice_base.VoiceError) as exc:
        run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert "500" in str(exc.value)


def test_a_list_wrapped_interaction_still_transcribes(wire, resolver):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    interaction = {"status": "completed", "steps": [{"type": "model_output", "content": [
        {"type": "text", "text": "hello there"}]}]}
    wire["responses"] = {"/interactions": (200, [interaction]), **wire["responses"]}
    out = run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert out["text"] == "hello there"


def test_a_list_wrapped_generate_content_reply_still_transcribes(wire, resolver):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-2.5-flash")
    reply = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": json.dumps({"language_code": "en", "segments": [
            {"speaker": "speaker_0", "start": 0, "end": 1, "text": "wrapped"}]})}]}}]}
    wire["responses"] = {":generateContent": (200, [reply]), **wire["responses"]}
    out = run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert out["text"] == "wrapped"


def test_a_list_wrapped_upload_reply_is_read(wire, resolver, monkeypatch):  # noqa: F811
    """The Files API steps (start -> upload -> poll) read dict fields off JSON too."""
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    big = b"x" * (voice_base_inline_limit() + 10)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload/v1beta/files"):
            return httpx.Response(200, headers={"X-Goog-Upload-URL":
                                                "https://generativelanguage.googleapis.com/upload/s/1"})
        if "/upload/s/1" in str(request.url):
            return httpx.Response(200, json=[{"file": {"name": "files/q", "state": "ACTIVE",
                                                       "uri": "https://generativelanguage.googleapis.com/v1beta/files/q"}}])
        if request.url.path.endswith("/interactions"):
            return httpx.Response(200, json={"status": "completed", "steps": [
                {"type": "model_output", "content": [{"type": "text", "text": "big"}]}]})
        return httpx.Response(200, json={})

    monkeypatch.setattr(voice_base, "_transport", httpx.MockTransport(handler))
    out = run(voice.transcribe("t-1", big, "a.flac", "audio/flac", transcription=ORIGINAL))
    assert out["text"] == "big"


def voice_base_inline_limit() -> int:
    from app.services.providers import stt_gemini
    return stt_gemini.TRANSCRIBE_INLINE_MAX_BYTES


def test_a_list_wrapped_openai_transcription_is_read(wire, resolver):  # noqa: F811
    resolver["stt"] = openai_stt("gpt-4o-transcribe")
    wire["responses"]["/audio/transcriptions"] = (200, [{"text": "from a list"}])
    out = run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert out["text"] == "from a list"


@pytest.mark.parametrize("detail", [[{"msg": "bad"}], [], "plain"])
def test_elevenlabs_error_details_in_any_shape_do_not_crash(detail):
    resp = httpx.Response(422, json={"detail": detail})
    err = el._api_error(resp, "Speech-to-text")
    assert isinstance(err, el.ElevenLabsError) and err.status == 422


def test_elevenlabs_error_body_that_is_a_list_does_not_crash():
    err = el._api_error(httpx.Response(500, json=[{"detail": "x"}]), "Speech-to-text")
    assert isinstance(err, el.ElevenLabsError) and err.status == 500
