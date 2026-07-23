"""Tenant-scoped KB retrieval for RAG.

Cosine similarity over pgvector with a threshold; if nothing clears it (common for
lower-resource languages like Georgian), fall back to a trigram keyword match so the
analysis still gets relevant context.

Two entry points, deliberately different in what they hand back:

* `retrieve()` — the batch/offline shape used by analyze, factcheck and scoring. It
  answers "give me some context text", and every caller treats an empty list as "no KB
  help available". Its thresholds are transcript-shaped (hundreds of words against one
  chunk) and must not be re-tuned for short questions without re-checking those three.
* `retrieve_ranked()` — the shape a *conversation* needs. A chat answer has to cite
  what it used (so: chunk/document ids), and a grounding gate has to decide "I don't
  know" *before* the model is called — which is impossible without knowing whether the
  hits came from the vector index or from the lexical fallback, how strong the best one
  was, and whether the tenant has a KB at all. `retrieve()` threw all of that away.
  `POST /kb/search` now uses this one too, for exactly that reason — a search UI needs
  the mechanics as much as a gate does.

  **Its filtering knobs are chat-shaped defaults, not universal policy.** Feeding a weak
  passage to an LLM that will state it as fact, and hiding a passage from a human who
  explicitly asked to see it, are opposite failures. So `relative_gate` and
  `keyword_min_score` are parameters: the chat path keeps them, and a search UI turns
  them off (`relative_gate=None`, `keyword_min_score=0.0`) so it returns exactly what
  the un-ranked `retrieve()` used to return and simply *labels* what it is worth.

Neither function raises: retrieval is an enrichment step everywhere it is used, and a
DB or embedding-service blip must degrade the answer, not fail the request. (The old
docstring claimed this while only wrapping the embed call — a DB error inside the
keyword fallback propagated straight out of `POST /kb/search`.)

--- confidence ----------------------------------------------------------------

`assess_confidence()` turns the raw score curve into something a person can read. It
exists because **a BGE-M3 cosine score is not calibrated in absolute terms — only its
ranking is meaningful**, and every search UI we have was presenting the raw ranking as
if it were a set of matches.

The measurement that motivated it, from a real tenant: a search for "greeting" — a word
appearing nowhere in that KB — returned *every* document the tenant owns, scored 0.397 /
0.386 / 0.371 / 0.360. A band **0.037 wide**, all of it sitting just above
`SIM_THRESHOLD = 0.35`. Two unrelated pieces of same-language text still score ~0.30–0.45
under this encoder; a genuine match is usually >= 0.55. So a flat band down at 0.36–0.40
is the model saying "none of these match", and an absolute 0.35 floor reads it as "all of
these match". (Ruled out empirically: the trigram fallback scored that query 0.000–0.036
and nothing passed the `%` operator, so those were genuine vector scores.)

The fix here is deliberately NOT a new magic threshold — a correctly calibrated floor
needs the labelled Georgian/Russian eval set from a pilot tenant that does not exist yet
(ADR-001, milestone P4). It is to make the weakness *visible instead of silent*: the UI
still shows the closest passages, because an operator may legitimately want the
best-effort ranking, but it can now say plainly that nothing confidently matched, and why.

`assess_confidence()` is pure and synchronous — no DB, no encoder — so the tuning it
encodes is unit-testable without either.
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
#
# The first two are DEFAULTS FOR THE CHAT PATH, and both are per-call overridable —
# see the module docstring. They exist to keep junk out of a prompt the model will
# speak from; applying them to a human's search box would delete rows that person asked
# to see, which is the failure this whole change exists to stop.
KEYWORD_MIN_SCORE = 0.45   # trigram similarity floor — the *chat* fallback had NO floor at
                           # all, so an off-topic question returned lexically-similar
                           # noise with nothing to distinguish it from a real match.
                           # `POST /kb/search` passes 0.0: it never had this floor, and a
                           # search that silently returns nothing during an embedding
                           # outage is strictly worse than one that returns a weak
                           # trigram match under a banner saying the encoder is down.
RELATIVE_GATE = 0.08       # drop hits more than this far below the best one: short
                           # queries produce a flat score curve where rank 6 is unrelated.
                           # `POST /kb/search` passes None — on a good result set (0.82 /
                           # 0.78 / 0.42 / 0.38) this gate removes the last two, i.e. it
                           # hides most from the queries that worked best, while a flat
                           # noise band 0.037 wide passes through it untouched.
RANKED_QUERY_CHARS = 512   # TEI latency is roughly linear in tokens and a chat question is
                           # short; the 4000 on retrieve() is transcript-shaped and stays.
MAX_TOP_K = 50             # one page of results is the most any caller has a use for, and
                           # an unbounded LIMIT is an unbounded response. Routers that accept
                           # top_k from the wire declare this bound in their request model
                           # rather than letting it clamp silently — a partner that asked for
                           # 60 and got 50 has no way to notice.
RRF_K = 60                 # reciprocal rank fusion constant (the standard 60).
VISIBILITY_OVERFETCH = 4   # pgvector post-filters HNSW results, so a `visibility` filter
VISIBILITY_OVERFETCH_CAP = 200   # applied after the scan can empty an otherwise-full page.

# --- confidence bands ---------------------------------------------------------
# ALL THREE ARE PROVISIONAL, pending the P4 labelled eval set. They are read off the
# observed shape of BGE-M3 cosine on this corpus, not off a measured precision/recall
# curve, and the bar for changing them is evidence better than the observation below —
# not intuition. See the module docstring for the full measurement.
#
# Observed: unrelated same-language text still scores ~0.30–0.45; a genuine match is
# usually >= 0.55. The reported failure case was four unrelated documents inside a
# 0.037-wide band at 0.36–0.40.
CONFIDENT_SCORE = 0.55   # at/above this the top hit really is about the query
WEAK_SCORE = 0.45        # between the two: plausible, worth showing, not worth trusting
FLAT_SPREAD = 0.06       # a top-of-ranking narrower than this means the encoder is not
                         # discriminating between candidates at all — the signature of
                         # the reported bug (0.037), not of a good result set.
FLAT_WINDOW = 4          # ...measured over the top N scores, NOT over however many rows
                         # the caller chose to display. Spread-over-the-whole-set is not a
                         # property of the query: the same query at the same instant
                         # returns 4 rows to `/kb/search` (absolute floor) and 7 to the
                         # playground (`threshold=0.0`, everything), so the whole-set
                         # spread was 0.037 on one surface and 0.079 on the other and the
                         # operator console diagnosed the tenant's ticket differently from
                         # the tenant's own search box. A fixed window makes the verdict a
                         # statement about the ranking, which is shared, instead of about
                         # the page size, which is not. 4 = the top of the first screen.

# Machine-readable `confidence.reason` keys. Stable: the search UIs map them to copy, so
# renaming one silently drops a translated string on the floor.
REASON_EMPTY_KB = "empty_kb"
REASON_UNAVAILABLE = "unavailable"
REASON_NO_HITS = "no_hits"
REASON_KEYWORD_FALLBACK = "keyword_fallback"
REASON_LOW_SCORE = "low_score"
REASON_FLAT = "flat_distribution"
REASON_OK = "ok"


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
    return _ranked("none", [], kb_present)


def unavailable_ranked(kb_present: bool | None = None) -> dict:
    """The envelope for "retrieval did not run", as distinct from "it ran and found nothing".

    Public because `chat.py` needs it for the case where no query could be built: an envelope
    assembled by hand there would be one more exit that does not carry `confidence`.

    The distinction is the whole point. `kb_present: False` used to be reported for a failed
    embed, a failed query and an empty KB alike, and the UI turned that into "your knowledge
    base is empty — go and import your policies". Told to a tenant with four ready documents
    during an `EMBEDDING_DIM` mismatch, that is a confident, actionable, wrong instruction —
    worse than the unexplained empty list it replaced. So a failure reports `kb_present: None`
    ("unknown", because nothing was ever looked up) and `reason: unavailable`.

    `None` is safe for every existing consumer: `chat.py::gate()` and `chat_store` read it as
    falsy exactly as `False` was read, so no refusal decision moves.
    """
    return _ranked("none", [], kb_present,
                   confidence=_confidence("none", REASON_UNAVAILABLE))


def _ranked(method: str, hits: list[dict], kb_present: bool | None,
            *, confidence: dict | None = None) -> dict:
    """The one place the ranked envelope is built, so every exit carries `confidence`.

    `confidence` is passed in only where the reason is a fact about the *machinery* rather
    than about the score curve (see `unavailable_ranked`), which no amount of looking at an
    empty hit list can recover.
    """
    return {
        "method": method,
        "top_score": _top_score(hits),
        "kb_present": kb_present,
        "hits": hits,
        "confidence": confidence or assess_confidence(hits, method=method,
                                                      kb_present=kb_present),
    }


def _hit(row) -> dict:
    """Normalize a chunk row into the citation-ready hit shape.

    `score` is rounded to 4 dp. That is a display decision applied at the source on purpose:
    the underlying number is an *approximate* HNSW cosine, which already moves in the far
    decimals with index recall, a re-embed or a pgvector build, so full float precision here
    would advertise a reproducibility the value does not have. 4 dp is two orders of
    magnitude finer than any threshold in this module.
    """
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
                          extra_queries: list[str] | None = None,
                          relative_gate: float | None = RELATIVE_GATE,
                          keyword_min_score: float = KEYWORD_MIN_SCORE) -> dict:
    """Retrieval with everything a grounding gate and a citation need.

    Returns `{method, top_score, kb_present, hits[], confidence}` where `method` is
    `vector | keyword | none`. `kb_present` is `True` / `False` / `None` — the third being
    "not looked up", because retrieval failed or never ran. All three are different messages
    to a customer, and only "no KB yet" is one they can act on.

    `relative_gate` and `keyword_min_score` default to the chat-path policy and exist to be
    turned off by a search UI (`None` / `0.0`), which must not hide a row a person asked for.
    Passing `relative_gate=None` reduces the filtering to the absolute `min_score` floor —
    exactly what the un-ranked `retrieve()` applies.

    `confidence` (see `assess_confidence`) is ADDITIVE metadata for the search UIs and
    changes nothing about which hits come back or what any existing key means — in
    particular `chat.py::gate()`, which is the separately-configurable per-tenant
    decision about whether the bot answers at all, is untouched by it.

    `visibility` is an *optional* filter: with the default None the SQL is identical to
    the internal path, so analyze/factcheck/scoring/kb_admin and the operator copilot are
    unaffected and only the public autopilot passes 'public'.

    Never raises — a failure degrades to `method: "none"`, which the gate reads as
    ungrounded.
    """
    try:
        return await _retrieve_ranked(client_id, query, top_k, min_score, visibility,
                                      extra_queries, relative_gate, keyword_min_score)
    except Exception as exc:  # noqa: BLE001 — see module docstring
        log.warning("ranked retrieval failed (client=%s): %s", client_id, exc)
        # NOT `_empty_ranked(False)`: nothing was looked up, so "this tenant has no KB" is
        # not a fact we hold. See `unavailable_ranked`.
        return unavailable_ranked()


async def _retrieve_ranked(client_id: str, query: str, top_k: int, min_score: float,
                           visibility: str | None,
                           extra_queries: list[str] | None,
                           relative_gate: float | None,
                           keyword_min_score: float) -> dict:
    if not client_id or not (query or "").strip():
        return unavailable_ranked()
    top_k = max(1, min(int(top_k), MAX_TOP_K))

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

    encoder_down = False
    try:
        # One batch call, not one per query: on a compute-bound CPU TEI backend a
        # batch of 2 is roughly 2x the work, NOT free — it buys a single round-trip
        # and a single queue slot, not free compute. Keep the list short.
        vecs = await embeddings.embed_texts(queries, purpose="query")
    except Exception as exc:  # noqa: BLE001
        log.warning("ranked embed failed (client=%s): %s", client_id, exc)
        vecs, encoder_down = [], True

    async with pool().acquire() as conn:
        if vecs:
            ranked = [await _vector_ranked(conn, client_id, to_pgvector(v), top_k, visibility)
                      for v in vecs]
            hits = ranked[0] if len(ranked) == 1 else _fuse(ranked)
            hits = _gate(hits, min_score, relative_gate)[:top_k]
            if hits:
                return _ranked("vector", hits, True)

        # Graceful degradation. Callers that treat keyword-only as ungrounded (the public
        # autopilot does) can still see it, because `method` survives to the caller.
        hits = await _keyword_ranked(conn, client_id, queries[0], top_k, visibility,
                                     keyword_min_score)
        if hits:
            return _ranked("keyword", hits, True)
    if encoder_down:
        # Nothing came back AND the encoder never answered. "Nothing matched — try other
        # words" would be advice about a KB we never actually searched; the KB is fine and
        # the box is broken, which is an operator's problem, not the tenant's.
        return unavailable_ranked(True)
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
                          visibility: str | None,
                          keyword_min_score: float = KEYWORD_MIN_SCORE) -> list[dict]:
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
            if h["score"] is not None and h["score"] >= keyword_min_score]


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


def _gate(hits: list[dict], min_score: float,
          relative_gate: float | None = RELATIVE_GATE) -> list[dict]:
    """Absolute floor, plus an OPTIONAL floor relative to the best hit. Preserves input
    order — fused hits are in RRF order, which is not score order.

    `relative_gate=None` leaves only the absolute floor, which is what a search UI wants:
    the relative floor is a prompt-hygiene rule (don't hand the model rank 6 when rank 1 is
    twice as good), and applied to a search box it deletes rows the user asked to see.
    """
    scored = [h for h in hits if h["score"] is not None]
    if not scored:
        return []
    floor = min_score if relative_gate is None else max(min_score,
                                                        _top_score(scored) - relative_gate)
    return [h for h in scored if h["score"] >= floor]


def _top_score(hits: list[dict]) -> float | None:
    scores = [h["score"] for h in hits if h["score"] is not None]
    return max(scores) if scores else None


# --- confidence assessment ----------------------------------------------------
#
# SCOPE, deliberately: this is metadata for the search UIs. It does NOT feed
# `services/chat.py::gate()`, which is what decides whether the bot answers or refuses.
# That gate is separately configurable per tenant (`chat_configs.min_score`) and is not the
# thing that was broken here, so wiring confidence into it would change live refusal
# behaviour for every tenant as a side effect of a display fix.
# FOLLOW-UP (not done here, on purpose): once the P4 eval set exists, `flat_distribution` is
# a plausible additional refusal signal for the public autopilot — a flat band is the case
# where the gate's absolute `min_score` is least trustworthy. Needs the labelled data first.

def assess_confidence(hits: list[dict], *, method: str, kb_present: bool | None,
                      confident_score: float = CONFIDENT_SCORE,
                      weak_score: float = WEAK_SCORE,
                      flat_spread: float = FLAT_SPREAD,
                      flat_window: int = FLAT_WINDOW) -> dict:
    """How much to believe this result set. Pure, synchronous, side-effect-free.

    Returns::

        {"level": "high" | "medium" | "low" | "none",
         "confident": bool,          # convenience: level in ("high", "medium")
         "reason": str,              # one of the REASON_* keys above
         "top_score": float | None,
         "spread": float | None,     # top minus the last of the top `flat_window` scores
         "margin": float | None}     # top - second: how far #1 stands out

    `spread` is measured over a bounded window of the ranking rather than over every hit
    handed in, so it describes the query and not the page size — see `FLAT_WINDOW`.

    Rules, in order:

    * `kb_present is None` -> `none` / `unavailable`. Nothing was looked up (a failed
      retrieval, or one that never ran), so every other verdict here would be a claim
      about a KB that was not consulted.
    * no KB at all -> `none` / `empty_kb`. Deliberately distinct from "no match": the
      tenant has imported nothing, which is a completely different thing to tell a user
      (and a fixable one). Only reachable when the caller actually established it.
    * no hits -> `none` / `no_hits`.
    * keyword method -> at most `low`, `keyword_fallback`. Trigram similarity is a
      different scale entirely and is not comparable to cosine, so it must never be
      presented as a confident match however high the number looks.
    * top >= `confident_score` -> `high` / `ok`; top >= `weak_score` -> `medium` / `ok`.
    * otherwise `low`, and `flat_distribution` when the whole result set fits inside
      `flat_spread` — the encoder is not discriminating between candidates, which is the
      actual signature of the reported bug — else `low_score`.

    No DB and no encoder are involved on purpose: this is the tuning that the P4 eval set
    will eventually argue with, and it should be arguable in a unit test.
    """
    if kb_present is None:
        return _confidence("none", REASON_UNAVAILABLE)
    if not kb_present:
        return _confidence("none", REASON_EMPTY_KB)
    if not hits:
        return _confidence("none", REASON_NO_HITS)

    # `score` can be missing or null on the keyword path, and a fused hit inherits None
    # when every phrasing missed it. Sort defensively rather than trusting hit order:
    # RRF output is in rank order, which is not score order.
    scores = sorted((h["score"] for h in hits
                     if isinstance(h, dict) and h.get("score") is not None), reverse=True)
    scores = [float(s) for s in scores]

    # The window is what makes the verdict independent of how many rows the caller chose to
    # show: `/kb/search` (absolute floor) and the playground (`threshold=0.0`) return
    # different-length prefixes of the SAME ranking, and comparing top-to-last made them
    # disagree about the identical query. See FLAT_WINDOW.
    window = scores[:max(2, int(flat_window))]
    top = scores[0] if scores else None
    spread = (window[0] - window[-1]) if len(window) >= 2 else (0.0 if scores else None)
    margin = (scores[0] - scores[1]) if len(scores) >= 2 else None
    stats = {"top_score": top, "spread": spread, "margin": margin}

    if method == "keyword":
        return _confidence("low", REASON_KEYWORD_FALLBACK, **stats)
    if top is None:
        # Hits with no usable score at all. Not "no hits" — there is something to show —
        # but nothing here can justify calling it a match.
        return _confidence("low", REASON_LOW_SCORE, **stats)
    if top >= confident_score:
        return _confidence("high", REASON_OK, **stats)
    if top >= weak_score:
        return _confidence("medium", REASON_OK, **stats)
    # `len(scores) >= 2`: with a single candidate the spread is arithmetically 0.0, but one
    # result cannot demonstrate that the encoder failed to discriminate *between* results.
    # Calling that "flat" would put the wrong explanation in front of the user — a lone weak
    # hit is a low score, full stop.
    flat = len(scores) >= 2 and spread is not None and spread < flat_spread
    return _confidence("low", REASON_FLAT if flat else REASON_LOW_SCORE, **stats)


def _confidence(level: str, reason: str, top_score: float | None = None,
                spread: float | None = None, margin: float | None = None) -> dict:
    return {
        "level": level,
        "confident": level in ("high", "medium"),
        "reason": reason,
        "top_score": round(top_score, 4) if top_score is not None else None,
        "spread": round(spread, 4) if spread is not None else None,
        "margin": round(margin, 4) if margin is not None else None,
    }


async def search_debug(client_id: str, query: str, top_k: int = 8,
                        threshold: float = 0.0) -> dict:
    """Retrieval playground: return the exact chunks retrieved with scores + ids + source,
    plus which method (vector or keyword) produced them. Tenant-scoped by client_id."""
    top_k = max(1, min(int(top_k), MAX_TOP_K))
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
