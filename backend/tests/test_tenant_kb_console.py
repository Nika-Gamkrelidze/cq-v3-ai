"""The tenant KB console: everything the operator can do, to its OWN knowledge base only.

`routers/kb.py` grew from 8 routes to 23 by delegating to `services/kb_console.py` — the same
functions `routers/kb_admin.py` calls. That is the right shape, and it is also the reason this
file is long: one shared implementation means one place to get tenant scoping wrong, and the
blast radius of getting it wrong is now three surfaces at once —

  * `/kb/*`      the tenant portal (bearer token or the tenant's own API key),
  * `/v1/kb/*`   the SAME router, mounted again for the B2B partner API,
  * `/admin/kb/{tenant_id}/*` the operator console, which must not have changed at all.

So the first and most important test here is not a feature test: it is a table of every new
route, driven twice — once with tenant A's credential against tenant B's ids, and once as B —
asserting that A gets a 404 or an empty result and that B's rows are byte-identical afterwards.
**A route that 404s and a route that leaks are very different outcomes**, so each case declares
which one it expects rather than accepting "not a 200". `test_every_tenant_kb_route_is_covered`
fails if a future route is added without landing in one of those tables, because an isolation
suite that silently stops covering new surface is worse than none.

The rest pins the behaviours that are easy to "simplify" back into bugs:

  * **Editing a document's text must RE-CHUNK it** (`kb_ingest.ingest_document`), not merely
    replace its vectors (`kb_ingest.reembed_document`, which never reads `content_text`). The
    latter is the tempting call — right there, correctly named, signature fits — and it leaves
    the old wording retrievable while every other observable says success. Named so that anyone
    who makes that change reads why it is wrong in the failure output.
  * **A failed re-embed is a 502, not `{"updated": true}`.** `ingest_document` swallows every
    exception into `status='error'` and returns None, so an await that returns says nothing.
  * **The full-KB re-embed is QUEUED, never run in the request.** Proved by detonating the
    encoder for the duration of the call: it is the single most expensive thing a tenant can
    trigger on a shared CPU-bound TEI container, and the double-click guard is a partial unique
    index in the database, not a disabled button.

Mechanics (skip-without-a-database, the loop-independent `sql()` helper, why every test is a
plain synchronous function driving HTTP) are inherited from `conftest.py`; read its docstring
before adding to this file. Two local deviations from conftest, both deliberate:

  * a **content-derived** fake encoder replaces conftest's constant one, because these tests
    ingest and retrieve real text and need identical text to embed identically;
  * `run_async()` (mirrored from `test_curation_apply.py`) is the only way to call an async
    service function directly, since the app's asyncpg pool belongs to the TestClient's loop.

No test here needs an Anthropic key: nothing on this surface calls a model, and conftest's
autouse detonator turns any attempt into a failure.
"""
import asyncio
import csv
import hashlib
import io
import json
import uuid
from collections import namedtuple
from pathlib import Path

import asyncpg
import pytest

from conftest import sql  # loop-independent standalone SQL; see its module docstring

from app.config import settings  # noqa: E402 — conftest performed the sys.path bootstrap
from app.services import embeddings, kb_ingest, retrieval
from app.services.embeddings.base import EmbeddingError

KB_CONSOLE = Path(__file__).resolve().parent.parent / "app" / "services" / "kb_console.py"

pytestmark = pytest.mark.skipif(
    not KB_CONSOLE.exists(),
    reason="app/services/kb_console.py has not landed yet")


# --------------------------------------------------------------------------- #
# Markers. Distinctive enough that a substring search over a whole response body
# (or a CSV export) is a meaningful leak assertion.
# --------------------------------------------------------------------------- #
A_MARK = "TENANT_A_ONLY_REFUND_WINDOW_IS_14_DAYS"
B_MARK = "TENANT_B_ONLY_REFUND_WINDOW_IS_90_DAYS"
EDIT_MARK = "HUMAN_EDITED_REFUND_WINDOW_IS_30_DAYS"

A_TEXT = f"{A_MARK}. Refunds are processed within fourteen days of the request."
B_TEXT = f"{B_MARK}. Refunds are processed within ninety days of the request."
# Long enough to chunk into several pieces (kb_ingest.CHUNK_SIZE is 1000 characters): that is
# what proves a re-chunk happened rather than a vector-only refresh.
EDITED_TEXT = f"{EDIT_MARK}. " + "Refunds are processed within thirty days. " * 60


# --------------------------------------------------------------------------- #
# Vectors + a loop-bound pool for direct service calls
# --------------------------------------------------------------------------- #
_DIM = 1024   # refreshed from the live column by the autouse `embedder` fixture


def _vec(text: str) -> list[float]:
    """A deterministic one-hot vector per distinct text.

    Identical text embeds identically (cosine 1.0), different text lands on a different axis
    (cosine ~0). That gives exact, threshold-free control over what retrieval should and should
    not return, without a TEI container.
    """
    vec = [0.0] * _DIM
    vec[int(hashlib.sha1((text or "").encode()).hexdigest()[:8], 16) % _DIM] = 1.0
    return vec


