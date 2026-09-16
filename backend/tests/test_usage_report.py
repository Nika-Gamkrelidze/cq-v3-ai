"""The usage overview and call log: numbers that must add up, and filters that must narrow.

An operator prices a workspace off these tables, so the property under test is arithmetic, not
layout: every breakdown sums back to the total, a filter changes the total the way it says, and
the call log's search, sort and pages cover each call exactly once. The seeded rows are scoped
to a throwaway workspace and every request filters by it, so whatever else a developer's
database holds cannot move an assertion.

Integration tests skip without a database (conftest); the pure helpers at the top do not.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services import usage, usage_report
from conftest import sql  # loop-independent SQL; see its module docstring

ADMIN = {"X-Admin-Token": settings.admin_token}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_bucket_is_hourly_up_to_two_days_inclusive():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert usage_report.bucket_of(start, start + timedelta(days=1)) == "hour"
    assert usage_report.bucket_of(start, start + timedelta(days=2)) == "hour"
    assert usage_report.bucket_of(start, start + timedelta(days=2, seconds=1)) == "day"
    assert usage_report.bucket_of(start, start + timedelta(days=30)) == "day"


def test_like_pattern_escapes_the_users_wildcards():
    assert usage_report.like_pattern("abc") == "%abc%"
    assert usage_report.like_pattern("100%") == "%100\\%%"
    assert usage_report.like_pattern("a_b") == "%a\\_b%"
    # The escape character itself first, or the `\` added for `%` would be doubled too.
    assert usage_report.like_pattern("a\\%") == "%a\\\\\\%%"


def test_call_sorts_are_the_contract_keys():
    assert set(usage_report.CALL_SORTS) == {
        "created_at", "total_tokens", "input_tokens", "output_tokens", "latency_ms",
        "audio_seconds", "consumed", "feature", "provider", "model", "tenant"}
    page = usage.page_params("1; DROP TABLE llm_usage", "sideways", 5, 0,
                             usage_report.CALL_SORTS, usage_report.DEFAULT_CALL_SORT)
    assert page["sort"] == "created_at" and page["dir"] == "desc"


# ---------------------------------------------------------------------------
# Seed: workspace A with a spread of calls, workspace B as the neighbour
# ---------------------------------------------------------------------------
def _call(feature, **kw):
    base = {"feature": feature, "model": "m-default", "input_tokens": None, "output_tokens": None,
            "cache_read_tokens": None, "cache_creation_tokens": None, "latency_ms": 100,
            "ok": True, "actor": None, "job": False, "byo": False, "provider": None,
            "capability": None, "conv": None, "audio_seconds": None, "characters": None}
    base.update(kw)
    return base


# Minutes ago -> call. Distinct ages keep created_at ordering deterministic.
A_CALLS = [
    _call("transcribe", model="scribe_v1", provider="elevenlabs", capability="stt",
          audio_seconds=62.5, job=True, latency_ms=4000),
    # NULL provider on a text call predates the registry: reports as anthropic.
    _call("factcheck_claims", model="claude-a", input_tokens=100, output_tokens=50, job=True),
    _call("factcheck_verdict", model="claude-a", provider="anthropic", input_tokens=200,
          output_tokens=25, cache_read_tokens=10, job=True),
    _call("autopilot", model="gemini-x", provider="gemini", input_tokens=300, output_tokens=30,
          conv="own", actor="tenant:apikey"),
    _call("triage", model="gemini-x", provider="gemini", input_tokens=40, output_tokens=0,
          conv="own", ok=False),
    _call("tts", model="eleven_v3", provider="elevenlabs", capability="tts", characters=120),
    _call("voice_tone", model="tone-local", provider="local", capability="voice_tone",
          input_tokens=0, output_tokens=0, audio_seconds=30.0),
    _call("copilot", model="gpt-x", provider="openai", input_tokens=500, output_tokens=100,
          cache_creation_tokens=7, byo=True, actor="tenant:owner"),
    # Points at workspace B's conversation and turn. The joins must not follow it there.
    _call("handoff", model="claude-a", provider="anthropic", input_tokens=5, output_tokens=5,
          conv="foreign"),
]


def _tokens(c) -> int:
    return sum(c[k] or 0 for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                                   "cache_creation_tokens"))


A_TOTAL = sum(_tokens(c) for c in A_CALLS)


@pytest.fixture(scope="module")
def seeded(api):
    """`api` first: its lifespan runs the migrations that add the columns inserted here."""
    suffix = uuid.uuid4().hex[:8]
    marker = f"zebra{suffix}"

    async def _setup(conn):
        ids = {}
        for label in ("a", "b"):
            ids[label] = await conn.fetchval(
                "INSERT INTO clients (slug, name, api_key) VALUES ($1,$2,$3) RETURNING id",
                f"usagerep-{label}-{suffix}", f"Usage report {label.upper()} {suffix}",
                f"usagerep-key-{label}-{suffix}")
        a, b = ids["a"], ids["b"]
        job = await conn.fetchval(
            "INSERT INTO audio_jobs (filename, client_id, status) VALUES ($1,$2,'done') "
            "RETURNING id", f"call-{suffix}.wav", a)
        convs, turns = {}, {}
        for label, cid, content in (("own", a, f"What is the {marker.upper()} refund window?"),
                                    ("foreign", b, f"secret-{suffix} of workspace B")):
            convs[label] = await conn.fetchval(
                "INSERT INTO chat_conversations (client_id, external_ref, channel) "
                "VALUES ($1,$2,'web') RETURNING id", cid, f"thread-{label}-{suffix}")
            turns[label] = await conn.fetchval(
                "INSERT INTO chat_turns (client_id, conversation_id, turn_ref, role, content) "
                "VALUES ($1,$2,$3,'customer',$4) RETURNING id",
                cid, convs[label], f"msg-{label}-{suffix}", content)

        insert = """
            INSERT INTO llm_usage (client_id, feature, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, latency_ms, ok, created_at, actor,
                job_id, byo, provider, capability, conversation_id, turn_id, audio_seconds,
                characters)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, now() - $10::interval, $11,$12,$13,$14,$15,$16,
                    $17,$18,$19)"""
        for i, c in enumerate(A_CALLS):
            await conn.execute(
                insert, a, c["feature"], c["model"], c["input_tokens"], c["output_tokens"],
                c["cache_read_tokens"], c["cache_creation_tokens"], c["latency_ms"], c["ok"],
                timedelta(minutes=10 * (i + 1)), c["actor"], job if c["job"] else None,
                c["byo"], c["provider"], c["capability"],
                convs[c["conv"]] if c["conv"] else None, turns[c["conv"]] if c["conv"] else None,
                c["audio_seconds"], c["characters"])
        # Outside every preset window but inside an explicit from/to.
        await conn.execute(insert, a, "probe", "claude-a", 1, 1, None, None, 50, True,
                           timedelta(days=40), None, None, False, "anthropic", "llm", None,
                           None, None, None)
        # The neighbour: must never appear under A's filter.
        await conn.execute(insert, b, "analysis", "claude-a", 999, 1, None, None, 50, True,
                           timedelta(minutes=5), None, None, False, "anthropic", "llm", None,
                           None, None, None)
        return {"a": str(a), "b": str(b), "job": str(job), "suffix": suffix, "marker": marker,
                "conv": str(convs["own"]), "foreign_conv": str(convs["foreign"])}

    data = sql(_setup)
    try:
        yield data
    finally:
        ids = [uuid.UUID(data["a"]), uuid.UUID(data["b"])]

        async def _cleanup(conn):
            # llm_usage and audio_jobs SET NULL on a client delete, so they go first; the chat
            # rows cascade from clients.
            await conn.execute("DELETE FROM llm_usage WHERE client_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM audio_jobs WHERE client_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM clients WHERE id = ANY($1::uuid[])", ids)
        sql(_cleanup)


def _get(api, path, **params):
    r = api.get(path, params=params, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def _sum(rows, key):
    return sum(r[key] for r in rows)


# ---------------------------------------------------------------------------
# Access and validation
# ---------------------------------------------------------------------------
def test_reports_require_the_admin_token(api):
    for path in ("/admin/usage/overview", "/admin/usage/calls"):
        assert api.get(path).status_code == 401
        assert api.get(path, headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_a_bad_date_is_a_400_with_a_sentence(api):
    for path in ("/admin/usage/overview", "/admin/usage/calls"):
        r = api.get(path, params={"from": "16/09/2026"}, headers=ADMIN)
        assert r.status_code == 400 and "from" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def test_overview_breakdowns_add_up_to_the_total(api, seeded):
    body = _get(api, "/admin/usage/overview", client_id=seeded["a"])
    total = body["total"]
    assert set(total) == set(usage.TOTAL_KEYS)
    assert total["calls"] == len(A_CALLS)
    assert total["total_tokens"] == A_TOTAL
    assert total["failed"] == 1
    assert total["byo_tokens"] == 607
    assert total["audio_seconds"] == pytest.approx(92.5)
    assert total["characters"] == 120
    assert total["first_used"] and total["last_used"]

    for key in ("by_tenant", "by_group", "by_feature", "by_provider", "by_model", "by_user"):
        rows = body[key]
        assert rows, key
        assert all(set(usage.TOTAL_KEYS) <= set(r) for r in rows), key
        assert _sum(rows, "total_tokens") == A_TOTAL, key
        assert _sum(rows, "calls") == len(A_CALLS), key
        tokens = [r["total_tokens"] for r in rows]
        assert tokens == sorted(tokens, reverse=True), key

    assert [(r["client_id"], r["name"]) for r in body["by_tenant"]] == [
        (seeded["a"], f"Usage report A {seeded['suffix']}")]
    assert _sum(body["series"], "total_tokens") == A_TOTAL
    assert _sum(body["series"], "calls") == len(A_CALLS)
    assert _sum(body["series"], "failed") == 1
    assert body["bucket"] == "day" and body["window"] == "30d"


def test_overview_maps_features_to_groups_and_null_providers(api, seeded):
    body = _get(api, "/admin/usage/overview", client_id=seeded["a"])
    groups = {r["group"]: r for r in body["by_group"]}
    assert groups["factcheck"]["calls"] == 2 and groups["factcheck"]["total_tokens"] == 385
    assert groups["bot"]["calls"] == 3 and groups["bot"]["failed"] == 1
    assert groups["sentiment"]["audio_seconds"] == pytest.approx(30.0)
    assert groups["transcription"]["audio_seconds"] == pytest.approx(62.5)
    features = {r["feature"]: r["group"] for r in body["by_feature"]}
    assert features["factcheck_claims"] == features["factcheck_verdict"] == "factcheck"
    assert features["voice_tone"] == "sentiment" and features["tts"] == "tts"

    providers = {r["provider"]: r["calls"] for r in body["by_provider"]}
    assert providers == {"anthropic": 3, "elevenlabs": 2, "gemini": 2, "local": 1, "openai": 1}
    models = {(r["provider"], r["model"], r["capability"]) for r in body["by_model"]}
    assert ("anthropic", "claude-a", "llm") in models
    assert ("elevenlabs", "scribe_v1", "stt") in models

    users = {r["actor"]: r for r in body["by_user"]}
    assert users["unattributed"]["calls"] == 7
    assert users["tenant:owner"]["total_tokens"] == 607
    assert all(r["client_id"] == seeded["a"] for r in body["by_user"])


def test_overview_filters_narrow_the_total_but_not_the_facets(api, seeded):
    gem = _get(api, "/admin/usage/overview", client_id=seeded["a"], provider="gemini")
    assert gem["total"]["calls"] == 2 and gem["total"]["total_tokens"] == 370
    assert [r["provider"] for r in gem["by_provider"]] == ["gemini"]

    failed = _get(api, "/admin/usage/overview", client_id=seeded["a"], status="failed")
    assert failed["total"]["calls"] == 1
    assert [r["feature"] for r in failed["by_feature"]] == ["triage"]

    grp = _get(api, "/admin/usage/overview", client_id=seeded["a"], group="factcheck")
    assert grp["total"]["calls"] == 2

    # Facets come from the range alone: the provider filter must not hide the other providers,
    # and the workspace filter must not hide the neighbour.
    facets = gem["facets"]
    assert {"anthropic", "elevenlabs", "gemini", "local", "openai"} <= set(facets["providers"])
    assert {seeded["a"], seeded["b"]} <= {t["client_id"] for t in facets["tenants"]}
    assert {"tenant:apikey", "tenant:owner"} <= set(facets["actors"])
    assert facets["groups"] == [g for g in usage.GROUP_ORDER if g in facets["groups"]]
    assert {"llm", "stt", "tts", "voice_tone"} <= set(facets["capabilities"])


def test_overview_buckets_and_explicit_range(api, seeded):
    day = _get(api, "/admin/usage/overview", client_id=seeded["a"], window="24h")
    assert day["bucket"] == "hour" and day["total"]["calls"] == len(A_CALLS)
    assert all(s["t"].endswith("+00:00") for s in day["series"])

    today = datetime.now(timezone.utc).date()
    wide = _get(api, "/admin/usage/overview", client_id=seeded["a"],
                **{"from": (today - timedelta(days=45)).isoformat(), "to": today.isoformat()})
    assert wide["window"] == "custom" and wide["bucket"] == "day"
    assert wide["total"]["calls"] == len(A_CALLS) + 1


# ---------------------------------------------------------------------------
# Call log
# ---------------------------------------------------------------------------
CALL_KEYS = {
    "id", "created_at", "client_id", "tenant_name", "slug", "feature", "group", "capability",
    "provider", "model", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "total_tokens", "audio_seconds", "characters", "latency_ms", "ok",
    "byo", "actor", "job_id", "filename", "conversation_id", "conversation_ref", "channel",
    "turn_id", "turn_role", "turn_preview", "summary_id"}


def test_call_rows_carry_their_recording_and_question(api, seeded):
    body = _get(api, "/admin/usage/calls", client_id=seeded["a"], limit=200)
    assert body["total"] == len(A_CALLS) and len(body["rows"]) == len(A_CALLS)
    assert (body["sort"], body["dir"], body["offset"]) == ("created_at", "desc", 0)
    assert all(set(r) == CALL_KEYS for r in body["rows"])
    created = [r["created_at"] for r in body["rows"]]
    assert created == sorted(created, reverse=True)

    rows = {r["feature"]: r for r in body["rows"]}
    stt = rows["transcribe"]
    assert (stt["capability"], stt["group"]) == ("stt", "transcription")
    assert stt["job_id"] == seeded["job"]
    assert stt["filename"] == f"call-{seeded['suffix']}.wav"
    assert stt["total_tokens"] == 0 and stt["input_tokens"] is None
    assert rows["factcheck_claims"]["provider"] == "anthropic"
    assert rows["tts"]["capability"] == "tts" and rows["tts"]["characters"] == 120

    bot = rows["autopilot"]
    assert bot["conversation_id"] == seeded["conv"] and bot["channel"] == "web"
    assert bot["conversation_ref"] == f"thread-own-{seeded['suffix']}"
    assert bot["turn_role"] == "customer" and seeded["marker"].upper() in bot["turn_preview"]
    assert bot["tenant_name"] == f"Usage report A {seeded['suffix']}"

    # The row names B's conversation, but the join is scoped to A, so nothing of B's shows.
    foreign = rows["handoff"]
    assert foreign["conversation_id"] == seeded["foreign_conv"]
    assert foreign["conversation_ref"] is None and foreign["turn_preview"] is None


def test_call_search_finds_turn_content_filename_and_actor(api, seeded):
    def found(q):
        body = _get(api, "/admin/usage/calls", client_id=seeded["a"], q=q)
        return sorted(r["feature"] for r in body["rows"]), body["total"]

    # Case-insensitive: stored upper case, searched lower case.
    assert found(seeded["marker"]) == (["autopilot", "triage"], 2)
    assert found(f"CALL-{seeded['suffix']}") == (
        ["factcheck_claims", "factcheck_verdict", "transcribe"], 3)
    assert found("tenant:own") == (["copilot"], 1)
    assert found(f"thread-own-{seeded['suffix']}")[1] == 2
    # B's turn text is not searchable through A's row that points at it.
    assert found(f"secret-{seeded['suffix']}") == ([], 0)
    # A literal wildcard matches only itself, and nothing here contains one.
    assert found("%") == ([], 0)
    assert found("_") == ([], 0)


def test_call_filters(api, seeded):
    gem = _get(api, "/admin/usage/calls", client_id=seeded["a"], provider="gemini")
    assert gem["total"] == 2 and {r["provider"] for r in gem["rows"]} == {"gemini"}
    failed = _get(api, "/admin/usage/calls", client_id=seeded["a"], status="failed")
    assert [r["feature"] for r in failed["rows"]] == ["triage"] and failed["total"] == 1
    grp = _get(api, "/admin/usage/calls", client_id=seeded["a"], group="factcheck")
    assert sorted(r["feature"] for r in grp["rows"]) == ["factcheck_claims", "factcheck_verdict"]
    cap = _get(api, "/admin/usage/calls", client_id=seeded["a"], capability="llm")
    assert cap["total"] == 6


def test_call_sort_by_tokens_both_ways(api, seeded):
    asc = _get(api, "/admin/usage/calls", client_id=seeded["a"], sort="total_tokens", dir="asc")
    desc = _get(api, "/admin/usage/calls", client_id=seeded["a"], sort="total_tokens", dir="desc")
    up = [r["total_tokens"] for r in asc["rows"]]
    down = [r["total_tokens"] for r in desc["rows"]]
    assert up == sorted(up) and down == sorted(down, reverse=True)
    assert down[0] == 607 and up[0] == 0
    assert (asc["sort"], asc["dir"]) == ("total_tokens", "asc")

    bogus = _get(api, "/admin/usage/calls", client_id=seeded["a"], sort="id; DROP", dir="up")
    assert (bogus["sort"], bogus["dir"]) == ("created_at", "desc")
    assert bogus["total"] == len(A_CALLS)


def test_call_pages_cover_every_call_once(api, seeded):
    seen = []
    for offset in (0, 3, 6):
        page = _get(api, "/admin/usage/calls", client_id=seeded["a"], limit=3, offset=offset,
                    sort="tenant")
        assert page["total"] == len(A_CALLS)
        assert (page["limit"], page["offset"]) == (3, offset)
        seen += [r["id"] for r in page["rows"]]
    assert len(seen) == len(set(seen)) == len(A_CALLS)

    past = _get(api, "/admin/usage/calls", client_id=seeded["a"], limit=3, offset=100)
    assert past["rows"] == [] and past["total"] == len(A_CALLS)

    clamped = _get(api, "/admin/usage/calls", client_id=seeded["a"], limit=5000, offset=-4)
    assert (clamped["limit"], clamped["offset"]) == (usage.MAX_LIMIT, 0)


@pytest.mark.parametrize("params", [{"to": "9999-12-31"}, {"to": "0001-01-05"},
                                    {"from": "2026-01-01", "to": "9999-12-31"}])
def test_calendar_edge_dates_are_a_400_not_a_500(params):
    """They parse as dates; the range arithmetic after them overflows."""
    with pytest.raises(usage.FilterError):
        usage.parse_filter(**{("from_" if k == "from" else k): v for k, v in params.items()})


def test_analyser_sort_keeps_each_group_together():
    """The column shows the analyser, so the feature sort ranks by group first."""
    expr = usage_report.CALL_SORTS["feature"]
    assert usage.group_rank_sql() in expr and "u.feature" in expr
