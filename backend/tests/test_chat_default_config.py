"""The superadmin-editable DEFAULT chat config, and how a tenant inherits it.

Driven over HTTP through the real admin routes, for the same reason the isolation suite is:
the contract that matters is what the console and the chat engine observe, not the shape of a
python dict. Three properties are pinned here because each has a failure mode that would be
invisible until it hit a customer:

  * **A default can never switch a bot on.** Whatever a PUT body carries, `autopilot_enabled`
    on the default is False — that switch is per tenant, with its own 409 guard, forever.
  * **Inheritance is field by field, not wholesale.** A tenant that saves one greeting must
    keep the operator's refusal copy in the other two languages, and a bespoke per-minute cap
    must not drop the inherited per-hour one.
  * **The default is range-checked on the way in.** It is inherited by every tenant at once,
    so a typo'd min_score would refuse every question on every bot; the write is where that
    must be caught, as a 400 the operator can read.

Every test starts from and returns to the built-in default: the blob row is deleted and the
5 s process cache dropped around each test, so the files that run after this one still see
`source == "builtin"` — they were written against the code defaults and must stay green.
"""
import uuid

import pytest

from app.config import settings
from app.services import chat_store
from conftest import sql

ADMIN = {"X-Admin-Token": settings.admin_token}
DEFAULT_URL = "/admin/chat/default-config"


def _reset_default() -> None:
    """Back to builtin: no stored blob AND no cached copy of the one that was just deleted.
    The cache is a module global in the app's process — the same process as this test — so
    resetting it here is what makes the next GET hit the (now empty) row."""
    sql(lambda c: c.execute("DELETE FROM app_settings WHERE key = $1",
                            chat_store.DEFAULT_CHAT_CONFIG_KEY))
    chat_store._default_cache = None


@pytest.fixture(autouse=True)
def builtin_default(api):
    _reset_default()
    r = api.get(DEFAULT_URL, headers=ADMIN)
    if r.status_code == 401:
        pytest.skip("ADMIN_TOKEN not configured in this environment")
    try:
        yield
    finally:
        _reset_default()


@pytest.fixture
def tenant(seed):
    """Seed tenant A with no chat config of its own — the day-one state. Its rows are removed
    on both sides so the inheritance assertions cannot be fooled by a version left behind by
    another file, and so nothing this file saves outlives it."""
    cid = seed["a"]["client_id"]

    def _wipe():
        sql(lambda c: c.execute("DELETE FROM chat_configs WHERE client_id = $1", uuid.UUID(cid)))

    _wipe()
    try:
        yield cid
    finally:
        _wipe()


def _tenant_url(cid: str) -> str:
    return f"/admin/chat/{cid}/config"


# A representative operator default: copy in two languages, a tighter threshold than the code
# default, both kinds of rate cap, and a disclosure policy.
STORED = {
    "persona": "You are the CommuniQ default assistant.",
    "greeting": {"en": "Hello from the default", "ka": "გამარჯობა (default)"},
    "refusal_copy": {"en": "Default refusal", "ka": "ვერ დაგეხმარებით (default)"},
    "languages": ["en", "ka"],
    "canned": [{"key": "hours", "text": "We are open 9-18."}],
    "settings": {
        "min_score": 0.5,
        "top_k": 6,
        "limits": {"tenant_per_minute": 30, "enduser_per_hour": 40},
        "disclosure_mode": "always",
        "disclosure": {"en": "Default AI disclosure", "ka": ""},
    },
}


# --------------------------------------------------------------------------- #
# The default itself
# --------------------------------------------------------------------------- #
def test_builtin_before_any_save(api):
    r = api.get(DEFAULT_URL, headers=ADMIN)
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["source"] == "builtin"
    assert cfg["autopilot_enabled"] is False
    assert cfg["is_active"] is False
    assert cfg["version"] == 0
    assert cfg["is_default"] is True
    assert cfg["persona"] is None
    assert cfg["languages"] == chat_store.CHAT_CONFIG_DEFAULTS["languages"]
    assert cfg["min_score"] == chat_store.CHAT_CONFIG_DEFAULTS["min_score"]
    assert cfg["updated_at"] is None and cfg["updated_by"] is None


