"""ElevenLabs text-to-speech as a TTS adapter.

The synthesis call itself is `services/elevenlabs.py::text_to_speech`, unchanged and pinned by
tests/test_tts_settings.py. What lives HERE is everything that used to sit in routers/tts.py
and was really a fact about ElevenLabs rather than about the product:

  * which models the customer form must not offer (legacy ids, deprecated aliases, the one
    speech-to-speech model), and in what order the rest are shown;
  * `model_caps` — what one model accepts (the v3 family's presets, its missing speed control
    and its rejection of language_code; style/boost/length from the model record);
  * the per-language defaults — above all the Georgian rule: `eleven_multilingual_v2`
    mispronounces Georgian (English-accented), the correct result comes from `eleven_v3`
    paired with a Georgian-capable voice ("Laura", 3b8fXc91YHS1i2DYAlBQ), and NO language_code
    (v3 reads the Georgian script; the v3 voice 400s on "ka"). Verified against the live API;
    a TTS→STT round-trip returns clean Georgian (lang=kat). Do not "simplify" this back to one
    model.
  * the model catalogue cache, per API key (two connections may see two catalogues).
"""
from __future__ import annotations

import logging
import re
import time

from .. import elevenlabs
from . import voice_base
from .voice_base import (LANGUAGE_ENFORCED, LANGUAGE_IGNORED, LANGUAGE_REJECTED, MAX_TEXT_CHARS,
                         STABILITY_PRESETS, public_model)

log = logging.getLogger("cq")

DEFAULT_MODEL = "eleven_multilingual_v2"

# A caller-supplied voice_id is interpolated into the ElevenLabs URL path, so it must be
# validated regardless of any allowlist (e.g. "../../v1/dubbing" would otherwise reach a
# different endpoint with our account key).
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{16,32}$")

GEORGIAN_VOICE = "3b8fXc91YHS1i2DYAlBQ"  # "Laura - Natural & Grounded" (shared voice)

# What "Auto" resolves to per language. `voice` None = the configured default voice. Whether
# ElevenLabs accepts a language_code is a property of the MODEL, not the language, and is
# answered by `model_caps` — one rule for the Auto path and for a caller who picks a model.
LANGUAGE_DEFAULTS: dict[str, dict] = {
    "en": {"model": "eleven_multilingual_v2", "voice": None, "note": ""},
    "ru": {"model": "eleven_multilingual_v2", "voice": None, "note": ""},
    "ka": {"model": "eleven_v3", "voice": GEORGIAN_VOICE,
           "note": "Georgian uses the eleven_v3 model with a Georgian-capable voice for "
                   "correct pronunciation. Leave the voice on default for best results."},
}

# Models GET /v1/models returns that the customer form must NOT offer. Legacy (v1 models
# and the English-only flash_v2 predate everything this product runs on), deprecated aliases
# (turbo_v2_5 IS flash_v2_5 and turbo_v2 IS flash_v2 — showing both means two rows that sound
# identical), and one that is not text-to-speech at all (english_sts_v2 is speech-to-speech).
# A model_id in here is refused by POST /tts too, so a hidden model cannot be reached by
# hand-writing the request.
MODEL_HIDE = frozenset({
    "eleven_multilingual_v1", "eleven_monolingual_v1", "eleven_flash_v2", "eleven_turbo_v2",
    "eleven_turbo_v2_5", "eleven_english_sts_v2",
})

# Display order: the default the product ships with, then the model Georgian needs, then the
# cheap/fast one, then whatever else the account has, alphabetically.
MODEL_ORDER = ("eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5")

# Models whose language_code ElevenLabs ENFORCES (it changes what comes out). Elsewhere it is
# ignored (harmless, sent anyway on the ones that take it) or rejected (v3 — the Georgian
# voice 400s on it, which is the whole reason the Georgian path never sent one).
_LANGUAGE_ENFORCING = frozenset({"eleven_flash_v2_5", "eleven_turbo_v2_5"})

