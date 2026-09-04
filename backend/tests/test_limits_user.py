"""The registered tier of `services/limits.py` (design-v2 §11): a self-service account is
counted per user, capped by the tier unless a per-user override names the key, switched off
per feature, and refused the moment the operator deactivates it.

The order of precedence is the whole point and is pinned from three directions:

  * pure `_user_cap`: override (even an explicit 0 = uncapped) > tier > 0; garbage ignored;
  * over HTTP, through the routes that actually spend the units — `POST /recordings/text`
    (an `analyses` unit), `POST /recordings` (the size cap), `POST /recordings/{id}/score`
    (`require_feature`) and `GET /limits` (the snapshot the account page renders);
  * directly against `reserve()`/`check()`/`snapshot()` for the kinds no keyless test can
    reach over HTTP (TTS needs ElevenLabs, conversions need ffmpeg), on a pool bound to the
    test's own loop — `test_autopilot._with_db` explains why.

No model is ever called: the only analyser exercised is monkeypatched at the module attribute
the router looks up, and conftest's `no_llm` detonator stands behind it. Every account is
created through the real sign-up route and deleted with everything it wrote; the registered
blob is pinned per test and restored exactly.
"""
import asyncio
import json
import uuid

import pytest
from fastapi import HTTPException

from app.routers import recordings
from app.services import elevenlabs, limits, media, scoring
from app.services.auth import Principal
from conftest import sql  # loop-independent SQL; see its module docstring

REGISTERED_KEY = "registered"
SIGNUP_SCOPE = "anon:testclient"   # what services.auth.client_ip sees for a TestClient call
# Low enough that two pastes hit the analyses cap and three direct reserves hit the TTS one.
REGISTERED_PIN = {
    "enabled": True, "max_analyses_per_day": 2, "max_tts_per_day": 3,
    "max_conversions_per_day": 4, "max_audio_mb": 50,
    # 0 = uncapped sign-ups (still counted): /auth/register is metered per IP, and every
    # account in this file comes from one client host, so the default cap would refuse
    # the eleventh test to ask for an account. The cap itself is tested on its own.
    "max_registrations_per_day": 0,
    "features": {"analyze": True, "tts": True, "convert": True,
                 "summarise": True, "score": True, "semantic": True},
}
FEATURE_OFF = "This feature is disabled for registered users."
DISABLED = "Account disabled"
TRANSCRIPT = "Agent: Good morning, thank you for calling.\nCustomer: Hi, I have a question."


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _with_db(coro_factory):
    """Run `coro_factory()` with an asyncpg pool bound to THIS loop, restoring the app's
    pool afterwards (it lives in the TestClient portal thread and cannot be awaited here)."""
    from app import db

    async def _run():
        prev = db._pool
        await db.connect()
        try:
            return await coro_factory()
        finally:
            await db.disconnect()
            db._pool = prev
    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def registered(api):
    """Pin `app_settings.registered`; hand back a setter; restore what was there (or its
    absence). `get_registered_config()` has no cache, so writes are seen at once."""
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
    """Registered accounts through the real sign-up route; deleted by id with everything
    they wrote (recordings, counters, rubrics, history) at teardown — idempotently."""
    made: list[str] = []

    def _make() -> dict:
        email = f"cq-limtest-{uuid.uuid4().hex[:10]}@example.test"
        r = api.post("/auth/register", json={"email": email, "password": "long-enough-pw"})
        assert r.status_code == 200, r.text
        body = r.json()
        made.append(body["user"]["id"])
        return {"id": body["user"]["id"], "email": email, "headers": _bearer(body["token"])}

    yield _make
    if made:
        ids = [uuid.UUID(i) for i in made]

        async def _cleanup(conn):
            await conn.execute("DELETE FROM audio_jobs WHERE user_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM call_summaries WHERE user_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM scoring_configs WHERE user_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM usage_counters WHERE scope_key = ANY($1::text[])",
                               [f"user:{i}" for i in made])
            await conn.execute("DELETE FROM app_users WHERE id = ANY($1::uuid[])", ids)
        sql(_cleanup)


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    """Stored uploads land in a temp dir, never on the real media volume."""
    root = tmp_path / "media"
    monkeypatch.setattr(media, "MEDIA_ROOT", root)
    return root


