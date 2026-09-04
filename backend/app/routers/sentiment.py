"""Standalone sentiment analysis — its own product, not a side effect of /analyze.

The bundled sentiment inside /analyze's full pipeline is unchanged and lives in
services/sentiment.py's `analyse()`. This router is the OTHER path: upload audio, get back
JUST a sentiment read (transcript + how it sounded), configurable per scope:

  Public app (anonymous, and superadmin previewing it): POST /sentiment
    GET/PUT /admin/public-sentiment-config            (superadmin, global)

  Tenant self-serve, and the tenant's own Playground:   POST /sentiment
    GET/PUT /sentiment/config                           (tenant; PUT needs owner|apikey)

  Superadmin, per-tenant (KB-admin console Playground): POST /admin/sentiment/{tenant_id}
    GET/PUT /admin/sentiment/{tenant_id}/config

POST /sentiment resolves WHICH config applies from the caller's own principal (tenant -> their
row; anonymous/superadmin -> the public row) rather than taking a scope parameter, so a caller
can never read or spend someone else's configuration by passing the wrong flag.
"""
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..db import pool
from ..services import analysis, elevenlabs, limits, sentiment, sentiment_config, settings_store
from ..services.auth import Principal, client_ip, resolve_principal
# The one "who owns this row" rule (see its docstring): a registered user calling this public
# route must get their own id on the job, not NULL.
from .analyze import _user_id

router = APIRouter(tags=["sentiment"])

MAX_BYTES = 100 * 1024 * 1024


class SentimentConfigBody(BaseModel):
    enabled: bool = True
    guidance: str = Field(default="", max_length=4000)


# --------------------------------------------------------------------------- #
# Superadmin, tenant-parameterized config
# --------------------------------------------------------------------------- #
async def _scope(tenant_id: str) -> str:
    """Superadmin-gated tenant existence check — see admin_test dependency style used
    throughout the admin routers. Duplicated from scoring.py's `_scope` rather than shared:
    both are a 5-line existence check, not a security decision worth a cross-module import."""
    async with pool().acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM clients WHERE id = $1", tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant_id


async def _admin_scope(tenant_id: str, principal: Principal = Depends(resolve_principal)) -> str:
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")
    return await _scope(tenant_id)


@router.get("/admin/sentiment/{tenant_id}/config")
async def admin_get_config(tid: str = Depends(_admin_scope)):
    return await sentiment_config.get_tenant_config(tid)


@router.put("/admin/sentiment/{tenant_id}/config")
async def admin_put_config(body: SentimentConfigBody, tid: str = Depends(_admin_scope)):
    return await sentiment_config.save_tenant_config(
        tid, enabled=body.enabled, guidance=body.guidance, actor="superadmin")


@router.get("/admin/public-sentiment-config")
async def admin_get_public_config(principal: Principal = Depends(resolve_principal)):
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")
    return await settings_store.get_public_sentiment_config()


@router.put("/admin/public-sentiment-config")
async def admin_put_public_config(body: SentimentConfigBody,
                                  principal: Principal = Depends(resolve_principal)):
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")
    await settings_store.set_public_sentiment_config(
        {"enabled": body.enabled, "guidance": body.guidance})
    return await settings_store.get_public_sentiment_config()


# --------------------------------------------------------------------------- #
# Tenant self-serve config — scoped to the authenticated tenant
# --------------------------------------------------------------------------- #
def _tenant(principal: Principal = Depends(resolve_principal)) -> str:
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login or API key required")
    return principal.client_id


def _tenant_owner(principal: Principal = Depends(resolve_principal)) -> str:
    """Same owner|apikey policy as scoring.py's rubric editor — keep the two in sync."""
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login or API key required")
    if principal.role not in ("owner", "apikey"):
        raise HTTPException(status_code=403, detail="Owner role required to edit sentiment settings")
    return principal.client_id


@router.get("/sentiment/config")
async def tenant_get_config(client_id: str = Depends(_tenant)):
    return await sentiment_config.get_tenant_config(client_id)


