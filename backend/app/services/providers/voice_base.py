"""What a voice adapter is: the STT and TTS protocols, and the vocabulary they share.

`services/voice.py` is the only caller. It resolves WHICH provider a tenant's request runs on
(`services/ai_resolve.py`), picks the adapter for `res.provider`, and hands it the `Resolved`
(decrypted key, model, base_url, settings). An adapter never reads settings or the database
itself: everything it needs is on `res`, which is what makes one adapter serve the deployment
key, an assigned connection and a tenant's own key without knowing which it was given.

Two things are deliberately NOT provider-neutral, and are therefore the adapter's to answer:

  * `caps(res, model_id)` — what one model accepts, in the one shape the customer form
    (`GET /tts/models` → `supports`) and the request shaper (`voice.shape_voice_settings`)
    both read. A control the adapter says is unsupported is dropped before it is sent and
    hidden in the form, from one answer, so the two cannot disagree.
  * `defaults_for_language(res, lang)` — what "Auto" means for a language on THIS provider.
    The Georgian rule (eleven_v3 + a Georgian-capable voice) lives in the ElevenLabs adapter:
    it is a fact about that provider's models, not about the product.
"""
from __future__ import annotations

import io
import logging
import wave
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:  # pragma: no cover — typing only; keeps this module import-light
    from ..ai_resolve import Resolved

log = logging.getLogger("cq")

# The text length /tts accepts regardless of provider or model: it is the anonymous quota unit
# the admin panel counts in, and every model this product exposes takes at least this much.
MAX_TEXT_CHARS = 5000

# Where the product UI lets `speed` go. Providers take wider ranges; these are the values past
# which a clip stops sounding like the voice, so the API refuses them up front (422) rather
# than shipping a clip nobody wanted and billing for it.
SPEED_MIN, SPEED_MAX = 0.7, 1.2

# `caps()["language_code"]` — what the provider does with a language code for this model:
#   enforced  it changes what comes out (send it)
#   ignored   harmless (send it where the model takes it; the adapter may drop it)
#   rejected  the provider 400s on it (never send it)
LANGUAGE_ENFORCED, LANGUAGE_IGNORED, LANGUAGE_REJECTED = "enforced", "ignored", "rejected"

# What `caps()["presets"]` means: stability is a three-position control (Creative / Natural /
# Robust) and a slider value is snapped to the nearest one before it is sent. ElevenLabs' v3
# family is the provider that defines it; the shaper only needs the positions.
STABILITY_PRESETS = (0.0, 0.5, 1.0)

# A test hook: `httpx.MockTransport` here makes every adapter that uses `http_request` talk to
# the test instead of the network. None = the real transport.
_transport: httpx.AsyncBaseTransport | None = None


class VoiceError(RuntimeError):
    """A classified voice-provider failure — the ONE exception type every adapter raises.

    `code`  — invalid_key | missing_permission | quota | blocked | transport | http |
              unknown_provider | not_configured
    `scope` — the permission the provider named as missing, when it named one
    `raw`   — first 500 chars of the response body (kept for the admin panel / job row)

    `elevenlabs.ElevenLabsError` subclasses this, so one `except VoiceError` covers both the
    classified ElevenLabs failures and the ones the newer adapters raise directly.
    """

    def __init__(self, message: str, *, status: int | None = None, code: str = "http",
                 scope: str | None = None, raw: str = ""):
        super().__init__(message)
        self.status, self.code, self.scope, self.raw = status, code, scope, raw


def caps(*, presets: bool = False, style: bool = True, speaker_boost: bool = True,
         speed: bool = True, language_code: str = LANGUAGE_IGNORED,
         max_chars: int | None = None) -> dict:
    """The capability record every TTS adapter returns — built here so a key can never be
    missing from one provider's answer. `max_chars` is capped at the product limit."""
    limit = int(max_chars) if max_chars else MAX_TEXT_CHARS
    return {
        "presets": bool(presets),
        "style": bool(style),
        "speaker_boost": bool(speaker_boost),
        "speed": bool(speed),
        "language_code": language_code,
        "max_chars": min(MAX_TEXT_CHARS, limit),
    }


