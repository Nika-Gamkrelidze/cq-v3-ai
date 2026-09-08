"""OpenAI speech-to-text (`POST /v1/audio/transcriptions`) as an STT adapter.

UNTESTED AGAINST THE LIVE API until a key is added — the registry's "Test connection" button is
how it gets verified. What is pinned (tests/test_voice.py) is the request shape: multipart with
`file`, `model`, `language` when the settings resolved one, and word timestamps asked for on the
models that offer them.

How the four transcription settings map (they are ElevenLabs' knobs; OpenAI has a different set):

  language_code  → `language`, which OpenAI wants as ISO-639-1. A 3-letter code the tenant set
                   for Scribe ("kat") is mapped where we know the pair, else dropped with a log
                   line — sending it would 400 every call on this connection.
  diarize        → NOT OFFERED by this endpoint. The words come back without speaker ids;
                   `segments.build_segments` gives them all the default speaker, so the
                   per-speaker analyses degrade to one speaker rather than crash. Logged, and
                   named in the result's `detail`.
  keyterms       → `prompt`: OpenAI's documented way to bias recognition toward vocabulary.
  audio_format   → honoured exactly as for Scribe (`services/audio.to_stt_format`); the
                   ElevenLabs-only `file_format` hint is simply not sent.

Word timings: `whisper-1` returns them (`verbose_json` + `timestamp_granularities[]=word`); the
`gpt-4o-transcribe` family only offers `json`/`text`, so those return `words: []` and the
timeline falls back to line-based segments — the same path a Scribe response without words
already takes.
"""
from __future__ import annotations

import logging

from .. import audio as audio_mod
from . import voice_base
from .voice_base import silence_wav

log = logging.getLogger("cq")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "whisper-1"
NO_DIARIZATION = "OpenAI transcription does not diarize; every word is attributed to one speaker."

# ISO-639-3 → ISO-639-1 for the codes a Scribe-configured tenant is likely to have set.
_ISO3_TO_1 = {
    "kat": "ka", "eng": "en", "rus": "ru", "deu": "de", "ger": "de", "fra": "fr", "fre": "fr",
    "spa": "es", "ita": "it", "por": "pt", "tur": "tr", "ukr": "uk", "pol": "pl", "nld": "nl",
    "dut": "nl", "ara": "ar", "zho": "zh", "chi": "zh", "jpn": "ja", "kor": "ko", "hin": "hi",
    "hye": "hy", "aze": "az", "heb": "he", "ell": "el", "gre": "el", "ces": "cs", "cze": "cs",
    "ron": "ro", "rum": "ro", "hun": "hu", "swe": "sv", "fin": "fi", "dan": "da", "nor": "no",
    "bul": "bg", "srp": "sr", "hrv": "hr", "slk": "sk", "slo": "sk", "slv": "sl", "lit": "lt",
    "lav": "lv", "est": "et", "fas": "fa", "per": "fa", "urd": "ur", "ind": "id", "vie": "vi",
    "tha": "th", "kaz": "kk", "uzb": "uz", "bel": "be", "cat": "ca", "msa": "ms", "may": "ms",
    "tam": "ta", "ben": "bn",
}

# `verbose_json` reports the detected language as a lowercase English NAME ("georgian").
_NAME_TO_CODE = {
    "english": "en", "russian": "ru", "georgian": "ka", "german": "de", "french": "fr",
    "spanish": "es", "italian": "it", "portuguese": "pt", "turkish": "tr", "ukrainian": "uk",
    "polish": "pl", "dutch": "nl", "arabic": "ar", "chinese": "zh", "japanese": "ja",
    "korean": "ko", "hindi": "hi", "armenian": "hy", "azerbaijani": "az", "hebrew": "he",
    "greek": "el", "czech": "cs", "romanian": "ro", "hungarian": "hu", "swedish": "sv",
    "finnish": "fi", "danish": "da", "norwegian": "no", "bulgarian": "bg", "serbian": "sr",
    "croatian": "hr", "slovak": "sk", "slovenian": "sl", "lithuanian": "lt", "latvian": "lv",
    "estonian": "et", "persian": "fa", "urdu": "ur", "indonesian": "id", "vietnamese": "vi",
    "thai": "th", "kazakh": "kk", "uzbek": "uz", "belarusian": "be", "catalan": "ca",
    "malay": "ms", "tamil": "ta", "bengali": "bn",
}


