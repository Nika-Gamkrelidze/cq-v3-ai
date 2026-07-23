"""KB console operations — ONE implementation, two audiences.

Every capability the superadmin KB console has (stats, params, document/chunk editing, the
retrieval playground, duplicates, export, activity, bulk actions, re-embedding, publishing)
lives here as a tenant-agnostic function whose FIRST argument is the client_id it is scoped to.
`routers/kb_admin.py` (operator, tenant-parameterized path) and `routers/kb.py` (tenant, tenant
taken only from the principal) are both thin translations over these functions.

WHY the extraction, rather than a second hand-written copy on the tenant side: twelve operations
maintained twice diverge within a month, and a divergence *here* is not a cosmetic bug — it is a
tenant-isolation bug. The one invariant this whole file exists to hold is that **every SQL
statement filters by client_id**, and it is far easier to keep that true in one place than in two
that drift. The single deliberate exception is the pg_catalog introspection in `params()`, which
reads column *types*, never tenant rows; it is marked inline.

Two conventions the routers depend on:

  * **No HTTPException here.** Not-found / bad-input raise `KBConsoleError`, which carries an
    HTTP-ish `status` hint the routers translate. A service that imports `fastapi` is a service
    that can only ever be called from a request, and the queued re-embed below is read by
    `cq-worker`, which has no request.
  * **Every mutating operation writes a `kb_events` row** with the caller's `actor`. That actor
    string ("superadmin" vs "tenant:<user>") is the only thing that makes the shared activity
    timeline answer "did *we* do this, or did the operator?" — which is the question a regulated
    tenant actually asks of an audit log.

A pool connection is never held across an embedding await (ingest and re-embed are called
outside every `async with pool().acquire()` block): the api runs one uvicorn worker, and parking
a connection behind the CPU-bound TEI encoder starves live retrieval.
"""
import csv
import io
import json
import logging

from ..db import pool
from . import kb_events, kb_ingest, retrieval, settings_store
from .embeddings.base import EmbeddingError

log = logging.getLogger("cq")

# What a re-embed can fail with. `kb_ingest.IngestError` covers ingestion's own invariants
# (count mismatch, chunk not found), but the DOMINANT failure — encoder down, 5xx, timeout —
# surfaces as `EmbeddingError`, an independent RuntimeError subclass raised by the provider and
# deliberately not wrapped. Catching only IngestError therefore makes every 502-plus-audit path
# below dead code for exactly the case it was written for, and the tenant gets a bare 500 with
# no event row. Both are caught everywhere embedding work runs.
REEMBED_FAILURES = (kb_ingest.IngestError, EmbeddingError)

# Two values, deliberately (mirrors routers/kb.py): anything richer invites "sort of public"
# and the publishability guarantee stops being auditable.
VISIBILITIES = ("internal", "public")
BULK_ACTIONS = ("delete", "reembed", "retag", "publish", "unpublish")

# A document page. 500 rather than 200 because the tenant portal's legacy list route asks for
# 500 in one shot and must not silently start truncating now that it shares this code path.
MAX_PAGE = 500
MAX_CHUNK_PAGE = 1000
# The near-duplicate scan is a self-join over kb_chunks — O(n^2) in chunks. Above this it is
# skipped rather than allowed to lock a pool connection for minutes on a big tenant.
NEAR_DUP_MAX_CHUNKS = 4000
NEAR_DUP_LIMIT = 50


class KBConsoleError(RuntimeError):
    """A caller-visible failure with an HTTP-ish status hint. Routers translate it.

    Deliberately NOT an HTTPException: this module is also called from `cq-worker`.
    """

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _iso(value):
    return value.isoformat() if value is not None else None


def _doc(row) -> dict:
    d = dict(row)
    d["id"] = str(row["id"])
    if d.get("client_id") is not None:
        d["client_id"] = str(d["client_id"])
    for key in ("created_at", "updated_at"):
        if key in d:
            d[key] = _iso(d[key])
    if isinstance(d.get("metadata"), str):
        d["metadata"] = json.loads(d["metadata"])
    return d


