"""The voice seam (`services/voice.py`) and the four adapters behind it.

Three claims are pinned here, and the first is the one that matters most:

1. With the resolver answering the LEGACY layer (what an empty registry resolves to), the
   request that leaves the process for ElevenLabs is byte for byte what it was before the seam
   existed — `model_id` / `diarize` / `tag_audio_events`, plus `language_code` / `keyterms`
   only when the settings set them; text + model (+ language_code where the model takes it)
   for TTS, the Georgian path still eleven_v3 + Laura with no language code.
2. A tenant resolved to provider `openai` reaches the OpenAI adapter, which sends the
   documented request shapes: multipart `/audio/transcriptions` with `language`, JSON
   `/audio/speech` returning the bytes. There are NO provider keys in this tree, so these are
   the only checks the OpenAI adapters get until an operator presses "Test connection".
3. The seam degrades rather than crashes: an unknown provider, a foreign model id, a rejected
   key and a dead network are each one classified `VoiceError` (or a `{ok: False}` probe).

Every request is captured with `httpx.MockTransport`, installed through `voice_base._transport`
— the hook both `elevenlabs._request` and `voice_base.http_request` read. The resolver is
stubbed by module attribute (the registry's chain is its own test's business; this file needs
only its OUTPUT), and `audio_format="original"` keeps ffmpeg out of the multipart assertions.
No database: nothing here touches the app's pool.
"""
import asyncio
import base64
import json
import re

import httpx
import pytest

from app.services import ai_resolve, segments, settings_store, voice
from app.services import transcription as tr
from app.services.ai_resolve import Resolved
from app.services.providers import tts_elevenlabs, voice_base

RACHEL = "21m00Tcm4TlvDq8ikWAM"
LAURA = tts_elevenlabs.GEORGIAN_VOICE
ORIGINAL = {"audio_format": "original"}     # no ffmpeg; every other setting on its default


def run(coro):
    """Drive one coroutine on its own loop — the house pattern (see conftest.sql)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Resolved values — what the registry's chain would hand the seam
# ---------------------------------------------------------------------------
def legacy_stt(model="scribe_v1"):
    return Resolved("stt", "elevenlabs", model, "el-key", None)


def legacy_tts(settings=None):
    return Resolved("tts", "elevenlabs", "eleven_multilingual_v2", "el-key", None,
                    settings={"voice_id": RACHEL} if settings is None else settings)


def openai_stt(model="whisper-1", base_url=None):
    return Resolved("stt", "openai", model, "sk-test", base_url, byo=True,
                    connection_id="c-1", source="byo")


def openai_tts(settings=None):
    return Resolved("tts", "openai", "tts-1", "sk-test", None,
                    settings={"voice_id": "nova"} if settings is None else settings,
                    connection_id="c-2", source="assigned")


# The ElevenLabs model catalogue as GET /v1/models returns it (languages as objects).
def _raw(model_id, *, style=True, limit=10000, langs=("en",)):
    return {"model_id": model_id, "name": model_id, "description": "",
            "can_do_text_to_speech": True, "can_use_style": style,
            "can_use_speaker_boost": True, "maximum_text_length_per_request": limit,
            "languages": [{"language_id": c, "name": c} for c in langs]}


EL_MODELS = [_raw("eleven_multilingual_v2", langs=("en", "ru")),
             _raw("eleven_v3", style=False, limit=5000, langs=("en", "ka", "ru")),
             _raw("eleven_flash_v2_5", limit=40000)]


def form_fields(req: dict) -> dict:
    """The multipart body as {field: [values]} — `<file>` for a file part. A list, because
    `keyterms` is a REPEATED field."""
    boundary = re.search(r"boundary=([^;]+)", req["content_type"]).group(1).encode()
    out: dict = {}
    for part in req["body"].split(b"--" + boundary):
        if b"Content-Disposition" not in part:
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        out.setdefault(name, []).append(
            "<file>" if b'filename="' in head else value.rstrip(b"\r\n").decode())
    return out


@pytest.fixture
def wire(monkeypatch):
    """Every request the adapters make, with canned responses by path substring."""
    state = {"sent": [], "responses": {}, "fail": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"] is not None:
            raise state["fail"]
        state["sent"].append({
            "method": request.method, "url": str(request.url), "path": request.url.path,
            "headers": dict(request.headers), "body": request.read(),
            "content_type": request.headers.get("content-type", "")})
        for key, (status, payload) in state["responses"].items():
            if key in request.url.path:
                if isinstance(payload, bytes):
                    return httpx.Response(status, content=payload,
                                          headers={"content-type": "audio/mpeg"})
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"error": {"message": f"no canned {request.url.path}"}})

    monkeypatch.setattr(voice_base, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(tts_elevenlabs, "_models_cache", {})   # cold catalogue every test
    state["responses"].update({
        "/speech-to-text": (200, {"text": "ok", "language_code": "ka",
                                  "words": [{"text": "ok", "start": 0.0, "end": 0.3,
                                             "speaker_id": "speaker_0", "type": "word"}]}),
        "/models": (200, EL_MODELS),
        "/voices": (200, {"voices": [{"voice_id": RACHEL, "name": "Rachel",
                                      "category": "premade", "preview_url": "u"}]}),
        "/text-to-speech/": (200, b"ID3fake-el"),
        "/audio/transcriptions": (200, {"text": "hello", "language": "english",
                                        "words": [{"word": "hello", "start": 0.0, "end": 0.4}]}),
        "/audio/speech": (200, b"ID3fake-openai"),
    })
    return state


@pytest.fixture
def resolver(monkeypatch):
    """The resolver's OUTPUT per capability, and who asked for what."""
    state = {"stt": legacy_stt(), "tts": legacy_tts(), "calls": []}

    async def _resolve(client_id, capability, *, api_key=None, model=None):
        state["calls"].append((client_id, capability))
        return state[capability]

    async def _effective():
        return {"elevenlabs_api_key": "el-key", "tts_voice_id": RACHEL,
                "tts_model": "eleven_multilingual_v2", "stt_model": "scribe_v1"}

    monkeypatch.setattr(ai_resolve, "resolve", _resolve)
    monkeypatch.setattr(settings_store, "get_effective", _effective)
    return state


