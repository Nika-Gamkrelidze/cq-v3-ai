"""Registered accounts (design-v2 §11): the token, sign-up, sign-in, `/auth/me`, profile
edits and the operator's password reset — every path a self-service user's session runs on.

Why these are HTTP tests and not unit tests of `routers/auth.py`: the properties worth pinning
are the ones a browser observes — that a sign-up token is accepted by the very next request,
that the three login failures are indistinguishable, that a deactivated account's still-valid
token stops working at `/auth/me` — and each of those crosses `resolve_principal`, the router
and the `app_users` row. The pure half (the token payload, `is_user`) needs no database and
runs everywhere; everything else inherits conftest's skip-without-a-database rule.

Every account created here is registered through the REAL `/auth/register` route (so the
tests break if the sign-up shape changes) and deleted by id afterwards, together with the
usage/recording rows a session could have left behind. The registered tier is pinned to a
known blob for the duration of each test and restored exactly — including "was absent" — so a
developer's `app_settings` is left as it was found.
"""
import json
import uuid

import pytest

from app.config import settings
from app.services import auth
from app.services.auth import Principal
from conftest import sql  # loop-independent SQL; see its module docstring

REGISTERED_KEY = "registered"
SIGNUP_SCOPE = "anon:testclient"   # what services.auth.client_ip sees for a TestClient call
# The tier as these tests need it: sign-ups open, nothing capped low enough to bite.
REGISTERED_PIN = {
    "enabled": True, "max_analyses_per_day": 50, "max_tts_per_day": 50,
    "max_conversions_per_day": 50, "max_audio_mb": 50,
    # 0 = uncapped sign-ups (still counted): /auth/register is metered per IP, and every
    # account in this file comes from one client host, so the default cap would refuse
    # the eleventh test to ask for an account. The cap itself is tested on its own.
    "max_registrations_per_day": 0,
    "features": {"analyze": True, "tts": True, "convert": True,
                 "summarise": True, "score": True, "semantic": True},
}
GENERIC_401 = "Invalid username or password"
ADMIN = {"X-Admin-Token": settings.admin_token}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _email() -> str:
    return f"cq-authtest-{uuid.uuid4().hex[:10]}@example.test"


# ---------------------------------------------------------------------------
# Fixtures: a pinned registered tier, and accounts that clean up after themselves
# ---------------------------------------------------------------------------
@pytest.fixture
def registered(api):
    """Pin `app_settings.registered` to a known blob; hand back a setter for the tests that
    flip a switch; restore what was there (or its absence) afterwards.

    `settings_store.get_registered_config()` has no cache, so a row written here is what the
    app thread reads on its next request.
    """
    prev = sql(lambda c: c.fetchval("SELECT value FROM app_settings WHERE key = $1", REGISTERED_KEY))

    def _set(**patch) -> dict:
        cfg = {**REGISTERED_PIN, **patch}
        sql(lambda c: c.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            REGISTERED_KEY, json.dumps(cfg)))
        return cfg

    _set()
    yield _set
    # /auth/register is metered per IP and every call here comes from the one TestClient
    # host, so the counter row this file incremented is dropped rather than left to grow.
    sql(lambda c: c.execute(
        "DELETE FROM usage_counters WHERE scope_key = $1 AND kind = 'registrations'",
        SIGNUP_SCOPE))
    if prev is None:
        sql(lambda c: c.execute("DELETE FROM app_settings WHERE key = $1", REGISTERED_KEY))
    else:
        sql(lambda c: c.execute("UPDATE app_settings SET value = $2::jsonb WHERE key = $1",
                                REGISTERED_KEY, prev))


