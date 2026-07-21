"""Knowledge base API (tenant-scoped).

Auth: tenant principal (X-API-Key or a tenant-user bearer token). Ingestion runs in a
background task; documents report status pending -> processing -> ready|error.
"""
import asyncio
import json

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, HTTPException,
                     UploadFile)
from pydantic import BaseModel

from ..db import pool
from ..services import kb_events, kb_ingest, retrieval
from ..services.auth import Principal, resolve_principal

router = APIRouter(prefix="/kb", tags=["kb"])

MAX_BYTES = 25 * 1024 * 1024

# A document is INTERNAL until a human publishes it (db/chat.sql defaults the column to
# 'internal'). The public autopilot retrieves with visibility='public' only, so this flag —
# not a prompt, not a model judgement — is what keeps an internal pricing floor or an
# escalation script out of a WhatsApp reply. Two values, deliberately: anything richer
# invites "sort of public" and the whole guarantee stops being auditable.
VISIBILITIES = ("internal", "public")


def check_visibility(value: str) -> str:
    v = (value or "").strip().lower()
    if v not in VISIBILITIES:
        raise HTTPException(status_code=400, detail="visibility must be 'internal' or 'public'")
    return v


async def count_public_documents(client_id: str) -> int:
    """How many documents this tenant has published.

    Lives here (rather than being re-typed per call site) because two very different
    surfaces need the same number: the KB console shows it, and enabling autopilot is
    gated on it being > 0. A tenant with an unpublished KB would refuse every single
    customer question, which reads as a broken product rather than a safe default.
    """
    async with pool().acquire() as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM kb_documents WHERE client_id = $1 AND visibility = 'public'",
            client_id) or 0)


def require_tenant(principal: Principal = Depends(resolve_principal)) -> str:
    if not principal.is_tenant:
        raise HTTPException(status_code=403, detail="Knowledge base requires a tenant (API key or login).")
    return principal.client_id


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


@router.post("/documents/upload")
async def upload_document(
    bg: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: str = Form("document"),
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
    try:
        # pypdf/python-docx parsing is synchronous and CPU-bound; running it inline here
        # blocked the single event loop for the whole parse, stalling every other request.
        text = await asyncio.to_thread(
            kb_ingest.extract_text, file.filename, file.content_type, data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read file: {exc}")
    doc_id = await _create_doc(client_id, doc_type, title or file.filename, "file",
                               file.filename, meta, tag_list)
    bg.add_task(kb_ingest.ingest_document, doc_id, client_id, "file", text=text, base_metadata=meta)
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


@router.get("/documents")
async def list_documents(doc_type: str | None = None, client_id: str = Depends(require_tenant)):
    q = ("SELECT id, doc_type, title, status, visibility, tags, chunk_count, char_count, error, "
         "created_at, updated_at FROM kb_documents WHERE client_id = $1")
    args = [client_id]
    if doc_type:
        q += " AND doc_type = $2"
        args.append(doc_type)
    q += " ORDER BY created_at DESC LIMIT 500"
    async with pool().acquire() as conn:
        rows = await conn.fetch(q, *args)
    return [{**dict(r), "id": str(r["id"]),
             "created_at": r["created_at"].isoformat(),
             "updated_at": r["updated_at"].isoformat()} for r in rows]


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, client_id: str = Depends(require_tenant)):
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM kb_documents WHERE id = $1 AND client_id = $2", doc_id, client_id)
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    d = dict(row)
    d["id"] = str(row["id"]); d["client_id"] = str(row["client_id"])
    for k in ("created_at", "updated_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    if isinstance(d.get("metadata"), str):
        d["metadata"] = json.loads(d["metadata"])
    return d


@router.get("/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str, client_id: str = Depends(require_tenant)):
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chunk_index, content, metadata FROM kb_chunks
            WHERE document_id = $1 AND client_id = $2 ORDER BY chunk_index
            """, doc_id, client_id)
    return [{"chunk_index": r["chunk_index"], "content": r["content"],
             "metadata": json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"]}
            for r in rows]


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, client_id: str = Depends(require_tenant)):
    async with pool().acquire() as conn:
        res = await conn.execute(
            "DELETE FROM kb_documents WHERE id = $1 AND client_id = $2", doc_id, client_id)
    if res.endswith("0"):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": True}


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
                         client_id: str = Depends(require_tenant)):
    vis = check_visibility(body.visibility)
    async with pool().acquire() as conn:
        title = await conn.fetchval(
            "UPDATE kb_documents SET visibility = $3, updated_at = now() "
            "WHERE id = $1 AND client_id = $2 RETURNING title", doc_id, client_id, vis)
    if title is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await kb_events.log(client_id, "publish" if vis == "public" else "unpublish",
                        document_id=doc_id, actor="tenant", status="ok", detail=title)
    return {"id": doc_id, "visibility": vis}


@router.post("/documents/visibility")
async def bulk_set_visibility(body: BulkVisibilityBody,
                              client_id: str = Depends(require_tenant)):
    vis = check_visibility(body.visibility)
    ids = [i for i in (body.document_ids or []) if i]
    if not ids:
        raise HTTPException(status_code=400, detail="No documents selected")
    async with pool().acquire() as conn:
        affected = int((await conn.execute(
            "UPDATE kb_documents SET visibility = $3, updated_at = now() "
            "WHERE client_id = $1 AND id = ANY($2::uuid[])", client_id, ids, vis)).split()[-1])
    await kb_events.log(client_id, "publish" if vis == "public" else "unpublish",
                        actor="tenant", status="ok", chunk_count=affected,
                        detail=f"{vis} on {len(ids)} documents")
    return {"visibility": vis, "affected": affected}


class SearchQuery(BaseModel):
    query: str
    top_k: int = 6


@router.post("/search")
async def search(body: SearchQuery, client_id: str = Depends(require_tenant)):
    hits = await retrieval.retrieve(client_id, body.query, top_k=body.top_k)
    return {"count": len(hits), "results": hits}
