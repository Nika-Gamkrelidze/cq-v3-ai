"""Guards for the curation loop's cost, safety and resume properties.

The curation loop is the only part of this system that spends money on its own, without a user
waiting for a result. Every property asserted here is one where the loop keeps *working* while
being wrong — a green pipeline, a plausible queue, and either a bill or a leak underneath:

  * **Cost.** One Claude call per *cluster* is the entire economic argument of the design. A
    refactor that calls per candidate is invisible in the UI (the same 6-10 cards appear) and
    shows up only on the invoice, multiplied by message volume. `test_one_llm_call_per_cluster`
    pins the ratio.
  * **Suppression must be semantic.** Declining a card has to cost tokens exactly once. A
    content hash looks like it works — until the same question arrives reworded, in Georgian,
    with a typo, which is what conversational traffic *is*. So the test declines one phrasing
    and re-raises a different one, and asserts the model was never called.
  * **Poisoning.** `distinct_sources >= 2` is what stops one persistent troll from writing the
    tenant's knowledge base. It is bypassed on purpose for operator corrections and fact-check
    verdicts (a human or the KB itself is the source there), and that bypass is exactly the
    hole someone would widen "for consistency".
  * **Isolation.** The miner reads four tables that all carry `client_id`, and a single missing
    filter here is worse than anywhere else in the codebase: the loop would launder tenant B's
    text into tenant A's KB *with a human approval record attached*. Asserted per signal.
  * **Resume.** Every push to `main` auto-deploys and restarts the stack, so a run dying
    mid-flight is routine, not theoretical. The day must resume from the watermark, not be
    skipped forever.

Two mechanical notes, both inherited from `conftest.py`:

  1. **The embedder is a stub with hand-picked vectors.** Clustering assertions here are about
     *the clustering code*, not about BGE-M3's cross-lingual quality — a real-model test would
     be a flaky model benchmark. Each seeded question carries a short ASCII concept marker that
     the stub keys on; the Georgian/Russian text beside it is what the assertion is *about*.
  2. **The pool is swapped, not shared.** An asyncpg pool belongs to the loop that created it,
     and the app's pool lives in the TestClient's portal thread. Curation code calls
     `db.pool()`, so `run_async` points that module global at a pool created inside the same
     `asyncio.run` as the work, and restores it afterwards.
"""
import asyncio
import datetime as dt
import hashlib
import json
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


# --------------------------------------------------------------------------- #
# The day under test. Noon UTC sits inside the tenant-day window for every
# plausible server timezone, so the fixtures never straddle a boundary.
# --------------------------------------------------------------------------- #
DAY = dt.date.today() - dt.timedelta(days=1)


def _at(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=dt.timezone.utc)


WINDOW_START = _at(0)
WINDOW_END = _at(0) + dt.timedelta(days=1)


# --------------------------------------------------------------------------- #
# Questions. `_q` appends a concept marker the stub encoder keys on — see note 1
# in the module docstring. Same marker == same vector == cosine 1.0.
# --------------------------------------------------------------------------- #
def _q(concept: str, text: str) -> str:
    return f"{text} [{concept}]"


KA_REFUND = _q("qrefund", "როგორ დავიბრუნო თანხა?")
RU_REFUND = _q("qrefund", "как вернуть деньги за заказ?")
EN_REFUND = _q("qrefund", "how do I get my money back?")
RU_REFUND_REWORDED = _q("qrefund", "можно ли оформить возврат средств?")
RU_HOURS = _q("qhours", "во сколько вы работаете в субботу?")
KA_DELIVERY = _q("qdelivery", "რამდენ ხანში ჩამოვა შეკვეთა?")


def _concept_of(text: str) -> str:
    """The concept marker inside `text`, or a per-text unique pseudo-concept."""
    lowered = (text or "").lower()
    for concept in ("qrefund", "qhours", "qdelivery"):
        if concept in lowered:
            return concept
    return "uniq:" + hashlib.sha1(lowered.encode()).hexdigest()[:12]


