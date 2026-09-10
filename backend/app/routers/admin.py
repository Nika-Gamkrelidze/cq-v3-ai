"""Admin panel API: login, view/update integration settings, and test connectivity.

The super-admin logs in with username + password (POST /admin/login), which returns
the admin token. That token then authorizes every other admin endpoint via the
X-Admin-Token header.
"""
import asyncio
import json
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..config import settings
from ..db import pool
from ..services import (ai_config, chat_credentials, chat_store, claude, health_metrics, limits,
                        sentiment, settings_store, usage, voice)
from ..services import transcription as transcription_svc
from .kb import count_public_documents

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(x_admin_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginRequest):
    """Exchange super-admin username+password for the admin token."""
    ok_user = secrets.compare_digest(req.username, settings.superadmin_username)
    ok_pass = secrets.compare_digest(req.password, settings.superadmin_password)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"token": settings.admin_token, "username": settings.superadmin_username}


class SettingsPatch(BaseModel):
    anthropic_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    llm_model: str | None = None
    stt_model: str | None = None
    tts_model: str | None = None
    tts_voice_id: str | None = None
    analysis_instructions: str | None = None


@router.get("/settings", dependencies=[Depends(require_admin)])
async def get_settings():
    return await settings_store.get_public()


@router.put("/settings", dependencies=[Depends(require_admin)])
async def put_settings(patch: SettingsPatch):
    await settings_store.update(patch.model_dump(exclude_none=True))
    return await settings_store.get_public()


# ---------------------------------------------------------------------------
# Capability probes — ONE ROW PER OPERATION, not per vendor.
#
# A green vendor row used to be a lie: GET /v1/voices requires only voices_read (and on
# some accounts no permission at all), while the two operations the product runs on —
# POST /v1/speech-to-text and POST /v1/text-to-speech/{voice_id} — need separate key
# permissions, separate model entitlements and separate credit balances. A key scoped
# without speech_to_text reported OK for ElevenLabs while every upload 401'd. There is no
# ElevenLabs endpoint that introspects the calling key's own scopes, so the only honest
# check is to actually perform each operation, as cheaply as possible.
#
# Each probe returns {"level": "ok"|"warn"|"fail", "detail": str} and may add a machine
# "code" (+ "scope") so the panel renders an actionable hint instead of raw JSON.
# ---------------------------------------------------------------------------
PROBE_TEXT = "ok"          # 2 billed characters
PROBE_TEXT_KA = "დიახ"     # 4 billed characters — exercises the Georgian eleven_v3 path


async def _probe_database() -> dict:
    async with pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"level": "ok", "detail": "connected"}


async def _probe_ffmpeg() -> dict:
    from ..services.audio import ffmpeg_available
    if ffmpeg_available():
        return {"level": "ok", "detail": "ffmpeg on PATH"}
    # audio.py falls back to the original bytes, so this degrades rather than breaks.
    return {"level": "warn", "code": "ffmpeg_missing",
            "detail": "ffmpeg is not installed — uploads go to Scribe untranscoded."}


# The voice probes below test the DEPLOYMENT'S voice provider — what an anonymous caller and
# every tenant without an assigned or bring-your-own connection runs on (`voice.tts(None)`,
# `voice.transcribe(None, ...)`). A tenant's own connection is tested from the registry's
# "Test connection" button (`voice.probe`). The report keys keep their historic
# `elevenlabs_*` names for the panel; the detail names the provider that actually answered.
async def _probe_voices() -> dict:
    ctx = await voice.tts(None)
    voices = await ctx.voices()
    if not voices:
        return {"level": "warn", "code": "no_voices",
                "detail": f"{ctx.provider}: authenticated, but this account lists 0 voices"}
    default = await ctx.default_voice()
    if default and default not in {v.get("voice_id") for v in voices} \
            and default not in await ctx.system_voice_ids():
        return {"level": "warn", "code": "voice_missing",
                "detail": f"{len(voices)} voices available; configured default {default} is not one"}
    return {"level": "ok", "detail": f"{ctx.provider}: {len(voices)} voices available"}


async def _probe_stt() -> dict:
    """A real speech-to-text call on 0.4 s of generated silence. Costs a fraction of a
    second of transcription and is the ONLY proof the key may transcribe. It also
    exercises ffmpeg, the multipart shape and the configured model in one call.

    It runs with the DEPLOYMENT'S transcription defaults (language, diarize, keyterms and —
    the reason this matters — the audio format), so this one button also answers "does
    the provider actually accept the encoding we are about to send every customer's call
    in". That is the check that has to pass before the default is moved off lossy MP3.
    """
    stt = await transcription_svc.get_default(force=True)
    try:
        out = await voice.transcribe(None, voice.silence_wav(), "probe.wav", "audio/wav",
                                     transcription=stt, timeout=60.0)
    except voice.VoiceError as exc:
        # A 400/422 that is NOT auth/permission/credit means the request was authorised and
        # only the payload was rejected — don't cry wolf about a working capability, but DO
        # name the format, because that is the most likely thing to have been refused.
        if exc.code == "http" and exc.status in (400, 422):
            return {"level": "warn", "code": "probe_rejected",
                    "detail": f"audio_format={stt['audio_format']}: {exc}"}
        raise
    return {"level": "ok",
            "detail": f"{out.get('provider')} model {out.get('model')} accepted a 0.4 s probe "
                      f"clip as {stt['audio_format']} (lang={out.get('language_code') or 'n/a'})"}


