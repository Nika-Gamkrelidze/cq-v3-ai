"""Shared audio-analysis pipeline: transcribe -> tenant RAG -> Claude analysis ->
KB fact-check -> rubric scoring, writing progress to one audio_jobs row.

One code path for every caller — the synchronous /analyze endpoint AND the async
/v1/analyses[/batch] partner endpoints — so results are identical and stay tenant-isolated
by client_id. Business failures are recorded as status='error' on the row; the function
never raises for them, so a background task can't crash the worker.
"""
import asyncio
import json
import logging
import time

from ..db import pool
from . import (claude, elevenlabs, factcheck, retrieval, scoring,
               scoring_store, settings_store)

log = logging.getLogger("cq")

# Bound concurrent BACKGROUND jobs so a large batch cannot exhaust the ElevenLabs /
# Anthropic keys or the asyncpg pool. The interactive sync /analyze does not use this.
_SEM = asyncio.Semaphore(3)

TERMINAL = ("done", "error")


async def _update(job_id: str, **fields) -> None:
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        vals.append(v)
        cast = "::jsonb" if k in ("analysis", "kb_used", "kb_check", "scoring") else ""
        cols.append(f"{k} = ${len(vals)+1}{cast}")
    async with pool().acquire() as conn:
        await conn.execute(
            f"UPDATE audio_jobs SET {', '.join(cols)}, updated_at=now() WHERE id=$1", job_id, *vals)


async def create_job(*, filename, content_type, size_bytes, client_id, principal_kind,
                     anon_key, status="queued", batch_id=None, external_ref=None) -> str:
    cfg = await settings_store.get_effective()
    async with pool().acquire() as conn:
        return str(await conn.fetchval(
            """
            INSERT INTO audio_jobs
                (filename, content_type, size_bytes, status, stt_model, llm_model,
                 client_id, principal_type, anon_key, batch_id, external_ref)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            RETURNING id
            """,
            filename, content_type, size_bytes, status, cfg["stt_model"], cfg["llm_model"],
            client_id, principal_kind, anon_key, batch_id, external_ref))


async def run_pipeline(job_id: str, audio: bytes, filename: str, content_type: str,
                       client_id: str | None, is_tenant: bool) -> dict:
    """Run the full pipeline for an already-created row, updating it in place. Returns the
    final result dict (with status 'done' or 'error'). Never raises for business errors."""
    cfg = await settings_store.get_effective()
    started = time.monotonic()

    async def fail(msg: str) -> dict:
        await _update(job_id, status="error", error=msg)
        return {"id": job_id, "status": "error", "error": msg}

    # 1. Transcribe
    await _update(job_id, status="transcribing")
    try:
        stt = await elevenlabs.transcribe(
            audio, filename, content_type, cfg["elevenlabs_api_key"], cfg["stt_model"])
    except Exception as exc:  # noqa: BLE001
        return await fail(f"Transcription failed: {exc}")
    transcript = (stt.get("text") or "")
    language = stt.get("language_code")
    await _update(job_id, status="analyzing", transcript=transcript, language=language)

    # 2. Tenant RAG
    kb_context, kb_used = "", []
    if is_tenant and transcript.strip():
        try:
            hits = await retrieval.retrieve(client_id, transcript)
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
        return await fail(f"Analysis failed: {exc}")

    # 4. KB fact-check + 5. rubric scoring — tenant-scoped, never block the result.
    kb_check = scorecard = None
    if is_tenant and transcript.strip():
        try:
            async with pool().acquire() as conn:
                has_kb = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM kb_chunks WHERE client_id = $1)", client_id)
            if has_kb:
                kb_check = await factcheck.run_factcheck(
                    transcript, client_id, cfg["anthropic_api_key"], cfg["llm_model"])
        except Exception:  # noqa: BLE001
            kb_check = None
        try:
            cfg_scoring = await scoring_store.get_active_config(client_id)
            if cfg_scoring and cfg_scoring.get("dimensions"):
                scorecard = await scoring.run_scoring(
                    transcript, cfg_scoring, cfg["anthropic_api_key"], cfg["llm_model"])
        except Exception:  # noqa: BLE001
            scorecard = None

    processing_ms = int((time.monotonic() - started) * 1000)
    await _update(job_id, status="done", analysis=json.dumps(analysis),
                  language=(analysis.get("language") or language), processing_ms=processing_ms,
                  kb_used=json.dumps(kb_used),
                  kb_check=json.dumps(kb_check) if kb_check is not None else None,
                  scoring=json.dumps(scorecard) if scorecard is not None else None)
    return {"id": job_id, "status": "done", "filename": filename,
            "language": analysis.get("language") or language, "transcript": transcript,
            "analysis": analysis, "kb_used": kb_used, "kb_check": kb_check,
            "scoring": scorecard, "processing_ms": processing_ms}


async def run_background(job_id: str, audio: bytes, filename: str, content_type: str,
                         client_id: str | None, is_tenant: bool) -> None:
    """Background entrypoint: same pipeline, but bounded by the concurrency semaphore and
    fully swallowing errors (they are already recorded on the row by run_pipeline)."""
    async with _SEM:
        try:
            await run_pipeline(job_id, audio, filename, content_type, client_id, is_tenant)
        except Exception as exc:  # noqa: BLE001 — last-resort guard for a background task
            log.exception("analysis job %s crashed", job_id)
            try:
                await _update(job_id, status="error", error=f"Internal error: {exc}")
            except Exception:  # noqa: BLE001
                pass


async def sweep_stuck_jobs() -> int:
    """On startup, fail any job left mid-flight by a crash/restart. Audio bytes are not
    persisted, so these cannot be re-run automatically — the partner resubmits."""
    async with pool().acquire() as conn:
        res = await conn.execute(
            """
            UPDATE audio_jobs SET status='error',
                   error='Interrupted by a server restart — please resubmit.', updated_at=now()
            WHERE status IN ('queued','transcribing','analyzing')
            """)
    try:
        return int(res.split()[-1])
    except (ValueError, IndexError):
        return 0
