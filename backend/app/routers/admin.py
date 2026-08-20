"""Admin panel API: login, view/update integration settings, and test connectivity.

The super-admin logs in with username + password (POST /admin/login), which returns
the admin token. That token then authorizes every other admin endpoint via the
X-Admin-Token header.
"""
import asyncio
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import pool
from ..services import chat_credentials, chat_store, claude, elevenlabs, settings_store
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


async def _probe_voices(cfg: dict) -> dict:
    from .tts import system_voice_ids
    voices = await elevenlabs.list_voices(cfg["elevenlabs_api_key"])
    if not voices:
        return {"level": "warn", "code": "no_voices",
                "detail": "authenticated, but this account lists 0 voices"}
    default = cfg.get("tts_voice_id")
    if default and default not in {v.get("voice_id") for v in voices} \
            and default not in system_voice_ids(cfg):
        return {"level": "warn", "code": "voice_missing",
                "detail": f"{len(voices)} voices available; configured default {default} is not one"}
    return {"level": "ok", "detail": f"{len(voices)} voices available"}


async def _probe_stt(cfg: dict) -> dict:
    """Real POST /v1/speech-to-text on 0.4 s of generated silence. Costs a fraction of a
    second of Scribe and is the ONLY proof of the speech_to_text permission. It also
    exercises ffmpeg, the multipart shape and the configured stt_model in one call."""
    try:
        out = await elevenlabs.transcribe(
            elevenlabs.silence_wav(), "probe.wav", "audio/wav",
            cfg["elevenlabs_api_key"], cfg["stt_model"], timeout=60.0)
    except elevenlabs.ElevenLabsError as exc:
        # A 400/422 that is NOT auth/permission/credit means the request was authorised and
        # only the synthetic clip was rejected — don't cry wolf about a working capability.
        if exc.code == "http" and exc.status in (400, 422):
            return {"level": "warn", "code": "probe_rejected", "detail": str(exc)}
        raise
    return {"level": "ok",
            "detail": f"model {cfg['stt_model']} accepted a 0.4 s probe clip "
                      f"(lang={out.get('language_code') or 'n/a'})"}


async def _probe_tts(cfg: dict) -> dict:
    if not cfg.get("tts_voice_id"):
        return {"level": "warn", "code": "no_voice_configured", "detail": "no default voice set"}
    audio = await elevenlabs.text_to_speech(
        PROBE_TEXT, cfg["elevenlabs_api_key"], cfg["tts_voice_id"], cfg["tts_model"],
        "en", output_format="mp3_22050_32", timeout=60.0)
    return {"level": "ok", "detail": f"model {cfg['tts_model']} returned {len(audio)} bytes"}


async def _probe_tts_ka(cfg: dict) -> dict:
    """Georgian is the load-bearing TTS path (CLAUDE.md section 4): eleven_v3 + a
    Georgian-capable shared voice. eleven_v3 is separately entitled and the shared voice may
    not be in the account listing at all, so GET /v1/voices cannot vouch for it."""
    from .tts import GEORGIAN_VOICE, LANGUAGES
    info = LANGUAGES["ka"]
    audio = await elevenlabs.text_to_speech(
        PROBE_TEXT_KA, cfg["elevenlabs_api_key"], info.get("voice") or GEORGIAN_VOICE,
        info["model"], None, output_format="mp3_22050_32", timeout=60.0)
    return {"level": "ok", "detail": f"{info['model']} + Georgian voice returned {len(audio)} bytes"}


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
    return {"level": "ok", "detail": f"scoring tool responded (total {(out or {}).get('total')})"}


async def _capture(coro) -> dict:
    """Never let one probe fail the whole report, and keep the historic {ok, detail} shape."""
    try:
        out = await coro
    except elevenlabs.ElevenLabsError as exc:
        out = {"level": "fail", "detail": str(exc), "code": exc.code, "scope": exc.scope}
    except Exception as exc:  # noqa: BLE001
        out = {"level": "fail", "detail": str(exc)}
    out.setdefault("level", "ok")
    out["ok"] = out["level"] != "fail"
    return out


@router.post("/test", dependencies=[Depends(require_admin)])
async def test_integrations(deep: bool = False):
    """Verify every third-party capability the product actually uses.

    Costs a few ElevenLabs credits per click (one 0.4 s STT clip + 6 TTS characters) —
    that is the price of never showing a green tick for something that is broken.
    `deep=true` adds the three non-analysis Anthropic tool schemas.
    """
    cfg = await settings_store.get_effective()
    probes = {
        "database": _probe_database(),
        "ffmpeg": _probe_ffmpeg(),
        "elevenlabs_voices": _probe_voices(cfg),
        "elevenlabs_stt": _probe_stt(cfg),
        "elevenlabs_tts": _probe_tts(cfg),
        "elevenlabs_tts_ka": _probe_tts_ka(cfg),
        "embeddings": _probe_embeddings(),
        # NB: "claude_analysis", not "claude" — the vendor roll-up below writes out["claude"],
        # which would otherwise clobber this probe's own detail.
        "claude_analysis": _probe_claude(cfg),
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


@router.get("/anonymous-limits", dependencies=[Depends(require_admin)])
async def get_anon_limits():
    return await settings_store.get_anonymous_config()


@router.put("/anonymous-limits", dependencies=[Depends(require_admin)])
async def put_anon_limits(patch: AnonPatch):
    await settings_store.set_anonymous_config(patch.model_dump(exclude_none=True))
    return await settings_store.get_anonymous_config()


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
    from .tts import GEORGIAN_VOICE, VOICE_ID_RE, system_voice_ids  # noqa: F401 (avoid import cycle at module load)

    cfg = await settings_store.get_effective()
    vcfg = await settings_store.get_voice_config()
    sysids = system_voice_ids(cfg)
    error, live = None, []
    try:
        live = await elevenlabs.list_voices(cfg["elevenlabs_api_key"])
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
    from .tts import VOICE_ID_RE

    if patch.mode is not None and patch.mode not in ("all", "allowlist"):
        raise HTTPException(status_code=400, detail="mode must be 'all' or 'allowlist'")
    current = await settings_store.get_voice_config()
    mode = patch.mode or current["mode"]
    ids = patch.voice_ids if patch.voice_ids is not None else current["voice_ids"]
    if mode == "allowlist" and not ids:
        raise HTTPException(status_code=400, detail="Select at least one voice.")
    for i in (patch.voice_ids or []):
        if not VOICE_ID_RE.match(i):
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
    # P1 issues SINGLE-GRANT credentials. The verify path is already multi-tenant and needs no
    # change to support more, so the pilot's blast radius is one tenant — bounded by DATA (how
    # many grant rows exist) rather than by code. Widening it later is deleting this check plus
    # an INSERT, and it should be a deliberate decision with its own review, not the default.
    if len(body.grants or []) != 1:
        raise HTTPException(status_code=400,
                            detail="Exactly one tenant grant per credential (P1 policy).")
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
