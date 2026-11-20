"""NOC history: the snapshots `noc.collect_loop` computes, stored so yesterday can be looked at.

One row in `noc_snapshots` per stored snapshot: every `noc_store_every_s` and at once when the
overall status changes (so an outage's first minute is never lost to the throttle), kept
`noc_retention_days` (worker `health_purge`). Superadmin-only data about the platform itself;
`client_id` is not involved.

`record` is registered as a sink of the collector (main.py). It must never raise: a failing insert is
logged and skipped, because monitoring that breaks what it watches is worse than a gap in a chart.
"""
import json
import logging
import time
from datetime import datetime, timedelta

from ..config import settings
from ..db import pool

log = logging.getLogger("cq")

_RANK = {"unknown": 0, "ok": 1, "warn": 2, "crit": 3}
_BAD = ("warn", "crit")
SNAPSHOT_WINDOW = timedelta(minutes=10)

# Throttle state: when and with which overall status the last row was stored. None = nothing stored
# since this process started, so the first call always stores.
_last_at: float | None = None
_last_status: str | None = None


def compact(snapshot: dict) -> dict:
    """What is stored: the snapshot with the in-memory probe history left out (it is a one-hour ring
    the page already gets live; the stored rows ARE the longer history)."""
    return {**snapshot, "probes": [{**p, "history": []} for p in snapshot.get("probes") or []]}


def bad_list(snapshot: dict) -> list[str]:
    """Every warn/crit item as "<status>:<kind>:<name>" (kind: check|ai|probe|worker)."""
    out = []
    for kind, rows, name in (
            ("check", snapshot.get("checks"), lambda c: c["key"] + (f":{c['ref']}" if c.get("ref") else "")),
            ("ai", snapshot.get("ai"), lambda r: r["name"]),
            ("probe", snapshot.get("probes"), lambda p: p["host"]),
            ("worker", snapshot.get("workers"), lambda w: w["duty"])):
        out += [f"{r['status']}:{kind}:{name(r)}" for r in rows or [] if r.get("status") in _BAD]
    return out


def _iso(o):
    return o.isoformat() if isinstance(o, datetime) else str(o)


async def record(snapshot: dict) -> None:
    global _last_at, _last_status
    try:
        status = snapshot["status"]
        now = time.monotonic()
        if (_last_at is not None and status == _last_status
                and now - _last_at < settings.noc_store_every_s):
            return
        counts = snapshot.get("counts") or {}
        async with pool().acquire() as conn:
            await conn.execute(
                "INSERT INTO noc_snapshots (ts, status, crit, warn, bad, payload) "
                "VALUES ($1, $2, $3, $4, $5, $6::jsonb) ON CONFLICT (ts) DO NOTHING",
                snapshot["now"], status, counts.get("crit", 0), counts.get("warn", 0),
                bad_list(snapshot), json.dumps(compact(snapshot), default=_iso))
        _last_at, _last_status = now, status      # only after a good insert: a failure retries next cycle
    except Exception:  # noqa: BLE001
        log.exception("noc history: snapshot not stored; continuing")


async def purge(days: int) -> int:
    async with pool().acquire() as conn:
        tag = await conn.execute("DELETE FROM noc_snapshots WHERE ts < now() - make_interval(days => $1)", days)
    return int(tag.rsplit(" ", 1)[-1])


async def days(tz: str, n: int) -> list[dict]:
    """One row per LOCAL calendar day in `tz` that has stored snapshots (the router validated tz)."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT (ts AT TIME ZONE $1::text)::date AS day, count(*) AS samples, "
            "count(*) FILTER (WHERE status = 'crit') AS crit, count(*) FILTER (WHERE status = 'warn') AS warn, "
            "max(CASE status WHEN 'crit' THEN 3 WHEN 'warn' THEN 2 WHEN 'ok' THEN 1 ELSE 0 END) AS rank "
            "FROM noc_snapshots WHERE ts >= now() - make_interval(days => $2) GROUP BY 1 ORDER BY 1", tz, n)
    name = {v: k for k, v in _RANK.items()}
    return [{"day": r["day"].isoformat(), "status": name[r["rank"]], "samples": r["samples"],
             "crit": r["crit"], "warn": r["warn"]} for r in rows]


def _index(snap: dict) -> dict:
    """One sample's numbers, keyed the way the series are."""
    checks = {(c["key"], c.get("ref")): c["value"] for c in snap.get("checks") or []}
    return {
        "checks": {name: checks.get((key, ref)) for name, (key, ref) in _SERIES.items()},
        "probes": {p["key"]: {"p50": p.get("p50_ms"), "loss": p.get("loss_pct")} for p in snap.get("probes") or []},
        "ai": {(r["id"] or f"legacy:{r['provider']}"): r for r in reversed(snap.get("ai") or [])},
    }


_SERIES = {"cpu": ("cpu", None), "mem": ("mem", None), "swap_mb": ("swap", None), "disk": ("disk", "/"),
           "net_mbs": ("net_rate", None), "net_loss": ("net_loss", None), "net_errors": ("net_errors", None),
           "db_ms": ("db_latency", None), "http_rate": ("http_rate", None), "http_5xx": ("http_5xx", None),
           "jobs_stuck": ("jobs_stuck", None)}


async def history(start: datetime, end: datetime) -> dict:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT ts, status, crit, warn, bad, payload FROM noc_snapshots "
            "WHERE ts >= $1 AND ts <= $2 ORDER BY ts", start, end)
    samples, idx = [], []
    for r in rows:
        samples.append({"ts": r["ts"].isoformat(), "status": r["status"], "crit": r["crit"],
                        "warn": r["warn"], "bad": list(r["bad"])})
        p = r["payload"]
        idx.append(_index(json.loads(p) if isinstance(p, str) else p))   # jsonb arrives as text
    hosts = sorted({h for i in idx for h in i["probes"]})
    ais = sorted({k for i in idx for k in i["ai"]})
    series: dict = {"ts": [int(r["ts"].timestamp() * 1000) for r in rows]}
    for name in _SERIES:
        series[name] = [i["checks"][name] for i in idx]
    series["probes"] = {h: {f: [i["probes"].get(h, {}).get(f) for i in idx] for f in ("p50", "loss")} for h in hosts}
    series["ai"] = {k: {"name": next(i["ai"][k]["name"] for i in reversed(idx) if k in i["ai"]),
                        "calls": [i["ai"].get(k, {}).get("calls_15m") for i in idx],
                        "fails": [i["ai"].get(k, {}).get("fails_15m") for i in idx],
                        "p50": [i["ai"].get(k, {}).get("p50_ms") for i in idx]} for k in ais}
    return {"from": start.isoformat(), "to": end.isoformat(), "step_s": int(settings.noc_store_every_s),
            "samples": samples, "series": series}


async def snapshot_at(at: datetime) -> dict | None:
    """The newest stored row at or before `at`, no more than ten minutes older."""
    async with pool().acquire() as conn:
        r = await conn.fetchrow(
            "SELECT ts, payload FROM noc_snapshots WHERE ts <= $1 AND ts > $2 ORDER BY ts DESC LIMIT 1",
            at, at - SNAPSHOT_WINDOW)
    if not r:
        return None
    p = r["payload"]
    return {"ts": r["ts"].isoformat(), "snapshot": json.loads(p) if isinstance(p, str) else p}
