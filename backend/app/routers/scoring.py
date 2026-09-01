"""Scoring-rubric config endpoints + the answer-scoring playground.

Surfaces, all tenant-isolated:
  • Superadmin, tenant-parameterized:  GET/PUT /admin/scoring/{tenant_id}/config
                                       POST   /admin/scoring/{tenant_id}/score-text
                                       POST   /admin/analyze/{tenant_id} (audio; see below)
  • Tenant self-serve (owner):          GET/PUT /scoring/config   (scoped to the caller)
"""
import asyncio
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..db import pool
from ..services import (analysis, factcheck, kb_ingest, scoring, scoring_import,
                        scoring_store, settings_store)
from ..services.auth import Principal, client_ip, resolve_principal

router = APIRouter(tags=["scoring"])


class Dimension(BaseModel):
    key: str | None = None
    name: str
    description: str | None = ""
    guidance: str | None = ""
    weight: float = 0.0


class ConfigBody(BaseModel):
    dimensions: list[Dimension]
    rubric: str | None = ""


def _dump(dims: list[Dimension]) -> list[dict]:
    return [d.model_dump() for d in dims]


# --------------------------------------------------------------------------- #
# Superadmin, tenant-parameterized
# --------------------------------------------------------------------------- #
async def _scope(tenant_id: str, principal: Principal = Depends(resolve_principal)) -> str:
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")
    try:
        uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Tenant not found")
    async with pool().acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM clients WHERE id = $1", tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant_id


@router.get("/admin/scoring/{tenant_id}/config")
async def admin_get(tid: str = Depends(_scope)):
    return await scoring_store.get_active_config(tid) or {"version": None, "dimensions": [],
                                                          "weights": {}, "rubric": "", "is_active": False}


@router.put("/admin/scoring/{tenant_id}/config")
async def admin_put(body: ConfigBody, tid: str = Depends(_scope)):
    try:
        return await scoring_store.save_config(tid, _dump(body.dimensions), body.rubric or "", "superadmin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


MAX_IMPORT_BYTES = 25 * 1024 * 1024


async def _rubric_draft_from_upload(file: UploadFile, client_id: str) -> dict:
    """Shared body of both rubric-import routes: extract text, let the AI map it to a
    dimensions draft, translate every failure to an HTTP error the uploader can act on.
    Nothing is saved — the caller's editor receives a draft to review."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")
    try:
        text = await asyncio.to_thread(
            kb_ingest.extract_text, file.filename, file.content_type, data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read file: {exc}")
    if not text.strip():
        raise HTTPException(status_code=422, detail=(
            "No readable text found in the file. If this is a scanned document, run OCR "
            "or export a text-based version, then import again."))
    try:
        return await scoring_import.rubric_from_text(text, client_id=client_id)
    except scoring_import.RubricImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/admin/scoring/{tenant_id}/import")
async def admin_import_rubric(file: UploadFile = File(...), tid: str = Depends(_scope)):
    return await _rubric_draft_from_upload(file, tid)


class ScoreTextBody(BaseModel):
    text: str
    factcheck: bool = True   # also verify the answer's claims against the tenant's KB


async def _score_text(tid: str, text: str, do_factcheck: bool) -> dict:
    """Score a written operator answer against a tenant's active rubric (same engine the
    audio pipeline uses), optionally fact-checked against that tenant's own KB. Strictly
    scoped by tid — only this tenant's rubric and KB are ever used."""
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    cfg = await scoring_store.get_active_config(tid)
    if not cfg or not cfg.get("dimensions"):
        raise HTTPException(status_code=400,
                            detail="No active scoring rubric for this tenant yet. Set one before scoring.")

    s = await settings_store.get_effective()
    try:
        scorecard = await scoring.run_scoring(text, cfg, s["anthropic_api_key"], s["llm_model"],
                                              client_id=tid)
    except scoring.ScoringError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Ground "correctness" in the tenant's KB when there is one. Never blocks the score.
    kb_check = None
    if do_factcheck:
        try:
            async with pool().acquire() as conn:
                has_kb = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM kb_chunks WHERE client_id = $1)", tid)
            if has_kb:
                kb_check = await factcheck.run_factcheck(text, tid, s["anthropic_api_key"], s["llm_model"])
        except Exception:  # noqa: BLE001 — fact-check must never block the scorecard
            kb_check = None

    return {"scoring": scorecard, "kb_check": kb_check, "config_version": cfg.get("version")}


@router.post("/admin/scoring/{tenant_id}/score-text")
async def admin_score_text(body: ScoreTextBody, tid: str = Depends(_scope)):
    """Answer-scoring playground (superadmin, per tenant)."""
    return await _score_text(tid, body.text, body.factcheck)


MAX_ANALYZE_BYTES = 100 * 1024 * 1024  # matches routers/analyze.py's own limit


@router.post("/admin/analyze/{tenant_id}")
async def admin_analyze_audio(request: Request, file: UploadFile = File(...),
                              tid: str = Depends(_scope)):
    """KB-admin Playground's audio mode: the full pipeline (transcribe -> analysis -> KB
    fact-check -> rubric scoring), run against a tenant the superadmin chose, exactly as if
    that tenant had uploaded the file themselves via POST /analyze.

    A separate route rather than reusing /analyze: that endpoint scopes to the CALLER's own
    principal, and a superadmin has no client_id — there is nothing for it to run against.
    This one takes the tenant explicitly, gated the same way every other /admin/*/{tenant_id}
    route in this file is."""
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_ANALYZE_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")

    job_id = await analysis.create_job(
        filename=file.filename, content_type=file.content_type, size_bytes=len(audio),
        client_id=tid, principal_kind="tenant", anon_key=None,
        status="transcribing", client_ip=client_ip(request), audio=audio)
    result = await analysis.run_pipeline(
        job_id, audio, file.filename, file.content_type, tid, True)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("error") or "Analysis failed")
    return result


# --------------------------------------------------------------------------- #
# Tenant self-serve — scoped to the authenticated tenant
# --------------------------------------------------------------------------- #
def _tenant(principal: Principal = Depends(resolve_principal)) -> str:
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login or API key required")
    return principal.client_id


def _tenant_owner(principal: Principal = Depends(resolve_principal)) -> str:
    """Editing the rubric needs full tenant authority: an owner login or the tenant API key."""
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login or API key required")
    if principal.role not in ("owner", "apikey"):
        raise HTTPException(status_code=403, detail="Owner role required to edit the scoring rubric")
    return principal.client_id


@router.get("/scoring/config")
async def tenant_get(client_id: str = Depends(_tenant)):
    return await scoring_store.get_active_config(client_id) or {"version": None, "dimensions": [],
                                                                "weights": {}, "rubric": "", "is_active": False}


@router.put("/scoring/config")
async def tenant_put(body: ConfigBody, client_id: str = Depends(_tenant_owner)):
    try:
        return await scoring_store.save_config(client_id, _dump(body.dimensions), body.rubric or "", "tenant")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/scoring/import")
async def tenant_import_rubric(file: UploadFile = File(...),
                               client_id: str = Depends(_tenant_owner)):
    """Owner-gated like every rubric edit: the draft is harmless, but gating the expensive
    AI call the same way as the save keeps members from spending the tenant's quota."""
    return await _rubric_draft_from_upload(file, client_id)


@router.post("/scoring/score-text")
async def tenant_score_text(body: ScoreTextBody, client_id: str = Depends(_tenant)):
    """Tenant playground: score a written answer against the caller's own rubric + KB."""
    return await _score_text(client_id, body.text, body.factcheck)
