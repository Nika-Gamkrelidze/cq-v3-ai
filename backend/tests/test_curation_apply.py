"""The regression guard for the single worst bug available in the curation design.

`apply_accepted` is the only automated writer into a tenant's knowledge base. Everything it can
get wrong is silent: the queue still empties, the card still says "accepted", the activity log
still fills up. Four of those silent failures are pinned here.

**1. An accepted `update` must actually change retrieval.**
`kb_ingest.reembed_document` is the tempting call — it is right there, it is named for this, and
its signature fits. It only runs `UPDATE kb_chunks SET embedding = …`; its own docstring says it
*"reuses existing chunk content, only replaces vectors."* It never reads `content_text` and never
re-chunks. Applying an update through it rewrites the document row, leaves the retrievable text
**bit-for-bit unchanged**, and the next night's miner re-detects the identical gap — forever, at
one Claude call per night. The correct call is `kb_ingest.ingest_document`, which re-chunks and
re-embeds. `test_accepted_update_rechunks_the_document_not_just_its_vectors` fails loudly if
anyone "simplifies" this back.

**2. A failed ingest must not be reported as a success.**
`ingest_document` swallows every exception: it sets the document to `status='error'`, logs, and
returns `None`. An `await` that returns normally therefore says nothing about whether anything
was written. apply must re-read `kb_documents.status` and land the proposal in `apply_failed`.

**3. Cross-tenant evidence must abort before any write.**
`curation_evidence.source_client_id` exists solely so this is detectable. If it disagrees with
the proposal's `client_id`, one tenant's text is about to be written into another's KB with a
human approval record attached — the worst outcome this system can produce.

**4. `remove` is never a hard delete from an automated pathway.**
It sets `visibility='internal'` and surfaces a human delete candidate. Chunks stay.

Mechanics (pool swapping, the stub encoder, skipping without a database) are inherited from
`conftest.py` and mirrored from `test_curation.py`; see those docstrings.
"""
import asyncio
import hashlib
import uuid
from pathlib import Path

import asyncpg
import pytest

from conftest import sql  # loop-independent standalone SQL; see its module docstring

from app.config import settings  # noqa: E402 — conftest performed the sys.path bootstrap

CURATION_DIR = Path(__file__).resolve().parent.parent / "app" / "services" / "curation"

pytestmark = pytest.mark.skipif(
    not CURATION_DIR.exists(),
    reason="app/services/curation/ has not landed yet (owned by another track)")


OLD_MARK = "OLD_POLICY_REFUND_WINDOW_IS_7_DAYS"
NEW_MARK = "NEW_POLICY_REFUND_WINDOW_IS_14_DAYS"
EDIT_MARK = "HUMAN_EDITED_REFUND_WINDOW_IS_30_DAYS"
MODEL_MARK = "MODEL_WROTE_REFUND_WINDOW_IS_21_DAYS"

OLD_TEXT = f"{OLD_MARK}. Refunds are processed within seven days."
# Long enough to chunk into several pieces: that is what proves a re-chunk happened rather than
# a vector-only refresh (kb_ingest.CHUNK_SIZE is 1000 characters).
NEW_TEXT = (f"{NEW_MARK}. " + "Refunds are processed within fourteen days of the request. " * 60)
EDITED_TEXT = (f"{EDIT_MARK}. " + "Refunds are processed within thirty days. " * 60)
MODEL_TEXT = (f"{MODEL_MARK}. " + "Refunds are processed within twenty-one days. " * 60)


# --------------------------------------------------------------------------- #
# Loop-bound pool + stub encoder (same reasoning as test_curation.py)
# --------------------------------------------------------------------------- #
def run_async(factory):
    from app import db as appdb

    async def _run():
        pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=4)
        previous = appdb._pool
        appdb._pool = pool
        try:
            return await factory()
        finally:
            appdb._pool = previous
            await pool.close()

    return asyncio.run(_run())


def _vector_dim() -> int:
    dim = sql(lambda c: c.fetchval(
        """SELECT a.atttypmod FROM pg_attribute a
             JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'kb_chunks' AND a.attname = 'embedding'"""))
    return int(dim) if dim and dim > 0 else 1024


@pytest.fixture
def embedder(api, monkeypatch):
    """A cheap deterministic encoder at the live column dimension.

    Vectors must be dimension-correct here (unlike the clustering tests) because these chunks
    are really written to pgvector.
    """
    from app.services import embeddings

    dim = _vector_dim()

    async def _embed(texts, *, purpose: str = "ingest"):
        out = []
        for text in texts:
            vec = [0.0] * dim
            seed = int(hashlib.sha1((text or "").encode()).hexdigest()[:8], 16)
            vec[seed % dim] = 1.0
            out.append(vec)
        return out

    monkeypatch.setattr(embeddings, "embed_texts", _embed)
    return _embed


