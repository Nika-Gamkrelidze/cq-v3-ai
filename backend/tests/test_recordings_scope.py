"""Ownership on the Call Workbench's HTTP surface (design-v2 §8): who may create a recording,
who may read it, who may run which analyser on it — and above all that a registered user's
rows are invisible to every tenant, to every other user and to the anonymous visitor, with
the `principal_type` discriminator doing its job.

The recordings are pasted transcripts (`POST /recordings/text`): they need no ElevenLabs
key, no file and no model, and they are the row shape every scope rule applies to. No
analyser ever reaches Claude here — the fact-check and score calls all end at a scope, kind
or knowledge-base refusal, and conftest's `no_llm` detonator stands behind that claim.

Principals: two registered users (created through the real sign-up route), one throwaway
tenant reached both by API key and by an owner's login (a tenant login ALSO carries a
`user_id` — its `tenant_users` row — which is exactly why the user scope must key on the
discriminator too), the anonymous visitor, the superadmin and conftest's chat integration
credential. Every row is deleted afterwards; the registered blob is pinned and restored.
"""
import json
import uuid

import pytest

from app.config import settings
from app.services import auth
from conftest import sql  # loop-independent SQL; see its module docstring

REGISTERED_KEY = "registered"
SIGNUP_SCOPE = "anon:testclient"   # what services.auth.client_ip sees for a TestClient call
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
ADMIN = {"X-Admin-Token": settings.admin_token}
ZERO = str(uuid.UUID(int=0))
TRANSCRIPT = ("Agent: Good morning, thank you for calling Northwind Bank.\n"
              "Customer: Hi, I want to ask about the wire fee.\n"
              "Agent: It is 25 lari for domestic transfers.")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _anon() -> dict:
    # A fresh, private quota key: nothing is written for anonymous reads, and the 401 on the
    # anonymous paste happens before any counter is touched.
    return {"X-Real-IP": f"rectest-{uuid.uuid4().hex[:10]}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def registered(api):
    """Pin `app_settings.registered` so sign-ups are open and pastes are not capped;
    restore what was there (or its absence) afterwards."""
    prev = sql(lambda c: c.fetchval("SELECT value FROM app_settings WHERE key = $1", REGISTERED_KEY))
    sql(lambda c: c.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        REGISTERED_KEY, json.dumps(REGISTERED_PIN)))
    yield
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
def users(api, registered):
    """Two registered accounts, A and B, through the real sign-up route. Deleted with
    everything they wrote at teardown."""
    made = {}
    for label in ("a", "b"):
        email = f"cq-rectest-{label}-{uuid.uuid4().hex[:8]}@example.test"
        r = api.post("/auth/register", json={"email": email, "password": "long-enough-pw"})
        assert r.status_code == 200, r.text
        body = r.json()
        made[label] = {"id": body["user"]["id"], "headers": _bearer(body["token"])}
    yield made
    ids = [uuid.UUID(u["id"]) for u in made.values()]

    async def _cleanup(conn):
        await conn.execute("DELETE FROM audio_jobs WHERE user_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM call_summaries WHERE user_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM scoring_configs WHERE user_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM usage_counters WHERE scope_key = ANY($1::text[])",
                           [f"user:{i}" for i in ids])
        await conn.execute("DELETE FROM app_users WHERE id = ANY($1::uuid[])", ids)
    sql(_cleanup)


@pytest.fixture
def tenant(api):
    """A throwaway tenant with an API key AND an owner login, so both tenant credentials are
    exercised. The `clients` DELETE cascades tenant_users and call_summaries; audio_jobs
    only SET NULLs its client_id, so those rows are deleted first."""
    suffix = uuid.uuid4().hex[:8]
    password = f"pw-{suffix}"

    async def _setup(conn):
        cid = await conn.fetchval(
            "INSERT INTO clients (slug, name, api_key) VALUES ($1, $2, $3) RETURNING id",
            f"rectest-{suffix}", "recordings scope test", f"rectest-key-{suffix}")
        uid = await conn.fetchval(
            "INSERT INTO tenant_users (client_id, username, password_hash, role) "
            "VALUES ($1, $2, $3, 'owner') RETURNING id",
            cid, f"rectest-owner-{suffix}", auth.hash_password(password))
        return cid, uid
    cid, owner_id = sql(_setup)

    login = api.post("/auth/login", json={"username": f"rectest-owner-{suffix}", "password": password})
    assert login.status_code == 200 and login.json()["scope"] == "tenant", login.text
    yield {"id": str(cid), "owner_id": str(owner_id), "apikey": {"X-API-Key": f"rectest-key-{suffix}"},
           "bearer": _bearer(login.json()["token"])}

    async def _cleanup(conn):
        await conn.execute("DELETE FROM audio_jobs WHERE client_id = $1", cid)
        await conn.execute("DELETE FROM usage_counters WHERE scope_key = $1", f"tenant:{cid}")
        await conn.execute("DELETE FROM clients WHERE id = $1", cid)
    sql(_cleanup)


