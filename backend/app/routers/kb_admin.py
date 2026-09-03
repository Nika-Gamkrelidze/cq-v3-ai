"""Superadmin KB management — operator command center across all tenants.

Every endpoint is superadmin-gated and tenant-parameterized (/admin/kb/{tenant_id}/...);
all queries are scoped by that tenant's client_id, so operations never cross tenants.

The operations themselves now live in `services/kb_console.py`, which the tenant-facing
`routers/kb.py` calls with the SAME functions. These handlers are deliberately thin: resolve
the tenant, call the shared function with `actor="superadmin"`, translate `KBConsoleError` to
`HTTPException`. WHY: the tenant KB console is a second, complete consumer of these twelve
operations, and two hand-maintained copies would drift — a drift in a tenant-scoped query is a
tenant-isolation bug, not a cosmetic one.

Paths, request bodies and response shapes are unchanged: `frontend/public/kb-admin.html` is a
live consumer of every route below. Ingestion (upload/text/csv) stays here rather than in the
shared service because it is background-task shaped and the two surfaces already had it.
"""
import asyncio
import json
import uuid

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, HTTPException,
                     Query, UploadFile)
from fastapi.responses import Response
from pydantic import BaseModel

from ..db import pool
from ..services import kb_console, kb_events, kb_ingest, kb_restructure
from ..services.auth import Principal, resolve_principal
from .kb import count_public_documents

router = APIRouter(prefix="/admin/kb", tags=["kb-admin"])

MAX_BYTES = 25 * 1024 * 1024
ACTOR = "superadmin"


async def scope(tenant_id: str, principal: Principal = Depends(resolve_principal)) -> str:
    """Superadmin gate + tenant existence check. Returns the client_id to scope by."""
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


async def _call(coro):
    """Await a kb_console operation, translating its service-layer error to HTTP.

    The service never raises HTTPException (it is also called from cq-worker), so the
    translation has to happen exactly once, here.
    """
    try:
        return await coro
    except kb_console.KBConsoleError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _parse_json(field, default):
    if not field:
        return default
    try:
        return json.loads(field)
    except (json.JSONDecodeError, TypeError):
        return default