def _pgvector(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def run_async(factory):
    """Run one async service call against a pool bound to THIS loop.

    The app's pool belongs to the TestClient's portal thread and loop, and an asyncpg pool
    cannot be used from another. Same trick as `test_curation_apply.run_async`: stand up a
    private pool, swap it in for the duration, restore. Safe because these tests are
    synchronous — nothing is in flight on the app side while this runs.
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
def embedder(api, monkeypatch):
    """Content-derived encoder at the LIVE column dimension.

    Overrides conftest's autouse `fake_embeddings` (which returns one constant vector for every
    input — perfect for its worst-case leak scenario, useless here, where a document is really
    ingested and then retrieved by its own text). Dimension is read off the column rather than
    from settings so a developer volume created under a different EMBEDDING_DIM fails with a
    real assertion instead of a confusing INSERT error.

    Deliberately NOT autouse, and requested by `two_tenants` instead: pytest sets autouse
    fixtures up before explicitly-requested ones of the same scope, so being requested is what
    guarantees this patch lands *after* conftest's — not the order the files happen to be read.
    """
    global _DIM
    dim = sql(lambda c: c.fetchval(
        """SELECT a.atttypmod FROM pg_attribute a
             JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'kb_chunks' AND a.attname = 'embedding'"""))
    if dim and dim > 0:
        _DIM = int(dim)

    async def _embed(texts, *, purpose: str = "ingest"):
        return [_vec(t) for t in texts]

    monkeypatch.setattr(embeddings, "embed_texts", _embed)
    return _embed


@pytest.fixture
def tenants(api):
    """Factory for isolated tenants. Every KB table cascades from `clients`, so one DELETE
    per tenant reclaims documents, chunks, events and re-embed jobs."""
    created: list[uuid.UUID] = []

    def _make(label: str) -> dict:
        suffix = uuid.uuid4().hex[:8]
        api_key = f"kbc-key-{label}-{suffix}"
        cid = sql(lambda c: c.fetchval(
            "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
            f"kbc-{label}-{suffix}", f"kb-console-{label}", api_key))
        created.append(cid)
        return {"client_id": str(cid), "api_key": api_key, "label": label}

    yield _make
    for cid in created:
        sql(lambda c, cid=cid: c.execute("DELETE FROM clients WHERE id = $1", cid))


@pytest.fixture
def two_tenants(tenants, embedder):
    """Tenant A and tenant B, each with one ready, internal, single-chunk document."""
    a, b = tenants("a"), tenants("b")
    for t, text, mark in ((a, A_TEXT, A_MARK), (b, B_TEXT, B_MARK)):
        t["doc"] = add_document(t["client_id"], text, title=f"Refund policy {mark}")
        t["chunk"] = str(chunks_of(t["doc"])[0]["id"])
    return a, b


@pytest.fixture
def both_busy(api, two_tenants):
    """Both tenants with something to leak on EVERY read route.

    Each gets a duplicate document (so `/kb/duplicates` has a group whose titles carry the
    marker), a published document (so `/kb/activity` has an event whose detail is the title),
    and a queued re-embed job (so `/kb/reembed/status` has a row carrying the tenant id).
    Without this the "B's marker is absent from A's response" assertions would pass on empty
    responses, which is the classic way an isolation suite goes quietly vacuous.
    """
    a, b = two_tenants
    for t, text, mark in ((a, A_TEXT, A_MARK), (b, B_TEXT, B_MARK)):
        t["dup"] = add_document(t["client_id"], text, title=f"Refund policy copy {mark}")
        assert call(api, t, "PATCH", f"/kb/documents/{t['doc']}/visibility",
                    json={"visibility": "public"}).status_code == 200
        assert call(api, t, "POST", "/kb/reembed").status_code == 202
    return a, b


# --------------------------------------------------------------------------- #
# Seeding + reads (raw SQL: these set up state, they are not what is under test)
# --------------------------------------------------------------------------- #
def add_document(client_id: str, text: str, *, title: str | None = None,
                 visibility: str = "internal", doc_type: str = "policy",
                 status: str = "ready", tags: list[str] | None = None) -> str:
    """A document written the way ingestion leaves it: chunked by the real chunker, embedded
    with the same vectors the fake encoder produces, checksummed the way kb_ingest does."""
    chunks = kb_ingest.chunk_text(text)

    async def _run(conn):
        doc = await conn.fetchval(
            """INSERT INTO kb_documents
                   (client_id, doc_type, title, status, visibility, source_type, actor,
                    content_text, chunk_count, char_count, checksum, tags)
               VALUES ($1,$2,$3,$4,$5,'paste','test',$6,$7,$8,$9,$10) RETURNING id""",
            uuid.UUID(client_id), doc_type, title or f"Doc {text[:24]}", status, visibility,
            text, len(chunks), len(text), hashlib.md5(text.encode()).hexdigest(),
            list(tags or []))
        for i, content in enumerate(chunks):
            await conn.execute(
                """INSERT INTO kb_chunks
                       (document_id, client_id, content, embedding, chunk_index, token_count)
                   VALUES ($1,$2,$3,$4::vector,$5,$6)""",
                doc, uuid.UUID(client_id), content, _pgvector(_vec(content)), i, len(content) // 4)
        return doc

    return str(sql(_run))


def document(doc_id: str) -> dict | None:
    row = sql(lambda c: c.fetchrow("SELECT * FROM kb_documents WHERE id = $1", uuid.UUID(doc_id)))
    return dict(row) if row else None


def documents_of(client_id: str) -> list[dict]:
    return [dict(r) for r in sql(lambda c: c.fetch(
        "SELECT * FROM kb_documents WHERE client_id = $1 ORDER BY created_at",
        uuid.UUID(client_id)))]


def chunks_of(doc_id: str) -> list[dict]:
    return [dict(r) for r in sql(lambda c: c.fetch(
        "SELECT * FROM kb_chunks WHERE document_id = $1 ORDER BY chunk_index", uuid.UUID(doc_id)))]


def events_of(client_id: str) -> list[dict]:
    return [dict(r) for r in sql(lambda c: c.fetch(
        "SELECT * FROM kb_events WHERE client_id = $1 ORDER BY created_at", uuid.UUID(client_id)))]


def jobs_of(client_id: str) -> list[dict]:
    return [dict(r) for r in sql(lambda c: c.fetch(
        "SELECT * FROM kb_reembed_jobs WHERE client_id = $1 ORDER BY created_at",
        uuid.UUID(client_id)))]


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def call(api, tenant: dict, method: str, path: str, json=None):
    """Drive a tenant route with that tenant's own API key.

    The API key (rather than a login token) is deliberate: it is the credential the `/v1/kb`
    partner mount accepts, so every assertion here holds for the partner surface too. It also
    makes the audit actor `tenant:apikey`, which some tests check.
    """
    return api.request(method, path, headers={"X-API-Key": tenant["api_key"]}, json=json)


def admin_call(api, method: str, path: str, json=None):
    return api.request(method, path, headers={"X-Admin-Token": settings.admin_token}, json=json)


def _kb_routes(app) -> set[tuple[str, str]]:
    """Every (method, path) the `/kb` mount serves.

    `app.routes` is not flat — an `include_router` lands as a wrapper whose children hang off
    `.routes` — so this walks the tree (same reasoning as `conftest._mounted_paths`). The `/v1`
    mount is excluded by the prefix filter: it is the same router object and the same handlers.
    """
    found: set[tuple[str, str]] = set()
    seen: set[int] = set()
    stack = list(getattr(app, "routes", []))
    while stack:
        route = stack.pop()
        if id(route) in seen:
            continue
        seen.add(id(route))
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if isinstance(path, str) and path.startswith("/kb") and methods:
            found.update((m, path) for m in methods if m not in ("HEAD", "OPTIONS"))
        children = getattr(route, "routes", None)
        if children is None:
            children = getattr(getattr(route, "router", None), "routes", None)
        if children:
            stack.extend(children)
    return found


# --------------------------------------------------------------------------- #
# 1. THE isolation tables. Do not weaken these.
# --------------------------------------------------------------------------- #
# Routes that NAME a document or chunk. Addressing another tenant's id must be indistinguishable
# from addressing one that does not exist: 404, no body content, no mutation. Anything else —
# 200, 403, a different error message — is information about another tenant's KB.
FOREIGN_RESOURCE_ROUTES = [
    pytest.param("GET", "/kb/documents/{doc_id}", None, id="GET-document"),
    pytest.param("PUT", "/kb/documents/{doc_id}", {"title": "HIJACKED"}, id="PUT-document-meta"),
    pytest.param("PUT", "/kb/documents/{doc_id}", {"text": "HIJACKED TEXT"}, id="PUT-document-text"),
    pytest.param("DELETE", "/kb/documents/{doc_id}", None, id="DELETE-document"),
    pytest.param("POST", "/kb/documents/{doc_id}/reembed", None, id="POST-document-reembed"),
    pytest.param("PUT", "/kb/documents/{doc_id}/visibility", {"visibility": "public"},
                 id="PUT-document-visibility"),
    pytest.param("PATCH", "/kb/documents/{doc_id}/visibility", {"visibility": "public"},
                 id="PATCH-document-visibility"),
    pytest.param("PUT", "/kb/chunks/{chunk_id}", {"content": "HIJACKED CHUNK"}, id="PUT-chunk"),
    pytest.param("DELETE", "/kb/chunks/{chunk_id}", None, id="DELETE-chunk"),
]

# Reads. `mirrors`: the same request as B must expose the needle, otherwise the "absent from A"
# assertion is vacuous. `self_evident`: A's own response must contain A's equivalent data, so a
# route cannot pass by returning nothing to anyone.
Read = namedtuple("Read", "method path body mirrors self_evident")

READ_ROUTES = [
    pytest.param(Read("GET", "/kb/documents", None, True, True), id="GET-documents"),
    pytest.param(Read("GET", "/kb/documents/{doc_id}/chunks", None, True, False), id="GET-chunks"),
    pytest.param(Read("GET", "/kb/stats", None, False, False), id="GET-stats"),
    pytest.param(Read("GET", "/kb/params", None, False, False), id="GET-params"),
    pytest.param(Read("GET", "/kb/public-count", None, False, False), id="GET-public-count"),
    pytest.param(Read("GET", "/kb/duplicates", None, True, True), id="GET-duplicates"),
    pytest.param(Read("GET", "/kb/export?format=json", None, True, True), id="GET-export-json"),
    pytest.param(Read("GET", "/kb/export?format=csv", None, True, True), id="GET-export-csv"),
    pytest.param(Read("GET", "/kb/activity", None, True, True), id="GET-activity"),
    pytest.param(Read("GET", "/kb/reembed/status", None, True, True), id="GET-reembed-status"),
    # The worst case on purpose: A asks, verbatim, the question only B's KB can answer.
    pytest.param(Read("POST", "/kb/playground", {"query": B_TEXT, "top_k": 20, "threshold": 0.0},
                      True, True), id="POST-playground"),
    # `self_evident=False`: /kb/search applies retrieval's real similarity floor, so whether A's
    # own chunk clears it for B's question is a tuning detail, not an isolation property.
    pytest.param(Read("POST", "/kb/search", {"query": B_TEXT, "top_k": 20}, True, False),
                 id="POST-search"),
]

# Routes with no tenant-addressable parameter at all, covered by a dedicated test each. Written
# out so `test_every_tenant_kb_route_is_covered` cannot be satisfied by forgetting one.
COVERED_BY_A_DEDICATED_TEST = {
    # Creation: client_id comes from the principal, so there is no foreign id to pass.
    ("POST", "/kb/documents/upload"): "test_import_routes_can_only_write_to_the_callers_own_tenant",
    ("POST", "/kb/documents/text"): "test_import_routes_can_only_write_to_the_callers_own_tenant",
    ("POST", "/kb/documents/csv"): "test_import_routes_can_only_write_to_the_callers_own_tenant",
    # Queue: one job per tenant, and the 409 guard must be per-tenant rather than global.
    ("POST", "/kb/reembed"): "test_full_kb_reembed_is_queued_and_never_runs_in_the_request",
    # Batch routes: they DO accept foreign ids, but the expected outcome is a 200 that changed
    # nothing (not a 404), so they get their own assertions.
    ("POST", "/kb/bulk"): "test_bulk_with_a_foreign_document_id_mutates_nothing",
    ("POST", "/kb/documents/visibility"): "test_bulk_visibility_ignores_another_tenants_documents",
}


def test_every_tenant_kb_route_is_covered(api):
    """Every route on the `/kb` mount must appear in one of the tables above.

    This is the test that keeps the rest of the file honest. The tenant console is the surface a
    partner API key reaches at `/v1/kb`; a route added later without an isolation case would
    leave that surface untested while the suite stays green.
    """
    covered = set(COVERED_BY_A_DEDICATED_TEST)
    for param in FOREIGN_RESOURCE_ROUTES:
        method, path, _ = param.values
        covered.add((method, path))
    for param in READ_ROUTES:
        case = param.values[0]
        covered.add((case.method, case.path.split("?")[0]))

    uncovered = _kb_routes(api.app) - covered
    assert not uncovered, (
        "these /kb routes have no tenant-isolation coverage: " + str(sorted(uncovered)) +
        "\nAdd each to FOREIGN_RESOURCE_ROUTES (it names a document/chunk id), to READ_ROUTES "
        "(it returns tenant data), or to COVERED_BY_A_DEDICATED_TEST with the test name.")


@pytest.mark.parametrize("method,path,body", FOREIGN_RESOURCE_ROUTES)
def test_a_tenant_cannot_address_another_tenants_document_or_chunk(
        api, two_tenants, method, path, body):
    a, b = two_tenants
    before_doc, before_chunks = document(b["doc"]), chunks_of(b["doc"])

    r = call(api, a, method, path.format(doc_id=b["doc"], chunk_id=b["chunk"]), json=body)

    assert r.status_code == 404, (
        f"{method} {path} answered {r.status_code} for another tenant's id. It must be a 404 — "
        f"indistinguishable from an id that does not exist. Body: {r.text[:200]}")
    assert B_MARK not in r.text, f"LEAK: {method} {path} returned another tenant's content"
    assert document(b["doc"]) == before_doc, f"{method} {path} MUTATED another tenant's document"
    assert chunks_of(b["doc"]) == before_chunks, f"{method} {path} MUTATED another tenant's chunks"


@pytest.mark.parametrize("case", READ_ROUTES)
def test_reads_never_surface_the_other_tenant(api, both_busy, case):
    a, b = both_busy
    path = case.path.format(doc_id=b["doc"], chunk_id=b["chunk"])

    mine = call(api, a, case.method, path, json=case.body)
    assert mine.status_code == 200, f"{case.method} {case.path} -> {mine.status_code} {mine.text[:200]}"

    for label, needle in (("B's marker", B_MARK), ("B's document id", b["doc"]),
                          ("B's tenant id", b["client_id"])):
        assert needle not in mine.text, (
            f"LEAK: {case.method} {case.path} returned {label} to another tenant")

    if case.self_evident:
        assert any(n in mine.text for n in (A_MARK, a["doc"], a["client_id"])), (
            f"{case.method} {case.path} returned none of the CALLER's own data, so the leak "
            "assertion above proves nothing. Fix the fixture, not the assertion.")

    if case.mirrors:
        theirs = call(api, b, case.method, path, json=case.body)
        assert theirs.status_code == 200
        assert any(n in theirs.text for n in (B_MARK, b["doc"], b["client_id"])), (
            f"{case.method} {case.path} does not expose these needles even to their owner — "
            "the isolation assertion above is vacuous for this route.")


def test_stats_count_only_the_callers_own_kb(api, both_busy):
    """`/kb/stats` carries no ids or text, so the substring guard above cannot see a leak here —
    the numbers are the only observable, and they must be the caller's alone."""
    a, b = both_busy
    stats = call(api, a, "GET", "/kb/stats").json()
    assert stats["documents"] == len(documents_of(a["client_id"])) == 2
    assert stats["public_documents"] == 1
    assert stats["chunks"] == sum(len(chunks_of(d)) for d in (a["doc"], a["dup"]))
    assert stats["chunks_no_embedding"] == 0 and stats["embedding_coverage"] == 100
    assert call(api, a, "GET", "/kb/public-count").json() == {"public_documents": 1}
    # B is busy in exactly the same way; identical numbers must come from B's own rows.
    assert call(api, b, "GET", "/kb/stats").json()["documents"] == 2


