"""PUT /admin/chat/{tenant_id}/autopilot — the Bot control switch, and only the switch.

The console's per-workspace Autopilot cell used to be a read-only pill; this route is what makes
it a control. What these tests pin is everything the obvious implementation gets wrong:

  * **It changes one field.** PUT /admin/chat/{id}/config writes a whole version from its body,
    so "just send autopilot_enabled" would erase the tenant's persona and lawyer-reviewed refusal
    copy. The switch must copy the tenant's stored row verbatim — asserted on the raw columns,
    not on the merged read, because the merge would hide a field that went missing.
  * **It does not detach a tenant from the default bot.** A tenant with no row of its own must
    get a row whose fields are the inherit values (empty `languages` included), so the next
    Default bot edit still reaches it.
  * **It teaches when it refuses.** No published document is a 409 with a machine-readable
    `code` at the top level, which the console turns into "share a document with the bot first".
  * **A double click is not a version.**

Plain synchronous tests over HTTP with standalone SQL fixtures, for the reason conftest's
docstring gives: no event loop may be shared with the app. No model call is made anywhere.
"""
import json
import uuid

import pytest

from app.config import settings
from app.services import chat_store
from conftest import sql

ADMIN = {"X-Admin-Token": settings.admin_token}


def _switch(tenant_id: str) -> str:
    return f"/admin/chat/{tenant_id}/autopilot"


def _config(tenant_id: str) -> str:
    return f"/admin/chat/{tenant_id}/config"


@pytest.fixture
def tenant(api):
    """A fresh workspace with no documents and no chat config — the day-one state. Its own
    clients row, so nothing here disturbs the shared `seed` tenants other files assert on, and
    one DELETE reclaims its documents and every chat_configs version (both cascade)."""
    if api.get("/admin/chat/default-config", headers=ADMIN).status_code == 401:
        pytest.skip("ADMIN_TOKEN not configured in this environment")
    suffix = uuid.uuid4().hex[:8]
    cid = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
        f"apswitch-{suffix}", "autopilot switch", f"apswitch-key-{suffix}"))
    try:
        yield str(cid)
    finally:
        sql(lambda c: c.execute("DELETE FROM clients WHERE id = $1", cid))


def _publish(tenant_id: str) -> None:
    """One document shared with the bot — exactly what the 409 gate counts."""
    sql(lambda c: c.execute(
        "INSERT INTO kb_documents (client_id, doc_type, title, status, visibility) "
        "VALUES ($1,'policy','Refund policy','ready','public')", uuid.UUID(tenant_id)))


def _active_row(tenant_id: str):
    """The RAW active row. jsonb read back as ::text is Postgres's canonical rendering, so two
    versions comparing equal here hold the same value, not merely the same merged view."""
    return sql(lambda c: c.fetchrow(
        """SELECT version, persona, greeting::text AS greeting,
                  refusal_copy::text AS refusal_copy, languages, canned::text AS canned,
                  settings::text AS settings, autopilot_enabled, updated_by
             FROM chat_configs WHERE client_id = $1 AND is_active""", uuid.UUID(tenant_id)))


def _row_count(tenant_id: str) -> int:
    return sql(lambda c: c.fetchval(
        "SELECT count(*) FROM chat_configs WHERE client_id = $1", uuid.UUID(tenant_id)))


COPIED = ("persona", "greeting", "refusal_copy", "languages", "canned", "settings")


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def test_enable_without_a_public_document_is_409_and_writes_nothing(api, tenant):
    r = api.put(_switch(tenant), headers=ADMIN, json={"enabled": True})
    assert r.status_code == 409, r.text
    body = r.json()
    # Top level, not nested under `detail` — the console branches on `code`.
    assert body["code"] == "no_public_documents"
    assert body["public_documents"] == 0
    assert "public" in body["detail"]
    assert _row_count(tenant) == 0


