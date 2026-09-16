"""Voice calls are metered into `llm_usage`, beside the text calls of the same recording.

Speech-to-text, text-to-speech and the local voice-tone sidecar never go through
`llm.call_tool`, so before this they cost nothing on the usage page — the one line item a
transcription-heavy workspace spends most on. What is pinned here:

1. Every STT adapter returns a `usage` block in the units its provider bills by: Scribe the
   audio length only, OpenAI tokens (gpt-4o-transcribe) or seconds (whisper-1), Gemini tokens on
   both of its APIs (cached input split out, thinking counted as output — the text adapter's
   rule).
2. `voice.transcribe` / `TTS.synthesize` record one row per provider call, failed ones too, and
   hand back the provider's own exception unchanged — accounting never replaces an error.
3. The sidecar records as provider `local` only when a request was actually made.

`llm._record` is captured, so the real `llm.record_usage` runs (its signature is part of what
is tested) and nothing is written anywhere. The wire/resolver fixtures are test_voice.py's.
"""
import dataclasses

import httpx
import pytest

from app.services import llm, sentiment, voice
from app.services.ai_resolve import Resolved
from app.services.providers import voice_base
from test_voice import (_first, _gemini_reply, _interaction, _word, gemini_stt,  # noqa: F401
                        openai_stt, openai_tts, resolver, run, wire, ORIGINAL)