async def _probe_tts() -> dict:
    """The configured default model + default voice, no language — what a caller who names
    nothing gets."""
    ctx = await voice.tts(None)
    plan = await ctx.plan()
    if not plan.voice_id:
        return {"level": "warn", "code": "no_voice_configured", "detail": "no default voice set"}
    audio = await ctx.synthesize(PROBE_TEXT, plan)
    return {"level": "ok",
            "detail": f"{ctx.provider} model {plan.model_id} returned {len(audio)} bytes"}


async def _probe_tts_ka() -> dict:
    """Georgian is the load-bearing TTS path (CLAUDE.md section 4): on ElevenLabs, eleven_v3
    + a Georgian-capable shared voice. eleven_v3 is separately entitled and the shared voice
    may not be in the account listing at all, so the voice list cannot vouch for it. The
    model and voice come from the provider's own per-language default, exactly as /tts
    resolves them for `language_code=ka`."""
    ctx = await voice.tts(None)
    plan = await ctx.plan(language_code="ka")
    audio = await ctx.synthesize(PROBE_TEXT_KA, plan)
    return {"level": "ok",
            "detail": f"{plan.model_id} + voice {plan.voice_id} returned {len(audio)} bytes"}


async def _probe_embeddings() -> dict:
    from ..services import embeddings as emb
    provider = await emb.get_provider()
    health = await provider.health()
    detail = f"model {health.get('model')} returned dim {health.get('dim')}"
    if health.get("dim") and provider.dim and health["dim"] != provider.dim:
        return {"level": "fail",
                "detail": f"{detail} but configured dim is {provider.dim} — "
                          f"set EMBEDDING_DIM to {health['dim']} and re-embed."}
    return {"level": "ok", "detail": detail}


async def _probe_sentiment(cfg: dict) -> dict:
    """Is the prosody sidecar reachable, and has it actually loaded its model?

    Worth its own row because this capability fails SILENTLY by design: sentiment degrades to
    the text half and every call still returns 200, so a sidecar that never got built (or is
    still downloading its model on a fresh deploy) is invisible from the outside. Reported as
    a warning rather than a failure — text-only sentiment is a working product, just a smaller
    one.
    """
    st = await sentiment.status(force=True)          # a probe the operator pressed: never cached
    state = st["state"]
    if state == "ok":
        return {"level": "ok", "detail": st["detail"]}
    if state == "warming":
        # Normal for the first minute after a deploy, and ONLY then — `warm_error` is what
        # separates this from a model that will never load, so it is not the same row.
        return {"level": "ok", "detail": st["detail"] + " (normal right after a deploy)"}
    # model_error is a real failure, not a warning about an absent optional service: the
    # sidecar IS deployed, it just cannot serve. Reporting it green is what hid this for weeks.
    level = "error" if state == "model_error" else "warn"
    return {"level": level, "code": f"sentiment_{state}", "detail": st["detail"]}


async def _probe_claude(cfg: dict) -> dict:
    analysis = await claude.analyze(
        "Speaker 1: Hello, this is a connectivity test. Speaker 2: Acknowledged.",
        cfg["anthropic_api_key"], cfg["llm_model"], cfg["analysis_instructions"])
    return {"level": "ok", "detail": f"model {cfg['llm_model']} responded",
            "sample": analysis.get("summary", "")[:120]}


async def _probe_claude_factcheck(cfg: dict) -> dict:
    """Deep only: submit_claims + submit_verifications use different tool schemas from
    submit_analysis, and submit_verifications is the one Anthropic call in the app that
    sends no `system` param — a shape the shallow probe never validates."""
    from ..services import factcheck
    n = await factcheck.probe_tools(cfg["anthropic_api_key"], cfg["llm_model"])
    return {"level": "ok", "detail": f"claim + verification tools responded ({n} claims)"}