# ---------------------------------------------------------------------------
# 1. ElevenLabs through the seam: the same requests as before
# ---------------------------------------------------------------------------
def test_legacy_stt_with_no_settings_sends_exactly_what_it_always_did(wire, resolver):
    out = run(voice.transcribe(None, b"raw", "call.wav", "audio/wav", transcription=ORIGINAL))
    [req] = wire["sent"]
    assert req["url"] == "https://api.elevenlabs.io/v1/speech-to-text"
    assert req["headers"]["xi-api-key"] == "el-key"
    assert form_fields(req) == {"model_id": ["scribe_v1"], "diarize": ["true"],
                                "tag_audio_events": ["true"], "file": ["<file>"]}
    assert (out["text"], out["language_code"]) == ("ok", "ka")
    assert out["words"][0]["speaker_id"] == "speaker_0"
    assert (out["provider"], out["model"], out["source"]) == ("elevenlabs", "scribe_v1", "legacy")
    assert resolver["calls"] == [(None, "stt")]


def test_legacy_stt_sends_language_keyterms_and_diarize_off_when_set(wire, resolver):
    run(voice.transcribe("t-1", b"raw", "call.wav", "audio/wav", transcription={
        **ORIGINAL, "language_code": "ka", "diarize": False,
        "keyterms": ["თვე", "policy number"]}))
    fields = form_fields(wire["sent"][0])
    assert fields["language_code"] == ["ka"]
    assert fields["diarize"] == ["false"]
    assert fields["keyterms"] == ["თვე", "policy number"]     # one part per term
    assert resolver["calls"] == [("t-1", "stt")]


def test_settings_are_resolved_for_the_tenant_when_the_caller_passes_none(wire, resolver,
                                                                           monkeypatch):
    asked = []

    async def _resolve_settings(client_id=None, per_file=None):
        asked.append(client_id)
        return {**tr.CODE_DEFAULTS, **ORIGINAL, "language_code": "ru"}

    monkeypatch.setattr(tr, "resolve", _resolve_settings)
    run(voice.transcribe("t-9", b"raw", "call.wav", "audio/wav"))
    assert asked == ["t-9"]
    assert form_fields(wire["sent"][0])["language_code"] == ["ru"]


