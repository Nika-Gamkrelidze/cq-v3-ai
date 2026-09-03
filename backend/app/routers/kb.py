"""Knowledge base API (tenant-scoped).

Auth: tenant principal (X-API-Key or a tenant-user bearer token). Ingestion runs in a
background task; documents report status pending -> processing -> ready|error.

This module is the tenant's FULL KB console: everything the superadmin console
(`/admin/kb/{tenant_id}/...`) can do, a tenant can now do to its own KB — stats, params,
document edit (re-chunk + re-embed), chunk view/edit/delete, the retrieval playground,
duplicates, activity log, export, bulk actions, per-document and full-KB re-embed, and
publishing. The operations themselves live in `services/kb_console.py` and are shared verbatim
with `routers/kb_admin.py`; nothing below re-implements a tenant-scoped query. The tenant is
taken ONLY from `principal.client_id` — there is deliberately no tenant id anywhere in these
paths, so there is nothing for a caller to tamper with.

!! THIS ROUTER IS MOUNTED TWICE (see main.py): at `/kb` for the browser UI and at `/v1/kb` for
the B2B partner API. Every route added here is therefore ALSO a partner-API route, reachable
with a tenant's own `X-API-Key`. That is legitimate — it is the tenant's key acting on the
tenant's own KB — but these are the ones that are destructive, expensive, or disclosive, and
nobody should discover them by accident:

  * `POST   /v1/kb/bulk` with action=delete  — mass document deletion, no confirmation step.
  * `DELETE /v1/kb/documents/{id}` · `DELETE /v1/kb/chunks/{id}` — irreversible.
  * `PUT    /v1/kb/documents/{id}` carrying `text` — re-chunks and re-embeds the document.
  * `POST   /v1/kb/documents/{id}/reembed` · `POST /v1/kb/bulk` with action=reembed — inline
    embedder work, bounded per document but unbounded in how often it can be called.
  * `POST   /v1/kb/reembed` — full-KB re-embed. QUEUED to cq-worker, never run in the request
    (see db/kb_ops.sql); one active job per tenant, so a retry loop cannot pile them up.
  * `GET    /v1/kb/export` — pulls the ENTIRE knowledge base out in one call. Audited.
  * `PATCH  /v1/kb/documents/{id}/visibility` · `POST /v1/kb/documents/visibility` · `POST
    /v1/kb/bulk` with action=publish — makes content quotable by the PUBLIC autopilot bot.

They are not blocked, because a tenant's key is the tenant's authority over its own data. They
are audited: every mutation writes a `kb_events` row whose actor is `tenant:<user_id>` for a
login and `tenant:apikey` for a server-to-server key, so the activity log can tell a person
clicking a button apart from a script.
"""
import asyncio
import json

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, HTTPException,
                     Query, UploadFile)
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..db import pool
from ..services import kb_console, kb_ingest, kb_restructure, retrieval
from ..services.auth import Principal, resolve_principal

router = APIRouter(prefix="/kb", tags=["kb"])

MAX_BYTES = 25 * 1024 * 1024

# A document is INTERNAL until a human publishes it (db/chat.sql defaults the column to
# 'internal'). The public autopilot retrieves with visibility='public' only, so this flag —
# not a prompt, not a model judgement — is what keeps an internal pricing floor or an
# escalation script out of a WhatsApp reply. Two values, deliberately: anything richer
# invites "sort of public" and the whole guarantee stops being auditable.
# The vocabulary itself lives in services/kb_console.py so the tenant and operator surfaces
# cannot disagree about what 'public' means.
VISIBILITIES = kb_console.VISIBILITIES


def check_visibility(value: str) -> str:
    """HTTP-flavoured wrapper over the shared validator (kept: other routers import this)."""
    try:
        return kb_console.check_visibility(value)
    except kb_console.KBConsoleError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


async def count_public_documents(client_id: str) -> int:
    """How many documents this tenant has published.

    Lives here (rather than being re-typed per call site) because two very different
    surfaces need the same number: the KB console shows it, and enabling autopilot is
    gated on it being > 0. A tenant with an unpublished KB would refuse every single
    customer question, which reads as a broken product rather than a safe default.
    """
    return await kb_console.count_public(client_id)


def require_tenant(principal: Principal = Depends(resolve_principal)) -> str:
    if not principal.is_tenant:
        raise HTTPException(status_code=403, detail="Knowledge base requires a tenant (API key or login).")
    return principal.client_id


