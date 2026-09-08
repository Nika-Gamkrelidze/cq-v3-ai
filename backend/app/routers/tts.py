"""Text-to-speech endpoints for the user UI.

Provider-neutral: every call goes through `services/voice.py`, which resolves the provider a
tenant runs on (ElevenLabs by default; an assigned or bring-your-own connection otherwise) and
answers the provider-specific questions — which models exist, what each accepts, what "Auto"
means for a language (the Georgian eleven_v3 + Laura rule is the ElevenLabs adapter's). What
stays here is the product: the supported language list, the text cap, the curation allowlist,
quota, and the retention row.

`POST /tts` answers with the clip. For a registered user and for a tenant LOGIN the clip is
also kept on the media volume so `GET /tts/history` can list it and `GET /tts/{id}/audio` play
it back — on the one deadline the Storage setting gives every stored file. Server-to-server
API-key traffic is logged but not kept (`_keeps_clip`): nobody plays back a bulk run. An
anonymous visitor's clip is kept for the same reason it always was (a paid, public endpoint has
to be investigable), and is never listed: it is keyed to an IP, and an IP is not a person.
"""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from ..db import pool
from ..services import limits, media, settings_store, voice
from ..services.auth import Principal, client_ip, resolve_principal
from ..services.voice import MAX_TEXT_CHARS, SPEED_MAX, SPEED_MIN

log = logging.getLogger("cq")

router = APIRouter(tags=["tts"])

# The languages the TTS feature offers. Which MODEL "Auto" resolves to for each, and whether a
# language has a default voice of its own, is the provider's answer
# (`voice.TTS.defaults_for_language`) — it differs per provider and must not be hardcoded here.
LANGUAGES: dict[str, str] = {"en": "English", "ru": "Russian", "ka": "Georgian"}


class VoiceSettingsIn(BaseModel):
    """The caller's wishes, bounded to what any provider documents for any model.

    Bounds here mean an out-of-range number is a 422 before any quota is reserved; what the
    resolved MODEL does with an in-range one (drop it, snap it) is `voice.shape_voice_settings`'s
    job against the adapter's caps, so a caller pointing at v3 with a style slider gets a clip,
    not an error.
    """
    stability: float | None = Field(default=None, ge=0.0, le=1.0)
    similarity_boost: float | None = Field(default=None, ge=0.0, le=1.0)
    style: float | None = Field(default=None, ge=0.0, le=1.0)
    use_speaker_boost: bool | None = None
    speed: float | None = Field(default=None, ge=SPEED_MIN, le=SPEED_MAX)


class TTSRequest(BaseModel):
    text: str
    voice_id: str | None = None
    model_id: str | None = None
    language_code: str | None = None
    # Both optional and both absent by default, so a request written against the old contract
    # produces the same provider body it always did (no voice_settings key at all).
    voice_settings: VoiceSettingsIn | None = None
    enforce_language: bool | None = None


@router.get("/languages")
async def languages(principal: Principal = Depends(resolve_principal)):
    """Languages the TTS feature supports, for the UI selector. `model` is what Auto picks for
    the language ON THIS CALLER'S PROVIDER, so the form can show that model's controls before
    the customer touches the Model dropdown."""
    ctx = await voice.tts(principal.client_id)
    out = []
    for code, name in LANGUAGES.items():
        info = ctx.defaults_for_language(code)
        out.append({"code": code, "name": name, "note": info.get("note") or "",
                    "model": info.get("model")})
    return out


@router.get("/voices")
async def voices(principal: Principal = Depends(resolve_principal)):
    """Public: the voices customers may choose from. When the admin has curated a list we
    return it in the admin's order; otherwise every voice. Fails OPEN — an unconfigured or
    stale allowlist returns the full list rather than an empty dropdown."""
    ctx = await voice.tts(principal.client_id)
    vcfg = await settings_store.get_voice_config()
    try:
        # Each row already carries `is_default` (the voice the server would pick on its own),
        # so "Default voice" and the named default read as the same thing in the dropdown.
        live = await ctx.voices()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))

    if vcfg["mode"] != "allowlist" or not vcfg["voice_ids"]:
        return live
    system = await ctx.system_voice_ids()
    by_id = {v.get("voice_id"): v for v in live if v.get("voice_id")}
    # The admin panel shows system defaults as an always-on tick and deliberately leaves them
    # OUT of voice_ids on save, and /tts accepts them regardless of the allowlist. So the
    # customer list has to add them back here — otherwise the one voice the operator was told
    # is "always on" is the one voice customers cannot see or choose by name. Defaults come
    # first (the per-language voices, e.g. Georgian, then the configured default), then the
    # admin's own order.
    default_order = sorted(ctx.language_voice_ids()) + [await ctx.default_voice()]
    default_order += sorted(system - set(default_order))
    ordered = [i for i in default_order if i and i in by_id]
    ordered += [i for i in vcfg["voice_ids"] if i in by_id and i not in system]
    seen: set[str] = set()
    picked = [by_id[i] for i in ordered if not (i in seen or seen.add(i))]
    # An allowlist that matches nothing live (key rotated, voices deleted) must not empty
    # the customer dropdown.
    return picked or live


