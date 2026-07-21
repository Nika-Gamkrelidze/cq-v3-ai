"""Tenant isolation for the chat surface — asserted at the HTTP boundary.

`test_tenant_isolation.py` already proves the property one layer down: call `retrieval` with
tenant A's id and B's content never comes back. That test would pass unchanged even if the
resolver handed the wrong `client_id` to it, because it never constructs a request. Every new
failure mode P1 introduces lives *above* retrieval, in credential resolution and routing:

  * a credential that is legitimately multi-tenant, narrowed by a caller-supplied header;
  * a resolution ORDER that used to let a second header silently pick a different tenant;
  * a bulk endpoint where one bad element must reject the whole batch;
  * a scope list whose only enforcement is a function nobody is forced to call.

So these tests drive the real ASGI app through `TestClient` and assert on status codes and on
what is in the database afterwards. They run with no Anthropic key: the fake embedder in
conftest makes every query unanswerable from tenant A's KB, so the deterministic gate refuses
before a model call — and `no_llm` detonates if anything reaches Claude anyway.
"""
import json
import time
import uuid

import pytest

from app.config import settings
from app.services.auth import Principal, assert_expected_tenant
from app.services import chat_credentials
from conftest import B_MARK, chat_route_present, chat_row_counts, require_chat_route, sql

TURNS = "/v1/chat/turns"
SYNC = "/v1/chat/conversations:sync"
SUGGESTIONS = "/v1/chat/suggestions/{suggest_ref}"


def _cq_headers(seed, tenant_sel: str, expect: str | None = None) -> dict:
    h = {"X-CQ-Key": seed["integration"]["api_key"], "X-CQ-Tenant": tenant_sel}
    if expect is not None:
        h["X-CQ-Expect-Tenant"] = expect
    return h


def _turn_body(conversation_ref: str, *, content: str = "What is the refund window?") -> dict:
    # The ADR's Turn envelope field names (docs/ADR-001-conversational-ai.md, "API contract").
    return {"conversation_ref": conversation_ref, "turn_ref": f"t-{uuid.uuid4().hex[:12]}",
            "role": "customer", "content": content, "channel": "web", "locale": "en"}


def _post_turn(api, headers: dict, body: dict):
    r = api.post(TURNS, headers=headers, json=body)
    if r.status_code == 422:
        # Not a tolerated outcome: it means this file and routers/chat.py disagree about the
        # documented request shape, which is itself the finding.
        pytest.fail(f"POST {TURNS} rejected the ADR Turn envelope as invalid: {r.text}")
    return r


# --------------------------------------------------------------------------- #
# 1. A credential naming a tenant it was never granted
# --------------------------------------------------------------------------- #
def test_ungranted_tenant_selector_is_401_and_writes_nothing(api, seed):
    """The integration is granted tenant A only. Naming B must be indistinguishable from a bad
    key — 401 — and must not leave a trace in B's tables.

    This is the single most important assertion in the file: `X-CQ-Tenant` is caller-supplied,
    and the whole design rests on it being able to *narrow* within the grant set and nothing
    else. If this ever returns 200, one compromised chat site owns every CQ tenant.
    """
    before_a = chat_row_counts(seed["a"]["client_id"])
    before_b = chat_row_counts(seed["b"]["client_id"])

    # Probe the resolver directly — /auth/me is mounted today and depends on resolve_principal,
    # so this case holds even before the chat router lands.
    for sel in (seed["b"]["client_id"], seed["b"]["slug"]):
        r = api.get("/auth/me", headers=_cq_headers(seed, sel))
        assert r.status_code == 401, f"selector {sel!r} resolved: {r.status_code} {r.text}"
        # The failure mode must not be leaked: unknown key, ungranted tenant and inactive tenant
        # all say the same thing.
        assert seed["b"]["client_id"] not in r.text

    # A bogus selector that is neither a uuid nor a slug must also be 401 — never a 400/500 from
    # a uuid cast, which would tell an attacker that uuid-shaped guesses are a different class.
    r = api.get("/auth/me", headers=_cq_headers(seed, "'; DROP TABLE clients; --"))
    assert r.status_code == 401

    if chat_route_present(api, TURNS):
        conv = f"c-{uuid.uuid4().hex[:12]}"
        r = _post_turn(api, _cq_headers(seed, seed["b"]["client_id"], seed["b"]["client_id"]),
                       _turn_body(conv))
        assert r.status_code == 401, r.text

    assert chat_row_counts(seed["a"]["client_id"]) == before_a
    assert chat_row_counts(seed["b"]["client_id"]) == before_b, "rows written for an ungranted tenant"


