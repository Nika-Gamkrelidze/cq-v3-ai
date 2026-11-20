"""The NOC read (`services/noc`): the rules that decide a colour, the probe ring, the packet-loss
arithmetic, the worker heartbeat, and the endpoint against the real schema.

A NOC that is wrong in either direction is worse than none: a false red teaches the operator to
ignore it, a false green hides the outage. So the thresholds, the "not enough data is `unknown`,
never red" rule and the stale-vs-failing worker distinction are pinned here, as pure functions.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import worker
from app.config import settings
from app.services import health_metrics as hm
from app.services import noc
from conftest import sql

ADMIN = {"X-Admin-Token": settings.admin_token}


# --------------------------------------------------------------------------- #
# Status rules
# --------------------------------------------------------------------------- #
def test_level_reads_both_directions():
    assert noc.level(None, 80, 90) == noc.UNKNOWN
    assert [noc.level(v, 80, 90) for v in (79.9, 80, 89.9, 90)] == ["ok", "warn", "warn", "crit"]
    # fewer days left is worse: certificate expiry
    assert [noc.level(v, 21, 7, low_is_bad=True) for v in (60, 21, 8, 7, 0)] == ["ok", "warn", "warn", "crit", "crit"]


def test_worst_ignores_unknown_unless_that_is_all_there_is():
    assert noc.worst([]) == noc.UNKNOWN
    assert noc.worst(["unknown", "unknown"]) == noc.UNKNOWN
    assert noc.worst(["unknown", "ok"]) == noc.OK
    assert noc.worst(["ok", "warn", "unknown"]) == noc.WARN
    assert noc.worst(["warn", "crit", "ok"]) == noc.CRIT


# --------------------------------------------------------------------------- #
# Probe ring
# --------------------------------------------------------------------------- #
def _ring(results, step=30.0, now=10_000.0):
    """results oldest -> newest, each (ok, ms); the newest lands at `now`."""
    ring = noc.ProbeRing()
    for i, (ok, ms) in enumerate(results):
        ring.add("h", ok, ms, None if ok else "timeout", at=now - (len(results) - 1 - i) * step)
    return ring, now


def test_probe_stats_loss_percentiles_and_streak():
    ring, now = _ring([(True, 10 * (i + 1)) for i in range(8)] + [(False, None), (False, None)])
    s = ring.stats("h", at=now)
    assert s["n"] == 10 and s["loss_pct"] == 20.0 and s["consecutive_fail"] == 2
    assert s["p50_ms"] == 50.0 and s["p95_ms"] == 80.0 and s["last_error"] == "timeout"
    assert noc.probe_status(s) == noc.WARN                    # 20% loss, not yet three in a row


def test_three_failures_in_a_row_is_red_even_with_little_loss():
    ring, now = _ring([(True, 12)] * 40 + [(False, None)] * 3)
    s = ring.stats("h", at=now)
    assert s["loss_pct"] < 10 and noc.probe_status(s) == noc.CRIT


def test_too_few_samples_is_unknown_not_red():
    ring, now = _ring([(False, None), (False, None)])
    assert noc.probe_status(ring.stats("h", at=now)) == noc.UNKNOWN
    assert noc.probe_status(noc.ProbeRing().stats("missing")) == noc.UNKNOWN


def test_old_samples_leave_the_window_and_deactivated_targets_are_forgotten():
    ring = noc.ProbeRing()
    ring.add("h", False, None, "dns", at=time.time() - 3 * 3600)
    ring.add("h", True, 20.0)
    assert ring.stats("h")["loss_pct"] == 0.0                 # the failure is 3 h old
    ring.forget_except({"other"})
    assert not ring.has("h")


# --------------------------------------------------------------------------- #
# A provider's colour
# --------------------------------------------------------------------------- #
def _stat(calls, fails, last_ok=None):
    return {"calls_w": calls, "fails_w": fails, "last_ok_at": last_ok}


def _probe(loss, streak=0, n=20):
    return {"n": n, "loss_pct": loss, "consecutive_fail": streak}


def test_provider_failure_rate_needs_enough_calls_to_mean_anything():
    assert noc.ai_status(_stat(10, 6), _probe(0), None) == noc.CRIT         # 60%
    assert noc.ai_status(_stat(10, 3), _probe(0), None) == noc.WARN         # 30%
    assert noc.ai_status(_stat(2, 2), _probe(0), None) == noc.OK            # two calls: noise
    assert noc.ai_status(_stat(0, 0), _probe(0), None) == noc.OK            # idle, path fine
    assert noc.ai_status(_stat(0, 0), None, None) == noc.UNKNOWN            # idle, nothing measured


def test_a_dead_network_path_beats_healthy_traffic_and_a_failed_manual_test_only_warns():
    assert noc.ai_status(_stat(10, 0), _probe(100, streak=5), None) == noc.CRIT
    t = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    failed = {"ok": False, "at": t.isoformat()}
    assert noc.ai_status(_stat(0, 0, None), _probe(0), failed) == noc.WARN
    # something succeeded after the failed test: the test is history
    assert noc.ai_status(_stat(3, 0, t + timedelta(minutes=1)), _probe(0), failed) == noc.OK


def test_probe_target_follows_the_connection_not_just_the_provider():
    assert noc._host_of({"provider": "anthropic", "base_url": None}) == ("api.anthropic.com", 443)
    assert noc._host_of({"provider": "openai", "base_url": "https://gw.example.com:8443/v1"}) == ("gw.example.com", 8443)
    assert noc._host_of({"provider": "nobody"}) is None


# --------------------------------------------------------------------------- #
# Packet counters
# --------------------------------------------------------------------------- #
def test_packet_rates_per_second_and_no_negative_spike_on_a_counter_reset():
    assert hm.derive_per_s(None, (1, 2, 3), 10) is None
    assert hm.derive_per_s((100, 2, 5), (1100, 2, 15), 10) == (100.0, 0.0, 1.0)
    assert hm.derive_per_s((100, 2, 5), (50, 2, 5), 10) is None         # went backwards
    assert hm.derive_per_s((1, 1, 1), (2, 2, 2), 0) is None


def test_sampler_reports_drops_per_second_between_two_readings():
    t = {"now": 0.0}
    readings = iter([{"netp": (1000, 0, 0)}, {"netp": (2000, 5, 20)}])
    sampler = hm.HostSampler(clock=lambda: t["now"], read_counters=lambda: next(readings), media_root="")
    first = sampler.rates()
    assert first["net_pkts_s"] is None and first["net_drops_s"] is None
    t["now"] = 10.0
    second = sampler.rates()
    assert (second["net_pkts_s"], second["net_errs_s"], second["net_drops_s"]) == (100.0, 0.5, 2.0)


# --------------------------------------------------------------------------- #
# Worker heartbeat + the endpoint
# --------------------------------------------------------------------------- #
def test_heartbeat_keeps_the_last_error_but_a_later_success_clears_the_failing_state(api):  # api: migrations ran
    duty = "test-noc-heartbeat"
    try:
        sql(lambda c: c.execute(worker._HEARTBEAT_SQL, duty, 30.0, 5, "ValueError: boom"))
        failing = sql(lambda c: c.fetchrow("SELECT * FROM worker_heartbeat WHERE duty = $1", duty))
        assert failing["last_error"] == "ValueError: boom" and failing["last_ok_at"] is None
        sql(lambda c: c.execute(worker._HEARTBEAT_SQL, duty, 30.0, 7, None))
        ok = sql(lambda c: c.fetchrow("SELECT * FROM worker_heartbeat WHERE duty = $1", duty))
        assert ok["last_ok_at"] > ok["last_error_at"] and ok["last_error"] == "ValueError: boom"
        assert ok["took_ms"] == 7
    finally:
        sql(lambda c: c.execute("DELETE FROM worker_heartbeat WHERE duty = $1", duty))


def test_endpoint_needs_the_admin_token(api):
    assert api.get("/admin/noc/overview").status_code == 401
    assert api.get("/admin/noc/overview", headers={"X-Admin-Token": "guess"}).status_code == 401


def test_endpoint_reports_each_duty_as_ok_failing_or_gone(api):
    now = datetime.now(timezone.utc)
    rows = [("test-noc-fresh", 30.0, now, now, None, None),
            ("test-noc-gone", 30.0, now - timedelta(hours=1), now - timedelta(hours=1), None, None),
            ("test-noc-failing", 30.0, now, now - timedelta(minutes=5), "KeyError: x", now)]
    try:
        for r in rows:
            sql(lambda c, r=r: c.execute(
                "INSERT INTO worker_heartbeat (duty, interval_s, last_run_at, last_ok_at, last_error, last_error_at) "
                "VALUES ($1,$2,$3,$4,$5,$6)", *r))
        r = api.get("/admin/noc/overview", headers=ADMIN)
        assert r.status_code == 200, r.text
        body = r.json()
        duties = {w["duty"]: w for w in body["workers"]}
        assert duties["test-noc-fresh"]["status"] == "ok"
        assert duties["test-noc-gone"]["status"] == "crit"
        assert duties["test-noc-failing"]["status"] == "warn" and duties["test-noc-failing"]["last_error"] == "KeyError: x"
        assert body["status"] in ("ok", "warn", "crit", "unknown") and body["counts"]["crit"] >= 1
    finally:
        sql(lambda c: c.execute("DELETE FROM worker_heartbeat WHERE duty LIKE 'test-noc-%'"))


def test_endpoint_shape_and_a_live_database_reading(api):
    body = api.get("/admin/noc/overview", headers=ADMIN).json()
    for key in ("now", "status", "counts", "checks", "ai", "probes", "workers"):
        assert key in body
    by_key = {(c["key"], c.get("ref")): c for c in body["checks"]}
    assert by_key[("db_latency", None)]["status"] == "ok" and by_key[("db_latency", None)]["unit"] == "ms"
    assert ("sampler", None) in by_key                         # the monitor watches itself
    for c in body["checks"]:
        assert c["status"] in ("ok", "warn", "crit", "unknown") and c["group"]