@router.get("/tts/models")
async def tts_models(principal: Principal = Depends(resolve_principal)):
    """Public: the models a customer may pick, with what each one accepts (`supports`), so
    the form shows the right controls per model instead of a slider the model will ignore."""
    return await (await voice.tts(principal.client_id)).models()


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
                      voice_id: str, model_id: str, audio: bytes,
                      voice_settings: dict | None = None) -> None:
    """Keep what a caller asked us to say, and what we said back.

    `voice_settings` is the SHAPED dict — what the provider was actually sent, not what the
    caller typed — so a clip a customer complains about can be reproduced exactly.

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
                     user_id, voice_settings)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb)
                """,
                principal.client_id, principal.kind, principal.anon_key, ip, text, len(text),
                language_code, voice_id, model_id, stored.get("path"), stored.get("bytes"),
                purge_after, principal.user_id if principal.kind == "user" else None,
                json.dumps(voice_settings) if voice_settings else None)
    except Exception:  # noqa: BLE001 — never fail a synthesis because we could not log it
        log.exception("tts retention record failed")


@router.post("/tts")
async def synthesize(request: Request, req: TTSRequest,
                     principal: Principal = Depends(resolve_principal)):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=400, detail=f"text exceeds {MAX_TEXT_CHARS} characters")

    ctx = await voice.tts(principal.client_id)

    # Validate/authorize the CALLER-SUPPLIED voice only, and do it before reserving quota
    # so a rejection never burns an anonymous user's daily credit. Never validate the
    # resolved voice below — that one may legitimately be a system default (e.g. Georgian).
    if req.voice_id:
        if not ctx.valid_voice_id(req.voice_id):
            raise HTTPException(status_code=400, detail="Invalid voice id")
        vcfg = await settings_store.get_voice_config()
        if vcfg["mode"] == "allowlist" and vcfg["voice_ids"]:
            allowed = set(vcfg["voice_ids"]) | await ctx.system_voice_ids()
            if req.voice_id not in allowed:
                raise HTTPException(status_code=400, detail="voice_unavailable")

    # Same rule for a CALLER-SUPPLIED model: it must be one the form offers (the provider's
    # cached list, or its built-ins when it cannot be asked), and it is checked before quota
    # for the same reason. A model the server resolves on its own is never checked — the
    # configured default may be one the list does not describe, and it worked yesterday.
    catalogue = {m["model_id"] for m in await ctx.models()}
    if req.model_id and req.model_id not in catalogue:
        return JSONResponse(status_code=400, content={
            "detail": f"Model '{req.model_id}' is not available for text-to-speech.",
            "code": "model_unavailable"})

    lang = (req.language_code or "").strip().lower()
    if lang and lang not in LANGUAGES:
        supported = ", ".join(f"{c} ({n})" for c, n in LANGUAGES.items())
        raise HTTPException(
            status_code=400,
            detail=f"Language '{lang}' is not supported for text-to-speech. Supported: {supported}.",
        )

    # Resolve model, voice, language code and settings for this provider — before quota, so a
    # text the resolved model cannot take (a provider with a lower per-request limit than the
    # product cap) is refused without a credit spent.
    plan = await ctx.plan(
        voice_id=req.voice_id, model_id=req.model_id, language_code=lang or None,
        voice_settings=req.voice_settings.model_dump(exclude_none=True) if req.voice_settings else None,
        enforce_language=req.enforce_language)
    if len(text) > plan.caps["max_chars"]:
        raise HTTPException(status_code=400,
                            detail=f"text exceeds {plan.caps['max_chars']} characters for model "
                                   f"{plan.model_id}")

    await limits.reserve(principal, "tts")

    try:
        audio = await ctx.synthesize(text, plan)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))

    await _record_tts(principal=principal, ip=client_ip(request), text=text,
                      language_code=plan.language_code, voice_id=plan.voice_id,
                      model_id=plan.model_id, audio=audio, voice_settings=plan.voice_settings)
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
