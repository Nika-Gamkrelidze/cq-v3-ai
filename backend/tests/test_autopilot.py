"""P3 safety claims — the public autopilot, asserted one claim per test.

This is the first surface in CQ pointed at the open internet: the customer half of every
conversation is written by an anonymous stranger on WhatsApp. So each test here corresponds to
a line in ADR-001's security bar, and the test names are the claims themselves — if one of
them goes red, the sentence in the ADR is false.

**How these tests drive the engine.** `POST /v1/chat/answer` is a different track and may not
be mounted; `chat.run_answer()` is where every one of these properties actually lives, so the
tests drive the generator directly. That also keeps them honest about layering: none of the
claims below depend on a route existing, and none of them can be satisfied by a route checking
something the engine does not.

**Loop and pool discipline** (the same constraint conftest.py documents): an asyncpg pool
belongs to the loop that created it, and the app's pool belongs to `TestClient`'s portal
thread. So the DB-backed tests here run the engine under `_with_db()`, which stands up a pool
in *this* loop, swaps it into `app.db`, and puts the previous one back afterwards. Tests that
need no database stub `chat.retrieve_ranked` and the kill-switch read instead and run
anywhere, with no Postgres and no API key.

**No Anthropic key, ever.** conftest's autouse `no_llm` detonates on any model call; the tests
that legitimately need a model install a local fake, and the tests that assert *no model was
called* install a counter that records the attempt before detonating. "The refusal costs zero
tokens" is therefore asserted, not assumed.
"""
import asyncio
import dataclasses
import json
import time
import uuid
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.services import chat, chat_prompts, chat_store, settings_store
from conftest import B_MARK, sql

# Markers for the internal/public pair. Deliberately free of anything
# `chat_safety.detect_commitment` or `should_escalate` reacts to, so a test that fails fails
# for the reason it is named after.
INTERNAL_MARK = "MARKER_INTERNAL_QQ7 staff handling note, not for customers"
PUBLIC_MARK = "MARKER_PUBLIC_ZP4 orders can be tracked in the account area"

QUESTION = "how can i see where my order is?"
SAFE_ANSWER = "You can see it in the account area [1]."


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def _ctx(client_id: str, *, cfg: dict | None = None, text: str = QUESTION,
         locale: str = "en", mode: str = "autopilot") -> chat.ChatContext:
    return chat.ChatContext(
        client_id=client_id, conversation_id="00000000-0000-0000-0000-0000000000c1",
        suggest_ref="sg_autopilot_test", locale=locale,
        messages=[{"role": "customer", "content": text}],
        cfg=dict(cfg or {}), api_key="not-a-real-key", model="test-model", mode=mode,
        channel="web", turn_ref="t-1", conversation_ref="conv-ref-1", envelope={})


def _drain(factory):
    """Run an async generator factory in a fresh loop; return [(name, data), …]."""
    async def _run():
        return [(e.name, e.data) async for e in factory()]
    return asyncio.run(_run())


def _with_db(coro_factory):
    """Run `coro_factory()` with an asyncpg pool bound to THIS loop.

    The app's pool lives in the TestClient portal thread's loop and cannot be awaited from
    here, so the engine gets its own for the duration and the previous pool is restored — a
    later HTTP test in the same session must still find the app's pool where it left it.
    """
    from app import db

    async def _run():
        prev = db._pool
        await db.connect()
        try:
            return await coro_factory()
        finally:
            await db.disconnect()
            db._pool = prev
    return asyncio.run(_run())


def _done(events: list[tuple[str, dict]]) -> dict:
    assert events and events[-1][0] == "done", [n for n, _ in events]
    return events[-1][1]


def _names(events) -> list[str]:
    return [n for n, _ in events]


class _Calls:
    """A model stub that records the attempt. Used two ways: as a counter next to a real
    fake, and as a detonator on the paths that must never reach Claude."""

    def __init__(self) -> None:
        self.stream = 0
        self.tool = 0

    @property
    def total(self) -> int:
        return self.stream + self.tool


def _no_model(monkeypatch) -> _Calls:
    """Record-then-raise. Louder than conftest's detonator because the count survives."""
    from app.services import llm

    calls = _Calls()

    async def _stream(**kw):
        calls.stream += 1
        raise AssertionError("stream_text was called on a path that must spend zero tokens")
        yield ""  # pragma: no cover — makes this an async generator

    async def _tool(**kw):
        calls.tool += 1
        raise AssertionError("call_tool was called on a path that must spend zero tokens")

    monkeypatch.setattr(llm, "stream_text", _stream, raising=False)
    monkeypatch.setattr(llm, "call_tool", _tool, raising=False)
    return calls


def _fake_answer(monkeypatch, text: str = SAFE_ANSWER, capture: dict | None = None) -> _Calls:
    """A streaming model that yields `text` in word-sized deltas."""
    from app.services import llm

    calls = _Calls()

    async def _stream(**kw):
        calls.stream += 1
        if capture is not None:
            capture.update(kw)
        for word in text.split(" "):
            yield word + " "

    async def _tool(**kw):
        calls.tool += 1
        raise AssertionError("the answer path must not make a tool call")

    monkeypatch.setattr(llm, "stream_text", _stream, raising=False)
    monkeypatch.setattr(llm, "call_tool", _tool, raising=False)
    return calls