def test_georgian_tts_is_v3_plus_laura_with_no_language_code(wire, resolver):
    audio = run(voice.synthesize(None, "დიახ", language_code="ka",
                                 voice_settings={"stability": 0.3, "style": 0.4, "speed": 1.1}))
    assert audio == b"ID3fake-el"
    tts_req = [r for r in wire["sent"] if "/text-to-speech/" in r["path"]][0]
    assert tts_req["path"].endswith(f"/text-to-speech/{LAURA}")
    assert "output_format=mp3_44100_128" in tts_req["url"]
    assert json.loads(tts_req["body"]) == {"text": "დიახ", "model_id": "eleven_v3",
                                           "voice_settings": {"stability": 0.5}}


def test_english_tts_sends_the_code_on_multilingual_v2_with_the_default_voice(wire, resolver):
    run(voice.synthesize(None, "hi", language_code="en"))
    tts_req = [r for r in wire["sent"] if "/text-to-speech/" in r["path"]][0]
    assert tts_req["path"].endswith(f"/text-to-speech/{RACHEL}")
    assert json.loads(tts_req["body"]) == {"text": "hi", "model_id": "eleven_multilingual_v2",
                                           "language_code": "en"}


def test_no_language_uses_the_connections_model_and_sends_no_code(wire, resolver):
    resolver["tts"] = Resolved("tts", "elevenlabs", "eleven_flash_v2_5", "el-key", None,
                               settings={"voice_id": RACHEL})
    run(voice.synthesize(None, "hi"))
    tts_req = [r for r in wire["sent"] if "/text-to-speech/" in r["path"]][0]
    assert json.loads(tts_req["body"]) == {"text": "hi", "model_id": "eleven_flash_v2_5"}


def test_default_voice_falls_back_to_the_admin_setting_for_elevenlabs_only(wire, resolver):
    resolver["tts"] = legacy_tts(settings={})                   # a connection with no voice
    ctx = run(voice.tts(None))
    assert ctx.provider == "elevenlabs"
    assert run(ctx.default_voice()) == RACHEL                  # the legacy tts_voice_id
    resolver["tts"] = openai_tts(settings={})
    ctx = run(voice.tts(None))
    assert run(ctx.default_voice()) == "alloy"                  # never the ElevenLabs id


def test_elevenlabs_models_and_voices_come_back_in_todays_shapes(wire, resolver):
    models = run(voice.list_models(None))
    assert [m["model_id"] for m in models] == ["eleven_multilingual_v2", "eleven_v3",
                                               "eleven_flash_v2_5"]
    v3 = models[1]
    assert v3["supports"] == {"presets": True, "style": False, "speaker_boost": True,
                              "speed": False, "language_code": "rejected"}
    assert v3["max_chars"] == 5000 and v3["languages"] == ["en", "ka", "ru"]
    voices = run(voice.list_voices(None))
    assert voices == [{"voice_id": RACHEL, "name": "Rachel", "category": "premade",
                       "preview_url": "u", "is_default": True}]
    ctx = run(voice.tts(None))
    assert run(ctx.system_voice_ids()) == {RACHEL, LAURA}


