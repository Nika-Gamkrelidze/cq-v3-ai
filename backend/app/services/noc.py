"""NOC: one read that says whether the platform is healthy right now — and, when it is not, why.

The Health tab (`health_metrics`) answers "how is the box doing" with charts. A NOC answers a
different question, in this order of how badly it hurts when the answer is no:

  1. the AI providers — every analysis, bot reply and transcription is a call to one of them;
  2. the network path to them (packet loss, latency, DNS) — and whether it is OUR path or THEIRS;
  3. the dependencies the product cannot run without: Postgres, the embeddings service, the
     voice-tone sidecar, the worker;
  4. the host: CPU, memory, swap, disk;
  5. what users feel: 5xx rate, stuck jobs, a re-embed queue nobody is draining;
  6. the certificate that serves it all.

Everything here is derived from data the product already has (the host sampler, `tenant_load`,
`llm_usage`, `ai_connections`, `kb_reembed_jobs`, the worker heartbeat) plus one new active
measurement: a TCP connect to each provider's host every 30 s. That probe spends no tokens and
sends no credential — it measures the network, not the account — and a baseline target outside
every provider tells "our network is down" apart from "Anthropic is down".

Status is decided HERE, from the thresholds below, so the page and any future alert agree. Each
check is `{key, group, status, value, unit, detail, ref?}`; the frontend only labels and formats.
`unknown` (no data yet, not configured) never turns the overall status red.

shortcut: probe history lives in memory (the api is one process), so a deploy resets the last
hour of RTT/loss; persist it in a table only if the NOC needs yesterday's network.
shortcut: thresholds are constants; move them to app_settings when two operators disagree.
"""
import asyncio
import logging
import socket
import ssl
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from ..config import settings
from ..db import pool
from . import ai_registry, health_metrics, sentiment

log = logging.getLogger("cq")

OK, WARN, CRIT, UNKNOWN = "ok", "warn", "crit", "unknown"
_RANK = {UNKNOWN: 0, OK: 1, WARN: 2, CRIT: 3}

# (warn at, crit at) — a value at or above the number is that level, except where `low_is_bad`.
T = {
    "cpu_pct": (85, 95), "load_per_core": (1.0, 2.0), "mem_pct": (85, 95), "swap_mb": (512, 2048),
    "disk_pct": (80, 90), "loss_pct": (0.1, 1.0), "err_pct": (0.1, 1.0),
    "db_pool_pct": (80, 95), "db_conn_pct": (70, 90), "db_ms": (200, 1000),
    "emb_ms": (2000, 8000), "http_5xx_pct": (2, 5), "ai_fail_pct": (20, 50),
    "probe_loss_pct": (10, 50), "tls_days": (21, 7), "stuck_jobs": (1, 3),
    "reembed_wait_min": (10, 60),
}
MIN_HTTP_REQUESTS = 20       # below this a 5xx percentage is noise, not a rate
MIN_AI_CALLS = 5             # same for a provider's failure rate
STUCK_JOB_MIN = 30           # an analysis job in flight longer than this is stuck, not busy
WINDOW_MIN = 15              # the "right now" window for provider and probe statistics

PROBE_INTERVAL_S, PROBE_TIMEOUT_S, RING_SIZE = 30.0, 3.0, 120   # 120 samples = 1 h at 30 s
PROVIDER_HOSTS = {"anthropic": "api.anthropic.com", "openai": "api.openai.com",
                  "gemini": "generativelanguage.googleapis.com", "elevenlabs": "api.elevenlabs.io"}
