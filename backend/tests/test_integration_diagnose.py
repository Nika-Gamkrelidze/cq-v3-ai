"""`chat_credentials` — naming the reason for a 401 without ever weakening the 401.

Why this deserves a file: on 2026-09-10 the Swift Chat pilot could not authenticate, and the
only evidence anywhere was `401 "Invalid integration credential or tenant selector."` — one
refusal standing for nine independent conditions (bad shape, unknown key_id, revoked, expired,
inactive integration, unknown tenant, inactive tenant, no grant, wrong secret). That opacity is
correct *toward the caller* and is not what changed. What changed is that the operator, who
already holds the superadmin token, can now get the reason by name.

So the two things worth pinning are (a) the reason literals stay a closed, unique set that the
log line and the diagnose endpoint agree on, and (b) nothing on the operator path can put a
secret into something that gets written down. Both are pure-function properties: no Postgres, no
network, no model — `_report` takes rows in and the report out, and takes the ALREADY-COMPARED
verdict rather than the secret, which is precisely the design this file exists to hold in place.
"""
import hashlib
import json

import pytest

from app.services import chat_credentials as cc

# The value that must never appear in anything the operator path produces.
SECRET = "s3cret-never-write-me-down-Zx9"
KEY_ID = "a1b2c3d4e5f60718"
CLIENT_ID = "ab3b2bab-5329-42e1-8e21-2a8112838c12"
INTEGRATION_ID = "22222222-2222-4222-8222-222222222222"


def facts(**over) -> dict:
    """The rows `_facts()` reads, in the shape it returns them — all nine conditions holding."""
    base = {
        "secret": {
            "secret_hash": hashlib.sha256(SECRET.encode()).hexdigest(),
            "is_revoked": False,
            "is_expired": False,
            "integration_id": INTEGRATION_ID,
            "integration_name": "swift chat",
            "integration_active": True,
            "integration_scopes": ["chat:turn", "chat:suggest", "chat:answer", "chat:sync"],
        },
        "tenant": {"id": CLIENT_ID, "slug": "demo", "name": "Demo", "is_active": True},
        "grant": {"is_active": True, "scopes": []},
        "granted_tenants": [
            {"client_id": CLIENT_ID, "slug": "demo", "name": "Demo", "is_active": True},
        ],
    }
    base.update(over)
    return base


def verdict(secret_ok=True, **over) -> str:
    return cc._report(KEY_ID, "demo", secret_ok, facts(**over))["verdict"]


# --------------------------------------------------------------------------- #
# 1. The operator's input: a key_id, a prefixed key_id, or the whole key
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    (KEY_ID, (KEY_ID, None)),                                   # bare key_id
    (f"cqi_{KEY_ID}", (KEY_ID, None)),                          # prefixed, no secret
    (f"cqi_{KEY_ID}.{SECRET}", (KEY_ID, SECRET)),               # the whole thing, as issued
    (f"  cqi_{KEY_ID}.{SECRET}  ", (KEY_ID, SECRET)),           # pasted with whitespace
    ("", ("", None)),
    ("   ", ("", None)),
    ("nonsense", ("nonsense", None)),                           # -> unknown_key_id, not a crash
    ("cqi_.abc", ("", "abc")),                                  # no key_id half at all
    (f"cqi_{KEY_ID}.", (KEY_ID, None)),                         # trailing dot, empty secret
])
def test_split_accepts_every_form_an_operator_might_paste(raw, expected):
    assert cc.split_diagnosed_key(raw) == expected


def test_split_takes_the_FIRST_dot_so_a_mangled_secret_never_corrupts_the_key_id():
    """A secret is urlsafe base64 and holds no dot, but a mangled paste can. Splitting at the
    first dot keeps the lookup handle right and lets the hash comparison be the thing that
    fails — which is the report the operator needs."""
    key_id, secret = cc.split_diagnosed_key(f"cqi_{KEY_ID}.aaa.bbb")
    assert key_id == KEY_ID
    assert secret == "aaa.bbb"


# --------------------------------------------------------------------------- #
# 2. The reason literals are a closed, unique set
# --------------------------------------------------------------------------- #
def test_reasons_are_exhaustive_and_unique():
    """One literal per condition in _RESOLVE_SQL's chain, plus the two pre-DB shape checks.
    A duplicate would make two different failures indistinguishable in the log — the very bug
    this file is here to prevent — and a missing one means a failure mode with no name."""
    assert cc.REASONS == (
        "bad_key_shape", "missing_tenant_selector", "unknown_key_id", "secret_revoked",
        "secret_expired", "integration_inactive", "tenant_not_found", "tenant_inactive",
        "no_grant", "grant_inactive", "secret_mismatch",
    )
    assert len(set(cc.REASONS)) == len(cc.REASONS) == 11
    assert cc._REASON_UNKNOWN not in cc.REASONS          # the "diagnosis itself failed" fallback


@pytest.mark.parametrize("over,expected", [
    ({"secret": None}, "unknown_key_id"),
    ({"secret": {"is_revoked": True}}, "secret_revoked"),
    ({"secret": {"is_expired": True}}, "secret_expired"),
    ({"secret": {"integration_active": False}}, "integration_inactive"),
    ({"tenant": None}, "tenant_not_found"),
    ({"tenant": {"id": CLIENT_ID, "slug": "demo", "is_active": False}}, "tenant_inactive"),
    ({"grant": None}, "no_grant"),
    ({"grant": {"is_active": False}}, "grant_inactive"),
])
def test_every_condition_gets_its_own_name(over, expected):
    # Merge onto the all-good fixture so exactly ONE condition is broken per case.
    if over.get("secret") or over.get("tenant"):
        key = "secret" if "secret" in over else "tenant"
        merged = dict(facts()[key])
        merged.update(over[key])
        over = {key: merged}
    assert verdict(**over) == expected
    assert verdict(**over) in cc.REASONS