def _chunk(row) -> dict:
    meta = row["metadata"]
    return {"id": str(row["id"]), "chunk_index": row["chunk_index"], "content": row["content"],
            "metadata": json.loads(meta) if isinstance(meta, str) else meta,
            "has_embedding": row["has_embedding"], "token_count": row["token_count"]}


def check_visibility(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in VISIBILITIES:
        raise KBConsoleError(400, "visibility must be 'internal' or 'public'")
    return v


def _page(limit, offset, cap: int) -> tuple[int, int]:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    return min(max(limit, 1), cap), max(offset, 0)


# --------------------------------------------------------------------------- #
# Stats / parameters
# --------------------------------------------------------------------------- #
async def stats(client_id: str) -> dict:
    """Health of one tenant's KB: counts, embedding coverage, approximate size."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM kb_documents WHERE client_id=$1) AS documents,
              (SELECT count(*) FROM kb_documents WHERE client_id=$1 AND status='error') AS failed,
              (SELECT count(*) FROM kb_documents WHERE client_id=$1 AND status IN ('pending','processing')) AS in_progress,
              (SELECT count(*) FROM kb_documents WHERE client_id=$1 AND visibility='public') AS public_documents,
              (SELECT count(*) FROM kb_chunks WHERE client_id=$1) AS chunks,
              (SELECT count(*) FROM kb_chunks WHERE client_id=$1 AND embedding IS NULL) AS chunks_no_embedding,
              (SELECT count(*) FROM kb_chunks WHERE client_id=$1 AND (content IS NULL OR btrim(content)='')) AS empty_chunks,
              (SELECT coalesce(sum(token_count),0) FROM kb_chunks WHERE client_id=$1) AS approx_tokens,
              (SELECT coalesce(sum(char_count),0) FROM kb_documents WHERE client_id=$1) AS approx_chars,
              (SELECT max(updated_at) FROM kb_documents WHERE client_id=$1) AS last_updated
            """, client_id)
    out = dict(row)
    chunks = out["chunks"] or 0
    out["embedding_coverage"] = (
        round(100 * (chunks - (out["chunks_no_embedding"] or 0)) / chunks) if chunks else 100)
    out["last_updated"] = _iso(out["last_updated"])
    return out