@pytest.fixture
def fake_keys(monkeypatch):
    """Integration settings with keys 'present', so the routes get past their key check to
    the quota gate — the thing under test — without an ElevenLabs/Anthropic key anywhere."""
    async def _settings(*needs):
        return {"anthropic_api_key": "test-key", "elevenlabs_api_key": "test-key",
                "llm_model": "test-model", "stt_model": "test-stt"}
    monkeypatch.setattr(recordings, "_settings", _settings)


@pytest.fixture
def fake_stt(monkeypatch):
    """A transcription that never leaves the process, so an admitted upload becomes a
    `ready` recording instead of a network call."""
    async def _transcribe(*a, **kw):
        return {"text": "hello there", "language_code": "en", "words": []}
    monkeypatch.setattr(elevenlabs, "transcribe", _transcribe)


def _override(user_id: str, limits_blob) -> None:
    sql(lambda c: c.execute("UPDATE app_users SET limits = $2::jsonb WHERE id = $1",
                            uuid.UUID(user_id), json.dumps(limits_blob)))


def _set_active(user_id: str, active: bool) -> None:
    sql(lambda c: c.execute("UPDATE app_users SET is_active = $2 WHERE id = $1",
                            uuid.UUID(user_id), active))


def _paste(api, headers: dict):
    return api.post("/recordings/text", json={"text": TRANSCRIPT}, headers=headers)


def _counter(user_id: str, kind: str) -> int:
    return sql(lambda c: c.fetchval(
        "SELECT coalesce(sum(n), 0) FROM usage_counters WHERE scope_key = $1 AND kind = $2",
        f"user:{user_id}", kind))


# ---------------------------------------------------------------------------
# Pure: the cap resolution and the feature gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("overrides, tier, expected", [
    ({"max_tts_per_day": 5}, {"max_tts_per_day": 50}, 5),        # override beats tier
    ({"max_tts_per_day": 0}, {"max_tts_per_day": 50}, 0),        # explicit 0 = uncapped
    ({"max_tts_per_day": "7"}, {"max_tts_per_day": 50}, 7),      # numeric string is a number
    ({"max_tts_per_day": 3.9}, {"max_tts_per_day": 50}, 3),
    ({"max_tts_per_day": -4}, {"max_tts_per_day": 50}, 0),       # never negative
    ({"max_tts_per_day": "abc"}, {"max_tts_per_day": 50}, 50),   # garbage is ignored, not 0
    ({"max_tts_per_day": True}, {"max_tts_per_day": 50}, 50),    # a bool is not a cap
    ({"max_tts_per_day": None}, {"max_tts_per_day": 50}, 50),
    ({}, {"max_tts_per_day": ""}, 0),                             # absent everywhere
    ({}, {"max_tts_per_day": "x"}, 0),
    ({"max_analyses_per_day": 1}, {"max_tts_per_day": 50}, 50),  # a different key is not an override
])
def test_user_cap_precedence(overrides, tier, expected):
    assert limits._user_cap(overrides, tier, "max_tts_per_day") == expected


