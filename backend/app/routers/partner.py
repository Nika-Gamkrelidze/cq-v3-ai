"""B2B partner API (versioned under /v1).

Partners authenticate server-to-server with the per-tenant `X-API-Key` header (a tenant
Bearer token also works). Everything is strictly scoped to the caller's client_id.

  GET  /v1/account                      — who am I + KB/rubric/usage snapshot
  POST /v1/transcriptions               — standalone STT (transcript + word timings)
  POST /v1/analyze                      — synchronous single-audio correctness check
  POST /v1/analyses                     — async single (202 -> poll /v1/jobs/{id})
  POST /v1/analyses/batch               — async bulk (202 -> poll /v1/analyses/batch/{id})
  GET  /v1/analyses/batch/{batch_id}    — batch status rollup
  GET  /v1/jobs                         — list results (paginated + filters)
  GET  /v1/jobs/{job_id}                — full result
  GET/PUT /v1/scoring/config            — the tenant's scoring rubric

KB (/v1/kb/*) and TTS (/v1/tts, /v1/voices, /v1/languages) are the existing routers
re-mounted under /v1 (see main.py).
"""
import json
import uuid

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, Header,
                     HTTPException, Query, UploadFile)
from pydantic import BaseModel

from ..db import pool
from ..services import analysis, elevenlabs, scoring, scoring_store, settings_store
from ..services.auth import Principal, resolve_principal

router = APIRouter(prefix="/v1", tags=["partner"])

MAX_BYTES = 100 * 1024 * 1024          # single upload
BATCH_MAX_BYTES = 25 * 1024 * 1024     # per file in a batch (lower — many held in memory)
BATCH_MAX_FILES = 50


def require_tenant(principal: Principal = Depends(resolve_principal)) -> Principal:
    if not principal.is_tenant:
        raise HTTPException(status_code=401,
                            detail="A tenant API key (X-API-Key) or tenant login is required.")
    return principal


def _job_public(row) -> dict:
    d = dict(row)
    d["id"] = str(row["id"])
    if d.get("batch_id"):
        d["batch_id"] = str(row["batch_id"])
    if d.get("created_at"):
        d["created_at"] = row["created_at"].isoformat()
    for k in ("analysis", "kb_used", "kb_check", "scoring"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


# --------------------------------------------------------------------------- #
# Account
# --------------------------------------------------------------------------- #
@router.get("/account")
async def account(p: Principal = Depends(require_tenant)):
    """Identity + a snapshot of this tenant's KB, active rubric, and usage."""
    cid = p.client_id
    async with pool().acquire() as conn:
        client = await conn.fetchrow(
            "SELECT id, slug, name, industry, region, is_active FROM clients WHERE id=$1", cid)
        if not client:
            raise HTTPException(status_code=404, detail="Tenant not found")
        docs = await conn.fetchval("SELECT count(*) FROM kb_documents WHERE client_id=$1", cid)
        chunks = await conn.fetchval("SELECT count(*) FROM kb_chunks WHERE client_id=$1", cid)
        analyses_total = await conn.fetchval("SELECT count(*) FROM audio_jobs WHERE client_id=$1", cid)
        analyses_today = await conn.fetchval(
            "SELECT count(*) FROM audio_jobs WHERE client_id=$1 AND created_at::date = now()::date", cid)
    rubric = await scoring_store.get_active_config(cid)
    return {
        "client_id": str(client["id"]), "name": client["name"], "slug": client["slug"],
        "industry": client["industry"], "region": client["region"],
        "is_active": client["is_active"], "role": p.role, "auth": p.via,
        "knowledge_base": {"documents": docs, "chunks": chunks},
        "scoring": {"active_rubric_version": rubric["version"] if rubric else None,
                    "dimensions": len(rubric["dimensions"]) if rubric else 0},
        "usage": {"analyses_total": analyses_total, "analyses_today": analyses_today},
    }


# --------------------------------------------------------------------------- #
# Standalone STT
# --------------------------------------------------------------------------- #
@router.post("/transcriptions")
async def transcribe(file: UploadFile = File(...), p: Principal = Depends(require_tenant)):
    """Transcribe audio (ElevenLabs Scribe, diarized) without running analysis/scoring.
    Returns the transcript, detected language, and per-word timings."""
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")
    cfg = await settings_store.get_effective()
    try:
        stt = await elevenlabs.transcribe(
            audio, file.filename, file.content_type, cfg["elevenlabs_api_key"], cfg["stt_model"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}")
    return {"filename": file.filename, "language": stt.get("language_code"),
            "transcript": stt.get("text") or "", "words": stt.get("words") or []}


# --------------------------------------------------------------------------- #
# Analysis: sync single / async single / async bulk
# --------------------------------------------------------------------------- #
async def _existing_by_ref(cid: str, external_ref: str | None):
    if not external_ref:
        return None
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, status FROM audio_jobs WHERE client_id=$1 AND external_ref=$2",
            cid, external_ref)


@router.post("/analyze")
async def analyze_sync(file: UploadFile = File(...), p: Principal = Depends(require_tenant)):
    """Synchronous single-audio correctness check (transcript + analysis + KB fact-check +
    rubric score). Blocks ~30s; use /v1/analyses for many files."""
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")
    job_id = await analysis.create_job(
        filename=file.filename, content_type=file.content_type, size_bytes=len(audio),
        client_id=p.client_id, principal_kind=p.kind, anon_key=None, status="transcribing")
    result = await analysis.run_pipeline(
        job_id, audio, file.filename, file.content_type, p.client_id, True)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("error") or "Analysis failed")
    return result


