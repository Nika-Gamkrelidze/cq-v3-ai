"""The AI-usage report is the superadmin's alone — no tenant credential reaches it in any form.

It shows every workspace's spend side by side, so a tenant who could read it would see its
competitors' volumes. The router is guarded by `require_admin` (the admin token and nothing
else); this file pins that against the credentials a tenant actually holds: a signed-in OWNER's
token, the workspace API key, and both combined with the operator header `X-Act-As-Tenant`
(inert for anyone but a verified superadmin — see test_act_as_tenant.py)."""
import uuid

import pytest

from app.config import settings
from app.services import auth
from conftest import sql

PATHS = ("overview", "calls", "recordings", "conversations", "pricing",
         f"recordings/{uuid.uuid4()}", f"conversations/{uuid.uuid4()}")
LEGACY = ("/admin/usage/tenants", f"/admin/usage/tenants/{uuid.uuid4()}")


@pytest.fixture
def tenant(api):
    """A workspace with an api key and a real owner login through /auth/login."""
    suffix = uuid.uuid4().hex[:8]
    api_key = f"usage-only-{suffix}"
    cid = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
        f"usage-only-{suffix}", f"usage-only-{suffix}", api_key))
    other = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name) VALUES ($1,$2) RETURNING id",
        f"usage-other-{suffix}", f"usage-other-{suffix}"))
    username, password = f"usage-owner-{suffix}", f"pw-{suffix}"
    sql(lambda c: c.execute(
        "INSERT INTO tenant_users (client_id, username, password_hash, role) VALUES ($1,$2,$3,'owner')",
        cid, username, auth.hash_password(password)))
    try:
        r = api.post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200 and r.json()["scope"] == "tenant", r.text
        yield {"bearer": {"Authorization": f"Bearer {r.json()['token']}"},
               "key": {"X-API-Key": api_key}, "other": str(other)}
    finally:
        for c_id in (cid, other):
            sql(lambda c, c_id=c_id: c.execute("DELETE FROM clients WHERE id = $1", c_id))


def _credentials(t: dict) -> dict[str, dict]:
    act = {"X-Act-As-Tenant": t["other"]}
    return {"owner login": t["bearer"], "api key": t["key"],
            "owner login + act-as": {**t["bearer"], **act}, "api key + act-as": {**t["key"], **act},
            "owner login + forged admin token": {**t["bearer"], "X-Admin-Token": "owner-guess"}}


def test_no_tenant_credential_reads_any_usage_report(api, tenant):
    for name, headers in _credentials(tenant).items():
        for path in PATHS:
            r = api.get(f"/admin/usage/{path}", headers=headers)
            assert r.status_code == 401, f"{name} reached /admin/usage/{path}: {r.status_code}"
        for path in LEGACY:
            r = api.get(path, headers=headers)
            assert r.status_code == 401, f"{name} reached {path}: {r.status_code}"


def test_the_superadmin_still_does(api):
    """The control: the same requests with the admin token are answered, so the 401s above are
    the guard and not a broken route."""
    r = api.get("/admin/usage/overview", headers={"X-Admin-Token": settings.admin_token})
    assert r.status_code == 200, r.text
    assert api.get("/admin/usage/tenants",
                   headers={"X-Admin-Token": settings.admin_token}).status_code == 200
