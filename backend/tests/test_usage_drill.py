"""The usage page's drill-downs (`services/usage_drill.py`): recordings with a subtotal per
analyser, chat conversations with a subtotal per feature, and the two detail views.

The rows are written straight into `llm_usage`, `audio_jobs`, `call_summaries` and the chat
tables — no model is called and no route that meters one is exercised; what is under test is
the reading side. Everything is scoped to two throwaway workspaces (every list call filters by
`client_id`), so rows other tests write at the same time cannot move a number here.

The isolation case is built in on purpose: workspace B owns one usage row whose recording,
conversation and turn ids all point at A's data. No such row is ever written by the product —
which is exactly why the reading side must not trust the ids alone: B's row must come back
with A's filename, conversation reference and customer words all NULL.
"""
import uuid

import pytest

from app.config import settings
from conftest import sql  # loop-independent SQL; see its module docstring

ADMIN = {"X-Admin-Token": settings.admin_token}
A_SECRET = "DRILL_A_ONLY_CUSTOMER_WORDS"
A_REF = "drill-a-thread"


@pytest.fixture(scope="module")
def world(api):
    suffix = uuid.uuid4().hex[:8]

    async def _setup(conn):
        cid = {}
        for label in ("a", "b"):
            cid[label] = await conn.fetchval(
                "INSERT INTO clients (slug, name, api_key) VALUES ($1, $2, $3) RETURNING id",
                f"drill-{label}-{suffix}", f"drill {label} {suffix}", f"drill-{label}-{suffix}")
        a, b = cid["a"], cid["b"]

        async def job(filename, duration, minutes_ago):
            return await conn.fetchval(
                "INSERT INTO audio_jobs (client_id, principal_type, filename, duration_s, "
                "language, created_at) VALUES ($1, 'tenant', $2, $3, 'ka', "
                "now() - make_interval(mins => $4)) RETURNING id",
                a, filename, duration, minutes_ago)

        job1 = await job("drill_call_1.wav", 61.5, 120)
        job2 = await job("drill-other.mp3", 30.0, 110)

        async def summary(job_ids, minutes_ago):
            return await conn.fetchval(
                "INSERT INTO call_summaries (principal_type, client_id, job_ids, summary, "
                "created_at) VALUES ('tenant', $1, $2::uuid[], '{}'::jsonb, "
                "now() - make_interval(mins => $3)) RETURNING id",
                a, job_ids, minutes_ago)

        single = await summary([job1], 100)
        multi = await summary([job1, job2], 90)
        multi_free = await summary([job2, job1], 80)   # no usage row: listed with zero totals

        conv = await conn.fetchval(
            "INSERT INTO chat_conversations (client_id, external_ref, channel, locale, subject, "
            "created_at) VALUES ($1, $2, 'web', 'ka', 'refund question', "
            "now() - interval '60 minutes') RETURNING id", a, f"{A_REF}-{suffix}")

        async def turn(ref, role, content, minutes_ago):
            return await conn.fetchval(
                "INSERT INTO chat_turns (client_id, conversation_id, turn_ref, role, content, "
                "grounded, created_at) VALUES ($1, $2, $3, $4, $5, true, "
                "now() - make_interval(mins => $6)) RETURNING id",
                a, conv, f"{ref}-{suffix}", role, content, minutes_ago)

        t1 = await turn("t1", "customer", A_SECRET + " " + "x" * 1200, 59)
        t2 = await turn("t2", "bot", "an answer with no calls of its own", 58)
        t3 = await turn("t3", "customer", "second question", 57)

        async def use(client, feature, minutes_ago, *, model="claude-x", provider=None,
                      capability=None, tokens=(0, 0), audio=None, job_id=None,
                      conversation_id=None, turn_id=None, summary_id=None, ok=True):
            return await conn.fetchval(
                "INSERT INTO llm_usage (client_id, feature, model, provider, capability, "
                "input_tokens, output_tokens, audio_seconds, job_id, conversation_id, turn_id, "
                "summary_id, ok, latency_ms, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,"
                "$10,$11,$12,$13,100, now() - make_interval(mins => $14)) RETURNING id",
                client, feature, model, provider, capability, tokens[0], tokens[1], audio,
                job_id, conversation_id, turn_id, summary_id, ok, minutes_ago)

        u = {}
        # Recording 1: every analyser, oldest first.
        u["transcribe1"] = await use(a, "transcribe", 50, model="scribe_v1",
                                     provider="elevenlabs", capability="stt", audio=61.5,
                                     job_id=job1)
        u["analysis1"] = await use(a, "analysis", 49, tokens=(100, 50), job_id=job1)
        u["claims1"] = await use(a, "factcheck_claims", 48, tokens=(200, 20), job_id=job1)
        u["verdict1"] = await use(a, "factcheck_verdict", 47, tokens=(300, 30), job_id=job1,
                                  ok=False)
        u["semantic1"] = await use(a, "semantic_text", 46, model="gemini-2.5-flash",
                                   provider="gemini", tokens=(40, 10), job_id=job1)
        u["summary1"] = await use(a, "summarise", 45, tokens=(10, 5), job_id=job1,
                                  summary_id=single)
        # Recording 2: a bigger fact-check, so the analyser sort has something to order.
        u["claims2"] = await use(a, "factcheck_claims", 44, tokens=(1000, 100), job_id=job2)
        # The multi-recording summary belongs to no single recording.
        u["multi"] = await use(a, "summarise", 43, tokens=(70, 30), summary_id=multi)
        # The conversation: two calls on the first question, none on the bot's turn, one on the
        # second question, one with no turn at all.
        u["triage"] = await use(a, "triage", 40, tokens=(20, 5), conversation_id=conv,
                                turn_id=t1)
        u["autopilot"] = await use(a, "autopilot", 39, tokens=(100, 40), conversation_id=conv,
                                   turn_id=t1)
        u["copilot"] = await use(a, "copilot", 38, tokens=(50, 10), conversation_id=conv,
                                 turn_id=t3)
        u["handoff"] = await use(a, "handoff", 37, tokens=(30, 10), conversation_id=conv)
        # Workspace B's row naming A's recording, conversation and turn.
        u["b_stray"] = await use(b, "autopilot", 36, tokens=(7, 3), job_id=job1,
                                 conversation_id=conv, turn_id=t1)
        return {"a": str(a), "b": str(b), "job1": str(job1), "job2": str(job2),
                "single": str(single), "multi": str(multi), "multi_free": str(multi_free),
                "conv": str(conv), "t1": str(t1), "t2": str(t2), "t3": str(t3),
                "usage": {k: str(v) for k, v in u.items()}, "suffix": suffix}

    data = sql(_setup)
    try:
        yield data
    finally:
        ids = [uuid.UUID(data["a"]), uuid.UUID(data["b"])]

        async def _cleanup(conn):
            # llm_usage and audio_jobs only SET NULL on a client delete, so they go first;
            # summaries and the chat rows cascade with the clients.
            await conn.execute("DELETE FROM llm_usage WHERE client_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM audio_jobs WHERE client_id = ANY($1::uuid[])", ids)
            await conn.execute("DELETE FROM clients WHERE id = ANY($1::uuid[])", ids)
        sql(_cleanup)


