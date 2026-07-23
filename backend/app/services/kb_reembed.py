"""Full-KB re-embed — the worker half of the tenant's queued re-embed.

`kb_console.enqueue_reembed()` writes a `kb_reembed_jobs` row and returns 202; this module is
what actually drains it, from `cq-worker`. Nothing here ever runs inside a request.

WHY the split (db/kb_ops.sql says it too, from the schema side): embeddings are fp32 BGE-M3 on
ONE CPU-bound TEI container with no CPU reservation, shared with live retrieval — the autopilot's
read path, every analysis job, every fact-check. Re-embedding a whole knowledge base is therefore
the single most expensive thing a tenant can trigger, and doing it inline would blow nginx's
300 s `proxy_read_timeout`, pin one of ten asyncpg pool connections for the duration, and starve
every *other* tenant's retrieval for as long as it ran.

Three properties this file exists to guarantee:

  * **Throttled.** `reembed_document` already funnels its batches through `kb_ingest._embed_gate`
    (one document's batch in flight at a time), so a latency-critical query embed gets an encoder
    slot within roughly one batch. We deliberately do NOT add a second, competing gate — we add a
    deliberate pause *between* documents on top of it (see DOC_PAUSE_S).

  * **Resumable.** Every push to `main` auto-deploys and restarts the stack, so a mid-run kill is
    routine, not exceptional. The claim is heartbeat-based and `done_documents` +
    `failed_documents` is a durable watermark written after every document, so a restart resumes
    at the document it died on instead of re-embedding the whole KB from zero.

  * **Not fragile.** One unreadable document must never strand a tenant's entire KB: per-document
    failures increment `failed_documents`, are logged, and the run carries on. The one exception
    is a *streak* of failures (MAX_CONSECUTIVE_FAILURES), which does not mean "bad document", it
    means the encoder is down — and hammering a dead TEI for another thousand documents helps
    nobody.

Re-chunking is deliberately NOT done here: the content is unchanged, so `ingest_document` would
be wasted CPU *and* would churn chunk ids (breaking any evidence citation that points at one).
`kb_ingest.reembed_document` replaces vectors only — see kb_console's own note on the difference.
"""
import asyncio
import logging
import time
from collections.abc import Callable

from ..db import pool
from . import kb_events, kb_ingest

log = logging.getLogger("cq")

# Pause between documents. THE TRADE-OFF, stated plainly: this is dead time added to a job the
# tenant is watching a progress bar for, bought in exchange for encoder headroom for everyone
# else on the box. `_embed_gate` already interleaves at batch granularity, but a document is many
# batches back-to-back, so without a gap between documents a long re-embed still keeps the
# encoder's queue permanently non-empty and every live retrieval pays one batch of latency for
# the whole run. One second is roughly one batch's worth of breathing room per document — cheap
# against the seconds-to-minutes a document takes, and it is the reason this job is allowed to be
# slow: nobody is blocked on it finishing.
DOC_PAUSE_S = 1.0

# A `running` job whose heartbeat is older than this lost its worker (deploy, OOM, crash) and is
# reclaimable. Mirrors the 30-minute window `curation_runs` uses, for one reason: two different
# staleness windows in one process is a thing you have to look up, and one of them is always the
# one you misremembered.
CLAIM_STALE_AFTER_S = 30 * 60

# Consecutive per-document failures that end the run. NOT a per-document abort — one bad document
# is expected and merely counted (see the module docstring). A streak this long is the encoder
# being unreachable, and the honest outcome is `state='error'` with a message the tenant can act
# on, rather than a job that "completes" with every document failed.
MAX_CONSECUTIVE_FAILURES = 10

_JOB_COLS = ("id, client_id, state, requested_by, total_documents, done_documents, "
             "failed_documents")


def _job(row) -> dict:
    """The subset of the row the worker needs. `kb_console._job()` owns the API-facing shape."""
    return {"id": str(row["id"]), "client_id": str(row["client_id"]), "state": row["state"],
            "requested_by": row["requested_by"], "total_documents": row["total_documents"],
            "done_documents": row["done_documents"], "failed_documents": row["failed_documents"]}


