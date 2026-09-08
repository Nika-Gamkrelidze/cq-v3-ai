"""Transcription settings: the inheritance chain, the validation, and the wire.

WHY THIS FILE EXISTS. ElevenLabs Scribe heard "36 წლამდე" (36 YEARS) where the recording said
"36 თვემდე" (36 MONTHS). That transcript feeds fact-check and rubric scoring, so a misheard
word becomes a compliance verdict about an agent. The four knobs added here (language_code,
diarize, keyterms, audio_format) are the levers on that, and every one of them is a lever on
EVERY transcription in the product — which is why the most important test in this file is not
about the new features at all:

    test_a_request_with_no_settings_sends_exactly_what_it_always_did

It pins the provider call for a caller that asks for nothing. Everything else here can be
wrong and be fixed; that one going red means we changed what happens to calls nobody
configured. It is the same claim tests/test_tts_settings.py pins for text-to-speech, and it is
stubbed the same way — at `elevenlabs._request`, one level BELOW the function under test,
because the claim is about what leaves the process.

No network and no keys: the provider is stubbed, and the ffmpeg tests skip themselves when the
host has no ffmpeg rather than failing a developer's laptop.
"""
import asyncio
import io
import json
import uuid
import wave

import pytest

from app.config import settings
from app.services import audio as audio_mod
from app.services import elevenlabs
from app.services import transcription as tr

from conftest import sql

ADMIN = {"X-Admin-Token": settings.admin_token}
DEFAULTS_URL = "/admin/transcription/defaults"
CONFIG_URL = "/transcription/config"


