"""What `assess_confidence()` promises, pinned so the next person to touch the constants
has to argue with a real measurement instead of with intuition.

The bug this guards against was measured on a live tenant, not imagined: a search for
**"greeting"** — a word appearing nowhere in that KB — returned *every* document the tenant
owned, scored 0.397 / 0.386 / 0.371 / 0.360. A band 0.037 wide, all of it sitting just above
`SIM_THRESHOLD = 0.35`, rendered by the UI as four results with nothing to mark them as noise.
A BGE-M3 cosine score is not calibrated in absolute terms — only its ranking is meaningful —
so a flat band down at 0.36-0.40 is the encoder saying "none of these match", and an absolute
0.35 floor reads it as "all of these match".

Two layers here, and the split is deliberate:

* **The pure tests** (most of the file) touch no database, no encoder and no HTTP, because
  `assess_confidence()` is pure and synchronous for exactly this reason: the tuning it encodes
  is what the P4 labelled eval set will eventually argue with, and that argument should be
  runnable anywhere, in milliseconds, with no services up.
* **Two integration tests** drive the real `POST /kb/search`. One of them exists purely to
  defend the partner API: that route is mounted twice (`/kb/search` and `/v1/kb/search`) and
  external consumers read its result fields, so the swap from `retrieve()` to
  `retrieve_ranked()` was allowed to ADD keys and never to drop, rename or reshape one. The
  other reproduces the reported band end-to-end, through the encoder, SQL, the relative gate
  and the router, so "the fix works" is not an assertion about a helper function in isolation.

Thresholds are asserted as *literals* rather than by reference to `CONFIDENT_SCORE` and
friends. Comparing the module against itself would pass no matter what the constants became;
the literals are the point of the test.
"""
import copy
import math
import uuid

import pytest

from app.services import retrieval
from app.services.retrieval import assess_confidence
from conftest import sql

# The measurement, verbatim. Do not "tidy" these numbers — they are data.
REPORTED_SCORES = [0.397, 0.386, 0.371, 0.360]

# Filled in from the live column type by the seeding fixture, so a developer volume created
# under a different EMBEDDING_DIM does not fail inside a fixture with a confusing error.
_DIM = 1024

MARK = "CONFIDENCE_FIXTURE_CHUNK"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def hits(*scores) -> list[dict]:
    """Minimal hit dicts. `assess_confidence` only ever reads `score`, and keeping these
    skeletal is a test in itself: the day it starts needing `content` or `chunk_id`, it has
    stopped being the pure scoring-curve function it is documented to be."""
    return [{"chunk_id": f"c{i}", "score": s} for i, s in enumerate(scores)]


def _unit_axis0() -> list[float]:
    """The query vector every integration test embeds to."""
    v = [0.0] * _DIM
    v[0] = 1.0
    return v


def _vec_with_cosine(score: float) -> list[float]:
    """A unit vector whose cosine against `_unit_axis0()` is exactly `score`.

    Lets a test name the score curve it wants — including the reported 0.037-wide band —
    instead of hoping some hand-written text happens to land there under the real encoder.
    """
    v = [0.0] * _DIM
    v[0] = score
    v[1] = math.sqrt(max(0.0, 1.0 - score * score))
    return v


