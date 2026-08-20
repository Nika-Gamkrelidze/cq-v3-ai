"""ElevenLabs integration: speech-to-text (Scribe) and text-to-speech.

Thin async wrappers over the ElevenLabs REST API using httpx. The API key is passed
in per call (it comes from runtime settings, not a module-level constant).

Every call goes through `_request`, so a failure always surfaces as an ElevenLabsError
carrying a machine `code` — and, for a restricted key, the `scope` ElevenLabs names as
missing — instead of a raw JSON blob pasted into the user's toast.
"""
import io
import re
import wave

import httpx

BASE_URL = "https://api.elevenlabs.io/v1"

# The key permission each operation needs. An ElevenLabs key can be restricted to any
# subset of these, which is why one green "ElevenLabs" row proves nothing about the rest:
# GET /v1/voices needs voices_read (or no permission at all), while the two operations the
# product actually runs on need speech_to_text and text_to_speech.
SCOPE_VOICES = "voices_read"
SCOPE_STT = "speech_to_text"
SCOPE_TTS = "text_to_speech"

# "The API key you used is missing the permission speech_to_text to execute this operation."
_MISSING_SCOPE_RE = re.compile(r"permission[:\s]+['\"]?([a-z][a-z0-9_]{2,40})", re.I)


class ElevenLabsError(RuntimeError):
    """A classified ElevenLabs failure.

    `code`  — missing_permission | invalid_key | quota | blocked | transport | http
    `scope` — the permission ElevenLabs named as missing, when it named one
    `raw`   — first 500 chars of the response body (kept for the admin panel / job row)
    """

    def __init__(self, message: str, *, status: int | None = None, code: str = "http",
                 scope: str | None = None, raw: str = ""):
        super().__init__(message)
        self.status, self.code, self.scope, self.raw = status, code, scope, raw


def _headers(api_key: str) -> dict:
    if not api_key:
        raise ElevenLabsError("ElevenLabs API key is not configured (set it in the admin panel).",
                              code="invalid_key")
    return {"xi-api-key": api_key}


def _api_error(resp: httpx.Response, action: str, scope: str | None = None) -> ElevenLabsError:
    """Map an ElevenLabs error response onto an actionable message.

    A restricted key is reported two different ways depending on the endpoint's vintage:
      401 {"detail":{"status":"missing_permissions","message":"... missing the permission
          speech_to_text ..."}}                                  (legacy shape, still live)
      403 {"detail":{"status":"insufficient_permissions", ...}}  (documented shape)
    Both fold into code="missing_permission", with the scope lifted out of the message so
    the operator is told exactly which checkbox to tick.
    """
    raw = resp.text[:500]
    status_str = msg = ""
    try:
        detail = (resp.json() or {}).get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        status_str = str(detail.get("status") or detail.get("code") or "").lower()
        msg = str(detail.get("message") or "")
    elif isinstance(detail, str):
        msg = detail

    if status_str in ("missing_permissions", "insufficient_permissions"):
        found = _MISSING_SCOPE_RE.search(msg)
        missing = found.group(1) if found else (scope or "required")
        return ElevenLabsError(
            f"{action} was refused: this ElevenLabs API key is missing the '{missing}' "
            f"permission. In ElevenLabs open Settings → API Keys → Edit on that key, enable "
            f"'{missing}', save, then re-test here.",
            status=resp.status_code, code="missing_permission", scope=missing, raw=raw)
    if status_str in ("detected_unusual_activity", "abuse_detected"):
        return ElevenLabsError(
            f"{action} was blocked by ElevenLabs as unusual activity — free-tier generation "
            f"is disabled for this account (typical from data-centre or VPN IPs). A paid plan "
            f"is required; key permissions are not the problem.",
            status=resp.status_code, code="blocked", raw=raw)
    if status_str in ("quota_exceeded", "insufficient_credits") or resp.status_code == 402:
        return ElevenLabsError(
            f"{action} failed: the ElevenLabs account is out of credits for this operation.",
            status=resp.status_code, code="quota", raw=raw)
    if status_str in ("invalid_api_key", "missing_api_key", "unauthorized",
                      "needs_authorization") or resp.status_code == 401:
        return ElevenLabsError(
            f"{action} failed: ElevenLabs did not accept the API key. Paste a current key in "
            f"the admin panel (Integrations) and save.",
            status=resp.status_code, code="invalid_key", raw=raw)
    # Everything else — including a 403 from the key's IP allowlist — keeps the raw body.
    return ElevenLabsError(f"{action} failed ({resp.status_code}): {raw}",
                           status=resp.status_code, raw=raw)


