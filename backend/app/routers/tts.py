"""Text-to-speech endpoints for the user UI (ElevenLabs)."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..services import elevenlabs, limits, settings_store
from ..services.auth import Principal, resolve_principal

router = APIRouter(tags=["tts"])

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


@router.get("/voices")
async def voices():
    cfg = await settings_store.get_effective()
    try:
        return await elevenlabs.list_voices(cfg["elevenlabs_api_key"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/tts")
async def synthesize(req: TTSRequest, principal: Principal = Depends(resolve_principal)):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > 5000:
        raise HTTPException(status_code=400, detail="text exceeds 5000 characters")

    await limits.reserve(principal, "tts")
    cfg = await settings_store.get_effective()

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