@pytest.fixture
def tenant(api):
    created: list[uuid.UUID] = []

    def _make(label: str = "t") -> str:
        suffix = uuid.uuid4().hex[:8]
        cid = sql(lambda c: c.fetchval(
            "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
            f"apply-{label}-{suffix}", f"apply-{label}", f"applykey-{suffix}"))
        created.append(cid)
        return str(cid)

    yield _make
    for cid in created:
        sql(lambda c, cid=cid: c.execute("DELETE FROM clients WHERE id = $1", cid))


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
# Refreshed from the live column by the autouse `_load_dim` fixture below.
_DIM = 1024


def add_document(client_id: str, text: str = OLD_TEXT, *, visibility: str = "public") -> str:
    """A ready document with exactly one chunk, written the way ingestion leaves it."""
    async def _run(conn):
        doc = await conn.fetchval(
            """INSERT INTO kb_documents
                   (client_id, doc_type, title, status, visibility, source_type,
                    content_text, chunk_count, checksum)
               VALUES ($1,'policy','Refund policy','ready',$2,'paste',$3,1,$4) RETURNING id""",
            uuid.UUID(client_id), visibility, text,
            hashlib.md5(text.encode()).hexdigest())
        vec = "[" + ",".join(["0.0"] * (_DIM - 1) + ["1.0"]) + "]"
        await conn.execute(
            """INSERT INTO kb_chunks (document_id, client_id, content, embedding, chunk_index)
               VALUES ($1,$2,$3,$4::vector,0)""",
            doc, uuid.UUID(client_id), text, vec)
        return doc
    return str(sql(_run))


def add_proposal(client_id: str, *, op: str = "update", content: str = NEW_TEXT,
                 document_id: str | None = None, title: str = "Refund window is 14 days",
                 state: str = "accepted") -> str:
    """Seed a card. The default is `accepted`, NOT `pending`, and that is load-bearing.

    `apply.apply_accepted()` claims atomically on `state = 'accepted'` — by design, so the
    worker's 5 s poll can never apply a card no human decided. A fixture seeding `pending` and
    then calling it directly asserts against a row nothing ever touched: every assertion about
    chunks, visibility and audit rows would pass or fail for the wrong reason. Tests that drive
    the *review* entry points (accept/decline) pass `state="pending"` explicitly.
    """
    async def _run(conn):
        return await conn.fetchval(
            """INSERT INTO curation_proposals
                   (client_id, op, state, signal, cluster_key, cluster_medoid, title,
                    proposed_content, rationale, target_document_id, suggested_tags,
                    occurrences, distinct_sources, confidence, priority, risk)
               VALUES ($1,$2,$3,'S1',$4,$5,$6,$7,'customers kept asking',$8,
                       ARRAY['curated'],4,2,0.9,10,'low') RETURNING id""",
            uuid.UUID(client_id), op, state, uuid.uuid4().hex,
            "how do I get my money back?", title, content,
            uuid.UUID(document_id) if document_id else None)
    return str(sql(_run))


def add_evidence(client_id: str, proposal_id: str, *, source_client_id: str | None = None,
                 question: str = "how do I get my money back?") -> str:
    async def _run(conn):
        return await conn.fetchval(
            """INSERT INTO curation_evidence
                   (client_id, proposal_id, source_client_id, signal, source_kind, channel,
                    locale, question, occurred_at)
               VALUES ($1,$2,$3,'S1','chat_turn','web','ka',$4, now()) RETURNING id""",
            uuid.UUID(client_id), uuid.UUID(proposal_id),
            uuid.UUID(source_client_id or client_id), question)
    return str(sql(_run))


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _load_dim(api):
    global _DIM
    _DIM = _vector_dim()


def chunks_of(document_id: str) -> list[dict]:
    return [dict(r) for r in sql(lambda c: c.fetch(
        "SELECT * FROM kb_chunks WHERE document_id = $1 ORDER BY chunk_index",
        uuid.UUID(document_id)))]


def document(document_id: str) -> dict | None:
    row = sql(lambda c: c.fetchrow(
        "SELECT * FROM kb_documents WHERE id = $1", uuid.UUID(document_id)))
    return dict(row) if row else None