async def params(client_id: str) -> dict:
    """The knobs retrieval actually runs with, plus the dim-mismatch alarm.

    `dim_mismatch` is the one number worth staring at: the pgvector column dimension MUST equal
    the configured provider's, and when it does not, every new embedding fails — silently, from
    the tenant's point of view, as "search stopped finding things".
    """
    emb = await settings_store.get_embedding_config()
    async with pool().acquire() as conn:
        # pg_catalog introspection: reads a COLUMN TYPE, never tenant rows. This is the single
        # statement in this module with no client_id filter, and it is data-free by construction.
        coltype = await conn.fetchval(
            """
            SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
            JOIN pg_class c ON a.attrelid=c.oid WHERE c.relname='kb_chunks' AND a.attname='embedding'
            """)
        null_emb = await conn.fetchval(
            "SELECT count(*) FROM kb_chunks WHERE client_id=$1 AND embedding IS NULL", client_id)
    col_dim = None
    if coltype and "(" in coltype:
        try:
            col_dim = int(coltype.split("(")[1].rstrip(")"))
        except ValueError:
            col_dim = None
    return {
        "embedding": {"provider": emb.get("provider"), "model": emb.get("model"),
                      "dim": int(emb.get("dim")), "base_url": emb.get("base_url"),
                      "column_dim": col_dim,
                      "dim_mismatch": col_dim is not None and col_dim != int(emb.get("dim"))},
        "chunking": {"size": kb_ingest.CHUNK_SIZE, "overlap": kb_ingest.CHUNK_OVERLAP},
        "retrieval": {"default_top_k": retrieval.DEFAULT_TOP_K,
                      "similarity_threshold": retrieval.SIM_THRESHOLD,
                      "distance_metric": "cosine", "index_type": "hnsw (vector_cosine_ops)"},
        "warnings": {"chunks_without_embedding": null_emb},
    }


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
async def list_documents(client_id: str, *, q: str | None = None, doc_type: str | None = None,
                         status: str | None = None, visibility: str | None = None,
                         limit: int = 50, offset: int = 0,
                         tag: str | None = None) -> dict:
    """Filtered, paginated document list. `tag` is an extra filter the operator console uses."""
    # The client_id predicate is part of the LITERAL sql, never an assembled fragment: the
    # optional filters can only ever NARROW it, and no code path can drop it by accident.
    extra = ""
    args: list = [client_id]
    if status:
        args.append(status); extra += f" AND status = ${len(args)}"
    if visibility:
        args.append(check_visibility(visibility)); extra += f" AND visibility = ${len(args)}"
    if doc_type:
        args.append(doc_type); extra += f" AND doc_type = ${len(args)}"
    if tag:
        args.append(tag); extra += f" AND ${len(args)} = ANY(tags)"
    if q:
        args.append(f"%{q}%")
        extra += f" AND (title ILIKE ${len(args)} OR content_text ILIKE ${len(args)})"
    lim, off = _page(limit, offset, MAX_PAGE)
    async with pool().acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM kb_documents WHERE client_id = $1{extra}", *args)
        rows = await conn.fetch(
            "SELECT id, doc_type, title, status, visibility, tags, chunk_count, char_count, "
            "source_type, source_uri, actor, ingest_ms, error, created_at, updated_at "
            f"FROM kb_documents WHERE client_id = $1{extra} "
            f"ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, lim, off)
    return {"total": total, "limit": limit, "offset": offset,
            "documents": [_doc(r) for r in rows]}


async def get_document(client_id: str, doc_id: str) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM kb_documents WHERE id=$1 AND client_id=$2", doc_id, client_id)
    if not row:
        raise KBConsoleError(404, "Document not found")
    return _doc(row)


async def update_document(client_id: str, doc_id: str, *, title: str | None = None,
                          doc_type: str | None = None, tags: list[str] | None = None,
                          text: str | None = None, metadata: dict | None = None,
                          actor: str = "tenant") -> dict:
    """Edit metadata and/or replace the document's text.

    New `text` goes through `kb_ingest.ingest_document`, NOT `reembed_document`: re-embedding
    only replaces the vectors of the chunks that already exist and never reads `content_text`,
    so using it after a text edit would leave the KB answering from the old wording with new
    vectors. Ingestion re-chunks and re-embeds, which is what an edit means.

    `ingest_document` swallows every exception into `status='error'` and returns None, so the
    outcome is re-read from `kb_documents.status`. Without that re-read a failed re-embed is
    reported to the tenant as a successful save — the document silently stops being retrievable
    and nobody finds out until a customer gets the wrong answer.
    """
    async with pool().acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM kb_documents WHERE id=$1 AND client_id=$2", doc_id, client_id)
        if not exists:
            raise KBConsoleError(404, "Document not found")
        sets, vals = [], []
        if title is not None:
            vals.append(title); sets.append(f"title = ${len(vals)+2}")
        if doc_type is not None:
            vals.append(doc_type); sets.append(f"doc_type = ${len(vals)+2}")
        if tags is not None:
            vals.append(list(tags)); sets.append(f"tags = ${len(vals)+2}")
        if metadata is not None:
            vals.append(json.dumps(metadata)); sets.append(f"metadata = ${len(vals)+2}::jsonb")
        if sets:
            await conn.execute(
                f"UPDATE kb_documents SET {', '.join(sets)}, updated_at=now() "
                "WHERE id=$1 AND client_id=$2", doc_id, client_id, *vals)

    reembedded = None
    if text is not None:
        # Outside the acquire above: this awaits the shared CPU-bound encoder.
        await kb_ingest.ingest_document(doc_id, client_id, "edit", text=text)
        async with pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, chunk_count, error FROM kb_documents WHERE id=$1 AND client_id=$2",
                doc_id, client_id)
        if row is None:
            raise KBConsoleError(404, "Document not found")
        if row["status"] == "error":
            detail = (row["error"] or "unknown error")[:500]
            await kb_events.log(client_id, "edit", document_id=doc_id, actor=actor,
                                status="error", detail=detail)
            raise KBConsoleError(502, f"Re-embedding failed: {detail}")
        reembedded = row["chunk_count"]

    await kb_events.log(client_id, "edit", document_id=doc_id, actor=actor, status="ok",
                        chunk_count=reembedded,
                        detail="content re-embedded" if text is not None else "metadata updated")
    return {"updated": True, "reembedded_chunks": reembedded}