# ---------------------------------------------------------------------------
# 2. A tenant on OpenAI reaches the OpenAI adapters
# ---------------------------------------------------------------------------
def test_openai_stt_sends_multipart_with_language_prompt_and_word_timestamps(wire, resolver):
    resolver["stt"] = openai_stt()
    out = run(voice.transcribe("t-1", b"raw", "call.wav", "audio/wav", transcription={
        **ORIGINAL, "language_code": "kat", "keyterms": ["თვე", "პოლისი"]}))
    [req] = wire["sent"]
    assert req["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert req["headers"]["authorization"] == "Bearer sk-test"
    assert form_fields(req) == {
        "model": ["whisper-1"], "language": ["ka"],          # ISO-639-3 → 639-1
        "prompt": ["თვე, პოლისი"], "response_format": ["verbose_json"],
        "timestamp_granularities[]": ["word"], "file": ["<file>"]}
    assert (out["text"], out["language_code"], out["provider"]) == ("hello", "en", "openai")
    # Words without speaker ids: the timeline degrades to one speaker, it does not crash.
    assert out["words"] == [{"text": "hello", "start": 0.0, "end": 0.4, "type": "word"}]
    assert "speaker_id" not in out["words"][0]
    [seg] = segments.build_segments(out["words"])
    assert seg["speaker"] == segments.DEFAULT_SPEAKER
    assert "diarize" in out["detail"]


def test_openai_gpt4o_asks_for_plain_json_and_honours_the_base_url(wire, resolver):
    resolver["stt"] = openai_stt("gpt-4o-transcribe", base_url="https://proxy.example/v1/")
    wire["responses"]["/audio/transcriptions"] = (200, {"text": "hi"})
    out = run(voice.transcribe("t-1", b"raw", "call.wav", "audio/wav", transcription=ORIGINAL))
    [req] = wire["sent"]
    assert req["url"] == "https://proxy.example/v1/audio/transcriptions"
    fields = form_fields(req)
    assert fields["response_format"] == ["json"]
    assert "timestamp_granularities[]" not in fields and "language" not in fields
    assert out["words"] == [] and out["language_code"] is None and out["text"] == "hi"


def test_openai_tts_sends_the_json_body_and_returns_the_bytes(wire, resolver):
    resolver["tts"] = openai_tts()
    audio = run(voice.synthesize("t-1", "ok", language_code="en",
                                 voice_settings={"speed": 1.1, "style": 0.4, "stability": 0.3}))
    assert audio == b"ID3fake-openai"
    [req] = wire["sent"]
    assert req["url"] == "https://api.openai.com/v1/audio/speech"
    assert req["headers"]["authorization"] == "Bearer sk-test"
    # style/stability have no OpenAI control (caps say so) and are shaped away; speed stays;
    # there is no language field on this API.
    assert json.loads(req["body"]) == {"model": "tts-1", "input": "ok", "voice": "nova",
                                       "response_format": "mp3", "speed": 1.1}
    assert resolver["calls"] == [("t-1", "tts")]


def test_openai_tts_catalogue_says_what_the_form_may_show(wire, resolver):
    resolver["tts"] = openai_tts()
    models = run(voice.list_models("t-1"))
    assert [m["model_id"] for m in models] == ["tts-1", "tts-1-hd", "gpt-4o-mini-tts"]
    assert models[0]["supports"] == {"presets": False, "style": False, "speaker_boost": False,
                                     "speed": True, "language_code": "ignored"}
    assert models[0]["max_chars"] == 4096
    voices = run(voice.list_voices("t-1"))
    assert [v["voice_id"] for v in voices][:3] == ["alloy", "ash", "ballad"]
    assert [v["voice_id"] for v in voices if v["is_default"]] == ["nova"]
    assert all(v["preview_url"] is None for v in voices)
    assert wire["sent"] == []                                   # no network for a fixed list
    ctx = run(voice.tts("t-1"))
    assert ctx.valid_voice_id("nova") and not ctx.valid_voice_id("../../v1/dubbing")
    assert ctx.defaults_for_language("ka") == {"model": "tts-1", "voice": None, "note": ""}


# ---------------------------------------------------------------------------
# 3. Degrade, never crash
# ---------------------------------------------------------------------------
def test_a_foreign_model_id_is_replaced_by_the_adapters_default(wire, resolver):
    resolver["stt"] = openai_stt(model="scribe_v1")             # legacy stt_model showing through
    run(voice.transcribe(None, b"raw", "a.wav", "audio/wav", transcription=ORIGINAL))
    assert form_fields(wire["sent"][0])["model"] == ["whisper-1"]
    resolver["stt"] = legacy_stt(model="whisper-1")
    run(voice.transcribe(None, b"raw", "a.wav", "audio/wav", transcription=ORIGINAL))
    assert form_fields(wire["sent"][1])["model_id"] == ["scribe_v1"]


def test_an_unknown_provider_is_one_classified_error(wire, resolver):
    resolver["stt"] = Resolved("stt", "acme", None, "k", None)
    with pytest.raises(voice.VoiceError) as exc:
        run(voice.transcribe(None, b"raw", "a.wav", "audio/wav", transcription=ORIGINAL))
    assert exc.value.code == "unknown_provider"
    assert wire["sent"] == []


def test_openai_key_rejection_is_classified_and_never_echoes_the_key(wire, resolver):
    resolver["stt"] = openai_stt()
    wire["responses"]["/audio/transcriptions"] = (
        401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}})
    with pytest.raises(voice.VoiceError) as exc:
        run(voice.transcribe(None, b"raw", "a.wav", "audio/wav", transcription=ORIGINAL))
    assert exc.value.code == "invalid_key" and exc.value.status == 401
    assert "sk-test" not in str(exc.value)