def _pgvector(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# --------------------------------------------------------------------------- #
# 1. The reported bug
# --------------------------------------------------------------------------- #
def test_searching_greeting_returned_every_document_is_not_confident():
    """The live failure: a tenant searched for "greeting" — absent from their KB — and got
    back all four documents they own, scored 0.397 / 0.386 / 0.371 / 0.360.

    A 0.037-wide band just above `SIM_THRESHOLD = 0.35`. Unrelated same-language text scores
    ~0.30-0.45 under BGE-M3 and a genuine match is usually >= 0.55, so this is the encoder
    failing to discriminate between candidates, not four answers. (Ruled out empirically at
    the time: the trigram fallback scored this query 0.000-0.036 and nothing passed the `%`
    operator, so these were genuine vector scores.)

    If a future re-tuning of CONFIDENT_SCORE / WEAK_SCORE / FLAT_SPREAD makes this case come
    back `confident`, the re-tuning is wrong — this is the exact shape the feature exists for.
    """
    c = assess_confidence(hits(*REPORTED_SCORES), method="vector", kb_present=True)

    assert c["confident"] is False
    assert c["level"] == "low"
    assert c["reason"] == "flat_distribution"
    assert c["top_score"] == 0.397
    assert c["spread"] == 0.037     # 0.397 - 0.360
    assert c["margin"] == 0.011     # 0.397 - 0.386


def test_reported_band_is_flat_by_the_module_constant_too():
    """Belt and braces on the one relationship the verdict actually hinges on: the measured
    band must sit strictly inside FLAT_SPREAD. Stated separately from the literals above so a
    constant change that breaks the reported case names *itself* in the failure output."""
    assert 0.397 - 0.360 < retrieval.FLAT_SPREAD


# --------------------------------------------------------------------------- #
# 2-3. The cases that should be believed
# --------------------------------------------------------------------------- #
def test_a_clearly_leading_top_score_is_high_confidence():
    """A genuine match: the top hit is well clear of the 0.55 "really is about the query"
    band, so the UI should present it as an answer and not hedge."""
    c = assess_confidence(hits(0.78, 0.74, 0.71), method="vector", kb_present=True)

    assert c["level"] == "high"
    assert c["reason"] == "ok"
    assert c["confident"] is True
    assert c["top_score"] == 0.78
    assert c["margin"] == 0.04


def test_a_mid_band_top_score_is_medium_confidence():
    """Between WEAK_SCORE and CONFIDENT_SCORE: plausible and worth showing. Still `confident`,
    because `confident` means "worth putting in front of a person", not "certain"."""
    c = assess_confidence(hits(0.48, 0.44), method="vector", kb_present=True)

    assert c["level"] == "medium"
    assert c["reason"] == "ok"
    assert c["confident"] is True


def test_high_and_medium_are_exactly_the_confident_levels():
    """`confident` is documented as a convenience for `level in ("high", "medium")`. Consumers
    branch on the boolean and translate the level, so the two must not drift apart."""
    for given, level in ((hits(0.90), "high"), (hits(0.50), "medium"),
                         (hits(0.20), "low"), ([], "none")):
        c = assess_confidence(given, method="vector", kb_present=True)
        assert c["level"] == level
        assert c["confident"] is (level in ("high", "medium"))


# --------------------------------------------------------------------------- #
# 4. The keyword fallback is a different scale
# --------------------------------------------------------------------------- #
def test_keyword_method_is_never_better_than_low_even_when_the_score_is_high():
    """Trigram similarity and cosine similarity are not the same number.

    A 0.91 trigram score means "these strings share a lot of character trigrams", which an
    off-topic question can produce trivially. Presenting it with the same confidence as a 0.91
    cosine would be the original bug wearing a different hat, so the keyword path is capped at
    `low` regardless of how good the number looks. The stats are still reported — an operator
    debugging retrieval wants to see the number, they just must not be told to trust it.
    """
    c = assess_confidence(hits(0.91, 0.88), method="keyword", kb_present=True)

    assert c["level"] == "low"
    assert c["reason"] == "keyword_fallback"
    assert c["confident"] is False
    assert c["top_score"] == 0.91
    assert c["margin"] == 0.03


# --------------------------------------------------------------------------- #
# 5-6. Nothing to say, and the two different reasons for it
# --------------------------------------------------------------------------- #
def test_no_kb_outranks_every_other_rule():
    """"You have imported nothing" and "nothing matched" are different things to tell a
    customer, and only the first one is fixable by the customer.

    `kb_present=False` is asserted here even with a perfect set of hits present — a caller
    that somehow has both is in a contradictory state, and the honest report is the one about
    the KB. The stats are deliberately blank: there is nothing meaningful to characterise.
    """
    c = assess_confidence(hits(0.99, 0.98), method="vector", kb_present=False)

    assert c["level"] == "none"
    assert c["reason"] == "empty_kb"
    assert c["confident"] is False
    assert c["top_score"] is None
    assert c["spread"] is None
    assert c["margin"] is None


def test_no_hits_is_distinct_from_no_kb():
    c = assess_confidence([], method="vector", kb_present=True)

    assert c["level"] == "none"
    assert c["reason"] == "no_hits"
    assert c["confident"] is False
    assert c["top_score"] is None


def test_unknown_kb_is_unavailable_not_empty_kb():
    """`kb_present=None` means "not looked up", and it must NOT collapse into "you have no KB".

    This is the difference between "search could not run, this says nothing about your
    documents" and "your knowledge base is empty, go and import your policies" — the second of
    which was being shown, in three languages, to a tenant with four ready documents whose
    retrieval had failed on an EMBEDDING_DIM mismatch. Confident, actionable and wrong is a
    worse failure than the unexplained empty list it replaced.
    """
    c = assess_confidence([], method="none", kb_present=None)

    assert c["level"] == "none"
    assert c["reason"] == "unavailable"
    assert c["confident"] is False

    # It outranks every other rule, exactly as `empty_kb` does: hits that arrived from a run we
    # know failed cannot be characterised, so nothing about their curve is reported.
    c = assess_confidence(hits(0.99, 0.98), method="vector", kb_present=None)
    assert c["reason"] == "unavailable"
    assert c["top_score"] is None


def test_unavailable_ranked_envelope_carries_confidence_like_every_other_exit():
    """`chat.py` needs this constructor because an envelope built by hand there was the one
    ranked exit without a `confidence` key — a KeyError waiting for the first consumer to read
    it, on precisely the degraded path. Its other fields must stay byte-identical to that
    literal, because `gate()` reads them and no refusal decision may move."""
    r = retrieval.unavailable_ranked(False)

    assert r == {"method": "none", "top_score": None, "kb_present": False, "hits": [],
                 "confidence": r["confidence"]}
    assert r["confidence"]["reason"] == "unavailable"
    # Default: not even the KB probe ran, so `kb_present` is unknown rather than False.
    assert retrieval.unavailable_ranked()["kb_present"] is None


# --------------------------------------------------------------------------- #
# 7. Null scores
# --------------------------------------------------------------------------- #
def test_a_hit_with_a_null_score_does_not_raise():
    """The keyword path can hand back a NULL `similarity()`, and a fused hit inherits None when
    every query phrasing missed it. Retrieval never raises by design, so neither may the thing
    that describes it — a crash here would turn a degraded search into a 500."""
    c = assess_confidence(hits(None, 0.50), method="vector", kb_present=True)

    assert c["level"] == "medium"
    assert c["top_score"] == 0.5
    assert c["margin"] is None   # only one usable score, so nothing to compare against


def test_hits_with_no_usable_score_at_all_are_low_not_absent():
    """All-None is NOT `no_hits`: there is something to show the operator, it just cannot
    justify calling anything a match. Reporting "no hits" would be a lie about the result set."""
    c = assess_confidence(hits(None, None), method="vector", kb_present=True)

    assert c["level"] == "low"
    assert c["reason"] == "low_score"
    assert c["confident"] is False
    assert c["top_score"] is None
    assert c["spread"] is None
    assert c["margin"] is None


def test_it_does_not_mutate_its_input():
    """Callers pass the live hit list that is about to be serialised to the client. Sorting or
    annotating it in place would reorder search results as a side effect of describing them."""
    given = hits(*REPORTED_SCORES)
    before = copy.deepcopy(given)

    assess_confidence(given, method="vector", kb_present=True)

    assert given == before


def test_unsorted_input_is_assessed_the_same_as_sorted():
    """RRF output is in *rank* order, which is not score order. If the top score were read off
    position 0 the whole assessment would silently describe the wrong hit."""
    shuffled = hits(0.371, 0.397, 0.360, 0.386)
    assert (assess_confidence(shuffled, method="vector", kb_present=True)
            == assess_confidence(hits(*REPORTED_SCORES), method="vector", kb_present=True))


# --------------------------------------------------------------------------- #
# 8. A single hit
# --------------------------------------------------------------------------- #
def test_a_single_weak_hit_is_low_score_not_flat_distribution():
    """One candidate has an arithmetic spread of 0.0, which is trivially "flat" — but a lone
    result cannot demonstrate that the encoder failed to discriminate *between* results, which
    is what `flat_distribution` tells the user. So the number is reported honestly as 0.0 and
    the explanation is the truthful one: the score is simply low."""
    c = assess_confidence(hits(0.40), method="vector", kb_present=True)

    assert c["level"] == "low"
    assert c["reason"] == "low_score"
    assert c["top_score"] == 0.4
    assert c["spread"] == 0.0    # not fudged — there genuinely is no spread
    assert c["margin"] is None   # there is no second hit to stand out from


def test_a_single_strong_hit_is_still_high():
    c = assess_confidence(hits(0.83), method="vector", kb_present=True)

    assert c["level"] == "high"
    assert c["reason"] == "ok"
    assert c["margin"] is None


# --------------------------------------------------------------------------- #
# 9. The thresholds are injectable
# --------------------------------------------------------------------------- #
def test_confident_score_is_injectable():
    """Per-tenant calibration is the whole point of the keyword-only arguments: the current
    constants are provisional guesses pending the P4 eval set, and a pilot tenant with a
    labelled question set must be able to move them without a code change."""
    strong = hits(0.50, 0.47)
    assert assess_confidence(strong, method="vector", kb_present=True)["level"] == "medium"
    assert assess_confidence(strong, method="vector", kb_present=True,
                             confident_score=0.45)["level"] == "high"


def test_weak_score_is_injectable():
    mid = hits(0.50, 0.47)
    assert assess_confidence(mid, method="vector", kb_present=True)["confident"] is True
    strict = assess_confidence(mid, method="vector", kb_present=True, weak_score=0.60)
    assert strict["level"] == "low"
    assert strict["confident"] is False


def test_flat_spread_is_injectable_in_both_directions():
    # A 0.12-wide band is not flat by default...
    wide = hits(0.42, 0.30)
    assert assess_confidence(wide, method="vector",
                             kb_present=True)["reason"] == "low_score"
    # ...but a tenant whose corpus produces wider bands can say so.
    assert assess_confidence(wide, method="vector", kb_present=True,
                             flat_spread=0.20)["reason"] == "flat_distribution"
    # And the reported case stops being called flat under a strict enough setting — the
    # verdict follows the threshold, it is not hard-coded to the symptom.
    assert assess_confidence(hits(*REPORTED_SCORES), method="vector", kb_present=True,
                             flat_spread=0.01)["reason"] == "low_score"


def test_flat_window_makes_the_verdict_independent_of_how_many_rows_are_shown():
    """The reported band, as `/kb/search` sees it (4 rows, absolute floor) and as the operator
    console sees it (7 rows, `threshold=0.0` — the same ranking, unfiltered).

    Top-to-last made these two disagree about one query at one instant: 0.037 on one surface,
    0.079 on the other, so the tenant's search box said `flat_distribution` while the console
    an operator was answering the ticket in said `low_score`. Spread is measured over a bounded
    window of the ranking for exactly this reason — it has to be a statement about the query,
    not about the page size.
    """
    tenant_view = hits(0.397, 0.386, 0.371, 0.360)
    console_view = hits(0.397, 0.386, 0.371, 0.360, 0.345, 0.330, 0.318)

    a = assess_confidence(tenant_view, method="vector", kb_present=True)
    b = assess_confidence(console_view, method="vector", kb_present=True)

    assert a["reason"] == b["reason"] == "flat_distribution"
    assert a["spread"] == b["spread"] == 0.037
    assert a["margin"] == b["margin"] == 0.011


def test_flat_window_is_injectable_and_never_narrower_than_a_pair():
    """A window of 1 would make "spread" meaningless (always 0.0, so always flat), so the floor
    is a pair. Widening it is the meaningful direction and stays available to a tenant whose
    corpus produces a longer flat head."""
    given = hits(0.40, 0.39, 0.38, 0.37, 0.10)

    assert assess_confidence(given, method="vector", kb_present=True)["spread"] == 0.03
    assert assess_confidence(given, method="vector", kb_present=True,
                             flat_window=5)["spread"] == 0.30
    assert assess_confidence(given, method="vector", kb_present=True,
                             flat_window=1)["spread"] == 0.01   # clamped to 2


# --------------------------------------------------------------------------- #
# 9b. The relative gate is a caller's policy, not a law of retrieval
# --------------------------------------------------------------------------- #
def test_relative_gate_can_be_switched_off_leaving_only_the_absolute_floor():
    """`_gate` exists to keep junk out of an LLM prompt. Applied to a human's search box it
    deletes rows that person asked to see — and it does the most damage to the results that
    worked BEST: on 0.82 / 0.78 / 0.42 / 0.38 it removes half the answers, while the 0.037-wide
    noise band that motivated this whole feature passes through it untouched."""
    good = hits(0.82, 0.78, 0.42, 0.38)

    assert len(retrieval._gate(good, 0.35)) == 2                      # chat policy
    assert len(retrieval._gate(good, 0.35, None)) == 4                # search UI
    # The absolute floor still applies with the relative gate off — this is exactly what the
    # un-ranked `retrieve()` did, which is what `/kb/search` used to be.
    assert len(retrieval._gate(hits(0.82, 0.30), 0.35, None)) == 1


def test_reason_keys_are_stable_wire_values():
    """Every reason string is a translation key in the search UIs (en/ka/ru). Renaming one
    without touching the DICT drops a translated string on the floor and shows the user a raw
    identifier, so the wire values are pinned here rather than only in the module."""
    assert retrieval.REASON_EMPTY_KB == "empty_kb"
    assert retrieval.REASON_UNAVAILABLE == "unavailable"
    assert retrieval.REASON_NO_HITS == "no_hits"
    assert retrieval.REASON_KEYWORD_FALLBACK == "keyword_fallback"
    assert retrieval.REASON_LOW_SCORE == "low_score"
    assert retrieval.REASON_FLAT == "flat_distribution"
    assert retrieval.REASON_OK == "ok"


# --------------------------------------------------------------------------- #
# Integration: the real POST /kb/search (= POST /v1/kb/search)
# --------------------------------------------------------------------------- #
# Everything below needs the database. It skips cleanly without one (see conftest).

# The fields `POST /kb/search` returned BEFORE confidence landed. This route is mounted twice
# — `/kb/search` for the browser UI and `/v1/kb/search` for the B2B partner API — so external
# consumers read these names. They may be added to; they may never be dropped or renamed.
LEGACY_RESULT_KEYS = {"content", "metadata", "title", "doc_type", "score"}
LEGACY_ENVELOPE_KEYS = {"count", "results"}

# What a result carries today: the legacy five plus the citation ids the ranked path added.
CURRENT_RESULT_KEYS = LEGACY_RESULT_KEYS | {"chunk_id", "document_id", "chunk_index"}
CURRENT_ENVELOPE_KEYS = LEGACY_ENVELOPE_KEYS | {"method", "top_score", "kb_present",
                                                "confidence"}


@pytest.fixture
def kb_tenant(api, db_available):
    """Factory: a tenant whose KB has one chunk per requested cosine score.

    Naming the score curve directly is what makes the reported 0.037-wide band reproducible
    end-to-end. Hand-written text plus the real encoder could not be relied on to land in that
    band, and a test that only *sometimes* reproduces the bug is not a regression test.

    Every KB table cascades from `clients`, so one DELETE per tenant reclaims documents,
    chunks and events.
    """
    created: list[uuid.UUID] = []

    def _make(scores: list[float], label: str = "conf", content: str | None = None) -> dict:
        suffix = uuid.uuid4().hex[:8]
        api_key = f"conf-key-{label}-{suffix}"

        async def _setup(conn):
            global _DIM
            # Read the real vector dimension off the column rather than trusting settings.
            dim = await conn.fetchval(
                """SELECT a.atttypmod FROM pg_attribute a
                     JOIN pg_class c ON c.oid = a.attrelid
                    WHERE c.relname = 'kb_chunks' AND a.attname = 'embedding'""")
            if dim and dim > 0:
                _DIM = int(dim)

            cid = await conn.fetchval(
                "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
                f"conf-{label}-{suffix}", f"confidence-{label}", api_key)
            doc = await conn.fetchval(
                "INSERT INTO kb_documents (client_id, doc_type, title, status, visibility) "
                "VALUES ($1,'policy',$2,'ready','public') RETURNING id",
                cid, f"Support Hours & Contact {suffix}")
            for i, s in enumerate(scores):
                await conn.execute(
                    "INSERT INTO kb_chunks (document_id, client_id, content, embedding, "
                    "chunk_index) VALUES ($1,$2,$3,$4::vector,$5)",
                    doc, cid, content or f"{MARK} {i}",
                    _pgvector(_vec_with_cosine(s)), i)
            return cid

        cid = sql(_setup)
        created.append(cid)
        return {"client_id": str(cid), "api_key": api_key}

    yield _make

    for cid in created:
        sql(lambda c, cid=cid: c.execute("DELETE FROM clients WHERE id = $1", cid))


@pytest.fixture
def axis0_query(monkeypatch):
    """Embed every query to the axis-0 unit vector, so a chunk seeded by `_vec_with_cosine(s)`
    scores exactly `s`. Overrides conftest's autouse embedder, which aims at tenant B's vector
    for the isolation suite and would score everything here at 0."""
    from app.services import embeddings

    async def _embed(texts, *, purpose: str = "ingest"):
        return [_unit_axis0() for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", _embed)
    return _embed


def _search(api, tenant: dict, query: str, top_k: int = 6):
    return api.post("/kb/search", headers={"X-API-Key": tenant["api_key"]},
                    json={"query": query, "top_k": top_k})


def _playground(api, tenant: dict, query: str, top_k: int = 8):
    return api.post("/kb/playground", headers={"X-API-Key": tenant["api_key"]},
                    json={"query": query, "top_k": top_k, "threshold": 0.0})


# --- 10. the partner-API contract ------------------------------------------- #
def test_kb_search_still_returns_every_field_it_returned_before(api, kb_tenant, axis0_query):
    """`POST /kb/search` is also `POST /v1/kb/search`. Swapping its backing call from
    `retrieve()` to `retrieve_ranked()` was allowed to ADD keys and never to remove, rename or
    reshape one, because partner integrations read these names out of the JSON.

    The subset assertion is the contract and must never be relaxed. The exact-set assertion
    below it is a tripwire, not a ban on growth: if you deliberately added a field, update the
    constant in this file and say so in the commit. If a field went missing, you just broke an
    external consumer.
    """
    tenant = kb_tenant([0.92], label="contract")

    r = _search(api, tenant, "what are your support hours?")
    assert r.status_code == 200, r.text
    body = r.json()

    assert LEGACY_ENVELOPE_KEYS <= set(body)
    assert set(body) == CURRENT_ENVELOPE_KEYS

    results = body.get("results")
    assert isinstance(results, list) and len(results) == 1
    assert body["count"] == len(results)

    keys = set(results[0])
    assert LEGACY_RESULT_KEYS <= keys, f"partner API lost {LEGACY_RESULT_KEYS - keys}"
    assert keys == CURRENT_RESULT_KEYS

    # `score` still means the cosine similarity it always meant, and a genuine match reads as
    # one — so the confidence block is additive information, not a re-scoring.
    assert results[0]["score"] == pytest.approx(0.92, abs=1e-3)
    assert body["confidence"]["level"] == "high"
    assert body["confidence"]["confident"] is True
    assert body["kb_present"] is True
    assert body["method"] == "vector"


# --- 11. the reported bug, end to end --------------------------------------- #
def test_flat_scoring_query_is_flagged_through_the_real_endpoint(api, kb_tenant, axis0_query):
    """The reported failure reproduced through the encoder, the SQL, the relative gate and the
    router — not just through the helper in isolation.

    All four hits still come back, on purpose: an operator may legitimately want the
    best-effort ranking, and hiding them would trade a silent failure for an unexplained empty
    page. What changed is that the response now says plainly that nothing confidently matched,
    and why.
    """
    tenant = kb_tenant(REPORTED_SCORES, label="flat")

    r = _search(api, tenant, "greeting")
    assert r.status_code == 200, r.text
    body = r.json()

    results = body.get("results")
    assert isinstance(results, list)
    assert len(results) == len(REPORTED_SCORES), "nothing may be hidden from a search UI"
    assert body["method"] == "vector"
    assert body["kb_present"] is True

    c = body["confidence"]
    assert c["confident"] is False
    assert c["level"] == "low"
    assert c["reason"] == "flat_distribution"
    assert c["top_score"] == pytest.approx(0.397, abs=1e-3)
    assert c["spread"] == pytest.approx(0.037, abs=1e-3)
    assert c["spread"] < retrieval.FLAT_SPREAD
    assert c["top_score"] < retrieval.WEAK_SCORE


def test_a_tenant_with_no_kb_is_told_so_rather_than_shown_nothing(api, kb_tenant, axis0_query):
    """The other half of the same UX problem: an empty result list with no explanation reads
    as "search is broken". `empty_kb` is the fixable case and has to be distinguishable."""
    tenant = kb_tenant([], label="emptykb")

    r = _search(api, tenant, "greeting")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["results"] == []
    assert body["count"] == 0
    assert body["kb_present"] is False
    assert body["confidence"]["level"] == "none"
    assert body["confidence"]["reason"] == "empty_kb"


# --- 12. nothing may be HIDDEN from a search UI ------------------------------ #
def test_a_good_result_set_keeps_every_passage_it_used_to_return(api, kb_tenant, axis0_query):
    """The regression that backing this route with `retrieve_ranked` first introduced.

    `RELATIVE_GATE` (0.08) drops anything that far below the best hit. That is the right rule
    for a chat prompt and the wrong one for a search box, and its damage is inverted: on a
    genuinely GOOD result set (0.82 / 0.78 / 0.42 / 0.38) it silently removed the last two,
    while the 0.037-wide noise band this whole feature exists to expose sailed through it.
    A partner integration would have watched its result count halve with no way to know why.

    All four are above `SIM_THRESHOLD`, which is the only floor `retrieve()` ever applied here.
    """
    tenant = kb_tenant([0.82, 0.78, 0.42, 0.38], label="good")

    r = _search(api, tenant, "greeting", top_k=6)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["count"] == 4, "the relative gate must not trim a search UI's results"
    assert [h["score"] for h in body["results"]] == pytest.approx([0.82, 0.78, 0.42, 0.38],
                                                                 abs=1e-3)
    # The leading hit is real, so this set is reported as trustworthy — the point being that
    # showing everything and labelling it are not in tension.
    assert body["confidence"]["level"] == "high"
    assert body["confidence"]["confident"] is True


# --- 13. an embedding outage degrades, it does not go silent ----------------- #
@pytest.fixture
def encoder_down(monkeypatch):
    """Every embed call fails, as during a TEI outage or a cold-start model download."""
    from app.services import embeddings

    async def _boom(texts, *, purpose: str = "ingest"):
        raise RuntimeError("TEI unreachable")

    monkeypatch.setattr(embeddings, "embed_texts", _boom)


def test_an_embedding_outage_still_returns_the_trigram_match_it_used_to(api, kb_tenant,
                                                                       encoder_down):
    """`/kb/search` never had a keyword floor; `retrieve_ranked`'s chat path has one at 0.45.

    Inheriting it turned a degraded-but-useful answer into an empty page, and the empty page
    was then explained as `no_hits` — "nothing matched, try other words, import a document" —
    when the truth is that the encoder is down and the KB was never consulted. The red
    "meaning-based search is unavailable" banner written for this exact outage was unreachable
    below trigram 0.45.

    The seeded passage scores ~0.36 against this query: over pg_trgm's 0.3 `%` threshold,
    under 0.45, which is precisely the band that disappeared. (Measured, not guessed — see the
    range asserted below, which fails loudly if a pg_trgm change moves it out of the band and
    quietly stops testing anything.)
    """
    tenant = kb_tenant([0.9], label="outage",
                       content="Support Hours & Contact — see the front desk")

    r = _search(api, tenant, "Support Hours")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["count"] == 1, "an outage must degrade to trigram, not to silence"
    assert body["method"] == "keyword"
    assert 0.3 <= body["results"][0]["score"] < retrieval.KEYWORD_MIN_SCORE
    c = body["confidence"]
    assert c["reason"] == "keyword_fallback"     # the banner this outage was written for
    assert c["confident"] is False               # trigram is a different scale — never trusted


def test_an_outage_with_no_trigram_match_says_unavailable_not_no_hits(api, kb_tenant,
                                                                     encoder_down):
    """Zero results because the encoder is down is not zero results because the KB is silent.
    Only the second one means "ask differently or import a document"."""
    tenant = kb_tenant([0.9], label="outage2",
                       content="Support Hours & Contact — see the front desk")

    body = _search(api, tenant, "zzzzz nothing shares trigrams with this").json()

    assert body["count"] == 0
    assert body["confidence"]["reason"] == "unavailable"


# --- 14. a failed retrieval is not an empty knowledge base ------------------- #
def test_a_failed_retrieval_does_not_tell_a_stocked_tenant_their_kb_is_empty(api, kb_tenant,
                                                                            monkeypatch):
    """Reproduces the documented `EMBEDDING_DIM` mismatch (CLAUDE.md: "changing embedding
    model/dim => re-embed every KB") by embedding a query to the wrong width, which makes
    pgvector raise `different vector dimensions 1024 and 768` inside the SQL.

    The old envelope reported `kb_present: false`, which the UI rendered as "This knowledge
    base is empty ... open the Import tab and add your policies first" — to a tenant with four
    ready documents, in all three languages. `kb_present` is now `null` ("not looked up") and
    the reason names the machinery.
    """
    from app.services import embeddings

    async def _wrong_width(texts, *, purpose: str = "ingest"):
        return [[0.0] * (max(2, _DIM // 2)) for _ in texts]

    tenant = kb_tenant(REPORTED_SCORES, label="dimfail")
    monkeypatch.setattr(embeddings, "embed_texts", _wrong_width)

    r = _search(api, tenant, "greeting")
    assert r.status_code == 200, r.text          # still never raises
    body = r.json()

    assert body["count"] == 0
    assert body["kb_present"] is None
    assert body["confidence"]["reason"] == "unavailable"
    assert body["confidence"]["level"] == "none"


def test_a_blank_query_is_unavailable_rather_than_an_empty_kb(api, kb_tenant, axis0_query):
    """Same wrong copy from the other direction: nothing was searched for, so nothing can be
    concluded about the KB. (`retrieve()` returned `[]` here, and the UI showed an unexplained
    empty list — bad, but not actively misleading.)"""
    tenant = kb_tenant(REPORTED_SCORES, label="blank")

    body = _search(api, tenant, "   ").json()

    assert body["count"] == 0
    assert body["kb_present"] is None
    assert body["confidence"]["reason"] == "unavailable"


# --- 15. the operator console and the tenant agree about one query ----------- #
def test_the_console_and_the_search_box_report_the_same_diagnosis(api, kb_tenant, axis0_query):
    """One tenant, one query, one instant, two surfaces that deliberately show different
    numbers of rows: `/kb/search` applies the absolute floor, `/kb/playground` shows the raw
    ranking (`threshold=0.0`). Measuring spread top-to-last made them disagree — 0.037 vs
    0.079, `flat_distribution` vs `low_score` — so the operator working the ticket read a
    different diagnosis than the tenant who filed it. The verdict is now measured over a
    bounded window of the shared ranking, so only the row counts differ.
    """
    tenant = kb_tenant([0.397, 0.386, 0.371, 0.360, 0.345, 0.330, 0.318], label="console")

    search = _search(api, tenant, "greeting", top_k=8).json()
    console = _playground(api, tenant, "greeting", top_k=8).json()

    assert len(search["results"]) == 4        # SIM_THRESHOLD = 0.35
    assert len(console["results"]) == 7       # deliberately unfiltered
    assert search["confidence"] == console["confidence"]
    assert search["confidence"]["reason"] == "flat_distribution"
    assert search["confidence"]["spread"] == 0.037


# --- 16. top_k is bounded in the contract, not silently clamped -------------- #
@pytest.mark.parametrize("top_k", [0, -3, 60])
def test_an_out_of_range_top_k_is_rejected_rather_than_quietly_reinterpreted(
        api, kb_tenant, axis0_query, top_k):
    """`retrieve_ranked` clamps to 1..50 for the chat path. Letting that clamp apply silently to
    a partner route means a client asking for 60 gets 50 and one asking for 0 gets 1, with
    nothing in the response to say so. A 422 naming the bound is the only version a client can
    detect and correct."""
    tenant = kb_tenant([0.92], label=f"topk{top_k}".replace("-", "n"))

    assert _search(api, tenant, "greeting", top_k=top_k).status_code == 422
    # ...and the whole legal range still works.
    assert _search(api, tenant, "greeting", top_k=retrieval.MAX_TOP_K).status_code == 200