async def _probe_scoring(cfg: dict) -> dict:
    from ..services import scoring
    demo = {"version": 0, "rubric": "", "dimensions": [
        {"key": "probe", "name": "Probe", "description": "connectivity probe",
         "weight": 100, "guidance": "Always score 50."}]}
    out = await scoring.run_scoring(
        "Speaker 1: Hello. Speaker 2: Acknowledged.", demo,
        cfg["anthropic_api_key"], cfg["llm_model"])
    # `weighted_total` is the key build_result has always returned; `total` never existed, so
    # this line reported "total None" on every successful probe.
    return {"level": "ok",
            "detail": f"scoring tool responded (total {(out or {}).get('weighted_total')})"}


async def _capture(coro) -> dict:
    """Never let one probe fail the whole report, and keep the historic {ok, detail} shape."""
    try:
        out = await coro
    except voice.VoiceError as exc:
        out = {"level": "fail", "detail": str(exc), "code": exc.code, "scope": exc.scope}
    except Exception as exc:  # noqa: BLE001
        out = {"level": "fail", "detail": str(exc)}
    out.setdefault("level", "ok")
    out["ok"] = out["level"] != "fail"
    return out


@router.post("/test", dependencies=[Depends(require_admin)])
async def test_integrations(deep: bool = False):
    """Verify every third-party capability the product actually uses.

    Costs a few voice-provider credits per click (one 0.4 s STT clip + 6 TTS characters) —
    that is the price of never showing a green tick for something that is broken.
    `deep=true` adds the three non-analysis Anthropic tool schemas.
    """
    cfg = await settings_store.get_effective()
    probes = {
        "database": _probe_database(),
        "ffmpeg": _probe_ffmpeg(),
        "elevenlabs_voices": _probe_voices(),
        "elevenlabs_stt": _probe_stt(),
        "elevenlabs_tts": _probe_tts(),
        "elevenlabs_tts_ka": _probe_tts_ka(),
        "embeddings": _probe_embeddings(),
        # NB: "claude_analysis", not "claude" — the vendor roll-up below writes out["claude"],
        # which would otherwise clobber this probe's own detail.
        "claude_analysis": _probe_claude(cfg),
        "sentiment": _probe_sentiment(cfg),
    }
    if deep:
        probes["claude_factcheck"] = _probe_claude_factcheck(cfg)
        probes["claude_scoring"] = _probe_scoring(cfg)

    keys = list(probes)
    results = await asyncio.gather(*(_capture(c) for c in probes.values()))
    out = dict(zip(keys, results))
    out["order"] = keys
    # Vendor roll-ups, worst-wins — kept so any external consumer of the old shape
    # (and the old two-row panel) degrades honestly instead of silently going green.
    for vendor, members in (("elevenlabs", [k for k in keys if k.startswith("elevenlabs_")]),
                            ("claude", [k for k in keys if k.startswith("claude")])):
        bad = [out[k] for k in members if not out[k]["ok"]]
        out[vendor] = bad[0] if bad else {"ok": True, "level": "ok",
                                          "detail": f"{len(members)} checks passed"}
    return out


# ---- Embeddings provider config -------------------------------------------
class EmbeddingPatch(BaseModel):
    provider: str | None = None       # tei | openai
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    dim: int | None = None


@router.get("/embeddings", dependencies=[Depends(require_admin)])
async def get_embeddings():
    return await settings_store.get_embedding_public()


@router.put("/embeddings", dependencies=[Depends(require_admin)])
async def put_embeddings(patch: EmbeddingPatch):
    await settings_store.set_embedding_config(patch.model_dump(exclude_none=True))
    return await settings_store.get_embedding_public()


@router.post("/embeddings/test", dependencies=[Depends(require_admin)])
async def test_embeddings():
    # Same probe the full report uses, so the two buttons can never disagree.
    return await _capture(_probe_embeddings())


# ---- Anonymous (no-tenant) usage limits -----------------------------------
class AnonPatch(BaseModel):
    enabled: bool | None = None
    max_analyses_per_day: int | None = None
    max_audio_mb: int | None = None
    max_tts_per_day: int | None = None
    features: dict | None = None
    # Days to keep an unregistered visitor's IP, audio and text. 0 means keep indefinitely,
    # which is a deliberate operator choice — set_anonymous_config drops None, not 0, so
    # "0" really is storable rather than silently ignored.
    #
    # Retention is now ONE number for every stored recording (PUT /admin/storage). This field
    # stays because older admin pages and operator scripts still send it, but it is forwarded
    # to the storage blob rather than left to rot: `get_storage_config` only consults the
    # anonymous blob until the storage key exists, so a write that landed here alone would be
    # accepted, echoed back, and then silently do nothing to any deadline.
    retention_days: int | None = Field(default=None, ge=0, le=3650)


@router.get("/anonymous-limits", dependencies=[Depends(require_admin)])
async def get_anon_limits():
    return await settings_store.get_anonymous_config()