# The v3 stability presets — Creative / Natural / Robust. ElevenLabs rejects any other value
# for v3, so a slider position has to be snapped to one of these before it is sent
# (`voice.shape_voice_settings` does the snapping, off `caps()["presets"]`).
V3_STABILITY_PRESETS = STABILITY_PRESETS


def model_caps(model: dict) -> dict:
    """What one model accepts — THE place these rules live.

    Everything the customer form shows and everything the request shaper drops derives from
    this one function, so the two can never disagree. Facts come from the ElevenLabs model
    record where it states them (style, speaker boost, max length); the v3 family's presets,
    missing speed control and language_code rejection are documented behaviour the API does
    not advertise per model, hence the prefix rule.
    """
    model_id = str(model.get("model_id") or "")
    presets = model_id.startswith("eleven_v3")
    if model_id in _LANGUAGE_ENFORCING:
        language_code = LANGUAGE_ENFORCED
    elif presets:
        language_code = LANGUAGE_REJECTED
    else:
        language_code = LANGUAGE_IGNORED
    # None (field absent — the built-in fallback, or a model the list did not describe) is
    # read as "supported": ElevenLabs ignores a field a model has no use for, whereas hiding a
    # control the model does take is a lost feature.
    style = model.get("can_use_style")
    boost = model.get("can_use_speaker_boost")
    limit = model.get("maximum_text_length_per_request")
    return voice_base.caps(
        presets=presets,
        style=(style is not False) and not presets,      # v3 takes emotion from audio tags
        speaker_boost=boost is not False,
        speed=not presets,                               # v3 paces itself
        language_code=language_code,
        max_chars=int(limit) if limit else MAX_TEXT_CHARS,
    )


# What the customer form gets when GET /v1/models is unreachable or the key lacks
# models_read: exactly the two models this code already synthesizes with, described the way
# the live record would describe them. The form keeps working; only the extra models vanish.
FALLBACK_MODELS: tuple[dict, ...] = (
    {"model_id": "eleven_multilingual_v2", "name": "Multilingual v2",
     "description": "Stable, natural speech in 29 languages.",
     "can_do_text_to_speech": True, "can_use_style": True, "can_use_speaker_boost": True,
     "languages": ["en", "ru"], "maximum_text_length_per_request": 10000},
    {"model_id": "eleven_v3", "name": "Eleven v3",
     "description": "Most expressive model; the one Georgian needs.",
     "can_do_text_to_speech": True, "can_use_style": False, "can_use_speaker_boost": True,
     "languages": ["en", "ru", "ka"], "maximum_text_length_per_request": 5000},
)

# ElevenLabs' model catalogue changes a few times a year, so ten minutes is a lifetime; the
# fallback is re-tried after one minute so a recovered API is seen without a restart, while
# a down one is not hit on every keystroke of the form. Module-level, so per-process — right
# for a single uvicorn worker (same assumption as settings_store's kill-switch cache). Keyed
# by API key: two connections are two accounts and may be entitled to two different lists.
MODELS_TTL_S = 600.0
MODELS_FALLBACK_TTL_S = 60.0
_models_cache: dict[str, tuple[float, list[dict], bool]] = {}   # key -> (fetched_at, models, live)


def _visible(models: list[dict]) -> list[dict]:
    """Hide the legacy/alias/non-TTS ids and put the product's models first."""
    keep = {m["model_id"]: m for m in models
            if m.get("model_id") and m["model_id"] not in MODEL_HIDE
            and m.get("can_do_text_to_speech") is not False}
    ordered = [keep.pop(mid) for mid in MODEL_ORDER if mid in keep]
    ordered += [keep[mid] for mid in sorted(keep)]
    return ordered