def test_the_first_failure_in_the_chain_is_the_one_reported():
    """Two things wrong at once must report the earlier one, matching the order the joins in
    _RESOLVE_SQL would have dropped the row — otherwise the operator fixes the second problem
    and gets the same 401."""
    assert verdict(secret=None, tenant=None) == "unknown_key_id"
    assert verdict(tenant=None, grant=None) == "tenant_not_found"


def test_a_structurally_perfect_pair_says_so_and_distinguishes_the_secret_check():
    assert verdict(secret_ok=True) == "ok"
    assert verdict(secret_ok=False) == "secret_mismatch"      # the realistic pilot cause
    # Only a key_id was pasted: everything checkable passes, and the report must not overclaim.
    report = cc._report(KEY_ID, "demo", None, facts())
    assert report["verdict"] == "ok_structurally"
    assert report["secret_checked"] is False
    matches = [c for c in report["checks"] if c["step"] == "secret_matches"][0]
    assert matches["ok"] is None and "no secret" in matches["detail"]


# --------------------------------------------------------------------------- #
# 3. Nothing on this path writes a secret down
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("secret_ok", [True, False, None])
def test_the_report_never_carries_the_secret_or_its_hash(secret_ok):
    """`_report` is handed the comparison's RESULT, not the secret — this is the assertion that
    keeps it that way. Serialize the whole body (that is what the endpoint returns) and grep."""
    body = json.dumps(cc._report(KEY_ID, "demo", secret_ok, facts()))

    assert SECRET not in body
    assert hashlib.sha256(SECRET.encode()).hexdigest() not in body
    assert "secret_hash" not in body
    assert KEY_ID in body                                   # the public half is the whole point


def test_the_refusal_log_line_carries_the_reason_and_no_credential(caplog):
    with caplog.at_level("WARNING", logger="cq"):
        cc._log_refusal(KEY_ID, "no_grant", CLIENT_ID)

    line = caplog.text
    assert "no_grant" in line and KEY_ID in line and CLIENT_ID in line
    assert SECRET not in line


def test_an_overlong_selector_is_truncated_before_it_reaches_the_log(caplog):
    """The selector is operator-supplied config and safe to log, but it arrives from the network
    and a megabyte of it must not become a log line."""
    with caplog.at_level("WARNING", logger="cq"):
        cc._log_refusal(KEY_ID, "tenant_not_found", "x" * 5000)

    assert "x" * 64 in caplog.text
    assert "x" * 65 not in caplog.text


# --------------------------------------------------------------------------- #
# 4. The field the operator actually came for
# --------------------------------------------------------------------------- #
def test_granted_tenants_shows_the_workspace_list_when_the_typed_one_is_missing():
    """The 401's most common cause is a workspace that is not on the credential. Seeing the
    list beside the one that was typed answers it at a glance — no psql, no guessing."""
    other = {"client_id": "9f9f9f9f-0000-4000-8000-000000000001",
             "slug": "acme", "name": "Acme", "is_active": True}
    report = cc._report(KEY_ID, CLIENT_ID, False, facts(grant=None, granted_tenants=[other]))

    assert report["verdict"] == "no_grant"
    assert report["granted_tenants"] == [{"client_id": other["client_id"], "slug": "acme",
                                          "name": "Acme", "is_active": True}]
    assert report["tenant"] == CLIENT_ID                    # echoed exactly as the operator sent
    assert report["integration"] == {"id": INTEGRATION_ID, "name": "swift chat",
                                     "is_active": True,
                                     "scopes": ["chat:turn", "chat:suggest",
                                                "chat:answer", "chat:sync"]}


def test_effective_scopes_are_the_intersection_resolve_sql_computes():
    """An empty grant scope array means "everything the integration holds"; a non-empty one may
    only narrow. If these two disagree the operator is shown scopes the request will not get."""
    assert cc._report(KEY_ID, "demo", True, facts())["effective_scopes"] == [
        "chat:turn", "chat:suggest", "chat:answer", "chat:sync"]

    narrowed = cc._report(KEY_ID, "demo", True,
                          facts(grant={"is_active": True, "scopes": ["chat:answer", "kb:write"]}))
    assert narrowed["effective_scopes"] == ["chat:answer"]   # kb:write is not held, so dropped
    assert narrowed["verdict"] == "ok"


def test_every_check_step_is_present_and_reported_once():
    """The UI renders these as a fixed checklist, so a step that silently disappears when a row
    is missing would leave a hole in the page rather than a "not evaluated" row."""
    steps = ["key_id_known", "secret_live", "integration_active", "tenant_found",
             "tenant_active", "grant_exists", "grant_active", "secret_matches", "scopes"]
    for over in ({}, {"secret": None}, {"tenant": None}, {"grant": None}):
        report = cc._report(KEY_ID, "demo", None, facts(**over))
        assert [c["step"] for c in report["checks"]] == steps
        assert all(c["ok"] in (True, False, None) and c["detail"] for c in report["checks"])