def run(coro):
    """Drive one coroutine on its own loop — the house pattern (see conftest.sql): the app's
    asyncpg pool belongs to the TestClient's loop and must never be touched from here."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Validation — one place, and it names the field
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("patch, field", [
    ({"language_code": "georgian!"}, "language_code"),
    ({"language_code": 42}, "language_code"),
    ({"diarize": "yes"}, "diarize"),
    ({"diarize": 1}, "diarize"),
    ({"keyterms": "policy"}, "keyterms"),
    ({"keyterms": ["ok", 7]}, "keyterms"),
    ({"keyterms": ["x" * 50]}, "keyterms"),
    ({"keyterms": ["one two three four five six"]}, "keyterms"),
    ({"keyterms": ["bad<term>"]}, "keyterms"),
    ({"keyterms": ["back\\slash"]}, "keyterms"),
    ({"audio_format": "ogg_16k"}, "audio_format"),
    ({"langauge_code": "ka"}, "langauge_code"),          # a typo must not silently no-op
])
def test_invalid_settings_are_refused_and_the_message_names_the_field(patch, field):
    with pytest.raises(tr.TranscriptionSettingsError) as exc:
        tr.validate(patch)
    assert exc.value.field == field
    assert field in str(exc.value), "the operator has to be told WHICH input to fix"


def test_null_means_inherit_for_every_field_but_the_language():
    """`None` is the house "not sent" (settings_store's rule), so a null diarize or
    audio_format means "keep inheriting this one". `language_code` is the single exception:
    null there is a VALUE — "detect automatically" — which is how a workspace stops inheriting
    the operator's Georgian without inventing a sentinel string."""
    assert tr.validate({"diarize": None, "audio_format": None, "keyterms": None}) == {}
    assert tr.validate({"language_code": None}) == {"language_code": None}


def test_language_null_is_detect_and_survives_as_a_value():
    assert tr.validate({"language_code": None}) == {"language_code": None}
    assert tr.validate({"language_code": ""}) == {"language_code": None}
    assert tr.validate({"language_code": " KA "}) == {"language_code": "ka"}


def test_keyterms_are_trimmed_deduplicated_and_blank_rows_dropped():
    assert tr.validate({"keyterms": ["  თვე ", "თვე", "", "  ", "policy"]}) == {
        "keyterms": ["თვე", "policy"]}


def test_keyterms_accept_the_documented_maximum_and_refuse_one_more():
    assert len(tr.validate({"keyterms": [f"t{i}" for i in range(tr.MAX_KEYTERMS)]})["keyterms"]) \
        == tr.MAX_KEYTERMS
    with pytest.raises(tr.TranscriptionSettingsError):
        tr.validate({"keyterms": [f"t{i}" for i in range(tr.MAX_KEYTERMS + 1)]})


def test_every_known_audio_format_validates():
    for fmt in audio_mod.STT_FORMATS:
        assert tr.validate({"audio_format": fmt}) == {"audio_format": fmt}


def test_a_partial_patch_stays_partial_and_a_full_one_is_filled_in():
    assert tr.validate({"diarize": False}) == {"diarize": False}
    full = tr.validate({"diarize": False}, partial=False)
    assert set(full) == set(tr.FIELDS)
    assert full["audio_format"] == tr.CODE_DEFAULTS["audio_format"]


# ---------------------------------------------------------------------------
# The chain: code defaults <- superadmin <- tenant <- per-file
# ---------------------------------------------------------------------------
@pytest.fixture
def layers(monkeypatch):
    """Stand the two stored layers up in memory, so the chain is testable without a database
    and without the 5 s cache being part of the assertion."""
    state = {"default": {}, "tenant": {}}

    async def _default(*, force: bool = False):
        return tr.merge(dict(tr.CODE_DEFAULTS), state["default"])

    async def _tenant(client_id):
        return dict(state["tenant"]) if client_id else {}

    monkeypatch.setattr(tr, "get_default", _default)
    monkeypatch.setattr(tr, "get_tenant_override", _tenant)
    return state


def test_nothing_configured_resolves_to_the_code_defaults(layers):
    assert run(tr.resolve("client")) == {
        "language_code": None, "diarize": True, "keyterms": [],
        "audio_format": tr.CODE_DEFAULTS["audio_format"]}


def test_each_layer_overrides_only_what_it_sets(layers):
    layers["default"] = {"language_code": "ka"}
    layers["tenant"] = {"diarize": False}
    assert run(tr.resolve("client")) == {
        "language_code": "ka",                       # from the operator default
        "diarize": False,                            # from the workspace
        "keyterms": [],                              # still the code floor
        "audio_format": tr.CODE_DEFAULTS["audio_format"]}


def test_the_per_file_override_wins_over_both(layers):
    layers["default"] = {"language_code": "en", "audio_format": "mp3_16k"}
    layers["tenant"] = {"language_code": "ru", "keyterms": ["policy"]}
    got = run(tr.resolve("client", {"language_code": "ka", "audio_format": "flac_16k"}))
    assert got == {"language_code": "ka", "diarize": True,
                   "keyterms": ["policy"],           # untouched by the per-file layer
                   "audio_format": "flac_16k"}


def test_a_caller_with_no_workspace_sees_the_system_layer(layers):
    layers["default"] = {"language_code": "ka"}
    layers["tenant"] = {"diarize": False}            # must not leak to a workspace-less caller
    assert run(tr.resolve(None))["language_code"] == "ka"
    assert run(tr.resolve(None))["diarize"] is True


def test_keyterms_replace_rather_than_accumulate(layers):
    """A workspace that narrowed the operator's list must not silently get the long one back —
    the +20% surcharge and the bias itself are both things a tenant may want to shrink."""
    layers["default"] = {"keyterms": ["alpha", "beta"]}
    layers["tenant"] = {"keyterms": ["beta"]}
    assert run(tr.resolve("client"))["keyterms"] == ["beta"]


def test_the_per_file_layer_is_validated_at_resolve_time_too(layers):
    with pytest.raises(tr.TranscriptionSettingsError):
        run(tr.resolve("client", {"audio_format": "wav_8k"}))


def test_effective_for_tenant_reports_the_layer_underneath(layers):
    layers["default"] = {"language_code": "ka", "keyterms": ["თვე"]}
    layers["tenant"] = {"diarize": False}
    view = run(tr.effective_for_tenant("client"))
    assert view["is_default"] is False
    assert view["diarize"] is False and view["language_code"] == "ka"
    assert view["inherited"] == {"language_code": "ka", "diarize": True, "keyterms": ["თვე"],
                                 "audio_format": tr.CODE_DEFAULTS["audio_format"],
                                 "source": "system"}
    assert view["override"] == {"diarize": False}


def test_is_default_is_true_while_the_workspace_owns_nothing(layers):
    layers["default"] = {"language_code": "ka"}
    view = run(tr.effective_for_tenant("client"))
    assert view["is_default"] is True and view["override"] == {}
    assert view["language_code"] == "ka", "inheriting is not the same as being unconfigured"


# ---------------------------------------------------------------------------
# The per-file object off the wire
# ---------------------------------------------------------------------------
def test_parse_override_reads_the_multipart_json_field():
    assert tr.parse_override(None) == {}
    assert tr.parse_override("") == {}
    assert tr.parse_override('{"language_code":"ka"}') == {"language_code": "ka"}
    assert tr.parse_override({"diarize": False}) == {"diarize": False}


@pytest.mark.parametrize("raw", ["not json", "[1,2]", '"ka"', "123"])
def test_parse_override_refuses_anything_that_is_not_an_object(raw):
    with pytest.raises(tr.TranscriptionSettingsError):
        tr.parse_override(raw)


def test_as_kwargs_of_nothing_is_nothing():
    """The seam that keeps an un-taught call path on the old behaviour: no settings in, no
    keyword arguments out, so `elevenlabs.transcribe` runs on its own defaults."""
    assert tr.as_kwargs(None) == {} and tr.as_kwargs({}) == {}


# ---------------------------------------------------------------------------
# Encoding: services/audio.py
# ---------------------------------------------------------------------------
def _wav(seconds: float = 0.4, rate: int = 44100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def test_original_never_invokes_ffmpeg(monkeypatch):
    """'original' exists so the owner can send the exact bytes their browser test sent. If it
    went anywhere near ffmpeg it would not be the original, and on a host without ffmpeg it
    has to work anyway — hence the check that precedes `ffmpeg_available()`."""
    def _boom():
        raise AssertionError("ffmpeg must not be consulted for audio_format='original'")

    async def _never(*a, **kw):
        raise AssertionError("ffmpeg must not be executed for audio_format='original'")

    monkeypatch.setattr(audio_mod, "ffmpeg_available", _boom)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _never)
    raw = _wav()
    out = run(audio_mod.to_stt_format(raw, "call.wav", "audio/wav", "original"))
    assert out.data is raw
    assert out.filename == "call.wav" and out.content_type == "audio/wav"
    assert out.file_format is None


def test_an_unknown_format_falls_back_to_the_default_instead_of_raising(monkeypatch):
    """Validation happens at the door; a config that somehow drifted must not stop a
    transcription hours later."""
    monkeypatch.setattr(audio_mod, "ffmpeg_available", lambda: False)
    out = run(audio_mod.to_stt_format(b"bytes", "a.bin", "x/y", "nonsense"))
    assert out.data == b"bytes" and out.file_format is None


def test_a_failed_conversion_returns_the_original_and_clears_the_file_format():
    """The safety property this module was written around: a file ffmpeg cannot read is still
    handed to the STT. The `file_format` hint MUST be dropped with it — the bytes going out are
    not the PCM it would promise."""
    out = run(audio_mod.to_stt_format(b"this is not audio", "junk.bin", "application/octet-stream",
                                      "wav_16k"))
    assert out.data == b"this is not audio"
    assert out.file_format is None, "a fallback must never claim to be pcm_s16le_16"


@pytest.mark.parametrize("fmt, ext, ctype, hint", [
    ("flac_full", ".flac", "audio/flac", None),
    ("flac_16k", ".flac", "audio/flac", None),
    ("wav_16k", ".wav", "audio/wav", "pcm_s16le_16"),
    ("mp3_16k", ".mp3", "audio/mpeg", None),
])
def test_each_format_produces_its_own_container(fmt, ext, ctype, hint):
    if not audio_mod.ffmpeg_available():
        pytest.skip("no ffmpeg on this host")
    out = run(audio_mod.to_stt_format(_wav(), "call.wav", "audio/wav", fmt))
    assert out.filename == "call" + ext and out.content_type == ctype
    assert out.file_format == hint
    assert out.data and out.data != _wav(), "the conversion actually ran"


def test_wav_16k_really_is_mono_16k_s16le():
    """`file_format=pcm_s16le_16` is a promise about the bytes. Check the bytes."""
    if not audio_mod.ffmpeg_available():
        pytest.skip("no ffmpeg on this host")
    out = run(audio_mod.to_stt_format(_wav(rate=44100), "call.wav", "audio/wav", "wav_16k"))
    with wave.open(io.BytesIO(out.data), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)


def test_flac_16k_is_lossless_and_smaller_than_the_source_wav():
    if not audio_mod.ffmpeg_available():
        pytest.skip("no ffmpeg on this host")
    src = _wav()
    out = run(audio_mod.to_stt_format(src, "call.wav", "audio/wav", "flac_16k"))
    assert out.data[:4] == b"fLaC", "the container has to actually be FLAC"
    assert len(out.data) < len(src)


# ---------------------------------------------------------------------------
# The wire: what elevenlabs.transcribe() actually posts
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def wire(monkeypatch):
    """Stub the provider one level below `transcribe`, and stub the encoder so the assertion is
    about the FORM FIELDS rather than about ffmpeg's output."""
    sent = []

    async def _request(method, path, action, scope=None, *, timeout, **kw):
        assert (method, path) == ("POST", "/speech-to-text")
        sent.append({"data": kw.get("data"), "files": kw.get("files"), "timeout": timeout})
        return _Resp({"text": "ok", "language_code": "ka", "words": []})

    async def _encode(data, filename="audio", content_type="", audio_format=None):
        spec = audio_mod.STT_FORMATS.get(audio_format or audio_mod.DEFAULT_STT_FORMAT)
        return audio_mod.SttPayload(b"ENCODED", "call" + (spec.ext or ""),
                                    spec.content_type or content_type, spec.file_format)

    monkeypatch.setattr(elevenlabs, "_request", _request)
    monkeypatch.setattr(audio_mod, "to_stt_format", _encode)
    return sent


def test_a_request_with_no_settings_sends_exactly_what_it_always_did(wire):
    """THE pin. Before these settings existed the body was exactly these three fields, with
    diarize hardcoded on. A caller that configures nothing must still produce that."""
    run(elevenlabs.transcribe(b"raw", "call.wav", "audio/wav", "key", "scribe_v1"))
    assert wire[0]["data"] == {"model_id": "scribe_v1", "diarize": "true",
                               "tag_audio_events": "true"}
    assert "language_code" not in wire[0]["data"]
    assert "keyterms" not in wire[0]["data"]
    assert "file_format" not in wire[0]["data"]


def test_language_and_keyterms_are_sent_only_when_they_have_a_value(wire):
    run(elevenlabs.transcribe(b"raw", "call.wav", "audio/wav", "key", "scribe_v1",
                              language_code="ka", keyterms=["თვე", "policy number"]))
    data = wire[0]["data"]
    assert data["language_code"] == "ka"
    # A LIST, not a JSON string: httpx expands a list in `data` into one form part per item,
    # which is exactly what the official elevenlabs-python client sends (it reserves JSON
    # encoding for `additional_formats`, the list-of-objects field).
    assert data["keyterms"] == ["თვე", "policy number"]
    assert not isinstance(data["keyterms"], str)


def test_empty_keyterms_and_a_null_language_add_no_fields(wire):
    run(elevenlabs.transcribe(b"raw", "call.wav", "audio/wav", "key", "scribe_v1",
                              language_code=None, keyterms=[]))
    assert wire[0]["data"] == {"model_id": "scribe_v1", "diarize": "true",
                               "tag_audio_events": "true"}


def test_diarize_off_is_sent_explicitly(wire):
    run(elevenlabs.transcribe(b"raw", "call.wav", "audio/wav", "key", "scribe_v1", diarize=False))
    assert wire[0]["data"]["diarize"] == "false"


def test_wav_16k_also_sends_the_file_format_hint(wire):
    run(elevenlabs.transcribe(b"raw", "call.wav", "audio/wav", "key", "scribe_v1",
                              audio_format="wav_16k"))
    assert wire[0]["data"]["file_format"] == "pcm_s16le_16"
    assert wire[0]["files"]["file"][2] == "audio/wav"


@pytest.mark.parametrize("fmt", ["original", "flac_full", "flac_16k", "mp3_16k"])
def test_no_other_format_claims_to_be_pcm(wire, fmt):
    run(elevenlabs.transcribe(b"raw", "call.wav", "audio/wav", "key", "scribe_v1",
                              audio_format=fmt))
    assert "file_format" not in wire[0]["data"]


def test_the_resolved_settings_reach_the_provider_as_one_hop(wire):
    """`as_kwargs` is the only translation between the settings and the call — pinned so a
    field added to one side cannot quietly stop reaching the other."""
    cfg = {"language_code": "ka", "diarize": False, "keyterms": ["თვე"],
           "audio_format": "flac_16k"}
    run(elevenlabs.transcribe(b"raw", "call.wav", "audio/wav", "key", "scribe_v1",
                              **tr.as_kwargs(cfg)))
    assert wire[0]["data"] == {"model_id": "scribe_v1", "diarize": "false",
                               "tag_audio_events": "true", "language_code": "ka",
                               "keyterms": ["თვე"]}


# ---------------------------------------------------------------------------
# Routes (integration — needs the database)
# ---------------------------------------------------------------------------
@pytest.fixture
def workspace(api):
    """One tenant with an API key. Everything cascades from `clients`."""
    suffix = uuid.uuid4().hex[:8]
    key = f"trset-key-{suffix}"
    cid = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
        f"trset-{suffix}", f"tr-settings-{suffix}", key))
    try:
        yield {"client_id": str(cid), "api_key": key, "headers": {"X-API-Key": key}}
    finally:
        sql(lambda c: c.execute("DELETE FROM clients WHERE id = $1", cid))