@router.put("/anonymous-limits", dependencies=[Depends(require_admin)])
async def put_anon_limits(patch: AnonPatch):
    await settings_store.set_anonymous_config(patch.model_dump(exclude_none=True))
    if patch.retention_days is not None:
        # One number, two doors: forwarded so the old field keeps meaning what its label says
        # instead of becoming an inert 200 the moment /admin/storage has been used once.
        await settings_store.set_storage_config({"retention_days": patch.retention_days})
    return await settings_store.get_anonymous_config()


# ---- Storage retention (every stored recording / TTS clip, whoever submitted it) ------
class StoragePatch(BaseModel):
    # 0 = keep forever, a deliberate operator choice (see settings_store.STORAGE_DEFAULTS).
    retention_days: int = Field(ge=0, le=3650)


@router.get("/storage", dependencies=[Depends(require_admin)])
async def get_storage():
    return await settings_store.get_storage_config()


@router.put("/storage", dependencies=[Depends(require_admin)])
async def put_storage(patch: StoragePatch):
    try:
        return await settings_store.set_storage_config(patch.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- Server health (the console's Health tab) ----------------------------------------------
# Reads over `system_metrics` / `tenant_load`, written by the api's sampler + load flusher
# (main.py) and trimmed by the worker's `health_purge`. All of it lives in
# services/health_metrics; these routes only choose the window.
class HealthSettingsPatch(BaseModel):
    # No Field bounds on purpose: the store validates (retention 1..365 days, interval
    # 5..300 s) and raises ValueError, which this router turns into the same 400 + `code` +
    # `field` shape the transcription settings use. Pydantic bounds here would answer the
    # out-of-range case with a 422 instead, and the console branches on the 400.
    retention_days: int | None = None
    sample_interval_s: int | None = None


@router.get("/health/overview", dependencies=[Depends(require_admin)])
async def health_overview():
    return await health_metrics.overview()


@router.get("/health/series", dependencies=[Depends(require_admin)])
async def health_series(range_: str = Query("24h", alias="range")):
    return await health_metrics.series(range_)


@router.get("/health/tenants", dependencies=[Depends(require_admin)])
async def health_tenants(range_: str = Query("24h", alias="range")):
    return await health_metrics.tenant_table(range_)


@router.get("/health/settings", dependencies=[Depends(require_admin)])
async def get_health_settings():
    return await settings_store.get_health_config()


@router.put("/health/settings", dependencies=[Depends(require_admin)])
async def put_health_settings(patch: HealthSettingsPatch):
    try:
        return await settings_store.set_health_config(patch.model_dump(exclude_none=True))
    except ValueError as exc:
        # The store raises HealthSettingError with the offending `field`; the message-scan is
        # only the fallback for a plain ValueError, so the tab can point at the input rather
        # than at the form either way.
        field = getattr(exc, "field", None) or next(
            (f for f in ("retention_days", "sample_interval_s") if f in str(exc)), None)
        # A JSONResponse, not HTTPException(detail={...}): the latter nests the dict under
        # `detail`, and the console reads `code` and `field` at the top level.
        return JSONResponse(status_code=400, content={
            "detail": str(exc), "code": "invalid_health_setting", "field": field})


# ---- Registered-user tier: daily limits + feature switches ---------------------------
class RegisteredPatch(BaseModel):
    enabled: bool | None = None                  # sign-ups open — never locks existing users out
    max_analyses_per_day: int | None = Field(default=None, ge=0)
    max_audio_mb: int | None = Field(default=None, ge=0)
    max_tts_per_day: int | None = Field(default=None, ge=0)
    max_conversions_per_day: int | None = Field(default=None, ge=0)
    # Sign-ups allowed per IP per day (0 = uncapped, still counted). Not one of the tier's
    # per-account caps: it bounds how many accounts one visitor can mint, which is what stops
    # an open form from being an unlimited-quota dispenser. Absent from REGISTERED_DEFAULTS, so
    # `routers/auth.py::REGISTRATIONS_PER_DAY` is what applies until an operator sets it.
    max_registrations_per_day: int | None = Field(default=None, ge=0)
    features: dict[str, bool] | None = None


@router.get("/registered-limits", dependencies=[Depends(require_admin)])
async def get_registered_limits():
    return await settings_store.get_registered_config()


@router.put("/registered-limits", dependencies=[Depends(require_admin)])
async def put_registered_limits(patch: RegisteredPatch):
    return await settings_store.set_registered_config(patch.model_dump(exclude_none=True))


# ---------------------------------------------------------------------------
# Registered users (app_users)
#
# The console's view of self-service accounts. There is no mail provider, so the reset route
# below is the ONLY password-recovery path a registered user has — the new password is
# generated here and shown to the operator exactly once, never stored in the clear.
#
# `limits` is the per-user override blob `services/limits.py` consults before the registered
# tier: only the four cap keys are accepted, and a PUT REPLACES the blob (null drops a key) so
# the console's modal can send the whole form without a separate "clear" action.
# ---------------------------------------------------------------------------
USER_LIMIT_KEYS = ("max_analyses_per_day", "max_tts_per_day", "max_conversions_per_day",
                   "max_audio_mb")
_USER_COLS = "id, email, display_name, is_active, created_at, last_login_at, limits"
RESET_PASSWORD_BYTES = 9        # token_urlsafe(9) is 12 characters


class UserPatch(BaseModel):
    is_active: bool | None = None
    display_name: str | None = None
    limits: dict | None = None


def _user_overrides(limits_in: dict) -> dict:
    """Validate an override blob: known keys only, non-negative whole numbers; null drops the
    key. Refused loudly rather than stored as-is because `limits._user_cap` IGNORES a value it
    cannot parse — which would leave the operator believing a cap they never got."""
    out = {}
    for key, value in (limits_in or {}).items():
        if key not in USER_LIMIT_KEYS:
            raise HTTPException(status_code=400,
                                detail=f"Unknown limit '{key}'. Allowed: {', '.join(USER_LIMIT_KEYS)}")
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HTTPException(status_code=400,
                                detail=f"{key} must be a non-negative whole number")
        out[key] = value
    return out


def _user_out(row, used: dict | None = None) -> dict:
    raw = row["limits"]
    lim = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return {"id": str(row["id"]), "email": row["email"], "display_name": row["display_name"],
            "is_active": row["is_active"], "created_at": row["created_at"],
            "last_login_at": row["last_login_at"], "limits": lim if isinstance(lim, dict) else {},
            "used": {"analyses": (used or {}).get("analyses", 0),
                     "tts": (used or {}).get("tts", 0),
                     "conversions": (used or {}).get("conversions", 0)}}


@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users(q: str = "", limit: int = 50):
    """Newest first, with today's usage. `q` matches email or display name, case-insensitively."""
    limit = max(1, min(int(limit), 500))
    needle = (q or "").strip()
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_USER_COLS} FROM app_users
            WHERE $1 = '' OR email ILIKE $2 OR coalesce(display_name, '') ILIKE $2
            ORDER BY created_at DESC
            LIMIT $3
            """, needle, f"%{needle}%", limit)
    used = await limits.usage_today([f"user:{r['id']}" for r in rows])
    return {"users": [_user_out(r, used.get(f"user:{r['id']}")) for r in rows]}


@router.put("/users/{user_id}", dependencies=[Depends(require_admin)])
async def update_user(user_id: str, body: UserPatch):
    sets, vals = [], []
    if body.is_active is not None:
        vals.append(body.is_active)
        sets.append(f"is_active = ${len(vals)}")
    if body.display_name is not None:
        vals.append(body.display_name.strip()[:120] or None)
        sets.append(f"display_name = ${len(vals)}")
    if body.limits is not None:
        vals.append(json.dumps(_user_overrides(body.limits)))
        sets.append(f"limits = ${len(vals)}::jsonb")
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update")
    vals.append(user_id)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE app_users SET {', '.join(sets)} WHERE id = ${len(vals)} RETURNING {_USER_COLS}",
            *vals)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    used = await limits.usage_today([f"user:{row['id']}"])
    return _user_out(row, used.get(f"user:{row['id']}"))


@router.post("/users/{user_id}/reset-password", dependencies=[Depends(require_admin)])
async def reset_user_password(user_id: str):
    """Set a fresh random password and return it ONCE. Only the hash is stored, so an operator
    who loses this response resets again rather than recovering it."""
    from ..services import auth
    password = secrets.token_urlsafe(RESET_PASSWORD_BYTES)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE app_users SET password_hash = $2 WHERE id = $1 RETURNING id, email",
            user_id, await asyncio.to_thread(auth.hash_password, password))
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"id": str(row["id"]), "email": row["email"], "password": password,
            "warning": "Shown once — pass it to the user now; it cannot be recovered."}


@router.delete("/users/{user_id}", dependencies=[Depends(require_admin)])
async def delete_user(user_id: str):
    """Remove the account row ONLY. `user_id` columns carry no foreign key on purpose (see
    db/workbench.sql): recordings, summaries, TTS clips and usage counters stay, and the
    retention purge is what removes their files on its normal schedule."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM app_users WHERE id = $1 RETURNING id, email", user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"id": str(row["id"]), "email": row["email"], "deleted": True,
            "note": "Account removed. Their recordings, summaries and TTS clips are kept until "
                    "the retention purge removes the stored files on its normal schedule."}


