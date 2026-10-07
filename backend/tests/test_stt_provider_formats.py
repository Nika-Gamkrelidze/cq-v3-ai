"""Each speech-to-text provider gets audio in a container it can actually read.

The workspace's `audio_format` setting (services/transcription.py) was designed around ElevenLabs
Scribe, which reads nearly anything — so `original`, or a conversion that fell back to the upload,
was always safe. Google's Interactions API takes an enum of audio MIME types and OpenAI a fixed
list, so the same setting could hand them a phone recorder's .amr, a .wma or a video, which they
refuse. `audio.for_provider` converts those to the adapter's own fallback format first.
"""
import json

import pytest

from app.services import audio as audio_mod
from app.services import voice
from app.services.providers import stt_gemini, stt_openai, voice_base

from tests.test_voice import ORIGINAL, gemini_stt, openai_stt, resolver, run, wire  # noqa: F401

WAV = voice_base.silence_wav(0.5)


@pytest.mark.parametrize("ctype, name, expect", [
    ("audio/x-wav", "a.wav", "audio/wav"), ("audio/wave", "a", "audio/wav"),
    ("audio/x-m4a", "a.m4a", "audio/m4a"), ("audio/mp4", "a.m4a", "audio/m4a"),
    ("audio/mpeg; charset=binary", "a.mp3", "audio/mpeg"), ("AUDIO/FLAC", "x", "audio/flac"),
    ("application/octet-stream", "rec.amr", "audio/amr"),
    ("", "clip.MOV", "video/quicktime"), ("", "noext", "application/octet-stream"),
])
def test_mime_types_are_canonical(ctype, name, expect):
    assert audio_mod.mime_of(ctype, name) == expect


def test_an_accepted_container_is_sent_as_chosen():
    out = run(audio_mod.for_provider(WAV, "call.wav", "audio/x-wav", "original",
                                     accepts=stt_gemini.INTERACTIONS_AUDIO, fallback="flac_16k"))
    assert out.data is WAV and out.content_type == "audio/wav"


def test_an_unreadable_container_is_converted_to_the_providers_format():
    # the bytes are a real WAV, labelled as a QuickTime video the way a browser would label a .mov
    out = run(audio_mod.for_provider(WAV, "clip.mov", "video/quicktime", "original",
                                     accepts=stt_gemini.INTERACTIONS_AUDIO, fallback="flac_16k"))
    assert out.content_type == "audio/flac" and out.data[:4] == b"fLaC"


def test_a_provider_that_reads_anything_keeps_the_original():
    out = run(audio_mod.for_provider(WAV, "rec.amr", "audio/amr", "original",
                                     accepts=None, fallback="flac_16k"))
    assert out.data is WAV and out.content_type == "audio/amr"


def test_nothing_better_possible_sends_the_upload_as_it_is():
    out = run(audio_mod.for_provider(b"not audio", "x.amr", "audio/amr", "original",
                                     accepts=stt_gemini.INTERACTIONS_AUDIO, fallback="flac_16k"))
    assert out.data == b"not audio" and out.content_type == "audio/amr"


def _sent_audio(wire, path_key):
    for req in wire["sent"]:
        if path_key in req["path"]:
            return req
    raise AssertionError(f"no request to {path_key}")


def test_gemini_transcribe_converts_a_video_upload_before_sending(wire, resolver):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    wire["responses"] = {"/interactions": (200, {"status": "completed", "steps": [
        {"type": "model_output", "content": [{"type": "text", "text": "ok"}]}]}),
        **wire["responses"]}
    out = run(voice.transcribe("t-1", WAV, "clip.mov", "video/quicktime",
                               transcription={**ORIGINAL, "audio_format": "original"}))
    assert out["text"] == "ok"
    [part] = json.loads(_sent_audio(wire, "/interactions")["body"])["input"]
    assert part["mime_type"] == "audio/flac"


def test_gemini_transcribe_sends_the_default_mp3_as_audio_mpeg(wire, resolver):  # noqa: F811
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    wire["responses"] = {"/interactions": (200, {"status": "completed", "steps": [
        {"type": "model_output", "content": [{"type": "text", "text": "ok"}]}]}),
        **wire["responses"]}
    run(voice.transcribe("t-1", WAV, "call.wav", "audio/wav",
                         transcription={**ORIGINAL, "audio_format": "mp3_16k"}))
    [part] = json.loads(_sent_audio(wire, "/interactions")["body"])["input"]
    assert part["mime_type"] == "audio/mpeg"
    assert part["mime_type"] in stt_gemini.INTERACTIONS_AUDIO


def test_gemini_chat_model_gets_m4a_converted(wire, resolver):  # noqa: F811
    """generateContent's documented list has no m4a; the Interactions enum does."""
    resolver["stt"] = gemini_stt("gemini-2.5-flash")
    reply = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(
        {"language_code": "en", "segments": [{"speaker": "speaker_0", "start": 0, "end": 1,
                                              "text": "ok"}]})}]}}]}
    wire["responses"] = {":generateContent": (200, reply), **wire["responses"]}
    run(voice.transcribe("t-1", WAV, "memo.m4a", "audio/x-m4a",
                         transcription={**ORIGINAL, "audio_format": "original"}))
    body = json.loads(_sent_audio(wire, ":generateContent")["body"])
    inline = body["contents"][0]["parts"][0]["inlineData"]
    assert inline["mimeType"] == "audio/flac"


def test_openai_converts_what_it_cannot_read(wire, resolver):  # noqa: F811
    resolver["stt"] = openai_stt("gpt-4o-transcribe")
    run(voice.transcribe("t-1", WAV, "rec.amr", "audio/amr",
                         transcription={**ORIGINAL, "audio_format": "original"}))
    req = _sent_audio(wire, "/audio/transcriptions")
    assert b'filename="rec.flac"' in req["body"] or b"audio/flac" in req["body"]


def test_every_fallback_is_a_format_its_provider_accepts():
    for accepts, fallback in ((stt_gemini.INTERACTIONS_AUDIO, stt_gemini.FALLBACK_FORMAT),
                              (stt_gemini.GENERATE_AUDIO, stt_gemini.FALLBACK_FORMAT),
                              (stt_openai.ACCEPTED_AUDIO, stt_openai.FALLBACK_FORMAT)):
        spec = audio_mod.STT_FORMATS[fallback]
        assert audio_mod.mime_of(spec.content_type, "x" + spec.ext) in accepts
