"""GET/PUT /chat/config — the tenant portal's own bot settings.

The route is a mirror of /admin/chat/{tenant_id}/config, and what these tests pin is the part
a mirror can get wrong: WHO it answers, and WHOSE row it answers with. Driven through the real
ASGI app with the tenants from conftest's `seed` (A and B each own one published document, so
A may legitimately switch autopilot on). No model call is ever made — `no_llm` detonates if one
is — because a config read or write never touches Claude.
"""
import uuid

import pytest

from app.services import auth
from conftest import sql

CFG = "/chat/config"


def _key(seed, label: str) -> dict:
    # A tenant API key resolves to role "apikey", which `may_configure_workspace` accepts.
    return {"X-API-Key": seed[label]["api_key"]}


def _body(**over) -> dict:
    body = {"persona": None, "greeting": {}, "refusal_copy": {}, "languages": ["en", "ka", "ru"],
            "canned": [], "autopilot_enabled": False, "settings": {}}
    body.update(over)
    return body


@pytest.fixture
def kill_switch_off():
    """The `killed` flag is the operator's global brake as well as a per-tenant list. A fresh
    seed tenant cannot be on the list, but a developer who left the global brake on would see
    `killed: true` for reasons unrelated to this route — say so instead of failing."""
    raw = sql(lambda c: c.fetchval("SELECT value FROM app_settings WHERE key = 'autopilot_kill'"))
    if raw:
        import json
        blob = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if blob.get("global_disabled"):
            pytest.skip("the autopilot kill switch is globally ON in this database")


@pytest.fixture
def member_login(api, seed):
    """A member-role login for tenant A: a real tenant_users row and a real /auth/login, so the
    403 below comes from the resolver's role, not from a hand-built principal."""
    suffix = uuid.uuid4().hex[:8]
    username, password = f"botcfg-member-{suffix}", f"pw-{suffix}"
    uid = sql(lambda c: c.fetchval(
        "INSERT INTO tenant_users (client_id, username, password_hash, role) "
        "VALUES ($1, $2, $3, 'member') RETURNING id",
        uuid.UUID(seed["a"]["client_id"]), username, auth.hash_password(password)))
    try:
        r = api.post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200 and r.json()["scope"] == "tenant", r.text
        assert r.json()["role"] == "member"
        yield {"Authorization": f"Bearer {r.json()['token']}"}
    finally:
        sql(lambda c: c.execute("DELETE FROM tenant_users WHERE id = $1", uid))


@pytest.fixture
def reset_a(api, seed):
    """Whatever a test saved for A is undone afterwards: autopilot back to false, persona
    cleared. The seed tenants are session-scoped, so a stale `autopilot_enabled` would leak
    into the next test in this file — and into any other file sharing the seed."""
    yield
    r = api.put(CFG, headers=_key(seed, "a"), json=_body())
    assert r.status_code == 200, r.text
    assert r.json()["autopilot_enabled"] is False


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def test_get_as_tenant_is_defaults_with_killed_false(api, seed, kill_switch_off):
    r = api.get(CFG, headers=_key(seed, "a"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["autopilot_enabled"] is False
    assert d["killed"] is False
    # The knobs the portal's fillBot reads are lifted to the top level by chat_store.
    for knob in ("min_score", "min_hits", "top_k", "suggestion_count", "languages", "settings"):
        assert knob in d, f"missing {knob!r} in {sorted(d)}"


def test_anonymous_is_401(api):
    assert api.get(CFG).status_code == 401
    assert api.put(CFG, json=_body()).status_code == 401


def test_integration_key_is_not_a_tenant_here(api, seed):
    """A chat credential has /v1/chat/config for its read; it must not be able to use the
    portal route — and never to write through it."""
    h = {"X-CQ-Key": seed["integration"]["api_key"], "X-CQ-Tenant": seed["a"]["client_id"]}
    assert api.get(CFG, headers=h).status_code == 401
    assert api.put(CFG, headers=h, json=_body(persona="hijack")).status_code == 401


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
def test_put_persona_round_trips_and_answers_get_shape(api, seed, reset_a, kill_switch_off):
    persona = f"Helpful-{uuid.uuid4().hex[:6]}"
    r = api.put(CFG, headers=_key(seed, "a"), json=_body(persona=persona))
    assert r.status_code == 200, r.text
    saved = r.json()
    # PUT answers with GET's merged shape, so the portal refills the form from it directly.
    assert saved["persona"] == persona
    assert saved["killed"] is False
    assert saved["version"] >= 1
    assert saved["updated_by"] == "tenant:apikey"

    g = api.get(CFG, headers=_key(seed, "a")).json()
    assert g["persona"] == persona
    assert g["version"] == saved["version"]


def test_put_autopilot_on_with_a_published_document(api, seed, reset_a):
    # Seed A owns a visibility='public' document, so the 409 guard must let this through.
    r = api.put(CFG, headers=_key(seed, "a"),
                json=_body(autopilot_enabled=True, refusal_copy={"en": "I cannot help with that."}))
    assert r.status_code == 200, r.text
    assert r.json()["autopilot_enabled"] is True
    assert api.get(CFG, headers=_key(seed, "a")).json()["autopilot_enabled"] is True


def test_put_autopilot_on_without_a_published_document_is_409(api):
    """A workspace whose KB has nothing public would refuse every customer question; the
    portal turns this exact status into its "share a document with the bot first" card."""
    suffix = uuid.uuid4().hex[:8]
    key = f"botcfg-bare-{suffix}"
    cid = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
        f"botcfg-bare-{suffix}", "bot-config bare", key))
    try:
        r = api.put(CFG, headers={"X-API-Key": key}, json=_body(autopilot_enabled=True))
        assert r.status_code == 409, r.text
        assert "public" in r.json()["detail"]
        # Refused means nothing was written: the tenant is still on the defaults.
        assert api.get(CFG, headers={"X-API-Key": key}).json()["autopilot_enabled"] is False
    finally:
        sql(lambda c: c.execute("DELETE FROM clients WHERE id = $1", cid))


def test_member_role_reads_but_cannot_write(api, member_login):
    assert api.get(CFG, headers=member_login).status_code == 200
    r = api.put(CFG, headers=member_login, json=_body(persona="member edit"))
    assert r.status_code == 403, r.text


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #
def test_tenant_b_never_sees_tenant_a_persona(api, seed, reset_a):
    persona = f"A-ONLY-PERSONA-{uuid.uuid4().hex[:6]}"
    assert api.put(CFG, headers=_key(seed, "a"), json=_body(persona=persona)).status_code == 200

    rb = api.get(CFG, headers=_key(seed, "b"))
    assert rb.status_code == 200, rb.text
    assert persona not in rb.text
    assert rb.json()["persona"] != persona

    # And B's own save does not disturb A's row.
    assert api.put(CFG, headers=_key(seed, "b"), json=_body(persona="B persona")).status_code == 200
    assert api.get(CFG, headers=_key(seed, "a")).json()["persona"] == persona
    # Leave B on the defaults too; the seed is shared across files.
    assert api.put(CFG, headers=_key(seed, "b"), json=_body()).status_code == 200
