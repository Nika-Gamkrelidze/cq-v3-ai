"""ElevenLabs integration: speech-to-text (Scribe) and text-to-speech.

Thin async wrappers over the ElevenLabs REST API using httpx. The API key is passed
in per call (it comes from runtime settings, not a module-level constant).
"""
import httpx

BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsError(RuntimeError):
    pass


def _headers(api_key: str) -> dict:
    if not api_key:
        raise ElevenLabsError("ElevenLabs API key is not configured (set it in the admin panel).")
    return {"xi-api-key": api_key}


async def transcribe(audio: bytes, filename: str, content_type: str, api_key: str,
                     model_id: str = "scribe_v1") -> dict:
    """Transcribe an audio file with speaker diarization. Returns {text, language_code}.
    Any input format (or a video) is first transcoded to mono 16 kHz MP3 for reliability."""
    from .audio import to_stt_format
    audio, filename, content_type = await to_stt_format(audio, filename, content_type)
    files = {"file": (filename or "audio", audio, content_type or "application/octet-stream")}
    data = {"model_id": model_id, "diarize": "true", "tag_audio_events": "true"}
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{BASE_URL}/speech-to-text",
            headers=_headers(api_key),
            data=data,
            files=files,
        )
    if resp.status_code >= 400:
        raise ElevenLabsError(f"Speech-to-text failed ({resp.status_code}): {resp.text[:500]}")
    body = resp.json()
    return {
        "text": body.get("text", ""),
        "language_code": body.get("language_code"),
        "words": body.get("words", []),
    }


async def text_to_speech(text: str, api_key: str, voice_id: str,
                         model_id: str = "eleven_multilingual_v2",
                         language_code: str | None = None,
                         output_format: str = "mp3_44100_128") -> bytes:
    """Synthesize speech. Returns MP3 bytes.

    Mirrors the request shape proven to work for Georgian in the reference project:
    a minimal body (text + model_id, no forced voice_settings) plus an output_format
    query param. `language_code` is included only when the caller knows the model
    accepts it — some models/languages (e.g. Georgian) reject language_code with a 400.
    """
    payload = {"text": (text or "").strip(), "model_id": model_id}
    if language_code:
        payload["language_code"] = language_code
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{BASE_URL}/text-to-speech/{voice_id}",
            params={"output_format": output_format},
            headers={**_headers(api_key), "Accept": "audio/mpeg", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code >= 400:
        raise ElevenLabsError(f"Text-to-speech failed ({resp.status_code}): {resp.text[:500]}")
    return resp.content


async def list_voices(api_key: str) -> list[dict]:
    """Return available voices as [{voice_id, name, category}]."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{BASE_URL}/voices", headers=_headers(api_key))
    if resp.status_code >= 400:
        raise ElevenLabsError(f"Could not list voices ({resp.status_code}): {resp.text[:500]}")
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