def tenant_actor(principal: Principal = Depends(resolve_principal)) -> str:
    """Who to record in `kb_events` for a tenant self-service action.

    Not an auth gate — `require_tenant` is, and FastAPI resolves `resolve_principal` once per
    request for both. This exists so the shared activity timeline can distinguish a person
    (`tenant:<user_id>`) from a server-to-server key (`tenant:apikey`) from the operator
    (`superadmin`); an audit log where every row says "tenant" answers no useful question.
    """
    return f"tenant:{principal.user_id or principal.role or 'user'}"


async def _call(coro):
    """Await a kb_console operation, translating its service-layer error to HTTP.

    The service never raises HTTPException (it is also called from cq-worker), so the
    translation has to happen exactly once, here.
    """
    try:
        return await coro
    except kb_console.KBConsoleError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _parse_json(field: str | None, default):
    if not field:
        return default
    try:
        return json.loads(field)
    except (json.JSONDecodeError, TypeError):
        return default


async def _create_doc(client_id: str, doc_type: str, title: str, source_type: str,
                      source_uri: str | None, metadata: dict, tags: list) -> str:
    async with pool().acquire() as conn:
        return str(await conn.fetchval(
            """
            INSERT INTO kb_documents
                (client_id, doc_type, title, source_uri, status, metadata, tags)
            VALUES ($1, $2, $3, $4, 'pending', $5::jsonb, $6)
            RETURNING id
            """,
            client_id, doc_type or source_type, title, source_uri,
            json.dumps(metadata or {}), list(tags or []),
        ))


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
@router.post("/documents/upload")
async def upload_document(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: str = Form("document"),
    title: str = Form(""),
    tags: str = Form(""),
    metadata: str = Form(""),
    restructure: str = Form(""),
    client_id: str = Depends(require_tenant),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")
    tag_list = _parse_json(tags, None) or [t.strip() for t in tags.split(",") if t.strip()]
    meta = _parse_json(metadata, {})
    # Uploader said the file does NOT follow the KB templates: Claude restructures the raw
    # extracted text into clean entries during ingestion (background, like everything else).
    want_ai = restructure.strip().lower() in ("1", "true", "yes", "on")
    # A CSV dropped into the generic file box gets the same row-per-chunk treatment as the
    # dedicated CSV path — decoded as one text blob it loses its row/field structure.
    # Unless AI restructuring was requested: a CSV that needs restructuring is by definition
    # NOT template-shaped rows, so it takes the text path below instead.
    if not want_ai and (
            (file.filename or "").lower().endswith(".csv") or "csv" in (file.content_type or "").lower()):
        doc_id = await _create_doc(client_id, doc_type, title or file.filename, "csv",
                                   file.filename, meta, tag_list)
        bg.add_task(kb_ingest.ingest_document, doc_id, client_id, "csv",
                    csv_bytes=data, base_metadata=meta)
        return {"id": doc_id, "status": "pending", "title": title or file.filename}
    try:
        # pypdf/python-docx parsing is synchronous and CPU-bound; running it inline here
        # blocked the single event loop for the whole parse, stalling every other request.
        text = await asyncio.to_thread(
            kb_ingest.extract_text, file.filename, file.content_type, data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read file: {exc}")
    # Failures knowable NOW are rejected NOW — not stored minutes later as a background
    # error the uploader has to go hunting for in the documents list.
    if not text.strip():
        raise HTTPException(status_code=422, detail=(
            "No readable text found in the file. If this is a scanned document, run OCR "
            "or export a text-based version, then import again."))
    if want_ai and len(text) > kb_restructure.MAX_INPUT_CHARS:
        raise HTTPException(status_code=422, detail=kb_restructure.OVERSIZE_MESSAGE)
    doc_id = await _create_doc(client_id, doc_type, title or file.filename, "file",
                               file.filename, meta, tag_list)
    bg.add_task(kb_ingest.ingest_document, doc_id, client_id, "file", text=text,
                base_metadata=meta, restructure=want_ai)
    return {"id": doc_id, "status": "pending", "title": title or file.filename}


class TextDoc(BaseModel):
    title: str = ""
    doc_type: str = "note"
    text: str
    tags: list[str] = []
    metadata: dict = {}


@router.post("/documents/text")
async def paste_document(body: TextDoc, bg: BackgroundTasks,
                         client_id: str = Depends(require_tenant)):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    doc_id = await _create_doc(client_id, body.doc_type, body.title or "Pasted text",
                               "text", None, body.metadata, body.tags)
    bg.add_task(kb_ingest.ingest_document, doc_id, client_id, "text",
                text=body.text, base_metadata=body.metadata)
    return {"id": doc_id, "status": "pending", "title": body.title or "Pasted text"}


@router.post("/documents/csv")
async def csv_document(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: str = Form("faq"),
    title: str = Form(""),
    tags: str = Form(""),
    metadata: str = Form(""),
    client_id: str = Depends(require_tenant),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")
    tag_list = _parse_json(tags, None) or [t.strip() for t in tags.split(",") if t.strip()]
    meta = _parse_json(metadata, {})
    doc_id = await _create_doc(client_id, doc_type, title or file.filename, "csv",
                               file.filename, meta, tag_list)
    bg.add_task(kb_ingest.ingest_document, doc_id, client_id, "csv",
                csv_bytes=data, base_metadata=meta)
    return {"id": doc_id, "status": "pending", "title": title or file.filename}


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
@router.get("/documents")
async def list_documents(doc_type: str | None = None, status: str | None = None,
                         tag: str | None = None, visibility: str | None = None,
                         q: str | None = None, limit: int = 500, offset: int = 0,
                         client_id: str = Depends(require_tenant)):
    """The tenant's document list.

    Returns a bare LIST, not the console's `{total, documents}` envelope: this route predates
    the console and `tenant.html` plus every partner integration reads it positionally. The
    filters and paging are additive — with no query params the result is what it always was,
    only with the console's extra columns (source_type, source_uri, actor, ingest_ms) alongside.
    """
    result = await _call(kb_console.list_documents(
        client_id, q=q, doc_type=doc_type, status=status, visibility=visibility,
        tag=tag, limit=limit, offset=offset))
    return result["documents"]


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, client_id: str = Depends(require_tenant)):
    return await _call(kb_console.get_document(client_id, doc_id))


class DocEdit(BaseModel):
    title: str | None = None
    doc_type: str | None = None
    tags: list[str] | None = None
    metadata: dict | None = None
    text: str | None = None   # if provided -> re-chunk + re-embed


@router.put("/documents/{doc_id}")
async def edit_document(doc_id: str, body: DocEdit,
                        client_id: str = Depends(require_tenant),
                        actor: str = Depends(tenant_actor)):
    """Edit metadata, and/or replace the text (which re-chunks + re-embeds the document).

    A failed re-embed is a 502, not a cheerful `{"updated": true}` — see kb_console.update_document.
    """
    return await _call(kb_console.update_document(
        client_id, doc_id, title=body.title, doc_type=body.doc_type, tags=body.tags,
        text=body.text, metadata=body.metadata, actor=actor))


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, client_id: str = Depends(require_tenant),
                          actor: str = Depends(tenant_actor)):
    return await _call(kb_console.delete_document(client_id, doc_id, actor=actor))


@router.get("/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str, client_id: str = Depends(require_tenant)):
    """Every chunk of one document, in order. Bare list (pre-existing shape), now carrying the
    chunk `id`, `has_embedding` and `token_count` the chunk editor below needs."""
    result = await _call(kb_console.list_chunks(client_id, doc_id, limit=None))
    return result["chunks"]


# --------------------------------------------------------------------------- #
# Chunk-level edit / delete
# --------------------------------------------------------------------------- #
class ChunkEdit(BaseModel):
    content: str


@router.put("/chunks/{chunk_id}")
async def edit_chunk(chunk_id: str, body: ChunkEdit,
                     client_id: str = Depends(require_tenant),
                     actor: str = Depends(tenant_actor)):
    return await _call(kb_console.update_chunk(client_id, chunk_id, content=body.content,
                                               actor=actor))


@router.delete("/chunks/{chunk_id}")
async def delete_chunk(chunk_id: str, client_id: str = Depends(require_tenant),
                       actor: str = Depends(tenant_actor)):
    return await _call(kb_console.delete_chunk(client_id, chunk_id, actor=actor))


# --------------------------------------------------------------------------- #
# Publishability — the control that decides what a public bot may quote.
# Every change writes a kb_events row so the existing activity timeline answers
# "who made this quotable to customers, and when".
# --------------------------------------------------------------------------- #
class VisibilityBody(BaseModel):
    visibility: str


class BulkVisibilityBody(BaseModel):
    document_ids: list[str]
    visibility: str


@router.get("/public-count")
async def public_count(client_id: str = Depends(require_tenant)):
    return {"public_documents": await count_public_documents(client_id)}


@router.put("/documents/{doc_id}/visibility")
async def set_visibility(doc_id: str, body: VisibilityBody,
                         client_id: str = Depends(require_tenant),
                         actor: str = Depends(tenant_actor)):
    return await _call(kb_console.set_visibility(client_id, doc_id, body.visibility, actor=actor))


@router.patch("/documents/{doc_id}/visibility")
async def patch_visibility(doc_id: str, body: VisibilityBody,
                           client_id: str = Depends(require_tenant),
                           actor: str = Depends(tenant_actor)):
    """PATCH alias of the route above. Both verbs exist because the console mirrors the
    operator surface (PUT) while a partial update of one field is a PATCH by any REST reading;
    they are the same operation and neither is deprecated."""
    return await _call(kb_console.set_visibility(client_id, doc_id, body.visibility, actor=actor))


@router.post("/documents/visibility")
async def bulk_set_visibility(body: BulkVisibilityBody,
                              client_id: str = Depends(require_tenant),
                              actor: str = Depends(tenant_actor)):
    return await _call(kb_console.bulk_visibility(client_id, body.document_ids,
                                                  body.visibility, actor=actor))


# --------------------------------------------------------------------------- #
# Search / playground
# --------------------------------------------------------------------------- #
class SearchQuery(BaseModel):
    # `top_k` is bounded HERE, in the contract, rather than clamped inside retrieval where a
    # partner asking for 60 would silently receive 50 and a partner asking for 0 would
    # silently receive 1. A 422 naming the limit is the only version of this a client can
    # notice and correct. The bound is retrieval's own MAX_TOP_K, so the two cannot drift.
    query: str
    top_k: int = Field(6, ge=1, le=retrieval.MAX_TOP_K)


@router.post("/search")
async def search(body: SearchQuery, client_id: str = Depends(require_tenant)):
    """Search this tenant's KB. Also `POST /v1/kb/search` — see the module docstring.

    Backed by `retrieve_ranked()` rather than `retrieve()` so the caller finally gets the
    mechanics along with the passages: `method` (vector or the trigram fallback), `top_score`,
    `kb_present`, and `confidence`. That last one is the point — a BGE-M3 cosine score is not
    calibrated in absolute terms, so this endpoint used to hand back four unrelated documents
    in a 0.037-wide band with nothing to distinguish them from four real answers, and every UI
    on top of it rendered noise as results.

    ADDITIVE ONLY, because this is a partner-API route with external consumers: `count` and
    `results` keep their meaning, and every field a result already carried (`content`,
    `metadata`, `title`, `doc_type`, `score`) is still there. Ranked hits carry MORE —
    `chunk_id`, `document_id`, `chunk_index` — which is what finally lets a result link back
    to the document it came from.

    Additive extends to WHICH PASSAGES COME BACK, which is why both of `retrieve_ranked`'s
    chat-shaped filters are switched off here:

    * `relative_gate=None` — the ranked path drops anything more than 0.08 below the best
      hit. That is right when the hits become an LLM prompt and wrong for a search box: on a
      good result set (0.82 / 0.78 / 0.42 / 0.38) it silently removes half the answers this
      endpoint used to return, while the 0.037-wide noise band that motivated this whole
      change sails straight through it. The absolute `SIM_THRESHOLD` floor, which is what
      `retrieve()` applied, is all that remains.
    * `keyword_min_score=0.0` — the ranked path requires trigram similarity >= 0.45; this
      route never had a keyword floor. Keeping it would mean that during an embedding-service
      outage a search that used to degrade to a trigram match returns nothing at all, and the
      user is told "nothing matched, import a document" when the truth is that the encoder is
      down — which is also precisely when `confidence.reason == "keyword_fallback"` needs to
      reach the UI.

    Still non-raising on a retrieval failure: `retrieve_ranked` degrades to `method: "none"`
    with `kb_present: null` and `confidence.reason: "unavailable"` — "we could not look",
    never "your knowledge base is empty".
    """
    r = await retrieval.retrieve_ranked(client_id, body.query, top_k=body.top_k,
                                        relative_gate=None, keyword_min_score=0.0)
    hits = r.get("hits") or []
    return {"count": len(hits), "results": hits, "method": r.get("method"),
            "top_score": r.get("top_score"), "kb_present": r.get("kb_present"),
            "confidence": r.get("confidence")}


class PlaygroundBody(BaseModel):
    query: str
    top_k: int = 8
    threshold: float = 0.0


@router.post("/playground")
async def playground(body: PlaygroundBody, client_id: str = Depends(require_tenant)):
    """Exactly what retrieval would return, with per-chunk scores, ids and the method used.

    Distinct from `/search` above, which answers "give me context": the playground is for a
    tenant debugging *why* an answer was wrong, so it exposes the raw ranking below the
    threshold as well (`threshold` defaults to 0.0 — nothing is filtered out).

    Carries the same `confidence` block, which matters MORE here than anywhere else: this
    view deliberately shows unfiltered hits, and without it a wall of 0.36 scores looks
    identical to a wall of real matches.
    """
    return await _call(kb_console.playground(client_id, body.query, top_k=body.top_k,
                                             threshold=body.threshold))


# --------------------------------------------------------------------------- #
# Health / parameters
# --------------------------------------------------------------------------- #
@router.get("/stats")
async def stats(client_id: str = Depends(require_tenant)):
    return await _call(kb_console.stats(client_id))


@router.get("/params")
async def params(client_id: str = Depends(require_tenant)):
    """Chunking, embedding and retrieval settings actually in force, plus `dim_mismatch` —
    the alarm that says new embeddings are failing and search has quietly stopped working."""
    return await _call(kb_console.params(client_id))


# --------------------------------------------------------------------------- #
# Re-embedding
#
# Per-document is INLINE (bounded: one document's chunks). The full-KB re-embed is QUEUED to
# cq-worker and never runs in the request — it saturates the single shared CPU-bound TEI
# container that also serves live retrieval for every other tenant. See db/kb_ops.sql.
# --------------------------------------------------------------------------- #
@router.post("/documents/{doc_id}/reembed")
async def reembed_document(doc_id: str, client_id: str = Depends(require_tenant),
                           actor: str = Depends(tenant_actor)):
    return await _call(kb_console.reembed_document(client_id, doc_id, actor=actor))


@router.post("/reembed", status_code=202)
async def reembed_all(client_id: str = Depends(require_tenant),
                      actor: str = Depends(tenant_actor)):
    """Queue a full-KB re-embed. 202 with the job row; 409 if one is already queued or running."""
    return await _call(kb_console.enqueue_reembed(client_id, requested_by=actor))


@router.get("/reembed/status")
async def reembed_status(client_id: str = Depends(require_tenant)):
    """This tenant's active re-embed job, or the most recent one. `{"job": null}` if never run."""
    return await _call(kb_console.reembed_status(client_id))


# --------------------------------------------------------------------------- #
# Bulk actions
# --------------------------------------------------------------------------- #
class BulkBody(BaseModel):
    action: str                 # delete | reembed | retag | publish | unpublish
    document_ids: list[str]
    tags: list[str] | None = None


@router.post("/bulk")
async def bulk(body: BulkBody, client_id: str = Depends(require_tenant),
               actor: str = Depends(tenant_actor)):
    return await _call(kb_console.bulk(client_id, body.action, body.document_ids,
                                       value=body.tags, actor=actor))


# --------------------------------------------------------------------------- #
# Duplicates / export / activity
# --------------------------------------------------------------------------- #
@router.get("/duplicates")
async def duplicates(near_threshold: float = 0.95,
                     client_id: str = Depends(require_tenant)):
    """Exact (checksum) duplicate groups plus near-duplicate chunk pairs.

    The near scan is a self-join over kb_chunks — O(n^2) — so it is skipped above a chunk
    ceiling and says so via `near_scan_skipped` rather than timing out.
    """
    return await _call(kb_console.duplicates(client_id, near_threshold=near_threshold))


@router.get("/export")
async def export(format: str = Query("json"), client_id: str = Depends(require_tenant),
                 actor: str = Depends(tenant_actor)):
    """Whole-KB export as JSON or CSV, as an attachment. Audited (see the module docstring)."""
    payload = await _call(kb_console.export(client_id, fmt=format, actor=actor))
    if format == "csv":
        return Response(payload, media_type="text/csv",
                        headers={"Content-Disposition":
                                 f'attachment; filename="kb-{client_id[:8]}.csv"'})
    return Response(json.dumps(payload, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="kb-{client_id[:8]}.json"'})


@router.get("/activity")
async def activity(action: str | None = None, limit: int = 100, offset: int = 0,
                   client_id: str = Depends(require_tenant)):
    """The tenant's own KB audit trail — imports, edits, deletes, re-embeds, publishes,
    exports — including the rows written by the operator, which is the point."""
    return await _call(kb_console.activity(client_id, action=action, limit=limit, offset=offset))
