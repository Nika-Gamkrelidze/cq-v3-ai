"""Google Gemini as a speech-to-text adapter.

Gemini has no transcription endpoint. It is a multimodal model: the audio goes in as a part of
a `generateContent` request and the transcript comes back as generated text — so this adapter
asks for the transcript as STRUCTURED OUTPUT (`responseSchema`, the same JSON-schema discipline
the text adapters keep) rather than parsing prose, and it is the model, not a signal-processing
pipeline, that labels speakers and places timestamps.

That has two consequences worth being honest about, both written into the result's `detail`:

  * Timings are SEGMENT-level and approximate. A model can tell you an utterance ran from
    about 0:12 to 0:19; word-by-word timings from it would be fabricated. So each segment is
    returned as ONE `words` entry carrying the whole utterance, and `segments.build_segments`
    treats it as a single word with a speaker and a span — the timeline highlights per turn.
  * Diarization is by ear, not by voiceprint. It is usually right on a two-party call and it is
    the model's judgement; it is not the acoustic clustering Scribe does.

UNTESTED AGAINST THE LIVE API until a key is added — the registry's "Test connection" is the
verification; what tests/test_voice.py pins is the request shape and the response mapping.

How the four transcription settings map (they are ElevenLabs' knobs):
  language_code  → a sentence in the instruction naming the language; the schema also asks the
                   model to report the language it heard, which wins when present.
  diarize        → the instruction asks for per-speaker labels (speaker_0, speaker_1 …) or for
                   one label for everything.
  keyterms       → listed in the instruction as spellings that occur in the audio — the same
                   lever ElevenLabs calls keyterms, expressed the only way a chat model takes it.
  audio_format   → honoured exactly as for Scribe (`services/audio.to_stt_format`).

Payload size: an inline part must keep the whole request under 20 MB, so above a threshold the
audio is pushed through the Files API first (resumable upload → wait for ACTIVE → reference by
URI → delete), which is what makes a long call on a lossless format work at all.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

from .. import audio as audio_mod
from . import voice_base
from .voice_base import silence_wav

log = logging.getLogger("cq")

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
DEFAULT_MODEL = "gemini-2.5-flash"
# Base64 grows bytes by 4/3 and the whole request must stay under 20 MB; 14 MB of raw audio
# leaves room for the instruction and the JSON envelope.
INLINE_MAX_BYTES = 14 * 1024 * 1024
MAX_OUTPUT_TOKENS = 32768      # a dense hour of Georgian is well inside this
FILE_ACTIVE_POLL_S = 2.0
FILE_ACTIVE_TIMEOUT_S = 180.0
DETAIL = ("Gemini transcribes as a language model: speaker labels are by ear and timings are "
          "approximate, per segment rather than per word.")

_LANG_NAMES = {
    "ka": "Georgian", "en": "English", "ru": "Russian", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "tr": "Turkish", "uk": "Ukrainian", "hy": "Armenian",
    "az": "Azerbaijani", "ar": "Arabic", "pt": "Portuguese", "pl": "Polish",
    "kat": "Georgian", "eng": "English", "rus": "Russian",
}

# Gemini's OpenAPI subset: UPPERCASE types, no additionalProperties.
TRANSCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "language_code": {"type": "STRING",
                          "description": "ISO-639-1 code of the language actually spoken"},
        "segments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "speaker": {"type": "STRING", "description": "speaker_0, speaker_1, …"},
                    "start": {"type": "NUMBER", "description": "seconds from the start"},
                    "end": {"type": "NUMBER", "description": "seconds from the start"},
                    "text": {"type": "STRING", "description": "verbatim, original language"},
                },
                "required": ["speaker", "start", "end", "text"],
            },
        },
    },
    "required": ["language_code", "segments"],
}


def _headers(res) -> dict:
    if not res.api_key:
        raise voice_base.VoiceError("Gemini API key is not configured for speech-to-text "
                                    "(set one on the connection).", code="invalid_key")
    return {"x-goog-api-key": res.api_key}


def instruction(*, language_code: str | None, diarize: bool, keyterms: list[str] | None) -> str:
    lines = [
        "Transcribe this audio verbatim, in the language and script actually spoken. "
        "Do not translate, summarise or clean up. Keep numbers exactly as said.",
    ]
    if language_code:
        name = _LANG_NAMES.get(language_code.strip().lower())
        lines.append(f"The audio is in {name}." if name
                     else f"The audio is in the language with code '{language_code}'.")
    if diarize:
        lines.append("Separate the speakers by voice and label them speaker_0, speaker_1, … "
                     "in order of first appearance; one segment per uninterrupted turn.")
    else:
        lines.append("Label every segment speaker_0.")
    if keyterms:
        lines.append("These terms occur in the audio and must be spelled exactly as given: "
                     + "; ".join(k.strip() for k in keyterms if k and k.strip()) + ".")
    lines.append("Give start and end as seconds from the beginning of the recording, and "
                 "report the language you heard as an ISO-639-1 code.")
    return " ".join(lines)


def _mime(content_type: str | None, filename: str | None) -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct.startswith("audio/") or ct.startswith("video/"):
        return ct
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    return {"mp3": "audio/mpeg", "flac": "audio/flac", "wav": "audio/wav", "m4a": "audio/mp4",
            "ogg": "audio/ogg", "opus": "audio/ogg", "webm": "audio/webm", "aac": "audio/aac",
            "mp4": "video/mp4"}.get(ext, "audio/mpeg")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def words_from(body: dict, *, asked_language: str | None) -> tuple[list[dict], str, str | None]:
    """The model's JSON → (words, text, language_code). Each segment is ONE word entry."""
    words: list[dict] = []
    for seg in body.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        words.append({"text": text, "start": _num(seg.get("start")), "end": _num(seg.get("end")),
                      "type": "word",
                      "speaker_id": str(seg.get("speaker") or "speaker_0").strip() or "speaker_0"})
    text = " ".join(w["text"] for w in words)
    lang = body.get("language_code")
    lang = lang.strip().lower() if isinstance(lang, str) and lang.strip() else None
    if lang and len(lang) != 2:
        lang = None
    return words, text, lang or (asked_language.strip().lower()[:2] if asked_language else None)


