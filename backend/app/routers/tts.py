"""Text-to-speech endpoints for the user UI (ElevenLabs).

`POST /tts` answers with the clip. For a registered user and for a tenant LOGIN the clip is
also kept on the media volume so `GET /tts/history` can list it and `GET /tts/{id}/audio` play
it back — on the one deadline the Storage setting gives every stored file. Server-to-server
API-key traffic is logged but not kept (`_keeps_clip`): nobody plays back a bulk run. An
anonymous visitor's clip is kept for the same reason it always was (a paid, public endpoint has
to be investigable), and is never listed: it is keyed to an IP, and an IP is not a person.
"""
import json
import logging
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from ..db import pool
from ..services import elevenlabs, limits, media, settings_store
from ..services.auth import Principal, client_ip, resolve_principal

log = logging.getLogger("cq")

router = APIRouter(tags=["tts"])

# A caller-supplied voice_id is interpolated into the ElevenLabs URL path, so it must be
# validated regardless of any allowlist (e.g. "../../v1/dubbing" would otherwise reach a
# different endpoint with our account key).
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{16,32}$")

# Language support for TTS. `model` is the model "Auto" resolves to; `voice` is an optional
# language-specific default voice used when the caller doesn't pick one. Whether ElevenLabs
# accepts a language_code is a property of the MODEL, not the language, and is answered by
# `model_caps` below — one rule for the Auto path and for a caller who picks a model by hand.
#
# Verified against the live API (and matching the reference contact-1 project):
#   * Georgian: eleven_multilingual_v2 mispronounces it (English-accented). The correct
#     result comes from `eleven_v3` paired with a Georgian-capable voice ("Laura",
#     3b8fXc91YHS1i2DYAlBQ). A TTS->STT round-trip returns clean Georgian (lang=kat).
#     No language_code (v3 reads the Georgian script; the v3 voice 400s on "ka").
#   * English/Russian: eleven_multilingual_v2 renders correctly and accepts language_code.
GEORGIAN_VOICE = "3b8fXc91YHS1i2DYAlBQ"  # "Laura - Natural & Grounded" (shared voice)

LANGUAGES: dict[str, dict] = {
    "en": {"name": "English",  "model": "eleven_multilingual_v2", "voice": None, "note": ""},
    "ru": {"name": "Russian",  "model": "eleven_multilingual_v2", "voice": None, "note": ""},
    "ka": {"name": "Georgian", "model": "eleven_v3", "voice": GEORGIAN_VOICE,
           "note": "Georgian uses the eleven_v3 model with a Georgian-capable voice for "
                   "correct pronunciation. Leave the voice on default for best results."},
}

# The text length /tts accepts regardless of model: it is the anonymous quota unit the admin
# panel counts in, and every model this product exposes takes at least this much.
MAX_TEXT_CHARS = 5000

# Where the product UI lets `speed` go. ElevenLabs takes a wider range; these are the values
# past which a clip stops sounding like the voice, so the API refuses them up front (422)
# rather than shipping a clip nobody wanted and billing for it.
SPEED_MIN, SPEED_MAX = 0.7, 1.2