async def delete_document(client_id: str, doc_id: str, *, actor: str = "tenant") -> dict:
    async with pool().acquire() as conn:
        res = await conn.execute(
            "DELETE FROM kb_documents WHERE id=$1 AND client_id=$2", doc_id, client_id)
    if res.endswith("0"):
        raise KBConsoleError(404, "Document not found")
    await kb_events.log(client_id, "delete", document_id=doc_id, actor=actor, status="ok")
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Chunks
# --------------------------------------------------------------------------- #
async def list_chunks(client_id: str, doc_id: str, *, limit: int | None = 200,
                      offset: int = 0) -> dict:
    """Chunks of one document. `limit=None` returns them all (the operator console's view)."""
    sql = ("SELECT id, chunk_index, content, metadata, (embedding IS NOT NULL) AS has_embedding, "
           "token_count FROM kb_chunks WHERE document_id=$1 AND client_id=$2 ORDER BY chunk_index")
    args: list = [doc_id, client_id]
    if limit is not None:
        lim, off = _page(limit, offset, MAX_CHUNK_PAGE)
        sql += f" LIMIT ${len(args)+1} OFFSET ${len(args)+2}"
        args += [lim, off]
    async with pool().acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM kb_chunks WHERE document_id=$1 AND client_id=$2",
            doc_id, client_id)
        rows = await conn.fetch(sql, *args)
    return {"total": total, "limit": limit, "offset": offset,
            "chunks": [_chunk(r) for r in rows]}


async def update_chunk(client_id: str, chunk_id: str, *, content: str,
                       actor: str = "tenant") -> dict:
    """Edit one chunk's text and re-embed just that chunk (bounded — safe inline)."""
    if not (content or "").strip():
        raise KBConsoleError(400, "content is required")
    try:
        await kb_ingest.reembed_chunk(chunk_id, client_id, new_content=content)
    except kb_ingest.IngestError as exc:
        raise KBConsoleError(404, str(exc)) from exc
    except EmbeddingError as exc:
        # A missing chunk is a 404; an unreachable encoder is not the caller's fault and must
        # not be reported as one (nor as a bare 500 — see REEMBED_FAILURES).
        await kb_events.log(client_id, "chunk_edit", actor=actor, status="error",
                            detail=str(exc)[:500])
        raise KBConsoleError(502, f"Re-embedding failed: {exc}") from exc
    await kb_events.log(client_id, "chunk_edit", actor=actor, status="ok",
                        detail=f"chunk {str(chunk_id)[:8]} re-embedded")
    return {"updated": True}