def _paste(api, headers: dict, text: str = TRANSCRIPT):
    return api.post("/recordings/text", json={"text": text}, headers=headers)


def _row(job_id: str) -> dict:
    return dict(sql(lambda c: c.fetchrow(
        "SELECT principal_type, client_id, user_id, anon_key, source, status, audio_path, "
        "purge_after, transcript FROM audio_jobs WHERE id = $1", uuid.UUID(job_id))))


def _ids(response) -> set:
    assert response.status_code == 200, response.text
    return {r["id"] for r in response.json()}


# ---------------------------------------------------------------------------
# Creating a recording: shape and ownership
# ---------------------------------------------------------------------------
def test_user_paste_creates_a_user_owned_recording(api, users):
    a = users["a"]
    r = _paste(api, a["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"id", "filename", "language", "duration_s", "transcript", "segments",
                         "audio_url", "status"}
    assert body["status"] == "ready" and body["audio_url"] is None
    assert body["filename"] == "transcript.txt"
    assert body["language"] is None and body["duration_s"] is None
    assert body["transcript"] == TRANSCRIPT
    assert [s["speaker"] for s in body["segments"]] == ["agent", "customer", "agent"]
    assert [s["i"] for s in body["segments"]] == [0, 1, 2]
    assert all(s["start"] is None and s["end"] is None for s in body["segments"])

    row = _row(body["id"])
    assert row["principal_type"] == "user" and row["user_id"] == uuid.UUID(a["id"])
    assert row["client_id"] is None and row["anon_key"] is None
    assert row["source"] == "text" and row["status"] == "ready"
    assert row["audio_path"] is None and row["purge_after"] is None


def test_anonymous_superadmin_and_integration_cannot_paste(api, registered, seed):
    r = _paste(api, _anon())
    assert r.status_code == 401 and r.json()["detail"] == "Sign in to paste a transcript."
    r = _paste(api, ADMIN)
    assert r.status_code == 403 and "cannot paste" in r.json()["detail"]
    integ = {"X-CQ-Key": seed["integration"]["api_key"], "X-CQ-Tenant": seed["a"]["slug"]}
    r = _paste(api, integ)
    assert r.status_code == 403, r.text
    # ...and an integration credential cannot read recordings either.
    r = api.get("/recordings", headers=integ)
    assert r.status_code == 403 and "cannot read recordings" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Reading: a user's recording is nobody else's
# ---------------------------------------------------------------------------
def test_users_recording_is_invisible_to_tenants_other_users_and_anonymous(api, users, tenant):
    a, b = users["a"], users["b"]
    rec = _paste(api, a["headers"]).json()["id"]

    for who, headers in (("tenant api key", tenant["apikey"]), ("tenant login", tenant["bearer"]),
                         ("user B", b["headers"])):
        r = api.get(f"/recordings/{rec}", headers=headers)
        assert r.status_code == 404, f"{who}: {r.status_code} {r.text}"
        assert r.json()["detail"] == "Recording not found"
        assert rec not in _ids(api.get("/recordings", headers=headers)), who
        r = api.get(f"/recordings/{rec}/audio", headers=headers)
        assert r.status_code == 404 and r.json()["detail"] == "Recording not found", who

    # Anonymous is refused outright rather than matched on its IP — see the next test.
    for path in (f"/recordings/{rec}", "/recordings", f"/recordings/{rec}/audio"):
        r = api.get(path, headers=_anon())
        assert r.status_code == 401, f"{path}: {r.status_code} {r.text}"

    r = api.get(f"/recordings/{rec}", headers=a["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == rec and body["source"] == "text" and body["has_audio"] is False
    assert body["audio_url"] is None and body["transcript"] == TRANSCRIPT
    assert body["kb_check"] is None and body["scoring"] is None and body["semantic"] is None
    assert rec in _ids(api.get("/recordings", headers=a["headers"]))
    listed = [x for x in api.get("/recordings", headers=a["headers"]).json() if x["id"] == rec][0]
    assert listed["has_audio"] is False and listed["source"] == "text"
    assert listed["ran"] == {"factcheck": False, "score": False, "semantic": False}
    # The owner's own audio link answers with the purge wording, not "not found".
    r = api.get(f"/recordings/{rec}/audio", headers=a["headers"])
    assert r.status_code == 404 and r.json()["detail"] == "Audio no longer stored"
    # The operator's scope is TRUE, mirroring analyze.py::_scope.
    assert api.get(f"/recordings/{rec}", headers=ADMIN).status_code == 200


def test_tenant_recording_is_invisible_to_users(api, users, tenant):
    rec = _paste(api, tenant["apikey"]).json()["id"]
    row = _row(rec)
    assert row["principal_type"] == "tenant" and row["client_id"] == uuid.UUID(tenant["id"])
    assert row["user_id"] is None
    for headers in (users["a"]["headers"], users["b"]["headers"]):
        assert api.get(f"/recordings/{rec}", headers=headers).status_code == 404
        assert rec not in _ids(api.get("/recordings", headers=headers))
    assert api.get(f"/recordings/{rec}", headers=_anon()).status_code == 401
    for headers in (tenant["apikey"], tenant["bearer"]):
        assert api.get(f"/recordings/{rec}", headers=headers).status_code == 200
        assert rec in _ids(api.get("/recordings", headers=headers))


def test_anonymous_cannot_read_recordings_even_from_its_own_ip(api):
    """The regression this router's `_scope` exists to prevent: `anon_key` is the client IP, so
    an IP-keyed read branch hands everyone behind one NAT (office, CGNAT, café) each other's
    recordings — id, transcript and, since audio is stored for every kind now, the original
    call itself. The row here is inserted directly because creating one needs an STT key; what
    matters is that the anonymous caller presenting the SAME IP is still refused."""
    ip = f"rectest-{uuid.uuid4().hex[:10]}"
    rec = str(sql(lambda c: c.fetchval(
        "INSERT INTO audio_jobs (filename, status, source, principal_type, anon_key, transcript, "
        "audio_path) VALUES ('private.wav', 'ready', 'audio', 'anonymous', $1, 'secret', "
        "'anon/private.wav') RETURNING id", ip)))
    try:
        same_ip = {"X-Real-IP": ip}
        for path in ("/recordings", f"/recordings/{rec}", f"/recordings/{rec}/audio"):
            r = api.get(path, headers=same_ip)
            assert r.status_code == 401, f"{path}: {r.status_code} {r.text}"
            assert r.json()["detail"] == "Sign in to see your recordings."
        # The operator can still see it; that is what `_owner_predicate`'s TRUE branch is for.
        assert api.get(f"/recordings/{rec}", headers=ADMIN).status_code == 200
    finally:
        sql(lambda c: c.execute("DELETE FROM audio_jobs WHERE id = $1", uuid.UUID(rec)))


def test_user_scope_keys_on_the_principal_type_discriminator(api, users, tenant):
    """A tenant-owned row that (wrongly) carries user A's id must still not be A's: the user
    predicate is `user_id = $n AND principal_type = 'user'`, on both the workbench routes and
    the legacy /jobs ones (`analyze.py::_scope`)."""
    a = users["a"]
    own = _paste(api, a["headers"]).json()["id"]
    stray = str(sql(lambda c: c.fetchval(
        "INSERT INTO audio_jobs (filename, status, source, principal_type, client_id, user_id, transcript) "
        "VALUES ('stray.txt', 'ready', 'text', 'tenant', $1, $2, 'stray') RETURNING id",
        uuid.UUID(tenant["id"]), uuid.UUID(a["id"]))))

    assert api.get(f"/recordings/{stray}", headers=a["headers"]).status_code == 404
    assert api.get(f"/recordings/{stray}", headers=tenant["apikey"]).status_code == 200
    listed = _ids(api.get("/recordings", headers=a["headers"]))
    assert own in listed and stray not in listed

    # Legacy /jobs sees the same policy for users.
    jobs = _ids(api.get("/jobs", headers=a["headers"]))
    assert own in jobs and stray not in jobs
    assert api.get(f"/jobs/{own}", headers=a["headers"]).status_code == 200
    assert api.get(f"/jobs/{stray}", headers=a["headers"]).status_code == 404
    assert api.get(f"/jobs/{own}", headers=tenant["apikey"]).status_code == 404
    assert api.get(f"/jobs/{own}", headers=users["b"]["headers"]).status_code == 404


# ---------------------------------------------------------------------------
# Analysers: kind, scope and the knowledge-base gate — never a model call
# ---------------------------------------------------------------------------
def test_factcheck_is_403_for_users_and_404_for_other_owners(api, users, tenant):
    a = users["a"]
    rec = _paste(api, a["headers"]).json()["id"]
    r = api.post(f"/recordings/{rec}/factcheck", headers=a["headers"])
    assert r.status_code == 403, r.text
    assert "knowledge base" in r.json()["detail"]
    # A user is refused by kind before the recording is even looked up.
    r = api.post(f"/recordings/{ZERO}/factcheck", headers=users["b"]["headers"])
    assert r.status_code == 403 and "knowledge base" in r.json()["detail"]
    for headers in (tenant["apikey"], tenant["bearer"]):
        r = api.post(f"/recordings/{rec}/factcheck", headers=headers)
        assert r.status_code == 404 and r.json()["detail"] == "Recording not found"
    r = api.post(f"/recordings/{rec}/factcheck", headers=_anon())
    assert r.status_code == 401 and r.json()["detail"].startswith("Sign in")
    assert api.get(f"/recordings/{rec}", headers=a["headers"]).json()["kb_check"] is None


def test_tenant_without_a_knowledge_base_gets_409_not_a_model_call(api, tenant):
    rec = _paste(api, tenant["apikey"]).json()["id"]
    r = api.post(f"/recordings/{rec}/factcheck", headers=tenant["bearer"])
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "No knowledge base yet"
    assert api.get(f"/recordings/{rec}", headers=tenant["apikey"]).json()["kb_check"] is None


def test_score_and_semantic_refuse_by_scope_and_kind(api, users, tenant):
    a, b = users["a"], users["b"]
    rec = _paste(api, a["headers"]).json()["id"]
    for headers in (b["headers"], tenant["apikey"], tenant["bearer"]):
        assert api.post(f"/recordings/{rec}/score", headers=headers).status_code == 404
        assert api.post(f"/recordings/{rec}/semantic", json={"modes": ["text"]},
                        headers=headers).status_code == 404
    r = api.post(f"/recordings/{rec}/score", headers=_anon())
    assert r.status_code == 401 and r.json()["detail"].startswith("Sign in")
    r = api.post(f"/recordings/{rec}/semantic", json={"modes": ["text"]}, headers=_anon())
    assert r.status_code == 401
    # The operator can read everything but runs nothing: analysers are for owners.
    assert api.post(f"/recordings/{rec}/score", headers=ADMIN).status_code == 403
    assert api.post(f"/recordings/{rec}/semantic", json={"modes": ["text"]}, headers=ADMIN).status_code == 403
    assert api.get(f"/recordings/{rec}", headers=a["headers"]).json()["scoring"] is None


def test_malformed_ids_are_400_for_every_caller(api, users, tenant):
    for headers in (users["a"]["headers"], tenant["apikey"], ADMIN):
        assert api.get("/recordings/not-a-uuid", headers=headers).status_code == 400
        assert api.post("/recordings/not-a-uuid/score", headers=headers).status_code in (400, 403)
    assert api.get(f"/recordings/{ZERO}", headers=users["a"]["headers"]).status_code == 404


# ---------------------------------------------------------------------------
# Summaries: same policy, no anonymous rows
# ---------------------------------------------------------------------------
def test_summaries_are_scoped_like_recordings(api, users, tenant):
    a, b = users["a"], users["b"]
    r = api.get("/summaries", headers=_anon())
    assert r.status_code == 401 and r.json()["detail"] == "Sign in to see your summaries."
    assert api.get("/summaries", headers=a["headers"]).json() == []

    rec = _paste(api, a["headers"]).json()["id"]
    digest = {"language": "en", "short_summary": "One call about a wire fee.",
              "key_points": ["fee is 25 lari"], "action_items": [], "participants": [],
              "calls": [{"index": 0, "job_id": rec, "filename": "transcript.txt",
                         "title": "Wire fee", "summary": "s", "outcome": "answered"}],
              "stages": 1}
    summary_id = str(sql(lambda c: c.fetchval(
        "INSERT INTO call_summaries (principal_type, client_id, user_id, job_ids, language, summary) "
        "VALUES ('user', NULL, $1, $2::uuid[], 'en', $3::jsonb) RETURNING id",
        uuid.UUID(a["id"]), [uuid.UUID(rec)], json.dumps(digest))))

    listed = api.get("/summaries", headers=a["headers"]).json()
    assert [s["id"] for s in listed] == [summary_id]
    assert listed[0]["call_count"] == 1 and listed[0]["short_summary"] == digest["short_summary"]
    assert listed[0]["calls"] == [{"job_id": rec, "filename": "transcript.txt", "title": "Wire fee"}]

    r = api.get(f"/summaries/{summary_id}", headers=a["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == summary_id and body["summary"]["short_summary"] == digest["short_summary"]
    assert [c["job_id"] for c in body["calls"]] == [rec]
    assert body["calls"][0]["transcript"] == TRANSCRIPT and body["calls"][0]["audio_url"] is None

    for headers in (b["headers"], tenant["apikey"], tenant["bearer"]):
        assert api.get(f"/summaries/{summary_id}", headers=headers).status_code == 404
        assert api.get("/summaries", headers=headers).json() == []
    assert api.get(f"/summaries/{summary_id}", headers=_anon()).status_code == 401
    assert api.get(f"/summaries/{ZERO}", headers=a["headers"]).status_code == 404
    assert api.get("/summaries/not-a-uuid", headers=a["headers"]).status_code == 400
    assert api.get(f"/summaries/{summary_id}", headers=ADMIN).status_code == 200