def test_activity_log_is_tenant_scoped(api, both_busy):
    a, b = both_busy
    events = call(api, a, "GET", "/kb/activity").json()["events"]
    assert isinstance(events, list) and events
    assert {e["client_id"] for e in events} == {a["client_id"]}
    assert any(e["action"] == "publish" for e in events)
    # The audit actor distinguishes a server-to-server key from a person and from the operator;
    # an activity log where every row says "tenant" answers no useful question.
    assert {e["actor"] for e in events} == {"tenant:apikey"}
    assert b["doc"] not in json.dumps(events)


def test_duplicates_are_tenant_scoped(api, both_busy):
    a, b = both_busy
    dupes = call(api, a, "GET", "/kb/duplicates").json()
    groups = dupes["exact_duplicate_groups"]
    assert len(groups) == 1, "A's two identical documents should form exactly one checksum group"
    assert sorted(groups[0]["document_ids"]) == sorted([a["doc"], a["dup"]])
    assert dupes["near_scan_skipped"] is False
    near = dupes["near_duplicate_pairs"]
    assert near, "A's two identical documents should also surface as a near-duplicate pair"
    assert all(A_MARK in p["a_title"] and A_MARK in p["b_title"] for p in near), (
        "the O(n^2) chunk self-join paired a chunk with one outside the caller's tenant")
    assert B_MARK not in json.dumps(dupes)