def test_registered_gate_is_about_features_not_signups():
    limits._registered_gate({"enabled": False, "features": {"score": True}}, "score")
    limits._registered_gate({"features": {}}, "score")                     # unlisted = on
    with pytest.raises(HTTPException) as exc:
        limits._registered_gate({"features": {"score": False}}, "score")
    assert exc.value.status_code == 403 and exc.value.detail == FEATURE_OFF


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------
def test_snapshot_has_the_anonymous_shape_plus_the_registered_facts(api, make_account):
    acct = make_account()
    r = api.get("/limits", headers=acct["headers"])
    assert r.status_code == 200, r.text
    snap = r.json()
    anon = api.get("/limits", headers={"X-Real-IP": f"limtest-{uuid.uuid4().hex[:8]}"}).json()

    assert snap["anonymous"] is False and snap["registered"] is True
    assert snap["kind"] == "user" and snap["user_id"] == acct["id"] and snap["active"] is True
    assert "enabled" not in snap          # on the tier it means "sign-ups open" — not for a user
    # The nested shape the existing banner renderer reads is identical to the anonymous one.
    assert set(snap["used"]) == set(anon["used"]) == {"analyses", "tts", "conversions"}
    assert set(snap["remaining"]) == set(anon["remaining"])
    for key in ("max_analyses_per_day", "max_tts_per_day", "max_conversions_per_day",
                "max_audio_mb", "features"):
        assert key in snap and key in anon
    assert snap["max_analyses_per_day"] == 2 and snap["max_tts_per_day"] == 3
    assert snap["max_conversions_per_day"] == 4 and snap["max_audio_mb"] == 50
    assert snap["features"] == REGISTERED_PIN["features"]
    assert snap["used"] == {"analyses": 0, "tts": 0, "conversions": 0}
    assert snap["remaining"] == {"analyses": 2, "tts": 3, "conversions": 4}


# ---------------------------------------------------------------------------
# Tier cap, per user, and the override that beats it
# ---------------------------------------------------------------------------
def test_tier_cap_is_enforced_per_user(api, make_account):
    a, b = make_account(), make_account()
    assert _paste(api, a["headers"]).status_code == 200
    assert _paste(api, a["headers"]).status_code == 200
    third = _paste(api, a["headers"])
    assert third.status_code == 429, third.text
    assert third.json()["detail"] == "Rate limit reached for analyses (2 per day)."
    assert _counter(a["id"], "analyses") == 2       # the refused one was not counted
    snap = api.get("/limits", headers=a["headers"]).json()
    assert snap["used"]["analyses"] == 2 and snap["remaining"]["analyses"] == 0
    # Counted PER USER: B's allowance is untouched by A's spending.
    assert _paste(api, b["headers"]).status_code == 200
    assert api.get("/limits", headers=b["headers"]).json()["used"]["analyses"] == 1


def test_per_user_override_beats_the_tier(api, make_account):
    acct = make_account()
    for _ in range(2):
        assert _paste(api, acct["headers"]).status_code == 200
    assert _paste(api, acct["headers"]).status_code == 429

    _override(acct["id"], {"max_analyses_per_day": 4})
    snap = api.get("/limits", headers=acct["headers"]).json()
    assert snap["max_analyses_per_day"] == 4 and snap["remaining"]["analyses"] == 2
    assert _paste(api, acct["headers"]).status_code == 200

    _override(acct["id"], {"max_analyses_per_day": 0})     # explicit 0: uncapped, still counted
    snap = api.get("/limits", headers=acct["headers"]).json()
    assert snap["max_analyses_per_day"] == 0 and snap["remaining"]["analyses"] is None
    assert _paste(api, acct["headers"]).status_code == 200
    assert _counter(acct["id"], "analyses") == 4

    _override(acct["id"], {"max_analyses_per_day": "abc"})  # garbage: back to the tier's 2
    assert api.get("/limits", headers=acct["headers"]).json()["max_analyses_per_day"] == 2
    assert _paste(api, acct["headers"]).status_code == 429


def test_override_of_a_different_key_does_not_touch_this_one(api, make_account):
    acct = make_account()
    _override(acct["id"], {"max_tts_per_day": 99})
    snap = api.get("/limits", headers=acct["headers"]).json()
    assert snap["max_tts_per_day"] == 99 and snap["max_analyses_per_day"] == 2


# ---------------------------------------------------------------------------
# Feature switches
# ---------------------------------------------------------------------------
def test_disabled_analyze_refuses_before_counting(api, registered, make_account):
    acct = make_account()
    registered(features={**REGISTERED_PIN["features"], "analyze": False})
    r = _paste(api, acct["headers"])
    assert r.status_code == 403 and r.json()["detail"] == FEATURE_OFF
    assert _counter(acct["id"], "analyses") == 0
    assert sql(lambda c: c.fetchval("SELECT count(*) FROM audio_jobs WHERE user_id = $1",
                                    uuid.UUID(acct["id"]))) == 0
    assert api.get("/limits", headers=acct["headers"]).json()["features"]["analyze"] is False