class ConceptEmbedder:
    """Deterministic stand-in for BGE-M3: one orthogonal basis vector per concept.

    Same concept -> identical vector -> cosine 1.0 (above both the 0.82 cluster threshold and
    the 0.88 suppression threshold). Different concepts -> orthogonal -> cosine 0.0. That makes
    every clustering assertion in this file exact instead of probabilistic.

    `batches` counts *calls*, which is how the "suppressed medoids are re-embedded in the SAME
    batch as the candidates" contract is asserted: doing it in a second call would double the
    encoder round-trips on the loop that also serves the copilot's read path.
    """

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.batches = 0
        self.texts: list[str] = []
        self._index: dict[str, int] = {}

    def _vector(self, text: str) -> list[float]:
        concept = _concept_of(text)
        if concept not in self._index:
            # +2 keeps these clear of index 0/1, which conftest's seed vectors occupy.
            self._index[concept] = (len(self._index) + 2) % self.dim
        vec = [0.0] * self.dim
        vec[self._index[concept]] = 1.0
        return vec

    async def __call__(self, texts, *, purpose: str = "ingest"):
        self.batches += 1
        self.texts.extend(texts)
        return [self._vector(t) for t in texts]


def _vector_dim() -> int:
    """The live `kb_chunks.embedding` dimension — the stub must match it, because propose()
    embeds the medoid and searches pgvector with the result."""
    try:
        dim = sql(lambda c: c.fetchval(
            """SELECT a.atttypmod FROM pg_attribute a
                 JOIN pg_class c ON c.oid = a.attrelid
                WHERE c.relname = 'kb_chunks' AND a.attname = 'embedding'"""))
        return int(dim) if dim and dim > 0 else 1024
    except Exception:  # noqa: BLE001 — no database: the pure-clustering tests still run
        return 1024


# --------------------------------------------------------------------------- #
# Running curation code against a pool bound to *this* loop
# --------------------------------------------------------------------------- #
def run_async(factory):
    """`await factory()` with `db.pool()` answering from a pool created in this call's loop.

    The module global is set and restored rather than monkeypatched because every service
    reaches the pool through `db.pool()` at call time; patching the function object would miss
    modules that did `from ..db import pool` (kb_ingest does exactly that).
    """
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


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def tenant(api):
    """Factory for throwaway tenants. Depends on `api` so the real lifespan has applied
    `curation.sql` — the schema comes from migrate.py, never from a fixture's own DDL."""
    created: list[uuid.UUID] = []

    def _make(label: str = "t") -> str:
        suffix = uuid.uuid4().hex[:8]
        cid = sql(lambda c: c.fetchval(
            "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
            f"cur-{label}-{suffix}", f"cur-{label}", f"curkey-{suffix}"))
        created.append(cid)
        return str(cid)

    yield _make
    for cid in created:
        sql(lambda c, cid=cid: c.execute("DELETE FROM clients WHERE id = $1", cid))


@pytest.fixture
def embedder(monkeypatch):
    """Install the concept encoder everywhere the loop might reach for one."""
    from app.services import embeddings

    stub = ConceptEmbedder(_vector_dim())
    monkeypatch.setattr(embeddings, "embed_texts", stub)
    return stub


@pytest.fixture
def llm_calls(monkeypatch):
    """Replace conftest's detonator with a counting stub that returns a valid proposal.

    conftest patches `llm.call_tool` to raise; this overrides it for the runner tests, which
    legitimately reach the synthesis step. The list it returns is the cost assertion.
    """
    from app.services import llm

    calls: list[dict] = []

    async def _call_tool(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        return {
            "op": "add",
            "title": f"Curated answer {n}",
            "proposed_content": f"CURATED_BODY_{n}. The refund window is 14 days.",
            "target_document_id": None,
            "target_chunk_id": None,
            "rationale": "Customers asked repeatedly and the bot could not ground an answer.",
            "confidence": 0.9,
            "risk": "low",
            "suggested_tags": ["curated"],
        }

    monkeypatch.setattr(llm, "call_tool", _call_tool)
    # A tenant with no configured key must still be able to reach the (stubbed) model, so the
    # suite stays runnable with ANTHROPIC_API_KEY unset. settings_store falls back to this.
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key-never-used", raising=False)
    return calls


# --------------------------------------------------------------------------- #
# Seeding helpers (all SQL, all tenant-scoped)
# --------------------------------------------------------------------------- #
def _column_exists(table: str, column: str) -> bool:
    return bool(sql(lambda c: c.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name=$2",
        table, column)))