@pytest.fixture
def clean_defaults(api):
    """Snapshot and restore the DEPLOYMENT default. These tests write a global row; leaving a
    developer's machine transcribing everything in Georgian would be a rude side effect."""
    before = sql(lambda c: c.fetchval(
        "SELECT value FROM app_settings WHERE key = $1", tr.DEFAULTS_KEY))
    # The blob is 5 s-cached in-process and these fixtures write it with raw SQL, so the cache
    # has to be dropped by hand on BOTH sides — otherwise one test's default leaks into the
    # next one's "inherited" layer and the assertions read as isolation bugs.
    tr.invalidate_default_cache()
    try:
        yield
    finally:
        if before is None:
            sql(lambda c: c.execute("DELETE FROM app_settings WHERE key = $1", tr.DEFAULTS_KEY))
        else:
            sql(lambda c: c.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                tr.DEFAULTS_KEY, before if isinstance(before, str) else json.dumps(before)))
        tr.invalidate_default_cache()


def test_the_defaults_route_needs_the_admin_token(api):
    assert api.get(DEFAULTS_URL).status_code == 401
    assert api.get(DEFAULTS_URL, headers={"X-Admin-Token": "nope"}).status_code == 401
    assert api.put(DEFAULTS_URL, json={"diarize": False}).status_code == 401


