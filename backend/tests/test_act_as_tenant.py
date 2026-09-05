"""`X-Act-As-Tenant`: the operator console drives the tenant's own routes.

The console and the portal are one page. Rather than a parallel set of `/admin/...` twins
that drift — the failure this header was introduced to end — a verified superadmin names a
workspace and the ORDINARY tenant routes serve them, scoped to it.

That is a real grant, so these tests pin its edges. The one that must never regress is
`test_a_tenant_credential_cannot_scope_itself_to_another_workspace`: if the header were
honoured for anyone but a superadmin, it would be a one-header cross-tenant read of every
customer's knowledge base.
"""
import uuid

import pytest

from app.config import settings
from app.services.auth import Principal

from conftest import sql


@pytest.fixture
def workspaces(api):
    """Two isolated workspaces. Everything cascades from `clients`, so one DELETE cleans up."""
    created: list[uuid.UUID] = []

    def _make(label: str) -> dict:
        suffix = uuid.uuid4().hex[:8]
        api_key = f"acts-key-{label}-{suffix}"
        cid = sql(lambda c: c.fetchval(
            "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
            f"acts-{label}-{suffix}", f"act-as-{label}", api_key))
        created.append(cid)
        return {"client_id": str(cid), "api_key": api_key}

    yield _make("a"), _make("b")
    for cid in created:
        sql(lambda c, cid=cid: c.execute("DELETE FROM clients WHERE id = $1", cid))


@pytest.fixture
def admin_headers():
    return {"X-Admin-Token": settings.admin_token}


# --------------------------------------------------------------------------- #
# The predicate the routers gate on
# --------------------------------------------------------------------------- #
def test_operator_principal_is_tenant_shaped_but_still_identifiable():
    op = Principal(kind="tenant", client_id=str(uuid.uuid4()), role="superadmin", via="admin")
    assert op.is_tenant, "the tenant routes gate on this; the whole design needs it true"
    assert op.is_operator, "and the audit trail needs to be able to tell them apart"
    assert not op.is_superadmin, "scoped to a workspace, so /admin routes must refuse it"
    assert op.user_id is None, "an operator is nobody's user account"


@pytest.mark.parametrize("role,allowed", [
    ("owner", True),
    ("apikey", True),
    ("superadmin", True),      # an operator acting on the workspace
    ("member", False),
    ("user", False),
    (None, False),
])
def test_may_configure_workspace(role, allowed):
    assert Principal(kind="tenant", client_id="c", role=role).may_configure_workspace is allowed


def test_a_plain_tenant_owner_is_not_an_operator():
    owner = Principal(kind="tenant", client_id="c", user_id="u", role="owner", via="token")
    assert owner.may_configure_workspace and not owner.is_operator


# --------------------------------------------------------------------------- #
# The header, end to end
# --------------------------------------------------------------------------- #
def _stats(api, headers):
    return api.get("/kb/stats", headers=headers)


def _doc_count(api, headers) -> int:
    r = _stats(api, headers)
    assert r.status_code == 200, r.text
    return r.json()["documents"]


def test_superadmin_scopes_to_the_named_workspace(api, workspaces, admin_headers):
    """Only workspace A gets a document, so the counts tell the two apart."""
    a, b = workspaces
    seed = api.post("/kb/documents/text",
                    headers={**admin_headers, "X-Act-As-Tenant": a["client_id"]},
                    json={"title": "A only", "text": "belongs to workspace A"})
    assert seed.status_code in (200, 201, 202), seed.text

    assert _doc_count(api, {**admin_headers, "X-Act-As-Tenant": a["client_id"]}) == 1
    assert _doc_count(api, {**admin_headers, "X-Act-As-Tenant": b["client_id"]}) == 0


def test_a_tenant_credential_cannot_scope_itself_to_another_workspace(api, workspaces,
                                                                     admin_headers):
    """The header must be inert for anyone who is not a superadmin.

    Not 403 — simply ignored: A asking to act as B gets A's OWN knowledge base back, so the
    header cannot even be used to probe whether another workspace exists.
    """
    a, b = workspaces
    # Give B the document, so "A sees 0" can only mean the header was ignored.
    seed = api.post("/kb/documents/text",
                    headers={**admin_headers, "X-Act-As-Tenant": b["client_id"]},
                    json={"title": "B only", "text": "belongs to workspace B"})
    assert seed.status_code in (200, 201, 202), seed.text

    leaked = _doc_count(api, {"X-API-Key": a["api_key"], "X-Act-As-Tenant": b["client_id"]})
    assert leaked == 0, "a tenant key was scoped to another workspace by a request header"


def test_the_header_alone_is_not_a_credential(api, workspaces):
    a, _ = workspaces
    assert _stats(api, {"X-Act-As-Tenant": a["client_id"]}).status_code in (401, 403)


def test_an_unknown_workspace_is_a_404_not_an_unscoped_superadmin(api, admin_headers):
    """The dangerous failure mode would be falling back to the unscoped operator principal:
    the request would then run against whatever the route makes of a superadmin."""
    r = _stats(api, {**admin_headers, "X-Act-As-Tenant": str(uuid.uuid4())})
    assert r.status_code == 404