# ---------------------------------------------------------------------------
# Customer-visible voices: the admin sees EVERY voice (unfiltered) to pre-listen and
# curate. The public GET /voices is the filtered one — the panel must not use it, or the
# admin couldn't preview a hidden voice.
# ---------------------------------------------------------------------------
class VoicePatch(BaseModel):
    mode: str | None = None
    voice_ids: list[str] | None = None


@router.get("/voices", dependencies=[Depends(require_admin)])
async def get_voices():
    # The deployment's provider: curation is one allowlist, applied to whatever voice list
    # the default connection (or the legacy key) answers with.
    ctx = await voice.tts(None)
    vcfg = await settings_store.get_voice_config()
    sysids = await ctx.system_voice_ids()
    error, live = None, []
    try:
        live = await ctx.voices()
    except Exception as exc:  # noqa: BLE001 — report in-band so the panel can render an error
        error = str(exc)

    by_id = {v.get("voice_id"): dict(v) for v in live if v.get("voice_id")}
    # System voices (e.g. the shared Georgian voice) may not be in the account listing at
    # all — surface them anyway so the operator can see what is always on.
    for vid in sysids:
        if vid not in by_id:
            by_id[vid] = {"voice_id": vid, "name": "System default voice",
                          "category": "shared", "preview_url": None}

    picked = set(vcfg["voice_ids"])
    ordered = [by_id[i] for i in vcfg["voice_ids"] if i in by_id]
    ordered += [v for k, v in by_id.items() if k not in picked]
    voices_out = [{**v,
                   "selected": v["voice_id"] in picked or v["voice_id"] in sysids,
                   "system": v["voice_id"] in sysids} for v in ordered]
    missing = [i for i in vcfg["voice_ids"] if i not in {v.get("voice_id") for v in live}]
    return {"mode": vcfg["mode"], "voice_ids": vcfg["voice_ids"],
            "voices": voices_out, "missing": missing, "error": error}


