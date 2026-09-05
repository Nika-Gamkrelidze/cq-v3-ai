"""Shared audio-analysis pipeline: transcribe -> tenant RAG -> Claude analysis ->
KB fact-check -> rubric scoring, writing progress to one audio_jobs row.

One code path for every caller — the synchronous /analyze endpoint AND the async
/v1/analyses[/batch] partner endpoints — so results are identical and stay tenant-isolated
by client_id. Business failures are recorded as status='error' on the row; the function
never raises for them, so a background task can't crash the worker.

The row is also the workbench's "recording": `create_job` keeps the uploaded bytes for every
principal kind (see its docstring) and `mark_ready` parks a transcribed-but-unanalysed row in
status 'ready', from which the analysers run on demand.
"""
import asyncio
import json
import logging
import time

from ..db import pool
from . import (attribution, claude, elevenlabs, factcheck, media, retrieval, scoring,
               scoring_store, segments, sentiment, settings_store)

log = logging.getLogger("cq")

# Bound concurrent BACKGROUND jobs so a large batch cannot exhaust the ElevenLabs /
# Anthropic keys or the asyncpg pool. The interactive sync /analyze does not use this.
_SEM = asyncio.Semaphore(3)

TERMINAL = ("done", "error")

# Columns whose Python value is a JSON string that must land in a jsonb column. Anything
# else in `_update` is passed through untouched.
_JSONB = frozenset({"analysis", "kb_used", "kb_check", "scoring", "sentiment", "segments",
                    "semantic"})


async def _update(job_id: str, **fields) -> None:
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        vals.append(v)
        cast = "::jsonb" if k in _JSONB else ""
        cols.append(f"{k} = ${len(vals)+1}{cast}")
    async with pool().acquire() as conn:
        await conn.execute(
            f"UPDATE audio_jobs SET {', '.join(cols)}, updated_at=now() WHERE id=$1", job_id, *vals)


async def create_job(*, filename, content_type, size_bytes, client_id, principal_kind,
                     anon_key, status="queued", batch_id=None, external_ref=None,
                     client_ip=None, audio=None, user_id=None, source="audio",
                     created_by=None) -> str:
    """Create the row. When `audio` is given the bytes are retained — for EVERY principal.

    Recordings used to be kept only for anonymous visitors (so abuse could be investigated);
    a tenant's audio was deliberately dropped once transcribed. History now replays a call
    with the analysers' highlights on a player, which needs the bytes back, so every stored
    copy gets the ONE global deadline from the Storage setting (`retention_days`, 0 = keep
    forever), written at insert time — never "when someone remembers". What the purge does
    at that deadline differs by who uploaded (services/retention.py): a signed-in principal
    keeps the transcript and results and loses only the file.

    `user_id` is the registered-user owner (None for every other kind); `source` is 'audio'
    or 'text' (a pasted transcript, no bytes). Both default so older callers are unchanged.
    """
    cfg = await settings_store.get_effective()
    stored, purge_after = {}, None
    if audio is not None:
        storage = await settings_store.get_storage_config()
        # Off the event loop: `media.save` writes the bytes and hashes them synchronously, and
        # since audio is kept for EVERY kind that is now up to 100 MB per upload (and once per
        # file inside a /summaries batch), measured at ~0.5 s each. On the single uvicorn
        # worker an inline call stalls every other request for that long.
        stored = await asyncio.to_thread(
            media.save, audio, content_type=content_type, filename=filename)
        purge_after = media.deadline(storage["retention_days"])
    async with pool().acquire() as conn:
        job_id = str(await conn.fetchval(
            """
            INSERT INTO audio_jobs
                (filename, content_type, size_bytes, status, stt_model, llm_model,
                 client_id, principal_type, anon_key, batch_id, external_ref,
                 client_ip, audio_path, audio_bytes, audio_sha256, purge_after,
                 user_id, source, created_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
            RETURNING id
            """,
            filename, content_type, size_bytes, status, cfg["stt_model"], cfg["llm_model"],
            client_id, principal_kind, anon_key, batch_id, external_ref,
            client_ip, stored.get("path"), stored.get("bytes"), stored.get("sha256"),
            purge_after, user_id, source, created_by))
    # Every AI call that follows in this request belongs to this recording, so the token
    # accounting can show a customer the call a line on their bill came from. Set HERE, the
    # one place a job id comes into existence, rather than in the ten routers that call this
    # — a router that forgot would record work against no recording at all.
    attribution.set_job(job_id)
    return job_id


async def mark_error(job_id: str, msg: str) -> None:
    """Record a terminal failure on a row owned by a caller that is not run_pipeline."""
    await _update(job_id, status="error", error=msg)