def proposal(proposal_id: str) -> dict:
    return dict(sql(lambda c: c.fetchrow(
        "SELECT * FROM curation_proposals WHERE id = $1", uuid.UUID(proposal_id))))


def kb_event_count(client_id: str) -> int:
    return sql(lambda c: c.fetchval(
        "SELECT count(*) FROM kb_events WHERE client_id = $1", uuid.UUID(client_id)))


def find_callable(*names: str):
    """First matching public callable across the curation package, or None.

    The accept/decline *entry points* live in a sibling track's router+store; only
    `apply_accepted` is a fixed contract here. Rather than pin a name this file does not own,
    resolve what exists and skip with an explicit reason when it does not — the same posture
    `conftest.require_chat_route` takes for the chat router.
    """
    import importlib

    for module_name in ("apply", "runner", "store", "review", "propose"):
        try:
            module = importlib.import_module(f"app.services.curation.{module_name}")
        except ImportError:
            continue
        for name in names:
            fn = getattr(module, name, None)
            if callable(fn):
                return fn
    return None


def call_flexibly(fn, positional: list, keywords: dict):
    """Call `fn` with only the keyword arguments it actually declares.

    Same reasoning as `find_callable`: this file must assert a *behaviour* owned by another
    track without freezing that track's signature. Returns None if the shape cannot be matched,
    which the caller treats as "not landed yet".
    """
    import inspect

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return None
    accepted = {k: v for k, v in keywords.items() if k in params}
    try:
        return fn(*positional, **accepted)
    except TypeError:
        return None


# --------------------------------------------------------------------------- #
# 1. THE one. Do not weaken this test.
# --------------------------------------------------------------------------- #
def test_accepted_update_rechunks_the_document_not_just_its_vectors(tenant, embedder):
    """An accepted `update` must change what retrieval returns — not only the document row.

    Concretely: after apply, `kb_chunks` must hold the NEW text, split into the number of chunks
    the new text derives, and the OLD text must be gone. `reembed_document` would leave one
    chunk containing OLD_TEXT while every other observable (document row, proposal state,
    activity log) looked exactly like success — and the miner would raise the same gap again
    every night, at one Claude call per night, forever.
    """
    from app.services import kb_ingest
    from app.services.curation import apply as apply_mod

    client_id = tenant("update")
    doc = add_document(client_id)
    pid = add_proposal(client_id, op="update", content=NEW_TEXT, document_id=doc)
    add_evidence(client_id, pid)

    before = chunks_of(doc)
    assert len(before) == 1 and OLD_MARK in before[0]["content"]

    run_async(lambda: apply_mod.apply_accepted(pid))

    after = chunks_of(doc)
    expected = kb_ingest.chunk_text(NEW_TEXT)
    assert len(expected) > 1, "fixture problem: NEW_TEXT must be long enough to re-chunk"

    joined = " ".join(c["content"] for c in after)
    assert OLD_MARK not in joined, (
        "the old text is still retrievable after an accepted update — this is the "
        "reembed_document bug: vectors were replaced but the chunk content was not re-derived")
    assert NEW_MARK in joined, "the accepted content never reached kb_chunks"
    assert len(after) == len(expected), (
        f"chunk count did not re-derive from the new text ({len(after)} != {len(expected)}); "
        "apply must call kb_ingest.ingest_document, which re-chunks and re-embeds")
    assert [c["chunk_index"] for c in after] == list(range(len(after)))

    doc_row = document(doc)
    assert doc_row["status"] == "ready"
    assert doc_row["chunk_count"] == len(after)
    assert NEW_MARK in (doc_row["content_text"] or "")

    row = proposal(pid)
    assert row["state"] == "accepted"
    assert str(row["applied_document_id"]) == doc
    assert row["applied_at"] is not None