def test_export_contains_only_the_callers_own_documents(api, both_busy):
    a, b = both_busy

    as_json = call(api, a, "GET", "/kb/export?format=json")
    payload = as_json.json()
    assert as_json.headers["content-disposition"] == (
        f'attachment; filename="kb-{a["client_id"][:8]}.json"')
    assert payload["tenant_id"] == a["client_id"]
    assert payload["document_count"] == 2
    assert sorted(d["id"] for d in payload["documents"]) == sorted([a["doc"], a["dup"]])
    assert B_MARK not in as_json.text

    as_csv = call(api, a, "GET", "/kb/export?format=csv")
    assert as_csv.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(as_csv.text)))
    assert rows[0][0] == "id"
    assert sorted(r[0] for r in rows[1:]) == sorted([a["doc"], a["dup"]])
    assert B_MARK not in as_csv.text


def test_import_routes_can_only_write_to_the_callers_own_tenant(api, two_tenants):
    """The creation routes take no tenant id — `client_id` comes from the principal. Pinned so a
    future "let the caller name the tenant" convenience cannot be added without a red test."""
    a, b = two_tenants
    before = documents_of(b["client_id"])

    created = call(api, a, "POST", "/kb/documents/text",
                   json={"title": "New note", "doc_type": "note", "text": f"{A_MARK} appendix."})
    assert created.status_code == 200
    doc_id = created.json()["id"]
    assert str(document(doc_id)["client_id"]) == a["client_id"]
    assert documents_of(b["client_id"]) == before