async def delete_chunk(client_id: str, chunk_id: str, *, actor: str = "tenant") -> dict:
    async with pool().acquire() as conn:
        doc_id = await conn.fetchval(
            "DELETE FROM kb_chunks WHERE id=$1 AND client_id=$2 RETURNING document_id",
            chunk_id, client_id)
        if doc_id is None:
            raise KBConsoleError(404, "Chunk not found")
        await conn.execute(
            "UPDATE kb_documents SET chunk_count=(SELECT count(*) FROM kb_chunks "
            "WHERE document_id=$1 AND client_id=$2), updated_at=now() "
            "WHERE id=$1 AND client_id=$2", doc_id, client_id)
    await kb_events.log(client_id, "chunk_delete", document_id=str(doc_id), actor=actor,
                        status="ok")
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Retrieval playground
# --------------------------------------------------------------------------- #
async def playground(client_id: str, query: str, *, top_k: int = 8,
                     threshold: float = 0.0) -> dict:
    """Exactly what retrieval would return for this query, with scores, ids and method.

    Read-only and never logged to kb_events: it is a debugging read, and an audit log that
    fills up with playground queries stops being read.

    Adds `confidence` (and the `kb_present` it needs) to `search_debug`'s `{method, results}`
    — additively; both surfaces that read this shape keep every key they had. WHY it belongs
    here rather than being left to each UI: a raw BGE-M3 cosine score is not calibrated in
    absolute terms, so a screen full of 0.36–0.40 rows is the encoder reporting "none of
    these match" while looking exactly like a result set (see `services/retrieval.py`).
    The playground is where someone goes to find out why an answer was wrong; it is the last
    place that should be silent about it.

    Confidence is computed from the results `search_debug` ALREADY returned rather than by
    calling `retrieve_ranked` a second time: a second embedding round-trip on an interactive
    path is exactly the waste the query batching elsewhere exists to avoid.

    The verdict is comparable with the tenant's own search box even though the two return
    different numbers of rows — this view is deliberately unfiltered (`threshold=0.0`) while
    `/kb/search` applies the absolute floor. That works because the flat-distribution test is
    measured over a bounded window of the ranking rather than over the returned list; before
    that, the console called the reported ticket `low_score` at the same instant the tenant's
    search box called it `flat_distribution`, which is the operator reading a different
    diagnosis than the person they are on the phone with. See `retrieval.FLAT_WINDOW`.
    """
    result = await retrieval.search_debug(client_id, query, top_k, threshold)
    results = result.get("results") or []
    kb_present = result.get("kb_present")
    if kb_present is None:
        # `search_debug` does not report it. Any hit proves the KB is non-empty, so the probe
        # only runs when there is nothing to infer from — the one case where the answer
        # actually changes the message ("you have imported nothing" vs "nothing matched").
        kb_present = bool(results) or bool(await _kb_present(client_id))
    result["kb_present"] = kb_present
    # `search_debug` only reports method 'keyword' when the embed call FAILED. With no rows to
    # describe either, that is not "nothing matched" — the index was never consulted — and
    # saying so would send a tenant off to fix a KB that is fine. `kb_present=None` selects
    # `unavailable`; the probed `kb_present` above stays in the response, since it is true.
    unavailable = not results and result.get("method") == "keyword"
    result["confidence"] = retrieval.assess_confidence(
        results, method=result.get("method") or "none",
        kb_present=None if unavailable else kb_present)
    return result


async def _kb_present(client_id: str) -> bool:
    """Does this tenant have ANY chunk? One cheap indexed probe (the same EXISTS pre-check
    `retrieve_ranked` and `routers/scoring.py` use).

    Deliberately NOT `AND embedding IS NOT NULL`, even though both vector queries filter on
    it: the trigram fallback does not: it matches `c.content % $1` with no embedding
    predicate, so a chunk that failed to embed is still retrievable. Narrowing this probe
    would report `kb_present: false` for content the very next query can return — and in
    `retrieve_ranked`, where the probe short-circuits, it would make the keyword fallback
    unreachable for precisely the tenants who depend on it.
    """
    async with pool().acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM kb_chunks WHERE client_id = $1)", client_id))


# --------------------------------------------------------------------------- #
# Re-embedding
# --------------------------------------------------------------------------- #
async def reembed_document(client_id: str, doc_id: str, *, actor: str = "tenant") -> dict:
    """Re-embed one document inline. Bounded (one document's chunks), so no queue needed."""
    async with pool().acquire() as conn:
        if not await conn.fetchval(
                "SELECT 1 FROM kb_documents WHERE id=$1 AND client_id=$2", doc_id, client_id):
            raise KBConsoleError(404, "Document not found")
    try:
        n = await kb_ingest.reembed_document(doc_id, client_id)
    except REEMBED_FAILURES as exc:
        await kb_events.log(client_id, "reembed", document_id=doc_id, actor=actor,
                            status="error", detail=str(exc)[:500])
        raise KBConsoleError(502, f"Re-embedding failed: {exc}") from exc
    await kb_events.log(client_id, "reembed", document_id=doc_id, actor=actor, status="ok",
                        chunk_count=n)
    return {"reembedded_chunks": n}


