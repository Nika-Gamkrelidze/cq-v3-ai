"""Money on the usage page: each call costed at the price in force WHEN IT RAN, plus markup.

What is pinned here is what an invoice argument turns on — a price change never re-prices last
month, an exact model beats the provider's catch-all, a call with no price is reported as
unpriced rather than free, the customer's own key and our own server cost us nothing, and the
markup (default, per-workspace override, and the end of an override) is applied by date too.

Prices are global, so every row here uses a provider name unique to this run; the calls belong
to a throwaway workspace and every report request filters by it.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services import pricing
from conftest import sql

ADMIN = {"X-Admin-Token": settings.admin_token}
NOW = datetime.now(timezone.utc)


def _day(n: int) -> str:
    """`n` days ago as an ISO timestamp."""
    return (NOW - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# Validation (no database)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("body,msg", [
    ({"provider": "x", "model": "m"}, "at least one price"),
    ({"provider": "x", "model": "m", "input_per_mtok": -1}, "negative"),
    ({"provider": "x", "model": "m", "input_per_mtok": "abc"}, "number"),
    ({"provider": "x", "model": "m", "input_per_mtok": True}, "number"),
    ({"provider": "x", "model": "m", "input_per_mtok": 1, "capability": "video"}, "kind"),
    ({"provider": "", "model": "m", "input_per_mtok": 1}, "provider"),
    ({"provider": "x", "model": "m", "input_per_mtok": 1, "effective_from": "next week"}, "date"),
    ({"provider": "x", "model": "m", "input_per_mtok": 1, "effective_from": "1899-01-01"}, "range"),
    ({"provider": "x", "model": "m", "input_per_mtok": 1e12}, "large"),
])
def test_a_bad_price_is_refused_with_a_reason(body, msg):
    with pytest.raises(pricing.PricingError, match=msg):
        pricing.clean_price(body)


def test_a_price_is_normalised():
    p = pricing.clean_price({"provider": " Anthropic ", "model": "claude-x", "input_per_mtok": "3",
                             "output_per_mtok": 15, "effective_from": "2026-10-01"})
    assert p["provider"] == "anthropic" and p["capability"] is None
    assert p["effective_from"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert str(p["input_per_mtok"]) == "3" and p["audio_per_hour"] is None


def test_the_default_markup_needs_a_number_but_an_override_may_end():
    with pytest.raises(pricing.PricingError, match="percentage"):
        pricing.clean_markup({"pct": None})
    end = pricing.clean_markup({"client_id": str(uuid.uuid4()), "pct": None})
    assert end["pct"] is None


# ---------------------------------------------------------------------------
# Costing (database)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world(api):
    sfx = uuid.uuid4().hex[:8]
    prov, other = f"prov{sfx}", f"other{sfx}"
    cid = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name) VALUES ($1,$2) RETURNING id", f"price-{sfx}", f"Price {sfx}"))
    cid = str(cid)

    def call(label, days, **kw):
        row = {"model": "m-exact", "provider": prov, "capability": "llm", "input_tokens": None,
               "output_tokens": None, "cache_read_tokens": None, "cache_creation_tokens": None,
               "audio_seconds": None, "characters": None, "byo": False}
        row.update(kw)
        return label, days, row

    calls = [
        call("old", 5, input_tokens=1_000_000),                      # v1 price in force
        call("new", 1, input_tokens=1_000_000),                      # v2 price in force
        call("wild", 1, model="m-other", output_tokens=1_000_000),   # only the '*' row matches
        call("cache", 1, cache_read_tokens=1_000_000, cache_creation_tokens=1_000_000),
        # No exact row for this model: the provider's speech rows (by kind) beat its '*' any-kind row.
        call("audio", 1, model="m-speech", capability="stt", audio_seconds=1800.0),
        call("chars", 1, model="m-speech", capability="tts", characters=2000),
        # Priced row exists but bills tokens only: a call that consumed only audio is UNPRICED.
        call("gap", 1, capability="stt", audio_seconds=60.0),
        call("byo", 1, input_tokens=1_000_000, byo=True),
        call("local", 1, provider="local", model="tone", capability="voice_tone", audio_seconds=9.0),
        call("none", 1, provider=other, model="nobody-priced-me", input_tokens=10),
        call("before", 20, input_tokens=1_000_000),                  # before any price existed
    ]

    async def seed(conn):
        ids = {}
        for label, days, r in calls:
            ids[label] = str(await conn.fetchval("""
                INSERT INTO llm_usage (client_id, feature, model, provider, capability,
                    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                    audio_seconds, characters, byo, ok, latency_ms, created_at)
                VALUES ($1,'analysis',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,true,10,
                        now() - $12::interval) RETURNING id""",
                cid, r["model"], r["provider"], r["capability"], r["input_tokens"],
                r["output_tokens"], r["cache_read_tokens"], r["cache_creation_tokens"],
                r["audio_seconds"], r["characters"], r["byo"], timedelta(days=days)))
        return ids

    ids = sql(seed)

    def post(path, body):
        r = api.post(f"/admin/usage/{path}", json=body, headers=ADMIN)
        assert r.status_code == 201, r.text
        return r.json()

    post("prices", {"provider": prov, "model": "m-exact", "input_per_mtok": 1, "output_per_mtok": 4,
                    "effective_from": _day(10)})
    v2 = post("prices", {"provider": prov, "model": "m-exact", "input_per_mtok": 2,
                         "output_per_mtok": 8, "cache_read_per_mtok": 0.5, "effective_from": _day(2)})
    post("prices", {"provider": prov, "model": "*", "input_per_mtok": 100, "output_per_mtok": 3,
                    "effective_from": _day(10)})
    # Same model id billed per hour of audio for speech-to-text only.
    post("prices", {"provider": prov, "model": "*", "capability": "stt", "audio_per_hour": 0.4,
                    "effective_from": _day(10)})
    post("prices", {"provider": prov, "model": "*", "capability": "tts", "chars_per_k": 0.3,
                    "effective_from": _day(10)})
    # Markup: this workspace 20% from 10 days ago, back to the default (50%) from 3 days ago.
    post("markups", {"client_id": cid, "pct": 20, "effective_from": _day(10)})
    post("markups", {"client_id": cid, "pct": None, "effective_from": _day(3)})
    default = post("markups", {"pct": 50, "effective_from": "2001-01-01"})

    try:
        yield {"cid": cid, "ids": ids, "prov": prov, "other": other, "v2": v2}
    finally:
        async def clean(conn):
            await conn.execute("DELETE FROM ai_prices WHERE provider = $1", prov)
            await conn.execute("DELETE FROM ai_markups WHERE id = $1::uuid", default["id"])
            await conn.execute("DELETE FROM llm_usage WHERE client_id = $1::uuid", cid)
            await conn.execute("DELETE FROM clients WHERE id = $1::uuid", cid)
        sql(clean)


def _calls(api, world, **params) -> dict:
    r = api.get("/admin/usage/calls", headers=ADMIN,
                params={"client_id": world["cid"], "window": "90d", "limit": 200, **params})
    assert r.status_code == 200, r.text
    return {row["id"]: row for row in r.json()["rows"]}


def test_each_call_is_costed_at_the_price_in_force_when_it_ran(api, world):
    rows = _calls(api, world)
    ids = world["ids"]
    by = {label: rows[i] for label, i in ids.items()}
    assert by["old"]["cost_usd"] == pytest.approx(1.0)        # v1, 1M input @ $1
    assert by["new"]["cost_usd"] == pytest.approx(2.0)        # v2, 1M input @ $2
    assert by["before"]["cost_usd"] is None                   # no price had started yet
    assert by["new"]["price_id"] == world["v2"]["id"]


def test_exact_model_and_kind_beat_the_catch_all_and_units_are_right(api, world):
    by = {label: _calls(api, world)[i] for label, i in world["ids"].items()}
    assert by["wild"]["cost_usd"] == pytest.approx(3.0)       # '*' row: 1M output @ $3
    # Cache read has its own price; cache write falls back to the input price.
    assert by["cache"]["cost_usd"] == pytest.approx(0.5 + 2.0)
    assert by["audio"]["cost_usd"] == pytest.approx(0.2)      # 30 min @ $0.40/hour
    assert by["chars"]["cost_usd"] == pytest.approx(0.6)      # 2000 chars @ $0.30/1k


def test_own_key_and_local_cost_us_nothing_and_unpriced_is_not_free(api, world):
    by = {label: _calls(api, world)[i] for label, i in world["ids"].items()}
    assert by["byo"]["cost_usd"] == 0 and by["byo"]["billed_usd"] == 0
    assert by["local"]["cost_usd"] == 0
    assert by["none"]["cost_usd"] is None and by["none"]["billed_usd"] is None
    assert by["gap"]["cost_usd"] is None


def test_markup_follows_the_override_and_its_end_by_date(api, world):
    by = {label: _calls(api, world)[i] for label, i in world["ids"].items()}
    assert by["old"]["markup_pct"] == pytest.approx(20)        # override in force 5 days ago
    assert by["old"]["billed_usd"] == pytest.approx(1.2)
    assert by["new"]["markup_pct"] == pytest.approx(50)        # override ended: the default
    assert by["new"]["billed_usd"] == pytest.approx(3.0)


def test_totals_add_up_and_count_the_unpriced(api, world):
    r = api.get("/admin/usage/overview", headers=ADMIN,
                params={"client_id": world["cid"], "window": "90d"})
    assert r.status_code == 200, r.text
    tot = r.json()["total"]
    costs = [c["cost_usd"] for c in _calls(api, world).values()]
    assert tot["cost_usd"] == pytest.approx(sum(c for c in costs if c is not None))
    assert tot["unpriced_calls"] == sum(1 for c in costs if c is None) == 3
    by_tenant = r.json()["by_tenant"]
    assert by_tenant[0]["billed_usd"] == pytest.approx(tot["billed_usd"])
    # The series carries money too, and it sums to the total.
    assert sum(p["cost_usd"] for p in r.json()["series"]) == pytest.approx(tot["cost_usd"])


def test_lists_sort_by_cost(api, world):
    r = api.get("/admin/usage/calls", headers=ADMIN,
                params={"client_id": world["cid"], "window": "90d", "sort": "cost_usd", "dir": "desc"})
    costs = [row["cost_usd"] for row in r.json()["rows"]]
    priced = [c for c in costs if c is not None]
    assert priced == sorted(priced, reverse=True) and costs[-1] is None  # NULLS LAST


def test_the_pricing_page_lists_what_still_needs_a_price(api, world):
    r = api.get("/admin/usage/pricing", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    todo = {(u["provider"], u["model"]) for u in body["unpriced"]}
    assert (world["other"], "nobody-priced-me") in todo
    assert (world["prov"], "m-exact") in todo     # the call from before any price existed
    assert any(p["provider"] == world["prov"] for p in body["prices"])


def test_versions_are_unique_and_edits_reprice(api, world):
    dup = {"provider": world["prov"], "model": "m-exact", "input_per_mtok": 9,
           "effective_from": world["v2"]["effective_from"]}
    assert api.post("/admin/usage/prices", json=dup, headers=ADMIN).status_code == 409
    assert api.post("/admin/usage/prices", json={**dup, "input_per_mtok": -1},
                    headers=ADMIN).status_code == 400
    assert api.put(f"/admin/usage/prices/{uuid.uuid4()}", json=dup, headers=ADMIN).status_code == 404
    assert api.delete(f"/admin/usage/prices/{uuid.uuid4()}", headers=ADMIN).status_code == 404

    # Correcting the v2 price re-prices the calls it covers, and only those.
    v2 = world["v2"]
    fixed = {**{k: v2[k] for k in ("provider", "model", "capability", "effective_from")},
             "input_per_mtok": 2.5, "output_per_mtok": 8, "cache_read_per_mtok": 0.5}
    r = api.put(f"/admin/usage/prices/{v2['id']}", json=fixed, headers=ADMIN)
    assert r.status_code == 200, r.text
    try:
        by = {label: _calls(api, world)[i] for label, i in world["ids"].items()}
        assert by["new"]["cost_usd"] == pytest.approx(2.5)
        assert by["old"]["cost_usd"] == pytest.approx(1.0)
    finally:
        api.put(f"/admin/usage/prices/{v2['id']}", json={**fixed, "input_per_mtok": 2}, headers=ADMIN)


def test_pricing_is_superadmin_only(api, world):
    for path in ("pricing",):
        assert api.get(f"/admin/usage/{path}").status_code == 401
    assert api.post("/admin/usage/prices", json={}).status_code == 401
    assert api.post("/admin/usage/markups", json={"pct": 1},
                    headers={"X-Admin-Token": "guess"}).status_code == 401


def test_a_call_recorded_after_the_price_is_costed_as_it_lands(api, world):
    """The trigger: no re-price runs for this row, the insert itself costs it."""
    async def insert(conn):
        return str(await conn.fetchval("""
            INSERT INTO llm_usage (client_id, feature, model, provider, capability, input_tokens,
                                   ok, latency_ms)
            VALUES ($1, 'analysis', 'm-exact', $2, 'llm', 500000, true, 5) RETURNING id""",
            world["cid"], world["prov"]))
    new_id = sql(insert)
    row = _calls(api, world)[new_id]
    assert row["cost_usd"] == pytest.approx(1.0)          # 0.5M input @ $2 (v2)
    assert row["billed_usd"] == pytest.approx(1.5)        # default 50% from 3 days ago