BASELINE = {"key": "internet", "host": "1.1.1.1", "port": 443, "kind": "baseline"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def level(value, warn, crit, *, low_is_bad: bool = False) -> str:
    """`unknown` for no value; otherwise the level `value` reaches against (warn, crit)."""
    if value is None:
        return UNKNOWN
    bad = (lambda t: value <= t) if low_is_bad else (lambda t: value >= t)
    return CRIT if bad(crit) else WARN if bad(warn) else OK


def worst(statuses) -> str:
    """The overall level: the worst of the known ones. `unknown` only wins when nothing is known."""
    return max(statuses, key=_RANK.get, default=UNKNOWN)


def check(key: str, group: str, status: str, value=None, unit: str = "text", detail=None, ref=None) -> dict:
    return {"key": key, "group": group, "status": status, "value": value, "unit": unit,
            "detail": detail, "ref": ref}


def _r(v, places=1):
    return None if v is None else round(float(v), places)


# --------------------------------------------------------------------------- #
# Probes: TCP connect to each provider host, kept in memory
# --------------------------------------------------------------------------- #
class ProbeRing:
    """The last `size` probe results per target. Pure data structure: no clock, no network."""

    def __init__(self, size: int = RING_SIZE) -> None:
        self._size = size
        self._d: dict[str, deque] = {}

    def add(self, target: str, ok: bool, ms: float | None, err: str | None = None,
            at: float | None = None) -> None:
        self._d.setdefault(target, deque(maxlen=self._size)).append(
            (time.time() if at is None else at, bool(ok), ms, err))

    def has(self, target: str) -> bool:
        return target in self._d

    def forget_except(self, keep: set[str]) -> None:
        """A provider that was deactivated must stop showing a stale red row."""
        for k in [k for k in self._d if k not in keep]:
            del self._d[k]

    def stats(self, target: str, window_s: float = WINDOW_MIN * 60, at: float | None = None) -> dict:
        rows = list(self._d.get(target, ()))
        cutoff = (time.time() if at is None else at) - window_s
        recent = [r for r in rows if r[0] >= cutoff]
        ok_ms = sorted(r[2] for r in recent if r[1] and r[2] is not None)
        consecutive = 0
        for r in reversed(rows):
            if r[1]:
                break
            consecutive += 1
        last_ok = next((r[0] for r in reversed(rows) if r[1]), None)
        last_err = next((r[3] for r in reversed(rows) if not r[1]), None)
        n = len(recent)

        def pct(q):
            return ok_ms[min(len(ok_ms) - 1, int(q * len(ok_ms)))] if ok_ms else None
        return {"n": n, "loss_pct": _r(100.0 * sum(not r[1] for r in recent) / n) if n else None,
                "p50_ms": _r(pct(0.5)), "p95_ms": _r(pct(0.95)), "consecutive_fail": consecutive,
                "last_ok_at": last_ok, "last_error": last_err}

    def history(self, target: str, n: int = 60) -> list:
        return [[round(t), ms if ok else None] for t, ok, ms, _ in list(self._d.get(target, ()))[-n:]]


_ring = ProbeRing()


def probe_status(s: dict) -> str:
    if s["n"] < 3:
        return UNKNOWN
    if s["consecutive_fail"] >= 3:
        return CRIT
    return level(s["loss_pct"], *T["probe_loss_pct"])


async def probe_once(host: str, port: int, timeout: float = PROBE_TIMEOUT_S):
    """`(ok, ms, error)` for one TCP connect — DNS included, because a DNS failure is exactly the
    kind of thing that looks like a provider outage from the application."""
    t0 = time.monotonic()
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except asyncio.TimeoutError:
        return False, None, "timeout"
    except socket.gaierror:
        return False, None, "dns"
    except ConnectionRefusedError:
        return False, None, "refused"
    except OSError as exc:
        return False, None, type(exc).__name__
    ms = (time.monotonic() - t0) * 1000
    w.close()
    return True, ms, None


def _host_of(conn: dict) -> tuple[str, int] | None:
    base = (conn.get("base_url") or "").strip()
    if base:
        u = urlparse(base)
        if u.hostname:
            return u.hostname, u.port or (443 if u.scheme == "https" else 80)
    host = PROVIDER_HOSTS.get(conn.get("provider") or "")
    return (host, 443) if host else None


async def targets() -> list[dict]:
    """The baseline plus one target per distinct host an ACTIVE connection talks to. With an empty
    registry (a keyless / legacy deployment) the two founding providers stand in."""
    out = [dict(BASELINE)]
    seen: set[str] = set()
    conns = [c for c in await ai_registry.list_connections() if c["is_active"]]
    for c in conns or [{"provider": "anthropic"}, {"provider": "elevenlabs"}]:
        hp = _host_of(c)
        if hp and hp[0] not in seen:
            seen.add(hp[0])
            out.append({"key": hp[0], "host": hp[0], "port": hp[1], "kind": "provider",
                        "provider": c["provider"]})
    return out


async def probe_loop() -> None:
    """Started by the api lifespan beside the host sampler. One bad round never ends the loop."""
    while True:
        try:
            ts = await targets()
            _ring.forget_except({t["key"] for t in ts})
            results = await asyncio.gather(*(probe_once(t["host"], t["port"]) for t in ts))
            for t, (ok, ms, err) in zip(ts, results):
                _ring.add(t["key"], ok, ms, err)
        except Exception:  # noqa: BLE001
            log.exception("noc probe round failed; continuing")
        await asyncio.sleep(PROBE_INTERVAL_S)


# --------------------------------------------------------------------------- #
# Small caches for the two checks that cost something
# --------------------------------------------------------------------------- #
_cache: dict[str, tuple[float, object]] = {}


async def _cached(key: str, ttl_s: float, fn, *, failed=lambda v: False, retry_s: float = 60):
    """`fn()` at most once per `ttl_s`; a result `failed()` calls bad is kept only `retry_s`, so
    a recovered dependency is noticed in a minute and not after six hours."""
    hit = _cache.get(key)
    if hit and time.monotonic() < hit[0]:
        return hit[1]
    value = await fn()
    _cache[key] = (time.monotonic() + (retry_s if failed(value) else ttl_s), value)
    return value


# --------------------------------------------------------------------------- #
# Collectors — each returns a list of checks (or rows) and may raise; overview() contains that
# --------------------------------------------------------------------------- #
async def _host(ov: dict) -> list[dict]:
    s, latest = ov["sampler"], ov.get("latest") or {}
    interval = s.get("interval_s") or 10
    age = s.get("age_s")
    out = [check("sampler", "host", CRIT if age is None else level(age, interval * 3, interval * 6),
                 age, "s", {"interval_s": interval})]
    if not latest:
        return out
    cpus = latest.get("cpu_count") or 1
    total, avail = latest.get("mem_total_mb"), latest.get("mem_available_mb")
    mem_pct = None if not total or avail is None else 100.0 * (total - avail) / total
    load_pc = None if latest.get("load5") is None else latest["load5"] / cpus
    out += [
        check("cpu", "host", level(latest.get("cpu_pct"), *T["cpu_pct"]), _r(latest.get("cpu_pct")), "pct",
              {"cores": cpus}),
        check("load", "host", level(load_pc, *T["load_per_core"]), _r(load_pc, 2), "ratio",
              {"load1": latest.get("load1"), "load5": latest.get("load5"), "load15": latest.get("load15")}),
        check("mem", "host", level(mem_pct, *T["mem_pct"]), _r(mem_pct), "pct",
              {"total_mb": total, "available_mb": avail}),
        check("swap", "host", level(latest.get("swap_used_mb"), *T["swap_mb"]), latest.get("swap_used_mb"), "mb"),
    ]
    for d in latest.get("disk") or []:
        out.append(check("disk", "host", level(d.get("pct"), *T["disk_pct"]), d.get("pct"), "pct",
                         {"used_gb": d.get("used_gb"), "total_gb": d.get("total_gb")}, ref=d.get("mount")))
    out.append(check("uptime", "host", OK, latest.get("uptime_s"), "s"))
    return out


async def _network() -> list[dict]:
    """Packet loss and errors of the api container's interface, summed over the window. Rates are
    per second between equal-length samples, so the sums are proportional to the totals."""
    async with pool().acquire() as conn:
        r = await conn.fetchrow(
            "SELECT sum(net_pkts_s) AS pkts, sum(net_drops_s) AS drops, sum(net_errs_s) AS errs, "
            "avg(net_rx_mb_s) AS rx, avg(net_tx_mb_s) AS tx, count(net_pkts_s) AS n "
            "FROM system_metrics WHERE ts >= now() - make_interval(mins => $1)", WINDOW_MIN)
    pkts = float(r["pkts"] or 0)
    ok = pkts > 0 and r["n"]
    drop_pct = 100.0 * float(r["drops"] or 0) / pkts if ok else None
    err_pct = 100.0 * float(r["errs"] or 0) / pkts if ok else None
    return [
        check("net_loss", "network", level(drop_pct, *T["loss_pct"]), _r(drop_pct, 3), "pct",
              {"window_min": WINDOW_MIN}),
        check("net_errors", "network", level(err_pct, *T["err_pct"]), _r(err_pct, 3), "pct"),
        check("net_rate", "network", OK if ok else UNKNOWN, _r((r["rx"] or 0) + (r["tx"] or 0), 2) if ok else None,
              "mbs", {"rx": _r(r["rx"], 2), "tx": _r(r["tx"], 2)}),
    ]


async def _embeddings() -> dict:
    from .embeddings import get_provider
    t0 = time.monotonic()
    try:
        h = await asyncio.wait_for((await get_provider()).health(), 10)
        return {"ok": bool(h.get("ok", True)), "ms": (time.monotonic() - t0) * 1000, "model": h.get("model")}
    except Exception as exc:  # noqa: BLE001 — a failing dependency IS the reading
        return {"ok": False, "ms": None, "error": f"{type(exc).__name__}: {exc}"[:200]}


async def _services(ov: dict) -> list[dict]:
    t0 = time.monotonic()
    async with pool().acquire() as conn:
        await conn.fetchval("SELECT 1")
        db_ms = (time.monotonic() - t0) * 1000
        used = await conn.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        cap = await conn.fetchval("SELECT setting::int FROM pg_settings WHERE name = 'max_connections'")
    latest = ov.get("latest") or {}
    psize, pused = latest.get("db_pool_size"), latest.get("db_pool_used")
    pool_pct = 100.0 * pused / psize if psize and pused is not None else None
    conn_pct = 100.0 * used / cap if cap else None
    out = [
        check("db_latency", "services", level(db_ms, *T["db_ms"]), _r(db_ms), "ms"),
        check("db_conns", "services", level(conn_pct, *T["db_conn_pct"]), _r(conn_pct), "pct",
              {"used": used, "max": cap}),
        check("db_pool", "services", level(pool_pct, *T["db_pool_pct"]), _r(pool_pct), "pct",
              {"used": pused, "size": psize}),
    ]
    emb = await _cached("embeddings", 60, _embeddings, failed=lambda v: not v["ok"], retry_s=15)
    out.append(check("embeddings", "services", CRIT if not emb["ok"] else level(emb["ms"], *T["emb_ms"]),
                     _r(emb["ms"]), "ms", {"model": emb.get("model"), "error": emb.get("error")}))
    tone = await sentiment.status()
    st = {"ok": OK, "warming": WARN, "disabled": UNKNOWN}.get(tone.get("state"), CRIT)
    out.append(check("voice_tone", "services", st, tone.get("state"), "text", {"model": tone.get("model")}))
    return out


async def _workload() -> list[dict]:
    async with pool().acquire() as conn:
        http = await conn.fetchrow(
            "SELECT coalesce(sum(requests),0) AS req, coalesce(sum(errors),0) AS err "
            "FROM tenant_load WHERE bucket >= now() - interval '5 minutes'")
        jobs = await conn.fetchrow(
            "SELECT count(*) AS inflight, "
            "count(*) FILTER (WHERE updated_at < now() - make_interval(mins => $2)) AS stuck "
            "FROM audio_jobs WHERE status = ANY($1::text[])",
            list(health_metrics._ACTIVE_JOB_STATUSES), STUCK_JOB_MIN)
        wait = await conn.fetchval(
            "SELECT extract(epoch FROM now() - min(created_at)) / 60 FROM kb_reembed_jobs WHERE state = 'queued'")
    req, err = int(http["req"]), int(http["err"])
    err_pct = 100.0 * err / req if req >= MIN_HTTP_REQUESTS else None
    return [
        check("http_5xx", "workload", level(err_pct, *T["http_5xx_pct"]), _r(err_pct, 2), "pct",
              {"requests": req, "errors": err, "window_min": 5}),
        check("http_rate", "workload", OK, _r(req / 5.0), "per_min"),
        check("jobs_stuck", "workload", level(jobs["stuck"], *T["stuck_jobs"]) if jobs["inflight"] else OK,
              int(jobs["stuck"]), "count", {"minutes": STUCK_JOB_MIN}),
        check("jobs_inflight", "workload", OK, int(jobs["inflight"]), "count"),
        check("reembed_backlog", "workload", level(wait, *T["reembed_wait_min"]) if wait is not None else OK,
              _r(wait, 0) if wait is not None else 0, "min"),
    ]


async def _tls_days(host: str) -> dict:
    try:
        ctx = ssl.create_default_context()
        _, w = await asyncio.wait_for(
            asyncio.open_connection(host, 443, ssl=ctx, server_hostname=host), 6)
        cert = w.get_extra_info("ssl_object").getpeercert()
        w.close()
        return {"days": (ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) / 86400}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


async def _edge() -> list[dict]:
    out = []
    for host in [h.strip() for h in settings.noc_tls_hosts.split(",") if h.strip()]:
        r = await _cached(f"tls:{host}", 6 * 3600, lambda h=host: _tls_days(h),
                          failed=lambda v: "days" not in v, retry_s=300)
        out.append(check("tls", "edge", level(r.get("days"), *T["tls_days"], low_is_bad=True),
                         _r(r.get("days"), 0), "days", {"error": r.get("error")}, ref=host))
    return out


_AI_SQL = """
SELECT u.connection_id, u.provider, u.capability,
       count(*) FILTER (WHERE u.created_at >= now() - make_interval(mins => $1)) AS calls_w,
       count(*) FILTER (WHERE u.created_at >= now() - make_interval(mins => $1) AND NOT u.ok) AS fails_w,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY u.latency_ms)
           FILTER (WHERE u.created_at >= now() - make_interval(mins => $1)) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY u.latency_ms)
           FILTER (WHERE u.created_at >= now() - make_interval(mins => $1)) AS p95_ms,
       count(*) AS calls_24h, count(*) FILTER (WHERE NOT u.ok) AS fails_24h,
       max(u.created_at) FILTER (WHERE u.ok) AS last_ok_at,
       max(u.created_at) FILTER (WHERE NOT u.ok) AS last_fail_at
FROM llm_usage u
WHERE u.created_at >= now() - interval '24 hours' AND u.feature <> 'probe' AND u.provider IS DISTINCT FROM 'local'
GROUP BY u.connection_id, u.provider, u.capability
"""


def ai_status(stat: dict, probe: dict | None, last_test: dict | None) -> str:
    """Worst of: the live failure rate, the network path to the provider, the last manual test."""
    parts = [probe_status(probe)] if probe else []
    if stat["calls_w"] >= MIN_AI_CALLS:
        parts.append(level(100.0 * stat["fails_w"] / stat["calls_w"], *T["ai_fail_pct"]))
    elif stat["calls_w"]:
        parts.append(OK)
    if last_test and last_test.get("ok") is False:
        try:
            tested = datetime.fromisoformat(str(last_test.get("at")))
        except ValueError:
            tested = None
        if tested and (stat["last_ok_at"] is None or stat["last_ok_at"] < tested):
            parts.append(WARN)     # the operator's own test failed and nothing has succeeded since
    return worst(parts) if parts else UNKNOWN


async def _ai() -> list[dict]:
    conns = [c for c in await ai_registry.list_connections() if c["is_active"]]
    async with pool().acquire() as conn:
        stats = await conn.fetch(_AI_SQL, WINDOW_MIN)
    by_conn = {str(r["connection_id"]): r for r in stats if r["connection_id"]}
    rows = []

    def row(r, name, provider, capability, model, cid, host_key, last_test):
        stat = {"calls_w": int(r["calls_w"]) if r else 0, "fails_w": int(r["fails_w"]) if r else 0,
                "last_ok_at": r["last_ok_at"] if r else None}
        probe = _ring.stats(host_key) if host_key else None
        return {"id": cid, "name": name, "provider": provider, "capability": capability, "model": model,
                "host": host_key, "calls_15m": stat["calls_w"], "fails_15m": stat["fails_w"],
                "p50_ms": _r(r["p50_ms"], 0) if r else None, "p95_ms": _r(r["p95_ms"], 0) if r else None,
                "calls_24h": int(r["calls_24h"]) if r else 0, "fails_24h": int(r["fails_24h"]) if r else 0,
                "last_ok_at": r["last_ok_at"] if r else None, "last_fail_at": r["last_fail_at"] if r else None,
                "probe": probe, "last_test": last_test, "status": ai_status(stat, probe, last_test)}

    for c in conns:
        hp = _host_of(c)
        rows.append(row(by_conn.get(c["id"]), c["name"], c["provider"], c["capability"], c["model"], c["id"],
                        hp[0] if hp else None, c.get("last_test")) | {"is_default": c["is_default"]})
    for r in stats:                # calls made without a registry connection (the legacy path)
        if not r["connection_id"]:
            host = PROVIDER_HOSTS.get(r["provider"] or "")
            rows.append(row(r, f"{r['provider']} (settings)", r["provider"], r["capability"], None, None,
                            host if host and _ring.has(host) else None, None) | {"is_default": False})
    return rows


async def _workers() -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM worker_heartbeat ORDER BY duty")
    t = now()
    out = []
    for r in rows:
        age = (t - r["last_run_at"]).total_seconds()
        stale_after = max(3 * (r["interval_s"] or 60), 120)
        failing = bool(r["last_error_at"] and (r["last_ok_at"] is None or r["last_error_at"] > r["last_ok_at"]))
        out.append({"duty": r["duty"], "interval_s": r["interval_s"], "age_s": _r(age, 0),
                    "stale_after_s": stale_after, "took_ms": r["took_ms"], "failing": failing,
                    "last_error": r["last_error"] if failing else None,
                    "status": CRIT if age > stale_after else WARN if failing else OK})
    return out


async def _guarded(name: str, coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 — a broken collector must not blank the whole page
        log.exception("noc collector %s failed", name)
        return exc


def _probe_rows(targets_: list[dict]) -> list[dict]:
    rows = []
    for t in targets_:
        s = _ring.stats(t["key"])
        rows.append({"key": t["key"], "host": t["host"], "kind": t["kind"], "provider": t.get("provider"),
                     **s, "status": probe_status(s), "history": _ring.history(t["key"])})
    return rows


async def overview() -> dict:
    ov = await health_metrics.overview()
    tg, host, net, svc, load, edge, ai, workers = await asyncio.gather(
        _guarded("targets", targets()), _guarded("host", _host(ov)), _guarded("network", _network()),
        _guarded("services", _services(ov)), _guarded("workload", _workload()),
        _guarded("edge", _edge()), _guarded("ai", _ai()), _guarded("workers", _workers()))
    checks: list[dict] = []
    for name, part in (("host", host), ("network", net), ("services", svc), ("workload", load), ("edge", edge)):
        if isinstance(part, Exception):
            checks.append(check("collector", "monitoring", WARN, None, "text",
                                {"error": f"{type(part).__name__}: {part}"[:200]}, ref=name))
        else:
            checks += part
    ai_rows = [] if isinstance(ai, Exception) else ai
    probe_rows = [] if isinstance(tg, Exception) else _probe_rows(tg)
    worker_rows = [] if isinstance(workers, Exception) else workers
    if not worker_rows and not isinstance(workers, Exception):
        checks.append(check("worker", "services", CRIT, None, "text", {"reason": "no heartbeat"}))
    elif isinstance(workers, Exception):
        checks.append(check("collector", "monitoring", WARN, None, "text", {"error": str(workers)[:200]}, ref="workers"))
    if isinstance(ai, Exception):
        checks.append(check("collector", "monitoring", WARN, None, "text", {"error": str(ai)[:200]}, ref="ai"))
    every = [c["status"] for c in checks] + [r["status"] for r in ai_rows + probe_rows + worker_rows]
    return {
        "now": now(), "status": worst(every),
        "counts": {s: every.count(s) for s in (CRIT, WARN, OK, UNKNOWN)},
        "checks": checks, "ai": ai_rows, "probes": probe_rows, "workers": worker_rows,
        "sampler_interval_s": ov["sampler"].get("interval_s"),
    }