@router.put("/voices", dependencies=[Depends(require_admin)])
async def put_voices(patch: VoicePatch):
    ctx = await voice.tts(None)
    if patch.mode is not None and patch.mode not in ("all", "allowlist"):
        raise HTTPException(status_code=400, detail="mode must be 'all' or 'allowlist'")
    current = await settings_store.get_voice_config()
    mode = patch.mode or current["mode"]
    ids = patch.voice_ids if patch.voice_ids is not None else current["voice_ids"]
    if mode == "allowlist" and not ids:
        raise HTTPException(status_code=400, detail="Select at least one voice.")
    for i in (patch.voice_ids or []):
        if not ctx.valid_voice_id(i):
            raise HTTPException(status_code=400, detail=f"Invalid voice id: {i[:24]}")
    await settings_store.set_voice_config(patch.model_dump(exclude_none=True))
    return await get_voices()


# ---------------------------------------------------------------------------
# Integration credentials (the chat site)
#
# Superadmin-only, and the ONLY place a credential is minted. The plaintext key is returned
# exactly once — at creation and at rotation — and is never recoverable afterwards, because only
# sha256(secret) is stored. If an operator loses it, the answer is rotate, not recover.
#
# See services/chat_credentials.py for why the tenant is a grant row rather than a column.
# ---------------------------------------------------------------------------
class IntegrationCreate(BaseModel):
    name: str
    scopes: list[str]
    grants: list[str]        # tenant selectors: uuid or clients.slug


@router.post("/integrations", dependencies=[Depends(require_admin)], status_code=201)
async def create_integration(body: IntegrationCreate):
    # One credential, many tenants — decided 2026-09-08. The P1 pilot capped issuance at exactly
    # one grant so its blast radius was one tenant, and its comment said widening would be "a
    # deliberate decision with its own review". This is that decision: the owner's chat backend
    # is ONE service acting for MANY CQ tenants, and N single-grant keys it has to route between
    # is the failure mode the grant table was designed to avoid. What bounds the blast radius is
    # unchanged — grant ROWS (data an operator adds and revokes below, one tenant at a time),
    # not this check. The verify path never needed to change; only this ceiling did.
    if not [g for g in (body.grants or []) if (g or "").strip()]:
        raise HTTPException(status_code=400, detail="At least one tenant grant is required.")
    try:
        key_id, plaintext = await chat_credentials.issue(body.name, body.scopes, body.grants)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    integration_id = await chat_credentials.integration_for_key_id(key_id)
    return {"integration_id": integration_id, "key_id": key_id, "api_key": plaintext,
            "warning": "Store this key now — it is shown once and cannot be recovered."}


@router.get("/integrations", dependencies=[Depends(require_admin)])
async def list_integrations():
    return {"integrations": await chat_credentials.list_integrations()}


@router.post("/integrations/{integration_id}/rotate", dependencies=[Depends(require_admin)])
async def rotate_integration(integration_id: str, overlap_days: int = 7):
    try:
        plaintext = await chat_credentials.rotate(integration_id, overlap_days)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"integration_id": integration_id, "api_key": plaintext,
            "overlap_days": overlap_days,
            "warning": "Both keys verify during the overlap. Store this one now — shown once."}


