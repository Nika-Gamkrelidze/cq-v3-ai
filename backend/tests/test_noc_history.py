"""NOC history (`services/noc_history`, `routers/noc_history`): what is stored, what the date picker
and the timeline read back, and the retention rule.

Rows are written in 2001 so real data never collides, and removed in `finally`. The router is not
registered in main.py by this change, so it is tested on a throwaway FastAPI app; the service reads
through `noc_history.pool`, replaced here by a pool that opens a fresh connection per use (the
real pool is bound to the api fixture's event loop — see conftest).
"""
import contextlib
import json
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import noc_history as router_mod
from app.services import noc_history as nh
from conftest import sql

ADMIN = {"X-Admin-Token": settings.admin_token}
D = datetime(2001, 3, 4, 10, 0, 0, tzinfo=timezone.utc)


class _FreshPool:
    @contextlib.asynccontextmanager
    async def acquire(self):
        conn = await asyncpg.connect(settings.database_url)
        try:
            yield conn
        finally:
            await conn.close()


@pytest.fixture(autouse=True)
def fresh_pool(monkeypatch):
    monkeypatch.setattr(nh, "pool", lambda: _FreshPool())
    monkeypatch.setattr(nh, "_last_at", None)
    monkeypatch.setattr(nh, "_last_status", None)


@pytest.fixture
def client(api):                       # api: the migrations ran
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


def run(coro_fn):
    import asyncio
    return asyncio.run(coro_fn())


def snap(ts=D, status="ok", cpu=10.0, probes=(), ai=(), checks=(), workers=(), counts=None):
    cs = [{"key": "cpu", "group": "host", "status": "ok", "value": cpu, "unit": "pct", "detail": None, "ref": None},
          {"key": "disk", "group": "host", "status": "ok", "value": 40, "unit": "pct", "detail": None, "ref": "/"},
          {"key": "disk", "group": "host", "status": "ok", "value": 99, "unit": "pct", "detail": None, "ref": "/data"},
          *checks]
    return {"now": ts, "status": status, "counts": counts or {"crit": 0, "warn": 0, "ok": len(cs), "unknown": 0},
            "checks": cs, "ai": list(ai), "probes": list(probes), "workers": list(workers),
            "sampler_interval_s": 10, "interval_s": 10}


def probe(key, p50=12.0, loss=0.0, status="ok"):
    return {"key": key, "host": key, "kind": "provider", "p50_ms": p50, "loss_pct": loss, "status": status,
            "history": [[1, 2.0], [3, None]]}


def ai(name, cid="c1", provider="anthropic", status="ok", calls=5, fails=1, p50=300.0):
    return {"id": cid, "name": name, "provider": provider, "status": status,
            "calls_15m": calls, "fails_15m": fails, "p50_ms": p50}


def wipe():
    sql(lambda c: c.execute("DELETE FROM noc_snapshots WHERE ts < '2002-01-01'"))


# --- pure ------------------------------------------------------------------
def test_compact_empties_probe_history_and_keeps_the_rest_without_mutating():
    s = snap(probes=[probe("a")], checks=[])
    s["checks"][0]["value"] = None
    c = nh.compact(s)
    assert c["probes"][0]["history"] == [] and s["probes"][0]["history"] == [[1, 2.0], [3, None]]
    assert c["checks"][0]["value"] is None and c["probes"][0]["p50_ms"] == 12.0
    assert {k: v for k, v in c.items() if k != "probes"} == {k: v for k, v in s.items() if k != "probes"}


def test_bad_list_names_every_warn_and_crit_item_only():
    s = snap(checks=[{"key": "disk", "status": "crit", "ref": "/"}, {"key": "swap", "status": "warn", "ref": None},
                     {"key": "mem", "status": "unknown", "ref": None}],
             ai=[ai("Anthropic main", status="warn"), ai("ok one")],
             probes=[probe("api.anthropic.com", status="warn")],
             workers=[{"duty": "curation_pass", "status": "crit"}, {"duty": "fine", "status": "ok"}])
    assert nh.bad_list(s) == ["crit:check:disk:/", "warn:check:swap", "warn:ai:Anthropic main",
                              "warn:probe:api.anthropic.com", "crit:worker:curation_pass"]
    assert nh.bad_list({}) == []