def test_the_default_starts_as_the_built_in_floor(api, clean_defaults):
    sql(lambda c: c.execute("DELETE FROM app_settings WHERE key = $1", tr.DEFAULTS_KEY))
    body = api.get(DEFAULTS_URL, headers=ADMIN).json()
    assert body["is_default"] is True and body["source"] == "builtin"
    assert body["audio_format"] == tr.CODE_DEFAULTS["audio_format"]
    assert body["diarize"] is True and body["language_code"] is None
    assert set(body["formats"]) == set(audio_mod.STT_FORMATS)


def test_saving_the_default_is_visible_immediately_despite_the_cache(api, clean_defaults):
    saved = api.put(DEFAULTS_URL, headers=ADMIN,
                    json={"language_code": "ka", "keyterms": ["თვე"]})
    assert saved.status_code == 200
    body = saved.json()
    assert body["language_code"] == "ka" and body["keyterms"] == ["თვე"]
    assert body["source"] == "stored" and body["is_default"] is False
    assert body["updated_by"] == "superadmin"
    # A field the body omitted falls back to the CODE default, not to the previous save.
    assert body["diarize"] is True
    assert api.get(DEFAULTS_URL, headers=ADMIN).json()["language_code"] == "ka"


def test_an_invalid_default_is_a_400_naming_the_field(api, clean_defaults):
    r = api.put(DEFAULTS_URL, headers=ADMIN, json={"audio_format": "ogg"})
    assert r.status_code == 400
    body = r.json()
    assert "audio_format" in body["detail"]
    assert body["code"] == "invalid_transcription_setting" and body["field"] == "audio_format"


