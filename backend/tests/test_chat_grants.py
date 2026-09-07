"""One credential, many tenants — the grant routes, asserted at the HTTP boundary.

Until 2026-09-08 `POST /admin/integrations` refused more than one grant (the P1 pilot's blast
radius). The owner's chat backend is one service acting for many CQ tenants, so the model is now
one key plus a grant row per tenant, added and revoked individually. What these tests pin is the
part that must not drift while that widening beds in:

  * the grant row is the WHOLE authorization: revoking it makes the same request 401 on the
    very next call (no cache), and leaves nothing behind in that tenant's tables;
  * re-granting is a clean round trip — the revoked row is re-activated, not duplicated;
  * an unknown selector is a 4xx from the operator's route, never a half-written grant.

Runs with no Anthropic key, like the rest of the chat suite: the turn posted here carries
`precompute: false`, so nothing is generated and `no_llm` stays quiet.
"""
import uuid

import pytest

from app.config import settings
from conftest import chat_route_present, chat_row_counts, sql

TURNS = "/v1/chat/turns"


@pytest.fixture(scope="module")
def admin(api):
    hdr = {"X-Admin-Token": settings.admin_token}
    if api.get("/admin/integrations", headers=hdr).status_code == 401:
        pytest.skip("ADMIN_TOKEN not configured in this environment")
    return hdr


@pytest.fixture(scope="module")
def shared(api, admin, seed):
    """A credential issued for BOTH seed tenants through the real operator route.

    Module-scoped so the tests below can walk one credential through grant → revoke → re-grant in
    order. Torn down through the same DELETE an operator would use (deactivate, the audit rows
    stay), then hard-deleted so the shared local database does not accrete one dead integration
    per test run.
    """
    r = api.post("/admin/integrations", headers=admin,
                 json={"name": f"grants-{seed['suffix']}", "scopes": ["chat:turn"],
                       "grants": [seed["a"]["slug"], seed["b"]["slug"]]})
    assert r.status_code == 201, f"two-grant issuance still refused: {r.status_code} {r.text}"
    body = r.json()
    try:
        yield body
    finally:
        api.delete(f"/admin/integrations/{body['integration_id']}", headers=admin)
        sql(lambda c: c.execute("DELETE FROM integrations WHERE id = $1",
                                uuid.UUID(body["integration_id"])))


def _headers(shared, tenant_sel: str) -> dict:
    return {"X-CQ-Key": shared["api_key"], "X-CQ-Tenant": tenant_sel,
            "X-CQ-Expect-Tenant": tenant_sel}


def _turn_body() -> dict:
    # test_chat_isolation._turn_body's envelope, minus the precompute so no generation is fired.
    return {"conversation_ref": f"c-{uuid.uuid4().hex[:12]}",
            "turn_ref": f"t-{uuid.uuid4().hex[:12]}", "role": "customer",
            "content": "What is the refund window?", "channel": "web", "locale": "en",
            "precompute": False}


def _post_turn(api, headers):
    r = api.post(TURNS, headers=headers, json=_turn_body())
    if r.status_code == 422:
        pytest.fail(f"POST {TURNS} rejected the Turn envelope: {r.text}")
    return r


def _grant_rows(shared, client_id: str) -> list[dict]:
    r = sql(lambda c: c.fetch(
        "SELECT is_active FROM integration_grants WHERE integration_id = $1 AND client_id = $2",
        uuid.UUID(shared["integration_id"]), uuid.UUID(client_id)))
    return [dict(x) for x in r]


# --------------------------------------------------------------------------- #
# 1. Issuance for two tenants at once
# --------------------------------------------------------------------------- #
def test_multi_grant_issuance_lists_both_tenants(api, admin, seed, shared):
    r = api.get("/admin/integrations", headers=admin)
    assert r.status_code == 200, r.text
    mine = next(i for i in r.json()["integrations"] if i["id"] == shared["integration_id"])
    granted = {g["slug"] for g in mine["grants"] if g["is_active"]}
    assert granted == {seed["a"]["slug"], seed["b"]["slug"]}, granted

    # Both tenants resolve with the ONE key — the whole point of the change.
    for label in ("a", "b"):
        r = api.get("/auth/me", headers=_headers(shared, seed[label]["slug"]))
        assert r.status_code == 200, f"{label}: {r.status_code} {r.text}"
        assert r.json().get("client_id") == seed[label]["client_id"], r.text