@pytest.fixture
def make_account(api, registered):
    """A factory for registered accounts, created through the real sign-up route.

    Returns `{id, email, password, token, headers}`. Every account (and anything it could
    have written: recordings, summaries, rubrics, counters, TTS/convert history) is deleted
    by id at teardown — idempotently, because some tests delete the account themselves.
    """
    made: list[str] = []

    def _make(display_name: str = "Auth Tester", password: str | None = None) -> dict:
        email, password = _email(), password or f"pw-{uuid.uuid4().hex[:12]}"
        r = api.post("/auth/register",
                     json={"email": email, "password": password, "display_name": display_name})
        assert r.status_code == 200, r.text
        body = r.json()
        made.append(body["user"]["id"])
        return {"id": body["user"]["id"], "email": email, "password": password,
                "token": body["token"], "headers": _bearer(body["token"])}

    yield _make
    if made:
        ids = [uuid.UUID(i) for i in made]

        async def _cleanup(conn):
            await conn.execute("DELETE FROM audio_jobs WHERE user_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM call_summaries WHERE user_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM scoring_configs WHERE user_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM tts_requests WHERE user_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM convert_batches WHERE user_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM usage_counters WHERE scope_key = ANY($1::text[])",
                               [f"user:{i}" for i in made])
            await conn.execute("DELETE FROM app_users WHERE id = ANY($1::uuid[])", ids)
        sql(_cleanup)


def _user_row(user_id: str):
    return sql(lambda c: c.fetchrow(
        "SELECT email, display_name, password_hash, is_active, last_login_at "
        "FROM app_users WHERE id = $1", uuid.UUID(user_id)))


# ---------------------------------------------------------------------------
# Pure: the token payload and the principal property (no database)
# ---------------------------------------------------------------------------
def test_user_token_round_trips_its_discriminator():
    uid = uuid.uuid4()
    payload = auth.verify_token(auth.make_user_token(uid))
    assert payload["kind"] == "user"
    assert payload["user_id"] == str(uid)
    assert payload["exp"] > auth._now()
    # A registered user never has a tenant: the payload must not carry one for the tenant
    # branch of the resolver to pick up.
    assert "client_id" not in payload and "role" not in payload


def test_tenant_token_payload_carries_no_kind():
    """Tenant tokens predate the discriminator; their shape is what keeps every token issued
    before it existed tenant-shaped."""
    payload = auth.verify_token(auth.make_token({"client_id": "c", "user_id": "u", "role": "owner"}))
    assert "kind" not in payload
    assert payload["client_id"] == "c" and payload["role"] == "owner"


def test_tampered_user_token_is_rejected():
    token = auth.make_user_token(uuid.uuid4())
    raw, sig = token.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    assert auth.verify_token(f"{raw}.{flipped}") is None
    assert auth.verify_token(token + "x") is None
    assert auth.verify_token("not-a-token") is None


def test_is_user_requires_the_kind_and_an_id():
    assert Principal(kind="user", user_id="u1").is_user
    assert not Principal(kind="user", user_id="u1").is_tenant
    assert not Principal(kind="user").is_user
    # A tenant login carries a user_id too (its tenant_users row) — that must not read as
    # a registered account.
    assert not Principal(kind="tenant", client_id="c", user_id="u1").is_user


# ---------------------------------------------------------------------------
# Sign-up
# ---------------------------------------------------------------------------
def test_register_returns_a_usable_session(api, make_account):
    email = f"  {_email().upper()}  "
    r = api.post("/auth/register", json={"email": email, "password": "correct horse",
                                         "display_name": "  Nino  "})
    assert r.status_code == 200, r.text
    body = r.json()
    try:
        assert body["scope"] == "user"
        assert set(body["user"]) == {"id", "email", "display_name"}
        assert body["user"]["email"] == email.strip().lower()
        assert body["user"]["display_name"] == "Nino"
        payload = auth.verify_token(body["token"])
        assert payload["kind"] == "user" and payload["user_id"] == body["user"]["id"]

        row = _user_row(body["user"]["id"])
        assert row["is_active"] is True
        assert row["last_login_at"] is not None          # a sign-up IS the first login
        assert row["password_hash"].startswith("pbkdf2_sha256$")
        assert "correct horse" not in row["password_hash"]

        # The token is accepted by the very next request.
        me = api.get("/auth/me", headers=_bearer(body["token"]))
        assert me.status_code == 200 and me.json()["user"]["id"] == body["user"]["id"]
    finally:
        sql(lambda c: c.execute("DELETE FROM app_users WHERE id = $1", uuid.UUID(body["user"]["id"])))


