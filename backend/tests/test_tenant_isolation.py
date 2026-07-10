"""Proof that KB retrieval (and therefore the fact-check) is strictly tenant-separated:
tenant A's audio is never checked against tenant B's knowledge base.

Runs against the real database (inside the api container). Embeddings are monkeypatched
so the test doesn't need the TEI service and can force the *worst case*: the query vector
is made identical to tenant B's chunk, so B is the single best semantic match — yet
retrieval scoped to tenant A must still never return B's content.
"""
import uuid

import pytest

from app import db
from app.services import embeddings, retrieval
from app.services.embeddings.base import to_pgvector

DIM = 1024
VEC_A = [0.9] + [0.0] * (DIM - 1)     # tenant A's chunk vector
VEC_B = [0.0, 0.9] + [0.0] * (DIM - 2)  # tenant B's chunk vector (also the query vector)
A_MARK = "TENANT_A_ONLY_REFUND_IS_14_DAYS"
B_MARK = "TENANT_B_ONLY_REFUND_IS_90_DAYS"


async def _mk_tenant(conn, marker, vec):
    cid = await conn.fetchval(
        "INSERT INTO clients (slug, name) VALUES ($1, $2) RETURNING id",
        f"iso-{uuid.uuid4().hex[:8]}", f"iso-{marker[:12]}")
    doc = await conn.fetchval(
        "INSERT INTO kb_documents (client_id, doc_type, title, status) "
        "VALUES ($1,'policy','Refund policy','ready') RETURNING id", cid)
    await conn.execute(
        "INSERT INTO kb_chunks (document_id, client_id, content, embedding, chunk_index) "
        "VALUES ($1,$2,$3,$4::vector,0)", doc, cid, marker, to_pgvector(vec))
    return cid


async def test_retrieval_is_tenant_isolated(monkeypatch):
    # Force the query to be tenant B's exact vector — the strongest cross-tenant match.
    async def fake_embed(texts):
        return [VEC_B for _ in texts]
    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)

    await db.connect()
    async with db.pool().acquire() as conn:
        a_id = await _mk_tenant(conn, A_MARK, VEC_A)
        b_id = await _mk_tenant(conn, B_MARK, VEC_B)
    try:
        # Query as tenant A. Even though VEC_B is the perfect match, A must only see A.
        a_hits = await retrieval.retrieve(a_id, "what is the refund window?", top_k=10, threshold=0.0)
        a_contents = " ".join(h["content"] for h in a_hits)
        assert B_MARK not in a_contents, "LEAK: tenant A retrieval returned tenant B's KB"
        assert all(h["content"] == A_MARK for h in a_hits)

        # Query as tenant B returns only B — sanity that data exists and scoping is per-tenant.
        b_hits = await retrieval.retrieve(b_id, "what is the refund window?", top_k=10, threshold=0.0)
        b_contents = " ".join(h["content"] for h in b_hits)
        assert A_MARK not in b_contents, "LEAK: tenant B retrieval returned tenant A's KB"
        assert any(h["content"] == B_MARK for h in b_hits)
    finally:
        async with db.pool().acquire() as conn:
            await conn.execute("DELETE FROM clients WHERE id = ANY($1::uuid[])", [a_id, b_id])
        await db.disconnect()