# --------------------------------------------------------------------------- #
# 2. Revoke one tenant: 401 next request, nothing written, the other tenant untouched
# --------------------------------------------------------------------------- #
def test_revoking_one_grant_closes_only_that_tenant(api, admin, seed, shared):
    if not chat_route_present(api, TURNS):
        pytest.skip(f"{TURNS} is not mounted")
    b = seed["b"]

    # Control: B works before the revoke.
    r = _post_turn(api, _headers(shared, b["client_id"]))
    assert r.status_code == 202, r.text
    assert r.json()["client_id"] == b["client_id"]

    before_b = chat_row_counts(b["client_id"])
    r = api.delete(f"/admin/integrations/{shared['integration_id']}/grants/{b['client_id']}",
                   headers=admin)
    assert r.status_code == 200, r.text
    assert r.json() == {"integration_id": shared["integration_id"],
                        "client_id": b["client_id"], "is_active": False}

    # The very next request, in both selector vocabularies, is a 401 indistinguishable from a
    # bad key — and B's tables gain nothing.
    for sel in (b["client_id"], b["slug"]):
        r = _post_turn(api, _headers(shared, sel))
        assert r.status_code == 401, f"{sel}: {r.status_code} {r.text}"
        assert b["client_id"] not in r.text
    assert chat_row_counts(b["client_id"]) == before_b, "rows written for a revoked grant"

    # Revoked, not deleted: the row is the audit trail.
    assert _grant_rows(shared, b["client_id"]) == [{"is_active": False}]

    # A keeps working on the same key — revoking per tenant is why this is not a rotation.
    r = api.get("/auth/me", headers=_headers(shared, seed["a"]["slug"]))
    assert r.status_code == 200, r.text

    # A second DELETE of the same grant is a no-op that still succeeds (idempotent), and a
    # never-granted pair is a 404.
    r = api.delete(f"/admin/integrations/{shared['integration_id']}/grants/{b['client_id']}",
                   headers=admin)
    assert r.status_code == 200, r.text
    r = api.delete(f"/admin/integrations/{shared['integration_id']}/grants/{uuid.uuid4()}",
                   headers=admin)
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------- #
# 3. Re-grant: the same row comes back to life, and the request works again
# --------------------------------------------------------------------------- #
def test_regranting_reactivates_the_same_row(api, admin, seed, shared):
    if not chat_route_present(api, TURNS):
        pytest.skip(f"{TURNS} is not mounted")
    b = seed["b"]

    r = api.post(f"/admin/integrations/{shared['integration_id']}/grants", headers=admin,
                 json={"tenant": b["slug"]})
    assert r.status_code == 201, r.text
    grant = r.json()["grant"]
    assert grant["client_id"] == b["client_id"]
    assert grant["slug"] == b["slug"]
    assert grant["is_active"] is True
    # No scopes asked for => the integration's own.
    assert grant["scopes"] == ["chat:turn"]

    # ON CONFLICT re-activated the existing row rather than adding a second one.
    assert _grant_rows(shared, b["client_id"]) == [{"is_active": True}]

    r = _post_turn(api, _headers(shared, b["client_id"]))
    assert r.status_code == 202, r.text
    assert r.json()["client_id"] == b["client_id"]


# --------------------------------------------------------------------------- #
# 4. A grant can only narrow the integration's scopes
# --------------------------------------------------------------------------- #
def test_grant_scopes_only_narrow(api, admin, seed, shared):
    url = f"/admin/integrations/{shared['integration_id']}/grants"
    # The integration holds chat:turn only; asking for chat:sync too must not widen it.
    r = api.post(url, headers=admin, json={"tenant": seed["a"]["slug"],
                                          "scopes": ["chat:turn", "chat:sync"]})
    assert r.status_code == 201, r.text
    assert r.json()["grant"]["scopes"] == ["chat:turn"]

    # Entirely outside what the key holds: refused, not stored as an empty (= "all") grant.
    r = api.post(url, headers=admin, json={"tenant": seed["a"]["slug"], "scopes": ["chat:sync"]})
    assert r.status_code == 400, r.text
    # Never-issuable scopes are refused here exactly as at issuance.
    r = api.post(url, headers=admin, json={"tenant": seed["a"]["slug"], "scopes": ["kb:write"]})
    assert r.status_code == 400, r.text
    assert "kb:write" in r.text


# --------------------------------------------------------------------------- #
# 5. Unknown selectors
# --------------------------------------------------------------------------- #
def test_unknown_tenant_or_integration_is_4xx(api, admin, seed, shared):
    url = f"/admin/integrations/{shared['integration_id']}/grants"
    grants_before = sql(lambda c: c.fetchval(
        "SELECT count(*) FROM integration_grants WHERE integration_id = $1",
        uuid.UUID(shared["integration_id"])))

    for sel in (f"no-such-tenant-{uuid.uuid4().hex[:8]}", str(uuid.uuid4()), "", "   "):
        r = api.post(url, headers=admin, json={"tenant": sel})
        assert 400 <= r.status_code < 500, f"{sel!r}: {r.status_code} {r.text}"

    # An integration that does not exist is a 404 on the path, and nothing is written for it.
    r = api.post(f"/admin/integrations/{uuid.uuid4()}/grants", headers=admin,
                 json={"tenant": seed["a"]["slug"]})
    assert r.status_code == 404, r.text

    assert sql(lambda c: c.fetchval(
        "SELECT count(*) FROM integration_grants WHERE integration_id = $1",
        uuid.UUID(shared["integration_id"]))) == grants_before

    # Superadmin only — the chat key itself must not be able to widen its own grants.
    r = api.post(url, headers=_headers(shared, seed["a"]["slug"]), json={"tenant": seed["b"]["slug"]})
    assert r.status_code in (400, 401, 403), r.text