def test_register_refuses_a_duplicate_email_in_any_case(api, make_account):
    acct = make_account()
    r = api.post("/auth/register", json={"email": acct["email"].upper(), "password": "another-one"})
    assert r.status_code == 409, r.text
    assert "already exists" in r.json()["detail"]
    assert sql(lambda c: c.fetchval("SELECT count(*) FROM app_users WHERE lower(email) = $1",
                                    acct["email"])) == 1


@pytest.mark.parametrize("body, status", [
    ({"email": "not-an-email", "password": "long-enough"}, 400),
    ({"email": "two words@example.test", "password": "long-enough"}, 400),
    ({"email": "a@" + "b" * 200 + ".test", "password": "long-enough"}, 400),
    ({"email": "ok@example.test", "password": "short"}, 400),
    ({"email": "ok@example.test"}, 422),
    ({"password": "long-enough"}, 422),
])
def test_register_validates_its_input(api, registered, body, status):
    r = api.post("/auth/register", json=body)
    assert r.status_code == status, r.text
    if "email" in body:
        assert sql(lambda c: c.fetchval("SELECT count(*) FROM app_users WHERE lower(email) = lower($1)",
                                        body["email"])) == 0


def test_closed_signups_refuse_new_accounts_but_not_existing_ones(api, registered, make_account):
    acct = make_account()
    registered(enabled=False)
    r = api.post("/auth/register", json={"email": _email(), "password": "long-enough"})
    assert r.status_code == 403 and r.json()["detail"] == "Registration is closed"
    # `enabled` closes the door for NEW sign-ups only (§11): the existing account logs in and
    # its session keeps working.
    login = api.post("/auth/login", json={"username": acct["email"], "password": acct["password"]})
    assert login.status_code == 200 and login.json()["scope"] == "user"
    assert api.get("/auth/me", headers=acct["headers"]).status_code == 200