# --------------------------------------------------------------------------- #
# 2. The caller's own stated tenant must match the credential-derived one
# --------------------------------------------------------------------------- #
def test_expect_tenant_mismatch_is_403():
    """`X-CQ-Expect-Tenant` is the only defence against the chat site's *own* mapping bugs, so
    it is asserted at the unit level too — it must hold for every write route that calls it,
    not only for the ones mounted today.
    """
    p = Principal(kind="integration", client_id="11111111-1111-1111-1111-111111111111",
                  integration_id="i", tenant_sel="acme", scopes=["chat:turn"])

    # Both vocabularies the caller may legitimately speak.
    assert_expected_tenant(p, "11111111-1111-1111-1111-111111111111")
    assert_expected_tenant(p, "acme")

    # Someone else's tenant, in either vocabulary.
    for bad in ("22222222-2222-2222-2222-222222222222", "other-corp"):
        with pytest.raises(Exception) as exc:
            assert_expected_tenant(p, bad)
        assert getattr(exc.value, "status_code", None) == 403, bad

    # Absent is 400 and never "assume the credential is right": a write with no assertion is
    # exactly the mis-routed write this header exists to catch.
    for missing in (None, "", "   "):
        with pytest.raises(Exception) as exc:
            assert_expected_tenant(p, missing)
        assert getattr(exc.value, "status_code", None) == 400


def test_expect_tenant_mismatch_is_403_over_http(api, seed):
    require_chat_route(api, TURNS)
    before_a = chat_row_counts(seed["a"]["client_id"])
    before_b = chat_row_counts(seed["b"]["client_id"])
    # Authenticated correctly for A, but the caller believes it is writing for B.
    r = _post_turn(api,
                   _cq_headers(seed, seed["a"]["client_id"], seed["b"]["client_id"]),
                   _turn_body(f"c-{uuid.uuid4().hex[:12]}"))
    assert r.status_code == 403, r.text
    # Neither the credential's tenant nor the asserted one may be written on a mismatch.
    assert chat_row_counts(seed["a"]["client_id"]) == before_a
    assert chat_row_counts(seed["b"]["client_id"]) == before_b


# --------------------------------------------------------------------------- #
# 3. Ambiguity is an error, not a precedence rule
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("extra", [
    {"Authorization": "Bearer whatever"},
    {"X-API-Key": "some-tenant-key"},
    {"X-Admin-Token": "some-admin-token"},
])
def test_two_credential_headers_is_400(api, seed, extra):
    """Two credentials for two different tenants must never be resolved by header priority.
    Before P1 the highest-priority one silently won, so a stale default header could decide
    which tenant a write landed in.
    """
    headers = _cq_headers(seed, seed["a"]["client_id"]) | extra
    r = api.get("/auth/me", headers=headers)
    assert r.status_code == 400, r.text
    # The message names what was presented so the caller can fix it; it must not resolve one.
    assert "credential" in r.json().get("detail", "").lower()


def test_two_tenant_credentials_is_400(api, seed):
    """The pre-existing pair, too: a tenant Bearer plus a tenant API key for a *different*
    tenant used to resolve to the Bearer's tenant.
    """
    r = api.get("/auth/me", headers={"Authorization": "Bearer x.y",
                                     "X-API-Key": seed["a"]["api_key"]})
    assert r.status_code == 400, r.text


# --------------------------------------------------------------------------- #
# 4. No silent downgrade
# --------------------------------------------------------------------------- #
def test_invalid_bearer_never_downgrades(api, seed):
    """An invalid/expired Bearer is terminal. It must not fall through to X-API-Key (which
    would flip the request to a different tenant by wall clock) nor to anonymous (which would
    quietly consume the anonymous IP quota and return a 200 with no tenant data).
    """
    # Alone: hard 401, not an anonymous principal.
    r = api.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401, r.text

    # Paired with a *valid* API key: rejected as ambiguous, and in particular never served as
    # the API key's tenant.
    r = api.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token",
                                     "X-API-Key": seed["a"]["api_key"]})
    assert r.status_code in (400, 401), r.text
    assert seed["a"]["client_id"] not in r.text, "downgraded to the API key's tenant"

    # An invalid API key alone is also terminal, not a demotion to the anonymous bucket.
    r = api.get("/auth/me", headers={"X-API-Key": f"nope-{uuid.uuid4().hex}"})
    assert r.status_code == 401, r.text

    # And an invalid integration key is 401, not anonymous.
    r = api.get("/auth/me", headers={"X-CQ-Key": "cqi_deadbeef.notasecret",
                                     "X-CQ-Tenant": seed["a"]["client_id"]})
    assert r.status_code == 401, r.text

    # Control: no credentials at all is still a perfectly good anonymous principal. The rules
    # above must not have made the public surface unreachable.
    r = api.get("/auth/me")
    assert r.status_code == 200, r.text
    assert r.json().get("kind") == "anonymous", r.text


