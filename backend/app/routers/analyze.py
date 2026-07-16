"""Audio analysis pipeline: upload -> ElevenLabs STT -> (tenant RAG) -> Claude -> stored result.

Tenant-scoped via the resolved principal. Anonymous users are allowed within admin-
configured limits; tenants get their knowledge base injected as RAG context.
"""
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..db import pool
from ..services import analysis, limits
from ..services.auth import Principal, resolve_principal

router = APIRouter(tags=["analyze"])

MAX_BYTES = 100 * 1024 * 1024


@router.get("/limits")
async def get_limits(principal: Principal = Depends(resolve_principal)):
    """Remaining anonymous quota for the caller (or unlimited for tenants/superadmin)."""
    return await limits.snapshot(principal)


@router.post("/analyze")
async def analyze_audio(file: UploadFile = File(...),
                        principal: Principal = Depends(resolve_principal)):
    """Synchronous single-audio analysis. Runs the full pipeline inline and returns the
    result. Partners with many files should use the async /v1/analyses[/batch] endpoints."""
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")

    await limits.reserve(principal, "analyses", len(audio))

    job_id = await analysis.create_job(
        filename=file.filename, content_type=file.content_type, size_bytes=len(audio),
        client_id=principal.client_id, principal_kind=principal.kind, anon_key=principal.anon_key,
        status="transcribing")
    result = await analysis.run_pipeline(
        job_id, audio, file.filename, file.content_type, principal.client_id, principal.is_tenant)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("error") or "Analysis failed")
    return result


def _scope(principal: Principal):
    """Return (where_sql, args) restricting jobs to what this principal may see."""
    if principal.is_superadmin:
        return "TRUE", []
    if principal.is_tenant:
        return "client_id = $1", [principal.client_id]
    return "anon_key = $1 AND principal_type = 'anonymous'", [principal.anon_key]


@router.get("/jobs")
async def list_jobs(limit: int = 20, principal: Principal = Depends(resolve_principal)):
    limit = max(1, min(limit, 100))
    where, args = _scope(principal)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, filename, status, language, processing_ms, created_at
            FROM audio_jobs WHERE {where} ORDER BY created_at DESC LIMIT ${len(args)+1}
            """, *args, limit)
    return [{**dict(r), "id": str(r["id"]), "created_at": r["created_at"].isoformat()} for r in rows]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, principal: Principal = Depends(resolve_principal)):
    # Build the scope predicate with placeholders after $1 (job_id).
    cols = ("id, filename, content_type, size_bytes, status, language, transcript, "
            "analysis, kb_used, kb_check, scoring, stt_model, llm_model, error, processing_ms, created_at")
    if principal.is_superadmin:
        where, args = "TRUE", []
    elif principal.is_tenant:
        where, args = "client_id = $2", [principal.client_id]
    else:
        where, args = "anon_key = $2 AND principal_type = 'anonymous'", [principal.anon_key]
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {cols} FROM audio_jobs WHERE id = $1 AND {where}", job_id, *args)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    data = dict(row)
    data["id"] = str(row["id"])
    data["created_at"] = row["created_at"].isoformat()
    for k in ("analysis", "kb_used", "kb_check", "scoring"):
        if isinstance(data.get(k), str):
            data[k] = json.loads(data[k])
    return data
