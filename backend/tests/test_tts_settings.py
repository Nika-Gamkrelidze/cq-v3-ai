"""Advanced TTS controls: the model catalogue and per-model shaping of voice_settings.

Two promises are pinned here. First, the rules live in ONE place — the ElevenLabs adapter's
`model_caps` (`services/providers/tts_elevenlabs.py`) — and both the customer form
(GET /tts/models) and the request shaper (`services/voice.py::shape_voice_settings`) derive
from it — so a v3 model that shows no style slider also never has a style value sent for it. Second, a request
written against the old contract (text + language, nothing else) still produces the exact
ElevenLabs body it always did: no `voice_settings` key, `language_code` on multilingual_v2,
nothing but text + model for Georgian.

The route tests stub `elevenlabs._request` — one level below `text_to_speech` — because the
claim under test is what leaves the process, and `text_to_speech` is the function that decides
whether a `voice_settings` key exists at all. No network, no keys; the quota gate is a no-op
and stored clips land in a temp dir. The routes reach ElevenLabs through `services/voice.py`
now; with no registry rows the resolver answers with the legacy settings, so the stubbed
`settings_store.get_effective` is still the whole configuration.
"""
import json
import uuid

import pytest

from app.services import elevenlabs, limits, media, settings_store, voice
from app.services.providers import tts_elevenlabs as el
from conftest import sql  # loop-independent SQL; see its module docstring

RACHEL = "21m00Tcm4TlvDq8ikWAM"
MARK = f"ttssettings-{uuid.uuid4().hex[:8]}"


def _raw(model_id, *, name=None, style=True, boost=True, tts_ok=True, limit=10000, langs=("en",)):
    """A model record in the shape GET /v1/models returns it (languages as objects)."""
    return {"model_id": model_id, "name": name or model_id, "description": f"{model_id} desc",
            "can_do_text_to_speech": tts_ok, "can_use_style": style,
            "can_use_speaker_boost": boost, "maximum_text_length_per_request": limit,
            "languages": [{"language_id": code, "name": code.upper()} for code in langs]}


RAW_LIVE = [
    _raw("eleven_flash_v2_5", name="Flash v2.5", limit=40000, langs=("en", "ru")),
    _raw("eleven_turbo_v2_5", limit=40000),                       # deprecated alias of flash
    _raw("eleven_v3_conversational", style=False, limit=5000),
    _raw("eleven_v3", name="Eleven v3", style=False, limit=5000, langs=("en", "ka", "ru")),
    _raw("eleven_monolingual_v1"),                                # legacy
    _raw("eleven_english_sts_v2", tts_ok=False),                  # speech-to-speech
    _raw("eleven_multilingual_v2", name="Multilingual v2", langs=("en", "ru")),
]

# The same records as `list_models` reduces them, for the pure tests.
V3 = {"model_id": "eleven_v3", "can_use_style": False, "can_use_speaker_boost": True,
      "maximum_text_length_per_request": 5000}
MV2 = {"model_id": "eleven_multilingual_v2", "can_use_style": True, "can_use_speaker_boost": True,
       "maximum_text_length_per_request": 10000}
FLASH = {"model_id": "eleven_flash_v2_5", "can_use_style": True, "can_use_speaker_boost": True,
         "maximum_text_length_per_request": 40000}


# ---------------------------------------------------------------------------
# Pure: the capability rules
# ---------------------------------------------------------------------------
def test_caps_v3_is_presets_only():
    caps = el.model_caps(V3)
    assert caps == {"presets": True, "style": False, "speaker_boost": True, "speed": False,
                    "language_code": "rejected", "max_chars": 5000}


def test_caps_multilingual_v2_takes_everything_and_ignores_the_language():
    caps = el.model_caps(MV2)
    assert caps == {"presets": False, "style": True, "speaker_boost": True, "speed": True,
                    "language_code": "ignored", "max_chars": 5000}   # min(5000, 10000)


def test_caps_flash_enforces_the_language():
    assert el.model_caps(FLASH)["language_code"] == "enforced"
    assert el.model_caps(FLASH)["max_chars"] == 5000


def test_caps_style_off_is_honoured_outside_v3():
    """A non-v3 model that says it cannot use style loses the slider too — the rule is the
    model's own flag, with the v3 prefix only adding to it."""
    assert el.model_caps(dict(MV2, can_use_style=False))["style"] is False


def test_caps_unknown_model_reads_as_supported():
    """The configured default may be a model the list does not describe. Absent flags mean
    "send it" — ElevenLabs ignores a field a model has no use for; hiding one it does take
    is a lost feature."""
    caps = el.model_caps({"model_id": "eleven_something_new"})
    assert caps["style"] and caps["speaker_boost"] and caps["speed"]
    assert caps["language_code"] == "ignored" and caps["max_chars"] == 5000


def test_visible_hides_legacy_and_orders_the_products_first():
    ids = [m["model_id"] for m in el._visible([dict(m) for m in RAW_LIVE])]
    assert ids == ["eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5",
                   "eleven_v3_conversational"]
    for hidden in el.MODEL_HIDE:
        assert hidden not in ids


