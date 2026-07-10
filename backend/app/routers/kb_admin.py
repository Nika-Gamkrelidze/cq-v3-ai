"""Superadmin KB management — operator command center across all tenants.

Every endpoint is superadmin-gated and tenant-parameterized (/admin/kb/{tenant_id}/...);
all queries are scoped by that tenant's client_id, so operations never cross tenants.
Reuses services/kb_ingest, retrieval, embeddings, settings_store.
"""
import csv
import io
import json
import uuid

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, HTTPException,
                     Query, UploadFile)
from fastapi.responses import Response
from pydantic import BaseModel

from ..db import pool
from ..services import kb_events, kb_ingest, retrieval, settings_store
from ..services.auth import Principal, resolve_principal

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
    async with pool().acquire() as conn:
        d = await conn.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM kb_documents WHERE client_id=$1) AS documents,
              (SELECT count(*) FROM kb_documents WHERE client_id=$1 AND status='error') AS failed,
              (SELECT count(*) FROM kb_documents WHERE client_id=$1 AND status IN ('pending','processing')) AS in_progress,
              (SELECT count(*) FROM kb_chunks WHERE client_id=$1) AS chunks,
              (SELECT count(*) FROM kb_chunks WHERE client_id=$1 AND embedding IS NULL) AS chunks_no_embedding,
              (SELECT count(*) FROM kb_chunks WHERE client_id=$1 AND (content IS NULL OR btrim(content)='')) AS empty_chunks,
              (SELECT coalesce(sum(token_count),0) FROM kb_chunks WHERE client_id=$1) AS approx_tokens,
              (SELECT coalesce(sum(char_count),0) FROM kb_documents WHERE client_id=$1) AS approx_chars,
              (SELECT max(updated_at) FROM kb_documents WHERE client_id=$1) AS last_updated
            """, tid)
    r = dict(d)
    chunks = r["chunks"] or 0
    r["embedding_coverage"] = round(100 * (chunks - (r["chunks_no_embedding"] or 0)) / chunks) if chunks else 100
    r["last_updated"] = r["last_updated"].isoformat() if r["last_updated"] else None
    return r


@router.get("/{tenant_id}/params")
async def params(tid: str = Depends(scope)):
    emb = await settings_store.get_embedding_config()
    async with pool().acquire() as conn:
        coltype = await conn.fetchval(
            """
            SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
            JOIN pg_class c ON a.attrelid=c.oid WHERE c.relname='kb_chunks' AND a.attname='embedding'
            """)
        null_emb = await conn.fetchval(
            "SELECT count(*) FROM kb_chunks WHERE client_id=$1 AND embedding IS NULL", tid)
    col_dim = None
    if coltype and "(" in coltype:
        try:
            col_dim = int(coltype.split("(")[1].rstrip(")"))
        except ValueError:
            col_dim = None
    return {
        "embedding": {"provider": emb.get("provider"), "model": emb.get("model"),
                      "dim": int(emb.get("dim")), "base_url": emb.get("base_url"),
                      "column_dim": col_dim, "dim_mismatch": col_dim is not None and col_dim != int(emb.get("dim"))},
        "chunking": {"size": kb_ingest.CHUNK_SIZE, "overlap": kb_ingest.CHUNK_OVERLAP},
        "retrieval": {"default_top_k": retrieval.DEFAULT_TOP_K, "similarity_threshold": retrieval.SIM_THRESHOLD,
                      "distance_metric": "cosine", "index_type": "hnsw (vector_cosine_ops)"},
        "warnings": {"chunks_without_embedding": null_emb},
    }


# ---------------------------------------------------------------------------
# Documents: list / get / edit / delete (#3, #4)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/documents")
async def list_documents(tid: str = Depends(scope), status: str | None = None,
                         doc_type: str | None = None, tag: str | None = None,
                         q: str | None = None, limit: int = 50, offset: int = 0):
    where = ["client_id = $1"]
    args = [tid]
    if status:
        args.append(status); where.append(f"status = ${len(args)}")
    if doc_type:
        args.append(doc_type); where.append(f"doc_type = ${len(args)}")
    if tag:
        args.append(tag); where.append(f"${len(args)} = ANY(tags)")
    if q:
        args.append(f"%{q}%"); where.append(f"(title ILIKE ${len(args)} OR content_text ILIKE ${len(args)})")
    where_sql = " AND ".join(where)
    async with pool().acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM kb_documents WHERE {where_sql}", *args)
        args2 = args + [min(max(limit, 1), 200), max(offset, 0)]
        rows = await conn.fetch(
            f"""
            SELECT id, doc_type, title, status, tags, chunk_count, char_count, source_type,
                   source_uri, actor, ingest_ms, error, created_at, updated_at
            FROM kb_documents WHERE {where_sql}
            ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}
            """, *args2)
    docs = [{**dict(r), "id": str(r["id"]), "created_at": r["created_at"].isoformat(),
             "updated_at": r["updated_at"].isoformat()} for r in rows]
    return {"total": total, "limit": limit, "offset": offset, "documents": docs}


@router.get("/{tenant_id}/documents/{doc_id}")
async def get_document(doc_id: str, tid: str = Depends(scope)):
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM kb_documents WHERE id=$1 AND client_id=$2", doc_id, tid)
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


class DocEdit(BaseModel):
    title: str | None = None
    doc_type: str | None = None
    tags: list[str] | None = None
    metadata: dict | None = None
    text: str | None = None   # if provided -> re-chunk + re-embed


@router.put("/{tenant_id}/documents/{doc_id}")
async def edit_document(doc_id: str, body: DocEdit, tid: str = Depends(scope)):
    async with pool().acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM kb_documents WHERE id=$1 AND client_id=$2", doc_id, tid)
        if not exists:
            raise HTTPException(status_code=404, detail="Document not found")
        sets, vals = [], []
        for field in ("title", "doc_type"):
            v = getattr(body, field)
            if v is not None:
                vals.append(v); sets.append(f"{field} = ${len(vals)+2}")
        if body.tags is not None:
            vals.append(list(body.tags)); sets.append(f"tags = ${len(vals)+2}")
        if body.metadata is not None:
            vals.append(json.dumps(body.metadata)); sets.append(f"metadata = ${len(vals)+2}::jsonb")
        if sets:
            await conn.execute(
                f"UPDATE kb_documents SET {', '.join(sets)}, updated_at=now() WHERE id=$1 AND client_id=$2",
                doc_id, tid, *vals)

    reembedded = None
    if body.text is not None:
        # Re-ingest the edited content synchronously so retrieval reflects it immediately.
        await kb_ingest.ingest_document(doc_id, tid, "edit", text=body.text)
        async with pool().acquire() as conn:
            reembedded = await conn.fetchval("SELECT chunk_count FROM kb_documents WHERE id=$1", doc_id)

    await kb_events.log(tid, "edit", document_id=doc_id, actor=ACTOR, status="ok",
                        chunk_count=reembedded,
                        detail="content re-embedded" if body.text is not None else "metadata updated")
    return {"updated": True, "reembedded_chunks": reembedded}


@router.delete("/{tenant_id}/documents/{doc_id}")
async def delete_document(doc_id: str, tid: str = Depends(scope)):
    async with pool().acquire() as conn:
        res = await conn.execute(
            "DELETE FROM kb_documents WHERE id=$1 AND client_id=$2", doc_id, tid)
    if res.endswith("0"):
        raise HTTPException(status_code=404, detail="Document not found")
    await kb_events.log(tid, "delete", document_id=doc_id, actor=ACTOR, status="ok")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Chunk-level edit / delete (#4)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str, tid: str = Depends(scope)):
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, chunk_index, content, metadata, (embedding IS NOT NULL) AS has_embedding, token_count
            FROM kb_chunks WHERE document_id=$1 AND client_id=$2 ORDER BY chunk_index
            """, doc_id, tid)
    return [{"id": str(r["id"]), "chunk_index": r["chunk_index"], "content": r["content"],
             "metadata": json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"],
             "has_embedding": r["has_embedding"], "token_count": r["token_count"]} for r in rows]


