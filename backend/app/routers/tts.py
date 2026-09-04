"""Text-to-speech endpoints for the user UI (ElevenLabs).

`POST /tts` answers with the clip. For a registered user and for a tenant LOGIN the clip is
also kept on the media volume so `GET /tts/history` can list it and `GET /tts/{id}/audio` play
it back — on the one deadline the Storage setting gives every stored file. Server-to-server
API-key traffic is logged but not kept (`_keeps_clip`): nobody plays back a bulk run. An
anonymous visitor's clip is kept for the same reason it always was (a paid, public endpoint has
to be investigable), and is never listed: it is keyed to an IP, and an IP is not a person.
"""
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ..db import pool
from ..services import elevenlabs, limits, media, settings_store
from ..services.auth import Principal, client_ip, resolve_principal

log = logging.getLogger("cq")

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


# The principal kinds whose clip is always kept on disk. Anonymous: so abuse of a public, paid
# endpoint can be investigated and a bad result reproduced. Registered user: so their account
# History can play it back (§12). NOT the operator or an integration — neither has a History,
# and neither is a retention subject.
_STORED_KINDS = frozenset({"anonymous", "user"})


def _keeps_clip(principal: Principal) -> bool:
    """Whether this synthesis's MP3 is worth the disk it will occupy for `retention_days`.

    A tenant LOGIN gets its clip kept, because a person sitting in the portal will want to play
    it back from History. An X-API-Key tenant does not: that is the server-to-server bulk path
    (5000 characters a call, uncapped by default), nothing ever plays those back, and every one
    of them would sit for a month in the same volume that now also holds every recording. The
    row — text, voice, model, who and when — is written either way, so nothing an operator
    needs for cost or abuse review depends on keeping the audio.
    """
    return (principal.kind in _STORED_KINDS
            or (principal.kind == "tenant" and principal.via == "token"))