# --------------------------------------------------------------------------- #
# Claim / heartbeat / progress
# --------------------------------------------------------------------------- #
async def claim_job() -> dict | None:
    """Claim the oldest queued (or abandoned) re-embed job, or None if there is nothing to do.

    Deliberately the ONE query in this module with no `client_id` predicate: the worker's queue is
    cross-tenant by definition and the tenant is a *column* of the work item, not a filter on it.
    Everything downstream — the document list, every re-embed, every counter update — is scoped to
    the `client_id` this row hands back.

    Claiming and reclaiming are one statement on purpose. `state='queued'` covers a fresh request;
    a `running` row with a stale heartbeat covers the routine case of a deploy killing a worker
    mid-run, and it resumes rather than restarting because `done_documents` survived the restart.
    `FOR UPDATE SKIP LOCKED` keeps two workers from claiming the same row if this process is ever
    scaled past one replica.

    Note the state transition stays inside `uq_kb_reembed_jobs_active` (queued -> running are both
    "active"), so it can never conflict with the tenant's own double-click guard.
    """
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE kb_reembed_jobs
               SET state = 'running',
                   started_at = COALESCE(started_at, now()),
                   heartbeat_at = now()
             WHERE id = (
                   SELECT id FROM kb_reembed_jobs
                    WHERE state = 'queued'
                       OR (state = 'running'
                           AND (heartbeat_at IS NULL
                                OR heartbeat_at < now() - make_interval(secs => $1::double precision)))
                    ORDER BY created_at
                       FOR UPDATE SKIP LOCKED
                     LIMIT 1)
            RETURNING {_JOB_COLS}
            """,
            float(CLAIM_STALE_AFTER_S))
    return _job(row) if row else None


async def heartbeat(job_id: str) -> None:
    """Mark the job alive. Addressed by uuid PK — the row carries no tenant data of its own.

    Called once per document rather than once per job: a single 400-chunk PDF is minutes of
    encoder time, and a run that never heartbeats between documents could be declared stale and
    stolen from itself by `claim_job` while it is perfectly healthy.
    """
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE kb_reembed_jobs SET heartbeat_at = now() WHERE id = $1 AND state = 'running'",
            job_id)


async def _advance(job_id: str, client_id: str, *, ok: bool) -> bool:
    """Record one processed document. Returns False if the job is no longer ours.

    The durable watermark: written after EVERY document, which is what makes both the progress bar
    real and the restart resumable. The `state='running'` guard is how a cancel (or a reclaim by
    another worker) is noticed — a lost claim must not keep writing counters over someone else's run.
    """
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE kb_reembed_jobs
               SET done_documents = done_documents + $3,
                   failed_documents = failed_documents + $4,
                   heartbeat_at = now()
             WHERE id = $1 AND client_id = $2 AND state = 'running'
            RETURNING done_documents, failed_documents
            """,
            job_id, client_id, 1 if ok else 0, 0 if ok else 1)
    return row is not None


async def _requeue(job_id: str, client_id: str) -> None:
    """Hand the job back to the queue on SIGTERM. A deploy is not a failure.

    `queued`, never `error`: the work is not wrong, this process just ran out of time. The next
    worker's `claim_job` picks it up and resumes from the watermark, and the tenant's progress bar
    never moves backwards.
    """
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE kb_reembed_jobs SET state = 'queued', heartbeat_at = now() "
            "WHERE id = $1 AND client_id = $2 AND state = 'running'",
            job_id, client_id)