# --------------------------------------------------------------------------- #
# 5. An external_ref is only unique WITHIN a tenant
# --------------------------------------------------------------------------- #
def test_external_ref_collision_creates_a_separate_conversation(api, seed):
    """The chat site's thread ids are its own namespace, so two tenants can legitimately use the
    same one. `chat_conversations` is UNIQUE (client_id, external_ref) — never external_ref
    alone — so tenant A writing B's thread id must create a NEW conversation under A and must
    never attach to, read from, or renumber B's.
    """
    require_chat_route(api, TURNS)
    shared_ref = f"shared-{uuid.uuid4().hex[:12]}"

    # Pre-create the conversation under B, directly, so the collision is real.
    b_conv = sql(lambda c: c.fetchval(
        "INSERT INTO chat_conversations (client_id, external_ref, channel, locale) "
        "VALUES ($1,$2,'web','en') RETURNING id",
        uuid.UUID(seed["b"]["client_id"]), shared_ref))
    b_turns_before = chat_row_counts(seed["b"]["client_id"])["turns"]

    r = _post_turn(api, _cq_headers(seed, seed["a"]["client_id"], seed["a"]["client_id"]),
                   _turn_body(shared_ref))
    assert r.status_code in (200, 201, 202), r.text
    body = r.json()

    # A distinct conversation row now exists under A for the same external_ref.
    rows = sql(lambda c: c.fetch(
        "SELECT id, client_id FROM chat_conversations WHERE external_ref = $1", shared_ref))
    owners = {str(row["client_id"]) for row in rows}
    assert owners == {seed["a"]["client_id"], seed["b"]["client_id"]}, owners
    a_conv = next(str(row["id"]) for row in rows
                  if str(row["client_id"]) == seed["a"]["client_id"])
    assert a_conv != str(b_conv), "tenant A attached to tenant B's conversation row"

    # Nothing was appended to B.
    assert chat_row_counts(seed["b"]["client_id"])["turns"] == b_turns_before

    # And B's KB never reaches A, even though the fake embedder makes B's chunk the perfect
    # semantic match for this query. Check the ingest response and the settled suggestion.
    assert B_MARK not in r.text
    settled = _await_suggestion(api, seed, body)
    if settled is not None:
        assert B_MARK not in json.dumps(settled), "LEAK: tenant B's KB surfaced in tenant A's suggestion"
        # The gate is deterministic and pre-LLM: with nothing retrievable from A's own KB this
        # must be a refusal, reached without a model call (no_llm would have detonated).
        grounding = settled.get("grounding") or {}
        if "grounded" in grounding:
            assert grounding["grounded"] is False, settled

    # Belt and braces at the row level: no turn of A's may reference B's conversation.
    crossed = sql(lambda c: c.fetchval(
        "SELECT count(*) FROM chat_turns WHERE client_id = $1 AND conversation_id = $2",
        uuid.UUID(seed["a"]["client_id"]), b_conv))
    assert crossed == 0


def _await_suggestion(api, seed, ingest_body: dict, timeout_s: float = 5.0):
    """Poll the warm path until the precompute settles. Returns None if there is nothing to poll
    (no suggest_ref in the response, or the endpoint is not mounted yet)."""
    suggest_ref = (ingest_body or {}).get("suggest_ref")
    if not suggest_ref or not chat_route_present(api, SUGGESTIONS):
        return None
    url = SUGGESTIONS.format(suggest_ref=suggest_ref)
    headers = _cq_headers(seed, seed["a"]["client_id"])
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        r = api.get(url, headers=headers)
        if r.status_code != 200:
            return None
        last = r.json()
        if (last.get("state") or "") != "running":
            return last
        time.sleep(0.15)
    return last


