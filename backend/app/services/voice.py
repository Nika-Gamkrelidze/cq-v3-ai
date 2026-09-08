"""The one seam every voice call goes through.

No router or service imports a voice provider any more. They call this module, which:

  1. resolves WHICH provider, model and key this tenant's call runs on
     (`ai_resolve.resolve(client_id, "stt" | "tts")` — default connection ← assigned
     connection ← the tenant's own key, with the legacy admin settings underneath);
  2. picks the adapter for `res.provider` from the two registries below;
  3. for STT, applies the tenant's resolved transcription settings
     (`services/transcription.py`: language, diarize, keyterms, audio format);
  4. for TTS, resolves the model and voice for the request — the provider's own per-language
     defaults (the Georgian rule lives in the ElevenLabs adapter), the connection's voice, the
     legacy default voice — and shapes the caller's voice_settings to what that model accepts.

Adding a provider is one adapter module plus one entry in `STT_ADAPTERS` / `TTS_ADAPTERS`.

`shape_voice_settings` lives here rather than in an adapter because it is provider-neutral:
it reads only the `caps()` record, and `caps()` is the adapter's answer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from . import ai_resolve, settings_store
from . import transcription as transcription_svc
from .ai_resolve import Resolved
from .providers import stt_elevenlabs, stt_gemini, stt_openai, tts_elevenlabs, tts_openai
from .providers.voice_base import (LANGUAGE_REJECTED, MAX_TEXT_CHARS, SPEED_MAX,  # noqa: F401
                                   SPEED_MIN, STABILITY_PRESETS, STTAdapter, TTSAdapter,
                                   VoiceError, silence_wav)

log = logging.getLogger("cq")

STT_ADAPTERS: dict[str, STTAdapter] = {
    stt_elevenlabs.adapter.id: stt_elevenlabs.adapter,
    stt_openai.adapter.id: stt_openai.adapter,
    stt_gemini.adapter.id: stt_gemini.adapter,
}
TTS_ADAPTERS: dict[str, TTSAdapter] = {
    tts_elevenlabs.adapter.id: tts_elevenlabs.adapter,
    tts_openai.adapter.id: tts_openai.adapter,
}


def stt_adapter(provider: str) -> STTAdapter:
    try:
        return STT_ADAPTERS[provider]
    except KeyError:
        raise VoiceError(f"No speech-to-text adapter for provider {provider!r}.",
                         code="unknown_provider") from None


def tts_adapter(provider: str) -> TTSAdapter:
    try:
        return TTS_ADAPTERS[provider]
    except KeyError:
        raise VoiceError(f"No text-to-speech adapter for provider {provider!r}.",
                         code="unknown_provider") from None


# ---------------------------------------------------------------------------
# Speech-to-text
# ---------------------------------------------------------------------------
async def transcribe(client_id: str | None, audio: bytes, filename: str | None,
                     content_type: str | None, *, transcription: dict | None = None,
                     timeout: float | None = None) -> dict:
    """Transcribe on the provider this tenant resolves to. → {text, language_code, words,
    provider, model, source} (the last three say what actually ran, for logs and probes).

    `transcription` is the ALREADY-RESOLVED settings dict for this request (the route resolves
    it, because only the route has seen the per-file override). Left out, it is resolved here
    from `client_id`, so a caller that has not been taught about the settings still honours
    the operator default and the workspace override rather than transcribing on code defaults.
    """
    res = await ai_resolve.resolve(client_id, "stt")
    adapter = stt_adapter(res.provider)
    cfg = transcription if transcription is not None \
        else await transcription_svc.resolve(client_id)
    kw = transcription_svc.as_kwargs(cfg)
    if timeout is not None:
        kw["timeout"] = timeout
    out = await adapter.transcribe(res, audio, filename, content_type, **kw)
    out = dict(out or {})
    out.setdefault("text", "")
    out.setdefault("language_code", None)
    out.setdefault("words", [])
    out["provider"], out["model"], out["source"] = res.provider, adapter.model(res), res.source
    return out


# ---------------------------------------------------------------------------
# Text-to-speech
# ---------------------------------------------------------------------------
def shape_voice_settings(caps: dict, vs: dict | None) -> dict | None:
    """Reduce a caller's voice_settings to what the resolved model accepts. Pure.

    Drops the keys the model has no control for, clamps the rest to the documented ranges,
    snaps stability to the nearest preset where the model only takes presets, and returns None
    when nothing survives — so the provider body carries no `voice_settings` at all rather than
    an empty object. One info line names what changed, because "why does my clip sound the
    same with style at 0.9" is a support question whose answer is otherwise nowhere.
    """
    if not vs:
        return None
    out: dict = {}
    dropped: list[str] = []
    changed: list[str] = []
    allowed = {"stability": True, "similarity_boost": True, "style": caps["style"],
               "use_speaker_boost": caps["speaker_boost"], "speed": caps["speed"]}
    for key, supported in allowed.items():
        val = vs.get(key)
        if val is None:
            continue
        if not supported:
            dropped.append(key)
            continue
        if key == "use_speaker_boost":
            out[key] = bool(val)
            continue
        lo, hi = (SPEED_MIN, SPEED_MAX) if key == "speed" else (0.0, 1.0)
        num = min(hi, max(lo, float(val)))
        if key == "stability" and caps["presets"]:
            num = min(STABILITY_PRESETS, key=lambda preset: abs(preset - num))
        if num != val:
            changed.append(f"{key} {val}->{num}")
        out[key] = num
    if dropped or changed:
        log.info("tts voice_settings shaped: dropped=%s adjusted=%s",
                 ",".join(dropped) or "-", ",".join(changed) or "-")
    return out or None


@dataclass(frozen=True)
class TTSPlan:
    """Everything one synthesis will be sent with — kept by the caller so the row it records
    holds what was SENT, not what was asked, and the clip can be reproduced exactly."""
    model_id: str
    voice_id: str | None
    language_code: str | None       # the code that will be sent (None = left to the model)
    voice_settings: dict | None     # SHAPED
    caps: dict
    lang: str | None                # the language the caller asked for, if any


class TTS:
    """One tenant's text-to-speech, resolved once. Routes build one per request so the
    validation, the catalogue and the synthesis all agree on the provider."""

    def __init__(self, res: Resolved, adapter: TTSAdapter):
        self.res, self.adapter = res, adapter
        self._default_voice: str | None | bool = False      # False = not looked up yet

    @property
    def provider(self) -> str:
        return self.res.provider

    async def models(self) -> list[dict]:
        return await self.adapter.list_models(self.res)

    async def caps(self, model_id: str) -> dict:
        return await self.adapter.caps(self.res, model_id)

    def defaults_for_language(self, lang: str) -> dict:
        return self.adapter.defaults_for_language(self.res, lang)

    def valid_voice_id(self, voice_id: str) -> bool:
        return self.adapter.valid_voice_id(voice_id)

    def language_voice_ids(self) -> set[str]:
        return set(self.adapter.language_voice_ids())

    async def default_voice(self) -> str | None:
        """The voice a request that names none gets: the connection's `settings.voice_id`,
        else — for ElevenLabs only, whose ids they are — the legacy `tts_voice_id` from the
        admin settings, else the adapter's own default. An ElevenLabs id must never be sent
        to another provider."""
        if self._default_voice is not False:
            return self._default_voice
        voice = (self.res.settings or {}).get("voice_id") or None
        if not voice and self.res.provider == tts_elevenlabs.adapter.id:
            cfg = await settings_store.get_effective()
            voice = cfg.get("tts_voice_id") or None
        if not voice:
            voice = self.adapter.default_voice
        self._default_voice = voice
        return voice

    async def system_voice_ids(self) -> set[str]:
        """Voices the server itself resolves to (the default + the per-language defaults).
        Always accepted by /tts and always shown as selected in the admin panel — curation
        must never be able to break a language path."""
        ids = {await self.default_voice()} | self.language_voice_ids()
        return {v for v in ids if v}

    async def voices(self) -> list[dict]:
        """The provider's live voice list, each row flagged `is_default` when it is one the
        server would pick on its own, so "Default voice" and the named default read as the
        same thing in a dropdown."""
        system = await self.system_voice_ids()
        return [dict(v, is_default=v.get("voice_id") in system)
                for v in await self.adapter.list_voices(self.res)]

    async def plan(self, *, voice_id: str | None = None, model_id: str | None = None,
                   language_code: str | None = None, voice_settings: dict | None = None,
                   enforce_language: bool | None = None) -> TTSPlan:
        """Resolve model and voice, then decide the language code and shape the settings.

        Model: the caller's, else the provider's default for the language (Georgian → v3 on
        ElevenLabs), else the connection's model. Voice: the caller's, else the language's own
        default voice, else the configured default. The language code is sent wherever the
        model takes it (enforced) or shrugs at it (ignored), never where it 400s (rejected),
        and not at all when the caller asked us to leave the language to the model.
        """
        lang = (language_code or "").strip().lower() or None
        defaults = self.adapter.defaults_for_language(self.res, lang or "")
        model = model_id or defaults.get("model") or self.adapter.default_model
        voice = voice_id or (defaults.get("voice") if lang else None) or await self.default_voice()
        caps = await self.caps(model)
        code = None
        if lang and caps["language_code"] != LANGUAGE_REJECTED and enforce_language is not False:
            code = lang
        return TTSPlan(model_id=model, voice_id=voice, language_code=code,
                       voice_settings=shape_voice_settings(caps, voice_settings), caps=caps,
                       lang=lang)

    async def synthesize(self, text: str, plan: TTSPlan) -> bytes:
        if not plan.voice_id:
            raise VoiceError("No TTS voice is configured (set one on the connection or in the "
                             "admin panel).", code="not_configured")
        return await self.adapter.synthesize(
            self.res, text, voice_id=plan.voice_id, model_id=plan.model_id,
            language_code=plan.language_code, voice_settings=plan.voice_settings)


async def tts(client_id: str | None) -> TTS:
    """This tenant's text-to-speech, resolved: provider, key, model, voice."""
    res = await ai_resolve.resolve(client_id, "tts")
    return TTS(res, tts_adapter(res.provider))