def test_put_then_get_reflects(api):
    r = api.put(DEFAULT_URL, headers=ADMIN, json=STORED)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["source"] == "stored"
    assert saved["updated_by"] == "superadmin"
    assert saved["updated_at"], "a stored default must say when it was saved"
    assert saved["persona"] == STORED["persona"]
    assert saved["greeting"] == STORED["greeting"]
    assert saved["refusal_copy"] == STORED["refusal_copy"]
    assert saved["languages"] == ["en", "ka"]
    assert saved["canned"] == STORED["canned"]
    # The gate knobs are lifted out of settings, exactly as on a tenant config.
    assert saved["min_score"] == 0.5 and saved["top_k"] == 6
    assert saved["settings"]["limits"] == STORED["settings"]["limits"]
    assert saved["settings"]["disclosure_mode"] == "always"
    # Present-but-empty disclosure copy survives the round trip: it means "suppressed".
    assert saved["settings"]["disclosure"] == {"en": "Default AI disclosure", "ka": ""}

    r = api.get(DEFAULT_URL, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json() == saved


def test_a_default_can_never_switch_autopilot_on(api):
    body = dict(STORED, autopilot_enabled=True,
                settings=dict(STORED["settings"], autopilot_enabled=True))
    r = api.put(DEFAULT_URL, headers=ADMIN, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["autopilot_enabled"] is False
    assert "autopilot_enabled" not in r.json()["settings"]

    got = api.get(DEFAULT_URL, headers=ADMIN).json()
    assert got["source"] == "stored"
    assert got["autopilot_enabled"] is False
    assert "autopilot_enabled" not in got["settings"]


@pytest.mark.parametrize("patch, needle", [
    ({"settings": {"min_score": 35}}, "min_score"),
    ({"settings": {"min_score": "high"}}, "min_score"),
    ({"settings": {"top_k": 0}}, "top_k"),
    ({"settings": {"suggestion_count": 9}}, "suggestion_count"),
    ({"settings": {"max_reply_chars": 10}}, "max_reply_chars"),
    ({"settings": {"limits": {"tenant_per_minute": -1}}}, "limits"),
    ({"settings": {"disclosure_mode": "sometimes"}}, "disclosure_mode"),
    ({"settings": {"allow_general_knowledge": "yes"}}, "allow_general_knowledge"),
    ({"settings": {"escalation_keywords": "human"}}, "escalation_keywords"),
    ({"languages": []}, "languages"),
    ({"languages": ["de"]}, "languages"),
    ({"greeting": {"fr": "Bonjour"}}, "greeting"),
    ({"refusal_copy": {"en": ["not", "a", "string"]}}, "refusal_copy"),
])
def test_invalid_default_is_400_and_names_the_field(api, patch, needle):
    r = api.put(DEFAULT_URL, headers=ADMIN, json=dict(STORED, **patch))
    assert r.status_code == 400, f"{patch} -> {r.status_code} {r.text}"
    assert needle in r.text, r.text
    # A rejected save must not have landed.
    assert api.get(DEFAULT_URL, headers=ADMIN).json()["source"] == "builtin"


def test_admin_token_is_required(api):
    assert api.get(DEFAULT_URL).status_code == 401
    assert api.put(DEFAULT_URL, json=STORED).status_code == 401
    assert api.get(DEFAULT_URL, headers={"X-Admin-Token": "not-it"}).status_code == 401


# --------------------------------------------------------------------------- #
# Inheritance
# --------------------------------------------------------------------------- #
def test_tenant_without_a_row_inherits_the_stored_default(api, tenant):
    assert api.put(DEFAULT_URL, headers=ADMIN, json=STORED).status_code == 200

    r = api.get(_tenant_url(tenant), headers=ADMIN)
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["is_default"] is True
    assert cfg["source"] == "stored"
    assert cfg["persona"] == STORED["persona"]
    assert cfg["refusal_copy"]["ka"] == STORED["refusal_copy"]["ka"]
    assert cfg["languages"] == ["en", "ka"]
    assert cfg["min_score"] == 0.5 and cfg["top_k"] == 6
    assert cfg["settings"]["limits"] == STORED["settings"]["limits"]
    assert cfg["autopilot_enabled"] is False
    assert cfg["version"] == 0


def test_tenant_row_overrides_field_by_field(api, tenant):
    assert api.put(DEFAULT_URL, headers=ADMIN, json=STORED).status_code == 200

    # The tenant saves ONE greeting and ONE cap. Everything else is the form's empty state.
    r = api.put(_tenant_url(tenant), headers=ADMIN, json={
        "greeting": {"en": "Tenant hello", "ka": ""},
        "settings": {"limits": {"tenant_per_minute": 5}},
    })
    assert r.status_code == 200, r.text
    for cfg in (r.json(), api.get(_tenant_url(tenant), headers=ADMIN).json()):
        assert cfg["is_default"] is False
        assert "source" not in cfg
        assert cfg["version"] >= 1
        # Its own greeting where it wrote one; the default's where it left the field blank.
        assert cfg["greeting"]["en"] == "Tenant hello"
        assert cfg["greeting"]["ka"] == STORED["greeting"]["ka"]
        # Untouched sections come through from the default whole.
        assert cfg["refusal_copy"]["ka"] == STORED["refusal_copy"]["ka"]
        assert cfg["persona"] == STORED["persona"]
        assert cfg["canned"] == STORED["canned"]
        assert cfg["languages"] == ["en", "ka", "ru"], "the tenant row's languages win"
        # limits merge one level deep: the bespoke per-minute cap keeps the inherited per-hour.
        assert cfg["settings"]["limits"] == {"tenant_per_minute": 5, "enduser_per_hour": 40}
        # Knobs are lifted from the MERGED settings, so the operator's threshold still applies.
        assert cfg["min_score"] == 0.5 and cfg["top_k"] == 6
        assert cfg["settings"]["disclosure_mode"] == "always"
        assert cfg["autopilot_enabled"] is False


def test_tenant_values_win_where_they_are_set(api, tenant):
    assert api.put(DEFAULT_URL, headers=ADMIN, json=STORED).status_code == 200
    r = api.put(_tenant_url(tenant), headers=ADMIN, json={
        "persona": "Tenant persona",
        "languages": ["ru"],
        "canned": [{"key": "own", "text": "own snippet"}],
        "settings": {"min_score": 0.2, "disclosure": {"ka": "ტენანტის დისკლოზერი"}},
    })
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["persona"] == "Tenant persona"
    assert cfg["languages"] == ["ru"]
    assert cfg["canned"] == [{"key": "own", "text": "own snippet"}]
    assert cfg["min_score"] == 0.2
    # disclosure merges per language: the tenant's Georgian, the default's English.
    assert cfg["settings"]["disclosure"] == {"en": "Default AI disclosure",
                                            "ka": "ტენანტის დისკლოზერი"}


def test_a_saved_default_reaches_tenants_without_a_restart(api, tenant):
    """The cache is invalidated by the setter, so the very next tenant read sees the new
    baseline — an operator must not have to wait out a TTL to confirm their change."""
    before = api.get(_tenant_url(tenant), headers=ADMIN).json()
    assert before["persona"] is None and before["source"] == "builtin"
    assert api.put(DEFAULT_URL, headers=ADMIN, json=STORED).status_code == 200
    after = api.get(_tenant_url(tenant), headers=ADMIN).json()
    assert after["persona"] == STORED["persona"] and after["source"] == "stored"