class VoiceSettingsIn(BaseModel):
    """The caller's wishes, bounded to what ElevenLabs documents for any model.

    Bounds here mean an out-of-range number is a 422 before any quota is reserved; what the
    resolved MODEL does with an in-range one (drop it, snap it) is `shape_voice_settings`'s
    job, so a caller pointing at v3 with a style slider gets a clip, not an error.
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
    # produces the same ElevenLabs body it always did (no voice_settings key at all).
    voice_settings: VoiceSettingsIn | None = None
    enforce_language: bool | None = None


@router.get("/languages")
async def languages():
    """Languages the TTS feature supports, for the UI selector. `model` is what Auto picks for
    the language, so the form can show that model's controls before the customer touches the
    Model dropdown."""
    return [
        {"code": code, "name": info["name"], "note": info.get("note", ""),
         "model": info["model"]}
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

    system = system_voice_ids(cfg)

    def _mark(items: list[dict]) -> list[dict]:
        # `is_default` lets the customer dropdown label the voice the server would pick on
        # its own, so "Default voice" and the named default read as the same thing.
        return [dict(v, is_default=v.get("voice_id") in system) for v in items]

    if vcfg["mode"] != "allowlist" or not vcfg["voice_ids"]:
        return _mark(live)
    by_id = {v.get("voice_id"): v for v in live if v.get("voice_id")}
    # The admin panel shows system defaults as an always-on tick and deliberately leaves them
    # OUT of voice_ids on save, and /tts accepts them regardless of the allowlist. So the
    # customer list has to add them back here — otherwise the one voice the operator was told
    # is "always on" is the one voice customers cannot see or choose by name. Defaults come
    # first (the Georgian voice, then the configured default), then the admin's own order.
    default_order = [LANGUAGES["ka"]["voice"], cfg.get("tts_voice_id")]
    default_order += sorted(system - set(default_order))
    ordered = [i for i in default_order if i and i in by_id]
    ordered += [i for i in vcfg["voice_ids"] if i in by_id and i not in system]
    seen: set[str] = set()
    picked = [by_id[i] for i in ordered if not (i in seen or seen.add(i))]
    # An allowlist that matches nothing live (key rotated, voices deleted) must not empty
    # the customer dropdown.
    return _mark(picked or live)


# ---- models -----------------------------------------------------------------
# Models GET /v1/models returns that the customer form must NOT offer. Legacy (v1 models
# and the English-only flash_v2 predate everything this product runs on), deprecated aliases
# (turbo_v2_5 IS flash_v2_5 and turbo_v2 IS flash_v2 — showing both means two rows that sound
# identical), and one that is not text-to-speech at all (english_sts_v2 is speech-to-speech).
# A model_id in here is refused by POST /tts too, so a hidden model cannot be reached by
# hand-writing the request.
MODEL_HIDE = frozenset({
    "eleven_multilingual_v1", "eleven_monolingual_v1", "eleven_flash_v2", "eleven_turbo_v2",
    "eleven_turbo_v2_5", "eleven_english_sts_v2",
})

# Display order: the default the product ships with, then the model Georgian needs, then the
# cheap/fast one, then whatever else the account has, alphabetically.
MODEL_ORDER = ("eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5")

# Models whose language_code ElevenLabs ENFORCES (it changes what comes out). Elsewhere it is
# ignored (harmless, sent anyway on the ones that take it) or rejected (v3 — the Georgian
# voice 400s on it, which is the whole reason the Georgian path never sent one).
_LANGUAGE_ENFORCING = frozenset({"eleven_flash_v2_5", "eleven_turbo_v2_5"})

# The v3 stability presets — Creative / Natural / Robust. ElevenLabs rejects any other value
# for v3, so a slider position has to be snapped to one of these before it is sent.
V3_STABILITY_PRESETS = (0.0, 0.5, 1.0)


def model_caps(model: dict) -> dict:
    """What one model accepts — THE place these rules live.

    Everything the customer form shows and everything the request shaper drops derives from
    this one function, so the two can never disagree. Facts come from the ElevenLabs model
    record where it states them (style, speaker boost, max length); the v3 family's presets,
    missing speed control and language_code rejection are documented behaviour the API does
    not advertise per model, hence the prefix rule.
    """
    model_id = str(model.get("model_id") or "")
    presets = model_id.startswith("eleven_v3")
    if model_id in _LANGUAGE_ENFORCING:
        language_code = "enforced"
    elif presets:
        language_code = "rejected"
    else:
        language_code = "ignored"
    # None (field absent — the built-in fallback, or a model the list did not describe) is
    # read as "supported": ElevenLabs ignores a field a model has no use for, whereas hiding a
    # control the model does take is a lost feature.
    style = model.get("can_use_style")
    boost = model.get("can_use_speaker_boost")
    limit = model.get("maximum_text_length_per_request")
    return {
        "presets": presets,
        "style": (style is not False) and not presets,      # v3 takes emotion from audio tags
        "speaker_boost": boost is not False,
        "speed": not presets,                               # v3 paces itself
        "language_code": language_code,
        "max_chars": min(MAX_TEXT_CHARS, int(limit) if limit else MAX_TEXT_CHARS),
    }


def shape_voice_settings(caps: dict, vs: dict | None) -> dict | None:
    """Reduce a caller's voice_settings to what the resolved model accepts. Pure.

    Drops the keys the model has no control for, clamps the rest to the documented ranges,
    snaps stability to the nearest v3 preset, and returns None when nothing survives — so the
    ElevenLabs body carries no `voice_settings` at all rather than an empty object. One info
    line names what changed, because "why does my clip sound the same with style at 0.9" is
    a support question whose answer is otherwise nowhere.
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
            num = min(V3_STABILITY_PRESETS, key=lambda preset: abs(preset - num))
        if num != val:
            changed.append(f"{key} {val}->{num}")
        out[key] = num
    if dropped or changed:
        log.info("tts voice_settings shaped: dropped=%s adjusted=%s",
                 ",".join(dropped) or "-", ",".join(changed) or "-")
    return out or None


# What the customer form gets when GET /v1/models is unreachable or the key lacks
# models_read: exactly the two models this code already synthesizes with, described the way
# the live record would describe them. The form keeps working; only the extra models vanish.
FALLBACK_MODELS: tuple[dict, ...] = (
    {"model_id": "eleven_multilingual_v2", "name": "Multilingual v2",
     "description": "Stable, natural speech in 29 languages.",
     "can_do_text_to_speech": True, "can_use_style": True, "can_use_speaker_boost": True,
     "languages": ["en", "ru"], "maximum_text_length_per_request": 10000},
    {"model_id": "eleven_v3", "name": "Eleven v3",
     "description": "Most expressive model; the one Georgian needs.",
     "can_do_text_to_speech": True, "can_use_style": False, "can_use_speaker_boost": True,
     "languages": ["en", "ru", "ka"], "maximum_text_length_per_request": 5000},
)