@router.put("/sentiment/config")
async def tenant_put_config(body: SentimentConfigBody, client_id: str = Depends(_tenant_owner)):
    return await sentiment_config.save_tenant_config(
        client_id, enabled=body.enabled, guidance=body.guidance, actor="tenant")


# --------------------------------------------------------------------------- #
# The standalone analysis itself
# --------------------------------------------------------------------------- #
async def _resolve_config(principal: Principal) -> dict:
    """Which sentiment config governs this call: the caller's own tenant row, or the public
    (superadmin-owned) row for everyone else — including a superadmin previewing the public
    experience, which is why this checks `is_tenant` rather than `kind == 'anonymous'`."""
    if principal.is_tenant:
        return await sentiment_config.get_tenant_config(principal.client_id)
    return await settings_store.get_public_sentiment_config()


@router.post("/sentiment")
async def standalone_sentiment(request: Request, file: UploadFile = File(...),
                               principal: Principal = Depends(resolve_principal)):
    """Transcript + how it sounded. Nothing else — no full analysis, no KB, no rubric.

    Metered on the same `analyses` daily bucket as /transcribe and /analyze: it is a third
    way to spend the same underlying STT budget, not a separate allowance.
    """
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")

    cfg_sent = await _resolve_config(principal)
    if not cfg_sent.get("enabled", True):
        raise HTTPException(
            status_code=403,
            detail="Sentiment analysis is disabled for this workspace." if principal.is_tenant
            else "Sentiment analysis is currently disabled.")

    await limits.reserve(principal, "analyses", len(audio))
    cfg = await settings_store.get_effective()

    job_id = await analysis.create_job(
        filename=file.filename, content_type=file.content_type, size_bytes=len(audio),
        client_id=principal.client_id, principal_kind=principal.kind, anon_key=principal.anon_key,
        status="transcribing", client_ip=client_ip(request), audio=audio,
        user_id=_user_id(principal))

    try:
        stt = await elevenlabs.transcribe(
            audio, file.filename, file.content_type, cfg["elevenlabs_api_key"], cfg["stt_model"])
    except Exception as exc:  # noqa: BLE001
        await analysis.mark_error(job_id, f"Transcription failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}")

    transcript = stt.get("text") or ""
    language = stt.get("language_code")

    sent = await sentiment.standalone(
        transcript, audio, api_key=cfg["anthropic_api_key"], model=cfg["llm_model"],
        guidance=cfg_sent.get("guidance") or "", client_id=principal.client_id,
        filename=file.filename, content_type=file.content_type)

    await analysis.mark_transcribed(job_id, transcript=transcript, language=language,
                                    sentiment=sent)
    return {"id": job_id, "status": "done", "filename": file.filename, "language": language,
            "transcript": transcript, "sentiment": sent}


@router.post("/admin/sentiment/{tenant_id}")
async def admin_standalone_sentiment(file: UploadFile = File(...), tid: str = Depends(_admin_scope)):
    """KB-admin Playground: standalone sentiment for a chosen tenant, using that tenant's own
    guidance. Stateless like the scoring playground's score-text — a superadmin's probe of a
    tenant's config is not a customer interaction and is not persisted or metered."""
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")

    cfg_sent = await sentiment_config.get_tenant_config(tid)
    cfg = await settings_store.get_effective()

    try:
        stt = await elevenlabs.transcribe(
            audio, file.filename, file.content_type, cfg["elevenlabs_api_key"], cfg["stt_model"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}")

    transcript = stt.get("text") or ""
    language = stt.get("language_code")
    sent = await sentiment.standalone(
        transcript, audio, api_key=cfg["anthropic_api_key"], model=cfg["llm_model"],
        guidance=cfg_sent.get("guidance") or "", client_id=tid,
        filename=file.filename, content_type=file.content_type)
    return {"language": language, "transcript": transcript, "sentiment": sent,
            "config_enabled": cfg_sent.get("enabled", True)}
