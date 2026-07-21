"""Admin panel API: login, view/update integration settings, and test connectivity.

The super-admin logs in with username + password (POST /admin/login), which returns
the admin token. That token then authorizes every other admin endpoint via the
X-Admin-Token header.
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..config import settings
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


@router.post("/test", dependencies=[Depends(require_admin)])
async def test_integrations():
    """Check that the configured keys actually work."""
    cfg = await settings_store.get_effective()
    result = {}

    # ElevenLabs: list voices as a cheap authenticated call.
    try:
        voices = await elevenlabs.list_voices(cfg["elevenlabs_api_key"])
        result["elevenlabs"] = {"ok": True, "detail": f"{len(voices)} voices available"}
    except Exception as exc:  # noqa: BLE001
        result["elevenlabs"] = {"ok": False, "detail": str(exc)}

    # Claude: a tiny analysis round-trip.
    try:
        analysis = await claude.analyze(
            "Speaker 1: Hello, this is a connectivity test. Speaker 2: Acknowledged.",
            cfg["anthropic_api_key"], cfg["llm_model"], cfg["analysis_instructions"],
        )
        result["claude"] = {"ok": True, "detail": f"model {cfg['llm_model']} responded",
                            "sample": analysis.get("summary", "")[:120]}
    except Exception as exc:  # noqa: BLE001
        result["claude"] = {"ok": False, "detail": str(exc)}

    return result


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
    from ..services import embeddings as emb
    try:
        provider = await emb.get_provider()
        health = await provider.health()
        cfg_dim = provider.dim
        detail = f"model {health.get('model')} returned dim {health.get('dim')}"
        if health.get("dim") and cfg_dim and health["dim"] != cfg_dim:
            return {"ok": False, "detail": f"{detail} but configured dim is {cfg_dim} — "
                                           f"set EMBEDDING_DIM to {health['dim']} and re-embed."}
        return {"ok": True, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


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
