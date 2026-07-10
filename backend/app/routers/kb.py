"""Knowledge base API (tenant-scoped).

Auth: tenant principal (X-API-Key or a tenant-user bearer token). Ingestion runs in a
background task; documents report status pending -> processing -> ready|error.
"""
import json

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, HTTPException,
                     UploadFile)
from pydantic import BaseModel

from ..db import pool
from ..services import kb_ingest, retrieval
from ..services.auth import Principal, resolve_principal

router = APIRouter(prefix="/kb", tags=["kb"])

MAX_BYTES = 25 * 1024 * 1024


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
        text = kb_ingest.extract_text(file.filename, file.content_type, data)
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
    q = ("SELECT id, doc_type, title, status, tags, chunk_count, char_count, error, "
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


class SearchQuery(BaseModel):
    query: str
    top_k: int = 6


@router.post("/search")
async def search(body: SearchQuery, client_id: str = Depends(require_tenant)):
    hits = await retrieval.retrieve(client_id, body.query, top_k=body.top_k)
    return {"count": len(hits), "results": hits}