def add_conversation(client_id: str, ref: str, *, channel: str = "web", locale: str = "ka",
                     mined_through: dt.datetime | None = None) -> str:
    async def _run(conn):
        return await conn.fetchval(
            """INSERT INTO chat_conversations
                   (client_id, external_ref, channel, locale, mined_through, last_message_at)
               VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
            uuid.UUID(client_id), ref, channel, locale, mined_through, _at(23))
    return str(sql(_run))


def add_turn(client_id: str, conversation_id: str, content: str, *, role: str = "customer",
             grounded: bool = False, method: str | None = None,
             top_score: float | None = None, at: dt.datetime | None = None) -> str:
    async def _run(conn):
        return await conn.fetchval(
            """INSERT INTO chat_turns
                   (client_id, conversation_id, turn_ref, role, content, locale,
                    grounded, method, top_score, hit_count, created_at)
               VALUES ($1,$2,$3,$4,$5,'ka',$6,$7,$8,$9,$10) RETURNING id""",
            uuid.UUID(client_id), uuid.UUID(conversation_id), f"turn-{uuid.uuid4().hex[:10]}",
            role, content, grounded, method, top_score, 0 if not grounded else 3,
            at or _at(12))
    return str(sql(_run))


def add_failed_exchange(client_id: str, conversation_id: str, question: str, *,
                        at: dt.datetime | None = None, method: str | None = None,
                        top_score: float | None = None,
                        operator_reply: str | None = None) -> str:
    """A customer question followed by a bot turn that failed to answer it.

    THE thing this file kept getting wrong: `chat_turns.grounded` defaults to false and
    `append_turn` writes it for every role, so an inbound CUSTOMER message is stored ungrounded
    too — which is why the miner requires `role = 'bot'` for S1/S2 (its docstring calls the role
    filter load-bearing: without it every customer message in the system would be flagged as a KB
    gap). Seeding only customer turns therefore produced no S1/S2 candidates at all.

    `method`/`top_score` on the bot turn select S2 (weak retrieval) instead of S1 (refusal);
    `operator_reply` appends the human turn that makes it S4. Returns the bot turn's id.
    """
    start = at or _next_slot()
    add_turn(client_id, conversation_id, question, role="customer", at=start)
    bot = add_turn(client_id, conversation_id, "I could not find that in the knowledge base.",
                   role="bot", grounded=method is not None and method not in ("keyword", "none"),
                   method=method, top_score=top_score, at=start + dt.timedelta(seconds=30))
    if operator_reply:
        add_turn(client_id, conversation_id, operator_reply, role="operator",
                 at=start + dt.timedelta(seconds=60))
    return bot


# Distinct, ordered timestamps for seeded turns. `_TURNS_SQL` partitions each thread into
# question groups with a window ordered by (created_at, id) — identical timestamps would make the
# customer/bot pairing depend on random uuid ordering, and the fixtures would pass or fail by luck.
_SLOT = {"n": 0}


def _next_slot() -> dt.datetime:
    _SLOT["n"] += 1
    return _at(1) + dt.timedelta(minutes=_SLOT["n"])


def add_edited_feedback(client_id: str, conversation_id: str, turn_id: str,
                        question: str, final_text: str, *,
                        at: dt.datetime | None = None) -> str:
    """S3 — the gold signal: an operator rewrote the AI's answer before sending it."""
    # `edit_distance` is a promoted column in some revisions of chat.sql and a metadata key in
    # others; write whichever exists so this fixture does not pin the schema shape. Resolved
    # out here because `sql()` opens its own event loop and cannot nest inside `_run`.
    has_edit_distance = _column_exists("copilot_feedback", "edit_distance")

    async def _run(conn):
        suggestion_id = await conn.fetchval(
            """INSERT INTO copilot_suggestions
                   (client_id, conversation_id, turn_id, suggest_ref, state, suggestions,
                    created_at)
               VALUES ($1,$2,$3,$4,'ready',$5::jsonb,$6) RETURNING id""",
            uuid.UUID(client_id), uuid.UUID(conversation_id), uuid.UUID(turn_id),
            f"sg-{uuid.uuid4().hex[:10]}",
            json.dumps([{"index": 0, "kind": "answer", "text": "A vague AI answer."}]),
            at or _at(13))
        columns = ["client_id", "suggestion_id", "suggest_ref", "suggestion_index",
                   "action", "final_text", "operator_ref", "metadata", "created_at"]
        values = [uuid.UUID(client_id), suggestion_id, f"sg-{uuid.uuid4().hex[:10]}", 0,
                  "edited_sent", final_text, "op-1",
                  json.dumps({"edit_distance": 0.87, "question": question}), at or _at(13)]
        if has_edit_distance:
            columns.insert(-1, "edit_distance")
            values.insert(-1, 0.87)
        placeholders = ",".join(f"${i + 1}" for i in range(len(values)))
        return await conn.fetchval(
            f"INSERT INTO copilot_feedback ({','.join(columns)}) "
            f"VALUES ({placeholders}) RETURNING id",
            *values)
    return str(sql(_run))


def add_audio_job(client_id: str, question: str, verdict: str, *,
                  at: dt.datetime | None = None) -> str:
    """S5/S6 — a fact-check verdict on a call. Shape mirrors services/factcheck.py's output."""
    kb_check = {
        "accuracy_score": 50,
        "counts": {"supported": 1, "contradicted": 1 if verdict == "CONTRADICTED" else 0,
                   "not_in_kb": 1 if verdict == "NOT_IN_KB" else 0},
        "claims": [{"claim": question, "speaker": "agent", "category": "policy",
                    "verdict": verdict, "rationale": "Nothing in the KB covers this.",
                    "confidence": 0.8, "evidence": None}],
        "contradicted": [question] if verdict == "CONTRADICTED" else [],
    }

    async def _run(conn):
        return await conn.fetchval(
            """INSERT INTO audio_jobs
                   (client_id, principal_type, status, transcript, kb_check, created_at)
               VALUES ($1,'tenant','done',$2,$3::jsonb,$4) RETURNING id""",
            uuid.UUID(client_id), f"agent: {question}", json.dumps(kb_check), at or _at(14))
    return str(sql(_run))


def suppress(client_id: str, medoid: str, *, reason: str = "not our policy") -> None:
    key = hashlib.sha1(medoid.strip().lower().encode()).hexdigest()
    sql(lambda c: c.execute(
        """INSERT INTO curation_suppressions (client_id, cluster_key, medoid, reason, created_by)
           VALUES ($1,$2,$3,$4,'test') ON CONFLICT (client_id, cluster_key) DO NOTHING""",
        uuid.UUID(client_id), key, medoid, reason))


def proposals_of(client_id: str, state: str | None = None) -> list[dict]:
    q = "SELECT * FROM curation_proposals WHERE client_id = $1"
    args = [uuid.UUID(client_id)]
    if state:
        q += " AND state = $2"
        args.append(state)
    return [dict(r) for r in sql(lambda c: c.fetch(q + " ORDER BY created_at", *args))]


def run_row(client_id: str) -> dict | None:
    row = sql(lambda c: c.fetchrow(
        "SELECT * FROM curation_runs WHERE client_id = $1 AND day = $2",
        uuid.UUID(client_id), DAY))
    return dict(row) if row else None


def candidate(client_id: str, question: str, *, signal: str = "S1",
              source_id: str | None = None, conversation_id: str | None = None):
    """A miner Candidate as the miner really builds one.

    `conversation_id` goes in `metadata`, NOT on the dataclass — `Candidate` has no such field,
    and that is exactly where the poisoning defence reads it from. A helper that omitted it would
    make every candidate its own source and silently disarm `distinct_sources`.
    """
    from app.services.curation.miner import Candidate

    return Candidate(
        client_id=client_id, signal=signal, question=question, answer=None,
        source_kind="chat_turn", source_id=source_id or str(uuid.uuid4()),
        channel="web", occurred_at=_at(12),
        metadata={"conversation_id": conversation_id or str(uuid.uuid4())})


# --------------------------------------------------------------------------- #
# 1. Clustering is cross-lingual
# --------------------------------------------------------------------------- #
def test_georgian_and_russian_phrasings_land_in_one_cluster(embedder):
    """One question asked in three languages must be ONE card, not three.

    This is the property that makes the queue readable for a Georgian support team, and it is
    also the one that quietly regresses if someone adds a language or locale filter to the
    clustering step "to reduce noise". The encoder here is a stub, so this asserts the greedy
    medoid grouping — not the real model's multilingual quality.
    """
    from app.services.curation import cluster as cluster_mod

    # Two sources per question: `cluster()` also enforces the poisoning floor, so a
    # single-source control question would be dropped for a reason that has nothing to do with
    # the grouping this test is about.
    cands = [candidate("c", KA_REFUND), candidate("c", RU_REFUND),
             candidate("c", EN_REFUND), candidate("c", RU_HOURS), candidate("c", RU_HOURS)]

    clusters = asyncio.run(cluster_mod.cluster(cands, []))

    by_concept = {_concept_of(cl.medoid): cl for cl in clusters}
    assert set(by_concept) == {"qrefund", "qhours"}, (
        "expected exactly two clusters (one refund question in 3 languages + one unrelated), "
        f"got medoids {[cl.medoid for cl in clusters]}")
    refund = by_concept["qrefund"]
    assert refund.occurrences == 3
    assert {c.question for c in refund.candidates} == {KA_REFUND, RU_REFUND, EN_REFUND}
    assert embedder.batches == 1, (
        "all candidates must be embedded in ONE batch; per-candidate calls would put the day's "
        "whole volume through the encoder one round-trip at a time")


# --------------------------------------------------------------------------- #
# 2. Suppression is semantic, and free
# --------------------------------------------------------------------------- #
def test_declining_suppresses_a_rewording_and_costs_no_tokens(tenant, embedder, llm_calls):
    """A declined question, asked again in different words, must not reach the model.

    A content hash passes a naive version of this test and fails in production on the first
    typo. The two strings below hash differently on purpose — that assertion is what makes the
    rest of the test meaningful.
    """
    client_id = tenant("supp")
    assert (hashlib.sha1(RU_REFUND.strip().lower().encode()).hexdigest()
            != hashlib.sha1(RU_REFUND_REWORDED.strip().lower().encode()).hexdigest()), (
        "the two phrasings must differ textually, otherwise this test would also pass "
        "against a content-hash implementation")

    suppress(client_id, RU_REFUND)

    conv_a = add_conversation(client_id, "supp-a")
    conv_b = add_conversation(client_id, "supp-b")
    add_failed_exchange(client_id, conv_a, RU_REFUND_REWORDED)
    add_failed_exchange(client_id, conv_b, KA_REFUND)

    from app.services.curation import runner

    run_async(lambda: runner.run_for_client(client_id, DAY))

    assert llm_calls == [], (
        "a suppressed cluster reached Claude — suppression must be applied BEFORE synthesis, "
        "and must match semantically rather than by content hash")
    assert proposals_of(client_id) == []
    assert embedder.batches >= 1, "the suppressed medoids still have to be embedded to compare"


# --------------------------------------------------------------------------- #
# 3. Poisoning defence
# --------------------------------------------------------------------------- #
def test_single_source_customer_cluster_is_not_proposed(tenant, embedder, llm_calls):
    """One conversation asking the same thing five times must not become a KB proposal.

    Without the floor, anyone who can open a chat can dictate the tenant's knowledge base by
    repeating themselves.
    """
    client_id = tenant("poison")
    conv = add_conversation(client_id, "poison-1")
    for _ in range(5):
        add_turn(client_id, conv, KA_REFUND)

    from app.services.curation import runner

    run_async(lambda: runner.run_for_client(client_id, DAY))

    assert llm_calls == [], "a single-source customer cluster must not be synthesised"
    assert proposals_of(client_id) == []


def test_operator_correction_bypasses_the_distinct_sources_floor(tenant, embedder, llm_calls):
    """S3 is exempt on purpose: the source is a human employee who wrote the right answer.

    Requiring two of those would throw away the highest-precision signal in the system.
    """
    client_id = tenant("gold")
    conv = add_conversation(client_id, "gold-1")
    turn = add_turn(client_id, conv, KA_REFUND)
    add_edited_feedback(client_id, conv, turn, KA_REFUND,
                        "Refunds are processed within 14 days of the request.")

    from app.services.curation import runner

    run_async(lambda: runner.run_for_client(client_id, DAY))

    pending = proposals_of(client_id, "pending")
    assert len(pending) == 1, (
        "an operator correction from a single conversation must still produce a card; "
        f"got {len(pending)}")
    assert pending[0]["distinct_sources"] <= 1
    assert len(llm_calls) == 1


# --------------------------------------------------------------------------- #
# 4. Cost: one call per cluster, not per candidate
# --------------------------------------------------------------------------- #
def test_one_llm_call_per_cluster_not_per_candidate(tenant, embedder, llm_calls):
    """20 failures, 3 distinct questions -> exactly 3 Claude calls.

    If this ever reads 20, the loop still produces the same queue and the same cards — the only
    visible difference is the bill, scaled by message volume instead of by question count.
    """
    client_id = tenant("cost")
    conv_a = add_conversation(client_id, "cost-a")
    conv_b = add_conversation(client_id, "cost-b")
    plan = [(KA_REFUND, 4), (RU_REFUND, 4), (RU_HOURS, 7), (KA_DELIVERY, 5)]
    for text, count in plan:
        for i in range(count):
            # A full exchange (customer question + ungrounded BOT reply), not a bare customer
            # turn: the miner's role filter is load-bearing and a customer message alone is
            # never a candidate. See add_failed_exchange's docstring.
            add_failed_exchange(client_id, conv_a if i % 2 == 0 else conv_b, text)

    from app.services.curation import runner

    run_async(lambda: runner.run_for_client(client_id, DAY))

    assert len(llm_calls) == 3, (
        "expected one synthesis call per cluster (refund/hours/delivery), got "
        f"{len(llm_calls)} for 20 candidates")
    row = run_row(client_id)
    assert row["candidates"] == 20
    assert row["clusters"] == 3
    assert row["llm_calls"] == 3
    assert row["proposals"] == 3
    assert row["state"] == "done"
    assert len(proposals_of(client_id, "pending")) == 3


# --------------------------------------------------------------------------- #
# 5. Don't drown the reviewer
# --------------------------------------------------------------------------- #
def test_ten_open_proposals_is_a_hard_cap(tenant, embedder, llm_calls):
    """A queue nobody can finish is a queue nobody opens. Ten cards is the whole product."""
    client_id = tenant("cap")

    async def _fill(conn):
        for i in range(10):
            await conn.execute(
                """INSERT INTO curation_proposals
                       (client_id, op, state, cluster_key, cluster_medoid, title,
                        proposed_content, occurrences, distinct_sources, priority)
                   VALUES ($1,'add','pending',$2,$3,$4,'body',3,2,$5)""",
                uuid.UUID(client_id), f"pre-existing-{i}", f"pre-existing medoid {i}",
                f"Pre-existing card {i}", float(100 - i))
    sql(_fill)

    conv_a = add_conversation(client_id, "cap-a")
    conv_b = add_conversation(client_id, "cap-b")
    for conv in (conv_a, conv_b):
        add_turn(client_id, conv, KA_REFUND)
        add_turn(client_id, conv, RU_HOURS)

    from app.services.curation import runner

    run_async(lambda: runner.run_for_client(client_id, DAY))

    pending = proposals_of(client_id, "pending")
    assert len(pending) == 10, (
        f"the open-proposal queue must stay capped at 10, found {len(pending)}")
    assert len(llm_calls) == 0, (
        "with the queue already full there is nothing to synthesise — the cap must be checked "
        "before spending tokens, not after")


# --------------------------------------------------------------------------- #
# 6. Tenant isolation, asserted per signal
# --------------------------------------------------------------------------- #
def test_miner_never_crosses_tenants(tenant, embedder):
    """Tenant A's miner must not see one row of tenant B, on any of the six signals.

    This is the highest-consequence assertion in the file. A leak here does not merely show one
    tenant another's text: the curation loop would carry it into A's knowledge base with an
    approval record saying a human reviewed it.
    """
    from app.services.curation import miner

    a, b = tenant("iso-a"), tenant("iso-b")
    marks = {}
    for label, client_id in (("A", a), ("B", b)):
        mark = f"MARK_{label}_{uuid.uuid4().hex[:6]}"
        marks[label] = mark
        conv1 = add_conversation(client_id, f"iso-{label}-1")
        conv2 = add_conversation(client_id, f"iso-{label}-2")
        # S1 — a bot turn that could not ground an answer. It has to be a full exchange:
        # the miner requires role = 'bot', so a lone customer message yields no candidate.
        add_failed_exchange(client_id, conv1, f"{KA_REFUND} {mark}")
        add_failed_exchange(client_id, conv2, f"{RU_REFUND} {mark}")
        # S2 — weak retrieval (keyword fallback / below min_score) on the bot turn.
        add_failed_exchange(client_id, conv1, f"{RU_HOURS} {mark}",
                            method="keyword", top_score=0.11)
        add_failed_exchange(client_id, conv2, f"{RU_HOURS} {mark}",
                            method="vector", top_score=0.05)
        # S3 — operator correction
        turn = add_turn(client_id, conv1, f"{KA_DELIVERY} {mark}")
        add_edited_feedback(client_id, conv1, turn, f"{KA_DELIVERY} {mark}",
                            f"Delivery takes 3 days. {mark}")
        # S4 — an operator answering right after a handoff/gate failure
        add_failed_exchange(client_id, conv2, f"{KA_DELIVERY} {mark}", at=_at(15),
                            operator_reply=f"Our courier delivers in 3 days. {mark}")
        # S5 / S6 — fact-check verdicts on a call
        add_audio_job(client_id, f"{EN_REFUND} {mark}", "NOT_IN_KB")
        add_audio_job(client_id, f"{EN_REFUND} {mark}", "CONTRADICTED")

    cands = run_async(lambda: miner.collect(a, WINDOW_START, WINDOW_END))

    assert cands, "the miner found nothing at all — the isolation assertion would be vacuous"
    by_signal: dict[str, list] = {}
    for c in cands:
        by_signal.setdefault(c.signal, []).append(c)

    for signal, found in sorted(by_signal.items()):
        for c in found:
            blob = f"{c.question or ''} {c.answer or ''}"
            assert marks["B"] not in blob, (
                f"{signal}: tenant B's content leaked into tenant A's candidates: {blob[:160]}")
            assert str(c.client_id) == a, (
                f"{signal}: candidate carries client_id {c.client_id}, expected {a}")

    # Each signal family must actually have fired, or the loop above proved nothing about it.
    signals = set(by_signal)
    assert signals & {"S1"}, f"no S1 (ungrounded) candidates; got {sorted(signals)}"
    assert signals & {"S2"}, f"no S2 (weak retrieval) candidates; got {sorted(signals)}"
    assert signals & {"S3"}, f"no S3 (operator correction) candidates; got {sorted(signals)}"
    assert signals & {"S5", "S6"}, f"no fact-check candidates; got {sorted(signals)}"


# --------------------------------------------------------------------------- #
# 7. Resume, don't skip
# --------------------------------------------------------------------------- #
def test_interrupted_run_resumes_from_the_watermark(tenant, embedder, llm_calls):
    """A run killed mid-flight must be re-claimed after the heartbeat window and finish the day.

    Two failure modes are covered at once. If the claim were `ON CONFLICT DO NOTHING`, the dead
    'running' row would make every later attempt skip this tenant-day forever. If the watermark
    were ignored, the already-mined half of the day would be re-mined and re-proposed, and the
    reviewer would see yesterday's decisions again.
    """
    client_id = tenant("resume")
    watermark = _at(12)
    conv = add_conversation(client_id, "resume-1", mined_through=watermark)
    conv2 = add_conversation(client_id, "resume-2", mined_through=watermark)
    # Already mined, before the watermark — must not come back. Full exchanges, because only a
    # bot turn is ever a candidate (see add_failed_exchange).
    add_failed_exchange(client_id, conv, RU_HOURS, at=_at(9))
    add_failed_exchange(client_id, conv2, RU_HOURS, at=_at(9, 30))
    # Not yet mined, after the watermark.
    add_failed_exchange(client_id, conv, KA_REFUND, at=_at(18))
    add_failed_exchange(client_id, conv2, RU_REFUND, at=_at(18, 30))

    # The corpse of the interrupted run: 'running', heartbeat well outside the 30-minute window.
    sql(lambda c: c.execute(
        """INSERT INTO curation_runs
               (client_id, day, state, candidates, heartbeat_at, started_at)
           VALUES ($1,$2,'running',2, now() - interval '45 minutes',
                   now() - interval '50 minutes')""",
        uuid.UUID(client_id), DAY))

    from app.services.curation import runner

    run_async(lambda: runner.run_for_client(client_id, DAY))

    row = run_row(client_id)
    assert row["state"] == "done", (
        "the stale run was not reclaimed — a crashed run must not park a tenant-day forever")

    pending = proposals_of(client_id, "pending")
    assert len(pending) == 1, (
        "expected exactly the post-watermark cluster to be raised, got "
        f"{[p['cluster_medoid'] for p in pending]}")
    assert _concept_of(pending[0]["cluster_medoid"]) == "qrefund", (
        "the resumed run re-mined content from before `mined_through` — the watermark is what "
        "makes a crash resume rather than restart")
    assert len(llm_calls) == 1

    advanced = sql(lambda c: c.fetchval(
        "SELECT min(mined_through) FROM chat_conversations WHERE client_id = $1",
        uuid.UUID(client_id)))
    assert advanced > watermark, "the watermark must advance as items are mined"