class ChunkEdit(BaseModel):
    content: str


@router.put("/{tenant_id}/chunks/{chunk_id}")
async def edit_chunk(chunk_id: str, body: ChunkEdit, tid: str = Depends(scope)):
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="content is required")
    try:
        await kb_ingest.reembed_chunk(chunk_id, tid, new_content=body.content)
    except kb_ingest.IngestError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await kb_events.log(tid, "chunk_edit", actor=ACTOR, status="ok", detail=f"chunk {chunk_id[:8]} re-embedded")
    return {"updated": True}


@router.delete("/{tenant_id}/chunks/{chunk_id}")
async def delete_chunk(chunk_id: str, tid: str = Depends(scope)):
    async with pool().acquire() as conn:
        doc_id = await conn.fetchval(
            "DELETE FROM kb_chunks WHERE id=$1 AND client_id=$2 RETURNING document_id", chunk_id, tid)
        if doc_id is None:
            raise HTTPException(status_code=404, detail="Chunk not found")
        await conn.execute(
            "UPDATE kb_documents SET chunk_count=(SELECT count(*) FROM kb_chunks WHERE document_id=$1), "
            "updated_at=now() WHERE id=$1", doc_id)
    await kb_events.log(tid, "chunk_delete", document_id=str(doc_id), actor=ACTOR, status="ok")
    return {"deleted": True}


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
                 tags: str = Form(""), metadata: str = Form("")):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")
    meta = _parse_json(metadata, {})
    try:
        text = kb_ingest.extract_text(file.filename, file.content_type, data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read file: {exc}")
    doc_id = await _new_doc(tid, doc_type, title or file.filename, "file", file.filename, meta, _tags(tags))
    ev = await kb_events.log(tid, "import", document_id=doc_id, method="file", status="pending",
                             actor=ACTOR, detail=title or file.filename)
    bg.add_task(kb_ingest.ingest_document, doc_id, tid, "file", text=text, base_metadata=meta, event_id=ev)
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
# ---------------------------------------------------------------------------
class SearchBody(BaseModel):
    query: str
    top_k: int = 8
    threshold: float = 0.0


@router.post("/{tenant_id}/search")
async def search(body: SearchBody, tid: str = Depends(scope)):
    return await retrieval.search_debug(tid, body.query, body.top_k, body.threshold)


@router.post("/{tenant_id}/playground")
async def playground(body: SearchBody, tid: str = Depends(scope)):
    return await retrieval.search_debug(tid, body.query, body.top_k, body.threshold)


# ---------------------------------------------------------------------------
# Re-embed (#8) — per-doc and bulk
# ---------------------------------------------------------------------------
@router.post("/{tenant_id}/documents/{doc_id}/reembed")
async def reembed_one(doc_id: str, tid: str = Depends(scope)):
    async with pool().acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM kb_documents WHERE id=$1 AND client_id=$2", doc_id, tid):
            raise HTTPException(status_code=404, detail="Document not found")
    n = await kb_ingest.reembed_document(doc_id, tid)
    await kb_events.log(tid, "reembed", document_id=doc_id, actor=ACTOR, status="ok", chunk_count=n)
    return {"reembedded_chunks": n}


@router.post("/{tenant_id}/reembed")
async def reembed_all(tid: str = Depends(scope)):
    async with pool().acquire() as conn:
        ids = [str(r["id"]) for r in await conn.fetch(
            "SELECT id FROM kb_documents WHERE client_id=$1 AND status='ready'", tid)]
    total = 0
    for doc_id in ids:
        total += await kb_ingest.reembed_document(doc_id, tid)
    await kb_events.log(tid, "reembed", actor=ACTOR, status="ok", chunk_count=total,
                        detail=f"bulk re-embed of {len(ids)} documents")
    return {"documents": len(ids), "reembedded_chunks": total}


# ---------------------------------------------------------------------------
# Bulk actions (#11)
# ---------------------------------------------------------------------------
class BulkBody(BaseModel):
    action: str                 # delete | reembed | retag
    document_ids: list[str]
    tags: list[str] | None = None


@router.post("/{tenant_id}/bulk")
async def bulk(body: BulkBody, tid: str = Depends(scope)):
    ids = [i for i in (body.document_ids or []) if i]
    if not ids:
        raise HTTPException(status_code=400, detail="No documents selected")
    affected = 0
    if body.action == "delete":
        async with pool().acquire() as conn:
            affected = int((await conn.execute(
                "DELETE FROM kb_documents WHERE client_id=$1 AND id = ANY($2::uuid[])", tid, ids)).split()[-1])
    elif body.action == "retag":
        async with pool().acquire() as conn:
            affected = int((await conn.execute(
                "UPDATE kb_documents SET tags=$3, updated_at=now() WHERE client_id=$1 AND id = ANY($2::uuid[])",
                tid, ids, list(body.tags or []))).split()[-1])
    elif body.action == "reembed":
        for doc_id in ids:
            await kb_ingest.reembed_document(doc_id, tid)
            affected += 1
    else:
        raise HTTPException(status_code=400, detail="Unknown bulk action")
    await kb_events.log(tid, "bulk", actor=ACTOR, status="ok", chunk_count=affected,
                        detail=f"{body.action} on {len(ids)} documents")
    return {"action": body.action, "affected": affected}


# ---------------------------------------------------------------------------
# Duplicates (#10)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/duplicates")
async def duplicates(tid: str = Depends(scope), near_threshold: float = 0.95):
    async with pool().acquire() as conn:
        exact_rows = await conn.fetch(
            """
            SELECT checksum, array_agg(id::text) AS ids, array_agg(title) AS titles, count(*) AS n
            FROM kb_documents WHERE client_id=$1 AND checksum IS NOT NULL
            GROUP BY checksum HAVING count(*) > 1
            """, tid)
        chunk_count = await conn.fetchval("SELECT count(*) FROM kb_chunks WHERE client_id=$1", tid)
        near = []
        if chunk_count and chunk_count <= 4000:
            near_rows = await conn.fetch(
                """
                SELECT a.id::text AS a_chunk, b.id::text AS b_chunk,
                       da.title AS a_title, db.title AS b_title,
                       1 - (a.embedding <=> b.embedding) AS sim
                FROM kb_chunks a JOIN kb_chunks b
                  ON a.client_id=b.client_id AND a.id < b.id AND a.document_id <> b.document_id
                JOIN kb_documents da ON da.id=a.document_id
                JOIN kb_documents db ON db.id=b.document_id
                WHERE a.client_id=$1 AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
                  AND 1 - (a.embedding <=> b.embedding) >= $2
                ORDER BY sim DESC LIMIT 50
                """, tid, near_threshold)
            near = [{"a_chunk": r["a_chunk"], "b_chunk": r["b_chunk"], "a_title": r["a_title"],
                     "b_title": r["b_title"], "similarity": round(float(r["sim"]), 4)} for r in near_rows]
    exact = [{"checksum": r["checksum"], "document_ids": r["ids"], "titles": r["titles"], "count": r["n"]}
             for r in exact_rows]
    return {"exact_duplicate_groups": exact, "near_duplicate_pairs": near,
            "near_scan_skipped": bool(chunk_count and chunk_count > 4000)}


# ---------------------------------------------------------------------------
# Export (#12)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/export")
async def export(tid: str = Depends(scope), format: str = Query("json")):
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, doc_type, title, tags, status, chunk_count, char_count, source_type,
                   source_uri, metadata, content_text, created_at, updated_at
            FROM kb_documents WHERE client_id=$1 ORDER BY created_at
            """, tid)
    await kb_events.log(tid, "export", actor=ACTOR, status="ok", detail=f"format={format}")
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "title", "doc_type", "tags", "status", "chunk_count", "source_type",
                    "created_at", "metadata"])
        for r in rows:
            meta = r["metadata"] if isinstance(r["metadata"], str) else json.dumps(r["metadata"])
            w.writerow([str(r["id"]), r["title"], r["doc_type"], ",".join(r["tags"] or []), r["status"],
                        r["chunk_count"], r["source_type"], r["created_at"].isoformat(), meta])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="kb-{tid[:8]}.csv"'})
    docs = []
    for r in rows:
        d = dict(r)
        d["id"] = str(r["id"])
        for k in ("created_at", "updated_at"):
            d[k] = d[k].isoformat() if d.get(k) else None
        if isinstance(d.get("metadata"), str):
            d["metadata"] = json.loads(d["metadata"])
        docs.append(d)
    payload = json.dumps({"tenant_id": tid, "document_count": len(docs), "documents": docs}, ensure_ascii=False)
    return Response(payload, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="kb-{tid[:8]}.json"'})


# ---------------------------------------------------------------------------
# Activity: import history + audit (#5, #13)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/activity")
async def activity(tid: str = Depends(scope), action: str | None = None,
                   limit: int = 100, offset: int = 0):
    return {"events": await kb_events.list_events(tid, action=action, limit=limit, offset=offset)}