# --------------------------------------------------------------------------- #
# 2. A swallowed failure is still a failure
# --------------------------------------------------------------------------- #
def test_failed_ingest_lands_the_proposal_in_apply_failed(tenant, embedder, monkeypatch):
    """`ingest_document` returns None on success AND on failure; apply must re-read the status.

    Without the re-read, the reviewer is told the gap is closed while the document sits in
    `status='error'` with no chunks — the worst kind of green.
    """
    from app.services import embeddings
    from app.services.curation import apply as apply_mod

    client_id = tenant("failing")
    doc = add_document(client_id)
    pid = add_proposal(client_id, op="update", content=NEW_TEXT, document_id=doc)
    add_evidence(client_id, pid)

    async def _explode(texts, *, purpose: str = "ingest"):
        raise RuntimeError("embedding backend is down")

    monkeypatch.setattr(embeddings, "embed_texts", _explode)

    try:
        run_async(lambda: apply_mod.apply_accepted(pid))
    except Exception:  # noqa: BLE001 — raising is an acceptable contract; the state is not
        pass

    assert document(doc)["status"] == "error", "fixture problem: the ingest did not fail"
    row = proposal(pid)
    assert row["state"] == "apply_failed", (
        f"a failed ingest left the proposal in {row['state']!r}; ingest_document swallows its "
        "own exceptions, so apply MUST reconcile against kb_documents.status")
    assert row["applied_at"] is None
    assert (row["apply_error"] or "").strip(), "apply_failed must carry a reason for the human"


# --------------------------------------------------------------------------- #
# 3. Cross-tenant evidence stops everything
# --------------------------------------------------------------------------- #
def test_cross_tenant_evidence_aborts_before_any_kb_write(tenant, embedder):
    """Evidence owned by another tenant must abort the apply with the KB untouched.

    This is the one moment a human is looking, which is exactly why the check lives here: an
    approved cross-tenant write is permanent and carries a review record vouching for it.
    """
    from app.services.curation import apply as apply_mod

    victim = tenant("victim")
    other = tenant("other")
    doc = add_document(victim)
    pid = add_proposal(victim, op="update", content=NEW_TEXT, document_id=doc)
    add_evidence(victim, pid, source_client_id=other, question="another tenant's question")

    before = chunks_of(doc)

    try:
        run_async(lambda: apply_mod.apply_accepted(pid))
    except Exception:  # noqa: BLE001 — refusing by raising is fine; writing anyway is not
        pass

    after = chunks_of(doc)
    assert [c["content"] for c in after] == [c["content"] for c in before], (
        "a proposal carrying evidence from another tenant was applied to the KB")
    assert OLD_MARK in after[0]["content"] and NEW_MARK not in after[0]["content"]
    assert document(doc)["content_text"] == OLD_TEXT
    row = proposal(pid)
    assert row["state"] != "accepted", (
        f"the proposal was marked {row['state']!r}; a cross-tenant apply must never record "
        "itself as an accepted, reviewed change")


# --------------------------------------------------------------------------- #
# 4. `remove` is not a delete
# --------------------------------------------------------------------------- #
def test_remove_hides_the_document_and_never_hard_deletes(tenant, embedder):
    """No automated pathway deletes tenant data. `remove` demotes visibility; a human decides.

    The chunks stay too: they are the evidence for the human's delete decision, and a wrong
    `remove` must be one UPDATE away from being undone.
    """
    from app.services.curation import apply as apply_mod

    client_id = tenant("remove")
    doc = add_document(client_id)
    pid = add_proposal(client_id, op="remove", content="superseded by the 14-day policy",
                       document_id=doc)
    add_evidence(client_id, pid)

    before = chunks_of(doc)
    run_async(lambda: apply_mod.apply_accepted(pid))

    row = document(doc)
    assert row is not None, "an automated `remove` hard-deleted a tenant's document"
    assert row["visibility"] == "internal", (
        f"`remove` must demote visibility to 'internal', found {row['visibility']!r}")
    after = chunks_of(doc)
    assert [c["content"] for c in after] == [c["content"] for c in before], (
        "`remove` deleted the chunks; they are the evidence for the human delete decision")
    assert proposal(pid)["state"] == "accepted"


# --------------------------------------------------------------------------- #
# 5. Accept-with-edits
# --------------------------------------------------------------------------- #
def test_accept_with_edits_writes_the_humans_text_not_the_models(tenant, embedder):
    """Whatever the reviewer typed is what enters the KB.

    Accept-with-edits is the same endpoint as accept with a `content` body, so the edited text
    has to reach `proposed_content` and from there the chunks. If apply re-read the model's
    original from anywhere, the reviewer's correction would be silently discarded — and the
    card would still say accepted.
    """
    from app.services.curation import apply as apply_mod

    client_id = tenant("edits")
    doc = add_document(client_id)
    pid = add_proposal(client_id, op="update", content=MODEL_TEXT, document_id=doc)
    add_evidence(client_id, pid)

    accept = find_callable("accept_proposal", "accept")

    async def _via_entry_point():
        coro = call_flexibly(accept, [client_id, pid],
                             {"content": EDITED_TEXT, "reviewer": "tester",
                              "reviewed_by": "tester", "actor": "tester"})
        if coro is None:
            return False
        await coro
        return True

    if accept is None or not run_async(_via_entry_point):
        # No accept entry point in this package yet (it lands with the router track): emulate
        # what it stores, so the property under test — the reviewer's text is what reaches
        # retrieval — is still asserted end to end.
        sql(lambda c: c.execute(
            "UPDATE curation_proposals SET proposed_content = $2 WHERE id = $1",
            uuid.UUID(pid), EDITED_TEXT))
        run_async(lambda: apply_mod.apply_accepted(pid))

    joined = " ".join(c["content"] for c in chunks_of(doc))
    assert EDIT_MARK in joined, "the reviewer's edited text never reached the knowledge base"
    assert MODEL_MARK not in joined, (
        "the model's original proposal was written instead of the reviewer's edit")
    assert EDIT_MARK in (document(doc)["content_text"] or "")


