"""Audio analysis pipeline: upload -> ElevenLabs STT -> (tenant RAG) -> Claude -> stored result.

Tenant-scoped via the resolved principal. Anonymous users are allowed within admin-
configured limits; tenants get their knowledge base injected as RAG context.
"""
import json

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ..db import pool
from ..services import analysis, elevenlabs, limits, media, sentiment, settings_store
from ..services.auth import Principal, client_ip, resolve_principal

router = APIRouter(tags=["analyze"])

MAX_BYTES = 100 * 1024 * 1024


def _user_id(principal: Principal) -> str | None:
    """The registered-user owner to WRITE on a row — only for kind 'user'.

    A tenant login also carries a `user_id` (its `tenant_users` row), and that must never land
    in the column a registered user's history is scoped by. Without this the row would be
    written `principal_type='user', user_id NULL`: the caller pays a quota unit for an upload
    that `_scope` can never match again, and `retention.purge_expired` keeps its transcript
    under the signed-in rule with no account able to see or delete it.
    """
    return principal.user_id if principal.kind == "user" else None


@router.get("/limits")
async def get_limits(principal: Principal = Depends(resolve_principal)):
    """Remaining anonymous quota for the caller (or unlimited for tenants/superadmin)."""
    return await limits.snapshot(principal)


@router.post("/analyze")
async def analyze_audio(request: Request, file: UploadFile = File(...),
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
        status="transcribing", client_ip=client_ip(request), audio=audio,
        user_id=_user_id(principal))
    result = await analysis.run_pipeline(
        job_id, audio, file.filename, file.content_type, principal.client_id, principal.is_tenant)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("error") or "Analysis failed")
    return result


@router.post("/transcribe")
async def transcribe_audio(request: Request, file: UploadFile = File(...),
                           principal: Principal = Depends(resolve_principal)):
    """Speech-to-text with sentiment, WITHOUT the Claude analysis pass.

    This is what the public site offers: a transcript and how the speaker sounded. It exists
    as its own route rather than a flag on /analyze because it is a different product with a
    different cost — no LLM call at all — and because the public page must not be able to
    trigger the expensive path by flipping a parameter.

    Metered on the same `analyses` bucket so an operator keeps one daily dial for "audio a
    stranger may send us", and retained under the same rule as every other anonymous upload.
    """
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")

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

    # Prosody only: there is no Claude pass here, so there is no text sentiment to pair it
    # with. `combine()` reports that honestly as prosody_only rather than inventing a
    # text half.
    sent = sentiment.combine(None, await sentiment.prosody(
        audio, file.filename, file.content_type))

    await analysis.mark_transcribed(job_id, transcript=transcript, language=language,
                                    sentiment=sent)
    return {"id": job_id, "status": "done", "filename": file.filename, "language": language,
            "transcript": transcript, "sentiment": sent}


def _scope(principal: Principal, first: int = 1):
    """Return (where_sql, args) restricting jobs to what this principal may see.

    `first` is the placeholder number the predicate may start at, so /jobs/{id} can put the job
    id in $1 and still share this one policy — two hand-written copies is how a scope check ends
    up fixed in only one of them.

    An integration credential is REJECTED rather than falling through to the anonymous branch.
    It read as safe only by accident (its anon_key is None and `anon_key = NULL` matches no row),
    which is a property of the data, not a decision — and the next principal kind added would
    inherit the same silent default.

    A registered user is matched on `user_id` WITH the `principal_type` discriminator: a tenant
    login also carries a `user_id` (its tenant_users row), and only the discriminator keeps a
    coincidence of ids from reading as ownership.
    """
    if principal.is_superadmin:
        return "TRUE", []
    if principal.is_tenant:
        return f"client_id = ${first}", [principal.client_id]
    if principal.kind == "user" and principal.user_id:
        return f"user_id = ${first} AND principal_type = 'user'", [principal.user_id]
    if principal.kind == "integration":
        raise HTTPException(status_code=403,
                            detail="This integration credential cannot read audio jobs.")
    return f"anon_key = ${first} AND principal_type = 'anonymous'", [principal.anon_key]


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
    where, args = _scope(principal, first=2)
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