def test_probes_report_rather_than_raise(wire, resolver):
    ok = run(voice.probe(openai_tts()))
    assert ok["ok"] is True and "bytes" in ok["detail"]
    assert json.loads(wire["sent"][-1]["body"])["voice"] == "nova"

    ok = run(voice.probe(legacy_stt()))
    assert ok["ok"] is True and "scribe_v1" in ok["detail"]

    wire["responses"]["/audio/speech"] = (401, {"error": {"code": "invalid_api_key"}})
    bad = run(voice.probe(openai_tts()))
    assert bad == {"ok": False, "detail": bad["detail"], "code": "invalid_key"}

    wire["fail"] = httpx.ConnectError("dns")
    dead = run(voice.probe(legacy_stt()))
    assert dead["ok"] is False and dead["code"] == "transport"
    assert run(voice.probe(Resolved("stt", "acme", None, "k", None)))["code"] == "unknown_provider"


# --------------------------------------------------------------------------- #
# Gemini speech-to-text: a multimodal model asked for a structured transcript
# --------------------------------------------------------------------------- #
from app.services.providers import stt_gemini  # noqa: E402


def gemini_stt(model="gemini-2.5-flash"):
    return Resolved("stt", "gemini", model, "AIza-test", None, connection_id="c-3",
                    source="assigned")


def _gemini_reply(payload: dict, finish: str = "STOP") -> tuple[int, dict]:
    return (200, {"candidates": [{"finishReason": finish,
                                  "content": {"parts": [{"text": json.dumps(payload)}]}}]})


def _first(wire, key, reply):
    # The fixture's "/models" key would match ":generateContent" first — ours must be checked first.
    wire["responses"] = {key: reply, **wire["responses"]}


def test_gemini_stt_inlines_the_audio_with_a_transcript_schema_and_maps_segments(wire, resolver):
    resolver["stt"] = gemini_stt()
    _first(wire, ":generateContent", _gemini_reply({
        "language_code": "ka",
        "segments": [{"speaker": "speaker_0", "start": 0.0, "end": 2.5, "text": "გამარჯობა"},
                     {"speaker": "speaker_1", "start": 2.6, "end": 5.0, "text": "36 თვემდე"}]}))
    out = run(voice.transcribe("t-1", b"RIFFraw", "call.wav", "audio/wav", transcription={
        **ORIGINAL, "language_code": "ka", "keyterms": ["თვემდე"], "diarize": True}))
    [req] = wire["sent"]
    assert req["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert req["headers"]["x-goog-api-key"] == "AIza-test"
    assert "key=" not in req["url"], "the key must travel in a header, never the URL"
    body = json.loads(req["body"])
    parts = body["contents"][0]["parts"]
    inline = parts[0]["inlineData"]
    assert inline["mimeType"] == "audio/wav"
    assert base64.b64decode(inline["data"]) == b"RIFFraw"
    text = parts[1]["text"]
    assert "Georgian" in text and "speaker_0, speaker_1" in text and "თვემდე" in text
    gc = body["generationConfig"]
    assert gc["responseMimeType"] == "application/json" and gc["temperature"] == 0
    assert gc["responseSchema"]["type"] == "OBJECT"
    assert "additionalProperties" not in json.dumps(gc["responseSchema"])
    # The mapping: one `words` entry per segment, carrying the speaker and the span.
    assert (out["text"], out["language_code"], out["provider"]) == ("გამარჯობა 36 თვემდე", "ka", "gemini")
    assert [w["speaker_id"] for w in out["words"]] == ["speaker_0", "speaker_1"]
    assert out["words"][1]["start"] == 2.6 and out["words"][1]["end"] == 5.0
    segs = segments.build_segments(out["words"])
    assert [s["speaker"] for s in segs] == ["speaker_0", "speaker_1"]
    assert "approximate" in out["detail"]


def test_gemini_stt_without_diarization_or_language_asks_for_one_speaker(wire, resolver):
    resolver["stt"] = gemini_stt()
    _first(wire, ":generateContent", _gemini_reply({"language_code": "", "segments": [
        {"speaker": "speaker_0", "start": "n/a", "end": None, "text": "hi"}]}))
    out = run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg",
                               transcription={**ORIGINAL, "diarize": False}))
    text = json.loads(wire["sent"][0]["body"])["contents"][0]["parts"][1]["text"]
    assert "Label every segment speaker_0" in text and "The audio is in" not in text
    # Unparseable timings become None (text-mode segments), never a crash or a fake number.
    assert out["words"] == [{"text": "hi", "start": None, "end": None, "type": "word",
                             "speaker_id": "speaker_0"}]
    assert out["language_code"] is None