# ElevenLabs' model catalogue changes a few times a year, so ten minutes is a lifetime; the
# fallback is re-tried after one minute so a recovered API is seen without a restart, while
# a down one is not hit on every keystroke of the form. Module-level, so per-process — right
# for a single uvicorn worker (same assumption as settings_store's kill-switch cache).
MODELS_TTL_S = 600.0
MODELS_FALLBACK_TTL_S = 60.0
_models_cache: tuple[float, list[dict], bool] | None = None   # (fetched_at, models, live)


def _visible(models: list[dict]) -> list[dict]:
    """Hide the legacy/alias/non-TTS ids and put the product's models first."""
    keep = {m["model_id"]: m for m in models
            if m.get("model_id") and m["model_id"] not in MODEL_HIDE
            and m.get("can_do_text_to_speech") is not False}
    ordered = [keep.pop(mid) for mid in MODEL_ORDER if mid in keep]
    ordered += [keep[mid] for mid in sorted(keep)]
    return ordered


async def visible_models(cfg: dict) -> list[dict]:
    """The models POST /tts will accept and GET /tts/models will list, cached. Fails OPEN to
    `FALLBACK_MODELS` so an ElevenLabs outage costs the extra models, not the feature."""
    global _models_cache
    now = time.monotonic()
    if _models_cache:
        fetched_at, models, live = _models_cache
        if (now - fetched_at) < (MODELS_TTL_S if live else MODELS_FALLBACK_TTL_S):
            return models
    try:
        models, live = _visible(await elevenlabs.list_models(cfg["elevenlabs_api_key"])), True
        if not models:
            # An account whose list came back empty is not one we can synthesize with anyway;
            # treat it like an outage so the built-ins stay offered.
            raise RuntimeError("ElevenLabs returned no usable text-to-speech model")
    except Exception as exc:  # noqa: BLE001 — every failure is the same answer: the built-ins
        log.warning("tts model list unavailable, using built-in fallback: %s", exc)
        models, live = _visible(list(FALLBACK_MODELS)), False
    _models_cache = (now, models, live)
    return models


def _public_model(model: dict) -> dict:
    caps = model_caps(model)
    return {
        "model_id": model["model_id"],
        "name": model.get("name") or model["model_id"],
        "description": model.get("description") or "",
        "max_chars": caps.pop("max_chars"),
        "languages": list(model.get("languages") or []),
        "supports": caps,
    }


@router.get("/tts/models")
async def tts_models():
    """Public: the models a customer may pick, with what each one accepts, so the form shows
    the right controls per model instead of a slider the model will ignore."""
    cfg = await settings_store.get_effective()
    return [_public_model(m) for m in await visible_models(cfg)]


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

    `voice_settings` is the SHAPED dict — what ElevenLabs was actually sent, not what the
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

    # Same rule for a CALLER-SUPPLIED model: it must be one the form offers (the cached list,
    # or the two built-ins when ElevenLabs cannot be asked), and it is checked before quota
    # for the same reason. A model the server resolves on its own is never checked — the
    # configured default may be one the list does not describe, and it worked yesterday.
    catalogue = {m["model_id"]: m for m in await visible_models(cfg)}
    if req.model_id and req.model_id not in catalogue:
        return JSONResponse(status_code=400, content={
            "detail": f"Model '{req.model_id}' is not available for text-to-speech.",
            "code": "model_unavailable"})

    await limits.reserve(principal, "tts")

    # Resolve model and voice from the selected language.
    lang = (req.language_code or "").strip().lower()
    if lang:
        info = LANGUAGES.get(lang)
        if info is None:
            supported = ", ".join(f"{c} ({i['name']})" for c, i in LANGUAGES.items())
            raise HTTPException(
                status_code=400,
                detail=f"Language '{lang}' is not supported for text-to-speech. Supported: {supported}.",
            )
        model_id = req.model_id or info["model"]
        # Voice priority: explicit request > language default voice > configured default.
        voice_id = req.voice_id or info.get("voice") or cfg["tts_voice_id"]
    else:
        # No language selected — keep prior behaviour (configured model + voice).
        model_id = req.model_id or cfg["tts_model"]
        voice_id = req.voice_id or cfg["tts_voice_id"]

    # What the resolved model accepts decides both the language_code and the settings. The
    # code is sent wherever the model takes it (enforced) or shrugs at it (ignored — the
    # multilingual_v2 path has always sent it and keeps doing so), never where it 400s (v3),
    # and not at all when the caller asked us to leave the language to the model.
    caps = model_caps(catalogue.get(model_id) or {"model_id": model_id})
    language_code = None
    if lang and caps["language_code"] != "rejected" and req.enforce_language is not False:
        language_code = lang
    voice_settings = shape_voice_settings(
        caps, req.voice_settings.model_dump(exclude_none=True) if req.voice_settings else None)

    try:
        audio = await elevenlabs.text_to_speech(
            text, cfg["elevenlabs_api_key"], voice_id, model_id, language_code,
            voice_settings=voice_settings,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))

    await _record_tts(principal=principal, ip=client_ip(request), text=text,
                      language_code=language_code, voice_id=voice_id, model_id=model_id,
                      audio=audio, voice_settings=voice_settings)
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