def _fake_triage(monkeypatch, kind: str, reply: str = "", *, answer_text: str | None = None,
                 capture: dict | None = None) -> _Calls:
    """A triage model that answers `{kind, reply}`, plus (optionally) a streaming answer model.

    `calls.tool` counts TRIAGE calls only — a handoff summary is served separately so a path
    that also summarises does not muddy the count. With `answer_text=None` the answer model
    detonates: the path under test must not reach it.
    """
    from app.services import llm

    calls = _Calls()

    async def _tool(**kw):
        if kw.get("feature") == "handoff":
            return {"summary": "Customer needs a colleague.", "customer_goal": "help"}
        assert kw.get("feature") == "triage", kw.get("feature")
        calls.tool += 1
        if capture is not None:
            capture.update(kw)
        return {"kind": kind, "reply": reply}

    async def _stream(**kw):
        calls.stream += 1
        if answer_text is None:
            raise AssertionError("the answer model was called on a path that must not reach it")
        for word in answer_text.split(" "):
            yield word + " "

    monkeypatch.setattr(llm, "call_tool", _tool, raising=False)
    monkeypatch.setattr(llm, "stream_text", _stream, raising=False)
    return calls


def _fixed_embedding(monkeypatch, vec: list[float]):
    """Override conftest's autouse fake embedder for tests that need a controlled match."""
    from app.services import embeddings

    async def _embed(texts, *, purpose: str = "ingest"):
        return [list(vec) for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", _embed)


def _stub_retrieval(monkeypatch, result: dict):
    """Replace retrieval with a fixed ranked result. `chat.py` binds the name at import, so
    the patch goes on the chat module — the same reason conftest patches module objects."""
    async def _retrieve(client_id, query, *, top_k=8, visibility=None, extra_queries=None,
                        min_score=None):
        return dict(result)

    monkeypatch.setattr(chat, "retrieve_ranked", _retrieve)


def _stub_kill_switch(monkeypatch, *, global_disabled: bool = False,
                      disabled_clients: list[str] | None = None):
    async def _get(*, force: bool = False):
        return {"global_disabled": global_disabled,
                "disabled_clients": list(disabled_clients or [])}

    monkeypatch.setattr(settings_store, "get_autopilot_kill_switch", _get)


def _hit(content: str, *, score: float = 0.91, n: int = 1) -> dict:
    return {"chunk_id": f"chunk-{n}", "document_id": f"doc-{n}", "chunk_index": 0,
            "content": content, "metadata": {}, "title": "Orders", "doc_type": "policy",
            "score": score}


VECTOR_RESULT = {"method": "vector", "top_score": 0.91, "kb_present": True,
                 "hits": [_hit(PUBLIC_MARK)]}
KEYWORD_RESULT = {"method": "keyword", "top_score": 0.88, "kb_present": True,
                  "hits": [_hit(PUBLIC_MARK, score=0.88)]}
UNGROUNDED_RESULT = {"method": "none", "top_score": None, "kb_present": True, "hits": []}


@pytest.fixture(autouse=True)
def _reset_kill_cache():
    """The kill switch is cached in a module global with a 5 s TTL. Left populated it would
    leak a decision from one test into the next."""
    settings_store._kill_cache = None
    yield
    settings_store._kill_cache = None


@pytest.fixture
def bare_tenant(api):
    """A tenant with no documents at all — the state every tenant is in on day one."""
    slug = f"autopilot-bare-{uuid.uuid4().hex[:8]}"
    cid = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
        slug, "autopilot-bare", f"tk-bare-{uuid.uuid4().hex[:8]}"))
    try:
        yield {"client_id": str(cid), "slug": slug}
    finally:
        sql(lambda c: c.execute("DELETE FROM clients WHERE id = $1", cid))


@pytest.fixture
def internal_and_public(seed, monkeypatch):
    """Two documents under tenant A that match the SAME question equally well — identical
    embeddings — differing only in `visibility`. That is the whole point: nothing about
    relevance may be what keeps the internal one out of a customer's reply.
    """
    import conftest

    vec = [0.0, 0.0, 0.9] + [0.0] * (conftest._DIM - 3)
    pgvec = conftest._pgvector(vec)
    cid = uuid.UUID(seed["a"]["client_id"])

    async def _seed(conn):
        out = {}
        for label, mark, visibility in (("internal", INTERNAL_MARK, "internal"),
                                        ("public", PUBLIC_MARK, "public")):
            doc = await conn.fetchval(
                "INSERT INTO kb_documents (client_id, doc_type, title, status, visibility) "
                "VALUES ($1,'policy',$2,'ready',$3) RETURNING id",
                cid, f"Order tracking ({label})", visibility)
            await conn.execute(
                "INSERT INTO kb_chunks (document_id, client_id, content, embedding, chunk_index)"
                " VALUES ($1,$2,$3,$4::vector,0)", doc, cid, mark, pgvec)
            out[label] = str(doc)
        return out

    docs = sql(_seed)
    _fixed_embedding(monkeypatch, vec)   # every query is a perfect match for BOTH documents
    try:
        yield docs
    finally:
        sql(lambda c: c.execute(
            "DELETE FROM kb_documents WHERE id = ANY($1::uuid[])",
            [uuid.UUID(docs["internal"]), uuid.UUID(docs["public"])]))


