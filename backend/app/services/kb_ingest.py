"""Knowledge-base ingestion: extract text -> chunk -> embed -> store.

Supports unstructured docs (PDF/DOCX/TXT/MD), pasted text, and semi-structured CSV
(each row becomes a chunk with its fields preserved in JSONB metadata). Flexible by
design: doc_type is free-text, and arbitrary metadata/tags ride on each document/chunk.
"""
import csv
import hashlib
import io
import json
import logging
import time

from ..db import pool
from . import embeddings
from .embeddings.base import to_pgvector

log = logging.getLogger("cq")

CHUNK_SIZE = 1000        # characters
CHUNK_OVERLAP = 150


class IngestError(RuntimeError):
    pass


# ---- text extraction -------------------------------------------------------
def extract_text(filename: str, content_type: str, data: bytes) -> str:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or "pdf" in ctype:
        return _pdf_text(data)
    if name.endswith(".docx") or "word" in ctype or "officedocument" in ctype:
        return _docx_text(data)
    # txt / md / anything else -> decode as utf-8
    return data.decode("utf-8", errors="replace")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data: bytes) -> str:
    import docx
    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)


# ---- chunking --------------------------------------------------------------
def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    # Prefer paragraph boundaries, then hard-wrap oversized paragraphs.
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= size:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= size:
                buf = p
            else:
                for i in range(0, len(p), size - overlap):
                    chunks.append(p[i:i + size])
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def csv_to_chunks(data: bytes) -> list[tuple[str, dict]]:
    """Each CSV row -> (readable text, structured metadata).

    Two-column files are treated as key/value (or Q/A); wider files render all columns.
    """
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    out: list[tuple[str, dict]] = []
    for row in rows[1:]:
        fields = {header[i] if i < len(header) else f"col{i}": (row[i].strip() if i < len(row) else "")
                  for i in range(max(len(header), len(row)))}
        content = "\n".join(f"{k}: {v}" for k, v in fields.items() if v)
        if content:
            out.append((content, {"row": fields}))
    return out


# ---- pipeline --------------------------------------------------------------
async def _set_status(doc_id: str, status: str, **extra) -> None:
    sets = ["status = $2", "updated_at = now()"]
    vals = [doc_id, status]
    for i, (k, v) in enumerate(extra.items(), start=3):
        sets.append(f"{k} = ${i}")
        vals.append(v)
    async with pool().acquire() as conn:
        await conn.execute(f"UPDATE kb_documents SET {', '.join(sets)} WHERE id = $1", *vals)


def _checksum(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


async def ingest_document(doc_id: str, client_id: str, source_type: str,
                          *, text: str | None = None, csv_bytes: bytes | None = None,
                          base_metadata: dict | None = None, event_id: str | None = None) -> None:
    """Background task: build chunks, embed them, store. Updates document status and,
    if given, the kb_events row (import history/audit)."""
    from . import kb_events
    base_metadata = base_metadata or {}
    started = time.monotonic()
    try:
        await _set_status(doc_id, "processing")
        if event_id:
            await kb_events.finish(event_id, "processing")
        if csv_bytes is not None:
            pairs = csv_to_chunks(csv_bytes)
            contents = [c for c, _ in pairs]
            metas = [{**base_metadata, **m} for _, m in pairs]
            full_text = "\n\n".join(contents)
        else:
            full_text = text or ""
            contents = chunk_text(full_text)
            metas = [dict(base_metadata) for _ in contents]

        ms = int((time.monotonic() - started) * 1000)
        if not contents:
            await _set_status(doc_id, "ready", chunk_count=0, char_count=len(full_text),
                              content_text=full_text[:200000], checksum=_checksum(full_text),
                              ingest_ms=ms, error=None)
            if event_id:
                await kb_events.finish(event_id, "ready", chunk_count=0, duration_ms=ms)
            return

        vectors = await embeddings.embed_texts(contents)
        if len(vectors) != len(contents):
            raise IngestError(f"embedding count mismatch ({len(vectors)} != {len(contents)})")

        async with pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM kb_chunks WHERE document_id = $1", doc_id)
                for i, (content, meta, vec) in enumerate(zip(contents, metas, vectors)):
                    await conn.execute(
                        """
                        INSERT INTO kb_chunks
                            (document_id, client_id, content, metadata, embedding, chunk_index, token_count)
                        VALUES ($1, $2, $3, $4::jsonb, $5::vector, $6, $7)
                        """,
                        doc_id, client_id, content, json.dumps(meta),
                        to_pgvector(vec), i, len(content) // 4,
                    )
        ms = int((time.monotonic() - started) * 1000)
        await _set_status(doc_id, "ready", chunk_count=len(contents), char_count=len(full_text),
                          content_text=full_text[:200000], checksum=_checksum(full_text),
                          ingest_ms=ms, error=None)
        if event_id:
            await kb_events.finish(event_id, "ready", chunk_count=len(contents), duration_ms=ms)
        log.info("kb ingest done: doc=%s chunks=%d", doc_id, len(contents))
    except Exception as exc:  # noqa: BLE001
        log.exception("kb ingest failed: doc=%s", doc_id)
        await _set_status(doc_id, "error", error=str(exc)[:500])
        if event_id:
            await kb_events.finish(event_id, "error", detail=str(exc)[:500])


async def reembed_document(doc_id: str, client_id: str) -> int:
    """Recompute embeddings for every chunk of a document (e.g. after model/dim change).
    Idempotent: reuses existing chunk content, only replaces vectors."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, content FROM kb_chunks WHERE document_id=$1 AND client_id=$2 ORDER BY chunk_index",
            doc_id, client_id)
    if not rows:
        return 0
    vectors = await embeddings.embed_texts([r["content"] for r in rows])
    async with pool().acquire() as conn:
        async with conn.transaction():
            for r, vec in zip(rows, vectors):
                await conn.execute(
                    "UPDATE kb_chunks SET embedding=$2::vector WHERE id=$1", r["id"], to_pgvector(vec))
        await conn.execute(
            "UPDATE kb_documents SET updated_at=now() WHERE id=$1 AND client_id=$2", doc_id, client_id)
    return len(rows)


async def reembed_chunk(chunk_id: str, client_id: str, new_content: str | None = None) -> None:
    """Re-embed a single chunk, optionally after editing its content."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT content, document_id FROM kb_chunks WHERE id=$1 AND client_id=$2", chunk_id, client_id)
        if row is None:
            raise IngestError("chunk not found")
        content = new_content if new_content is not None else row["content"]
    vec = (await embeddings.embed_texts([content]))[0]
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE kb_chunks SET content=$2, embedding=$3::vector, token_count=$4 WHERE id=$1",
            chunk_id, content, to_pgvector(vec), len(content) // 4)
        await conn.execute(
            "UPDATE kb_documents SET updated_at=now() WHERE id=$1", row["document_id"])