# --------------------------------------------------------------------------- #
# 2. The operator console must be exactly as it was
# --------------------------------------------------------------------------- #
def test_superadmin_console_routes_keep_their_shapes(api, two_tenants):
    """`kb-admin.html` is a live consumer of all 20 operator routes and there is no build step
    or type check between it and these responses. The refactor into `kb_console` was supposed to
    be shape-neutral; this is what says so."""
    a, _ = two_tenants
    tid = a["client_id"]

    stats = admin_call(api, "GET", f"/admin/kb/{tid}/stats")
    assert stats.status_code == 200
    assert {"documents", "failed", "in_progress", "public_documents", "chunks",
            "chunks_no_embedding", "empty_chunks", "approx_tokens", "approx_chars",
            "last_updated", "embedding_coverage"} <= set(stats.json())

    params = admin_call(api, "GET", f"/admin/kb/{tid}/params").json()
    assert {"embedding", "chunking", "retrieval", "warnings"} <= set(params)
    assert {"provider", "model", "dim", "column_dim", "dim_mismatch"} <= set(params["embedding"])
    # A bool, not necessarily False: a developer volume created under a different EMBEDDING_DIM
    # legitimately reports True. What must not change is that the alarm is present and typed.
    assert isinstance(params["embedding"]["dim_mismatch"], bool)

    docs = admin_call(api, "GET", f"/admin/kb/{tid}/documents").json()
    assert {"total", "limit", "offset", "documents"} <= set(docs), (
        "the operator list keeps its envelope; only the TENANT list returns a bare array")
    assert docs["total"] == 1
    assert {"id", "doc_type", "title", "status", "visibility", "tags", "chunk_count",
            "source_type", "actor", "ingest_ms", "created_at"} <= set(docs["documents"][0])

    one = admin_call(api, "GET", f"/admin/kb/{tid}/documents/{a['doc']}").json()
    assert one["id"] == a["doc"] and A_MARK in one["content_text"]

    chunks = admin_call(api, "GET", f"/admin/kb/{tid}/documents/{a['doc']}/chunks").json()
    assert isinstance(chunks, list), "this route has always returned a BARE list, unpaginated"
    assert {"id", "chunk_index", "content", "has_embedding", "token_count"} <= set(chunks[0])

    assert set(admin_call(api, "GET", f"/admin/kb/{tid}/activity").json()) == {"events"}
    assert {"exact_duplicate_groups", "near_duplicate_pairs", "near_scan_skipped"} == set(
        admin_call(api, "GET", f"/admin/kb/{tid}/duplicates").json())
    assert {"method", "results"} <= set(
        admin_call(api, "POST", f"/admin/kb/{tid}/playground", json={"query": A_TEXT}).json())
    assert admin_call(api, "POST", f"/admin/kb/{tid}/bulk",
                      json={"action": "retag", "document_ids": [a["doc"]],
                            "tags": ["reviewed"]}).json() == {"action": "retag", "affected": 1}

    export = admin_call(api, "GET", f"/admin/kb/{tid}/export?format=csv")
    assert export.headers["content-disposition"] == f'attachment; filename="kb-{tid[:8]}.csv"'
    assert A_MARK in export.text


def test_tenant_and_operator_see_the_same_stats(api, two_tenants):
    """The point of the shared service: two front doors, one implementation. If these ever
    disagree, one of them has grown its own copy of a tenant-scoped query."""
    a, _ = two_tenants
    assert (call(api, a, "GET", "/kb/stats").json()
            == admin_call(api, "GET", f"/admin/kb/{a['client_id']}/stats").json())


def test_operator_full_kb_reembed_still_runs_inline_and_queues_nothing(api, two_tenants):
    """The asymmetry is deliberate, so it is asserted rather than left to a comment: the tenant's
    full-KB re-embed is queued to cq-worker; the operator's predates the queue, returns counts
    inline, and `kb-admin.html` reads that shape."""
    a, _ = two_tenants
    r = admin_call(api, "POST", f"/admin/kb/{a['client_id']}/reembed")
    assert r.status_code == 200
    assert r.json() == {"documents": 1, "reembedded_chunks": 1}
    assert jobs_of(a["client_id"]) == [], "the operator path must not touch the tenant queue"