def test_gemini_stt_uses_the_files_api_above_the_inline_limit(monkeypatch, resolver):
    """A long call on a lossless format cannot be inlined; it goes resumable-upload → wait
    ACTIVE → reference by URI → delete, and the transcript request carries fileData."""
    resolver["stt"] = gemini_stt()
    monkeypatch.setattr(stt_gemini, "INLINE_MAX_BYTES", 8)
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/upload/v1beta/files"):
            assert request.headers["x-goog-upload-command"] == "start"
            assert request.headers["x-goog-upload-header-content-type"] == "audio/flac"
            return httpx.Response(200, headers={"X-Goog-Upload-URL":
                                                "https://generativelanguage.googleapis.com/upload/session/1"})
        if "upload/session/1" in str(request.url):
            assert request.headers["x-goog-upload-command"] == "upload, finalize"
            assert request.read() == b"BIGFLACBYTES"
            return httpx.Response(200, json={"file": {"name": "files/abc", "state": "ACTIVE",
                                                      "uri": "https://generativelanguage.googleapis.com/v1beta/files/abc"}})
        if request.url.path.endswith(":generateContent"):
            body = json.loads(request.read())
            assert body["contents"][0]["parts"][0] == {"fileData": {
                "mimeType": "audio/flac",
                "fileUri": "https://generativelanguage.googleapis.com/v1beta/files/abc"}}
            return httpx.Response(200, json=_gemini_reply({"language_code": "en", "segments": [
                {"speaker": "speaker_0", "start": 0, "end": 1, "text": "long"}]})[1])
        if request.method == "DELETE" and request.url.path.endswith("/files/abc"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": f"unexpected {request.url}"})

    monkeypatch.setattr(voice_base, "_transport", httpx.MockTransport(handler))
    out = run(voice.transcribe("t-1", b"BIGFLACBYTES", "call.flac", "audio/flac",
                               transcription=ORIGINAL))
    assert out["text"] == "long"
    assert [m for m, _ in calls] == ["POST", "POST", "POST", "DELETE"]


def test_gemini_stt_truncated_transcript_is_a_clear_error(wire, resolver):
    resolver["stt"] = gemini_stt()
    _first(wire, ":generateContent", _gemini_reply({"language_code": "ka", "segments": []},
                                                    finish="MAX_TOKENS"))
    with pytest.raises(voice_base.VoiceError) as exc:
        run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert exc.value.code == "truncated"


def test_gemini_probe_reports_ok_and_names_the_model(wire, resolver):
    _first(wire, ":generateContent", _gemini_reply({"language_code": "en", "segments": []}))
    out = run(voice.probe(gemini_stt("gemini-2.5-pro")))
    assert out["ok"] is True and "gemini-2.5-pro" in out["detail"]


def test_gemini_is_offered_for_speech_to_text():
    """The dropdown is catalog-driven, so this is what puts Gemini in it."""
    from app.services.providers import catalog
    assert "gemini" in catalog.CATALOG["stt"] and "gemini" in voice.STT_ADAPTERS
    assert catalog.CATALOG["stt"]["gemini"]["allows_base_url"] is False
    # The dedicated ASR model heads the list and is what a connection without a model sends.
    assert catalog.CATALOG["stt"]["gemini"]["known_models"][0] == "gemini-3.5-transcribe" \
        == stt_gemini.DEFAULT_MODEL


# --------------------------------------------------------------------------- #
# Gemini 3.5 Transcribe: the dedicated ASR model, on the Interactions API
# --------------------------------------------------------------------------- #
def _interaction(text, annotations=None, status="completed", **extra) -> tuple[int, dict]:
    content: dict = {"type": "text", "text": text}
    if annotations is not None:
        content["annotations"] = annotations
    return (200, {"id": "interactions/1", "status": status,
                  "steps": [{"type": "model_output", "content": [content]}], **extra})


def _word(text, start, end, speaker=None) -> dict:
    w = {"type": "word_info", "text": text, "start_offset": start, "end_offset": end}
    if speaker:
        w["speaker"] = speaker
    return w


def test_gemini_transcribe_is_the_default_and_speaks_the_interactions_api(wire, resolver):
    """No model on the connection → gemini-3.5-transcribe on POST /interactions: the audio
    inline, ka → ka-GE, diarization + word timestamps, nothing stored on Google's side."""
    resolver["stt"] = gemini_stt(None)
    _first(wire, "/interactions", _interaction("გამარჯობა 36 თვემდე", [
        _word("გამარჯობა", "0.100s", "0.900s", "spk_2"),
        _word("36", "1.200s", "1.500s", "spk_1"),
        _word("თვემდე", "1.550s", "2.100s", "spk_1")]))
    out = run(voice.transcribe("t-1", b"RIFFraw", "call.wav", "audio/wav", transcription={
        **ORIGINAL, "language_code": "ka", "diarize": True}))
    [req] = wire["sent"]
    assert req["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert req["headers"]["x-goog-api-key"] == "AIza-test" and "key=" not in req["url"]
    body = json.loads(req["body"])
    assert body["model"] == "gemini-3.5-transcribe" and body["store"] is False
    [part] = body["input"]
    assert part["type"] == "audio" and part["mime_type"] == "audio/wav"
    assert base64.b64decode(part["data"]) == b"RIFFraw" and "uri" not in part
    assert body["generation_config"]["transcription_config"] == {
        "language_codes": ["ka-GE"],
        "mode": {"type": "verbatim", "diarization_mode": "speaker",
                 "timestamp_granularities": ["word"]}}
    assert (out["text"], out["language_code"], out["model"]) == \
        ("გამარჯობა 36 თვემდე", "ka", "gemini-3.5-transcribe")
    # Google's spk_N labels become speaker_N in order of first appearance; "0.100s" → 0.1.
    assert [(w["text"], w["speaker_id"], w["start"], w["end"]) for w in out["words"]] == [
        ("გამარჯობა", "speaker_0", 0.1, 0.9), ("36", "speaker_1", 1.2, 1.5),
        ("თვემდე", "speaker_1", 1.55, 2.1)]
    assert [s["speaker"] for s in segments.build_segments(out["words"])] == \
        ["speaker_0", "speaker_1"]
    assert out["detail"].startswith("Gemini Transcribe") and "Key terms" not in out["detail"]


def test_gemini_transcribe_drops_key_terms_under_diarization_and_says_so(wire, resolver):
    """Google rejects custom_vocabulary alongside diarization or timestamps; speaker
    separation wins and the detail names what was left out."""
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    _first(wire, "/interactions", _interaction("hi", []))
    out = run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription={
        **ORIGINAL, "diarize": True, "keyterms": ["თვემდე", " ", "თვემდე"]}))
    tc = json.loads(wire["sent"][0]["body"])["generation_config"]["transcription_config"]
    assert "custom_vocabulary" not in tc and tc["mode"]["diarization_mode"] == "speaker"
    assert "language_codes" not in tc                      # unset → automatic detection
    assert "Key terms were not sent" in out["detail"]