@router.delete("/integrations/{integration_id}", dependencies=[Depends(require_admin)])
async def deactivate_integration(integration_id: str):
    """Deactivate, never hard-delete: the grant and usage history is the audit trail."""
    if not await chat_credentials.deactivate(integration_id):
        raise HTTPException(status_code=404, detail="Integration not found")
    return {"integration_id": integration_id, "is_active": False}


class GrantCreate(BaseModel):
    tenant: str                        # selector: uuid or clients.slug
    scopes: list[str] | None = None    # None => the integration's own scopes; may only narrow


@router.post("/integrations/{integration_id}/grants", dependencies=[Depends(require_admin)],
             status_code=201)
async def add_integration_grant(integration_id: str, body: GrantCreate):
    """Onboard one more tenant onto an existing credential — a grant row, not a new key."""
    try:
        grant = await chat_credentials.add_grant(integration_id, body.tenant, body.scopes)
    except chat_credentials.UnknownIntegration as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"integration_id": integration_id, "grant": grant}


@router.delete("/integrations/{integration_id}/grants/{client_id}",
               dependencies=[Depends(require_admin)])
async def remove_integration_grant(integration_id: str, client_id: str):
    """Revoke one tenant without touching the key or the other tenants on it.

    Deactivates the row rather than deleting it (audit trail), and is effective on the next
    request — the resolver joins on the grant's is_active with no cache in between.
    """
    if not await chat_credentials.remove_grant(integration_id, client_id):
        raise HTTPException(status_code=404, detail="Grant not found")
    return {"integration_id": integration_id, "client_id": client_id, "is_active": False}


# ---------------------------------------------------------------------------
# Per-tenant chat config
#
# The writer for `chat_configs`. Without it the table exists, `get_chat_config` reads it, and
# nothing can ever put a row in — so every tenant permanently runs on CHAT_CONFIG_DEFAULTS and
# persona, refusal copy, canned snippets and the gate thresholds are unreachable features.
#
# Superadmin-only and deliberately so: `refusal_copy` is the text a lawyer signed off on, and
# `settings.limits` is a commercial term. Neither belongs to the tenant's own portal, and
# neither is reachable with a chat integration key (`admin:*` is never issuable).
# ---------------------------------------------------------------------------
class ChatConfigBody(BaseModel):
    persona: str | None = None
    greeting: dict = {}              # {en,ka,ru}
    refusal_copy: dict = {}          # {en,ka,ru}
    languages: list[str] = ["en", "ka", "ru"]
    canned: list = []
    autopilot_enabled: bool = False
    # Free-form knob blob: min_score / min_hits / top_k / suggestion_count / strict, plus
    # `limits` ({tenant_per_minute, enduser_per_hour}). Kept as one jsonb rather than columns
    # because every one of them is a tuning value the engine already reads through `_cfg`.
    settings: dict = {}


@router.get("/chat/{tenant_id}/config", dependencies=[Depends(require_admin)])
async def get_chat_config(tenant_id: str):
    return await chat_store.get_chat_config(tenant_id)