def test_disabled_analyser_switches_are_enforced_by_the_routes(api, registered, make_account):
    """score / semantic / summarise are not metered kinds, so the routes gate them through
    `require_feature` — otherwise the admin's checkboxes would be display-only."""
    acct = make_account()
    rec = _paste(api, acct["headers"]).json()["id"]
    registered(features={**REGISTERED_PIN["features"], "score": False, "semantic": False,
                         "summarise": False})
    r = api.post(f"/recordings/{rec}/score", headers=acct["headers"])
    assert r.status_code == 403 and r.json()["detail"] == FEATURE_OFF
    r = api.post(f"/recordings/{rec}/semantic", json={"modes": ["text"]}, headers=acct["headers"])
    assert r.status_code == 403 and r.json()["detail"] == FEATURE_OFF
    r = api.post("/summaries", files=[("files", ("a.wav", b"RIFF0000", "audio/wav"))],
                 headers=acct["headers"])
    assert r.status_code == 403 and r.json()["detail"] == FEATURE_OFF
    # Nothing was spent by the refusals.
    assert _counter(acct["id"], "analyses") == 1
    assert api.get(f"/recordings/{rec}", headers=acct["headers"]).json()["scoring"] is None


def test_enabled_score_runs_against_the_default_rubric(api, registered, make_account,
                                                       fake_keys, monkeypatch):
    """The same switch, on: the route reaches the scorer with the caller's own identity and
    the rubric every user without one inherits. The scorer is a fake — the point is what it
    is handed and that its answer lands on the row."""
    acct = make_account()
    rec = _paste(api, acct["headers"]).json()["id"]
    seen = {}

    async def _fake_scoring(transcript, config, api_key, model, client_id=None, segments=None,
                            user_id=None):
        seen.update(transcript=transcript, config=config, client_id=client_id,
                    segments=segments, user_id=user_id)
        return {"config_version": config.get("version"), "weighted_total": 77.5,
                "max_total": 100, "dimensions": [], "lanes": [], "operator_speaker": "agent"}
    monkeypatch.setattr(scoring, "run_scoring", _fake_scoring)

    r = api.post(f"/recordings/{rec}/score", headers=acct["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["weighted_total"] == 77.5
    assert seen["user_id"] == acct["id"] and seen["client_id"] is None
    assert seen["transcript"] == TRANSCRIPT
    assert [s["speaker"] for s in seen["segments"]] == ["agent", "customer"]
    assert seen["config"].get("is_default") is True and seen["config"]["dimensions"]
    row = api.get(f"/recordings/{rec}", headers=acct["headers"]).json()
    assert row["scoring"]["weighted_total"] == 77.5
    listed = api.get("/recordings", headers=acct["headers"]).json()
    assert [x["ran"]["score"] for x in listed if x["id"] == rec] == [True]


def test_closed_signups_do_not_lock_existing_users_out(api, registered, make_account):
    acct = make_account()
    registered(enabled=False)
    assert _paste(api, acct["headers"]).status_code == 200
    assert api.get("/limits", headers=acct["headers"]).status_code == 200


def test_analyser_runs_are_metered_not_only_the_upload(api, registered, make_account,
                                                      fake_keys, monkeypatch):
    """A recording is uploaded once but can be judged again and again, and every judgement is
    its own Claude call — so re-running one costs a unit like the upload did. Without this a
    free account with a single recording is an unbounded paid loop."""
    async def _fake_scoring(*a, **kw):
        return {"config_version": 0, "weighted_total": 50.0, "max_total": 100,
                "dimensions": [], "lanes": [], "operator_speaker": "agent"}
    monkeypatch.setattr(scoring, "run_scoring", _fake_scoring)

    registered(max_analyses_per_day=3)
    acct = make_account()
    rec = _paste(api, acct["headers"]).json()["id"]                  # 1: the upload
    assert api.post(f"/recordings/{rec}/score", headers=acct["headers"]).status_code == 200   # 2
    assert api.post(f"/recordings/{rec}/score", headers=acct["headers"]).status_code == 200   # 3
    assert _counter(acct["id"], "analyses") == 3

    r = api.post(f"/recordings/{rec}/score", headers=acct["headers"])
    assert r.status_code == 429, r.text
    assert r.json()["detail"] == "Rate limit reached for analyses (3 per day)."
    assert _counter(acct["id"], "analyses") == 3        # the refused run was not counted
    # The refusals that cost nothing still come first: no rubric, no transcript, wrong kind.
    assert api.get("/limits", headers=acct["headers"]).json()["remaining"]["analyses"] == 0


def test_summaries_refuse_an_oversize_file_before_spending_a_unit(api, make_account,
                                                                  fake_keys, media_root):
    """Every file's size is known before the first `reserve()`, so a batch whose second file
    is over the caller's cap must not pay for the first one on the way to a certain 413."""
    acct = make_account()
    _override(acct["id"], {"max_audio_mb": 1, "max_analyses_per_day": 10})
    r = api.post("/summaries", headers=acct["headers"], files=[
        ("files", ("small.wav", b"\0" * 1024, "audio/wav")),
        ("files", ("big.wav", b"\0" * (1536 * 1024), "audio/wav")),
    ])
    assert r.status_code == 413, r.text
    detail = r.json()["detail"]
    assert "1 MB" in detail and "big.wav" in detail
    assert _counter(acct["id"], "analyses") == 0
    assert sql(lambda c: c.fetchval("SELECT count(*) FROM audio_jobs WHERE user_id = $1",
                                    uuid.UUID(acct["id"]))) == 0


def test_summaries_check_the_quota_before_reading_the_batch(api, make_account):
    """`_read_uploads` holds the whole batch (up to 300 MB) in this process and cannot be
    streamed, so a caller with nothing left must be turned away at the door rather than after
    the read. The empty file is the tell: its 400 comes from inside the read, so a 429 here
    proves the meter ran first."""
    acct = make_account()
    for _ in range(2):                                   # the tier's two analyses
        assert _paste(api, acct["headers"]).status_code == 200
    r = api.post("/summaries", headers=acct["headers"],
                 files=[("files", ("empty.wav", b"", "audio/wav"))])
    assert r.status_code == 429, r.text
    assert r.json()["detail"] == "Rate limit reached for analyses (2 per day)."


# ---------------------------------------------------------------------------
# Inactive account
# ---------------------------------------------------------------------------
def test_deactivated_account_is_refused_at_the_meter_but_can_still_see_why(api, registered,
                                                                            make_account):
    acct = make_account()
    rec = _paste(api, acct["headers"]).json()["id"]
    _set_active(acct["id"], False)

    r = _paste(api, acct["headers"])
    assert r.status_code == 403 and r.json()["detail"] == DISABLED
    r = api.post(f"/recordings/{rec}/score", headers=acct["headers"])
    assert r.status_code == 403 and r.json()["detail"] == DISABLED
    # The snapshot does not 403: the account page renders a banner from `active` instead.
    snap = api.get("/limits", headers=acct["headers"])
    assert snap.status_code == 200 and snap.json()["active"] is False
    assert _counter(acct["id"], "analyses") == 1

    _set_active(acct["id"], True)
    assert _paste(api, acct["headers"]).status_code == 200
    assert api.get("/limits", headers=acct["headers"]).json()["active"] is True


def test_deleted_account_with_a_live_token_is_refused_at_the_meter(api, make_account):
    acct = make_account()
    sql(lambda c: c.execute("DELETE FROM app_users WHERE id = $1", uuid.UUID(acct["id"])))
    r = _paste(api, acct["headers"])
    assert r.status_code == 403 and r.json()["detail"] == DISABLED
    snap = api.get("/limits", headers=acct["headers"])
    assert snap.status_code == 200 and snap.json()["active"] is False


# ---------------------------------------------------------------------------
# Upload size cap (max_audio_mb) — through the real upload route
# ---------------------------------------------------------------------------
def test_audio_size_cap_honours_the_per_user_override(api, make_account, media_root,
                                                      fake_keys, fake_stt):
    acct = make_account()
    _override(acct["id"], {"max_audio_mb": 1})
    big = b"\0" * (1536 * 1024)
    r = api.post("/recordings", files={"file": ("big.wav", big, "audio/wav")}, headers=acct["headers"])
    assert r.status_code == 413, r.text
    assert r.json()["detail"] == "Uploads are limited to 1 MB for your account."
    # Refused BEFORE a unit was spent or a row created.
    assert _counter(acct["id"], "analyses") == 0
    assert sql(lambda c: c.fetchval("SELECT count(*) FROM audio_jobs WHERE user_id = $1",
                                    uuid.UUID(acct["id"]))) == 0
    assert not list(media_root.rglob("*")) or not [p for p in media_root.rglob("*") if p.is_file()]

    small = b"\0" * (100 * 1024)
    r = api.post("/recordings", files={"file": ("small.wav", small, "audio/wav")}, headers=acct["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready" and body["audio_url"] == f"/recordings/{body['id']}/audio"
    assert body["transcript"] == "hello there"
    assert _counter(acct["id"], "analyses") == 1
    assert len([p for p in media_root.rglob("*") if p.is_file()]) == 1


# ---------------------------------------------------------------------------
# Directly against the service: the kinds no keyless HTTP test can reach
# ---------------------------------------------------------------------------
def test_reserve_check_and_snapshot_for_a_user(make_account, registered):
    acct = make_account()
    p = Principal(kind="user", user_id=acct["id"], role="user", via="token")
    scope = f"user:{acct['id']}"

    async def _run():
        for _ in range(3):
            await limits.reserve(p, "tts")
        with pytest.raises(HTTPException) as exc:
            await limits.reserve(p, "tts")
        assert exc.value.status_code == 429
        assert exc.value.detail == "Rate limit reached for tts (3 per day)."
        with pytest.raises(HTTPException) as exc:
            await limits.check(p, "tts")
        assert exc.value.status_code == 429 and "tts (3 per day)" in exc.value.detail
        # check() never spends; conversions are a separate counter with its own cap.
        assert (await limits.usage_today([scope]))[scope] == {"tts": 3}
        await limits.check(p, "conversions")
        await limits.reserve(p, "conversions")
        snap = await limits.snapshot(p)
        assert snap["used"] == {"analyses": 0, "tts": 3, "conversions": 1}
        assert snap["remaining"] == {"analyses": 2, "tts": 0, "conversions": 3}
        # A size only matters for analyses: a huge "tts" reservation is a plain quota check.
        with pytest.raises(HTTPException) as exc:
            await limits.reserve(p, "tts", 10 ** 9)
        assert exc.value.status_code == 429
    _with_db(_run)


def test_reserve_and_require_feature_honour_switches_and_deactivation(make_account, registered):
    acct = make_account()
    p = Principal(kind="user", user_id=acct["id"], role="user", via="token")
    registered(features={**REGISTERED_PIN["features"], "tts": False, "semantic": False})

    async def _switched_off():
        for call in (limits.reserve(p, "tts"), limits.check(p, "tts"),
                     limits.require_feature(p, "semantic")):
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 403 and exc.value.detail == FEATURE_OFF
        await limits.require_feature(p, "score")               # still on
        await limits.reserve(p, "conversions")                 # still on, and counted
        assert (await limits.usage_today([f"user:{acct['id']}"]))[f"user:{acct['id']}"] == {"conversions": 1}
    _with_db(_switched_off)

    _set_active(acct["id"], False)

    async def _deactivated():
        for call in (limits.reserve(p, "conversions"), limits.check(p, "conversions"),
                     limits.require_feature(p, "score")):
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 403 and exc.value.detail == DISABLED
        ghost = Principal(kind="user", user_id=str(uuid.uuid4()), role="user", via="token")
        with pytest.raises(HTTPException) as exc:
            await limits.reserve(ghost, "conversions")
        assert exc.value.status_code == 403 and exc.value.detail == DISABLED
        # Non-user kinds are untouched by any of this.
        await limits.require_feature(Principal(kind="superadmin"), "score")
        assert (await limits.snapshot(Principal(kind="superadmin")))["unlimited"] is True
    _with_db(_deactivated)