# --------------------------------------------------------------------------- #
# 1. THE test: a public bot cannot quote an internal document
# --------------------------------------------------------------------------- #
def test_public_bot_cannot_quote_an_internal_document(seed, internal_and_public, monkeypatch):
    """The single most important assertion in P3.

    `kb_documents.visibility` DEFAULTS to 'internal', so a tenant's pricing floors, escalation
    scripts and staff notes are in the same table as their published FAQ. Two documents are
    seeded here with *identical embeddings* so relevance cannot be what separates them: if the
    `visibility='public'` filter is ever dropped, weakened, or moved somewhere a caller can
    forget it, the internal text lands in a stranger's WhatsApp thread and this test goes red.

    Asserted at three depths, because each one can fail independently:
      1. retrieval itself — 'public' filters, the unfiltered call proves the doc is there;
      2. the PROMPT — the internal text never reaches the model at all (a model cannot leak
         what it was never shown, which is why filtering happens before the call, not after);
      3. the envelope — no citation, no text, nothing anywhere in the emitted events.
    """
    client_id = seed["a"]["client_id"]

    # 1. Retrieval. Unfiltered (what the operator copilot uses) sees both; 'public' sees one.
    unfiltered, public_only = _with_db(lambda: _both_retrievals(client_id))

    unfiltered_docs = {h["document_id"] for h in unfiltered["hits"]}
    assert internal_and_public["internal"] in unfiltered_docs, \
        "fixture is not exercising the filter: the internal doc is not retrievable at all"
    public_docs = {h["document_id"] for h in public_only["hits"]}
    assert public_docs == {internal_and_public["public"]}, public_only
    assert all(INTERNAL_MARK not in (h["content"] or "") for h in public_only["hits"])

    # 2 + 3. The full answer turn.
    prompt: dict = {}
    calls = _fake_answer(monkeypatch, capture=prompt)
    cfg = {"autopilot_enabled": True}
    events = _with_db(lambda: _collect(chat.run_answer(_ctx(client_id, cfg=cfg))))

    assert calls.stream == 1
    assert INTERNAL_MARK not in prompt.get("user", ""), \
        "the internal document was placed in the model's prompt"
    assert PUBLIC_MARK in prompt.get("user", ""), prompt.get("user", "")[:400]

    body = json.dumps(events, default=str)
    assert INTERNAL_MARK not in body, "the internal document surfaced in the emitted events"
    assert internal_and_public["internal"] not in body, "an internal document was cited"

    done = _done(events)
    assert done["grounding"]["grounded"] is True, done["grounding"]
    cited = {c["document_id"] for c in done["reply"]["citations"]}
    assert cited == {internal_and_public["public"]}, done["reply"]


async def _both_retrievals(client_id: str):
    from app.services.retrieval import retrieve_ranked

    unfiltered = await retrieve_ranked(client_id, QUESTION, top_k=8, visibility=None)
    public_only = await retrieve_ranked(client_id, QUESTION, top_k=8, visibility="public")
    return unfiltered, public_only


async def _collect(agen):
    return [(e.name, e.data) async for e in agen]


# --------------------------------------------------------------------------- #
# 2. Off by default; enabling requires a published KB
# --------------------------------------------------------------------------- #
def test_autopilot_is_off_by_default(bare_tenant, monkeypatch):
    """A tenant that has never configured chat must not have a bot talking to its customers.
    Two layers assert it: the config default, and the engine — which refuses on a config that
    simply does not mention autopilot, rather than requiring an explicit False.
    """
    assert chat_store.CHAT_CONFIG_DEFAULTS["autopilot_enabled"] is False

    calls = _no_model(monkeypatch)
    client_id = bare_tenant["client_id"]

    cfg = _with_db(lambda: chat_store.get_chat_config(client_id))
    assert cfg["autopilot_enabled"] is False

    events = _with_db(lambda: _collect(chat.run_answer(_ctx(client_id, cfg=cfg))))
    done = _done(events)
    assert done["grounding"]["reason"] == chat.REASON_DISABLED
    assert done["handoff"]["recommended"] is True
    assert calls.total == 0, "a disabled autopilot called the model"
    # It refuses before retrieval, too — there is nothing to retrieve for and no reason to pay
    # for an embedding round-trip on a surface that is switched off.
    assert _names(events) == ["open", "done"], _names(events)


def test_enabling_autopilot_requires_at_least_one_public_document(api, bare_tenant):
    """A bot with no published KB refuses every question — the product looks broken and the
    tenant blames the model. So enabling must fail loudly (409) while the KB is entirely
    internal, and succeed once a human has published something.

    The guard belongs to the config writer (`routers/admin.py`, the `SELECT EXISTS(…)`
    pre-check pattern from `routers/scoring.py`) rather than to the answer endpoint: refusing
    to *speak* is a runtime symptom, refusing to *enable* is the thing an operator can fix.
    """
    hdr = {"X-Admin-Token": settings.admin_token}
    url = f"/admin/chat/{bare_tenant['client_id']}/config"
    if api.get(url, headers=hdr).status_code == 401:
        pytest.skip("ADMIN_TOKEN not configured in this environment")

    body = {"persona": "helper", "greeting": {}, "refusal_copy": {},
            "languages": ["en"], "canned": [], "autopilot_enabled": True, "settings": {}}
    r = api.put(url, headers=hdr, json=body)
    assert r.status_code == 409, (
        "enable guard missing: PUT /admin/chat/{id}/config accepted autopilot_enabled=true "
        f"for a tenant with no public documents ({r.status_code}) — {r.text}")

    # The refusal must be specific enough for an operator to act on.
    assert "public" in r.text.lower(), r.text
    assert _with_db(lambda: chat_store.get_chat_config(
        bare_tenant["client_id"]))["autopilot_enabled"] is False

    # Publish one document, and the same request now succeeds.
    doc = sql(lambda c: c.fetchval(
        "INSERT INTO kb_documents (client_id, doc_type, title, status, visibility) "
        "VALUES ($1,'faq','Published','ready','public') RETURNING id",
        uuid.UUID(bare_tenant["client_id"])))
    try:
        r = api.put(url, headers=hdr, json=body)
        assert r.status_code in (200, 201), r.text
        assert r.json().get("autopilot_enabled") is True, r.text
    finally:
        sql(lambda c: c.execute("DELETE FROM kb_documents WHERE id = $1", doc))


