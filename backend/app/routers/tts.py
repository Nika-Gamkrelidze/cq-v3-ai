"""Text-to-speech endpoints for the user UI (ElevenLabs)."""
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..services import elevenlabs, limits, settings_store
from ..services.auth import Principal, resolve_principal

router = APIRouter(tags=["tts"])

# A caller-supplied voice_id is interpolated into the ElevenLabs URL path, so it must be
# validated regardless of any allowlist (e.g. "../../v1/dubbing" would otherwise reach a
# different endpoint with our account key).
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{16,32}$")

# Language support for TTS. `model` is the model to use; `enforce` is whether ElevenLabs
# accepts a language_code for that (model, language) pair; `voice` is an optional
# language-specific default voice used when the caller doesn't pick one.
#
# Verified against the live API (and matching the reference contact-1 project):
#   * Georgian: eleven_multilingual_v2 mispronounces it (English-accented). The correct
#     result comes from `eleven_v3` paired with a Georgian-capable voice ("Laura",
#     3b8fXc91YHS1i2DYAlBQ). A TTS->STT round-trip returns clean Georgian (lang=kat).
#     No language_code (v3 reads the Georgian script; "ka" enforcement is unsupported).
#   * English/Russian: eleven_multilingual_v2 renders correctly and accepts language_code.
GEORGIAN_VOICE = "3b8fXc91YHS1i2DYAlBQ"  # "Laura - Natural & Grounded" (shared voice)

LANGUAGES: dict[str, dict] = {
    "en": {"name": "English",  "model": "eleven_multilingual_v2", "enforce": True,
           "voice": None, "note": ""},
    "ru": {"name": "Russian",  "model": "eleven_multilingual_v2", "enforce": True,
           "voice": None, "note": ""},
    "ka": {"name": "Georgian", "model": "eleven_v3", "enforce": False,
           "voice": GEORGIAN_VOICE,
           "note": "Georgian uses the eleven_v3 model with a Georgian-capable voice for "
                   "correct pronunciation. Leave the voice on default for best results."},
}


class TTSRequest(BaseModel):
    text: str
    voice_id: str | None = None
    model_id: str | None = None
    language_code: str | None = None


@router.get("/languages")
async def languages():
    """Languages the TTS feature supports, for the UI selector."""
    return [
        {"code": code, "name": info["name"], "note": info.get("note", "")}
        for code, info in LANGUAGES.items()
    ]


def system_voice_ids(cfg: dict) -> set[str]:
    """Voices the server itself resolves to (configured default + per-language defaults,
    incl. the Georgian voice). Always accepted by /tts and always shown as selected in the
    admin panel — curation must never be able to break the Georgian path."""
    ids = {cfg.get("tts_voice_id")} | {info.get("voice") for info in LANGUAGES.values()}
    return {v for v in ids if v}


@router.get("/voices")
async def voices():
    """Public: the voices customers may choose from. When the admin has curated a list we
    return it in the admin's order; otherwise every voice. Fails OPEN — an unconfigured or
    stale allowlist returns the full list rather than an empty dropdown."""
    cfg = await settings_store.get_effective()
    vcfg = await settings_store.get_voice_config()
    try:
        live = await elevenlabs.list_voices(cfg["elevenlabs_api_key"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))

    if vcfg["mode"] != "allowlist" or not vcfg["voice_ids"]:
        return live
    by_id = {v.get("voice_id"): v for v in live if v.get("voice_id")}
    picked = [by_id[i] for i in vcfg["voice_ids"] if i in by_id]
    # An allowlist that matches nothing live (key rotated, voices deleted) must not empty
    # the customer dropdown.
    return picked or live


@router.post("/tts")
async def synthesize(req: TTSRequest, principal: Principal = Depends(resolve_principal)):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > 5000:
        raise HTTPException(status_code=400, detail="text exceeds 5000 characters")

    cfg = await settings_store.get_effective()

    # Validate/authorize the CALLER-SUPPLIED voice only, and do it before reserving quota
    # so a rejection never burns an anonymous user's daily credit. Never validate the
    # resolved voice below — that one may legitimately be a system default (e.g. Georgian).
    if req.voice_id:
        if not VOICE_ID_RE.match(req.voice_id):
            raise HTTPException(status_code=400, detail="Invalid voice id")
        vcfg = await settings_store.get_voice_config()
        if vcfg["mode"] == "allowlist" and vcfg["voice_ids"]:
            allowed = set(vcfg["voice_ids"]) | system_voice_ids(cfg)
            if req.voice_id not in allowed:
                raise HTTPException(status_code=400, detail="voice_unavailable")

    await limits.reserve(principal, "tts")

    # Resolve model, voice, and language enforcement from the selected language.
    lang = (req.language_code or "").strip().lower()
    language_code = None
    if lang:
        info = LANGUAGES.get(lang)
        if info is None:
            supported = ", ".join(f"{c} ({i['name']})" for c, i in LANGUAGES.items())
            raise HTTPException(
                status_code=400,
                detail=f"Language '{lang}' is not supported for text-to-speech. Supported: {supported}.",
            )
        model_id = req.model_id or info["model"]
        language_code = lang if info["enforce"] else None
        # Voice priority: explicit request > language default voice > configured default.
        voice_id = req.voice_id or info.get("voice") or cfg["tts_voice_id"]
    else:
        # No language selected — keep prior behaviour (configured model + voice).
        model_id = req.model_id or cfg["tts_model"]
        voice_id = req.voice_id or cfg["tts_voice_id"]

    try:
        audio = await elevenlabs.text_to_speech(
            text, cfg["elevenlabs_api_key"], voice_id, model_id, language_code,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(content=audio, media_type="audio/mpeg")
