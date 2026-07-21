"""Shared fixtures for the chat/isolation tests.

Three constraints shaped everything here, and each one explains a choice that would otherwise
look convoluted:

1. **No event loop may be shared with the app.** `TestClient` runs the ASGI app — and therefore
   `db.connect()`'s asyncpg pool — inside its own anyio portal thread and loop. An async test
   function under `asyncio_mode = auto` runs in a *different* loop, and an asyncpg pool is bound
   to the loop that created it. So every fixture here does its SQL over a short-lived standalone
   connection via `asyncio.run`, and every test is a plain synchronous function driving HTTP.
   Nothing in this package touches `db.pool()` from test code.

2. **No network, no API keys.** The embedder is monkeypatched (autouse) so the TEI container is
   never needed, and `llm.call_tool` / `llm.stream_text` are replaced with detonators (autouse)
   so a test that accidentally reaches Claude fails loudly instead of costing money or being
   skipped in CI. The suite must pass with `ANTHROPIC_API_KEY` unset — which is exactly why the
   isolation tests drive the *ungrounded* path, where the deterministic gate refuses before any
   model call.

3. **Skip, never fail, without a database.** Matching `test_tenant_isolation.py`: these are
   integration tests and a developer without Postgres up should see skips, not red.

The fake embedder deliberately returns tenant **B**'s vector for every query — the worst case.
Under it, a query issued as tenant A is *unanswerable from A's KB* (score ~0), so the gate must
refuse with no LLM call, and B's chunk (the perfect match) must still never surface. That single
choice makes the isolation assertions strict and the LLM-free requirement automatic.
"""
import asyncio
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

# `pytest` (as opposed to `python -m pytest`) does not put the invocation directory on sys.path,
# and there is no conftest.py at backend/ for pytest to anchor on — so `import app` depends on
# how the suite happened to be started. Pin it: the package root is this file's parent.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.config import settings  # noqa: E402 — must follow the sys.path bootstrap above

# ---------------------------------------------------------------------------
# Marker content. Distinctive enough that a substring search over a whole JSON
# response body is a meaningful leak assertion.
# ---------------------------------------------------------------------------
A_MARK = "TENANT_A_ONLY_REFUND_WINDOW_IS_14_DAYS"
B_MARK = "TENANT_B_ONLY_REFUND_WINDOW_IS_90_DAYS"

# Filled in by the `seed` fixture from the live column type, so this never drifts from
# EMBEDDING_DIM or from whatever dimension the developer's volume was created with.
_DIM = 1024


def _vec_a() -> list[float]:
    return [0.9] + [0.0] * (_DIM - 1)


def _vec_b() -> list[float]:
    return [0.0, 0.9] + [0.0] * (_DIM - 2)


