"""OpenAI text-to-speech (`POST /v1/audio/speech`) as a TTS adapter.

UNTESTED AGAINST THE LIVE API until a key is added — the registry's "Test connection" button is
how it gets verified. What is pinned (tests/test_voice.py) is the JSON body and that the bytes
come back as the clip.

What this provider has, and what it does not, in `caps()` terms:
  * a FIXED voice list (no account voices, no previews) — `list_voices` is a constant;
  * `speed` (0.25–4.0 upstream; the product's 0.7–1.2 sits inside it);
  * NO stability / similarity / style / speaker-boost controls and NO v3-style presets, so
    the customer form shows only the speed slider and the shaper drops the rest before the
    request is built;
  * no language parameter at all — the model reads the script — so `language_code` is
    "ignored": the seam may pass one, this adapter simply does not send it;
  * `instructions` (gpt-4o-mini-tts only) is where OpenAI takes tone. Nothing in the
    product's voice_settings expresses tone, so it is taken from the connection's own
    `settings.instructions` when set, and never sent to the `tts-1` models, which reject it.
"""
from __future__ import annotations

import logging

from . import voice_base
from .voice_base import LANGUAGE_IGNORED, public_model

log = logging.getLogger("cq")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE = "alloy"
INPUT_LIMIT = 4096          # characters per request, documented by OpenAI

# Known, not exhaustive: an operator may type another id on the connection.
VOICES: tuple[str, ...] = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
                           "sage", "shimmer", "verse")

MODELS: tuple[dict, ...] = (
    {"model_id": "tts-1", "name": "TTS-1",
     "description": "Fast, low-latency speech."},
    {"model_id": "tts-1-hd", "name": "TTS-1 HD",
     "description": "Higher-quality speech; slower than TTS-1."},
    {"model_id": "gpt-4o-mini-tts", "name": "GPT-4o mini TTS",
     "description": "Steerable speech: tone and style follow the connection's instructions."},
)
_INSTRUCTABLE = ("gpt-4o-mini-tts",)

# OpenAI documents 50+ languages for its speech models; the product's three are certainly in.
LANGUAGES = ["en", "ru", "ka"]


def base_url(res) -> str:
    return (res.base_url or DEFAULT_BASE_URL).rstrip("/")


def _headers(res) -> dict:
    if not res.api_key:
        raise voice_base.VoiceError("OpenAI API key is not configured for text-to-speech "
                                    "(set one on the connection).", code="invalid_key")
    return {"Authorization": f"Bearer {res.api_key}", "Content-Type": "application/json",
            "Accept": "audio/mpeg"}


def model_caps(model_id: str) -> dict:
    return voice_base.caps(presets=False, style=False, speaker_boost=False, speed=True,
                           language_code=LANGUAGE_IGNORED, max_chars=INPUT_LIMIT)


class OpenAITTS:
    id = "openai"
    default_model = DEFAULT_MODEL
    default_voice = DEFAULT_VOICE

    def model(self, res) -> str:
        return voice_base.own_model(res, DEFAULT_MODEL, foreign_prefixes=("eleven_",))

    async def synthesize(self, res, text: str, *, voice_id: str, model_id: str,
                         language_code: str | None = None,
                         voice_settings: dict | None = None) -> bytes:
        if not voice_id:
            raise voice_base.VoiceError("No TTS voice is configured for this OpenAI connection.",
                                        code="not_configured")
        body: dict = {"model": model_id, "input": (text or "").strip(), "voice": voice_id,
                      "response_format": "mp3"}
        vs = dict(voice_settings or {})
        if vs.get("speed") is not None:
            body["speed"] = float(vs.pop("speed"))
        if vs:
            # The shaper already dropped what caps() disowns; anything still here is a key
            # this provider has no field for — say so rather than silently eating it.
            log.info("openai tts: voice_settings %s have no OpenAI equivalent; ignored",
                     ",".join(sorted(vs)))
        if language_code:
            log.debug("openai tts: no language parameter on this API; %r not sent", language_code)
        instructions = (res.settings or {}).get("instructions")
        if instructions and model_id in _INSTRUCTABLE:
            body["instructions"] = str(instructions)
        resp = await voice_base.http_request(
            "POST", f"{base_url(res)}/audio/speech", "Text-to-speech", vendor="OpenAI",
            timeout=120.0, headers=_headers(res), json=body)
        return resp.content

    async def list_voices(self, res) -> list[dict]:
        return [{"voice_id": v, "name": v.capitalize(), "category": "premade",
                 "preview_url": None} for v in VOICES]

    async def list_models(self, res) -> list[dict]:
        return [public_model(m["model_id"], name=m["name"], description=m["description"],
                             languages=LANGUAGES, caps=model_caps(m["model_id"]))
                for m in MODELS]

    async def caps(self, res, model_id: str) -> dict:
        return model_caps(model_id)

    def defaults_for_language(self, res, lang: str) -> dict:
        # No per-language rule: every OpenAI voice speaks every supported language.
        return {"model": self.model(res), "voice": None, "note": ""}

    def valid_voice_id(self, voice_id: str) -> bool:
        # The id goes into a JSON body, not a URL; the shape check is against the known list
        # plus the same conservative charset, so a connection-configured new voice still works.
        return bool(voice_id) and (voice_id in VOICES
                                   or (voice_id.isascii() and voice_id.replace("-", "").replace(
                                       "_", "").isalnum() and len(voice_id) <= 32))

    def language_voice_ids(self) -> set[str]:
        return set()

    async def probe(self, res) -> dict:
        voice_id = (res.settings or {}).get("voice_id") or DEFAULT_VOICE
        try:
            audio = await self.synthesize(res, "ok", voice_id=voice_id, model_id=self.model(res))
        except voice_base.VoiceError as exc:
            return {"ok": False, "detail": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001 — a probe never raises
            return {"ok": False, "detail": str(exc), "code": "http"}
        return {"ok": True, "detail": f"OpenAI model {self.model(res)} returned {len(audio)} "
                                      f"bytes with voice {voice_id}"}


adapter = OpenAITTS()