async def _finish(job_id: str, client_id: str, state: str, error: str | None = None) -> dict | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE kb_reembed_jobs
               SET state = $3, error = $4, finished_at = now(), heartbeat_at = now()
             WHERE id = $1 AND client_id = $2 AND state = 'running'
            RETURNING done_documents, failed_documents
            """,
            job_id, client_id, state, (error or None) and error[:2000])
    return dict(row) if row else None


async def _ready_document_ids(client_id: str) -> list[str]:
    """The documents to re-embed, in a stable order.

    `ORDER BY created_at, id` is load-bearing, not cosmetic: the resume watermark is a COUNT, so
    the ordering has to be the same on the second worker as on the first, and new documents have
    to append at the END (a document imported mid-run must not shift the offset and cause an
    already-done document to be skipped). Deleting a document mid-run can still shift it, which
    costs at worst one document re-embedded twice — `reembed_document` only replaces vectors, so
    that is wasted CPU, never a wrong result.
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM kb_documents WHERE client_id = $1 AND status = 'ready' "
            "ORDER BY created_at, id",
            client_id)
    return [str(r["id"]) for r in rows]


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
async def run_job(job: dict, *, should_stop: Callable[[], bool] | None = None) -> None:
    """Drain one claimed job, document by document, throttled and resumable.

    `should_stop` is the worker's SIGTERM flag (`_stop.is_set`). It is a parameter rather than an
    import so this module stays importable from the api process without dragging in `worker`;
    called with no callback it simply never stops early.
    """
    job_id, client_id = job["id"], job["client_id"]
    stop = should_stop or (lambda: False)
    started = time.monotonic()

    try:
        doc_ids = await _ready_document_ids(client_id)
    except Exception as exc:  # noqa: BLE001 — the job must not be left `running` forever
        log.exception("kb re-embed: job=%s could not list documents", job_id)
        await _finish(job_id, client_id, "error", f"could not list documents: {exc}")
        return

    # Refresh the denominator the API guessed at enqueue time: documents can have been added,
    # deleted or repaired in the interval, and a progress bar whose scale is wrong is worse than
    # no progress bar.
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE kb_reembed_jobs SET total_documents = $3 "
            "WHERE id = $1 AND client_id = $2 AND state = 'running'",
            job_id, client_id, len(doc_ids))

    # Both counters, not just `done_documents`: a document that failed was still *processed*, and
    # resuming at `done_documents` alone would retry every failure on every restart — which, when
    # the failure is deterministic, is an infinite loop that never reaches the end of the KB.
    processed = int(job.get("done_documents") or 0) + int(job.get("failed_documents") or 0)
    pending = doc_ids[processed:]
    if processed:
        log.info("kb re-embed: job=%s resuming at document %s/%s", job_id, processed, len(doc_ids))

    consecutive_failures = 0
    for doc_id in pending:
        # Between documents, never mid-document: a re-embed is a transaction over one document's
        # chunks, so finishing the current one costs seconds and leaves the KB consistent.
        if stop():
            await _requeue(job_id, client_id)
            log.info("kb re-embed: job=%s stop requested; re-queued for the next worker", job_id)
            return

        await heartbeat(job_id)
        try:
            await kb_ingest.reembed_document(doc_id, client_id)
            ok = True
        except Exception as exc:  # noqa: BLE001 — one document must not strand the whole KB
            ok = False
            log.warning("kb re-embed: job=%s doc=%s failed: %s", job_id, doc_id, exc)

        if not await _advance(job_id, client_id, ok=ok):
            # Cancelled by a human, or reclaimed by another worker while we were slow.
            log.info("kb re-embed: job=%s no longer running; abandoning", job_id)
            return

        if ok:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                msg = (f"aborted after {consecutive_failures} consecutive document failures "
                       f"(the embedding service is likely unavailable)")
                log.error("kb re-embed: job=%s %s", job_id, msg)
                await _finish(job_id, client_id, "error", msg)
                return

        # The throttle. Interruptible by construction: the stop flag is re-read at the top of the
        # next iteration, one second later at most.
        await asyncio.sleep(DOC_PAUSE_S)

    counters = await _finish(job_id, client_id, "done")
    if counters is None:
        return
    done, failed = counters["done_documents"], counters["failed_documents"]
    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.info("kb re-embed: job=%s finished client=%s done=%s failed=%s in %.1fs",
             job_id, client_id, done, failed, elapsed_ms / 1000)
    await kb_events.log(
        client_id, "reembed", actor=job.get("requested_by") or "tenant",
        status="ok" if not failed else "partial", duration_ms=elapsed_ms,
        detail=f"full re-embed finished: {done} document(s) re-embedded, {failed} failed")