# --------------------------------------------------------------------------- #
# 3. The kill switch
# --------------------------------------------------------------------------- #
def test_kill_switch_stops_the_bot_globally_and_per_client(seed, monkeypatch):
    """The operator brake. A misbehaving public bot has to be stoppable in seconds by someone
    who cannot deploy — so the switch lives in `app_settings`, is read on every autopilot turn
    (cached 5 s), and is checked FIRST, before any tenant setting can matter.

    Note what is being asserted about the tenant that is *not* killed: it still answers. A
    brake that stops everyone is an outage, not a brake.
    """
    calls = _fake_answer(monkeypatch)
    cfg = {"autopilot_enabled": True}
    a, b = seed["a"]["client_id"], seed["b"]["client_id"]

    async def _scenario():
        out = {}
        await settings_store.set_autopilot_kill_switch(
            {"global_disabled": True, "disabled_clients": []})
        out["global_a"] = await _collect(chat.run_answer(_ctx(a, cfg=cfg)))
        out["global_b"] = await _collect(chat.run_answer(_ctx(b, cfg=cfg)))

        await settings_store.set_autopilot_kill_switch(
            {"global_disabled": False, "disabled_clients": [a]})
        out["scoped_a"] = await _collect(chat.run_answer(_ctx(a, cfg=cfg)))
        out["scoped_b"] = await _collect(chat.run_answer(_ctx(b, cfg=cfg)))

        # --- the cache, which is the only way this can be wrong in production ---
        # `set_…` drops the cache, so an operator's flip is visible immediately.
        await settings_store.set_autopilot_kill_switch({"global_disabled": True})
        out["after_set"] = await settings_store.get_autopilot_kill_switch()

        # A write that goes around `set_…` is honoured only after the TTL — asserted rather
        # than tolerated, because "it took five seconds" and "it never took effect" look
        # identical to an operator watching a bot misbehave.
        await settings_store._save_key(settings_store.AUTOPILOT_KILL_KEY,
                                       {"global_disabled": False, "disabled_clients": []})
        out["within_ttl"] = await settings_store.get_autopilot_kill_switch()
        stale = time.monotonic() - settings_store.AUTOPILOT_KILL_TTL_S - 1
        settings_store._kill_cache = (stale, settings_store._kill_cache[1])
        out["after_ttl"] = await settings_store.get_autopilot_kill_switch()

        await settings_store.set_autopilot_kill_switch(
            {"global_disabled": False, "disabled_clients": []})
        return out

    out = _with_db(_scenario)

    for key in ("global_a", "global_b", "scoped_a"):
        done = _done(out[key])
        assert done["grounding"]["reason"] == chat.REASON_KILLED, (key, done["grounding"])
        assert done["handoff"]["recommended"] is True, key

    # Tenant B is not on the list and is unaffected — the fake embedder makes B's own public
    # chunk a perfect match, so this turn goes all the way through to an answer.
    assert _done(out["scoped_b"])["grounding"]["reason"] != chat.REASON_KILLED
    assert calls.stream >= 1, "the un-killed tenant never reached the model"

    assert out["after_set"]["global_disabled"] is True, "set_() did not drop the cache"
    assert out["within_ttl"]["global_disabled"] is True, "the 5s cache was not honoured"
    assert out["after_ttl"]["global_disabled"] is False, \
        "a kill-switch change never took effect without a restart"

    # And the pure predicate, so the routing logic is testable without a database at all.
    kill = {"global_disabled": False, "disabled_clients": [a]}
    assert settings_store.autopilot_killed(kill, a) is True
    assert settings_store.autopilot_killed(kill, b) is False
    assert settings_store.autopilot_killed({"global_disabled": True}, b) is True


def test_the_kill_switch_is_reachable_over_http(api, seed):
    """A brake only an engineer with a VPN and a psql prompt can pull is not a brake.

    The engine-level test above proves the switch *works*; this one proves an operator can
    actually reach it — the admin panel's Bot Control panel calls exactly these two routes,
    and while they were missing it degraded politely instead of erroring, which reads as
    working right up until the night it matters.
    """
    hdr = {"X-Admin-Token": settings.admin_token}
    if api.get("/admin/chat/kill-switch", headers=hdr).status_code == 401:
        pytest.skip("ADMIN_TOKEN not configured in this environment")

    # Unauthenticated it is invisible, like every other admin route.
    assert api.get("/admin/chat/kill-switch").status_code == 401

    before = api.get("/admin/chat/kill-switch", headers=hdr)
    assert before.status_code == 200, before.text
    original = before.json()

    cid = seed["a"]["client_id"]
    try:
        r = api.put("/admin/chat/kill-switch", headers=hdr,
                    json={"global_disabled": True, "disabled_clients": [cid]})
        assert r.status_code == 200, r.text
        assert r.json()["global_disabled"] is True
        assert cid in r.json()["disabled_clients"]

        # And the read-back is the truth, not a cached pre-flip value.
        again = api.get("/admin/chat/kill-switch", headers=hdr).json()
        assert again["global_disabled"] is True
        assert settings_store.autopilot_killed(again, cid) is True

        # Patch semantics: releasing the global brake must not silently un-stop the tenant
        # the operator stopped individually.
        r = api.put("/admin/chat/kill-switch", headers=hdr, json={"global_disabled": False})
        assert r.status_code == 200, r.text
        assert r.json()["global_disabled"] is False
        assert cid in r.json()["disabled_clients"]
    finally:
        api.put("/admin/chat/kill-switch", headers=hdr, json={
            "global_disabled": bool(original.get("global_disabled")),
            "disabled_clients": list(original.get("disabled_clients") or [])})


