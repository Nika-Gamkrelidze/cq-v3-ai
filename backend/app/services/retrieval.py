"""Tenant-scoped KB retrieval for RAG.

Cosine similarity over pgvector with a threshold; if nothing clears it (common for
lower-resource languages like Georgian), fall back to a trigram keyword match so the
analysis still gets relevant context. Never raises to the caller — returns [] on failure.
"""
import logging

from ..db import pool
from . import embeddings
from .embeddings.base import to_pgvector

log = logging.getLogger("cq")

DEFAULT_TOP_K = 6
SIM_THRESHOLD = 0.35     # cosine similarity floor for vector hits


async def retrieve(client_id: str, query: str, top_k: int = DEFAULT_TOP_K,
                   threshold: float = SIM_THRESHOLD) -> list[dict]:
    if not client_id or not (query or "").strip():
        return []
    try:
        vecs = await embeddings.embed_texts([query[:4000]])
    except Exception as exc:  # noqa: BLE001
        log.warning("retrieval embed failed: %s", exc)
        return await _keyword(client_id, query, top_k)

    qv = to_pgvector(vecs[0])
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.content, c.metadata, d.title, d.doc_type,
                   1 - (c.embedding <=> $2::vector) AS score
            FROM kb_chunks c
            JOIN kb_documents d ON d.id = c.document_id
            WHERE c.client_id = $1 AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> $2::vector
            LIMIT $3
            """,
            client_id, qv, top_k,
        )
    hits = [dict(r) for r in rows if r["score"] is not None and r["score"] >= threshold]
    if hits:
        return hits
    # graceful degradation — keyword fallback
    return await _keyword(client_id, query, top_k)


async def _keyword(client_id: str, query: str, top_k: int) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.content, c.metadata, d.title, d.doc_type,
                   similarity(c.content, $2) AS score
            FROM kb_chunks c
            JOIN kb_documents d ON d.id = c.document_id
            WHERE c.client_id = $1 AND c.content % $2
            ORDER BY similarity(c.content, $2) DESC
            LIMIT $3
            """,
            client_id, query[:4000], top_k,
        )
    return [dict(r) for r in rows]


async def search_debug(client_id: str, query: str, top_k: int = 8,
                        threshold: float = 0.0) -> dict:
    """Retrieval playground: return the exact chunks retrieved with scores + ids + source,
    plus which method (vector or keyword) produced them. Tenant-scoped by client_id."""
    top_k = max(1, min(int(top_k), 50))
    if not client_id or not (query or "").strip():
        return {"method": "none", "results": []}
    try:
        vecs = await embeddings.embed_texts([query[:4000]])
        qv = to_pgvector(vecs[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("playground embed failed: %s", exc)
        return {"method": "keyword", "results": await _keyword_rich(client_id, query, top_k, threshold)}
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id AS chunk_id, c.document_id, c.chunk_index, c.content,
                   d.title, d.doc_type, 1 - (c.embedding <=> $2::vector) AS score
            FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id
            WHERE c.client_id = $1 AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> $2::vector LIMIT $3
            """, client_id, qv, top_k)
    results = [{**dict(r), "chunk_id": str(r["chunk_id"]), "document_id": str(r["document_id"]),
                "score": round(float(r["score"]), 4) if r["score"] is not None else None}
               for r in rows if (r["score"] or 0) >= threshold]
    return {"method": "vector", "results": results}


async def _keyword_rich(client_id: str, query: str, top_k: int, threshold: float) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id AS chunk_id, c.document_id, c.chunk_index, c.content,
                   d.title, d.doc_type, similarity(c.content, $2) AS score
            FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id
            WHERE c.client_id = $1 AND c.content % $2
            ORDER BY similarity(c.content, $2) DESC LIMIT $3
            """, client_id, query[:4000], top_k)
    return [{**dict(r), "chunk_id": str(r["chunk_id"]), "document_id": str(r["document_id"]),
             "score": round(float(r["score"]), 4) if r["score"] is not None else None}
            for r in rows if (r["score"] or 0) >= threshold]


def format_context(hits: list[dict], max_chars: int = 6000) -> str:
    """Render retrieved chunks into a compact context block for the LLM prompt."""
    out, used = [], 0
    for i, h in enumerate(hits, 1):
        title = h.get("title") or h.get("doc_type") or "KB"
        block = f"[{i}] ({title}) {h['content'].strip()}"
        if used + len(block) > max_chars:
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)
