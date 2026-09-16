"""Every chat model call names the conversation, the message and the generation it served.

`llm_usage` gained `conversation_id`, `turn_id` and `suggest_ref` so the usage page can answer
"which customer question cost this?". The accounting seam accepts them, but nothing forces a
call site to pass them: a call that forgets records a row that looks correct and is simply
unattributed, which no status code and no other test would ever notice. So this file pins it
two ways:

  1. **Structurally** — every `llm.call_tool` / `llm.stream_text` in `services/chat.py` passes
     all three keywords from the context, and every `_build_context` call in the router hands
     the context a `turn_id`. The failing paths (a regeneration, a ticket stream opened after a
     restart) are exactly the ones an end-to-end run rarely exercises.
  2. **Over HTTP** — each driver (POST /turns precompute, POST /regenerate, GET /stream, POST
     /answer blocking and streamed) reaches the model with the ids the database actually holds.
     The model is a local fake that records its keyword arguments; no key, no network.
"""
import ast
import dataclasses
import uuid
from pathlib import Path

import pytest

from app.services import attribution, chat, chat_store, llm, settings_store
from conftest import sql

CHAT_SRC = Path(chat.__file__).read_text(encoding="utf-8")
LLM_SRC = Path(llm.__file__).read_text(encoding="utf-8")
ROUTER_SRC = (Path(chat.__file__).parent.parent / "routers" / "chat.py").read_text(encoding="utf-8")

ATTRIBUTION_KEYWORDS = ("conversation_id", "turn_id", "suggest_ref")
MODEL_CALLS = ("call_tool", "stream_text")


# --------------------------------------------------------------------------- #
# 1. Structure
# --------------------------------------------------------------------------- #
def _model_calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in MODEL_CALLS
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "llm"]


def test_chat_context_carries_a_turn_id():
    fields = {f.name: f for f in dataclasses.fields(chat.ChatContext)}
    assert "turn_id" in fields, "ChatContext lost turn_id — usage rows cannot name the question"
    # Defaulted: the copilot tests and any older construction site pass none.
    assert fields["turn_id"].default is None


def test_the_seam_accepts_the_attribution_keywords():
    """Read off llm.py's source, not `inspect.signature`: conftest's autouse `no_llm` has already
    replaced both functions with a `**kw` detonator that would accept anything."""
    tree = ast.parse(LLM_SRC)
    for name in MODEL_CALLS:
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name),
                  None)
        assert fn is not None, f"llm.{name} not found — did it move?"
        accepted = {a.arg for a in fn.args.kwonlyargs}
        missing = set(ATTRIBUTION_KEYWORDS) - accepted
        assert not missing, f"llm.{name} does not accept {sorted(missing)}"


def test_every_chat_model_call_passes_conversation_turn_and_suggest_ref():
    calls = _model_calls(ast.parse(CHAT_SRC))
    features = sorted(
        next((kw.value.value for kw in c.keywords
              if kw.arg == "feature" and isinstance(kw.value, ast.Constant)), "?")
        for c in calls)
    # The four chat features. A new call site is welcome; it just has to be attributed too,
    # and this list is where the reviewer notices it.
    assert features == ["autopilot", "copilot", "handoff", "triage"], features

    for call in calls:
        passed = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        for key in ATTRIBUTION_KEYWORDS:
            assert key in passed, f"services/chat.py:{call.lineno} does not pass {key}="
            value = passed[key]
            assert (isinstance(value, ast.Attribute) and value.attr == key
                    and isinstance(value.value, ast.Name) and value.value.id == "ctx"), (
                f"services/chat.py:{call.lineno} passes {key}= from somewhere other than "
                f"ctx.{key}")