async def mark_transcribed(job_id: str, *, transcript: str, language: str | None,
                           sentiment: dict | None = None) -> None:
    """Close out a transcribe-only job (/transcribe): no analysis, no LLM, still 'done'.

    Separate from run_pipeline's own writes so the STT-only product cannot accidentally leave
    a row stuck in 'transcribing' and get swept as abandoned.
    """
    await _update(job_id, status="done", transcript=transcript, language=language,
                  sentiment=json.dumps(sentiment) if sentiment is not None else None)


async def mark_ready(job_id: str, *, transcript: str, language: str | None,
                     segments: list[dict], duration_s: float | None) -> None:
    """Park a workbench recording in status 'ready': transcribed, timeline built, no
    analyser run yet. The analysers (fact-check, score, semantic) are triggered on demand
    against this row later, each writing its own column — so unlike 'done' this is a resting
    state, not a terminal one, and `sweep_stuck_jobs` must leave it alone."""
    await _update(job_id, status="ready", transcript=transcript, language=language,
                  segments=json.dumps(segments), duration_s=duration_s)


def _timeline(stt: dict, transcript: str) -> tuple[list[dict], float | None]:
    """(segments, duration_s) for a Scribe result. Falls back to line-based segments when the
    STT response carried no usable words (older responses, or an empty recording), so a
    legacy job still has a transcript the workbench can highlight — just without times."""
    segs = segments.build_segments(stt.get("words")) or segments.segments_from_text(transcript)
    return segs, segments.duration_of(segs)


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
    # The timeline is stored with the transcript, not at the end: if a later stage fails the
    # row still carries everything the workbench needs to replay the recording.
    segs, duration_s = _timeline(stt, transcript)
    await _update(job_id, status="analyzing", transcript=transcript, language=language,
                  segments=json.dumps(segs), duration_s=duration_s)

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
            cfg["analysis_instructions"], kb_context=kb_context, client_id=client_id)
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
                # `segments=segs` — the SAME list stored on the row above. Both analysers
                # return `#` indices into whatever timeline they were prompted with; leave it
                # out and they rebuild one from the transcript's own lines, so the spans the
                # workbench replays would point at paragraphs nobody ever stored.
                kb_check = await factcheck.run_factcheck(
                    transcript, client_id, cfg["anthropic_api_key"], cfg["llm_model"],
                    segments=segs)
        except Exception:  # noqa: BLE001
            kb_check = None
        try:
            cfg_scoring = await scoring_store.get_active_config(client_id)
            if cfg_scoring and cfg_scoring.get("dimensions"):
                scorecard = await scoring.run_scoring(
                    transcript, cfg_scoring, cfg["anthropic_api_key"], cfg["llm_model"],
                    client_id=client_id, segments=segs)
        except Exception:  # noqa: BLE001
            scorecard = None

    # 6. Sentiment — the words (from the analysis above) plus the voice (prosody sidecar).
    # Runs for every caller, not just tenants: it needs no knowledge base and it is the one
    # signal an anonymous visitor gets beyond the transcript. Never blocks the result.
    try:
        sent = await sentiment.analyse(audio, analysis, filename=filename,
                                       content_type=content_type)
    except Exception:  # noqa: BLE001 — a tone model must never cost anyone their transcript
        log.exception("sentiment failed for job %s", job_id)
        sent = None

    processing_ms = int((time.monotonic() - started) * 1000)
    await _update(job_id, status="done", analysis=json.dumps(analysis),
                  language=(analysis.get("language") or language), processing_ms=processing_ms,
                  kb_used=json.dumps(kb_used),
                  kb_check=json.dumps(kb_check) if kb_check is not None else None,
                  scoring=json.dumps(scorecard) if scorecard is not None else None,
                  sentiment=json.dumps(sent) if sent is not None else None)
    return {"id": job_id, "status": "done", "filename": filename,
            "language": analysis.get("language") or language, "transcript": transcript,
            "segments": segs, "duration_s": duration_s,
            "analysis": analysis, "kb_used": kb_used, "kb_check": kb_check,
            "scoring": scorecard, "sentiment": sent, "processing_ms": processing_ms}


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
    """On startup, fail any job left mid-flight by a crash/restart.

    The stored audio would make a re-run possible, but nothing resumes an interrupted
    pipeline yet — the caller resubmits. Only the three in-flight statuses are swept: 'ready'
    is a workbench recording waiting for an analyser to be asked for, not a job that was
    interrupted, and marking it failed would erase a perfectly good transcript."""
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