@router.put("/chat/{tenant_id}/config", dependencies=[Depends(require_admin)])
async def put_chat_config(tenant_id: str, body: ChatConfigBody):
    """Save a new active version.

    `autopilot_enabled` unlocks POST /v1/chat/answer — the one route whose output reaches a
    member of the public with no human in between — so it is gated here, on the write, rather
    than argued about later: a tenant whose KB is entirely `visibility='internal'` would refuse
    EVERY customer question (the public bot retrieves published documents only), which reads as
    a broken product and gets blamed on the model. Publishing is a human act, so the guard is a
    cheap EXISTS-style pre-check (`kb.count_public_documents`, the pattern routers/scoring.py
    uses) and a 409 naming the missing thing, not a silent downgrade to false — an operator who
    asked for autopilot must be told it did not happen.
    """
    if body.autopilot_enabled and not await count_public_documents(tenant_id):
        raise HTTPException(
            status_code=409,
            detail="Cannot enable autopilot: this tenant has no public knowledge-base "
                   "documents. Publish at least one document (visibility='public') first.")
    try:
        return await chat_store.save_chat_config(
            tenant_id, persona=body.persona, greeting=body.greeting,
            refusal_copy=body.refusal_copy, languages=body.languages, canned=body.canned,
            autopilot_enabled=body.autopilot_enabled, settings=body.settings,
            updated_by="superadmin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# The default chat config — the baseline every tenant inherits
#
# The chat-side twin of GET/PUT /admin/default-rubric: what a workspace runs on until it
# saves a config of its own, editable from the console rather than by redeploying
# CHAT_CONFIG_DEFAULTS. Superadmin-only for the same reasons as the per-tenant writer above,
# doubly so because one save here changes every tenant that has not overridden the field.
#
# `/chat/default-config` has one path segment fewer than `/chat/{tenant_id}/config`, so the
# two cannot be confused by the router whatever order they are registered in.
# ---------------------------------------------------------------------------
class DefaultChatConfigBody(BaseModel):
    # Same shape as ChatConfigBody minus `autopilot_enabled`: a default can never switch a
    # public bot on, so the field does not exist here rather than being ignored.
    persona: str | None = None
    greeting: dict = {}              # {en,ka,ru}
    refusal_copy: dict = {}          # {en,ka,ru}
    languages: list[str] = ["en", "ka", "ru"]
    canned: list = []
    settings: dict = {}


@router.get("/chat/default-config", dependencies=[Depends(require_admin)])
async def get_default_chat_config():
    """`source` says whether the console is looking at a stored default or the built-in one.
    Bypasses the 5 s cache: an operator who just saved must see their own save."""
    return await chat_store.get_default_chat_config(force=True)


@router.put("/chat/default-config", dependencies=[Depends(require_admin)])
async def put_default_chat_config(body: DefaultChatConfigBody):
    try:
        return await chat_store.set_default_chat_config(
            persona=body.persona, greeting=body.greeting, refusal_copy=body.refusal_copy,
            languages=body.languages, canned=body.canned, settings=body.settings,
            updated_by="superadmin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Autopilot kill switch — the operator brake
#
# ADR-001 security bar item 9: stopping a misbehaving public bot must be something an operator
# can do in seconds, from the admin panel, without a deploy, without SSH and therefore without
# the VPN. The state itself is one `app_settings` blob read (5 s-cached) on every autopilot
# turn; these two routes are the only way to see and flip it that does not involve a human
# writing SQL on the server at 3am.
#
# Superadmin-only: it is a cross-tenant brake, and a tenant must not be able to clear the
# stop the operator just put on them.
# ---------------------------------------------------------------------------
class KillSwitchBody(BaseModel):
    # Both optional and both patch semantics: the admin panel flips the global brake and the
    # per-tenant list from two different controls, and neither should clobber the other.
    global_disabled: bool | None = None
    disabled_clients: list[str] | None = None


@router.get("/chat/kill-switch", dependencies=[Depends(require_admin)])
async def get_kill_switch():
    """Read the brake, bypassing the 5 s cache — an operator checking whether the stop landed
    must see the truth, not a value that was fresh enough for a request path."""
    return await settings_store.get_autopilot_kill_switch(force=True)


@router.put("/chat/kill-switch", dependencies=[Depends(require_admin)])
async def put_kill_switch(body: KillSwitchBody):
    return await settings_store.set_autopilot_kill_switch(body.model_dump(exclude_none=True))


# --------------------------------------------------------------------------- #
# Token accounting + per-tenant AI configuration (superadmin only)
#
# Both answer commercial questions rather than operational ones: what a workspace consumed,
# and which AI it runs on. They live behind the same admin gate as everything else here.
# --------------------------------------------------------------------------- #
@router.get("/usage/tenants", dependencies=[Depends(require_admin)])
async def usage_tenants(window: str = usage.DEFAULT_WINDOW):
    """Every tenant's token total for the window, biggest first."""
    return {"window": window if window in usage.WINDOWS else usage.DEFAULT_WINDOW,
            "windows": list(usage.WINDOWS),
            "tenants": await usage.totals_by_tenant(window)}


@router.get("/usage/tenants/{tenant_id}", dependencies=[Depends(require_admin)])
async def usage_tenant(tenant_id: str, window: str = usage.DEFAULT_WINDOW):
    """One tenant, split by user, feature, model and recording."""
    return await usage.tenant_breakdown(tenant_id, window)


@router.get("/ai-config/{tenant_id}", dependencies=[Depends(require_admin)])
async def get_ai_config(tenant_id: str):
    """A tenant's AI overrides. The stored key is never returned — only `has_key`."""
    return await ai_config.public_config(tenant_id)


class AiConfigBody(BaseModel):
    enabled: bool = False
    provider: str | None = "anthropic"
    model: str | None = None
    base_url: str | None = None
    # Only sent when SETTING a new key. Absent means "leave whatever is stored alone", which
    # is why clearing needs its own flag: the console cannot read the key back, so it cannot
    # echo it, and treating absent as empty would wipe a tenant's credential on every edit.
    api_key: str | None = None
    clear_key: bool = False
    notes: str | None = None


@router.put("/ai-config/{tenant_id}", dependencies=[Depends(require_admin)])
async def put_ai_config(tenant_id: str, body: AiConfigBody):
    async with pool().acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM clients WHERE id = $1::uuid", tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
    return await ai_config.save_config(
        tenant_id, enabled=body.enabled, provider=body.provider, model=body.model,
        base_url=body.base_url, api_key=body.api_key, clear_key=body.clear_key,
        notes=body.notes, updated_by="superadmin")