# --- record ----------------------------------------------------------------
def test_record_throttles_but_stores_a_status_change_at_once():
    t0 = datetime(2001, 3, 4, 10, 0, 0, tzinfo=timezone.utc)
    try:
        async def go():
            await nh.record(snap(ts=t0))                                         # first call always stores
            await nh.record(snap(ts=t0 + timedelta(seconds=10)))                 # inside the window, same status
            await nh.record(snap(ts=t0 + timedelta(seconds=20), status="crit",
                                 counts={"crit": 2, "warn": 1, "ok": 0, "unknown": 0},
                                 checks=[{"key": "swap", "status": "crit", "ref": None}]))   # changed -> stored
        run(go)
        rows = sql(lambda c: c.fetch("SELECT * FROM noc_snapshots WHERE ts < '2002-01-01' ORDER BY ts"))
        assert [r["status"] for r in rows] == ["ok", "crit"]
        assert (rows[1]["crit"], rows[1]["warn"], list(rows[1]["bad"])) == (2, 1, ["crit:check:swap"])
        stored = json.loads(rows[1]["payload"])
        assert stored["now"].startswith("2001-03-04T10:00:20")
    finally:
        wipe()


def test_record_never_raises_and_retries_after_a_failed_insert(monkeypatch):
    class Broken:
        def acquire(self):
            raise RuntimeError("db down")
    monkeypatch.setattr(nh, "pool", lambda: Broken())
    run(lambda: nh.record(snap()))                                  # must not raise
    assert nh._last_at is None                                       # so the next cycle tries again


# --- days ------------------------------------------------------------------
def test_days_groups_by_local_day_and_counts(client):
    # 20:30 UTC is 00:30 the NEXT day in Tbilisi (UTC+4), 19:30 UTC is still the same local day.
    rows = [(datetime(2001, 3, 4, 19, 30, tzinfo=timezone.utc), "ok"),
            (datetime(2001, 3, 4, 20, 30, tzinfo=timezone.utc), "warn"),
            (datetime(2001, 3, 4, 21, 30, tzinfo=timezone.utc), "crit"),
            (datetime(2001, 3, 4, 22, 30, tzinfo=timezone.utc), "ok")]
    try:
        for ts, st in rows:
            sql(lambda c, ts=ts, st=st: c.execute(
                "INSERT INTO noc_snapshots (ts, status, payload) VALUES ($1, $2, '{}'::jsonb)", ts, st))
        got = run(lambda: nh.days("Asia/Tbilisi", 20000))
        by_day = {d["day"]: d for d in got if d["day"].startswith("2001")}
        assert by_day["2001-03-04"] == {"day": "2001-03-04", "status": "ok", "samples": 1, "crit": 0, "warn": 0}
        assert by_day["2001-03-05"] == {"day": "2001-03-05", "status": "crit", "samples": 3, "crit": 1, "warn": 1}
        utc = {d["day"]: d for d in run(lambda: nh.days("UTC", 20000)) if d["day"].startswith("2001")}
        assert utc["2001-03-04"]["samples"] == 4
        # endpoint: shape, ordering, clamps and the tz check
        r = client.get("/admin/noc/history/days?tz=Asia/Tbilisi&days=500", headers=ADMIN)
        assert r.status_code == 200 and r.json()["tz"] == "Asia/Tbilisi" and r.json()["retention_days"] == 30
        days = [d["day"] for d in r.json()["days"]]
        assert days == sorted(days)
        bad = client.get("/admin/noc/history/days?tz=Mars/Base", headers=ADMIN)
        assert bad.status_code == 400 and "time zone" in bad.json()["detail"]
    finally:
        wipe()


# --- history ---------------------------------------------------------------
def _store(*snaps):
    for s in snaps:
        sql(lambda c, s=s: c.execute(
            "INSERT INTO noc_snapshots (ts, status, crit, warn, bad, payload) VALUES ($1,$2,0,0,$3,$4::jsonb)",
            s["now"], s["status"], nh.bad_list(s), json.dumps(nh.compact(s), default=nh._iso)))