# --------------------------------------------------------------------------- #
# 3. Editing text must RE-CHUNK. Do not weaken this test.
# --------------------------------------------------------------------------- #
def test_editing_document_text_rechunks_it_rather_than_only_replacing_vectors(api, two_tenants):
    """A text edit must change what retrieval returns — not only the document row.

    `kb_ingest.reembed_document` is the tempting call here: it is right there, it is named for
    re-embedding, and its signature fits. It runs `UPDATE kb_chunks SET embedding = …` and
    nothing else — its own docstring says it *"reuses existing chunk content, only replaces
    vectors"*. It never reads `content_text` and never re-chunks. Route the edit through it and
    the OLD wording stays retrievable forever while the document row, the response, the chunk
    count and the activity log all look exactly like success. The correct call is
    `kb_ingest.ingest_document`.
    """
    a, _ = two_tenants
    expected = kb_ingest.chunk_text(EDITED_TEXT)
    assert len(expected) > 1, "fixture problem: EDITED_TEXT must be long enough to re-chunk"

    before = chunks_of(a["doc"])
    assert len(before) == 1 and A_MARK in before[0]["content"]

    r = call(api, a, "PUT", f"/kb/documents/{a['doc']}", json={"text": EDITED_TEXT})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": True, "reembedded_chunks": len(expected)}

    after = chunks_of(a["doc"])
    joined = " ".join(c["content"] for c in after)
    assert A_MARK not in joined, (
        "the old text is still retrievable after an edit — this is the reembed_document bug: "
        "vectors were replaced but the chunk content was never re-derived")
    assert EDIT_MARK in joined, "the edited content never reached kb_chunks"
    assert len(after) == len(expected), (
        f"chunk count did not re-derive from the new text ({len(after)} != {len(expected)}); "
        "the edit must call kb_ingest.ingest_document, which re-chunks AND re-embeds")
    assert [c["chunk_index"] for c in after] == list(range(len(after)))

    doc = document(a["doc"])
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == len(expected)
    assert EDIT_MARK in (doc["content_text"] or "")


def test_editing_only_metadata_leaves_the_chunks_alone(api, two_tenants):
    """The other half of the contract: no `text` means no re-embed, and `reembedded_chunks` is
    null rather than 0 — the console shows "metadata updated", not "0 chunks re-embedded"."""
    a, _ = two_tenants
    before = chunks_of(a["doc"])
    r = call(api, a, "PUT", f"/kb/documents/{a['doc']}",
             json={"title": "Refund policy v2", "tags": ["reviewed"], "doc_type": "faq"})
    assert r.status_code == 200
    assert r.json() == {"updated": True, "reembedded_chunks": None}
    assert chunks_of(a["doc"]) == before
    doc = document(a["doc"])
    assert doc["title"] == "Refund policy v2" and doc["tags"] == ["reviewed"]


def test_a_failed_reembed_surfaces_as_an_error_not_a_false_success(api, two_tenants, monkeypatch):
    """`ingest_document` swallows every exception into `status='error'` and returns None, so an
    await that returns normally says nothing about whether anything was written. The outcome is
    forced here through the real path — a detonating encoder — rather than by writing
    `status='error'` directly, so the re-read in `update_document` is what is actually tested.

    Reporting this as `{"updated": true}` would tell a tenant their correction is live while the
    document has silently stopped being retrievable at all.
    """
    a, _ = two_tenants

    async def _boom(texts, *, purpose: str = "ingest"):
        raise RuntimeError("TEI is down")

    monkeypatch.setattr(embeddings, "embed_texts", _boom)

    r = call(api, a, "PUT", f"/kb/documents/{a['doc']}", json={"text": EDITED_TEXT})
    assert r.status_code == 502, f"a failed re-embed must not be a 2xx (got {r.status_code})"
    assert "Re-embedding failed" in r.json()["detail"]

    assert document(a["doc"])["status"] == "error"
    edits = [e for e in events_of(a["client_id"]) if e["action"] == "edit"]
    assert edits and all(e["status"] == "error" for e in edits), (
        "the failure must be in the audit trail as an error, not logged as a successful edit")


# --------------------------------------------------------------------------- #
# 4. The full-KB re-embed is queued, not run in the request
# --------------------------------------------------------------------------- #
def test_full_kb_reembed_is_queued_and_never_runs_in_the_request(api, two_tenants, monkeypatch):
    """A full re-embed saturates the single shared CPU-bound TEI container that also serves live
    retrieval for every other tenant, and would blow past nginx's 300 s read timeout while
    holding a pool connection. So the request only writes a queue row.

    The encoder is a detonator for the duration: if this route ever runs the work inline, the
    test fails with a message saying exactly that instead of merely being slow.
    """
    a, b = two_tenants

    async def _detonate(texts, *, purpose: str = "ingest"):
        raise AssertionError(
            "POST /kb/reembed ran the encoder inside the request — it must only enqueue a job")

    monkeypatch.setattr(embeddings, "embed_texts", _detonate)

    r = call(api, a, "POST", "/kb/reembed")
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["state"] == "queued"
    assert job["client_id"] == a["client_id"]
    assert job["total_documents"] == 1, "the progress bar needs its scale at enqueue time"
    assert job["done_documents"] == 0 and job["started_at"] is None
    assert job["requested_by"] == "tenant:apikey"

    rows = jobs_of(a["client_id"])
    assert len(rows) == 1 and rows[0]["state"] == "queued"

    # Double-click: the guard is a partial unique index in the database, not a disabled button.
    again = call(api, a, "POST", "/kb/reembed")
    assert again.status_code == 409
    assert len(jobs_of(a["client_id"])) == 1, "a retry loop must not pile up full-KB re-embeds"

    # ...and it is per tenant. A global lock would make one tenant's queued job block everyone.
    assert call(api, b, "POST", "/kb/reembed").status_code == 202
    assert len(jobs_of(b["client_id"])) == 1


def test_reembed_status_reports_worker_progress(api, two_tenants):
    """What the console polls. The worker owns these columns; this asserts the read path
    reflects them, and that a tenant only ever sees its own job."""
    a, b = two_tenants
    assert call(api, a, "GET", "/kb/reembed/status").json() == {"job": None}

    queued = call(api, a, "POST", "/kb/reembed").json()["job"]
    call(api, b, "POST", "/kb/reembed")

    sql(lambda c: c.execute(
        """UPDATE kb_reembed_jobs SET state='running', started_at=now(), heartbeat_at=now(),
               total_documents=4, done_documents=3, failed_documents=1
           WHERE client_id = $1""", uuid.UUID(a["client_id"])))

    job = call(api, a, "GET", "/kb/reembed/status").json()["job"]
    assert job["id"] == queued["id"] and job["client_id"] == a["client_id"]
    assert (job["state"], job["done_documents"], job["failed_documents"],
            job["total_documents"]) == ("running", 3, 1, 4)
    assert job["heartbeat_at"] is not None, "a long but healthy run must not look abandoned"

    assert call(api, b, "GET", "/kb/reembed/status").json()["job"]["state"] == "queued"