def test_the_workspace_inherits_until_it_overrides(api, workspace, clean_defaults):
    api.put(DEFAULTS_URL, headers=ADMIN, json={"language_code": "ka"})
    view = api.get(CONFIG_URL, headers=workspace["headers"]).json()
    assert view["is_default"] is True and view["override"] == {}
    assert view["language_code"] == "ka"
    assert view["inherited"]["source"] == "system"
    assert view["can_edit"] is True

    saved = api.put(CONFIG_URL, headers=workspace["headers"], json={"diarize": False})
    assert saved.status_code == 200
    assert saved.json() == {**saved.json(), "is_default": False, "diarize": False}
    assert saved.json()["language_code"] == "ka", "an unset field keeps inheriting"
    assert saved.json()["inherited"]["diarize"] is True, "the fallback is still visible"


def test_deleting_the_override_goes_back_to_inheriting(api, workspace, clean_defaults):
    api.put(DEFAULTS_URL, headers=ADMIN, json={"language_code": "ka", "audio_format": "flac_16k"})
    api.put(CONFIG_URL, headers=workspace["headers"],
            json={"language_code": "ru", "audio_format": "original"})
    assert api.get(CONFIG_URL, headers=workspace["headers"]).json()["language_code"] == "ru"

    dropped = api.delete(CONFIG_URL, headers=workspace["headers"])
    assert dropped.status_code == 200
    assert dropped.json()["is_default"] is True
    assert dropped.json()["language_code"] == "ka"
    assert dropped.json()["audio_format"] == "flac_16k"