@pytest.fixture
def recorded(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(llm, "_record", lambda **kw: rows.append(kw))
    return rows


def _no_tokens(usage):
    return usage is None or all(usage.get(k) is None for k in
                                ("input_tokens", "output_tokens", "cache_read_tokens",
                                 "cache_creation_tokens"))


# ---------------------------------------------------------------------------
# 1. The usage block, per provider
# ---------------------------------------------------------------------------
def test_scribe_reports_no_tokens_and_the_audio_length(wire, resolver, recorded):
    out = run(voice.transcribe("t-1", b"raw", "call.wav", "audio/wav", transcription=ORIGINAL))
    assert out["usage"] == {"input_tokens": None, "output_tokens": None,
                            "cache_read_tokens": None, "cache_creation_tokens": None,
                            "audio_seconds": 0.3}
    [row] = recorded
    assert (row["feature"], row["capability"], row["client_id"], row["model"], row["ok"]) == \
        ("transcribe", "stt", "t-1", "scribe_v1", True)
    assert (row["provider"], row["byo"], row["connection_id"]) == ("elevenlabs", False, None)
    assert row["audio_seconds"] == 0.3 and _no_tokens(row["usage"])
    assert row["characters"] is None and isinstance(row["latency_ms"], int)


def test_whisper_bills_by_duration(wire, resolver, recorded):
    resolver["stt"] = openai_stt()
    wire["responses"]["/audio/transcriptions"] = (200, {
        "text": "hello", "language": "english", "duration": 12.5,
        "usage": {"type": "duration", "seconds": 13},
        "words": [{"word": "hello", "start": 0.0, "end": 0.4}]})
    out = run(voice.transcribe("t-1", b"raw", "call.wav", "audio/wav", transcription=ORIGINAL))
    assert out["usage"]["audio_seconds"] == 13.0 and _no_tokens(out["usage"])
    [row] = recorded
    assert (row["provider"], row["byo"], row["connection_id"], row["model"]) == \
        ("openai", True, "c-1", "whisper-1")
    assert row["audio_seconds"] == 13.0


def test_whisper_without_a_usage_block_falls_back_to_duration_then_words(wire, resolver,
                                                                        recorded):
    resolver["stt"] = openai_stt()
    wire["responses"]["/audio/transcriptions"] = (200, {"text": "hi", "duration": 7.25})
    assert run(voice.transcribe("t-1", b"raw", "a.wav", "audio/wav",
                                transcription=ORIGINAL))["usage"]["audio_seconds"] == 7.25
    # No usage and no duration: the last word's end is the length.
    wire["responses"]["/audio/transcriptions"] = (200, {
        "text": "hello", "words": [{"word": "hello", "start": 0.0, "end": 0.4}]})
    assert run(voice.transcribe("t-1", b"raw", "a.wav", "audio/wav",
                                transcription=ORIGINAL))["usage"]["audio_seconds"] == 0.4


def test_gpt4o_transcribe_bills_by_tokens(wire, resolver, recorded):
    resolver["stt"] = openai_stt("gpt-4o-transcribe")
    wire["responses"]["/audio/transcriptions"] = (200, {
        "text": "hi", "usage": {"type": "tokens", "input_tokens": 120, "output_tokens": 8,
                                "total_tokens": 128,
                                "input_token_details": {"audio_tokens": 110, "text_tokens": 10}}})
    out = run(voice.transcribe("t-1", b"raw", "call.wav", "audio/wav", transcription=ORIGINAL))
    assert out["usage"] == {"input_tokens": 120, "output_tokens": 8, "cache_read_tokens": None,
                            "cache_creation_tokens": None, "audio_seconds": None}
    [row] = recorded
    assert (row["usage"]["input_tokens"], row["usage"]["output_tokens"]) == (120, 8)
    assert row["audio_seconds"] is None and row["model"] == "gpt-4o-transcribe"


def test_gemini_generate_content_maps_usage_metadata_like_the_text_adapter(wire, resolver,
                                                                          recorded):
    resolver["stt"] = gemini_stt()
    status, body = _gemini_reply({"language_code": "ka", "segments": [
        {"speaker": "speaker_0", "start": 0.0, "end": 2.5, "text": "გამარჯობა"},
        {"speaker": "speaker_1", "start": 2.6, "end": 5.0, "text": "36 თვემდე"}]})
    body["usageMetadata"] = {"promptTokenCount": 900, "cachedContentTokenCount": 100,
                             "candidatesTokenCount": 40, "thoughtsTokenCount": 5,
                             "totalTokenCount": 945}
    _first(wire, ":generateContent", (status, body))
    out = run(voice.transcribe("t-1", b"RIFFraw", "call.wav", "audio/wav",
                               transcription=ORIGINAL))
    assert out["usage"] == {"input_tokens": 800, "output_tokens": 45, "cache_read_tokens": 100,
                            "cache_creation_tokens": None, "audio_seconds": 5.0}
    [row] = recorded
    assert (row["provider"], row["connection_id"], row["model"]) == \
        ("gemini", "c-3", "gemini-2.5-flash")
    assert row["usage"]["input_tokens"] == 800 and row["audio_seconds"] == 5.0


def test_gemini_transcribe_reads_the_interaction_usage(wire, resolver, recorded):
    resolver["stt"] = gemini_stt(None)
    words = [_word("გამარჯობა", "0.100s", "0.900s", "spk_1"),
             _word("36", "1.200s", "2.100s", "spk_2")]
    _first(wire, "/interactions", _interaction("გამარჯობა 36", words, usage={
        "total_input_tokens": 700, "total_output_tokens": 30, "total_tokens": 730}))
    out = run(voice.transcribe("t-1", b"RIFFraw", "call.wav", "audio/wav",
                               transcription=ORIGINAL))
    assert out["usage"] == {"input_tokens": 700, "output_tokens": 30, "cache_read_tokens": None,
                            "cache_creation_tokens": None, "audio_seconds": 2.1}
    [row] = recorded
    assert row["model"] == "gemini-3.5-transcribe" and row["audio_seconds"] == 2.1


@pytest.mark.parametrize("extra, tokens", [
    ({"usage": {"input_tokens": 50, "output_tokens": 4}}, (50, 4, None)),
    ({"usage": {"total_input_tokens": 50, "total_cached_tokens": 20,
                "total_output_tokens": 4, "total_thought_tokens": 1}}, (30, 5, 20)),
    ({"usage": "not an object"}, (None, None, None)),
    ({}, (None, None, None)),
])
def test_gemini_transcribe_usage_shapes_are_read_defensively(wire, resolver, recorded, extra,
                                                            tokens):
    """The Interactions usage shape is unconfirmed against a live response: every plausible
    spelling is read, and an unknown one costs the row its tokens, never the transcript."""
    resolver["stt"] = gemini_stt(None)
    _first(wire, "/interactions", _interaction("hi", [_word("hi", "0.1s", "0.6s")], **extra))
    out = run(voice.transcribe("t-1", b"RIFFraw", "call.wav", "audio/wav",
                               transcription=ORIGINAL))
    u = out["usage"]
    assert (u["input_tokens"], u["output_tokens"], u["cache_read_tokens"]) == tokens
    assert out["text"] == "hi" and u["audio_seconds"] == 0.6


# ---------------------------------------------------------------------------
# 2. The seam: failures, stubs, a broken ledger
# ---------------------------------------------------------------------------
class _StubSTT:
    id = "stub"
    default_model = "stub-1"

    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def model(self, res):
        return "stub-1"

    async def transcribe(self, res, audio, filename, content_type, **kw):
        if self.error is not None:
            raise self.error
        return self.result

    async def probe(self, res):
        return {"ok": True, "detail": ""}


def _stub(monkeypatch, resolver, adapter):
    monkeypatch.setitem(voice.STT_ADAPTERS, "stub", adapter)
    resolver["stt"] = Resolved("stt", "stub", None, "k", None)


def test_a_failed_transcription_is_recorded_and_the_original_error_re_raised(
        monkeypatch, resolver, recorded):
    err = voice_base.VoiceError("provider said no", status=402, code="quota")
    _stub(monkeypatch, resolver, _StubSTT(error=err))
    with pytest.raises(voice_base.VoiceError) as caught:
        run(voice.transcribe("t-1", b"raw", "a.wav", "audio/wav", transcription=ORIGINAL))
    assert caught.value is err
    [row] = recorded
    assert (row["feature"], row["capability"], row["ok"], row["model"], row["provider"]) == \
        ("transcribe", "stt", False, "stub-1", "stub")
    assert row["usage"] is None and row["audio_seconds"] is None


def test_a_rejected_openai_key_is_recorded_as_failed(wire, resolver, recorded):
    resolver["stt"] = openai_stt()
    wire["responses"]["/audio/transcriptions"] = (401, {"error": {"code": "invalid_api_key"}})
    with pytest.raises(voice_base.VoiceError) as caught:
        run(voice.transcribe("t-1", b"raw", "a.wav", "audio/wav", transcription=ORIGINAL))
    assert caught.value.code == "invalid_key"
    [row] = recorded
    assert row["ok"] is False and row["provider"] == "openai" and row["byo"] is True


def test_an_adapter_without_a_usage_block_still_records(monkeypatch, resolver, recorded):
    _stub(monkeypatch, resolver, _StubSTT(result={
        "text": "hello there", "words": [{"text": "hello", "start": 0.0, "end": 1.5}]}))
    out = run(voice.transcribe("t-1", b"raw", "a.wav", "audio/wav", transcription=ORIGINAL))
    assert out["text"] == "hello there" and out["usage"]["audio_seconds"] == 1.5
    [row] = recorded
    assert row["ok"] is True and row["audio_seconds"] == 1.5 and _no_tokens(row["usage"])


def test_an_adapter_returning_nothing_at_all_still_records(monkeypatch, resolver, recorded):
    _stub(monkeypatch, resolver, _StubSTT(result=None))
    out = run(voice.transcribe("t-1", b"raw", "a.wav", "audio/wav", transcription=ORIGINAL))
    assert out["text"] == "" and out["usage"]["audio_seconds"] is None
    assert [r["ok"] for r in recorded] == [True]


def test_a_broken_ledger_never_costs_the_transcript(wire, resolver, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(llm, "_record", _boom)
    out = run(voice.transcribe("t-1", b"raw", "a.wav", "audio/wav", transcription=ORIGINAL))
    assert out["text"] == "ok"


def test_tts_records_the_characters_it_synthesised(wire, resolver, recorded):
    audio = run(voice.synthesize("t-1", "hello there", language_code="en"))
    assert audio == b"ID3fake-el"
    [row] = recorded
    assert (row["feature"], row["capability"], row["client_id"], row["ok"]) == \
        ("tts", "tts", "t-1", True)
    assert (row["provider"], row["model"], row["characters"]) == \
        ("elevenlabs", "eleven_multilingual_v2", 11)
    assert _no_tokens(row["usage"]) and row["audio_seconds"] is None


def test_georgian_tts_records_the_model_actually_sent(wire, resolver, recorded):
    run(voice.synthesize(None, "დიახ", language_code="ka"))
    [row] = recorded
    assert (row["model"], row["characters"], row["client_id"]) == ("eleven_v3", 4, None)


def test_a_failed_tts_is_recorded_and_re_raised(wire, resolver, recorded):
    resolver["tts"] = openai_tts()
    wire["responses"]["/audio/speech"] = (429, {"error": {"message": "slow down"}})
    with pytest.raises(voice_base.VoiceError) as caught:
        run(voice.synthesize("t-2", "ok"))
    assert caught.value.code == "quota"
    [row] = recorded
    assert (row["ok"], row["provider"], row["connection_id"], row["characters"]) == \
        (False, "openai", "c-2", 2)


def test_tts_with_no_voice_makes_no_call_and_records_nothing(wire, resolver, recorded):
    ctx = run(voice.tts("t-1"))
    plan = run(ctx.plan())
    with pytest.raises(voice_base.VoiceError):
        run(ctx.synthesize("hi", dataclasses.replace(plan, voice_id=None)))
    assert recorded == []


# ---------------------------------------------------------------------------
# 3. The voice-tone sidecar
# ---------------------------------------------------------------------------
def _sidecar(monkeypatch, routes: dict, *, url="http://sentiment:8080"):
    """`routes`: path → dict payload | (status, payload) | an exception to raise."""
    async def _cfg():
        return {"sentiment_url": url}
    monkeypatch.setattr(sentiment.settings_store, "get_effective", _cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        reply = routes.get(request.url.path, (404, {}))
        if isinstance(reply, Exception):
            raise reply
        status, payload = reply if isinstance(reply, tuple) else (200, reply)
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client(*a, **kw):
        kw.pop("transport", None)
        return real_client(*a, transport=transport, **kw)

    monkeypatch.setattr(sentiment.httpx, "AsyncClient", client)


async def test_prosody_records_a_local_zero_token_row(monkeypatch, recorded):
    _sidecar(monkeypatch, {"/prosody": {"label": "calm", "arousal": 0.2, "valence": 0.7,
                                        "model": "superb/wav2vec2-base-superb-er"}})
    out = await sentiment.prosody(b"audio", "a.wav", "audio/wav", client_id="t-1")
    assert out["label"] == "calm"
    [row] = recorded
    assert (row["feature"], row["capability"], row["client_id"], row["ok"]) == \
        ("voice_tone", "voice_tone", "t-1", True)
    assert (row["provider"], row["byo"], row["connection_id"], row["model"]) == \
        ("local", False, None, "superb/wav2vec2-base-superb-er")
    assert _no_tokens(row["usage"])


async def test_prosody_unreachable_is_a_failed_row_and_still_none(monkeypatch, recorded):
    _sidecar(monkeypatch, {"/prosody": httpx.ConnectError("refused")})
    assert await sentiment.prosody(b"audio") is None
    [row] = recorded
    assert (row["ok"], row["model"], row["provider"], row["client_id"]) == \
        (False, "cq-sentiment", "local", None)


async def test_prosody_not_configured_records_nothing(monkeypatch, recorded):
    _sidecar(monkeypatch, {}, url="")
    assert await sentiment.prosody(b"audio") is None
    assert recorded == []


async def test_prosody_segments_records_the_audio_it_covered(monkeypatch, recorded):
    _sidecar(monkeypatch, {"/prosody/segments": {"model": "m-1", "segments": [
        {"i": 0, "label": "angry", "confidence": 0.9}, {"i": 1, "label": "calm"}]}})
    items, status = await sentiment.prosody_segments(
        b"audio", [{"i": 0, "start": 0.0, "end": 4.0}, {"i": 1, "start": 4.2, "end": 9.5}],
        client_id="t-1")
    assert status == "ok" and [i["label"] for i in items] == ["angry", "calm"]
    [row] = recorded
    assert (row["ok"], row["model"], row["audio_seconds"], row["client_id"]) == \
        (True, "m-1", 9.5, "t-1")


async def test_prosody_segments_failure_keeps_its_status_and_records_failed(monkeypatch,
                                                                            recorded):
    _sidecar(monkeypatch, {"/prosody/segments": (500, {}),
                           "/health": {"loaded": False, "warm_error": None}})
    items, status = await sentiment.prosody_segments(
        b"audio", [{"i": 0, "start": 0.0, "end": 3.0}])
    assert (items, status) == (None, "warming")
    [row] = recorded
    assert (row["ok"], row["audio_seconds"], row["model"]) == (False, 3.0, "cq-sentiment")


@pytest.mark.parametrize("url, audio, segs, status", [
    ("", b"audio", [{"i": 0, "start": 0.0, "end": 1.0}], "disabled"),
    ("http://sentiment:8080", b"", [{"i": 0, "start": 0.0, "end": 1.0}], "no_audio"),
    ("http://sentiment:8080", b"audio", [{"i": 0, "start": None, "end": None}], "no_timestamps"),
])
async def test_prosody_segments_without_a_request_records_nothing(monkeypatch, recorded, url,
                                                                  audio, segs, status):
    _sidecar(monkeypatch, {}, url=url)
    assert await sentiment.prosody_segments(audio, segs) == (None, status)
    assert recorded == []


def test_voice_base_stt_usage_ignores_junk_timings():
    words = [{"end": "n/a"}, {"end": None}, {"end": True}, {"end": float("nan")}, "x",
             {"end": 2}, {"end": 1.5}]
    assert voice_base.stt_usage(words=words)["audio_seconds"] == 2.0
    assert voice_base.stt_usage(audio_seconds=-1, words=[])["audio_seconds"] is None
    assert voice_base.stt_usage(input_tokens=True)["input_tokens"] is None