def test_admin_routes_refuse_a_scoped_operator(api, workspaces, admin_headers):
    """Scoping trades the superadmin principal for a tenant one, so the `/admin/...`
    surface — including the workspace list the picker reads — must stop accepting it. This
    is why the page sends the header on tenant calls only."""
    a, _ = workspaces
    r = api.get("/admin/tenants", headers={**admin_headers, "X-Act-As-Tenant": a["client_id"]})
    assert r.status_code == 401
    assert api.get("/admin/tenants", headers=admin_headers).status_code == 200


def test_operator_may_edit_the_rubric_but_not_reset_it(api, workspaces, admin_headers):
    """Editing is the operator's job. Reset re-checks the CALLER's own password, and an
    operator has no tenant-user password — so it stays the account holder's alone."""
    a, _ = workspaces
    h = {**admin_headers, "X-Act-As-Tenant": a["client_id"]}
    put = api.put("/scoring/config", headers=h, json={
        "dimensions": [{"name": "Greeting", "weight": 100, "guidance": "g"}], "rubric": "r"})
    assert put.status_code == 200, put.text
    assert api.post("/scoring/reset", headers=h, json={"password": "x"}).status_code in (401, 403)


def test_operator_edits_are_audited_as_the_operator(api, workspaces, admin_headers):
    """An operator's change must never be recorded against one of the customer's people."""
    a, _ = workspaces
    h = {**admin_headers, "X-Act-As-Tenant": a["client_id"]}
    made = api.post("/kb/documents/text", headers=h,
                    json={"title": "Operator note", "text": "written by the operator"})
    assert made.status_code in (200, 201, 202), made.text
    # Deleting is audited (importing, on this route, is not) — so delete to get a row.
    assert api.delete(f"/kb/documents/{made.json()['id']}", headers=h).status_code == 200
    events = api.get("/kb/activity?limit=20", headers=h).json()
    rows = events if isinstance(events, list) else events.get("events", [])
    actors = {e.get("actor") for e in rows}
    assert any(a_ and "superadmin" in a_ for a_ in actors), actors


# --------------------------------------------------------------------------- #
# A purpose-scoped token is not a session
# --------------------------------------------------------------------------- #
def test_a_chat_stream_ticket_is_not_a_tenant_session(api, workspaces):
    """Found while reviewing the operator grant; the hole predated it.

    `make_token` signs every token with one secret, and a chat stream ticket carries
    `{"scope": "chat_stream", "client_id": ...}`. It used to fall through the Bearer branch
    and become a full tenant principal — and these tickets are deliberately placed in a URL
    query string (EventSource cannot set headers), so they reach nginx access logs and the
    browser history of every visitor to a customer's public chat widget. A verified run
    returned the workspace's entire knowledge base from `GET /kb/documents`.
    """
    from app.services.auth import make_token

    a, _ = workspaces
    ticket = make_token({"scope": "chat_stream", "client_id": a["client_id"],
                         "suggest_ref": "s", "jti": "j"}, ttl_hours=1)
    r = api.get("/kb/documents?limit=2", headers={"Authorization": f"Bearer {ticket}"})
    assert r.status_code == 401, "a stream ticket was accepted as a tenant session"


def test_any_scoped_token_is_refused_not_just_this_one(api, workspaces):
    """The guard keys on the presence of `scope`, so a future purpose-built token inherits
    the refusal instead of having to remember it."""
    from app.services.auth import make_token

    a, _ = workspaces
    tok = make_token({"scope": "something_new", "client_id": a["client_id"]}, ttl_hours=1)
    assert api.get("/kb/stats", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


# --------------------------------------------------------------------------- #
# Attribution: an operator is never recorded as the customer
# --------------------------------------------------------------------------- #
def test_operator_rubric_edits_are_attributed_to_the_operator(api, workspaces, admin_headers):
    a, _ = workspaces
    h = {**admin_headers, "X-Act-As-Tenant": a["client_id"]}
    put = api.put("/scoring/config", headers=h, json={
        "dimensions": [{"name": "Greeting", "weight": 100, "guidance": "g"}], "rubric": "r"})
    assert put.status_code == 200, put.text
    cfg = api.get("/scoring/config", headers=h).json()
    assert cfg.get("updated_by") == "superadmin", cfg.get("updated_by")


def test_operator_work_does_not_spend_the_customers_quota(monkeypatch, workspaces):
    """Support work must not bill the customer — and a customer who has spent their day's
    allowance must not be able to lock support out of the account that needs looking at."""
    import asyncio

    from app.services import limits
    from app.services.auth import Principal

    a, _ = workspaces
    spent = []

    async def _boom(*args, **kwargs):
        spent.append(args)
        raise AssertionError("the customer's counter was charged for operator work")

    async def _limit(*args, **kwargs):
        return 100      # stubbed: the DB lives on the app's loop, not this test's

    monkeypatch.setattr(limits, "reserve_counter", _boom)
    monkeypatch.setattr(limits, "_tenant_limit", _limit)
    operator = Principal(kind="tenant", client_id=a["client_id"], role="superadmin",
                         via="admin")
    asyncio.run(limits.reserve(operator, "analyses"))
    assert not spent

    # ...while a genuine tenant IS still metered, so the exemption is the operator's alone.
    customer = Principal(kind="tenant", client_id=a["client_id"], user_id="u", role="owner",
                         via="token")
    with pytest.raises(AssertionError, match="charged for operator work"):
        asyncio.run(limits.reserve(customer, "analyses"))