def test_an_override_replaces_rather_than_merges(api, workspace, clean_defaults):
    """The form posts the whole panel, so "I cleared that field" has to mean "inherit it
    again" — a merge would make a cleared field impossible to express."""
    api.put(DEFAULTS_URL, headers=ADMIN, json={})          # the floor, explicitly
    api.put(CONFIG_URL, headers=workspace["headers"],
            json={"language_code": "ka", "diarize": False})
    second = api.put(CONFIG_URL, headers=workspace["headers"], json={"diarize": False}).json()
    assert second["override"] == {"diarize": False}
    assert second["language_code"] is None, "the dropped field went back to inheriting"


def test_the_override_lands_beside_the_workspaces_other_settings(api, workspace, clean_defaults):
    """`clients.settings` also carries curation and the per-day caps. Writing this must not be
    the thing that wipes them."""
    sql(lambda c: c.execute(
        "UPDATE clients SET settings = settings || '{\"max_analyses_per_day\": 7}'::jsonb "
        "WHERE id = $1", uuid.UUID(workspace["client_id"])))
    api.put(CONFIG_URL, headers=workspace["headers"], json={"language_code": "ka"})
    raw = sql(lambda c: c.fetchval("SELECT settings FROM clients WHERE id = $1",
                                   uuid.UUID(workspace["client_id"])))
    stored = json.loads(raw) if isinstance(raw, str) else raw
    assert stored["max_analyses_per_day"] == 7
    assert stored["transcription"] == {"language_code": "ka"}

    api.delete(CONFIG_URL, headers=workspace["headers"])
    raw = sql(lambda c: c.fetchval("SELECT settings FROM clients WHERE id = $1",
                                   uuid.UUID(workspace["client_id"])))
    stored = json.loads(raw) if isinstance(raw, str) else raw
    assert stored["max_analyses_per_day"] == 7 and "transcription" not in stored


def test_an_invalid_override_is_a_400_naming_the_field(api, workspace):
    r = api.put(CONFIG_URL, headers=workspace["headers"], json={"keyterms": ["a<b>"]})
    assert r.status_code == 400
    assert r.json()["field"] == "keyterms" and "keyterms" in r.json()["detail"]


def test_a_signed_out_caller_cannot_read_the_settings(api):
    assert api.get(CONFIG_URL).status_code == 401


def test_one_workspace_cannot_see_or_change_another(api, workspace, clean_defaults):
    other_key = f"trset-other-{uuid.uuid4().hex[:8]}"
    other = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
        f"trset-o-{uuid.uuid4().hex[:8]}", "tr-other", other_key))
    try:
        api.put(DEFAULTS_URL, headers=ADMIN, json={"language_code": "en"})
        api.put(CONFIG_URL, headers=workspace["headers"], json={"language_code": "ka"})
        seen = api.get(CONFIG_URL, headers={"X-API-Key": other_key}).json()
        assert seen["is_default"] is True and seen["override"] == {}
        assert seen["language_code"] == "en", "the neighbour's override must not reach here"
    finally:
        sql(lambda c: c.execute("DELETE FROM clients WHERE id = $1", other))


def test_an_operator_reaches_the_workspace_through_act_as_tenant(api, workspace, clean_defaults):
    """One page, one route: the console drives the customer's own endpoint (root CLAUDE.md §2),
    so there is no /admin twin of /transcription/config to drift."""
    hdr = {**ADMIN, "X-Act-As-Tenant": workspace["client_id"]}
    assert api.put(CONFIG_URL, headers=hdr, json={"language_code": "ka"}).status_code == 200
    assert api.get(CONFIG_URL, headers=workspace["headers"]).json()["language_code"] == "ka"
