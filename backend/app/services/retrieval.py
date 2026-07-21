"""Tenant-scoped KB retrieval for RAG.

Cosine similarity over pgvector with a threshold; if nothing clears it (common for
lower-resource languages like Georgian), fall back to a trigram keyword match so the
analysis still gets relevant context.

Two entry points, deliberately different in what they hand back:

* `retrieve()` — the batch/offline shape used by analyze, factcheck, scoring and
  `POST /kb/search`. It answers "give me some context text", and every caller treats
  an empty list as "no KB help available".
* `retrieve_ranked()` — the shape a *conversation* needs. A chat answer has to cite
  what it used (so: chunk/document ids), and a grounding gate has to decide "I don't
  know" *before* the model is called — which is impossible without knowing whether the
  hits came from the vector index or from the lexical fallback, how strong the best one
  was, and whether the tenant has a KB at all. `retrieve()` threw all of that away.

Neither function raises: retrieval is an enrichment step everywhere it is used, and a
DB or embedding-service blip must degrade the answer, not fail the request. (The old
docstring claimed this while only wrapping the embed call — a DB error inside the
keyword fallback propagated straight out of `POST /kb/search`.)
"""
import logging

from ..db import pool
from . import embeddings
from .embeddings.base import to_pgvector

log = logging.getLogger("cq")

DEFAULT_TOP_K = 6
SIM_THRESHOLD = 0.35     # cosine similarity floor for vector hits

# --- ranked-path tuning knobs -------------------------------------------------
# All three numbers below are educated guesses, not measurements. P4 ("retrieval
# quality for short Georgian questions") is where they get tuned against a real
# labelled eval set; until then they are deliberately conservative, because on the
# public autopilot a weak hit is worse than no hit.
KEYWORD_MIN_SCORE = 0.45   # trigram similarity floor — the old fallback had NO floor at
                           # all, so an off-topic question returned lexically-similar
                           # noise with nothing to distinguish it from a real match.
RELATIVE_GATE = 0.08       # drop hits more than this far below the best one: short
                           # queries produce a flat score curve where rank 6 is unrelated.
RANKED_QUERY_CHARS = 512   # TEI latency is roughly linear in tokens and a chat question is
                           # short; the 4000 on retrieve() is transcript-shaped and stays.
RRF_K = 60                 # reciprocal rank fusion constant (the standard 60).
VISIBILITY_OVERFETCH = 4   # pgvector post-filters HNSW results, so a `visibility` filter
VISIBILITY_OVERFETCH_CAP = 200   # applied after the scan can empty an otherwise-full page.


async def retrieve(client_id: str, query: str, top_k: int = DEFAULT_TOP_K,
                   threshold: float = SIM_THRESHOLD) -> list[dict]:
    """Context chunks for the analysis pipelines. Shape and thresholds unchanged."""
    try:
        return await _retrieve(client_id, query, top_k, threshold)
    except Exception as exc:  # noqa: BLE001 — see module docstring: never raise
        log.warning("retrieval failed (client=%s): %s", client_id, exc)
        return []


async def _retrieve(client_id: str, query: str, top_k: int, threshold: float) -> list[dict]:
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


# --- ranked retrieval (chat / grounding gate) ---------------------------------

def _empty_ranked(kb_present: bool = False) -> dict:
    return {"method": "none", "top_score": None, "kb_present": kb_present, "hits": []}


def _hit(row) -> dict:
    """Normalize a chunk row into the citation-ready hit shape."""
    score = row["score"]
    return {
        "chunk_id": str(row["chunk_id"]),
        "document_id": str(row["document_id"]),
        "chunk_index": row["chunk_index"],
        "content": row["content"],
        "metadata": row["metadata"],
        "title": row["title"],
        "doc_type": row["doc_type"],
        "score": round(float(score), 4) if score is not None else None,
    }


async def retrieve_ranked(client_id: str, query: str, *, top_k: int = 8,
                          min_score: float = SIM_THRESHOLD,
                          visibility: str | None = None,
                          extra_queries: list[str] | None = None) -> dict:
    """Retrieval with everything a grounding gate and a citation need.

    Returns `{method, top_score, kb_present, hits[]}` where `method` is
    `vector | keyword | none`. `kb_present` distinguishes "this tenant has no KB yet"
    from "the KB had nothing to say", which are different messages to a customer.

    `visibility` is an *optional* filter: with the default None the SQL is identical to
    the internal path, so analyze/factcheck/scoring/kb_admin and the operator copilot are
    unaffected and only the public autopilot passes 'public'.

    Never raises — a failure degrades to `method: "none"`, which the gate reads as
    ungrounded.
    """
    try:
        return await _retrieve_ranked(client_id, query, top_k, min_score,
                                      visibility, extra_queries)
    except Exception as exc:  # noqa: BLE001 — see module docstring
        log.warning("ranked retrieval failed (client=%s): %s", client_id, exc)
        return _empty_ranked()