# ---------------------------------------------------------------------------
# Pure: shaping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("asked, sent", [(0.3, 0.5), (0.8, 1.0), (0.0, 0.0), (0.2, 0.0), (1.0, 1.0)])
def test_v3_snaps_stability_to_a_preset(asked, sent):
    shaped = voice.shape_voice_settings(el.model_caps(V3), {"stability": asked})
    assert shaped == {"stability": sent}


def test_v3_drops_style_and_speed_but_keeps_similarity_and_boost():
    shaped = voice.shape_voice_settings(
        el.model_caps(V3),
        {"stability": 0.3, "style": 0.4, "speed": 1.1, "similarity_boost": 0.9,
         "use_speaker_boost": False})
    assert shaped == {"stability": 0.5, "similarity_boost": 0.9, "use_speaker_boost": False}


def test_multilingual_v2_keeps_everything_clamped():
    shaped = voice.shape_voice_settings(
        el.model_caps(MV2),
        {"stability": 1.7, "similarity_boost": -0.2, "style": 0.4, "use_speaker_boost": 1,
         "speed": 0.5})
    assert shaped == {"stability": 1.0, "similarity_boost": 0.0, "style": 0.4,
                      "use_speaker_boost": True, "speed": 0.7}


def test_nothing_surviving_is_none_not_an_empty_object():
    assert voice.shape_voice_settings(el.model_caps(MV2), None) is None
    assert voice.shape_voice_settings(el.model_caps(MV2), {}) is None
    assert voice.shape_voice_settings(el.model_caps(MV2), {"style": None}) is None
    # v3 with only the controls it does not have: the model gets the voice's defaults.
    assert voice.shape_voice_settings(el.model_caps(V3), {"style": 0.4, "speed": 1.1}) is None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, *, content: bytes = b"", body=None):
        self.content, self._body = content, body

    def json(self):
        return self._body


@pytest.fixture
def stubs(monkeypatch, tmp_path):
    state = {"models": RAW_LIVE, "models_down": False, "sent": [], "reserved": 0}

    async def _request(method, path, action, scope=None, *, timeout, **kw):
        if path == "/models":
            if state["models_down"]:
                raise elevenlabs.ElevenLabsError("models down", code="transport")
            return _Resp(body=[dict(m) for m in state["models"]])
        assert method == "POST" and path.startswith("/text-to-speech/"), path
        state["sent"].append({"voice_id": path.rsplit("/", 1)[1], "json": kw.get("json")})
        return _Resp(content=b"ID3fake-mp3")

    async def _effective():
        return {"elevenlabs_api_key": "k", "tts_voice_id": RACHEL,
                "tts_model": "eleven_multilingual_v2"}

    async def _voice_config():
        return {"mode": "all", "voice_ids": []}

    async def _reserve(principal, kind, size_bytes=0):
        state["reserved"] += 1

    monkeypatch.setattr(elevenlabs, "_request", _request)
    monkeypatch.setattr(settings_store, "get_effective", _effective)
    monkeypatch.setattr(settings_store, "get_voice_config", _voice_config)
    monkeypatch.setattr(limits, "reserve", _reserve)
    # An anonymous clip is kept on disk: point the media root at a temp dir, never the volume.
    monkeypatch.setattr(media, "MEDIA_ROOT", tmp_path / "media")
    # Every test starts with a cold catalogue, so `models_down` is seen by the next request.
    monkeypatch.setattr(el, "_models_cache", {})
    try:
        yield state
    finally:
        sql(lambda c: c.execute("DELETE FROM tts_requests WHERE text LIKE $1", f"{MARK}%"))


def _text(tag: str) -> str:
    return f"{MARK} {tag}"


def _stored_settings(text: str):
    raw = sql(lambda c: c.fetchval(
        "SELECT voice_settings FROM tts_requests WHERE text = $1 ORDER BY created_at DESC LIMIT 1",
        text))
    return json.loads(raw) if isinstance(raw, str) else raw


def test_models_route_hides_legacy_ids_and_orders_mv2_first(api, stubs):
    body = api.get("/tts/models").json()
    assert [m["model_id"] for m in body] == [
        "eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5", "eleven_v3_conversational"]
    by_id = {m["model_id"]: m for m in body}
    assert by_id["eleven_v3"]["supports"] == {
        "presets": True, "style": False, "speaker_boost": True, "speed": False,
        "language_code": "rejected"}
    assert by_id["eleven_v3"]["languages"] == ["en", "ka", "ru"]
    assert by_id["eleven_multilingual_v2"]["max_chars"] == 5000       # min(5000, 10000)
    assert by_id["eleven_flash_v2_5"]["supports"]["language_code"] == "enforced"
    assert by_id["eleven_flash_v2_5"]["name"] == "Flash v2.5"
    # Also on the partner surface.
    assert api.get("/v1/tts/models").json() == body