async def _record_tts(*, principal: Principal, ip: str, text: str, language_code: str | None,
                      voice_id: str, model_id: str, audio: bytes) -> None:
    """Keep what a caller asked us to say, and what we said back.

    /tts used to stream the clip straight out and keep nothing at all — no text, no IP, no
    trace — which left abuse of a public, paid, unauthenticated endpoint uninvestigable. One
    row per synthesis fixes that.

    The clip itself is kept for the callers `_keeps_clip` names, on the ONE deadline the
    Storage setting gives every stored file (`retention_days`, 0 = keep) — the same number the
    recordings use, read through `get_storage_config()` so an anonymous clip keeps following
    the admin's number after that field moves out of the anonymous panel. `user_id` is written
    for a registered user ONLY: a tenant login's `principal.user_id` is a tenant_users row, and
    writing it here would let a user-scoped History query match a tenant's clip.

    Never raises: recording is a retention duty, not part of answering the request.
    """
    try:
        stored, purge_after = {}, None
        if _keeps_clip(principal):
            storage = await settings_store.get_storage_config()
            stored = media.save(audio, content_type="audio/mpeg", filename="speech.mp3")
            purge_after = media.deadline(storage["retention_days"])
        async with pool().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tts_requests
                    (client_id, principal_type, anon_key, client_ip, text, text_chars,
                     language_code, voice_id, tts_model, audio_path, audio_bytes, purge_after,
                     user_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                """,
                principal.client_id, principal.kind, principal.anon_key, ip, text, len(text),
                language_code, voice_id, model_id, stored.get("path"), stored.get("bytes"),
                purge_after, principal.user_id if principal.kind == "user" else None)
    except Exception:  # noqa: BLE001 — never fail a synthesis because we could not log it
        log.exception("tts retention record failed")


@router.post("/tts")
async def synthesize(request: Request, req: TTSRequest,
                     principal: Principal = Depends(resolve_principal)):
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

    await _record_tts(principal=principal, ip=client_ip(request), text=text,
                      language_code=language_code, voice_id=voice_id, model_id=model_id,
                      audio=audio)
    return Response(content=audio, media_type="audio/mpeg")


# ---- history ----------------------------------------------------------------
def _history_scope(principal: Principal, first: int = 1) -> tuple[str, list]:
    """(where_sql, args) restricting `tts_requests` to what this principal may see.

    Mirrors `routers/analyze.py::_scope`: the superadmin sees everything (it is the operator,
    not a customer), a tenant its own rows, a registered user their own — each with the
    `principal_type` discriminator riding along so a row can never match through the wrong
    column. `first` is the placeholder the predicate may start at, so the audio route can put
    the row id in $1 and still share this one policy. There is NO anonymous branch on purpose:
    an anonymous clip is keyed to an IP, and an IP is shared by everyone behind the same NAT,
    so "your history" would be your office's.
    """
    if principal.is_superadmin:
        return "TRUE", []
    if principal.is_tenant:
        return f"client_id = ${first} AND principal_type = 'tenant'", [principal.client_id]
    if principal.kind == "user" and principal.user_id:
        return f"user_id = ${first} AND principal_type = 'user'", [principal.user_id]
    if principal.kind == "integration":
        raise HTTPException(status_code=403,
                            detail="This integration credential cannot read text-to-speech history.")
    raise HTTPException(status_code=401, detail="Sign in to see your text-to-speech history.")


def _stored_file(rel_path: str | None) -> Path | None:
    """The on-disk file behind a `tts_requests.audio_path`, or None when there is nothing to
    serve (never stored, purged, or gone from the volume).

    Resolved under MEDIA_ROOT and required to still be under it — the same defence
    `media.delete` applies before it unlinks — because this function hands bytes OUT, and the
    row's path is data, not code, however much we trust the writer.
    """
    if not rel_path:
        return None
    root = media.MEDIA_ROOT.resolve()
    target = (media.MEDIA_ROOT / rel_path).resolve()
    if root not in target.parents:
        log.warning("tts audio refused a path outside the media root: %s", rel_path)
        return None
    return target if target.is_file() else None


@router.get("/tts/history")
async def tts_history(limit: int = 20, principal: Principal = Depends(resolve_principal)):
    """A signed-in caller's past syntheses, newest first.

    `text` is cut to 120 characters HERE rather than in the browser: a row keeps up to 5000,
    and a list of a hundred of those is half a megabyte of prose nobody reads in a list.
    `has_audio` turns false once the retention purge has taken the clip (the row outlives the
    file for a signed-in caller — services/retention.py), and `audio_url` is null in the same
    case so no renderer offers a player for bytes that are gone.
    """
    limit = max(1, min(limit, 100))
    where, args = _history_scope(principal)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, left(text, 120) AS text, language_code, voice_id, created_at,
                   audio_path IS NOT NULL AS has_audio
              FROM tts_requests
             WHERE {where}
             ORDER BY created_at DESC
             LIMIT ${len(args) + 1}
            """, *args, limit)
    return [{
        "id": str(r["id"]),
        "text": r["text"],
        "language_code": r["language_code"],
        "voice_id": r["voice_id"],
        "created_at": r["created_at"].isoformat(),
        "has_audio": r["has_audio"],
        "audio_url": f"/tts/{r['id']}/audio" if r["has_audio"] else None,
    } for r in rows]


@router.get("/tts/{tts_id}/audio")
async def tts_audio(tts_id: str, principal: Principal = Depends(resolve_principal)):
    """Stream one stored clip back to the caller it belongs to.

    Not yours, never existed and already purged are ONE answer — 404 — so the id space cannot
    be probed for other people's clips. `private, no-store`: someone's spoken text must not
    sit in a shared cache, and the retention purge has to be the only thing deciding how long
    a copy exists. `FileResponse` honours Range on the installed Starlette, which is what lets
    the browser's player seek; `inline` so the same URL plays in an <audio> element and still
    saves under a sensible name.
    """
    where, args = _history_scope(principal, first=2)
    async with pool().acquire() as conn:
        rel = await conn.fetchval(
            f"SELECT audio_path FROM tts_requests WHERE id = $1 AND {where}", tts_id, *args)
    path = _stored_file(rel)
    if path is None:
        raise HTTPException(status_code=404, detail="This clip is no longer available.")
    return FileResponse(path, media_type="audio/mpeg", filename=f"tts-{tts_id[:8]}.mp3",
                        content_disposition_type="inline",
                        headers={"Cache-Control": "private, no-store"})