# --------------------------------------------------------------------------- #
# 6. The activity timeline covers curation
# --------------------------------------------------------------------------- #
def test_accept_writes_a_kb_events_row(tenant, embedder):
    """Curation gets its audit trail from the existing KB activity log — zero new UI.

    An automated writer with no entry in the timeline is an unexplainable KB change: the
    operator console shows content nobody can account for.
    """
    from app.services.curation import apply as apply_mod

    client_id = tenant("events")
    doc = add_document(client_id)
    pid = add_proposal(client_id, op="update", content=NEW_TEXT, document_id=doc)
    add_evidence(client_id, pid)

    before = kb_event_count(client_id)
    run_async(lambda: apply_mod.apply_accepted(pid))

    assert kb_event_count(client_id) > before, (
        "accepting a proposal wrote nothing to kb_events; the KB activity timeline is the "
        "audit trail for every KB change, including the automated ones")


def test_decline_writes_a_kb_events_row(tenant, embedder):
    """A decline is a curation decision about the KB and belongs in the same timeline.

    It is also what feeds `curation_suppressions`, so an unlogged decline is a decision with no
    record of who made it or why.
    """
    decline = find_callable("record_decline", "decline_proposal", "decline")
    if decline is None:
        pytest.skip("no decline entry point in app/services/curation/ yet (sibling track)")

    client_id = tenant("decline")
    doc = add_document(client_id)
    # A decline is a decision on an UNdecided card: `record_decline` returns the current state
    # untouched for anything other than 'pending'.
    pid = add_proposal(client_id, op="update", content=NEW_TEXT, document_id=doc,
                       state="pending")
    add_evidence(client_id, pid)

    before = kb_event_count(client_id)

    async def _decline():
        coro = call_flexibly(decline, [client_id, pid],
                             {"reason": "conflicts with the printed policy",
                              "decline_reason": "conflicts with the printed policy",
                              "reviewer": "tester", "reviewed_by": "tester",
                              "actor": "tester"})
        if coro is None:
            return False
        await coro
        return True

    if not run_async(_decline):
        pytest.skip(f"{decline.__name__}() does not accept the reviewer/reason shape yet")

    assert proposal(pid)["state"] == "declined"
    assert kb_event_count(client_id) > before, (
        "declining a proposal wrote nothing to kb_events")


# --------------------------------------------------------------------------- #
# 7. The unused-argument trap
# --------------------------------------------------------------------------- #
def test_added_document_records_its_source_type(tenant, embedder):
    """`ingest_document`'s `source_type` argument is never used in its body.

    The column is written by the document INSERT, so an `add` that relies on passing it through
    leaves `source_type` NULL — and the KB console can no longer tell curated content from an
    operator's upload, which is the one filter a reviewer wants after a bad night.
    """
    from app.services.curation import apply as apply_mod

    client_id = tenant("add")
    pid = add_proposal(client_id, op="add", content=NEW_TEXT, document_id=None)
    add_evidence(client_id, pid)

    run_async(lambda: apply_mod.apply_accepted(pid))

    row = proposal(pid)
    assert row["state"] == "accepted", f"the add was not applied: {row['apply_error']!r}"
    assert row["applied_document_id"], "an accepted `add` must record the document it created"
    created = document(str(row["applied_document_id"]))
    assert created["client_id"] == uuid.UUID(client_id)
    assert (created["source_type"] or "").strip(), (
        "source_type is NULL on a curated document — it must be set on the INSERT, because "
        "kb_ingest.ingest_document ignores the argument of the same name")
    assert NEW_MARK in " ".join(c["content"] for c in chunks_of(str(row["applied_document_id"])))