# --------------------------------------------------------------------------- #
# 5. Chunk-level edit / delete
# --------------------------------------------------------------------------- #
def test_chunk_delete_updates_the_documents_chunk_count(api, two_tenants):
    a, _ = two_tenants
    doc = add_document(a["client_id"], EDITED_TEXT, title="Long policy")
    before = chunks_of(doc)
    assert len(before) > 1, "fixture problem: need a multi-chunk document"

    r = call(api, a, "DELETE", f"/kb/chunks/{before[0]['id']}")
    assert r.status_code == 200 and r.json() == {"deleted": True}

    after = chunks_of(doc)
    assert len(after) == len(before) - 1
    assert document(doc)["chunk_count"] == len(after), (
        "the document's chunk_count must be re-derived, or the console reports a count the KB "
        "no longer has")
    assert any(e["action"] == "chunk_delete" for e in events_of(a["client_id"]))


def test_chunk_edit_replaces_the_content_and_its_vector(api, two_tenants):
    a, _ = two_tenants
    before = chunks_of(a["doc"])[0]

    r = call(api, a, "PUT", f"/kb/chunks/{before['id']}",
             json={"content": f"{EDIT_MARK} corrected wording."})
    assert r.status_code == 200 and r.json() == {"updated": True}

    after = chunks_of(a["doc"])[0]
    assert EDIT_MARK in after["content"] and A_MARK not in after["content"]
    assert after["embedding"] != before["embedding"], (
        "the chunk's vector still encodes the OLD text — retrieval would keep matching wording "
        "that is no longer there")
    assert document(a["doc"])["chunk_count"] == 1


def test_chunk_edit_rejects_empty_content(api, two_tenants):
    a, _ = two_tenants
    chunk = chunks_of(a["doc"])[0]
    r = call(api, a, "PUT", f"/kb/chunks/{chunk['id']}", json={"content": "   "})
    assert r.status_code == 400
    assert chunks_of(a["doc"])[0]["content"] == chunk["content"]


# --------------------------------------------------------------------------- #
# 6. Bulk actions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("action", ["delete", "retag", "publish", "unpublish"])
def test_bulk_with_a_foreign_document_id_mutates_nothing(api, two_tenants, action):
    """Every bulk statement is a single `WHERE client_id = $1 AND id = ANY(...)`, so a foreign id
    matches no row: the reported `affected` is 0 and nothing anywhere changed.

    Note what this is NOT: the request is not rejected. A batch naming another tenant's document
    is answered 200/affected-0 rather than 400. That is a deliberate consequence of scoping in
    SQL rather than pre-validating ids, and the security property (no read, no write, no
    existence disclosure) holds either way — but it means a caller cannot tell "that id is not
    mine" from "that id is gone".
    """
    a, b = two_tenants
    before_doc, before_chunks = document(b["doc"]), chunks_of(b["doc"])
    before_events = len(events_of(b["client_id"]))

    r = call(api, a, "POST", "/kb/bulk",
             json={"action": action, "document_ids": [b["doc"]], "tags": ["hijacked"]})

    assert r.status_code == 200
    assert r.json() == {"action": action, "affected": 0}
    assert document(b["doc"]) == before_doc, f"bulk {action} MUTATED another tenant's document"
    assert chunks_of(b["doc"]) == before_chunks
    assert len(events_of(b["client_id"])) == before_events, (
        "the operation was logged against the wrong tenant's audit trail")


def test_bulk_reembed_of_a_foreign_document_touches_nothing(api, two_tenants):
    """`reembed` is the one bulk action that loops rather than issuing a single scoped UPDATE,
    so `affected` has to be derived from what the loop actually did.

    `kb_ingest.reembed_document` is itself client-scoped: for a foreign id it finds no chunks
    and returns 0. Counting the iteration instead would answer `affected: 1` for work that
    never happened and file a successful `bulk` row in the CALLER's audit log — not a leak, but
    a lie, and the kind that sends an operator the wrong way during an incident.
    """
    a, b = two_tenants
    before = chunks_of(b["doc"])

    r = call(api, a, "POST", "/kb/bulk",
             json={"action": "reembed", "document_ids": [b["doc"]]})

    assert r.status_code == 200
    assert r.json() == {"action": "reembed", "affected": 0}, (
        "a foreign document id was reported back as re-embedded")
    assert chunks_of(b["doc"]) == before, "another tenant's vectors were rewritten"


def test_a_failed_bulk_reembed_is_a_502_with_an_audit_row_not_a_bare_500(
        api, two_tenants, monkeypatch):
    """The encoder raises `EmbeddingError`, which is NOT an `IngestError` — so a handler that
    catches only the latter is dead code for the dominant failure (encoder down, 5xx, timeout)
    and the tenant gets a bodyless 500 with nothing in the activity log to explain it."""
    a, _ = two_tenants

    async def _boom(texts, *, purpose: str = "ingest"):
        raise EmbeddingError("Embeddings service unreachable")

    monkeypatch.setattr(embeddings, "embed_texts", _boom)
    before_events = len(events_of(a["client_id"]))

    r = call(api, a, "POST", "/kb/bulk",
             json={"action": "reembed", "document_ids": [a["doc"]]})

    assert r.status_code == 502, f"an unreachable encoder must not be a bare 500 (got {r.status_code})"
    assert "Re-embedding failed" in r.json()["detail"]
    events = events_of(a["client_id"])          # oldest first, so the new row is last
    assert len(events) > before_events, "a failed bulk re-embed left no trace in the activity log"
    assert events[-1]["action"] == "bulk" and events[-1]["status"] == "error", (
        "the failure was logged as a successful bulk operation")