def _tags(field):
    v = _parse_json(field, None)
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [t.strip() for t in (field or "").split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Stats / health (#9) and parameters (#6)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/stats")
async def stats(tid: str = Depends(scope)):
    return await _call(kb_console.stats(tid))


@router.get("/{tenant_id}/params")
async def params(tid: str = Depends(scope)):
    return await _call(kb_console.params(tid))


# ---------------------------------------------------------------------------
# Documents: list / get / edit / delete (#3, #4)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/documents")
async def list_documents(tid: str = Depends(scope), status: str | None = None,
                         doc_type: str | None = None, tag: str | None = None,
                         visibility: str | None = None,
                         q: str | None = None, limit: int = 50, offset: int = 0):
    return await _call(kb_console.list_documents(
        tid, q=q, doc_type=doc_type, status=status, visibility=visibility,
        tag=tag, limit=limit, offset=offset))


@router.get("/{tenant_id}/documents/{doc_id}")
async def get_document(doc_id: str, tid: str = Depends(scope)):
    return await _call(kb_console.get_document(tid, doc_id))


class DocEdit(BaseModel):
    title: str | None = None
    doc_type: str | None = None
    tags: list[str] | None = None
    metadata: dict | None = None
    text: str | None = None   # if provided -> re-chunk + re-embed


@router.put("/{tenant_id}/documents/{doc_id}")
async def edit_document(doc_id: str, body: DocEdit, tid: str = Depends(scope)):
    return await _call(kb_console.update_document(
        tid, doc_id, title=body.title, doc_type=body.doc_type, tags=body.tags,
        text=body.text, metadata=body.metadata, actor=ACTOR))


@router.delete("/{tenant_id}/documents/{doc_id}")
async def delete_document(doc_id: str, tid: str = Depends(scope)):
    return await _call(kb_console.delete_document(tid, doc_id, actor=ACTOR))


# ---------------------------------------------------------------------------
# Publishability — the operator-side twin of routers/kb.py's tenant endpoints.
#
# `visibility` is the only thing standing between an internal pricing floor and a
# WhatsApp customer: the public autopilot retrieves with visibility='public', so a
# document nobody published is unquotable by construction rather than by prompt.
# Superadmins get the same two operations tenants have — and the same kb_events
# rows, so the activity timeline answers "who made this quotable, and when".
# ---------------------------------------------------------------------------
class VisibilityBody(BaseModel):
    visibility: str


@router.get("/{tenant_id}/public-count")
async def public_count(tid: str = Depends(scope)):
    """How many documents this tenant has published — what gates autopilot enablement."""
    return {"public_documents": await count_public_documents(tid)}


@router.put("/{tenant_id}/documents/{doc_id}/visibility")
async def set_visibility(doc_id: str, body: VisibilityBody, tid: str = Depends(scope)):
    return await _call(kb_console.set_visibility(tid, doc_id, body.visibility, actor=ACTOR))


# ---------------------------------------------------------------------------
# Chunk-level edit / delete (#4)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str, tid: str = Depends(scope)):
    # limit=None: this view has always returned every chunk of the document, unpaginated.
    result = await _call(kb_console.list_chunks(tid, doc_id, limit=None))
    return result["chunks"]


class ChunkEdit(BaseModel):
    content: str


@router.put("/{tenant_id}/chunks/{chunk_id}")
async def edit_chunk(chunk_id: str, body: ChunkEdit, tid: str = Depends(scope)):
    return await _call(kb_console.update_chunk(tid, chunk_id, content=body.content, actor=ACTOR))


@router.delete("/{tenant_id}/chunks/{chunk_id}")
async def delete_chunk(chunk_id: str, tid: str = Depends(scope)):
    return await _call(kb_console.delete_chunk(tid, chunk_id, actor=ACTOR))


# ---------------------------------------------------------------------------
# Import (#2) — file / text / csv, with event logging
# ---------------------------------------------------------------------------
async def _new_doc(tid, doc_type, title, source_type, source_uri, metadata, tags):
    async with pool().acquire() as conn:
        return str(await conn.fetchval(
            """
            INSERT INTO kb_documents
                (client_id, doc_type, title, source_uri, status, metadata, tags, source_type, actor)
            VALUES ($1,$2,$3,$4,'pending',$5::jsonb,$6,$7,$8) RETURNING id
            """, tid, doc_type or source_type, title, source_uri,
            json.dumps(metadata or {}), list(tags or []), source_type, ACTOR))


@router.post("/{tenant_id}/documents/upload")
async def upload(bg: BackgroundTasks, tid: str = Depends(scope), file: UploadFile = File(...),
                 doc_type: str = Form("document"), title: str = Form(""),
                 tags: str = Form(""), metadata: str = Form(""), restructure: str = Form("")):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")
    meta = _parse_json(metadata, {})
    try:
        # pypdf/python-docx parsing is synchronous and CPU-bound; running it inline here
        # blocked the single event loop for the whole parse, stalling every other request
        # (same fix as routers/kb.py — an operator upload is no cheaper than a tenant one).
        text = await asyncio.to_thread(
            kb_ingest.extract_text, file.filename, file.content_type, data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read file: {exc}")
    want_ai = restructure.strip().lower() in ("1", "true", "yes", "on")
    # Same upload-time rejections as the tenant route: knowable-now failures are not
    # deferred to a background error the operator has to hunt for.
    if not text.strip():
        raise HTTPException(status_code=422, detail=(
            "No readable text found in the file. If this is a scanned document, run OCR "
            "or export a text-based version, then import again."))
    if want_ai and len(text) > kb_restructure.MAX_INPUT_CHARS:
        raise HTTPException(status_code=422, detail=kb_restructure.OVERSIZE_MESSAGE)
    doc_id = await _new_doc(tid, doc_type, title or file.filename, "file", file.filename, meta, _tags(tags))
    ev = await kb_events.log(tid, "import", document_id=doc_id, method="file", status="pending",
                             actor=ACTOR,
                             detail=(title or file.filename) + (" [AI]" if want_ai else ""))
    bg.add_task(kb_ingest.ingest_document, doc_id, tid, "file", text=text, base_metadata=meta,
                event_id=ev, restructure=want_ai)
    return {"id": doc_id, "status": "pending"}


class TextImport(BaseModel):
    title: str = ""
    doc_type: str = "note"
    text: str
    tags: list[str] = []
    metadata: dict = {}


@router.post("/{tenant_id}/documents/text")
async def import_text(body: TextImport, bg: BackgroundTasks, tid: str = Depends(scope)):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    doc_id = await _new_doc(tid, body.doc_type, body.title or "Pasted text", "paste", None,
                            body.metadata, body.tags)
    ev = await kb_events.log(tid, "import", document_id=doc_id, method="paste", status="pending",
                             actor=ACTOR, detail=body.title or "Pasted text")
    bg.add_task(kb_ingest.ingest_document, doc_id, tid, "paste", text=body.text,
                base_metadata=body.metadata, event_id=ev)
    return {"id": doc_id, "status": "pending"}


@router.post("/{tenant_id}/documents/csv")
async def import_csv(bg: BackgroundTasks, tid: str = Depends(scope), file: UploadFile = File(...),
                     doc_type: str = Form("faq"), title: str = Form(""),
                     tags: str = Form(""), metadata: str = Form("")):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")
    meta = _parse_json(metadata, {})
    doc_id = await _new_doc(tid, doc_type, title or file.filename, "csv", file.filename, meta, _tags(tags))
    ev = await kb_events.log(tid, "import", document_id=doc_id, method="csv", status="pending",
                             actor=ACTOR, detail=title or file.filename)
    bg.add_task(kb_ingest.ingest_document, doc_id, tid, "csv", csv_bytes=data,
                base_metadata=meta, event_id=ev)
    return {"id": doc_id, "status": "pending"}


# ---------------------------------------------------------------------------
# Search (#3) / Playground (#7)
#
# Both return `{method, results, kb_present, confidence}`. `confidence` comes straight from
# `kb_console.playground`, which is the whole reason these two handlers share one
# implementation. It is additive: `kb-admin.html` reads `method` and `results` and keeps
# working untouched.
#
# Note what "the same" does and does not mean. This console deliberately shows MORE rows than
# the tenant's `/kb/search` (`threshold` defaults to 0.0 — the raw ranking, unfiltered), so
# the two lists differ by design. The *verdict* must not: it is measured over a bounded window
# of the ranking, not over the returned list, so an operator reading a ticket sees the same
# diagnosis as the tenant who filed it. See `retrieval.FLAT_WINDOW`.
#
# It is also the answer to the operator question these two routes exist for. A raw BGE-M3
# cosine score is not calibrated in absolute terms — unrelated same-language text still scores
# ~0.30–0.45 — so a tenant reporting "search returns everything" is looking at a flat band the
# UI presented as matches. `confidence.reason` names that case (`flat_distribution`) instead of
# leaving an operator to eyeball four numbers and guess.
# ---------------------------------------------------------------------------
class SearchBody(BaseModel):
    query: str
    top_k: int = 8
    threshold: float = 0.0


@router.post("/{tenant_id}/search")
async def search(body: SearchBody, tid: str = Depends(scope)):
    return await _call(kb_console.playground(tid, body.query, top_k=body.top_k,
                                             threshold=body.threshold))


@router.post("/{tenant_id}/playground")
async def playground(body: SearchBody, tid: str = Depends(scope)):
    return await _call(kb_console.playground(tid, body.query, top_k=body.top_k,
                                             threshold=body.threshold))


# ---------------------------------------------------------------------------
# Re-embed (#8) — per-doc and bulk
#
# The operator's full-KB re-embed still runs INLINE (unlike the tenant's, which is queued to
# cq-worker): its response shape is consumed by kb-admin.html and an operator triggering the
# most expensive operation on the box is assumed to know it. See db/kb_ops.sql.
# ---------------------------------------------------------------------------
@router.post("/{tenant_id}/documents/{doc_id}/reembed")
async def reembed_one(doc_id: str, tid: str = Depends(scope)):
    return await _call(kb_console.reembed_document(tid, doc_id, actor=ACTOR))


@router.post("/{tenant_id}/reembed")
async def reembed_all(tid: str = Depends(scope)):
    return await _call(kb_console.reembed_all(tid, actor=ACTOR))


# ---------------------------------------------------------------------------
# Bulk actions (#11)
# ---------------------------------------------------------------------------
class BulkBody(BaseModel):
    action: str                 # delete | reembed | retag | publish | unpublish
    document_ids: list[str]
    tags: list[str] | None = None


@router.post("/{tenant_id}/bulk")
async def bulk(body: BulkBody, tid: str = Depends(scope)):
    return await _call(kb_console.bulk(tid, body.action, body.document_ids,
                                       value=body.tags, actor=ACTOR))


# ---------------------------------------------------------------------------
# Duplicates (#10)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/duplicates")
async def duplicates(tid: str = Depends(scope), near_threshold: float = 0.95):
    return await _call(kb_console.duplicates(tid, near_threshold=near_threshold))


# ---------------------------------------------------------------------------
# Export (#12)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/export")
async def export(tid: str = Depends(scope), format: str = Query("json")):
    payload = await _call(kb_console.export(tid, fmt=format, actor=ACTOR))
    if format == "csv":
        return Response(payload, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="kb-{tid[:8]}.csv"'})
    return Response(json.dumps(payload, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="kb-{tid[:8]}.json"'})


# ---------------------------------------------------------------------------
# Activity: import history + audit (#5, #13)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/activity")
async def activity(tid: str = Depends(scope), action: str | None = None,
                   limit: int = 100, offset: int = 0):
    return await _call(kb_console.activity(tid, action=action, limit=limit, offset=offset))
