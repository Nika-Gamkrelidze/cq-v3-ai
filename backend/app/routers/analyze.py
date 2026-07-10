"""Audio analysis pipeline: upload -> ElevenLabs STT -> (tenant RAG) -> Claude -> stored result.

Tenant-scoped via the resolved principal. Anonymous users are allowed within admin-
configured limits; tenants get their knowledge base injected as RAG context.
"""
import json
import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..db import pool
from ..services import (claude, elevenlabs, factcheck, limits, retrieval,
                        scoring, scoring_store, settings_store)
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
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")

    await limits.reserve(principal, "analyses", len(audio))

    cfg = await settings_store.get_effective()
    started = time.monotonic()

    async with pool().acquire() as conn:
        job_id = await conn.fetchval(
            """
            INSERT INTO audio_jobs
                (filename, content_type, size_bytes, status, stt_model, llm_model,
                 client_id, principal_type, anon_key)
            VALUES ($1,$2,$3,'transcribing',$4,$5,$6,$7,$8)
            RETURNING id
            """,
            file.filename, file.content_type, len(audio), cfg["stt_model"], cfg["llm_model"],
            principal.client_id, principal.kind, principal.anon_key,
        )

    async def fail(msg: str):
        async with pool().acquire() as conn:
            await conn.execute(
                "UPDATE audio_jobs SET status='error', error=$2, updated_at=now() WHERE id=$1",
                job_id, msg)
        raise HTTPException(status_code=502, detail=msg)

    # 1. Transcribe
    try:
        stt = await elevenlabs.transcribe(
            audio, file.filename, file.content_type, cfg["elevenlabs_api_key"], cfg["stt_model"])
    except Exception as exc:  # noqa: BLE001
        await fail(f"Transcription failed: {exc}")

    transcript = (stt.get("text") or "")
    language = stt.get("language_code")

    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE audio_jobs SET status='analyzing', transcript=$2, language=$3, updated_at=now() WHERE id=$1",
            job_id, transcript, language)

    # 2. Tenant RAG — retrieve relevant KB context
    kb_context, kb_used = "", []
    if principal.is_tenant and transcript.strip():
        try:
            hits = await retrieval.retrieve(principal.client_id, transcript)
            if hits:
                kb_context = retrieval.format_context(hits)
                kb_used = [{"title": h.get("title"), "doc_type": h.get("doc_type"),
                            "score": round(float(h["score"]), 3) if h.get("score") is not None else None}
                           for h in hits]
        except Exception:  # noqa: BLE001 — KB must never block analysis
            kb_context, kb_used = "", []

    # 3. Analyse
    try:
        analysis = await claude.analyze(
            transcript, cfg["anthropic_api_key"], cfg["llm_model"],
            cfg["analysis_instructions"], kb_context=kb_context)
    except Exception as exc:  # noqa: BLE001
        await fail(f"Analysis failed: {exc}")

    # 4. KB correctness / fact-check — tenant-scoped, only if the tenant has a KB.
    kb_check = None
    if principal.is_tenant and transcript.strip():
        try:
            async with pool().acquire() as conn:
                has_kb = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM kb_chunks WHERE client_id = $1)", principal.client_id)
            if has_kb:
                kb_check = await factcheck.run_factcheck(
                    transcript, principal.client_id, cfg["anthropic_api_key"], cfg["llm_model"])
        except Exception:  # noqa: BLE001 — fact-check must never block the analysis
            kb_check = None

    # 5. Rubric scoring — tenant-scoped, only if the tenant has an active scoring config.
    #    Coexists with generic analysis + KB fact-check; must never block the result.
    scorecard = None
    if principal.is_tenant and transcript.strip():
        try:
            cfg_scoring = await scoring_store.get_active_config(principal.client_id)
            if cfg_scoring and cfg_scoring.get("dimensions"):
                scorecard = await scoring.run_scoring(
                    transcript, cfg_scoring, cfg["anthropic_api_key"], cfg["llm_model"])
        except Exception:  # noqa: BLE001 — scoring must never block the analysis
            scorecard = None

    processing_ms = int((time.monotonic() - started) * 1000)
    async with pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE audio_jobs
            SET status='done', analysis=$2::jsonb, language=COALESCE(language,$3),
                processing_ms=$4, kb_used=$5::jsonb, kb_check=$6::jsonb, scoring=$7::jsonb,
                updated_at=now()
            WHERE id=$1
            """,
            job_id, json.dumps(analysis), analysis.get("language"), processing_ms,
            json.dumps(kb_used), json.dumps(kb_check) if kb_check is not None else None,
            json.dumps(scorecard) if scorecard is not None else None)

    return {
        "id": str(job_id), "status": "done", "filename": file.filename,
        "language": analysis.get("language") or language,
        "transcript": transcript, "analysis": analysis,
        "kb_used": kb_used, "kb_check": kb_check, "scoring": scorecard,
        "processing_ms": processing_ms,
    }


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