def test_signups_are_metered_per_ip(api, registered):
    """The one thing between an open sign-up form and an unlimited-quota dispenser.

    Every account minted here comes with the registered tier's whole daily allowance
    (analyses, TTS, conversions) plus the analysers the anonymous kind is refused outright,
    and there is no verification step to slow a loop of throwaway addresses down — so the
    counter is the control. Per IP, on `usage_counters`, like every other meter here.
    """
    registered(max_registrations_per_day=2)
    ip = f"authtest-{uuid.uuid4().hex[:10]}"
    elsewhere = f"authtest-{uuid.uuid4().hex[:10]}"
    made, refused_email = [], _email()

    def _signup(source: str, email: str):
        return api.post("/auth/register", json={"email": email, "password": "long-enough"},
                        headers={"X-Real-IP": source})
    try:
        for _ in range(2):
            r = _signup(ip, _email())
            assert r.status_code == 200, r.text
            made.append(r.json()["user"]["id"])

        r = _signup(ip, refused_email)
        assert r.status_code == 429, r.text
        assert "registrations" in r.json()["detail"]
        # Nothing was created by the refusal — the meter runs before the insert.
        assert sql(lambda c: c.fetchval(
            "SELECT count(*) FROM app_users WHERE lower(email) = $1", refused_email)) == 0

        # Per IP, not global: another visitor's first sign-up is unaffected.
        r = _signup(elsewhere, _email())
        assert r.status_code == 200, r.text
        made.append(r.json()["user"]["id"])
    finally:
        ids = [uuid.UUID(i) for i in made]
        sql(lambda c: c.execute("DELETE FROM app_users WHERE id = ANY($1::uuid[])", ids))
        sql(lambda c: c.execute(
            "DELETE FROM usage_counters WHERE scope_key = ANY($1::text[])",
            [f"anon:{ip}", f"anon:{elsewhere}"]))


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------
def test_login_by_email_in_any_case_and_stamps_last_login(api, make_account):
    acct = make_account()
    sql(lambda c: c.execute("UPDATE app_users SET last_login_at = '2000-01-01' WHERE id = $1",
                            uuid.UUID(acct["id"])))
    r = api.post("/auth/login", json={"username": acct["email"].upper(), "password": acct["password"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "user"
    assert body["user"] == {"id": acct["id"], "email": acct["email"], "display_name": "Auth Tester"}
    assert auth.verify_token(body["token"])["user_id"] == acct["id"]
    assert _user_row(acct["id"])["last_login_at"].year > 2000


def test_login_failures_share_one_generic_401(api, make_account):
    """Wrong password, unknown email, disabled account: the caller must not be able to tell
    them apart, or the login form becomes an account-enumeration oracle."""
    acct = make_account()
    wrong = api.post("/auth/login", json={"username": acct["email"], "password": "not-it-at-all"})
    unknown = api.post("/auth/login", json={"username": _email(), "password": acct["password"]})
    sql(lambda c: c.execute("UPDATE app_users SET is_active = false WHERE id = $1", uuid.UUID(acct["id"])))
    disabled = api.post("/auth/login", json={"username": acct["email"], "password": acct["password"]})
    for r in (wrong, unknown, disabled):
        assert r.status_code == 401, r.text
        assert r.json()["detail"] == GENERIC_401


def test_a_tenant_username_equal_to_an_email_does_not_lock_that_account_out(api, make_account):
    """The two identifier spaces overlap: `tenant_users.username` is free-form text a
    superadmin types, and email-as-username is an ordinary convention.

    A WORKING tenant credential still wins — a registered account can never shadow a workspace
    one — but a tenant row whose password does not verify must fall through to app_users
    instead of ending the request, or the registered owner of that address is locked out for
    good: even the superadmin's password reset cannot reach them, because this branch answers
    first and tenant usernames are immutable.
    """
    acct = make_account()
    suffix = uuid.uuid4().hex[:8]
    tenant_password = f"tenant-pw-{suffix}"
    cid = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name) VALUES ($1, $2) RETURNING id",
        f"authtest-{suffix}", "login collision test"))
    try:
        sql(lambda c: c.execute(
            "INSERT INTO tenant_users (client_id, username, password_hash, role) "
            "VALUES ($1, $2, $3, 'owner')", cid, acct["email"],
            auth.hash_password(tenant_password)))

        r = api.post("/auth/login", json={"username": acct["email"], "password": acct["password"]})
        assert r.status_code == 200, r.text
        assert r.json()["scope"] == "user" and r.json()["user"]["id"] == acct["id"]

        r = api.post("/auth/login", json={"username": acct["email"], "password": tenant_password})
        assert r.status_code == 200, r.text
        assert r.json()["scope"] == "tenant" and r.json()["client"]["id"] == str(cid)

        r = api.post("/auth/login", json={"username": acct["email"], "password": "neither-of-these"})
        assert r.status_code == 401 and r.json()["detail"] == GENERIC_401
    finally:
        sql(lambda c: c.execute("DELETE FROM clients WHERE id = $1", cid))


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------
def test_me_describes_a_registered_account(api, make_account):
    acct = make_account(display_name="Me Tester")
    r = api.get("/auth/me", headers=acct["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "user" and body["role"] == "user" and body["via"] == "token"
    assert body["user"] == {"id": acct["id"], "email": acct["email"], "display_name": "Me Tester"}
    assert "client_id" not in body           # a user has no tenant, not even a null one


def test_me_stops_working_when_the_account_is_disabled_or_deleted(api, make_account):
    """Tokens are stateless; this is where a deactivation catches up with a live session."""
    acct = make_account()
    uid = uuid.UUID(acct["id"])
    sql(lambda c: c.execute("UPDATE app_users SET is_active = false WHERE id = $1", uid))
    r = api.get("/auth/me", headers=acct["headers"])
    assert r.status_code == 403 and r.json()["detail"] == "Account disabled"
    sql(lambda c: c.execute("DELETE FROM app_users WHERE id = $1", uid))
    r = api.get("/auth/me", headers=acct["headers"])
    assert r.status_code == 401 and r.json()["detail"] == "Account not found"


@pytest.mark.parametrize("token", [
    "garbage",
    auth.make_token({"kind": "user"}),                        # malformed: no user_id
    auth.make_user_token(uuid.UUID(int=0)),                   # well-formed, no such account
])
def test_bad_user_bearers_are_hard_401s_never_a_tenant(api, registered, token):
    """A user-shaped token must never fall through to the tenant branch of the resolver: a
    signed `{"kind": "user"}` with no user_id used to become a client_id-less tenant."""
    r = api.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 401, r.text
    assert r.json()["detail"] in ("Invalid or expired session token", "Account not found")


# ---------------------------------------------------------------------------
# PUT /auth/me
# ---------------------------------------------------------------------------
def test_rename_trims_and_empty_clears(api, make_account):
    acct = make_account()
    r = api.put("/auth/me", json={"display_name": "  Renamed  "}, headers=acct["headers"])
    assert r.status_code == 200 and r.json()["user"]["display_name"] == "Renamed"
    assert _user_row(acct["id"])["display_name"] == "Renamed"
    r = api.put("/auth/me", json={"display_name": ""}, headers=acct["headers"])
    assert r.status_code == 200 and r.json()["user"]["display_name"] is None
    assert api.get("/auth/me", headers=acct["headers"]).json()["user"]["display_name"] is None


def test_password_change_needs_the_current_password(api, make_account):
    acct = make_account()
    h = acct["headers"]
    assert api.put("/auth/me", json={"new_password": "brand-new-pw"}, headers=h).status_code == 403
    assert api.put("/auth/me", json={"new_password": "brand-new-pw", "current_password": "wrong"},
                   headers=h).status_code == 403
    assert api.put("/auth/me", json={"new_password": "short", "current_password": acct["password"]},
                   headers=h).status_code == 400
    # None of the refusals changed the hash.
    assert api.post("/auth/login", json={"username": acct["email"],
                                         "password": acct["password"]}).status_code == 200

    r = api.put("/auth/me", json={"new_password": "brand-new-pw", "current_password": acct["password"]},
                headers=h)
    assert r.status_code == 200, r.text
    assert api.post("/auth/login", json={"username": acct["email"],
                                         "password": acct["password"]}).status_code == 401
    assert api.post("/auth/login", json={"username": acct["email"],
                                         "password": "brand-new-pw"}).status_code == 200


def test_profile_edits_are_for_registered_users_only(api, make_account):
    acct = make_account()
    assert api.put("/auth/me", json={}, headers=acct["headers"]).status_code == 400
    assert api.put("/auth/me", json={"display_name": "x"}).status_code == 403          # anonymous
    assert api.put("/auth/me", json={"display_name": "x"}, headers=ADMIN).status_code == 403


# ---------------------------------------------------------------------------
# Operator: reset password, delete
# ---------------------------------------------------------------------------
def test_admin_reset_password_is_the_only_recovery_path(api, make_account):
    acct = make_account()
    r = api.post(f"/admin/users/{acct['id']}/reset-password", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == acct["id"] and body["email"] == acct["email"]
    assert isinstance(body["password"], str) and len(body["password"]) == 12
    assert body["password"] != acct["password"]
    # Shown once: only the hash is stored.
    assert body["password"] not in _user_row(acct["id"])["password_hash"]
    assert api.post("/auth/login", json={"username": acct["email"],
                                         "password": acct["password"]}).status_code == 401
    assert api.post("/auth/login", json={"username": acct["email"],
                                         "password": body["password"]}).status_code == 200


def test_admin_reset_password_refusals(api, make_account):
    acct = make_account()
    assert api.post(f"/admin/users/{acct['id']}/reset-password").status_code == 401
    assert api.post(f"/admin/users/{uuid.UUID(int=0)}/reset-password", headers=ADMIN).status_code == 404
    assert api.post("/admin/users/not-a-uuid/reset-password", headers=ADMIN).status_code == 400


def test_admin_delete_removes_the_account_row_only(api, make_account):
    acct = make_account()
    r = api.delete(f"/admin/users/{acct['id']}", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert "retention" in r.json().get("note", "").lower()
    assert _user_row(acct["id"]) is None
    assert api.get("/auth/me", headers=acct["headers"]).status_code == 401
    assert api.delete(f"/admin/users/{acct['id']}", headers=ADMIN).status_code == 404