@router.post("/analyses", status_code=202)
async def analyze_async(bg: BackgroundTasks, file: UploadFile = File(...),
                        external_ref: str | None = Form(None),
                        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                        p: Principal = Depends(require_tenant)):
    """Submit one audio for async analysis. Returns 202 immediately; poll GET /v1/jobs/{id}.
    An `external_ref` (or Idempotency-Key header) makes retries safe — a repeat returns the
    same job instead of re-running (and re-billing)."""
    ref = (external_ref or idempotency_key or None)
    existing = await _existing_by_ref(p.client_id, ref)
    if existing and existing["status"] != "error":
        return {"id": str(existing["id"]), "status": existing["status"], "external_ref": ref,
                "idempotent_replay": True}
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")
    if existing:  # previous attempt errored — reuse the row and re-run
        job_id = str(existing["id"])
        await analysis._update(job_id, status="queued", error=None)
    else:
        job_id = await analysis.create_job(
            filename=file.filename, content_type=file.content_type, size_bytes=len(audio),
            client_id=p.client_id, principal_kind=p.kind, anon_key=None, external_ref=ref)
    bg.add_task(analysis.run_background, job_id, audio, file.filename, file.content_type,
                p.client_id, True)
    return {"id": job_id, "status": "queued", "external_ref": ref}