def _get(api, path, **params):
    r = api.get(path, headers=ADMIN, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------------------------
def test_recordings_group_subtotals(api, world):
    body = _get(api, "/admin/usage/recordings", client_id=world["a"])
    assert body["total"] == 2 and body["sort"] == "last_used" and body["dir"] == "desc"
    rows = {r["job_id"]: r for r in body["rows"]}
    r1 = rows[world["job1"]]
    assert r1["filename"] == "drill_call_1.wav" and r1["language"] == "ka"
    assert r1["duration_s"] == 61.5 and r1["created_at"]
    assert r1["tenant_name"].startswith("drill a")

    g = r1["groups"]
    assert list(g) == ["transcription", "analysis", "factcheck", "sentiment", "summary"]
    assert g["transcription"]["audio_seconds"] == 61.5
    assert g["transcription"]["total_tokens"] == 0 and g["transcription"]["calls"] == 1
    assert g["transcription"]["models"] == ["elevenlabs/scribe_v1"]
    assert g["factcheck"] == {"total_tokens": 550, "input_tokens": 500, "output_tokens": 50,
                              "calls": 2, "failed": 1, "audio_seconds": 0.0, "characters": 0,
                              "models": ["anthropic/claude-x"]}
    assert g["sentiment"]["total_tokens"] == 50
    assert g["sentiment"]["models"] == ["gemini/gemini-2.5-flash"]
    assert g["summary"]["total_tokens"] == 15   # the one-recording summary counts here
    assert r1["models"] == ["anthropic/claude-x", "elevenlabs/scribe_v1",
                            "gemini/gemini-2.5-flash"]
    t = r1["total"]
    assert t["calls"] == 6 and t["failed"] == 1 and t["total_tokens"] == 150 + 550 + 50 + 15
    assert t["audio_seconds"] == 61.5 and t["first_used"] and t["last_used"]

    # Filters narrow the calls counted, not just the rows.
    only_fc = _get(api, "/admin/usage/recordings", client_id=world["a"], group="factcheck")
    by_id = {r["job_id"]: r for r in only_fc["rows"]}
    assert list(by_id[world["job1"]]["groups"]) == ["factcheck"]
    assert by_id[world["job1"]]["total"]["total_tokens"] == 550


def test_recordings_sort_by_group(api, world):
    asc = _get(api, "/admin/usage/recordings", client_id=world["a"], sort="group:factcheck",
               dir="asc")
    assert asc["sort"] == "group:factcheck" and asc["dir"] == "asc"
    assert [r["job_id"] for r in asc["rows"]] == [world["job1"], world["job2"]]
    desc = _get(api, "/admin/usage/recordings", client_id=world["a"], sort="group:factcheck",
                dir="desc")
    assert [r["job_id"] for r in desc["rows"]] == [world["job2"], world["job1"]]
    # An unknown sort key falls back to the default rather than reaching SQL.
    odd = _get(api, "/admin/usage/recordings", client_id=world["a"], sort="1;drop table x")
    assert odd["sort"] == "last_used"
    paged = _get(api, "/admin/usage/recordings", client_id=world["a"], sort="group:factcheck",
                 dir="desc", limit=1, offset=1)
    assert paged["total"] == 2 and [r["job_id"] for r in paged["rows"]] == [world["job1"]]


def test_recordings_search_filename(api, world):
    hit = _get(api, "/admin/usage/recordings", client_id=world["a"], q="CALL_1")
    assert [r["job_id"] for r in hit["rows"]] == [world["job1"]] and hit["total"] == 1
    # `_` is literal: unescaped it would match the "l-o" in drill-other.mp3.
    assert _get(api, "/admin/usage/recordings", client_id=world["a"], q="l_o")["total"] == 0
    assert _get(api, "/admin/usage/recordings", client_id=world["a"], q="%")["total"] == 0
    # Filter parameters and the search parameter number their placeholders together.
    both = _get(api, "/admin/usage/recordings", client_id=world["a"], provider="elevenlabs",
                status="ok", q="call_1", sort="group:transcription")
    assert [r["job_id"] for r in both["rows"]] == [world["job1"]]
    assert list(both["rows"][0]["groups"]) == ["transcription"]


def test_recording_detail(api, world):
    body = _get(api, f"/admin/usage/recordings/{world['job1']}")
    rec = body["recording"]
    assert rec["job_id"] == world["job1"] and rec["client_id"] == world["a"]
    assert rec["filename"] == "drill_call_1.wav" and rec["duration_s"] == 61.5
    assert [g["group"] for g in body["groups"]] == [
        "transcription", "analysis", "factcheck", "sentiment", "summary", "bot"]
    fc = next(g for g in body["groups"] if g["group"] == "factcheck")
    assert fc["total_tokens"] == 550 and fc["calls"] == 2 and fc["models"] == [
        "anthropic/claude-x"]

    u = world["usage"]
    assert [c["id"] for c in body["calls"]] == [
        u["transcribe1"], u["analysis1"], u["claims1"], u["verdict1"], u["semantic1"],
        u["summary1"], u["b_stray"]]
    first = body["calls"][0]
    assert first["group"] == "transcription" and first["capability"] == "stt"
    assert first["provider"] == "elevenlabs" and first["audio_seconds"] == 61.5
    assert first["filename"] == "drill_call_1.wav" and first["total_tokens"] == 0
    assert body["calls"][1]["provider"] == "anthropic"   # NULL provider on a text call
    assert body["calls"][1]["capability"] == "llm"
    assert body["total"]["calls"] == 7

    # Only the summaries of several recordings, in order, with zero totals when unmetered.
    sums = body["summaries"]
    assert [s["summary_id"] for s in sums] == [world["multi"], world["multi_free"]]
    assert sums[0]["job_count"] == 2 and sums[0]["total_tokens"] == 100
    assert sums[0]["calls"] == 1
    assert sums[1]["calls"] == 0 and sums[1]["total_tokens"] == 0


# ---------------------------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------------------------
def test_conversations_list(api, world):
    body = _get(api, "/admin/usage/conversations", client_id=world["a"])
    assert body["total"] == 1
    row = body["rows"][0]
    assert row["conversation_id"] == world["conv"]
    assert row["external_ref"] == f"{A_REF}-{world['suffix']}"
    assert row["channel"] == "web" and row["locale"] == "ka" and row["subject"]
    assert row["turns"] == 3
    assert row["questions"] == 2          # t1 and t3; the turnless handoff is not a question
    assert row["total"]["calls"] == 4 and row["total"]["total_tokens"] == 25 + 140 + 60 + 40
    assert set(row["features"]) == {"triage", "autopilot", "copilot", "handoff"}
    assert row["features"]["autopilot"] == {"total_tokens": 140, "input_tokens": 100,
                                            "output_tokens": 40, "calls": 1, "failed": 0}
    assert row["models"] == ["anthropic/claude-x"]

    assert _get(api, "/admin/usage/conversations", client_id=world["a"],
                q="REFUND")["total"] == 1
    assert _get(api, "/admin/usage/conversations", client_id=world["a"],
                channel="whatsapp")["total"] == 0
    narrowed = _get(api, "/admin/usage/conversations", client_id=world["a"], group="bot",
                    q="refund", channel="web", sort="turns", dir="asc")
    assert narrowed["total"] == 1
    assert set(narrowed["rows"][0]["features"]) == {"triage", "autopilot", "handoff"}
    assert narrowed["rows"][0]["questions"] == 1
    for key in ("turns", "questions", "channel", "tenant", "created_at", "calls"):
        assert _get(api, "/admin/usage/conversations", client_id=world["a"],
                    sort=key)["sort"] == key


def test_conversation_detail(api, world):
    body = _get(api, f"/admin/usage/conversations/{world['conv']}")
    conv = body["conversation"]
    assert conv["conversation_id"] == world["conv"] and conv["client_id"] == world["a"]
    turns = body["turns"]
    assert [t["turn_id"] for t in turns] == [world["t1"], world["t2"], world["t3"]]
    u = world["usage"]

    t1, t2, t3 = turns
    assert t1["role"] == "customer" and len(t1["content"]) == 1000 and t1["grounded"] is True
    assert [c["id"] for c in t1["calls"]] == [u["triage"], u["autopilot"]]
    assert t1["total"]["calls"] == 2 and t1["total"]["total_tokens"] == 165
    assert t1["calls"][0]["turn_role"] == "customer"
    assert len(t1["calls"][0]["turn_preview"]) == 160
    assert t1["calls"][0]["conversation_ref"] == f"{A_REF}-{world['suffix']}"

    assert t2["calls"] == [] and t2["total"]["calls"] == 0 and t2["total"]["last_used"] is None
    assert [c["id"] for c in t3["calls"]] == [u["copilot"]]

    # No turn, or another workspace's row naming this conversation: unattached.
    assert [c["id"] for c in body["unattached"]] == [u["handoff"], u["b_stray"]]
    assert body["total"]["calls"] == 5


def test_other_workspace_row_never_borrows_tenant_data(api, world):
    """B's usage row names A's recording, conversation and turn. Every client-matched join
    must come back NULL for it — and B's list rows must not carry a word of A's."""
    recs = api.get("/admin/usage/recordings", headers=ADMIN, params={"client_id": world["b"]})
    assert recs.status_code == 200
    (rec,) = recs.json()["rows"]
    assert rec["job_id"] == world["job1"] and rec["client_id"] == world["b"]
    assert rec["filename"] is None and rec["duration_s"] is None and rec["language"] is None

    convs = api.get("/admin/usage/conversations", headers=ADMIN,
                    params={"client_id": world["b"]})
    assert convs.status_code == 200
    (row,) = convs.json()["rows"]
    assert row["conversation_id"] == world["conv"] and row["client_id"] == world["b"]
    assert row["external_ref"] is None and row["subject"] is None and row["turns"] == 0
    for resp in (recs, convs):
        assert A_SECRET not in resp.text and A_REF not in resp.text

    # B's search must not find A's conversation through A's reference.
    assert _get(api, "/admin/usage/conversations", client_id=world["b"],
                q=A_REF)["total"] == 0

    detail = _get(api, f"/admin/usage/conversations/{world['conv']}")
    stray = next(c for c in detail["unattached"] if c["id"] == world["usage"]["b_stray"])
    assert stray["conversation_ref"] is None and stray["channel"] is None
    assert stray["turn_role"] is None and stray["turn_preview"] is None
    assert stray["filename"] is None and stray["client_id"] == world["b"]

    # The call log matches the recording's owner too, so the All calls tab agrees with the
    # Recordings tab: no filename for B's row, and B's search cannot find it by A's filename.
    calls = _get(api, "/admin/usage/calls", client_id=world["b"], limit=200)
    assert calls["rows"] and all(c["filename"] is None for c in calls["rows"])
    assert _get(api, "/admin/usage/calls", client_id=world["b"], q="drill_call_1")["total"] == 0


# ---------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------
def test_unknown_malformed_and_unauthorised(api, world):
    missing = str(uuid.uuid4())
    for path in ("recordings", "conversations"):
        assert api.get(f"/admin/usage/{path}/{missing}", headers=ADMIN).status_code == 404
        assert api.get(f"/admin/usage/{path}/not-a-uuid", headers=ADMIN).status_code == 400
        assert api.get(f"/admin/usage/{path}").status_code == 401
        assert api.get(f"/admin/usage/{path}/{world['job1']}").status_code == 401