def test_every_router_context_is_given_a_turn_id():
    calls = [node for node in ast.walk(ast.parse(ROUTER_SRC))
             if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_build_context"]
    assert len(calls) >= 3, "fewer _build_context call sites than drivers — did one move?"
    for call in calls:
        assert any(kw.arg == "turn_id" for kw in call.keywords), (
            f"routers/chat.py:{call.lineno} builds a context without turn_id=; its usage rows "
            f"will not name the message that caused them")


# --------------------------------------------------------------------------- #
# 2. Over HTTP, against the rows the database holds
# --------------------------------------------------------------------------- #
ANSWER_TEXT = "You can see it in the account area [1]."
RESULT = {"method": "vector", "top_score": 0.91, "query_top_scores": [0.91], "kb_present": True,
          "hits": [{"chunk_id": "chunk-1", "document_id": "doc-1", "chunk_index": 0,
                    "content": "orders can be tracked in the account area", "metadata": {},
                    "title": "Orders", "doc_type": "policy", "score": 0.91}]}


@pytest.fixture
def fake_model(monkeypatch):
    """Records every model call's keywords plus the actor visible at call time."""
    seen: list[dict] = []

    def _note(kw):
        seen.append({**kw, "_actor": attribution.current()[0]})

    async def _tool(**kw):
        _note(kw)
        if kw.get("feature") == "copilot":
            return {"suggestions": [{"kind": "answer", "text": ANSWER_TEXT}],
                    "handoff_recommended": False}
        if kw.get("feature") == "handoff":
            return {"summary": "Customer needs a colleague.", "customer_goal": "help"}
        return {"kind": "chitchat", "reply": "Hello!"}

    async def _stream(**kw):
        _note(kw)
        for word in ANSWER_TEXT.split(" "):
            yield word + " "

    async def _retrieve(client_id, query, *, top_k=8, visibility=None, extra_queries=None,
                        min_score=None):
        return dict(RESULT)

    async def _kill(*, force: bool = False):
        return {"global_disabled": False, "disabled_clients": []}

    monkeypatch.setattr(llm, "call_tool", _tool, raising=False)
    monkeypatch.setattr(llm, "stream_text", _stream, raising=False)
    monkeypatch.setattr(chat, "retrieve_ranked", _retrieve)
    monkeypatch.setattr(settings_store, "get_autopilot_kill_switch", _kill)
    settings_store._kill_cache = None
    yield seen
    settings_store._kill_cache = None


def _headers(seed, *, write: bool = True) -> dict:
    h = {"X-CQ-Key": seed["integration"]["api_key"], "X-CQ-Tenant": seed["a"]["slug"]}
    if write:
        h["X-CQ-Expect-Tenant"] = seed["a"]["slug"]
    return h


def _turn(api, seed, **extra) -> dict:
    body = {"conversation_ref": f"usage-{uuid.uuid4().hex[:12]}",
            "turn_ref": f"t-{uuid.uuid4().hex[:12]}", "role": "customer",
            "content": "how can i see where my order is?", "channel": "web", "locale": "en",
            **extra}
    r = api.post("/v1/chat/turns", headers=_headers(seed), json=body)
    assert r.status_code == 202, r.text
    return r.json()


def _stored(seed, suggest_ref: str) -> dict:
    """The suggestion row and its turn, read straight from the tables."""
    cid = uuid.UUID(seed["a"]["client_id"])

    async def _read(conn):
        row = await conn.fetchrow(
            "SELECT s.conversation_id, s.turn_id, t.conversation_id AS turn_conversation_id "
            "FROM copilot_suggestions s JOIN chat_turns t "
            "  ON t.id = s.turn_id AND t.client_id = s.client_id "
            "WHERE s.client_id = $1 AND s.suggest_ref = $2", cid, suggest_ref)
        return dict(row) if row else None

    row = sql(_read)
    assert row, f"no stored suggestion/turn for {suggest_ref}"
    assert row["conversation_id"] == row["turn_conversation_id"]
    return {"conversation_id": str(row["conversation_id"]), "turn_id": str(row["turn_id"])}


def _only(seen: list[dict], feature: str) -> dict:
    hits = [c for c in seen if c.get("feature") == feature]
    assert len(hits) == 1, [c.get("feature") for c in seen]
    return hits[0]


def _assert_attributed(call: dict, *, conversation_id: str, turn_id: str, suggest_ref: str):
    assert call["conversation_id"] == conversation_id
    assert call["turn_id"] == turn_id
    assert call["suggest_ref"] == suggest_ref


def test_a_precomputed_copilot_suggestion_names_its_turn(api, seed, fake_model):
    out = _turn(api, seed)
    stored = _stored(seed, out["suggest_ref"])
    assert stored == {"conversation_id": out["conversation_id"], "turn_id": out["turn_id"]}

    call = _only(fake_model, "copilot")
    _assert_attributed(call, suggest_ref=out["suggest_ref"], **stored)
    assert call["_actor"] == f"integration:{seed['integration']['integration_id']}"


def test_a_regeneration_names_the_original_turn_and_its_own_ref(api, seed, fake_model):
    out = _turn(api, seed)
    fake_model.clear()

    r = api.post("/v1/chat/regenerate", headers=_headers(seed),
                 json={"suggest_ref": out["suggest_ref"], "transform": "shorter"})
    assert r.status_code == 202, r.text
    new_ref = r.json()["suggest_ref"]
    assert new_ref != out["suggest_ref"]

    call = _only(fake_model, "copilot")
    _assert_attributed(call, conversation_id=out["conversation_id"], turn_id=out["turn_id"],
                       suggest_ref=new_ref)
    assert _stored(seed, new_ref)["turn_id"] == out["turn_id"]


def test_a_ticket_stream_names_the_turn_and_the_integration(api, seed, fake_model):
    """The ticket route has no `resolve_principal`, so the actor is set from the ticket."""
    out = _turn(api, seed, precompute=False)
    assert not fake_model, "precompute=false still called the model"

    t = api.post("/v1/chat/stream-tickets", headers=_headers(seed, write=False),
                 json={"suggest_ref": out["suggest_ref"]})
    assert t.status_code == 200, t.text
    r = api.get("/v1/chat/stream", params={"ticket": t.json()["ticket"]})
    assert r.status_code == 200, r.text
    assert "event: done" in r.text, r.text[-400:]

    call = _only(fake_model, "copilot")
    _assert_attributed(call, suggest_ref=out["suggest_ref"], **_stored(seed, out["suggest_ref"]))
    assert call["_actor"] == f"integration:{seed['integration']['integration_id']}"


@pytest.mark.parametrize("streamed", [False, True], ids=["blocking", "sse"])
def test_an_autopilot_answer_names_its_turn(api, seed, fake_model, monkeypatch, streamed):
    real = chat_store.get_chat_config

    async def _cfg(client_id):
        return {**(await real(client_id)), "autopilot_enabled": True}

    monkeypatch.setattr(chat_store, "get_chat_config", _cfg)
    body = {"conversation_ref": f"usage-{uuid.uuid4().hex[:12]}",
            "turn_ref": f"t-{uuid.uuid4().hex[:12]}",
            "content": "how can i see where my order is?", "channel": "web", "locale": "en"}
    r = api.post("/v1/chat/answer", headers=_headers(seed), json=body,
                 params={"stream": 1} if streamed else None)
    assert r.status_code == 200, r.text

    async def _ids(conn):
        return await conn.fetchrow(
            "SELECT t.id AS turn_id, t.conversation_id FROM chat_turns t "
            "WHERE t.client_id = $1 AND t.turn_ref = $2",
            uuid.UUID(seed["a"]["client_id"]), body["turn_ref"])

    row = sql(_ids)
    assert row, "the answer's turn was not stored"
    turn_id, conversation_id = str(row["turn_id"]), str(row["conversation_id"])
    if not streamed:
        assert r.json()["turn_id"] == turn_id

    call = _only(fake_model, "autopilot")
    _assert_attributed(call, conversation_id=conversation_id, turn_id=turn_id,
                       suggest_ref=f"an_{turn_id}")
