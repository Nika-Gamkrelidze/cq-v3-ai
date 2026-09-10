"""Google Gemini as a speech-to-text adapter — two Google APIs behind one adapter.

Google sells speech-to-text two ways, and the connection's MODEL ID decides which one it gets:

  * ``gemini-3.5-transcribe`` (the default) — a DEDICATED transcription model on the
    Interactions API (``POST /v1beta/interactions``). Native speaker diarization, word-level
    timestamps, BCP-47 language hints, custom vocabulary, 85+ languages (Georgian is
    ``ka-GE``). Recordings up to one hour; 30 minutes when diarization or timestamps are on.
  * any other id (``gemini-3.8-flash``, ``gemini-2.5-pro``, …) — a multimodal CHAT model on
    ``generateContent``: the audio goes in as a part and the transcript comes back as
    structured output against a schema (the same JSON-schema discipline the text adapters
    keep). Timings are SEGMENT-level and approximate — a model can say an utterance ran from
    about 0:12 to 0:19; word timings from it would be fabricated, so each segment is ONE
    ``words`` entry — and speakers are labelled by ear, not by voiceprint. Kept for the case
    where a chat model's reading of context beats the ASR model on a hard recording.

``gemini-3.5-transcribe-live`` is a WebSocket streaming model with no unary endpoint; the
adapter refuses it in words rather than sending an HTTP request that cannot succeed.

How the four transcription settings map on the Transcribe model (the knobs are ElevenLabs'):
  language_code  → ``language_codes: [<BCP-47>]`` (``ka`` → ``ka-GE``). Unset = automatic
                   detection, which also follows code-switching mid-call.
  diarize        → ``mode.diarization_mode = "speaker"`` plus word timestamps.
  keyterms       → ``custom_vocabulary`` — BUT Google rejects a request that combines it with
                   diarization or timestamps. So speaker separation wins when both are set
                   (the timeline and per-speaker scoring are what this product is built on)
                   and the key terms are dropped with a warning; with speaker separation off
                   the key terms are sent and the transcript comes back without timings, and
                   the analysis works from the text alone (``segments_from_text``).
  audio_format   → honoured exactly as for Scribe (``services/audio.to_stt_format``).
On a chat model the same settings become sentences in the instruction, the only way a chat
model takes them.

The transcription is sent with ``store: false``: Google keeps Interactions by default for
server-side conversation state, which a customer's call recording has no use for.

Payload size: a short clip is inlined as base64; anything bigger goes through the Files API
(resumable upload → wait ACTIVE → reference by URI → delete). Google's guidance for the
Transcribe model is to upload anything longer than a few seconds, so its inline threshold is
deliberately small; a ``generateContent`` request must simply stay under 20 MB.

UNTESTED AGAINST THE LIVE API until a key is added — the registry's "Test connection" is the
verification; what tests/test_voice.py pins is the request shapes and the response mappings.
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
INTERACTIONS_URL = f"{BASE_URL}/interactions"
DEFAULT_MODEL = "gemini-3.5-transcribe"
# generateContent: base64 grows bytes by 4/3 and the whole request must stay under 20 MB;
# 14 MB of raw audio leaves room for the instruction and the JSON envelope.
INLINE_MAX_BYTES = 14 * 1024 * 1024
# Transcribe: Google says to upload "files longer than a few seconds". 1 MB of the default
# mono 16 kHz MP3 is a few minutes — the probe clip stays inline, a real call is uploaded.
TRANSCRIBE_INLINE_MAX_BYTES = 1024 * 1024
MAX_OUTPUT_TOKENS = 32768       # a dense hour of Georgian is well inside this
FILE_ACTIVE_POLL_S = 2.0
FILE_ACTIVE_TIMEOUT_S = 180.0

DETAIL_GENERATE = ("Gemini transcribes as a language model: speaker labels are by ear and "
                   "timings are approximate, per segment rather than per word.")
DETAIL = DETAIL_GENERATE        # the name the first version exported
DETAIL_TRANSCRIBE = "Gemini Transcribe labels speakers and times every word natively."
NOTE_TERMS_DROPPED = (" Key terms were not sent: Gemini Transcribe cannot combine custom "
                      "vocabulary with word timings, and the timings are kept — they are what "
                      "the timeline, per-speaker scoring and Voice tone are built on. The "
                      "language hint does the accuracy work here.")
NOTE_TERMS_RULE = (" Key terms are not sent on this model; word timings are kept instead. "
                   "Use ElevenLabs Scribe for a recording that needs key terms.")

_LANG_NAMES = {
    "ka": "Georgian", "en": "English", "ru": "Russian", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "tr": "Turkish", "uk": "Ukrainian", "hy": "Armenian",
    "az": "Azerbaijani", "ar": "Arabic", "pt": "Portuguese", "pl": "Polish",
    "kat": "Georgian", "eng": "English", "rus": "Russian",
}

# The Transcribe model takes BCP-47 tags with a region (the codes on Google's supported-
# languages table). Our settings hold ISO-639-1/-3 codes, ElevenLabs' convention.
_BCP47 = {
    "ka": "ka-GE", "kat": "ka-GE", "en": "en-US", "eng": "en-US", "ru": "ru-RU", "rus": "ru-RU",
    "de": "de-DE", "fr": "fr-FR", "es": "es-ES", "it": "it-IT", "tr": "tr-TR", "uk": "uk-UA",
    "hy": "hy-AM", "az": "az-AZ", "ar": "ar-EG", "pt": "pt-PT", "pl": "pl-PL", "he": "he-IL",
    "el": "el-GR", "kk": "kk-KZ", "ro": "ro-RO", "bg": "bg-BG", "cs": "cs-CZ", "nl": "nl-NL",
    "sv": "sv-SE", "fi": "fi-FI", "da": "da-DK", "hu": "hu-HU", "ja": "ja-JP", "ko": "ko-KR",
    "hi": "hi-IN", "id": "id-ID", "vi": "vi-VN", "th": "th-TH", "fa": "fa-IR", "uz": "uz-UZ",
}

# The Interactions API's audio mime type is an ENUM; the common aliases mapped onto it.
_INTERACTIONS_MIME = {
    "audio/mp4": "audio/m4a", "audio/x-m4a": "audio/m4a", "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav", "audio/vnd.wave": "audio/wav", "audio/x-flac": "audio/flac",
    "audio/x-aiff": "audio/aiff", "audio/mp3": "audio/mp3",
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


def is_transcribe_model(model: str | None) -> bool:
    """The dedicated ASR family (``gemini-3.5-transcribe``, later versions), by name."""
    return "transcribe" in (model or "").lower()


def bcp47(code: str | None) -> str | None:
    """``ka`` → ``ka-GE``; a tag that already carries a region passes through; an unknown
    bare code is sent as it is (BCP-47 allows a bare language subtag) and Google's answer
    says whether it took it."""
    c = (code or "").strip()
    if not c:
        return None
    if "-" in c or "_" in c:
        return c.replace("_", "-")
    return _BCP47.get(c.lower(), c.lower())


def transcription_config(*, language_code: str | None, diarize: bool,
                         keyterms: list[str] | None) -> tuple[dict, str]:
    """The Transcribe model's ``transcription_config`` for our settings, plus the sentence
    that says what could not be honoured (empty when everything was). Pure.

    WORD TIMINGS ALWAYS WIN. Google rejects ``custom_vocabulary`` in the same request as
    diarization or word timestamps, so on this model key terms and timings are exclusive —
    and timings are worth more than a spelling hint, because THREE downstream features are
    built on them and none of them fails loudly:

      * the player timeline (evidence spans are placed by segment index → seconds),
      * per-speaker rubric scoring and fact-check attribution,
      * Voice tone, whose per-segment prosody needs a start and an end for every turn and
        otherwise reports `no_timestamps` — a recording that simply goes quiet.

    An earlier version sent the key terms whenever speaker separation happened to be off.
    That traded all three for a vocabulary hint, silently, on a per-file switch nobody
    associated with the timeline. So key terms are never sent on this model; the language hint
    (``ka-GE``) does the Georgian work, and the caller is told in words.
    """
    tc: dict = {}
    lang = bcp47(language_code)
    if lang:
        tc["language_codes"] = [lang]
    mode = {"type": "verbatim", "timestamp_granularities": ["word"]}
    if diarize:
        mode["diarization_mode"] = "speaker"
    tc["mode"] = mode
    terms = {(k or "").strip() for k in (keyterms or [])}
    terms.discard("")
    if terms:
        log.warning("gemini transcribe: %d key terms not sent — Google rejects custom "
                    "vocabulary alongside word timestamps, and the timings win", len(terms))
        return tc, NOTE_TERMS_DROPPED
    return tc, ""


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


def _secs(v) -> float | None:
    """``"0.450s"`` (the Interactions API's Duration) or a bare number → seconds; None
    for anything else, never a fabricated 0."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v >= 0 else None
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("s"):
            s = s[:-1].strip()
        return _num(s)
    return None


def words_from(body: dict, *, asked_language: str | None) -> tuple[list[dict], str, str | None]:
    """The chat model's JSON → (words, text, language_code). Each segment is ONE word entry."""
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


def words_from_interaction(data: dict) -> tuple[list[dict], str]:
    """An Interaction → (words, text). The transcript is the text content of the model's
    output steps; with timestamps or diarization on, every word arrives as a ``word_info``
    annotation on it. Google's speaker labels (``spk_1``, ``spk_2``) are renamed to the
    house ``speaker_0``, ``speaker_1`` in order of first appearance, so the first voice heard
    is ``speaker_0`` on every provider. Without annotations ``words`` is empty and the
    analysis falls back to the text (``analysis.py`` → ``segments_from_text``)."""
    texts: list[str] = []
    words: list[dict] = []
    labels: dict[str, str] = {}

    def speaker(label) -> str:
        label = str(label or "").strip()
        if not label:
            return "speaker_0"
        if label not in labels:
            labels[label] = f"speaker_{len(labels)}"
        return labels[label]

    for step in data.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") not in (None, "model_output"):
            continue
        for content in step.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "text":
                continue
            t = str(content.get("text") or "").strip()
            if t:
                texts.append(t)
            for ann in content.get("annotations") or []:
                if not isinstance(ann, dict) or ann.get("type") != "word_info":
                    continue
                w = str(ann.get("text") or "").strip()
                if not w:
                    continue
                words.append({"text": w, "start": _secs(ann.get("start_offset")),
                              "end": _secs(ann.get("end_offset")), "type": "word",
                              "speaker_id": speaker(ann.get("speaker"))})
    text = "\n".join(texts) or " ".join(w["text"] for w in words)
    return words, text


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
        if is_transcribe_model(model) and model.lower().endswith("-live"):
            raise voice_base.VoiceError(
                f"{model} is Google's streaming model (WebSockets only) and cannot transcribe "
                f"a recording; set the connection's model to {DEFAULT_MODEL}.",
                code="invalid_model")
        payload = await audio_mod.to_stt_format(audio, filename or "audio", content_type or "",
                                                audio_format)
        mime = _mime(payload.content_type, payload.filename)
        timeout = timeout or 300.0
        if is_transcribe_model(model):
            return await self._transcribe_interactions(
                res, model, payload.data, mime, language_code=language_code, diarize=diarize,
                keyterms=keyterms, timeout=timeout)
        return await self._transcribe_generate(
            res, model, payload.data, mime, language_code=language_code, diarize=diarize,
            keyterms=keyterms, timeout=timeout)

    async def _transcribe_interactions(self, res, model: str, data: bytes, mime: str, *,
                                       language_code: str | None, diarize: bool,
                                       keyterms: list[str] | None, timeout: float) -> dict:
        """The dedicated ASR model: ``POST /interactions`` with a ``transcription_config``."""
        mime = _INTERACTIONS_MIME.get(mime, mime)
        tc, note = transcription_config(language_code=language_code, diarize=diarize,
                                        keyterms=keyterms)
        uploaded: str | None = None
        if len(data) <= TRANSCRIBE_INLINE_MAX_BYTES:
            audio_part = {"type": "audio", "mime_type": mime,
                          "data": base64.b64encode(data).decode("ascii")}
        else:
            log.info("gemini transcribe: %d bytes; using the Files API", len(data))
            uploaded, uri = await self._upload(res, data, mime, timeout)
            audio_part = {"type": "audio", "mime_type": mime, "uri": uri}
        body = {
            "model": model,
            "input": [audio_part],
            "store": False,
            "generation_config": {"transcription_config": tc},
        }
        try:
            resp = await voice_base.http_request(
                "POST", INTERACTIONS_URL, "Speech-to-text", vendor="Gemini", timeout=timeout,
                headers={**_headers(res), "Content-Type": "application/json"}, json=body)
        finally:
            if uploaded:
                await self._delete_quietly(res, uploaded)
        payload = resp.json()
        data_out = payload if isinstance(payload, dict) else {}
        words, text = words_from_interaction(data_out)
        status = str(data_out.get("status") or "completed")
        if status != "completed" and not text:
            err = data_out.get("error")
            msg = str(err.get("message") or "") if isinstance(err, dict) else str(err or "")
            raise voice_base.VoiceError(
                f"Gemini did not complete the transcription (status {status!r}"
                f"{': ' + msg if msg else ''}).", code="bad_response")
        lang = language_code.strip().lower()[:2] if language_code and language_code.strip() \
            else None
        return {"text": text, "language_code": lang, "words": words,
                "detail": DETAIL_TRANSCRIBE + note}

    async def _transcribe_generate(self, res, model: str, data: bytes, mime: str, *,
                                   language_code: str | None, diarize: bool,
                                   keyterms: list[str] | None, timeout: float) -> dict:
        """A chat model: ``generateContent`` with the audio as a part and a transcript schema."""
        uploaded: str | None = None
        if len(data) <= INLINE_MAX_BYTES:
            audio_part = {"inlineData": {"mimeType": mime,
                                         "data": base64.b64encode(data).decode("ascii")}}
        else:
            log.info("gemini stt: %d bytes exceeds the inline limit; using the Files API",
                     len(data))
            uploaded, uri = await self._upload(res, data, mime, timeout)
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
        out = resp.json() or {}
        candidates = out.get("candidates") or []
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
        return {"text": text, "language_code": lang, "words": words, "detail": DETAIL_GENERATE}

    async def probe(self, res) -> dict:
        model = self.model(res)
        try:
            await self.transcribe(res, silence_wav(), "probe.wav", "audio/wav",
                                  diarize=False, audio_format="original", timeout=60.0)
        except voice_base.VoiceError as exc:
            return {"ok": False, "detail": str(exc), "code": exc.code}
        except Exception as exc:  # noqa: BLE001 — a probe never raises
            return {"ok": False, "detail": str(exc), "code": "http"}
        about = DETAIL_TRANSCRIBE + NOTE_TERMS_RULE if is_transcribe_model(model) \
            else DETAIL_GENERATE
        return {"ok": True, "detail": f"Gemini model {model} accepted a 0.4 s probe clip. {about}"}


adapter = GeminiSTT()