@router.post("/analyses/batch", status_code=202)
async def analyze_batch(bg: BackgroundTasks, files: list[UploadFile] = File(...),
                        external_refs: list[str] | None = Form(None),
                        p: Principal = Depends(require_tenant)):
    """Submit up to 50 audios for async correctness checking. Returns 202 with a batch_id +
    one job per file; poll GET /v1/analyses/batch/{batch_id}. Optional `external_refs`
    (repeat the form field, aligned by order) make each item idempotent."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > BATCH_MAX_FILES:
        raise HTTPException(status_code=413, detail=f"Batch exceeds {BATCH_MAX_FILES} files")
    refs = external_refs or []
    batch_id = str(uuid.uuid4())
    jobs, tasks = [], []
    for i, file in enumerate(files):
        ref = refs[i] if i < len(refs) and refs[i] else None
        existing = await _existing_by_ref(p.client_id, ref)
        if existing and existing["status"] != "error":
            jobs.append({"id": str(existing["id"]), "external_ref": ref,
                         "status": existing["status"], "idempotent_replay": True})
            continue
        audio = await file.read()
        if not audio:
            jobs.append({"external_ref": ref, "status": "rejected", "error": "empty file"})
            continue
        if len(audio) > BATCH_MAX_BYTES:
            jobs.append({"external_ref": ref, "status": "rejected",
                         "error": f"file exceeds {BATCH_MAX_BYTES // (1024*1024)} MB batch limit"})
            continue
        if existing:
            job_id = str(existing["id"])
            await analysis._update(job_id, status="queued", error=None, batch_id=batch_id)
        else:
            job_id = await analysis.create_job(
                filename=file.filename, content_type=file.content_type, size_bytes=len(audio),
                client_id=p.client_id, principal_kind=p.kind, anon_key=None,
                batch_id=batch_id, external_ref=ref)
        jobs.append({"id": job_id, "external_ref": ref, "status": "queued"})
        tasks.append((job_id, audio, file.filename, file.content_type))
    for job_id, audio, fname, ctype in tasks:
        bg.add_task(analysis.run_background, job_id, audio, fname, ctype, p.client_id, True)
    return {"batch_id": batch_id, "count": len(jobs), "queued": len(tasks), "jobs": jobs}


@router.get("/analyses/batch/{batch_id}")
async def batch_status(batch_id: str, p: Principal = Depends(require_tenant)):
    """Aggregate status of a bulk submission (counts by status + per-job summary)."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, external_ref, status, language, processing_ms, error, created_at
            FROM audio_jobs WHERE batch_id=$1 AND client_id=$2 ORDER BY created_at
            """, batch_id, p.client_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Batch not found")
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["status"]] = totals.get(r["status"], 0) + 1
    done = all(r["status"] in analysis.TERMINAL for r in rows)
    jobs = [{"id": str(r["id"]), "external_ref": r["external_ref"], "status": r["status"],
             "language": r["language"], "processing_ms": r["processing_ms"],
             "error": r["error"]} for r in rows]
    return {"batch_id": batch_id, "count": len(rows), "complete": done,
            "totals": totals, "jobs": jobs}


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@router.get("/jobs")
async def list_jobs(p: Principal = Depends(require_tenant),
                    status: str | None = None, batch_id: str | None = None,
                    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    """List this tenant's analysis jobs (newest first), with optional status/batch filters."""
    where, args = ["client_id = $1"], [p.client_id]
    if status:
        args.append(status); where.append(f"status = ${len(args)}")
    if batch_id:
        args.append(batch_id); where.append(f"batch_id = ${len(args)}")
    where_sql = " AND ".join(where)
    async with pool().acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM audio_jobs WHERE {where_sql}", *args)
        rows = await conn.fetch(
            f"""
            SELECT id, filename, status, language, batch_id, external_ref, processing_ms, created_at
            FROM audio_jobs WHERE {where_sql}
            ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}
            """, *args, limit, offset)
    jobs = [{**dict(r), "id": str(r["id"]),
             "batch_id": str(r["batch_id"]) if r["batch_id"] else None,
             "created_at": r["created_at"].isoformat()} for r in rows]
    next_offset = offset + limit if offset + limit < total else None
    return {"total": total, "limit": limit, "offset": offset, "next_offset": next_offset, "jobs": jobs}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, p: Principal = Depends(require_tenant)):
    """Full result for one job (transcript, analysis, kb_check, scoring)."""
    cols = ("id, filename, content_type, size_bytes, status, language, transcript, analysis, "
            "kb_used, kb_check, scoring, batch_id, external_ref, stt_model, llm_model, error, "
            "processing_ms, created_at")
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {cols} FROM audio_jobs WHERE id=$1 AND client_id=$2", job_id, p.client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_public(row)


# --------------------------------------------------------------------------- #
# Scoring rubric (partner self-serve)
# --------------------------------------------------------------------------- #
class Dimension(BaseModel):
    key: str | None = None
    name: str
    description: str | None = ""
    guidance: str | None = ""
    weight: float = 0.0


class RubricBody(BaseModel):
    dimensions: list[Dimension]
    rubric: str | None = ""


@router.get("/scoring/config")
async def get_rubric(p: Principal = Depends(require_tenant)):
    """The tenant's active scoring rubric (dimensions + weights + guidance)."""
    return await scoring_store.get_active_config(p.client_id) or {
        "version": None, "dimensions": [], "weights": {}, "rubric": "", "is_active": False}


@router.put("/scoring/config")
async def put_rubric(body: RubricBody, p: Principal = Depends(require_tenant)):
    """Replace the tenant's scoring rubric (weights must total 100%)."""
    try:
        return await scoring_store.save_config(
            p.client_id, [d.model_dump() for d in body.dimensions], body.rubric or "", "partner")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