def public_model(model_id: str, *, name: str | None = None, description: str = "",
                 languages: list[str] | None = None, caps: dict) -> dict:
    """One row of `GET /tts/models`: the shape `frontend/next/app/home/ttsAdvanced.ts` reads.
    `max_chars` is top-level; `supports` is the caps record WITHOUT it."""
    supports = dict(caps)
    max_chars = supports.pop("max_chars")
    return {
        "model_id": model_id,
        "name": name or model_id,
        "description": description or "",
        "max_chars": max_chars,
        "languages": list(languages or []),
        "supports": supports,
    }


def silence_wav(seconds: float = 0.4, rate: int = 16000) -> bytes:
    """~13 KB of mono 16-bit digital silence, built in-process.

    Probe payload only. It is the cheapest thing that still exercises a REAL speech-to-text
    endpoint — the only way to prove a key may transcribe, since no provider has an endpoint
    that reports a key's own scopes.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def own_model(res: "Resolved", default: str, *, foreign_prefixes: tuple[str, ...] = ()) -> str:
    """The model an adapter should send: `res.model`, unless it is unset or plainly another
    provider's id (a legacy `stt_model=scribe_v1` showing through under an OpenAI connection
    that set no model of its own). A foreign id is logged and the adapter's default used —
    sending it would be a certain 400 for every call on that connection."""
    model = (res.model or "").strip()
    if not model:
        return default
    if any(model.startswith(p) for p in foreign_prefixes):
        log.warning("%s %s: model %r belongs to another provider; using %s",
                    res.provider, res.capability, model, default)
        return default
    return model


def _snippet(resp: httpx.Response) -> str:
    return resp.text[:500]


def _classify(resp: httpx.Response, action: str, vendor: str) -> VoiceError:
    """Map an OpenAI-style error response onto an actionable message + machine code."""
    raw = _snippet(resp)
    err_code = err_type = msg = ""
    try:
        err = (resp.json() or {}).get("error")
    except ValueError:
        err = None
    if isinstance(err, dict):
        err_code = str(err.get("code") or "").lower()
        err_type = str(err.get("type") or "").lower()
        msg = str(err.get("message") or "")
    elif isinstance(err, str):
        msg = err
    if resp.status_code == 401 or err_code in ("invalid_api_key",):
        return VoiceError(
            f"{action} failed: {vendor} did not accept the API key. Paste a current key on the "
            f"connection and re-test.", status=resp.status_code, code="invalid_key", raw=raw)
    if resp.status_code == 403 or err_type == "insufficient_permissions" \
            or "permission" in err_code:
        return VoiceError(
            f"{action} was refused: this {vendor} key lacks the permission for it"
            f"{' — ' + msg if msg else ''}.",
            status=resp.status_code, code="missing_permission", raw=raw)
    if err_code == "insufficient_quota" or err_type == "insufficient_quota" \
            or resp.status_code == 402:
        return VoiceError(
            f"{action} failed: the {vendor} account is out of credits or over its quota.",
            status=resp.status_code, code="quota", raw=raw)
    if resp.status_code == 429:
        return VoiceError(
            f"{action} was rate-limited by {vendor}; try again in a moment.",
            status=resp.status_code, code="quota", raw=raw)
    return VoiceError(f"{action} failed ({resp.status_code}): {msg or raw}",
                      status=resp.status_code, raw=raw)


async def http_request(method: str, url: str, action: str, *, vendor: str, timeout: float,
                       **kw) -> httpx.Response:
    """One classified HTTP call, for adapters whose vendor has no module of its own (ElevenLabs
    keeps `services/elevenlabs.py::_request`, with its vendor-specific error shapes).

    Transport and HTTP failures both come back as a `VoiceError` carrying a machine `code`,
    never as a raw httpx exception string or a JSON blob pasted into the user's toast.
    """
    async with httpx.AsyncClient(timeout=timeout, transport=_transport) as client:
        try:
            resp = await client.request(method, url, **kw)
        except httpx.RequestError as exc:
            raise VoiceError(
                f"{action} could not reach {vendor} ({exc.__class__.__name__}). Check the "
                f"server's outbound network/DNS and the connection's base URL.",
                code="transport") from exc
    if resp.status_code >= 400:
        raise _classify(resp, action, vendor)
    return resp


@runtime_checkable
class STTAdapter(Protocol):
    """Speech-to-text for one provider."""
    id: str
    default_model: str

    def model(self, res: "Resolved") -> str:
        """The model id this adapter will send for `res` (its own default when unset)."""
        ...

    async def transcribe(self, res: "Resolved", audio: bytes, filename: str | None,
                         content_type: str | None, *, language_code: str | None = None,
                         diarize: bool = True, keyterms: list[str] | None = None,
                         audio_format: str | None = None,
                         timeout: float | None = None) -> dict:
        """→ {text, language_code, words}. The four keyword settings are exactly what
        `services/transcription.py::as_kwargs` produces (`audio_format` is one of
        `services/audio.STT_FORMATS`); a provider that has no use for one drops it and says
        so in the log. `words` entries carry `speaker_id` only where the provider diarizes —
        `segments.build_segments` gives a word without one the default speaker."""
        ...

    async def probe(self, res: "Resolved") -> dict:
        """→ {ok, detail}. A real, minimal call on the key. Never raises."""
        ...


@runtime_checkable
class TTSAdapter(Protocol):
    """Text-to-speech for one provider."""
    id: str
    default_model: str
    default_voice: str | None       # what to say with when nothing at all is configured

    def model(self, res: "Resolved") -> str:
        """The model id this adapter will send for `res` (its own default when unset)."""
        ...

    async def synthesize(self, res: "Resolved", text: str, *, voice_id: str, model_id: str,
                         language_code: str | None = None,
                         voice_settings: dict | None = None) -> bytes:
        """→ MP3 bytes. `voice_settings` is ALREADY shaped to `caps()` — the adapter sends
        what it is handed (and may map or drop a key it has no field for, with a log line)."""
        ...

    async def list_voices(self, res: "Resolved") -> list[dict]:
        """→ [{voice_id, name, category, preview_url}]. `is_default` is added by voice.py."""
        ...

    async def list_models(self, res: "Resolved") -> list[dict]:
        """→ rows in `public_model` shape, in the order the customer form should offer them.
        Fails OPEN to a built-in list when the provider cannot be asked."""
        ...

    async def caps(self, res: "Resolved", model_id: str) -> dict:
        """→ the `caps()` record for one model — including a model the list does not describe
        (the configured default may be one the provider's catalogue omits)."""
        ...

    def defaults_for_language(self, res: "Resolved", lang: str) -> dict:
        """→ {model, voice, note}: what "Auto" picks for `lang` on this provider. `voice` None
        means the configured default; a language with no rule of its own gets the connection's
        model."""
        ...

    def valid_voice_id(self, voice_id: str) -> bool:
        """Whether a CALLER-supplied id is even well-formed for this provider — checked before
        it is interpolated into a URL or sent, regardless of any allowlist."""
        ...

    def language_voice_ids(self) -> set[str]:
        """The per-language default voices the server resolves to on its own (the Georgian
        voice, for ElevenLabs). Always accepted by /tts and always shown as selected in the
        admin panel — curation must never be able to break a language path."""
        ...

    async def probe(self, res: "Resolved") -> dict:
        """→ {ok, detail}. A real, minimal call on the key. Never raises."""
        ...