def test_gemini_transcribe_sends_key_terms_when_speakers_are_off(wire, resolver):
    """Diarization off + key terms → custom_vocabulary and no mode; the transcript comes
    back as text only, so `words` is empty and the analysis falls back to the text."""
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    _first(wire, "/interactions", _interaction("36 თვემდე. კარგი."))
    out = run(voice.transcribe("t-1", b"x", "a.m4a", "audio/mp4", transcription={
        **ORIGINAL, "diarize": False, "keyterms": ["თვემდე", "თვემდე", "ფრანშიზა"]}))
    body = json.loads(wire["sent"][0]["body"])
    assert body["input"][0]["mime_type"] == "audio/m4a"      # Google's enum, not audio/mp4
    assert body["generation_config"]["transcription_config"] == {
        "custom_vocabulary": ["თვემდე", "ფრანშიზა"]}
    assert out["words"] == [] and out["text"] == "36 თვემდე. კარგი."
    assert segments.build_segments(out["words"]) == []
    assert [s["text"] for s in segments.segments_from_text(out["text"])] == ["36 თვემდე. კარგი."]
    assert "no word timings" in out["detail"]


def test_gemini_transcribe_without_speakers_or_terms_still_asks_for_word_times(wire, resolver):
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    _first(wire, "/interactions", _interaction("hello", [_word("hello", 0.25, "n/a")]))
    out = run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg",
                               transcription={**ORIGINAL, "diarize": False}))
    tc = json.loads(wire["sent"][0]["body"])["generation_config"]["transcription_config"]
    assert tc == {"mode": {"type": "verbatim", "timestamp_granularities": ["word"]}}
    # A bare number is seconds too; an unparseable offset is None, never a fabricated 0.
    assert out["words"] == [{"text": "hello", "start": 0.25, "end": None, "type": "word",
                             "speaker_id": "speaker_0"}]
    assert out["language_code"] is None