def base_url(res) -> str:
    return (res.base_url or DEFAULT_BASE_URL).rstrip("/")


def _headers(res) -> dict:
    if not res.api_key:
        raise voice_base.VoiceError("OpenAI API key is not configured for speech-to-text "
                                    "(set one on the connection).", code="invalid_key")
    return {"Authorization": f"Bearer {res.api_key}"}


def iso639_1(code: str | None) -> str | None:
    """`language` for OpenAI: a 2-letter code, or None when the value cannot be expressed."""
    if not code:
        return None
    base = code.strip().lower().replace("_", "-").split("-", 1)[0]
    if len(base) == 2:
        return base
    mapped = _ISO3_TO_1.get(base)
    if not mapped:
        log.warning("openai stt: language_code %r has no ISO-639-1 form known here; letting "
                    "the model detect the language", code)
    return mapped


def language_code_of(value, fallback: str | None) -> str | None:
    """The code for what the response reported (a name for whisper-1, sometimes a code,
    often nothing), else what we asked for."""
    if isinstance(value, str) and value.strip():
        v = value.strip().lower()
        if len(v) == 2:
            return v
        if v in _NAME_TO_CODE:
            return _NAME_TO_CODE[v]
    return fallback


def wants_word_timestamps(model: str) -> bool:
    return model.startswith("whisper")


class OpenAISTT:
    id = "openai"
    default_model = DEFAULT_MODEL

    def model(self, res) -> str:
        return voice_base.own_model(res, DEFAULT_MODEL, foreign_prefixes=("scribe", "eleven_"))

    async def transcribe(self, res, audio: bytes, filename: str | None,
                         content_type: str | None, *, language_code: str | None = None,
                         diarize: bool = True, keyterms: list[str] | None = None,
                         audio_format: str | None = None,
                         timeout: float | None = None) -> dict:
        model = self.model(res)
        payload = await audio_mod.to_stt_format(audio, filename or "audio", content_type or "",
                                                audio_format)
        files = {"file": (payload.filename or "audio", payload.data,
                          payload.content_type or "application/octet-stream")}
        data: dict = {"model": model}
        lang = iso639_1(language_code)
        if lang:
            data["language"] = lang
        if keyterms:
            data["prompt"] = ", ".join(keyterms)
        if wants_word_timestamps(model):
            data["response_format"] = "verbose_json"
            data["timestamp_granularities[]"] = "word"
        else:
            data["response_format"] = "json"
        if diarize:
            log.info("openai stt (%s): %s", model, NO_DIARIZATION)
        resp = await voice_base.http_request(
            "POST", f"{base_url(res)}/audio/transcriptions", "Speech-to-text", vendor="OpenAI",
            timeout=timeout or 300.0, headers=_headers(res), data=data, files=files)
        body = resp.json() or {}
        words = [
            {"text": w.get("word"), "start": w.get("start"), "end": w.get("end"),
             "type": "word"}
            for w in (body.get("words") or []) if isinstance(w, dict) and w.get("word")
        ]
        return {
            "text": body.get("text", "") or "",
            "language_code": language_code_of(body.get("language"), lang),
            "words": words,
            "detail": NO_DIARIZATION if diarize else "",
        }

    async def probe(self, res) -> dict:
        try:
            out = await self.transcribe(res, silence_wav(), "probe.wav", "audio/wav",
                                        diarize=False, audio_format="original", timeout=60.0)
        except voice_base.VoiceError as exc:
            return {"ok": False, "detail": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001 — a probe never raises
            return {"ok": False, "detail": str(exc), "code": "http"}
        return {"ok": True,
                "detail": f"OpenAI model {self.model(res)} accepted a 0.4 s probe clip "
                          f"(lang={out.get('language_code') or 'n/a'}). {NO_DIARIZATION}"}


adapter = OpenAISTT()