async def visible_models(api_key: str) -> list[dict]:
    """The models POST /tts will accept and GET /tts/models will list, cached per key. Fails
    OPEN to `FALLBACK_MODELS` so an ElevenLabs outage costs the extra models, not the feature."""
    now = time.monotonic()
    cache_key = api_key or ""
    hit = _models_cache.get(cache_key)
    if hit:
        fetched_at, models, live = hit
        if (now - fetched_at) < (MODELS_TTL_S if live else MODELS_FALLBACK_TTL_S):
            return models
    try:
        models, live = _visible(await elevenlabs.list_models(api_key)), True
        if not models:
            # An account whose list came back empty is not one we can synthesize with anyway;
            # treat it like an outage so the built-ins stay offered.
            raise RuntimeError("ElevenLabs returned no usable text-to-speech model")
    except Exception as exc:  # noqa: BLE001 — every failure is the same answer: the built-ins
        log.warning("tts model list unavailable, using built-in fallback: %s", exc)
        models, live = _visible(list(FALLBACK_MODELS)), False
    _models_cache[cache_key] = (now, models, live)
    return models


def _public(model: dict) -> dict:
    return public_model(model["model_id"], name=model.get("name"),
                        description=model.get("description") or "",
                        languages=model.get("languages"), caps=model_caps(model))


class ElevenLabsTTS:
    id = "elevenlabs"
    default_model = DEFAULT_MODEL
    default_voice = None        # the product's legacy default voice is voice.py's to supply

    def model(self, res) -> str:
        return voice_base.own_model(res, DEFAULT_MODEL, foreign_prefixes=("tts-", "gpt-", "gemini"))

    async def synthesize(self, res, text: str, *, voice_id: str, model_id: str,
                         language_code: str | None = None,
                         voice_settings: dict | None = None) -> bytes:
        return await elevenlabs.text_to_speech(text, res.api_key, voice_id, model_id,
                                               language_code, voice_settings=voice_settings)

    async def list_voices(self, res) -> list[dict]:
        return await elevenlabs.list_voices(res.api_key)

    async def list_models(self, res) -> list[dict]:
        return [_public(m) for m in await visible_models(res.api_key)]

    async def caps(self, res, model_id: str) -> dict:
        catalogue = {m["model_id"]: m for m in await visible_models(res.api_key)}
        return model_caps(catalogue.get(model_id) or {"model_id": model_id})

    def defaults_for_language(self, res, lang: str) -> dict:
        info = LANGUAGE_DEFAULTS.get((lang or "").lower())
        if info:
            return dict(info)
        return {"model": self.model(res), "voice": None, "note": ""}

    def valid_voice_id(self, voice_id: str) -> bool:
        return bool(voice_id) and VOICE_ID_RE.match(voice_id) is not None

    def language_voice_ids(self) -> set[str]:
        return {info["voice"] for info in LANGUAGE_DEFAULTS.values() if info.get("voice")}

    async def probe(self, res) -> dict:
        """List the voices (proves the key, costs nothing), then say two characters with the
        connection's voice — or the first voice the account has — so a key restricted to
        voices_read cannot show green for text_to_speech."""
        try:
            voices = await self.list_voices(res)
            voice_id = (res.settings or {}).get("voice_id") or \
                next((v["voice_id"] for v in voices if v.get("voice_id")), None)
            if not voice_id:
                return {"ok": False, "code": "no_voices",
                        "detail": "authenticated, but this account lists 0 voices and the "
                                  "connection sets no voice"}
            audio = await elevenlabs.text_to_speech("ok", res.api_key, voice_id, self.model(res),
                                                    None, output_format="mp3_22050_32",
                                                    timeout=60.0)
        except voice_base.VoiceError as exc:
            return {"ok": False, "detail": str(exc), "code": exc.code, "scope": exc.scope}
        except Exception as exc:  # noqa: BLE001 — a probe never raises
            return {"ok": False, "detail": str(exc), "code": "http"}
        return {"ok": True,
                "detail": f"{len(voices)} voices; model {self.model(res)} returned "
                          f"{len(audio)} bytes with voice {voice_id}"}


adapter = ElevenLabsTTS()