async def _request(method: str, path: str, action: str, scope: str | None = None, *,
                   timeout: float, **kw) -> httpx.Response:
    """The single place every ElevenLabs call is made, so transport and HTTP failures both
    come back classified rather than as a bare httpx exception string."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.request(method, f"{BASE_URL}{path}", **kw)
        except httpx.RequestError as exc:
            raise ElevenLabsError(
                f"{action} could not reach ElevenLabs ({exc.__class__.__name__}). Check the "
                f"server's outbound network/DNS.", code="transport") from exc
    if resp.status_code >= 400:
        raise _api_error(resp, action, scope)
    return resp


def silence_wav(seconds: float = 0.4, rate: int = 16000) -> bytes:
    """~13 KB of mono 16-bit digital silence, built in-process.

    Probe payload only. It is the cheapest thing that still exercises the REAL
    speech-to-text endpoint — the only way to prove the speech_to_text permission, since
    ElevenLabs has no endpoint that reports a key's own scopes.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


async def transcribe(audio: bytes, filename: str, content_type: str, api_key: str,
                     model_id: str = "scribe_v1", timeout: float = 300.0) -> dict:
    """Transcribe an audio file with speaker diarization. Returns {text, language_code}.
    Any input format (or a video) is first transcoded to mono 16 kHz MP3 for reliability."""
    from .audio import to_stt_format
    audio, filename, content_type = await to_stt_format(audio, filename, content_type)
    files = {"file": (filename or "audio", audio, content_type or "application/octet-stream")}
    data = {"model_id": model_id, "diarize": "true", "tag_audio_events": "true"}
    resp = await _request("POST", "/speech-to-text", "Speech-to-text", SCOPE_STT,
                          timeout=timeout, headers=_headers(api_key), data=data, files=files)
    body = resp.json()
    return {
        "text": body.get("text", ""),
        "language_code": body.get("language_code"),
        "words": body.get("words", []),
    }


async def text_to_speech(text: str, api_key: str, voice_id: str,
                         model_id: str = "eleven_multilingual_v2",
                         language_code: str | None = None,
                         output_format: str = "mp3_44100_128",
                         timeout: float = 120.0) -> bytes:
    """Synthesize speech. Returns MP3 bytes.

    Mirrors the request shape proven to work for Georgian in the reference project:
    a minimal body (text + model_id, no forced voice_settings) plus an output_format
    query param. `language_code` is included only when the caller knows the model
    accepts it — some models/languages (e.g. Georgian) reject language_code with a 400.
    """
    if not voice_id:
        raise ElevenLabsError("No TTS voice is configured (set one in the admin panel).")
    payload = {"text": (text or "").strip(), "model_id": model_id}
    if language_code:
        payload["language_code"] = language_code
    resp = await _request(
        "POST", f"/text-to-speech/{voice_id}", "Text-to-speech", SCOPE_TTS, timeout=timeout,
        params={"output_format": output_format},
        headers={**_headers(api_key), "Accept": "audio/mpeg", "Content-Type": "application/json"},
        json=payload)
    return resp.content


async def list_voices(api_key: str) -> list[dict]:
    """Return available voices as [{voice_id, name, category}]."""
    resp = await _request("GET", "/voices", "Listing voices", SCOPE_VOICES,
                          timeout=30.0, headers=_headers(api_key))
    voices = resp.json().get("voices", [])
    return [
        {
            "voice_id": v.get("voice_id"),
            "name": v.get("name"),
            "category": v.get("category"),
            # Pre-generated sample clip hosted by ElevenLabs — free to play (zero credits).
            "preview_url": v.get("preview_url"),
        }
        for v in voices
    ]
