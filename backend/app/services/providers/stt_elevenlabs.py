"""ElevenLabs Scribe as an STT adapter — a wrapper over `services/elevenlabs.py::transcribe`.

The request that function builds is pinned byte for byte by tests/test_transcription_settings.py
and is NOT rebuilt here: this module's only job is to hand it the key and model the resolver
chose for this tenant, and the four settings `services/transcription.py` resolved.

`elevenlabs` is imported as a MODULE and called by attribute, on purpose: the existing tests
stub `elevenlabs.transcribe` / `elevenlabs._request` by module attribute, and that seam has to
keep working through the adapter.
"""
from __future__ import annotations

from .. import elevenlabs
from . import voice_base
from .voice_base import silence_wav

DEFAULT_MODEL = "scribe_v1"


class ElevenLabsSTT:
    id = "elevenlabs"
    default_model = DEFAULT_MODEL

    def model(self, res) -> str:
        return voice_base.own_model(res, DEFAULT_MODEL,
                                    foreign_prefixes=("whisper", "gpt-", "gemini"))

    async def transcribe(self, res, audio: bytes, filename: str | None,
                         content_type: str | None, *, language_code: str | None = None,
                         diarize: bool = True, keyterms: list[str] | None = None,
                         audio_format: str | None = None,
                         timeout: float | None = None) -> dict:
        kw: dict = {"language_code": language_code, "diarize": diarize,
                    "keyterms": keyterms, "audio_format": audio_format}
        if timeout is not None:
            kw["timeout"] = timeout
        return await elevenlabs.transcribe(audio, filename or "audio",
                                           content_type or "application/octet-stream",
                                           res.api_key, self.model(res), **kw)

    async def probe(self, res) -> dict:
        """A real POST /v1/speech-to-text on 0.4 s of silence — the only proof of the
        speech_to_text permission — with the code-default settings (diarize on, no language,
        mono 16 kHz MP3), which is the request every customer's call makes."""
        try:
            out = await self.transcribe(res, silence_wav(), "probe.wav", "audio/wav",
                                        timeout=60.0)
        except voice_base.VoiceError as exc:
            if exc.code == "http" and exc.status in (400, 422):
                # Authorised, payload rejected: a working key, an unexpected model or format.
                return {"ok": False, "detail": f"model {self.model(res)} rejected the probe "
                                               f"clip: {exc}", "code": "probe_rejected"}
            return {"ok": False, "detail": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001 — a probe never raises
            return {"ok": False, "detail": str(exc), "code": "http"}
        return {"ok": True,
                "detail": f"ElevenLabs model {self.model(res)} accepted a 0.4 s probe clip "
                          f"(lang={out.get('language_code') or 'n/a'})"}


adapter = ElevenLabsSTT()