def test_the_copilot_ingest_cannot_ask_for_the_public_engine(api, seed):
    """`mode` used to be a free string on POST /turns that `_runner_for` dispatched on, so a
    body field selected the public answer engine — bypassing the `chat:answer` scope, this
    router's 503/409 pre-checks and the separate `chat_answer` meter. The public bot has its
    own endpoint; asking for it here is rejected."""
    hdr = {"X-CQ-Key": seed["integration"]["api_key"], "X-CQ-Tenant": seed["a"]["slug"],
           "X-CQ-Expect-Tenant": seed["a"]["client_id"]}
    body = {"conversation_ref": f"conv-mode-{uuid.uuid4().hex[:8]}", "content": "hello",
            "precompute": False, "mode": "autopilot"}
    r = api.post("/v1/chat/turns", headers=hdr, json=body)
    assert r.status_code == 422, r.text

    body["mode"] = "assist"
    r = api.post("/v1/chat/turns", headers=hdr, json=body)
    assert r.status_code == 202, r.text


# --------------------------------------------------------------------------- #
# 4. The refusal costs zero tokens
# --------------------------------------------------------------------------- #
def test_a_question_the_gate_cannot_ground_never_reaches_the_answer_model(seed, monkeypatch):
    """"I don't know" is a property of the system, not a hope pinned on prompt wording.

    conftest's fake embedder returns tenant B's vector for every query, so tenant A's question
    is unanswerable from A's own KB. Since 2026-09-14 such a message may spend ONE small triage
    call (no passages) to learn whether it is small talk, off-topic or a business question —
    here the stub says business — and then gets the refusal and a colleague. What it can never
    do is reach the answer model: the stream stub was never entered. (The exits that still
    spend nothing at all are asserted in `test_the_free_exits_spend_nothing`.)
    """
    calls = _fake_triage(monkeypatch, "business")
    client_id = seed["a"]["client_id"]

    events = _with_db(lambda: _collect(
        chat.run_answer(_ctx(client_id, cfg={"autopilot_enabled": True}))))
    done = _done(events)

    assert done["grounding"]["grounded"] is False
    assert done["grounding"]["reason"] in (chat.REASON_NO_HITS, chat.REASON_LOW_SCORE,
                                           chat.REASON_KEYWORD_ONLY, chat.REASON_KB_EMPTY)
    assert done["reply"]["answered_from_kb"] is False
    # `startswith`, not `==`: the AI-disclosure line is appended in python on the way out
    # (`chat._disclosed`), so the refusal copy is the START of what the customer sees.
    assert done["reply"]["text"].startswith(chat_prompts.refusal_text({}, "en"))
    assert done["handoff"]["recommended"] is True
    # The handoff summary is the customer's own last message — no summary call either.
    assert done["handoff"]["summary"] == QUESTION

    assert calls.stream == 0, "an ungrounded question reached the answer model"
    assert calls.tool <= 1
    # And nothing of tenant B's leaked while refusing.
    assert B_MARK not in json.dumps(events, default=str)


# --------------------------------------------------------------------------- #
# 5. Keyword-only: ungrounded out here, grounded for the copilot
# --------------------------------------------------------------------------- #
def test_keyword_only_is_ungrounded_for_the_public_bot_but_grounded_for_the_copilot(monkeypatch):
    """Same retrieval result, two verdicts — because `strict` differs, and only because of it.

    A pg_trgm hit is character-overlap; for Georgian it happily matches a passage that merely
    shares morphology with the question. A human operator reading a mediocre passage before
    sending is fine. A stranger receiving it as an answer is not.

    The second half is the part that has to hold under pressure: a tenant CAN set
    `strict: false` (the copilot honours it), and the public bot must ignore them. `run_answer`
    forces `cfg["strict"] = True` rather than `setdefault`-ing it, so "the tenant turned strict
    off" is not a route to an ungrounded public bot.
    """
    assert chat.gate(KEYWORD_RESULT, {"strict": True}) == (False, chat.REASON_KEYWORD_ONLY)
    assert chat.gate(KEYWORD_RESULT, {"strict": False}) == (True, chat.REASON_OK)
    # Unset means strict — anything that forgets to decide fails closed.
    assert chat.gate(KEYWORD_RESULT, {}) == (False, chat.REASON_KEYWORD_ONLY)

    _stub_retrieval(monkeypatch, KEYWORD_RESULT)
    _stub_kill_switch(monkeypatch)
    # Triage may read the message (it is given no passages); the answer model must not run.
    calls = _fake_triage(monkeypatch, "business")

    # The tenant asked for strict=False. The public bot overrules them.
    events = _drain(lambda: chat.run_answer(
        _ctx("11111111-1111-1111-1111-111111111111",
             cfg={"autopilot_enabled": True, "strict": False})))
    done = _done(events)
    assert done["grounding"]["grounded"] is False
    assert done["grounding"]["reason"] == chat.REASON_KEYWORD_ONLY
    assert done["reply"]["answered_from_kb"] is False
    assert done["handoff"]["recommended"] is True
    assert calls.stream == 0, "a keyword-only hit reached the answer model on the public bot"

    # The copilot, given the identical retrieval result, proceeds.
    from app.services import llm

    async def _tool(**kw):
        return {"suggestions": [{"kind": "answer", "text": "Check the account area [1]."}],
                "handoff_recommended": False}

    monkeypatch.setattr(llm, "call_tool", _tool, raising=False)
    events = _drain(lambda: chat.run_suggest(
        _ctx("11111111-1111-1111-1111-111111111111", cfg={}, mode="assist")))
    grounding = dict(events[1][1])
    assert events[1][0] == "grounding"
    assert grounding["grounded"] is True, grounding
    assert grounding["method"] == "keyword"
    assert _done(events)["suggestions"], "the copilot produced no draft from a keyword hit"


# --------------------------------------------------------------------------- #
# 6. What the bot does with a message the documents do not settle (2026-09-14)
#
# The first pilot conversation: "what day is today?" refused, "list the planets" answered at
# full cost because the follow-up window matched, and a KB-stated installation time handed the
# customer to a queue. Each test below is one of those, or the policy that replaced the old
# `allow_general_knowledge` flag.
# --------------------------------------------------------------------------- #
WEAK_RESULT = {"method": "vector", "top_score": 0.62, "kb_present": True,
               "hits": [_hit(PUBLIC_MARK, score=0.62)], "query_top_scores": [0.38, 0.62]}