async def reembed_all(client_id: str, *, actor: str = "superadmin") -> dict:
    """Re-embed every ready document INLINE. Operator-only; the tenant path is queued.

    This is the single most expensive thing anyone can trigger — it saturates the shared,
    CPU-bound TEI container that also serves live retrieval for every other tenant. It stays
    here because the superadmin console has always had it and an operator running it knows
    what they are doing; `enqueue_reembed()` below is what the self-serve tenant surface calls.
    """
    async with pool().acquire() as conn:
        ids = [str(r["id"]) for r in await conn.fetch(
            "SELECT id FROM kb_documents WHERE client_id=$1 AND status='ready'", client_id)]
    total = 0
    for i, doc_id in enumerate(ids):
        try:
            total += await kb_ingest.reembed_document(doc_id, client_id)
        except REEMBED_FAILURES as exc:
            # Dying mid-loop with no event row leaves the operator unable to tell how far it
            # got — which matters here, because everything before the failure IS re-embedded.
            await kb_events.log(
                client_id, "reembed", actor=actor, status="error", chunk_count=total,
                detail=f"full re-embed failed after {i} of {len(ids)} documents: {exc}"[:500])
            raise KBConsoleError(
                502, f"Re-embedding failed after {i} of {len(ids)} documents: {exc}") from exc
    await kb_events.log(client_id, "reembed", actor=actor, status="ok", chunk_count=total,
                        detail=f"bulk re-embed of {len(ids)} documents")
    return {"documents": len(ids), "reembedded_chunks": total}


def _job(row) -> dict:
    return {"id": str(row["id"]), "client_id": str(row["client_id"]), "state": row["state"],
            "requested_by": row["requested_by"], "total_documents": row["total_documents"],
            "done_documents": row["done_documents"], "failed_documents": row["failed_documents"],
            "error": row["error"], "heartbeat_at": _iso(row["heartbeat_at"]),
            "created_at": _iso(row["created_at"]), "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["finished_at"])}


_JOB_COLS = ("id, client_id, state, requested_by, total_documents, done_documents, "
             "failed_documents, error, heartbeat_at, created_at, started_at, finished_at")


async def enqueue_reembed(client_id: str, *, requested_by: str = "tenant") -> dict:
    """Queue a full-KB re-embed for `cq-worker`. NEVER runs the work in the request.

    Product decision, not an implementation shortcut: a full re-embed is minutes-to-hours of
    the shared single-CPU encoder, so running it in the request would (a) blow past nginx's
    300 s `proxy_read_timeout`, (b) hold one of ten pool connections for the duration, and
    (c) starve live retrieval for every other tenant on the box. The tenant clicks it, the
    worker drains it at a throttled rate, and `reembed_status()` feeds the progress UI.

    Double-click safety is the DATABASE's job, not the UI's: a partial unique index over
    (client_id) WHERE state IN ('queued','running') means the second insert conflicts and
    returns no row, which surfaces here as 409.
    """
    async with pool().acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM kb_documents WHERE client_id=$1 AND status='ready'", client_id)
        row = await conn.fetchrow(
            f"""
            INSERT INTO kb_reembed_jobs (client_id, state, requested_by, total_documents)
            VALUES ($1, 'queued', $2, $3)
            ON CONFLICT DO NOTHING
            RETURNING {_JOB_COLS}
            """, client_id, requested_by, int(total or 0))
    if row is None:
        raise KBConsoleError(409, "A full re-embed is already queued or running for this tenant.")
    await kb_events.log(client_id, "reembed", actor=requested_by, status="pending",
                        chunk_count=int(total or 0),
                        detail=f"full re-embed queued for {int(total or 0)} documents")
    return {"job": _job(row)}


async def reembed_status(client_id: str) -> dict:
    """The tenant's active job, or the most recent one if none is active (progress UI)."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {_JOB_COLS} FROM kb_reembed_jobs WHERE client_id=$1
            ORDER BY (state IN ('queued','running')) DESC, created_at DESC LIMIT 1
            """, client_id)
    return {"job": _job(row) if row else None}