# --------------------------------------------------------------------------- #
# 6. One bad element rejects the whole batch
# --------------------------------------------------------------------------- #
def test_sync_batch_is_all_or_nothing(api, seed):
    """`conversations:sync` carries a per-conversation `client_id` (the chat site's own
    mapping). A partial apply is the worst possible outcome: the caller gets an error, assumes
    nothing landed, retries — and the good half is written twice while the mismatched one is
    silently dropped. Reject the batch, write nothing.
    """
    require_chat_route(api, SYNC)
    before_a = chat_row_counts(seed["a"]["client_id"])
    before_b = chat_row_counts(seed["b"]["client_id"])

    good_ref = f"sync-good-{uuid.uuid4().hex[:8]}"
    bad_ref = f"sync-bad-{uuid.uuid4().hex[:8]}"
    payload = {"conversations": [
        {"client_id": seed["a"]["client_id"], "external_ref": good_ref,
         "channel": "web", "locale": "en", "turns": [
             {"turn_ref": f"{good_ref}-1", "role": "customer", "content": "hello"}]},
        # Same batch, someone else's tenant.
        {"client_id": seed["b"]["client_id"], "external_ref": bad_ref,
         "channel": "web", "locale": "en", "turns": [
             {"turn_ref": f"{bad_ref}-1", "role": "customer", "content": "hello"}]},
    ]}
    r = api.post(SYNC, headers=_cq_headers(seed, seed["a"]["client_id"], seed["a"]["client_id"]),
                 json=payload)
    assert r.status_code in (400, 403, 422), f"mismatched batch accepted: {r.status_code} {r.text}"

    assert chat_row_counts(seed["a"]["client_id"]) == before_a, "the good half of a rejected batch was written"
    assert chat_row_counts(seed["b"]["client_id"]) == before_b
    assert sql(lambda c: c.fetchval(
        "SELECT count(*) FROM chat_conversations WHERE external_ref = ANY($1::text[])",
        [good_ref, bad_ref])) == 0


# --------------------------------------------------------------------------- #
# 7. The never-issued scopes
# --------------------------------------------------------------------------- #
FORBIDDEN = ["kb:write", "kb:delete", "scoring:write", "admin:*", "admin:settings",
             "admin:tenants", "kb:write:bulk"]


@pytest.mark.parametrize("scope", FORBIDDEN)
def test_forbidden_scopes_are_never_issuable(scope):
    """The predictable scope creep is "just let curation apply proposals directly". That single
    change would make this credential exactly as dangerous as the plaintext tenant key it
    replaces — applying a curation proposal must stay a tenant-admin-authenticated review
    action. This test is what stops it, so it asserts the *policy*, not just the current list.
    """
    with pytest.raises(ValueError):
        chat_credentials.validate_scopes([scope])
    # Also rejected when smuggled alongside a legitimate one — the loop must not stop at the
    # first valid entry.
    with pytest.raises(ValueError):
        chat_credentials.validate_scopes(["chat:turn", scope])
    assert scope not in chat_credentials.SCOPES


def test_issuable_scope_set_is_read_only_chat():
    assert set(chat_credentials.SCOPES) == {"chat:turn", "chat:suggest", "chat:answer", "chat:sync"}
    for s in chat_credentials.SCOPES:
        assert not s.startswith(chat_credentials.FORBIDDEN_SCOPE_PREFIXES)
    # An empty grant is not "all scopes".
    with pytest.raises(ValueError):
        chat_credentials.validate_scopes([])


def test_admin_issuance_rejects_forbidden_scopes(api, seed):
    """End to end through the operator's own endpoint, so the guard cannot be bypassed by the
    router forgetting to call validate_scopes().
    """
    hdr = {"X-Admin-Token": settings.admin_token}
    probe = api.get("/admin/integrations", headers=hdr)
    if probe.status_code == 401:
        pytest.skip("ADMIN_TOKEN not configured in this environment")

    r = api.post("/admin/integrations", headers=hdr,
                 json={"name": "creep", "scopes": ["chat:turn", "kb:write"],
                       "grants": [seed["a"]["slug"]]})
    assert r.status_code == 400, r.text
    assert "kb:write" in r.text

    # And the P1 single-grant policy: a credential must not be issued for two tenants at once.
    r = api.post("/admin/integrations", headers=hdr,
                 json={"name": "wide", "scopes": ["chat:turn"],
                       "grants": [seed["a"]["slug"], seed["b"]["slug"]]})
    assert r.status_code == 400, r.text

    # Nothing was created by either attempt.
    assert sql(lambda c: c.fetchval(
        "SELECT count(*) FROM integrations WHERE name = ANY($1::text[])", ["creep", "wide"])) == 0


def test_integration_principal_is_not_a_tenant_principal(api, seed):
    """`is_tenant` must stay False for an integration principal. Every pre-existing tenant route
    gates on it, so if this flips, a chat key becomes a skeleton key for /kb, /analyze and
    /scoring — routes it was never scoped for — with no change to any of them.
    """
    p = Principal(kind="integration", client_id=seed["a"]["client_id"],
                  integration_id=seed["integration"]["integration_id"], scopes=["chat:turn"])
    assert p.is_integration is True
    assert p.is_tenant is False
    assert p.is_superadmin is False

    # Observable over HTTP: the chat credential is valid, and still cannot read the KB.
    headers = _cq_headers(seed, seed["a"]["client_id"])
    r = api.get("/kb/documents", headers=headers)
    assert r.status_code in (401, 403, 404), f"chat key reached the KB: {r.status_code} {r.text}"