def test_gemini_transcribe_uploads_a_real_call_through_the_files_api(monkeypatch, resolver):
    """Above the (small) inline threshold the audio is uploaded first and referenced by URI
    in the audio part, then deleted — Google's own guidance for the Transcribe model."""
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    monkeypatch.setattr(stt_gemini, "TRANSCRIBE_INLINE_MAX_BYTES", 8)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.url.path.endswith("/upload/v1beta/files"):
            return httpx.Response(200, headers={"X-Goog-Upload-URL":
                                                "https://generativelanguage.googleapis.com/upload/session/9"})
        if "upload/session/9" in str(request.url):
            assert request.read() == b"LONGCALLBYTES"
            return httpx.Response(200, json={"file": {"name": "files/xyz", "state": "ACTIVE",
                                                      "uri": "https://generativelanguage.googleapis.com/v1beta/files/xyz"}})
        if request.url.path.endswith("/interactions"):
            [part] = json.loads(request.read())["input"]
            assert part == {"type": "audio", "mime_type": "audio/flac",
                            "uri": "https://generativelanguage.googleapis.com/v1beta/files/xyz"}
            return httpx.Response(200, json=_interaction("long", [])[1])
        if request.method == "DELETE" and request.url.path.endswith("/files/xyz"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": f"unexpected {request.url}"})

    monkeypatch.setattr(voice_base, "_transport", httpx.MockTransport(handler))
    out = run(voice.transcribe("t-1", b"LONGCALLBYTES", "call.flac", "audio/flac",
                               transcription=ORIGINAL))
    assert out["text"] == "long"
    assert calls == ["POST", "POST", "POST", "DELETE"]


def test_gemini_transcribe_live_is_refused_in_words_before_any_request(wire, resolver):
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe-live")
    with pytest.raises(voice_base.VoiceError) as exc:
        run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert exc.value.code == "invalid_model" and "gemini-3.5-transcribe" in str(exc.value)
    assert wire["sent"] == []


def test_gemini_transcribe_unfinished_interaction_is_a_clear_error(wire, resolver):
    resolver["stt"] = gemini_stt("gemini-3.5-transcribe")
    _first(wire, "/interactions", _interaction("", status="failed",
                                               error={"message": "audio too long"}))
    with pytest.raises(voice_base.VoiceError) as exc:
        run(voice.transcribe("t-1", b"x", "a.mp3", "audio/mpeg", transcription=ORIGINAL))
    assert exc.value.code == "bad_response" and "audio too long" in str(exc.value)


def test_gemini_transcribe_probe_names_the_model_and_the_key_terms_rule(wire, resolver):
    _first(wire, "/interactions", _interaction("", []))
    out = run(voice.probe(gemini_stt(None)))
    assert out["ok"] is True and "gemini-3.5-transcribe" in out["detail"]
    assert "Key terms apply only when speaker separation is off" in out["detail"]
    tc = json.loads(wire["sent"][0]["body"])["generation_config"]["transcription_config"]
    assert tc == {"mode": {"type": "verbatim", "timestamp_granularities": ["word"]}}
    # A chat-model id Google does not know is what "Test connection" once showed as a bare
    # Failed: Google's own sentence is the detail, so the operator can read the reason.
    wire["responses"] = {":generateContent": (404, {"error": {
        "code": 404, "status": "NOT_FOUND",
        "message": "models/gemini-nope is not found for API version v1beta, or is not "
                   "supported for generateContent."}}), **wire["responses"]}
    bad = run(voice.probe(gemini_stt("gemini-nope")))
    assert bad["ok"] is False and "is not found" in bad["detail"]


def test_bcp47_mapping_for_the_transcribe_model():
    assert stt_gemini.bcp47("ka") == "ka-GE" and stt_gemini.bcp47("kat") == "ka-GE"
    assert stt_gemini.bcp47("EN") == "en-US" and stt_gemini.bcp47("ru_RU") == "ru-RU"
    assert stt_gemini.bcp47("es-419") == "es-419" and stt_gemini.bcp47("xx") == "xx"
    assert stt_gemini.bcp47("") is None and stt_gemini.bcp47(None) is None