def test_a_failed_per_document_reembed_is_a_502_with_an_audit_row(api, two_tenants, monkeypatch):
    a, _ = two_tenants

    async def _boom(texts, *, purpose: str = "ingest"):
        raise EmbeddingError("Embeddings service unreachable")

    monkeypatch.setattr(embeddings, "embed_texts", _boom)

    r = call(api, a, "POST", f"/kb/documents/{a['doc']}/reembed")

    assert r.status_code == 502
    assert "Re-embedding failed" in r.json()["detail"]
    reembeds = [e for e in events_of(a["client_id"]) if e["action"] == "reembed"]
    assert reembeds and reembeds[-1]["status"] == "error"


def test_bulk_mixed_batch_applies_only_to_the_callers_own_documents(api, two_tenants):
    a, b = two_tenants
    r = call(api, a, "POST", "/kb/bulk",
             json={"action": "delete", "document_ids": [a["doc"], b["doc"]]})
    assert r.status_code == 200
    assert r.json()["affected"] == 1, "only the caller's own document may be counted"
    assert document(a["doc"]) is None
    assert document(b["doc"]) is not None, "a mixed batch deleted another tenant's document"


def test_bulk_rejects_an_unknown_action_and_an_empty_selection(api, two_tenants):
    a, _ = two_tenants
    assert call(api, a, "POST", "/kb/bulk",
                json={"action": "drop_table", "document_ids": [a["doc"]]}).status_code == 400
    assert call(api, a, "POST", "/kb/bulk",
                json={"action": "delete", "document_ids": []}).status_code == 400
    assert document(a["doc"]) is not None


def test_bulk_visibility_ignores_another_tenants_documents(api, two_tenants):
    a, b = two_tenants
    r = call(api, a, "POST", "/kb/documents/visibility",
             json={"document_ids": [b["doc"]], "visibility": "public"})
    assert r.status_code == 200
    assert r.json() == {"visibility": "public", "affected": 0}
    assert document(b["doc"])["visibility"] == "internal", (
        "PUBLISHING another tenant's document would make it quotable by a public bot")


def test_visibility_rejects_anything_but_internal_or_public(api, two_tenants):
    """Two values, deliberately: anything richer invites "sort of public" and the guarantee that
    an unpublished document is unquotable stops being auditable."""
    a, _ = two_tenants
    r = call(api, a, "PATCH", f"/kb/documents/{a['doc']}/visibility",
             json={"visibility": "semi-public"})
    assert r.status_code == 400
    assert document(a["doc"])["visibility"] == "internal"


# --------------------------------------------------------------------------- #
# 7. Publishing is what the public autopilot may quote
# --------------------------------------------------------------------------- #
def test_publishing_makes_a_document_retrievable_by_the_public_bot(api, two_tenants):
    """`visibility` is the only thing between an internal pricing floor and a WhatsApp reply:
    the public autopilot retrieves with `visibility='public'`, so this is asserted against
    `retrieval.retrieve_ranked(..., visibility="public")` — the actual read path — rather than
    against the column.
    """
    a, b = two_tenants
    # B publishes too, so the assertions below are about scoping and not about B having nothing.
    assert call(api, b, "PATCH", f"/kb/documents/{b['doc']}/visibility",
                json={"visibility": "public"}).status_code == 200

    def public_hits(tenant, query):
        return run_async(lambda: retrieval.retrieve_ranked(
            tenant["client_id"], query, visibility="public"))

    before = public_hits(a, A_TEXT)
    assert before["hits"] == [] and before["kb_present"] is False, (
        "an unpublished KB must read as 'nothing published', not as 'nothing matched'")

    r = call(api, a, "PATCH", f"/kb/documents/{a['doc']}/visibility",
             json={"visibility": "public"})
    assert r.status_code == 200 and r.json() == {"id": a["doc"], "visibility": "public"}

    after = public_hits(a, A_TEXT)
    assert [h["content"] for h in after["hits"]] == [A_TEXT]
    assert after["method"] == "vector"

    # The worst case: A asks, verbatim, the question only B's published document answers.
    cross = public_hits(a, B_TEXT)
    assert B_MARK not in " ".join(h["content"] for h in cross["hits"]), (
        "LEAK: the public retrieval path returned another tenant's published document")

    back = call(api, a, "PATCH", f"/kb/documents/{a['doc']}/visibility",
                json={"visibility": "internal"})
    assert back.status_code == 200
    assert public_hits(a, A_TEXT)["hits"] == [], "unpublishing must remove it from the public bot"

    actions = [e["action"] for e in events_of(a["client_id"])]
    assert "publish" in actions and "unpublish" in actions, (
        "publish/unpublish are logged under their own action, not the generic 'bulk', so the "
        "timeline can be filtered to exactly 'what became customer-quotable, and when'")


# --------------------------------------------------------------------------- #
# 8. The playground is the tenant's debugging read
# --------------------------------------------------------------------------- #
def test_playground_returns_scored_hits_from_the_callers_kb_only(api, two_tenants):
    a, b = two_tenants
    r = call(api, a, "POST", "/kb/playground", json={"query": A_TEXT, "top_k": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "vector"
    assert [x["content"] for x in body["results"]] == [A_TEXT]
    assert {"chunk_id", "document_id", "chunk_index", "score", "title", "doc_type"} <= set(
        body["results"][0])
    assert body["results"][0]["score"] == pytest.approx(1.0, abs=1e-3)

    # Debugging reads must not fill the audit log, or nobody reads the audit log.
    assert not events_of(a["client_id"]), "the playground must not be logged to kb_events"
    assert not events_of(b["client_id"])