EMPTY_KB_RESULT = {"method": "none", "top_score": None, "kb_present": False, "hits": []}
PINNED_NOW = datetime(2026, 9, 14, 11, 42, tzinfo=timezone.utc)     # 15:42 in Tbilisi, a Monday
CID = "11111111-1111-1111-1111-111111111111"
ON = {"autopilot_enabled": True}
INSTALL = "Installation typically takes 3 business days after the contract is signed."


def _run(ctx: chat.ChatContext) -> dict:
    return _done(_drain(lambda: chat.run_answer(ctx)))


def test_answer_policy_defaults_to_kb_only_and_reads_the_legacy_flag():
    """ADR-001 open decision #1, resolved as configuration. A row saved before the policy
    existed carries only the old boolean; the new key wins wherever both are present."""
    assert chat.DEFAULTS["answer_policy"] == "kb_only"
    assert chat_prompts.answer_policy({}) == "kb_only"
    assert chat_prompts.answer_policy({"settings": {"allow_general_knowledge": True}}) == "general"
    assert chat_prompts.answer_policy({"settings": {"allow_general_knowledge": "yes"}}) == "kb_only"
    assert chat_prompts.answer_policy(
        {"settings": {"answer_policy": "kb_only", "allow_general_knowledge": True}}) == "kb_only"
    assert chat_prompts.answer_policy({"settings": {"answer_policy": "general"}}) == "general"
    assert chat_prompts.answer_policy({"settings": {"answer_policy": "anything"}}) == "kb_only"


def test_what_day_is_it_is_answered_from_the_business_clock(monkeypatch):
    """Nothing in the KB, default policy: one triage call whose prompt carries the tenant's
    LOCAL date and time, and its short answer goes out — no refusal, no handoff."""
    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)
    seen: dict = {}
    calls = _fake_triage(monkeypatch, "chitchat", "Today is Monday, 14 September.", capture=seen)

    done = _run(dataclasses.replace(_ctx(CID, cfg=ON, text="hey what day is today?"),
                                    now=PINNED_NOW))

    assert "Monday, 14 September 2026, 15:42 (Asia/Tbilisi, UTC+04:00)" in seen["user"]
    assert done["reply"]["text"].startswith("Today is Monday, 14 September.")
    assert done["handoff"]["recommended"] is False
    assert done["scope"]["kind"] == "chitchat" and done["scope"]["answered"] is True
    assert done["reply"]["answered_from_kb"] is False
    assert (calls.tool, calls.stream) == (1, 0)


def test_general_and_chitchat_answers_are_ready_not_refused():
    from app.routers import chat as chat_router

    assert chat_router._state_for({"grounding": {"grounded": False},
                                   "scope": {"answered": True}}) == "ready"
    assert chat_router._state_for({"grounding": {"grounded": False},
                                   "scope": {"answered": False}}) == "refused"
    assert chat_router._state_for({"grounding": {"grounded": True}, "scope": None}) == "ready"


def test_the_follow_up_window_no_longer_grounds_an_unrelated_question(monkeypatch):
    """The window prepends the bot's last line, which named the company, so an unrelated
    question fused to a good score and went straight to a full answer. The customer's OWN
    score now decides whether triage is skipped."""
    grounded, _ = chat.gate(WEAK_RESULT, {})
    assert grounded is True
    assert chat.own_score(WEAK_RESULT) == 0.38
    assert chat.direct_match(WEAK_RESULT, {}, grounded) is False
    # No per-query scores (an older retrieval, a stub): the fused top stands in.
    assert chat.direct_match(VECTOR_RESULT, {}, True) is True
    # The tenant's own min_score still raises the bar above direct_min_score.
    assert chat.direct_match({**WEAK_RESULT, "query_top_scores": [0.55, 0.62]},
                             {"min_score": 0.6}, True) is False

    _stub_retrieval(monkeypatch, WEAK_RESULT)
    _stub_kill_switch(monkeypatch)
    calls = _fake_triage(monkeypatch, "off_topic", "I can only help with our services.")
    done = _run(_ctx(CID, cfg=ON, text="list me planets of our solar system"))
    assert calls.stream == 0
    assert done["grounding"]["grounded"] is False
    assert done["grounding"]["reason"] == chat.REASON_WEAK_MATCH