class GeminiSTT:
    id = "gemini"
    default_model = DEFAULT_MODEL

    def model(self, res) -> str:
        return voice_base.own_model(res, DEFAULT_MODEL,
                                    foreign_prefixes=("scribe", "eleven_", "whisper", "gpt-"))

    # ---- the Files API, for audio too big to inline --------------------------------------
    async def _upload(self, res, data: bytes, mime: str, timeout: float) -> tuple[str, str]:
        start = await voice_base.http_request(
            "POST", UPLOAD_URL, "Speech-to-text upload", vendor="Gemini", timeout=timeout,
            headers={**_headers(res), "X-Goog-Upload-Protocol": "resumable",
                     "X-Goog-Upload-Command": "start",
                     "X-Goog-Upload-Header-Content-Length": str(len(data)),
                     "X-Goog-Upload-Header-Content-Type": mime,
                     "Content-Type": "application/json"},
            json={"file": {"display_name": "cq-transcription"}})
        upload_url = start.headers.get("x-goog-upload-url") or start.headers.get("X-Goog-Upload-URL")
        if not upload_url:
            raise voice_base.VoiceError("Gemini did not return an upload URL for the audio.",
                                        code="upload")
        done = await voice_base.http_request(
            "POST", upload_url, "Speech-to-text upload", vendor="Gemini", timeout=timeout,
            headers={**_headers(res), "X-Goog-Upload-Offset": "0",
                     "X-Goog-Upload-Command": "upload, finalize", "Content-Type": mime},
            content=data)
        info = (done.json() or {}).get("file") or {}
        name, uri, state = info.get("name"), info.get("uri"), info.get("state")
        if not (name and uri):
            raise voice_base.VoiceError("Gemini accepted the upload but returned no file URI.",
                                        code="upload")
        waited = 0.0
        while state and state != "ACTIVE":
            if state == "FAILED":
                raise voice_base.VoiceError("Gemini could not process the uploaded audio.",
                                            code="upload")
            if waited >= FILE_ACTIVE_TIMEOUT_S:
                raise voice_base.VoiceError("Gemini took too long to process the uploaded "
                                            "audio.", code="timeout")
            await asyncio.sleep(FILE_ACTIVE_POLL_S)
            waited += FILE_ACTIVE_POLL_S
            poll = await voice_base.http_request(
                "GET", f"{BASE_URL}/{name}", "Speech-to-text upload", vendor="Gemini",
                timeout=30.0, headers=_headers(res))
            state = (poll.json() or {}).get("state")
        return name, uri

    async def _delete_quietly(self, res, name: str) -> None:
        try:
            await voice_base.http_request("DELETE", f"{BASE_URL}/{name}", "Speech-to-text upload",
                                          vendor="Gemini", timeout=30.0, headers=_headers(res))
        except Exception:  # noqa: BLE001 — the transcript is already in hand
            log.warning("gemini stt: could not delete uploaded file %s", name)

    # ---- the adapter -----------------------------------------------------------------------
    async def transcribe(self, res, audio: bytes, filename: str | None,
                         content_type: str | None, *, language_code: str | None = None,
                         diarize: bool = True, keyterms: list[str] | None = None,
                         audio_format: str | None = None,
                         timeout: float | None = None) -> dict:
        model = self.model(res)
        payload = await audio_mod.to_stt_format(audio, filename or "audio", content_type or "",
                                                audio_format)
        mime = _mime(payload.content_type, payload.filename)
        timeout = timeout or 300.0
        uploaded: str | None = None
        if len(payload.data) <= INLINE_MAX_BYTES:
            audio_part = {"inlineData": {"mimeType": mime,
                                         "data": base64.b64encode(payload.data).decode("ascii")}}
        else:
            log.info("gemini stt: %d bytes exceeds the inline limit; using the Files API",
                     len(payload.data))
            uploaded, uri = await self._upload(res, payload.data, mime, timeout)
            audio_part = {"fileData": {"mimeType": mime, "fileUri": uri}}
        body = {
            "contents": [{"role": "user", "parts": [
                audio_part,
                {"text": instruction(language_code=language_code, diarize=diarize,
                                     keyterms=keyterms)},
            ]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
                "responseSchema": TRANSCRIPT_SCHEMA,
            },
        }
        try:
            resp = await voice_base.http_request(
                "POST", f"{BASE_URL}/models/{model}:generateContent", "Speech-to-text",
                vendor="Gemini", timeout=timeout,
                headers={**_headers(res), "Content-Type": "application/json"}, json=body)
        finally:
            if uploaded:
                await self._delete_quietly(res, uploaded)
        data = resp.json() or {}
        candidates = data.get("candidates") or []
        cand = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        if cand.get("finishReason") == "MAX_TOKENS":
            raise voice_base.VoiceError("Gemini stopped before the end of the transcript "
                                        "(output limit). Split the recording or use a shorter "
                                        "one.", code="truncated")
        parts = ((cand.get("content") or {}).get("parts") or [])
        raw = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            raise voice_base.VoiceError(f"Gemini returned a transcript that is not valid JSON: "
                                        f"{exc}", code="bad_response") from exc
        words, text, lang = words_from(parsed if isinstance(parsed, dict) else {},
                                       asked_language=language_code)
        return {"text": text, "language_code": lang, "words": words, "detail": DETAIL}

    async def probe(self, res) -> dict:
        try:
            await self.transcribe(res, silence_wav(), "probe.wav", "audio/wav",
                                  diarize=False, audio_format="original", timeout=60.0)
        except voice_base.VoiceError as exc:
            return {"ok": False, "detail": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001 — a probe never raises
            return {"ok": False, "detail": str(exc), "code": "http"}
        return {"ok": True,
                "detail": f"Gemini model {self.model(res)} accepted a 0.4 s probe clip. {DETAIL}"}


adapter = GeminiSTT()