def test_history_series_align_with_samples_and_a_late_probe_gets_full_arrays(client):
    t = [D + timedelta(minutes=i) for i in range(3)]
    try:
        _store(snap(ts=t[0], cpu=11.0, probes=[probe("a", 10.0)], ai=[ai("Main", calls=3)]),
               snap(ts=t[1], cpu=12.0, probes=[probe("a", 11.0), probe("b", 50.0, 5.0)],
                    ai=[ai("Main", calls=4), ai("Late", cid=None, provider="openai")], status="warn",
                    checks=[{"key": "swap", "status": "warn", "ref": None, "value": 600}]),
               snap(ts=t[2], cpu=None, probes=[probe("a", None, None)]))
        r = client.get("/admin/noc/history", params={"from": "2001-03-04T09:59:00Z", "to": "2001-03-04T10:05:00"},
                       headers=ADMIN)
        assert r.status_code == 200, r.text
        b = r.json()
        s = b["series"]
        assert [x["status"] for x in b["samples"]] == ["ok", "warn", "ok"] and b["step_s"] == 60
        assert b["samples"][1]["bad"] == ["warn:check:swap"]
        assert s["ts"] == [int(x.timestamp() * 1000) for x in t]
        assert s["cpu"] == [11.0, 12.0, None] and s["disk"] == [40, 40, 40]       # disk is the "/" mount only
        assert s["swap_mb"] == [None, 600, None]
        assert s["probes"]["a"]["p50"] == [10.0, 11.0, None]
        assert s["probes"]["b"] == {"p50": [None, 50.0, None], "loss": [None, 5.0, None]}
        assert s["ai"]["c1"]["calls"] == [3, 4, None] and s["ai"]["c1"]["name"] == "Main"
        assert s["ai"]["legacy:openai"]["name"] == "Late" and s["ai"]["legacy:openai"]["calls"] == [None, 5, None]
        n = len(b["samples"])
        flat = [v for k, v in s.items() if isinstance(v, list)]
        flat += [a for p in s["probes"].values() for a in p.values()]
        flat += [a for p in s["ai"].values() for a in p.values() if isinstance(a, list)]
        assert all(len(a) == n for a in flat)
    finally:
        wipe()


def test_history_window_cap_and_bad_input(client):
    ok = client.get("/admin/noc/history", params={"from": "2001-03-04T00:00:00Z", "to": "2001-03-06T00:00:00Z"},
                    headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["series"]["ts"] == []
    over = client.get("/admin/noc/history", params={"from": "2001-03-04T00:00:00Z", "to": "2001-03-06T00:00:01Z"},
                      headers=ADMIN)
    assert over.status_code == 400 and "48" in over.json()["detail"]
    assert client.get("/admin/noc/history", params={"from": "yesterday", "to": "now"}, headers=ADMIN).status_code == 400
    assert client.get("/admin/noc/history", params={"from": "2001-03-05T00:00:00Z", "to": "2001-03-04T00:00:00Z"},
                      headers=ADMIN).status_code == 400


# --- snapshot_at -----------------------------------------------------------
def test_snapshot_at_returns_the_newest_row_within_ten_minutes(client):
    try:
        _store(snap(ts=D, cpu=1.0, probes=[probe("a")]), snap(ts=D + timedelta(minutes=5), cpu=2.0))
        hit = client.get("/admin/noc/snapshot", params={"at": "2001-03-04T10:07:00Z"}, headers=ADMIN)
        assert hit.status_code == 200 and hit.json()["ts"] == "2001-03-04T10:05:00+00:00"
        assert hit.json()["snapshot"]["checks"][0]["value"] == 2.0
        older = client.get("/admin/noc/snapshot", params={"at": "2001-03-04T10:04:00Z"}, headers=ADMIN).json()
        assert older["snapshot"]["probes"][0]["history"] == []                # stored compact
        assert client.get("/admin/noc/snapshot", params={"at": "2001-03-04T10:15:01Z"}, headers=ADMIN).status_code == 404
        miss = client.get("/admin/noc/snapshot", params={"at": "2001-03-04T09:59:59Z"}, headers=ADMIN)
        assert miss.status_code == 404 and miss.json() == {"detail": "No snapshot was stored near that time."}
    finally:
        wipe()


# --- purge -----------------------------------------------------------------
def test_purge_deletes_only_rows_older_than_the_cutoff():
    recent = datetime.now(timezone.utc) - timedelta(days=1, microseconds=7)
    try:
        _store(snap(ts=D), snap(ts=recent))
        assert run(lambda: nh.purge(30)) >= 1
        left = sql(lambda c: c.fetchval("SELECT count(*) FROM noc_snapshots WHERE ts = $1", recent))
        gone = sql(lambda c: c.fetchval("SELECT count(*) FROM noc_snapshots WHERE ts = $1", D))
        assert (left, gone) == (1, 0)
    finally:
        sql(lambda c: c.execute("DELETE FROM noc_snapshots WHERE ts = $1", recent))
        wipe()


# --- auth ------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/admin/noc/history/days", "/admin/noc/history?from=a&to=b",
                                  "/admin/noc/snapshot?at=2001-01-01T00:00:00Z"])
def test_every_route_refuses_without_the_admin_token(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Admin-Token": "guess"}).status_code == 401