def test_off_topic_is_redirected_then_warned_then_cut_off_without_a_handoff(monkeypatch):
    """Warn at 3, stop at 5 (the defaults). The router carries the count in; the engine returns
    the new count in `scope.off_topic`. Nothing hands off: the customer can still ask a real
    question, or ask for a person."""
    from app.services import chat_copy

    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)
    calls = _fake_triage(monkeypatch, "off_topic", "I can only help with our internet plans.")

    first = _run(_ctx(CID, cfg=ON, text="list the planets"))
    assert first["scope"]["off_topic"] == {"count": 1, "warn_after": 3, "cutoff_after": 5,
                                           "state": "ok"}
    assert first["reply"]["text"].startswith("I can only help with our internet plans.")
    assert chat_copy.DEFAULT_OFF_TOPIC_WARNING["en"] not in first["reply"]["text"]
    assert first["handoff"]["recommended"] is False
    assert first["reply"]["answered_from_kb"] is False

    third = _run(dataclasses.replace(_ctx(CID, cfg=ON, text="and their moons?"),
                                     off_topic_count=2))
    assert third["scope"]["off_topic"]["count"] == 3
    assert third["scope"]["off_topic"]["state"] == "warned"
    assert chat_copy.DEFAULT_OFF_TOPIC_WARNING["en"] in third["reply"]["text"]

    fifth = _run(dataclasses.replace(_ctx(CID, cfg=ON, text="write me a poem"),
                                     off_topic_count=4))
    assert fifth["scope"]["off_topic"]["state"] == "cut_off"
    assert fifth["reply"]["text"].startswith(chat_copy.DEFAULT_OFF_TOPIC_CUTOFF["en"])
    assert fifth["handoff"]["recommended"] is False
    assert calls.tool == 3

    # Past the cut-off: no model call at all for a message the documents do not clearly cover…
    silent = _no_model(monkeypatch)
    after = _run(dataclasses.replace(_ctx(CID, cfg=ON, text="one more joke"), off_topic_count=5))
    assert after["scope"]["source"] == "cutoff"
    assert after["reply"]["text"].startswith(chat_copy.DEFAULT_OFF_TOPIC_CUTOFF["en"])
    assert after["handoff"]["recommended"] is False
    assert silent.total == 0

    # …while a question the documents DO clearly cover is still answered.
    _stub_retrieval(monkeypatch, VECTOR_RESULT)
    answer = _fake_answer(monkeypatch)
    real = _run(dataclasses.replace(_ctx(CID, cfg=ON), off_topic_count=9))
    assert real["scope"]["source"] == "direct" and real["grounding"]["grounded"] is True
    assert answer.stream == 1


def test_the_free_exits_spend_nothing(monkeypatch):
    """What still costs zero tokens: a KB with nothing published (and both off switches, and
    the cut-off above)."""
    _stub_retrieval(monkeypatch, EMPTY_KB_RESULT)
    _stub_kill_switch(monkeypatch)
    calls = _no_model(monkeypatch)
    done = _run(_ctx(CID, cfg=ON, text="what are your prices?"))
    assert done["handoff"]["recommended"] is True
    assert done["scope"]["source"] == "refusal"
    assert calls.total == 0


def test_a_business_question_goes_to_the_documents_or_to_a_colleague(monkeypatch):
    _stub_kill_switch(monkeypatch)

    # Nothing the gate can stand on: the refusal and a handoff; the answer model never runs.
    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    calls = _fake_triage(monkeypatch, "business")
    refused = _run(_ctx(CID, cfg=ON, text="how much is it?"))
    assert refused["handoff"]["recommended"] is True
    assert refused["handoff"]["reason"] == chat.REASON_NO_HITS
    assert refused["scope"]["kind"] == "business"
    assert calls.stream == 0

    # A follow-up the window matched but the customer's own words did not: triage says
    # business, so the grounded answer runs with the passages.
    _stub_retrieval(monkeypatch, WEAK_RESULT)
    calls = _fake_triage(monkeypatch, "business", answer_text=SAFE_ANSWER)
    done = _run(_ctx(CID, cfg=ON, text="and how much?"))
    assert (calls.tool, calls.stream) == (1, 1)
    assert done["grounding"]["grounded"] is True
    assert done["scope"]["source"] == "triage" and done["scope"]["kind"] == "business"
    assert done["reply"]["answered_from_kb"] is True


def test_a_related_question_gets_general_knowledge_only_under_the_general_policy(monkeypatch):
    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)
    question = "which doctor should i book for a broken leg?"
    general_reply = ("An orthopaedist (a trauma specialist) treats broken bones — this is general "
                     "information, not our own.")

    _fake_triage(monkeypatch, "related", general_reply)
    strict = _run(_ctx(CID, cfg=ON, text=question))
    assert strict["reply"]["text"].startswith(chat_prompts.refusal_text({}, "en"))
    assert strict["handoff"]["reason"] == chat.REASON_RELATED_NOT_IN_KB

    seen: dict = {}
    _fake_triage(monkeypatch, "related", general_reply, capture=seen)
    general = _run(_ctx(CID, cfg={**ON, "settings": {"answer_policy": "general"}}, text=question))
    assert general["reply"]["text"].startswith(general_reply)
    assert general["handoff"]["recommended"] is False
    assert general["scope"]["policy"] == "general" and general["scope"]["answered"] is True
    assert "a short, helpful general answer" in seen["system"]

    # Written without the documents, so any price in it is unbacked by definition.
    _fake_triage(monkeypatch, "related", "It usually costs about $50 elsewhere.")
    priced = _run(_ctx(CID, cfg={**ON, "settings": {"answer_policy": "general"}}, text=question))
    assert priced["handoff"]["reason"] == "commitment:money"


def test_a_risky_message_gets_the_emergency_number_first_and_a_colleague(monkeypatch):
    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)
    _fake_triage(monkeypatch, "risky", "")
    done = _run(_ctx(CID, cfg=ON, text="my father fell and cannot move his leg"))
    assert done["handoff"]["recommended"] is True
    assert done["handoff"]["reason"] == chat.REASON_RISKY
    # The model wrote nothing, so the built-in safety notice went out.
    assert "call 112 now" in done["reply"]["text"]
    assert done["scope"]["kind"] == "risky"


def test_asking_for_a_person_gets_the_handoff_notice_not_the_refusal(monkeypatch):
    from app.services import chat_copy

    _stub_kill_switch(monkeypatch)
    _no_model(monkeypatch)    # the handoff summary falls back to the transcript
    person = _run(_ctx(CID, cfg=ON, text="can I talk to a person please"))
    assert person["reply"]["text"].startswith(chat_copy.DEFAULT_HANDOFF_NOTICE["en"])
    assert person["handoff"]["reason"] == "escalation:complaint"

    emergency = _run(_ctx(CID, cfg=ON, text="this is an emergency"))
    assert emergency["reply"]["text"].startswith("If you or someone else may be in danger")
    assert "call 112 now" in emergency["reply"]["text"]
    assert emergency["handoff"]["reason"] == "escalation:distress"