# --------------------------------------------------------------------------- #
# Bulk actions
# --------------------------------------------------------------------------- #
async def bulk(client_id: str, action: str, ids: list[str], *, value=None,
               actor: str = "tenant") -> dict:
    """delete | reembed | retag (value=tags) | publish | unpublish, over selected documents."""
    ids = [i for i in (ids or []) if i]
    if not ids:
        raise KBConsoleError(400, "No documents selected")
    if action not in BULK_ACTIONS:
        raise KBConsoleError(400, "Unknown bulk action")

    affected = 0
    if action == "delete":
        async with pool().acquire() as conn:
            affected = int((await conn.execute(
                "DELETE FROM kb_documents WHERE client_id=$1 AND id = ANY($2::uuid[])",
                client_id, ids)).split()[-1])
    elif action == "retag":
        async with pool().acquire() as conn:
            affected = int((await conn.execute(
                "UPDATE kb_documents SET tags=$3, updated_at=now() "
                "WHERE client_id=$1 AND id = ANY($2::uuid[])",
                client_id, ids, list(value or []))).split()[-1])
    elif action == "reembed":
        # No connection held: each iteration awaits the encoder.
        #
        # `affected` counts documents that were actually re-embedded, NOT ids submitted:
        # `kb_ingest.reembed_document` is itself client_id-scoped and returns 0 for a document
        # this tenant does not own (and for one with no chunks, e.g. status='error'). Counting
        # the iteration instead would report another tenant's ids back as work done and write a
        # successful `bulk` row into this tenant's audit log for something that never happened.
        for i, doc_id in enumerate(ids):
            try:
                if await kb_ingest.reembed_document(doc_id, client_id):
                    affected += 1
            except REEMBED_FAILURES as exc:
                await kb_events.log(
                    client_id, "bulk", actor=actor, status="error", chunk_count=affected,
                    detail=f"reembed failed after {i} of {len(ids)} documents: {exc}"[:500])
                raise KBConsoleError(
                    502, f"Re-embedding failed after {i} of {len(ids)} documents: {exc}") from exc
    else:  # publish | unpublish
        vis = "public" if action == "publish" else "internal"
        async with pool().acquire() as conn:
            affected = int((await conn.execute(
                "UPDATE kb_documents SET visibility=$3, updated_at=now() "
                "WHERE client_id=$1 AND id = ANY($2::uuid[])", client_id, ids, vis)).split()[-1])
    # Publishing is logged under its own action (not the generic "bulk") so the activity
    # timeline can be filtered down to exactly "what became customer-quotable, and when".
    event_action = action if action in ("publish", "unpublish") else "bulk"
    detail = f"{action} on {len(ids)} documents"
    if action == "reembed":
        # For every other action `len(ids)` and `affected` differ only when rows were missing;
        # for reembed a submitted id can be a silent no-op (not this tenant's, or no chunks), so
        # the audit line says how many documents were actually re-embedded.
        detail += f" ({affected} re-embedded)"
    await kb_events.log(client_id, event_action, actor=actor, status="ok", chunk_count=affected,
                        detail=detail)
    return {"action": action, "affected": affected}


# --------------------------------------------------------------------------- #
# Publishability
# --------------------------------------------------------------------------- #
async def set_visibility(client_id: str, doc_id: str, visibility: str, *,
                         actor: str = "tenant") -> dict:
    """`visibility` is the only thing between an internal pricing floor and a WhatsApp reply:
    the public autopilot retrieves with visibility='public', so an unpublished document is
    unquotable by construction rather than by prompt."""
    vis = check_visibility(visibility)
    async with pool().acquire() as conn:
        title = await conn.fetchval(
            "UPDATE kb_documents SET visibility = $3, updated_at = now() "
            "WHERE id = $1 AND client_id = $2 RETURNING title", doc_id, client_id, vis)
    if title is None:
        raise KBConsoleError(404, "Document not found")
    await kb_events.log(client_id, "publish" if vis == "public" else "unpublish",
                        document_id=doc_id, actor=actor, status="ok", detail=title)
    return {"id": doc_id, "visibility": vis}


async def bulk_visibility(client_id: str, ids: list[str], visibility: str, *,
                          actor: str = "tenant") -> dict:
    vis = check_visibility(visibility)
    ids = [i for i in (ids or []) if i]
    if not ids:
        raise KBConsoleError(400, "No documents selected")
    async with pool().acquire() as conn:
        affected = int((await conn.execute(
            "UPDATE kb_documents SET visibility = $3, updated_at = now() "
            "WHERE client_id = $1 AND id = ANY($2::uuid[])", client_id, ids, vis)).split()[-1])
    await kb_events.log(client_id, "publish" if vis == "public" else "unpublish",
                        actor=actor, status="ok", chunk_count=affected,
                        detail=f"{vis} on {len(ids)} documents")
    return {"visibility": vis, "affected": affected}