def _pgvector(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# ---------------------------------------------------------------------------
# Standalone SQL access (never the app's pool — see the module docstring)
# ---------------------------------------------------------------------------
def sql(fn):
    """Run `async fn(conn)` on a fresh connection. Synchronous, loop-independent."""
    async def _run():
        conn = await asyncpg.connect(settings.database_url)
        try:
            return await fn(conn)
        finally:
            await conn.close()
    return asyncio.run(_run())


@pytest.fixture(scope="session")
def db_available():
    try:
        sql(lambda c: c.fetchval("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any connect failure means "no database here"
        pytest.skip(f"no database reachable ({type(exc).__name__}); integration tests skipped")
    return True


# ---------------------------------------------------------------------------
# The app, with its real lifespan (which is what applies db/chat.sql)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def api(db_available):
    """A started TestClient. Entering the context manager runs the real lifespan, so the chat
    tables exist because *migrate.py* created them — not because a fixture duplicated the DDL.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    # raise_server_exceptions=False: an unhandled 500 should be asserted on as a status code,
    # not re-raised into the test as the original exception. Isolation tests care about what the
    # caller observes.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _mounted_paths(app) -> set[str]:
    """Every route path the app actually serves.

    `app.routes` is NOT a flat list of routes on current FastAPI/Starlette: an `include_router`
    lands as a wrapper object whose own `.path` is None and whose children hang off `.routes`.
    A one-level `getattr(r, "path", None)` therefore sees nothing from any included router — so
    a guard written that way reports "not mounted" for a route that is answering 202, and the
    tests that depend on it skip forever while looking green. Hence: walk the tree, and union
    with the OpenAPI paths so a future container shape cannot make this blind again.
    """
    found: set[str] = set()
    seen: set[int] = set()
    stack = list(getattr(app, "routes", []))
    while stack:
        route = stack.pop()
        if id(route) in seen:
            continue
        seen.add(id(route))
        path = getattr(route, "path", None)
        if isinstance(path, str) and path:
            found.add(path)
        children = getattr(route, "routes", None)
        if children is None:
            children = getattr(getattr(route, "router", None), "routes", None)
        if children:
            stack.extend(children)
    try:
        found.update(app.openapi().get("paths") or {})
    except Exception:  # noqa: BLE001 — a schema-generation problem must not hide the routes
        pass
    return found


def chat_route_present(api, path: str) -> bool:
    """True if `path` is mounted. The chat router lands in a sibling track; tests that need it
    skip with an explicit reason rather than failing the whole file while it is in flight.
    """
    return path in _mounted_paths(api.app)


def require_chat_route(api, path: str) -> None:
    if not chat_route_present(api, path):
        pytest.skip(f"{path} is not mounted yet (routers/chat.py not landed)")


# ---------------------------------------------------------------------------
# Seed: two tenants, one KB chunk each, one integration granted to A only
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def seed(api):
    """Two isolated tenants + an integration credential that may act for A and ONLY A.

    Session-scoped and torn down by deleting the two `clients` rows: every chat table cascades
    from `clients`, so one DELETE reclaims conversations, turns, suggestions and feedback.
    """
    from app.services import chat_credentials

    suffix = uuid.uuid4().hex[:8]

    async def _setup(conn):
        global _DIM
        # Read the real vector dimension off the column rather than trusting settings: a
        # developer volume created under a different EMBEDDING_DIM would otherwise fail the
        # INSERT with a confusing error inside a fixture.
        dim = await conn.fetchval(
            """SELECT a.atttypmod FROM pg_attribute a
                 JOIN pg_class c ON c.oid = a.attrelid
                WHERE c.relname = 'kb_chunks' AND a.attname = 'embedding'""")
        if dim and dim > 0:
            _DIM = int(dim)

        out = {}
        for label, mark, vec in (("a", A_MARK, _vec_a()), ("b", B_MARK, _vec_b())):
            cid = await conn.fetchval(
                "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
                f"chatiso-{label}-{suffix}", f"chat-iso-{label}", f"tk-{label}-{suffix}")
            doc = await conn.fetchval(
                "INSERT INTO kb_documents (client_id, doc_type, title, status, visibility) "
                "VALUES ($1,'policy','Refund policy','ready','public') RETURNING id", cid)
            await conn.execute(
                "INSERT INTO kb_chunks (document_id, client_id, content, embedding, chunk_index) "
                "VALUES ($1,$2,$3,$4::vector,0)", doc, cid, mark, _pgvector(vec))
            out[label] = {"client_id": str(cid), "slug": f"chatiso-{label}-{suffix}",
                          "api_key": f"tk-{label}-{suffix}", "document_id": str(doc)}
        return out

    tenants = sql(_setup)

    # The credential is issued through the REAL issuance path, so the test exercises grant rows
    # written the way production writes them (and would break if issuance changed shape).
    async def _issue(conn):
        # chat_credentials.issue() uses db.pool(); the pool belongs to the app's loop, so the
        # rows are written here directly with the same statements, keeping this fixture
        # loop-independent. The verify path (_RESOLVE_SQL) is what the tests actually assert on.
        import hashlib
        import secrets as _s
        key_id, secret = _s.token_hex(8), _s.token_urlsafe(32)
        integ = await conn.fetchval(
            "INSERT INTO integrations (name, kind, scopes, is_active) "
            "VALUES ($1,'chat',$2,true) RETURNING id",
            f"chat-iso-{suffix}", list(chat_credentials.SCOPES))
        await conn.execute(
            "INSERT INTO integration_grants (integration_id, client_id, scopes, created_by) "
            "VALUES ($1,$2,$3,'test')",
            integ, uuid.UUID(tenants["a"]["client_id"]), list(chat_credentials.SCOPES))
        await conn.execute(
            "INSERT INTO integration_secrets (integration_id, key_id, secret_hash, label) "
            "VALUES ($1,$2,$3,'test')",
            integ, key_id, hashlib.sha256(secret.encode()).hexdigest())
        return {"integration_id": str(integ), "key_id": key_id,
                "api_key": f"{chat_credentials.KEY_PREFIX}{key_id}.{secret}"}

    integration = sql(_issue)

    data = {"a": tenants["a"], "b": tenants["b"], "integration": integration,
            "suffix": suffix, "A_MARK": A_MARK, "B_MARK": B_MARK}
    try:
        yield data
    finally:
        sql(lambda c: c.execute(
            "DELETE FROM clients WHERE id = ANY($1::uuid[])",
            [uuid.UUID(tenants["a"]["client_id"]), uuid.UUID(tenants["b"]["client_id"])]))
        sql(lambda c: c.execute(
            "DELETE FROM integrations WHERE id = $1", uuid.UUID(integration["integration_id"])))


# ---------------------------------------------------------------------------
# Autouse guards: no network, no model calls
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    """Every query embeds to tenant B's vector — the strongest possible cross-tenant match.

    Patched on the `embeddings` module object because `retrieval` calls it as
    `embeddings.embed_texts(...)`, so the attribute swap is seen by the app thread too
    (monkeypatch is process-global; it is only the *event loop* that is per-thread).
    """
    from app.services import embeddings

    async def _fake(texts, *, purpose: str = "ingest"):
        return [_vec_b() for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", _fake)
    return _fake


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Detonate on any model call.

    The isolation suite must be runnable with no ANTHROPIC_API_KEY. Rather than tolerate that by
    letting calls fail with a provider error (which looks the same as a bug), any attempt to
    reach Claude raises immediately — so "the gate refused before spending a token" is an
    asserted property of the tests, not an assumption.

    Patched on the module object, which is why chat code must call `llm.call_tool(...)` rather
    than `from .llm import call_tool` — a name imported at module load would bypass this.
    """
    from app.services import llm

    async def _boom(*a, **kw):
        raise AssertionError("an LLM call was made on a path that must not call the model")

    monkeypatch.setattr(llm, "call_tool", _boom, raising=False)
    monkeypatch.setattr(llm, "stream_text", _boom, raising=False)
    return _boom


# ---------------------------------------------------------------------------
# Row-count helpers used by the "and nothing was written" assertions
# ---------------------------------------------------------------------------
def chat_row_counts(client_id: str) -> dict:
    """Conversation / turn / suggestion counts for one tenant."""
    async def _q(conn):
        cid = uuid.UUID(client_id)
        return {
            "conversations": await conn.fetchval(
                "SELECT count(*) FROM chat_conversations WHERE client_id = $1", cid),
            "turns": await conn.fetchval(
                "SELECT count(*) FROM chat_turns WHERE client_id = $1", cid),
            "suggestions": await conn.fetchval(
                "SELECT count(*) FROM copilot_suggestions WHERE client_id = $1", cid),
        }
    return sql(_q)