def test_a_failed_triage_hands_off_with_llm_error(monkeypatch):
    from app.services import llm

    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)

    async def _tool(**kw):
        raise llm.LLMBusyError("busy")

    monkeypatch.setattr(llm, "call_tool", _tool, raising=False)
    events = _drain(lambda: chat.run_answer(_ctx(CID, cfg=ON, text="hello")))
    assert "error" in _names(events)
    done = _done(events)
    assert done["handoff"]["reason"] == chat.REASON_LLM_ERROR
    assert done["reply"]["text"].startswith(chat_prompts.refusal_text({}, "en"))


def test_a_deadline_the_documents_state_does_not_hand_off_but_an_invented_one_does(monkeypatch):
    """The pilot's automatic "Connecting you to an operator…": the bot quoted the tenant's own
    installation time and the commitment regex called it a promise."""
    _stub_kill_switch(monkeypatch)
    _stub_retrieval(monkeypatch, {"method": "vector", "top_score": 0.9, "kb_present": True,
                                  "hits": [_hit(INSTALL, score=0.9)]})
    question = "how fast do you install?"

    _fake_answer(monkeypatch, text="Installation is usually done within 3 business days [1].")
    backed = _run(_ctx(CID, cfg=ON, text=question))
    assert backed["handoff"]["recommended"] is False, backed["handoff"]

    _fake_answer(monkeypatch, text="We can install within 1 business day [1].")
    invented = _run(_ctx(CID, cfg=ON, text=question))
    assert invented["handoff"]["reason"] == "commitment:deadline"

    # The strict behaviour is one hidden setting away.
    _fake_answer(monkeypatch, text="Installation is usually done within 3 business days [1].")
    strict = _run(_ctx(CID, cfg={**ON, "settings": {"handoff_on_kb_commitments": True}},
                       text=question))
    assert strict["handoff"]["reason"] == "commitment:deadline"


# --------------------------------------------------------------------------- #
# 7. Tenant isolation, on the public surface
# --------------------------------------------------------------------------- #
def test_tenant_a_bot_never_retrieves_tenant_b_chunks(seed, monkeypatch):
    """The #1 invariant of the whole product, restated for the bot: every tenant-scoped query
    filters by `client_id`, and `visibility` is a filter ON TOP of that, never instead of it.

    The fake embedder makes tenant B's chunk the perfect semantic match for A's question —
    the worst case — and B's document is `visibility='public'`, so publishability offers no
    accidental protection here. Only `client_id` does.
    """
    calls = _fake_triage(monkeypatch, "business")
    a = seed["a"]["client_id"]

    events = _with_db(lambda: _collect(
        chat.run_answer(_ctx(a, cfg={"autopilot_enabled": True}))))

    body = json.dumps(events, default=str)
    assert B_MARK not in body, "LEAK: tenant B's KB reached tenant A's public bot"
    assert seed["b"]["document_id"] not in body
    assert _done(events)["client_id"] == a
    assert calls.stream == 0

    # One layer down, both filters, both directions.
    async def _probe():
        from app.services.retrieval import retrieve_ranked
        return [await retrieve_ranked(a, B_MARK, top_k=8, visibility=v)
                for v in (None, "public")]

    for result in _with_db(_probe):
        assert all(B_MARK not in (h["content"] or "") for h in result["hits"]), result


# --------------------------------------------------------------------------- #
# 8. One envelope, every transport
# --------------------------------------------------------------------------- #
def test_streamed_done_envelope_is_byte_identical_to_the_blocking_answer(monkeypatch):
    """P1 asserts this for `suggest`; the autopilot inherits the claim and must keep it.

    The ADR's transport property is that the Turn envelope is produced ONCE and every
    transport carries that object — the blocking adapter returns the terminal event's payload,
    the SSE adapter serialises the same payload, and a future WebSocket adapter will frame it.
    Two ways it could quietly stop being true: the engine becoming non-deterministic (asserted
    by running it twice with the clock pinned), or a transport reconstructing the envelope
    instead of forwarding it (asserted through the real SSE serialiser).

    The last assertion is the one with product meaning: the raw `delta` text and the
    authoritative `reply.text` are NOT the same string, because validation runs after the
    stream. A consumer that renders deltas must replace them with the `done` text.
    """
    from app.routers import chat as chat_router

    _stub_retrieval(monkeypatch, VECTOR_RESULT)
    _stub_kill_switch(monkeypatch)
    monkeypatch.setattr(chat, "_ms", lambda started: 7)   # pin the only nondeterminism
    # Includes a markdown link, so sanitisation demonstrably changes the text.
    _fake_answer(monkeypatch, text="See [the tracker](https://evil.example/x) here [1].")

    cfg = {"autopilot_enabled": True}
    first = _drain(lambda: chat.run_answer(_ctx("11111111-1111-1111-1111-111111111111", cfg=cfg)))
    second = _drain(lambda: chat.run_answer(_ctx("11111111-1111-1111-1111-111111111111", cfg=cfg)))

    blocking = _done(first)
    assert json.dumps(blocking, sort_keys=True) == json.dumps(_done(second), sort_keys=True)

    # The SSE adapter carries the same object, byte for byte, through a real frame.
    frame = chat_router._sse("done", {"turn": blocking, "seq": len(first)})
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["turn"] == blocking
    assert json.dumps(payload["turn"], sort_keys=True) == json.dumps(blocking, sort_keys=True)

    # Deltas are raw model text; `done` is the authoritative, validated answer.
    streamed = "".join(d["text"] for n, d in first if n == "delta")
    assert "evil.example" in streamed
    assert "evil.example" not in blocking["reply"]["text"]
    assert blocking["reply"]["text"] != streamed.strip()
    assert [c["n"] for c in blocking["reply"]["citations"]] == [1]