def test_enable_after_publishing_one_document(api, tenant):
    _publish(tenant)
    r = api.put(_switch(tenant), headers=ADMIN, json={"enabled": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["autopilot_enabled"] is True
    assert body["public_documents"] == 1
    assert body["is_default"] is False
    assert body["version"] >= 1
    assert api.get(_config(tenant), headers=ADMIN).json()["autopilot_enabled"] is True
    assert _active_row(tenant)["updated_by"] == "superadmin"


def test_disabling_needs_no_public_document(api, tenant):
    """Turning a bot OFF is the safe direction and must never be blocked by the gate."""
    r = api.put(_switch(tenant), headers=ADMIN, json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["autopilot_enabled"] is False
    assert r.json()["public_documents"] == 0


# --------------------------------------------------------------------------- #
# One field changes; everything else is copied verbatim
# --------------------------------------------------------------------------- #
def test_custom_copy_survives_enable_and_disable_byte_for_byte(api, tenant):
    _publish(tenant)
    persona = f"Nino from Refunds {uuid.uuid4().hex[:6]}"
    saved = api.put(_config(tenant), headers=ADMIN, json={
        "persona": persona,
        "greeting": {"en": "Hello, how can I help?", "ka": "გამარჯობა, როგორ შემიძლია დაგეხმაროთ?"},
        "refusal_copy": {"en": "I cannot help with that.",
                         "ru": "К сожалению, я не могу с этим помочь."},
        "languages": ["ka", "ru"],
        "canned": [{"trigger": "hours", "text": "We are open 9 to 6."}],
        "autopilot_enabled": False,
        "settings": {"min_score": 0.5},
    })
    assert saved.status_code == 200, saved.text
    before = _active_row(tenant)
    assert before["persona"] == persona and before["autopilot_enabled"] is False

    on = api.put(_switch(tenant), headers=ADMIN, json={"enabled": True})
    assert on.status_code == 200, on.text
    after_on = _active_row(tenant)
    assert after_on["autopilot_enabled"] is True
    assert after_on["version"] == before["version"] + 1
    for col in COPIED:
        assert after_on[col] == before[col], f"{col} changed on enable"

    off = api.put(_switch(tenant), headers=ADMIN, json={"enabled": False})
    assert off.status_code == 200, off.text
    after_off = _active_row(tenant)
    assert after_off["autopilot_enabled"] is False
    assert after_off["version"] == before["version"] + 2
    for col in COPIED:
        assert after_off[col] == before[col], f"{col} changed on disable"

    # And the merged read the console and the bot see still carries the tenant's own copy.
    g = api.get(_config(tenant), headers=ADMIN).json()
    assert g["persona"] == persona
    assert g["greeting"]["ka"] == "გამარჯობა, როგორ შემიძლია დაგეხმაროთ?"
    assert g["refusal_copy"]["ru"] == "К сожалению, я не могу с этим помочь."


def test_tenant_with_no_row_keeps_inheriting_the_default(api, tenant):
    _publish(tenant)
    assert _row_count(tenant) == 0
    r = api.put(_switch(tenant), headers=ADMIN, json={"enabled": True})
    assert r.status_code == 200, r.text

    row = _active_row(tenant)
    assert row["autopilot_enabled"] is True
    # Inherit values, not today's default frozen into the row.
    assert row["persona"] is None
    assert list(row["languages"]) == []
    assert json.loads(row["greeting"]) == {} and json.loads(row["refusal_copy"]) == {}
    assert json.loads(row["canned"]) == [] and json.loads(row["settings"]) == {}

    # The app runs in this process, so its 5 s default cache can be dropped from here: the
    # comparison below must be against the default as stored now, not a copy from earlier.
    chat_store._default_cache = None
    default = api.get("/admin/chat/default-config", headers=ADMIN).json()
    g = api.get(_config(tenant), headers=ADMIN).json()
    assert g["is_default"] is False          # the tenant now has a row of its own ...
    assert g["languages"] == default["languages"]  # ... and still inherits through it


def test_repeated_enable_does_not_bump_the_version(api, tenant):
    _publish(tenant)
    first = api.put(_switch(tenant), headers=ADMIN, json={"enabled": True})
    second = api.put(_switch(tenant), headers=ADMIN, json={"enabled": True})
    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert second.json()["version"] == first.json()["version"]
    assert _row_count(tenant) == 1


# --------------------------------------------------------------------------- #
# Who and which tenant
# --------------------------------------------------------------------------- #
def test_unknown_tenant_is_404_with_a_code(api):
    if api.get("/admin/chat/default-config", headers=ADMIN).status_code == 401:
        pytest.skip("ADMIN_TOKEN not configured in this environment")
    for enabled in (True, False):
        r = api.put(_switch(str(uuid.uuid4())), headers=ADMIN, json={"enabled": enabled})
        assert r.status_code == 404, r.text
        assert r.json()["code"] == "tenant_not_found"


def test_requires_the_admin_token(api, tenant):
    _publish(tenant)
    assert api.put(_switch(tenant), json={"enabled": True}).status_code == 401
    assert api.put(_switch(tenant), headers={"X-Admin-Token": "not-it"},
                   json={"enabled": True}).status_code == 401
    assert _row_count(tenant) == 0