async def _retrieve_ranked(client_id: str, query: str, top_k: int, min_score: float,
                           visibility: str | None,
                           extra_queries: list[str] | None) -> dict:
    if not client_id or not (query or "").strip():
        return _empty_ranked()
    top_k = max(1, min(int(top_k), 50))

    # Same EXISTS pre-check routers/scoring.py uses: one cheap indexed probe that
    # turns "no hits" into an answerable question. Its connection is released immediately:
    # the embed below is outbound HTTP with a multi-second budget, and holding one of the
    # pool's few connections across it would let a slow TEI backend starve every other
    # request in the process (auth, /health, analyze) — see services/embeddings.
    # Scoped to the SAME visibility the retrieval below will use, or the probe answers a
    # different question than the caller asked: a tenant whose KB is entirely 'internal' has
    # nothing the public bot may read, and reporting `kb_present: True` there turns "this
    # tenant has published nothing" into "the KB had nothing to say". Those are different
    # operational facts — they select different refusal copy and the P2 curation miner counts
    # them differently — and the publishing one is the actionable half.
    async with pool().acquire() as conn:
        if visibility is None:
            kb_present = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM kb_chunks WHERE client_id = $1)", client_id)
        else:
            kb_present = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM kb_chunks c JOIN kb_documents d "
                "ON d.id = c.document_id WHERE c.client_id = $1 AND d.visibility = $2)",
                client_id, visibility)
    if not kb_present:
        return _empty_ranked(False)

    queries = [query[:RANKED_QUERY_CHARS]]
    for q in (extra_queries or []):
        q = (q or "").strip()[:RANKED_QUERY_CHARS]
        if q and q not in queries:
            queries.append(q)

    try:
        # One batch call, not one per query: on a compute-bound CPU TEI backend a
        # batch of 2 is roughly 2x the work, NOT free — it buys a single round-trip
        # and a single queue slot, not free compute. Keep the list short.
        vecs = await embeddings.embed_texts(queries, purpose="query")
    except Exception as exc:  # noqa: BLE001
        log.warning("ranked embed failed (client=%s): %s", client_id, exc)
        vecs = []

    async with pool().acquire() as conn:
        if vecs:
            ranked = [await _vector_ranked(conn, client_id, to_pgvector(v), top_k, visibility)
                      for v in vecs]
            hits = ranked[0] if len(ranked) == 1 else _fuse(ranked)
            hits = _gate(hits, min_score)[:top_k]
            if hits:
                return {"method": "vector", "top_score": _top_score(hits),
                        "kb_present": True, "hits": hits}

        # Graceful degradation. Callers that treat keyword-only as ungrounded (the public
        # autopilot does) can still see it, because `method` survives to the caller.
        hits = await _keyword_ranked(conn, client_id, queries[0], top_k, visibility)
        if hits:
            return {"method": "keyword", "top_score": _top_score(hits),
                    "kb_present": True, "hits": hits}
    return _empty_ranked(True)


async def _vector_ranked(conn, client_id: str, qv: str, top_k: int,
                         visibility: str | None) -> list[dict]:
    # HNSW is scanned before the visibility predicate is applied, so a filtered query has
    # to over-fetch or it can come back short (or empty) while matching rows exist.
    limit = top_k if visibility is None else min(top_k * VISIBILITY_OVERFETCH,
                                                 VISIBILITY_OVERFETCH_CAP)
    rows = await conn.fetch(
        f"""
        SELECT c.id AS chunk_id, c.document_id, c.chunk_index, c.content, c.metadata,
               d.title, d.doc_type, 1 - (c.embedding <=> $2::vector) AS score
        FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id
        WHERE c.client_id = $1 AND c.embedding IS NOT NULL
          {"AND d.visibility = $4" if visibility is not None else ""}
        ORDER BY c.embedding <=> $2::vector
        LIMIT $3
        """,
        *((client_id, qv, limit) if visibility is None
          else (client_id, qv, limit, visibility)),
    )
    return [_hit(r) for r in rows][:top_k]


async def _keyword_ranked(conn, client_id: str, query: str, top_k: int,
                          visibility: str | None) -> list[dict]:
    rows = await conn.fetch(
        f"""
        SELECT c.id AS chunk_id, c.document_id, c.chunk_index, c.content, c.metadata,
               d.title, d.doc_type, similarity(c.content, $2) AS score
        FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id
        WHERE c.client_id = $1 AND c.content % $2
          {"AND d.visibility = $4" if visibility is not None else ""}
        ORDER BY similarity(c.content, $2) DESC
        LIMIT $3
        """,
        *((client_id, query, top_k) if visibility is None
          else (client_id, query, top_k, visibility)),
    )
    return [h for h in (_hit(r) for r in rows)
            if h["score"] is not None and h["score"] >= KEYWORD_MIN_SCORE]


def _fuse(ranked_lists: list[list[dict]]) -> list[dict]:
    """Reciprocal rank fusion over one result list per query vector.

    RRF ranks on position rather than score, which is what we want here: the cosine
    scores of two different query phrasings are not on a comparable scale. The reported
    `score` stays the best raw cosine for the chunk, because that is the number the
    grounding gate and the citation display are calibrated on.
    """
    merged: dict[str, dict] = {}
    for hits in ranked_lists:
        for rank, h in enumerate(hits):
            cur = merged.get(h["chunk_id"])
            if cur is None:
                cur = merged[h["chunk_id"]] = dict(h, _rrf=0.0)
            cur["_rrf"] += 1.0 / (RRF_K + rank + 1)
            if h["score"] is not None and (cur["score"] is None or h["score"] > cur["score"]):
                cur["score"] = h["score"]
    fused = sorted(merged.values(), key=lambda h: h["_rrf"], reverse=True)
    for h in fused:
        h.pop("_rrf", None)
    return fused


def _gate(hits: list[dict], min_score: float) -> list[dict]:
    """Absolute floor plus a floor relative to the best hit. Preserves input order —
    fused hits are in RRF order, which is not score order."""
    scored = [h for h in hits if h["score"] is not None]
    if not scored:
        return []
    floor = max(min_score, _top_score(scored) - RELATIVE_GATE)
    return [h for h in scored if h["score"] >= floor]


def _top_score(hits: list[dict]) -> float | None:
    scores = [h["score"] for h in hits if h["score"] is not None]
    return max(scores) if scores else None


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