async def synthesize(client_id: str | None, text: str, *, voice_id: str | None = None,
                     model_id: str | None = None, language_code: str | None = None,
                     voice_settings: dict | None = None,
                     enforce_language: bool | None = None) -> bytes:
    """One-shot synthesis on the provider this tenant resolves to. → MP3 bytes."""
    ctx = await tts(client_id)
    plan = await ctx.plan(voice_id=voice_id, model_id=model_id, language_code=language_code,
                          voice_settings=voice_settings, enforce_language=enforce_language)
    return await ctx.synthesize(text, plan)


async def list_voices(client_id: str | None) -> list[dict]:
    """→ [{voice_id, name, category, preview_url, is_default}]"""
    return await (await tts(client_id)).voices()


async def list_models(client_id: str | None) -> list[dict]:
    """→ the rows GET /tts/models returns: {model_id, name, description, max_chars,
    languages, supports}."""
    return await (await tts(client_id)).models()


# ---------------------------------------------------------------------------
# The registry's "Test connection" button
# ---------------------------------------------------------------------------
async def probe(res: Resolved) -> dict:
    """→ {ok, detail} for one resolved voice connection, STT or TTS by `res.capability`.
    A real, minimal call on the key. Never raises."""
    try:
        if res.capability == "stt":
            return await stt_adapter(res.provider).probe(res)
        if res.capability == "tts":
            return await tts_adapter(res.provider).probe(res)
        return {"ok": False, "code": "unknown_capability",
                "detail": f"{res.capability!r} is not a voice capability"}
    except VoiceError as exc:
        return {"ok": False, "detail": str(exc), "code": exc.code}
    except Exception as exc:  # noqa: BLE001 — a probe reports, it never raises
        return {"ok": False, "detail": str(exc), "code": "http"}