async def count_public(client_id: str) -> int:
    async with pool().acquire() as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM kb_documents WHERE client_id = $1 AND visibility = 'public'",
            client_id) or 0)


# --------------------------------------------------------------------------- #
# Duplicates
# --------------------------------------------------------------------------- #
async def duplicates(client_id: str, *, near_threshold: float = 0.95) -> dict:
    """Exact duplicates by checksum, plus near-duplicate chunk pairs by cosine similarity."""
    async with pool().acquire() as conn:
        exact_rows = await conn.fetch(
            """
            SELECT checksum, array_agg(id::text) AS ids, array_agg(title) AS titles, count(*) AS n
            FROM kb_documents WHERE client_id=$1 AND checksum IS NOT NULL
            GROUP BY checksum HAVING count(*) > 1
            """, client_id)
        chunk_count = await conn.fetchval(
            "SELECT count(*) FROM kb_chunks WHERE client_id=$1", client_id)
        near = []
        if chunk_count and chunk_count <= NEAR_DUP_MAX_CHUNKS:
            near_rows = await conn.fetch(
                f"""
                SELECT a.id::text AS a_chunk, b.id::text AS b_chunk,
                       da.title AS a_title, db.title AS b_title,
                       1 - (a.embedding <=> b.embedding) AS sim
                FROM kb_chunks a JOIN kb_chunks b
                  ON a.client_id=b.client_id AND a.id < b.id AND a.document_id <> b.document_id
                JOIN kb_documents da ON da.id=a.document_id
                JOIN kb_documents db ON db.id=b.document_id
                WHERE a.client_id=$1 AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
                  AND 1 - (a.embedding <=> b.embedding) >= $2
                ORDER BY sim DESC LIMIT {NEAR_DUP_LIMIT}
                """, client_id, near_threshold)
            near = [{"a_chunk": r["a_chunk"], "b_chunk": r["b_chunk"], "a_title": r["a_title"],
                     "b_title": r["b_title"], "similarity": round(float(r["sim"]), 4)}
                    for r in near_rows]
    exact = [{"checksum": r["checksum"], "document_ids": r["ids"], "titles": r["titles"],
              "count": r["n"]} for r in exact_rows]
    return {"exact_duplicate_groups": exact, "near_duplicate_pairs": near,
            "near_scan_skipped": bool(chunk_count and chunk_count > NEAR_DUP_MAX_CHUNKS)}


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
async def export(client_id: str, *, fmt: str = "json", actor: str = "tenant") -> dict | str:
    """Whole-KB export. Returns a dict for json and a CSV string for csv; the routers wrap it.

    Logged to kb_events on purpose: this is the one operation that pulls an entire tenant's
    knowledge base out in a single call, and "who exported the KB, and when" is the first
    question asked after anything goes wrong.
    """
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, doc_type, title, tags, status, visibility, chunk_count, char_count,
                   source_type, source_uri, metadata, content_text, created_at, updated_at
            FROM kb_documents WHERE client_id=$1 ORDER BY created_at
            """, client_id)
    await kb_events.log(client_id, "export", actor=actor, status="ok", detail=f"format={fmt}")
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "title", "doc_type", "tags", "status", "visibility", "chunk_count",
                    "source_type", "created_at", "metadata"])
        for r in rows:
            meta = r["metadata"] if isinstance(r["metadata"], str) else json.dumps(r["metadata"])
            w.writerow([str(r["id"]), r["title"], r["doc_type"], ",".join(r["tags"] or []),
                        r["status"], r["visibility"], r["chunk_count"], r["source_type"],
                        r["created_at"].isoformat(), meta])
        return buf.getvalue()
    docs = [_doc(r) for r in rows]
    return {"tenant_id": client_id, "document_count": len(docs), "documents": docs}


# --------------------------------------------------------------------------- #
# Activity log
# --------------------------------------------------------------------------- #
async def activity(client_id: str, *, limit: int = 100, offset: int = 0,
                   action: str | None = None) -> dict:
    return {"events": await kb_events.list_events(client_id, action=action,
                                                  limit=limit, offset=offset)}