def test_models_route_fails_open_to_the_built_ins(api, stubs):
    stubs["models_down"] = True
    body = api.get("/tts/models").json()
    assert [m["model_id"] for m in body] == ["eleven_multilingual_v2", "eleven_v3"]
    assert body[1]["supports"]["presets"] is True


def test_languages_carry_the_model_auto_resolves_to(api):
    langs = {row["code"]: row["model"] for row in api.get("/languages").json()}
    assert langs == {"en": "eleven_multilingual_v2", "ru": "eleven_multilingual_v2",
                     "ka": "eleven_v3"}


def test_georgian_snaps_to_a_preset_and_never_sends_a_language_code(api, stubs):
    text = _text("ka")
    r = api.post("/tts", json={"text": text, "language_code": "ka",
                               "voice_settings": {"stability": 0.3, "style": 0.4, "speed": 1.1}})
    assert r.status_code == 200 and r.content == b"ID3fake-mp3"
    [sent] = stubs["sent"]
    assert sent["voice_id"] == el.GEORGIAN_VOICE
    assert sent["json"] == {"text": text, "model_id": "eleven_v3",
                            "voice_settings": {"stability": 0.5}}
    assert "language_code" not in sent["json"]
    # The row keeps what was SENT, not what was asked, so the clip can be reproduced.
    assert _stored_settings(text) == {"stability": 0.5}


def test_flash_with_enforced_language_sends_the_code_and_every_control(api, stubs):
    text = _text("flash")
    vs = {"stability": 0.4, "similarity_boost": 0.8, "style": 0.2, "use_speaker_boost": True,
          "speed": 1.1}
    r = api.post("/tts", json={"text": text, "language_code": "en",
                               "model_id": "eleven_flash_v2_5", "enforce_language": True,
                               "voice_settings": vs})
    assert r.status_code == 200
    [sent] = stubs["sent"]
    assert sent["voice_id"] == RACHEL
    assert sent["json"] == {"text": text, "model_id": "eleven_flash_v2_5",
                            "language_code": "en", "voice_settings": vs}


def test_enforce_language_false_leaves_the_language_to_the_model(api, stubs):
    text = _text("noforce")
    r = api.post("/tts", json={"text": text, "language_code": "en", "enforce_language": False})
    assert r.status_code == 200
    [sent] = stubs["sent"]
    assert sent["json"] == {"text": text, "model_id": "eleven_multilingual_v2"}


def test_speed_out_of_range_is_a_422_before_anything_happens(api, stubs):
    r = api.post("/tts", json={"text": _text("fast"), "language_code": "en",
                               "voice_settings": {"speed": 2.0}})
    assert r.status_code == 422
    assert stubs["sent"] == [] and stubs["reserved"] == 0


def test_unknown_model_is_refused_before_quota(api, stubs):
    r = api.post("/tts", json={"text": _text("nope"), "language_code": "en",
                               "model_id": "eleven_made_up"})
    assert r.status_code == 400
    assert r.json() == {"detail": "Model 'eleven_made_up' is not available for text-to-speech.",
                        "code": "model_unavailable"}
    assert stubs["sent"] == [] and stubs["reserved"] == 0


def test_a_hidden_model_cannot_be_reached_by_hand(api, stubs):
    r = api.post("/tts", json={"text": _text("alias"), "language_code": "en",
                               "model_id": "eleven_turbo_v2_5"})
    assert r.status_code == 400 and r.json()["code"] == "model_unavailable"


def test_built_ins_are_accepted_when_the_list_is_down(api, stubs):
    stubs["models_down"] = True
    ok = api.post("/tts", json={"text": _text("down-v3"), "language_code": "en",
                                "model_id": "eleven_v3"})
    assert ok.status_code == 200
    # en on v3 by hand: v3 rejects a language_code, so none is sent even for English.
    assert stubs["sent"][-1]["json"] == {"text": _text("down-v3"), "model_id": "eleven_v3"}
    no = api.post("/tts", json={"text": _text("down-flash"), "language_code": "en",
                                "model_id": "eleven_flash_v2_5"})
    assert no.status_code == 400 and no.json()["code"] == "model_unavailable"


def test_a_request_without_the_new_fields_is_byte_identical(api, stubs):
    """The old contract, unchanged: no voice_settings key at all, language_code on the
    multilingual_v2 path exactly as before, text + model only for Georgian."""
    en, ka = _text("legacy-en"), _text("legacy-ka")
    assert api.post("/tts", json={"text": en, "language_code": "en"}).status_code == 200
    assert api.post("/tts", json={"text": ka, "language_code": "ka"}).status_code == 200
    assert api.post("/tts", json={"text": _text("legacy-none")}).status_code == 200
    bodies = [s["json"] for s in stubs["sent"]]
    assert bodies[0] == {"text": en, "model_id": "eleven_multilingual_v2", "language_code": "en"}
    assert bodies[1] == {"text": ka, "model_id": "eleven_v3"}
    assert bodies[2] == {"text": _text("legacy-none"), "model_id": "eleven_multilingual_v2"}
    assert all("voice_settings" not in b for b in bodies)
    assert _stored_settings(en) is None
