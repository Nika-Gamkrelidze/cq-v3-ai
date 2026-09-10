"""Server health: host samples, per-tenant request load, and the queries behind /admin/health/*.

Three things live here, and the split follows who calls them:

- `HostSampler` + `insert_sample` — run by the api's lifespan every `health.sample_interval_s`
  seconds. One `system_metrics` row per tick.
- `LoadAccumulator` + `flush_load` — the timing middleware in main.py records every request
  into ONE module-level accumulator; a lifespan task drains it into `tenant_load` every 15 s
  as a single upsert per (minute, principal kind, client).
- `overview` / `series` / `tenant_table` / `purge` — the read side for routers/admin.py and the
  worker's hourly `health_purge` duty.

What psutil sees from inside the container — read this before trusting a number:

  * cpu, load average, memory, swap, boot time: the HOST's. /proc is not namespaced for these,
    so the figures describe the whole box, not the api container's cgroup share. That is what
    the operator wants from a "is the server ok" tab, and it is also why `api_rss_mb` /
    `api_cpu_pct` are read separately from our own process.
  * disk usage: whatever is mounted at '/' and MEDIA_ROOT inside the container — on the
    server both sit on the host's root filesystem (bind mount), so the usage IS the host's.
  * disk I/O counters: host-wide (block devices are not namespaced).
  * network counters: the CONTAINER's own interfaces (eth0 on the compose bridge), so
    net_rx/tx is api traffic plus its DB / TEI / provider chatter, not the host NIC.

Every psutil reading is wrapped so one missing metric (no swap, no load average on the platform,
a permission error on num_fds) nulls that one column and never loses the sample. `cpu_percent`
needs a priming call: the first reading after process start is meaningless, so the sampler
primes in its constructor and every later call returns the average since the previous one —
which, at a 10 s interval, is exactly the number a chart wants.

Rates (`*_mb_s`) are derived from the difference between two readings of cumulative counters,
against a monotonic clock. The first reading, a counter that went backwards (interface reset,
container restart) and a zero-length interval all yield None rather than a spike.

No `client_id` filter on purpose: these tables are superadmin-only (routers/admin.py) and
`client_id` is a label here, not a scope. Nothing in this module is reachable by a tenant.
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ..config import settings
from ..db import pool
from . import settings_store

log = logging.getLogger("cq")

try:  # psutil is a runtime dependency; the pure parts (ranges, accumulator, rate maths) and the
    import psutil  # tests must not need it.
except ImportError:  # pragma: no cover - exercised only where the package is absent
    psutil = None

_MB = 1024 * 1024
_GB = 1024 ** 3

# range key -> (window, downsampling step in seconds). Shared with the frontend: the tab's
# range picker sends the key and reads `step_s` back, so this is a contract, not a tuning.
RANGES: dict[str, tuple[timedelta, int]] = {
    "1h": (timedelta(hours=1), 10),
    "6h": (timedelta(hours=6), 60),
    "24h": (timedelta(days=1), 120),
    "7d": (timedelta(days=7), 900),
    "30d": (timedelta(days=30), 3600),
}
DEFAULT_RANGE = "24h"

# `principal_kind` vocabulary of tenant_load. Anything else the middleware hands over becomes
# 'unknown' rather than a new row per typo.
PRINCIPAL_KINDS = frozenset({"tenant", "superadmin", "user", "anonymous", "unknown"})

# The in-flight statuses `analysis.sweep_stuck_jobs` recognises, plus the insert default
# ('pending' — a job that exists but has not started transcribing) and 'processing'.
_ACTIVE_JOB_STATUSES = ("pending", "queued", "processing", "transcribing", "analyzing")


def resolve_range(range_: str | None) -> tuple[str, timedelta, int]:
    """`(key, window, step_s)` for a range key; anything unknown falls back to 24h, so a stale
    bookmark or a typo shows the default chart instead of a 400."""
    key = (range_ or "").strip().lower()
    if key not in RANGES:
        key = DEFAULT_RANGE
    window, step = RANGES[key]
    return key, window, step


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe(fn, *args, default=None):
    """Call a psutil (or any) reader; a failure is that one value's None, never the sample's."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 — the whole point is "one bad reading, not a lost sample"
        return default


def _num(v):
    """asyncpg hands back numeric aggregates as Decimal; the JSON side wants plain numbers."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


def _int(v) -> int:
    return int(v or 0)


def _round(v, places: int = 2):
    return None if v is None else round(float(v), places)


# --------------------------------------------------------------------------- #
# Host sampler
# --------------------------------------------------------------------------- #
def derive_rate(prev: tuple[float, float] | None, cur: tuple[float, float] | None,
                dt_s: float) -> tuple[float | None, float | None]:
    """MB/s for a pair of cumulative byte counters `(a, b)` read `dt_s` apart.

    None (for both) when there is no previous reading, the interval is not positive, or a
    counter went backwards — a reset must read as a gap in the chart, not a negative rate.
    """
    if prev is None or cur is None or dt_s <= 0:
        return None, None
    da, db = cur[0] - prev[0], cur[1] - prev[1]
    if da < 0 or db < 0:
        return None, None
    return da / _MB / dt_s, db / _MB / dt_s


class HostSampler:
    """One instance per api process. `sample()` never raises.

    `clock` and `read_counters` are injectable so the rate maths can be tested against fake
    counters without psutil: `read_counters()` returns `{"disk": (read_bytes, write_bytes) |
    None, "net": (rx_bytes, tx_bytes) | None}`.
    """

    def __init__(self, *, clock=time.monotonic, read_counters=None,
                 media_root: str | None = None) -> None:
        self._clock = clock
        self._read_counters = read_counters or self._psutil_counters
        self._media_root = media_root if media_root is not None else settings.media_root
        self._prev_t: float | None = None
        self._prev_disk: tuple[float, float] | None = None
        self._prev_net: tuple[float, float] | None = None
        self._proc = _safe(psutil.Process) if psutil else None
        # Priming: the first cpu_percent() after start is 0.0 by definition; call it now so the
        # first real sample reports the interval since here.
        if psutil:
            _safe(psutil.cpu_percent, None)
        if self._proc is not None:
            _safe(self._proc.cpu_percent, None)

    # -- counters + rates (pure enough to test) --------------------------------------------
    @staticmethod
    def _psutil_counters() -> dict:
        disk = _safe(psutil.disk_io_counters) if psutil else None
        net = _safe(psutil.net_io_counters) if psutil else None
        return {
            "disk": (disk.read_bytes, disk.write_bytes) if disk else None,
            "net": (net.bytes_recv, net.bytes_sent) if net else None,
        }

    def rates(self) -> dict:
        """Read the cumulative counters, derive MB/s against the previous reading, and keep
        this reading as the next previous. First call: all four are None."""
        now = self._clock()
        counters = _safe(self._read_counters, default={}) or {}
        disk, net = counters.get("disk"), counters.get("net")
        dt = (now - self._prev_t) if self._prev_t is not None else 0.0
        read_mb_s, write_mb_s = derive_rate(self._prev_disk, disk, dt)
        rx_mb_s, tx_mb_s = derive_rate(self._prev_net, net, dt)
        self._prev_t, self._prev_disk, self._prev_net = now, disk, net
        return {
            "disk_read_mb_s": _round(read_mb_s, 3),
            "disk_write_mb_s": _round(write_mb_s, 3),
            "net_rx_mb_s": _round(rx_mb_s, 3),
            "net_tx_mb_s": _round(tx_mb_s, 3),
        }

    # -- host readings ------------------------------------------------------------------
    def _disk_usage(self) -> list[dict]:
        """Usage of '/' plus MEDIA_ROOT when the latter is a different device (a separate
        volume for recordings is the one case where '/' being fine says nothing about uploads)."""
        mounts = ["/"]
        root_dev = _safe(lambda: os.stat("/").st_dev)
        media = self._media_root
        if media and _safe(os.path.isdir, media):
            media_dev = _safe(lambda: os.stat(media).st_dev)
            if media_dev is not None and media_dev != root_dev:
                mounts.append(media)
        out = []
        for m in mounts:
            u = _safe(psutil.disk_usage, m) if psutil else None
            if u is None:
                continue
            out.append({"mount": m, "total_gb": _round(u.total / _GB), "used_gb": _round(u.used / _GB),
                        "pct": _round(u.percent, 1)})
        return out

    def _host(self) -> dict:
        if psutil is None:
            return {}
        vm = _safe(psutil.virtual_memory)
        sw = _safe(psutil.swap_memory)
        load = _safe(psutil.getloadavg)
        boot = _safe(psutil.boot_time)
        return {
            "cpu_pct": _round(_safe(psutil.cpu_percent, None), 1),
            "cpu_count": _safe(psutil.cpu_count),
            "load1": _round(load[0]) if load else None,
            "load5": _round(load[1]) if load else None,
            "load15": _round(load[2]) if load else None,
            "mem_total_mb": int(vm.total / _MB) if vm else None,
            "mem_used_mb": int(vm.used / _MB) if vm else None,
            "mem_available_mb": int(vm.available / _MB) if vm else None,
            "swap_used_mb": int(sw.used / _MB) if sw else None,
            "disk": self._disk_usage(),
            "uptime_s": int(time.time() - boot) if boot else None,
        }

    def _process(self) -> dict:
        p = self._proc
        if p is None:
            return {}
        mem = _safe(p.memory_info)
        return {
            "api_rss_mb": _round(mem.rss / _MB) if mem else None,
            "api_cpu_pct": _round(_safe(p.cpu_percent, None), 1),
            # num_fds is Unix-only and can be refused by a hardened container; None then.
            "api_open_fds": _safe(getattr(p, "num_fds", lambda: None)),
        }

    @staticmethod
    def _pool_stats() -> dict:
        try:
            p = pool()
            size = p.get_size()
            return {"db_pool_size": size, "db_pool_used": size - p.get_idle_size()}
        except Exception:  # noqa: BLE001 — pool not up yet, or an asyncpg without the getters
            return {"db_pool_size": None, "db_pool_used": None}

    @staticmethod
    async def _db_readings() -> dict:
        out = {"db_size_mb": None, "active_jobs": None}
        try:
            async with pool().acquire() as conn:
                size = await conn.fetchval("SELECT pg_database_size(current_database())")
                out["db_size_mb"] = _round((size or 0) / _MB)
                out["active_jobs"] = await conn.fetchval(
                    "SELECT count(*) FROM audio_jobs WHERE status = ANY($1::text[])",
                    list(_ACTIVE_JOB_STATUSES))
        except Exception as exc:  # noqa: BLE001 — the host part of the sample is still worth keeping
            log.debug("health sampler: db readings failed: %s", exc)
        return out

    async def sample(self) -> dict:
        """One `system_metrics` row as a dict (keys = columns, plus `ts`). Never raises."""
        row: dict = {"ts": _now()}
        row.update(_safe(self._host, default={}) or {})
        row.update(_safe(self.rates, default={}) or {})
        row.update(_safe(self._process, default={}) or {})
        row.update(self._pool_stats())
        row.update(await self._db_readings())
        return row


_SAMPLE_COLUMNS = (
    "ts", "cpu_pct", "cpu_count", "load1", "load5", "load15", "mem_total_mb", "mem_used_mb",
    "mem_available_mb", "swap_used_mb", "disk", "disk_read_mb_s", "disk_write_mb_s",
    "net_rx_mb_s", "net_tx_mb_s", "api_rss_mb", "api_cpu_pct", "api_open_fds", "db_size_mb",
    "db_pool_size", "db_pool_used", "active_jobs", "uptime_s",
)
_INSERT_SAMPLE_SQL = (
    f"INSERT INTO system_metrics ({', '.join(_SAMPLE_COLUMNS)}) VALUES ("
    + ", ".join(f"${i}::jsonb" if c == "disk" else f"${i}"
                for i, c in enumerate(_SAMPLE_COLUMNS, start=1))
    + ") ON CONFLICT (ts) DO NOTHING"
)


async def insert_sample(sample: dict) -> None:
    values = [sample.get(c) for c in _SAMPLE_COLUMNS]
    values[_SAMPLE_COLUMNS.index("disk")] = json.dumps(sample.get("disk") or [])
    async with pool().acquire() as conn:
        await conn.execute(_INSERT_SAMPLE_SQL, *values)


# --------------------------------------------------------------------------- #
# Per-request load accumulator
# --------------------------------------------------------------------------- #
def _minute(ts: float) -> datetime:
    return datetime.fromtimestamp(int(ts) - int(ts) % 60, tz=timezone.utc)


def _as_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


class LoadAccumulator:
    """In-memory counters keyed (minute bucket, principal kind, client key), added up by the
    middleware on every request and handed to `flush_load` by the lifespan flusher.

    Plain dict, no lock: the api runs one uvicorn worker and both `record` and `drain` are
    synchronous calls on that loop, so they cannot interleave. `clock` is `time.time` (wall
    time — the bucket is a calendar minute, not a monotonic offset) and injectable for tests.
    """

    def __init__(self, *, clock=time.time) -> None:
        self._clock = clock
        self._rows: dict[tuple[datetime, str, str], dict] = {}

    def __len__(self) -> int:
        return len(self._rows)

    def record(self, *, principal_kind: str, client_id: str | None, ms: float, status: int,
               bytes_in: int, bytes_out: int) -> None:
        # The chat service authenticates with an integration credential (Principal.kind
        # 'integration') but every call it makes is on behalf of the tenant in X-CQ-Tenant,
        # so its wall time is that tenant's load — fold it in rather than filing it under
        # 'unknown', where the client_id would be kept but never shown as the tenant's.
        if principal_kind == "integration":
            principal_kind = "tenant"
        kind = principal_kind if principal_kind in PRINCIPAL_KINDS else "unknown"
        client_key = str(client_id) if client_id else ""
        key = (_minute(self._clock()), kind, client_key)
        row = self._rows.get(key)
        if row is None:
            row = self._rows[key] = {
                "bucket": key[0], "principal_kind": kind, "client_key": client_key,
                "client_id": _as_uuid(client_key), "requests": 0, "errors": 0, "total_ms": 0,
                "max_ms": 0, "bytes_in": 0, "bytes_out": 0,
            }
        ms_int = max(0, int(round(ms or 0)))
        row["requests"] += 1
        row["errors"] += int((status or 0) >= 500)
        row["total_ms"] += ms_int
        row["max_ms"] = max(row["max_ms"], ms_int)
        row["bytes_in"] += max(0, int(bytes_in or 0))
        row["bytes_out"] += max(0, int(bytes_out or 0))

    def drain(self) -> list[dict]:
        """Hand over everything recorded so far and start empty. The caller owns the rows from
        here — if the flush fails they are lost, which for a load chart beats double counting."""
        rows = list(self._rows.values())
        self._rows = {}
        return rows


_FLUSH_SQL = """
INSERT INTO tenant_load
    (bucket, principal_kind, client_key, client_id, requests, errors, total_ms, max_ms,
     bytes_in, bytes_out)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (bucket, principal_kind, client_key) DO UPDATE SET
    requests  = tenant_load.requests  + EXCLUDED.requests,
    errors    = tenant_load.errors    + EXCLUDED.errors,
    total_ms  = tenant_load.total_ms  + EXCLUDED.total_ms,
    max_ms    = GREATEST(tenant_load.max_ms, EXCLUDED.max_ms),
    bytes_in  = tenant_load.bytes_in  + EXCLUDED.bytes_in,
    bytes_out = tenant_load.bytes_out + EXCLUDED.bytes_out
"""


async def flush_load(rows: list[dict]) -> None:
    """One executemany upsert. Adding (not replacing) on conflict is what makes a flush every
    15 s and a minute-wide bucket agree: the same minute is flushed up to four times."""
    if not rows:
        return
    args = [(r["bucket"], r["principal_kind"], r["client_key"], r.get("client_id"),
             r["requests"], r["errors"], r["total_ms"], r["max_ms"], r["bytes_in"], r["bytes_out"])
            for r in rows]
    async with pool().acquire() as conn:
        await conn.executemany(_FLUSH_SQL, args)


# --------------------------------------------------------------------------- #
# Read side
# --------------------------------------------------------------------------- #
def _row_dict(r) -> dict | None:
    if r is None:
        return None
    d = {k: _num(v) for k, v in dict(r).items()}
    disk = d.get("disk")
    if isinstance(disk, str):  # asyncpg returns jsonb as text without a codec
        try:
            d["disk"] = json.loads(disk)
        except ValueError:
            d["disk"] = []
    return d


async def overview() -> dict:
    cfg = await settings_store.get_health_config()
    now = _now()
    async with pool().acquire() as conn:
        latest = _row_dict(await conn.fetchrow(
            "SELECT * FROM system_metrics ORDER BY ts DESC LIMIT 1"))
        size = await conn.fetchval("SELECT pg_database_size(current_database())")
        metrics_rows = await conn.fetchval("SELECT count(*) FROM system_metrics")
        load_rows = await conn.fetchval("SELECT count(*) FROM tenant_load")
    last_at = latest["ts"] if latest else None
    return {
        "now": now,
        "sampler": {
            "interval_s": cfg["sample_interval_s"],
            "last_sample_at": last_at,
            "age_s": _round((now - last_at).total_seconds(), 1) if last_at else None,
        },
        "latest": latest,
        "db": {"size_mb": _round((size or 0) / _MB), "metrics_rows": _int(metrics_rows),
               "load_rows": _int(load_rows)},
        "retention_days": cfg["retention_days"],
    }


_SERIES_METRICS_SQL = """
SELECT date_bin($1::interval, ts, $2::timestamptz) AS b,
       avg(cpu_pct) AS cpu_pct, avg(load1) AS load1, avg(load5) AS load5, avg(load15) AS load15,
       avg(mem_used_mb) AS mem_used_mb, max(mem_total_mb) AS mem_total_mb,
       avg(swap_used_mb) AS swap_used_mb,
       avg(disk_read_mb_s) AS disk_read_mb_s, avg(disk_write_mb_s) AS disk_write_mb_s,
       avg(net_rx_mb_s) AS net_rx_mb_s, avg(net_tx_mb_s) AS net_tx_mb_s,
       avg(api_rss_mb) AS api_rss_mb, avg(api_cpu_pct) AS api_cpu_pct,
       avg(db_size_mb) AS db_size_mb, avg(db_pool_used) AS db_pool_used,
       max(active_jobs) AS active_jobs
FROM system_metrics
WHERE ts >= $2 AND ts < $3
GROUP BY b ORDER BY b
"""
_SERIES_LOAD_SQL = """
SELECT date_bin($1::interval, bucket, $2::timestamptz) AS b,
       sum(requests) AS requests, sum(errors) AS errors, sum(total_ms) AS total_ms
FROM tenant_load
WHERE bucket >= $2 AND bucket < $3
GROUP BY b ORDER BY b
"""
_SERIES_FIELDS = ("cpu_pct", "load1", "load5", "load15", "mem_used_mb", "mem_total_mb",
                  "swap_used_mb", "disk_read_mb_s", "disk_write_mb_s", "net_rx_mb_s",
                  "net_tx_mb_s", "api_rss_mb", "api_cpu_pct", "db_size_mb", "db_pool_used",
                  "active_jobs")


async def series(range_: str) -> dict:
    """Downsampled points for the charts: host metrics averaged per `date_bin(step)`, request
    counters summed per bin from tenant_load. `tenant_load` is minute-grained, so for the 1h
    range (10 s bins) the request counts land on the bin that holds the minute's start."""
    key, window, step = resolve_range(range_)
    to = _now()
    frm = to - window
    interval = timedelta(seconds=step)
    async with pool().acquire() as conn:
        metrics = await conn.fetch(_SERIES_METRICS_SQL, interval, frm, to)
        load = await conn.fetch(_SERIES_LOAD_SQL, interval, frm, to)
        latest = _row_dict(await conn.fetchrow(
            "SELECT disk FROM system_metrics ORDER BY ts DESC LIMIT 1"))
    points: dict[datetime, dict] = {}
    for r in metrics:
        p = {"ts": r["b"], "requests": 0, "errors": 0, "avg_ms": None}
        for f in _SERIES_FIELDS:
            v = _num(r[f])
            p[f] = _round(v) if isinstance(v, float) else v
        points[r["b"]] = p
    for r in load:
        p = points.get(r["b"])
        if p is None:
            p = points[r["b"]] = {"ts": r["b"], **{f: None for f in _SERIES_FIELDS}}
        req, err, total = _int(r["requests"]), _int(r["errors"]), _int(r["total_ms"])
        p["requests"], p["errors"] = req, err
        p["avg_ms"] = _round(total / req, 1) if req else None
    return {
        "range": key, "step_s": step, "from": frm, "to": to,
        "points": [points[k] for k in sorted(points)],
        "disk_latest": (latest or {}).get("disk") or [],
    }


_TENANT_LOAD_SQL = """
SELECT l.principal_kind, l.client_key, l.client_id, c.slug, c.name,
       sum(l.requests) AS requests, sum(l.errors) AS errors, sum(l.total_ms) AS total_ms,
       max(l.max_ms) AS max_ms, sum(l.bytes_in) AS bytes_in, sum(l.bytes_out) AS bytes_out
FROM tenant_load l LEFT JOIN clients c ON c.id = l.client_id
WHERE l.bucket >= $1 AND l.bucket < $2
GROUP BY l.principal_kind, l.client_key, l.client_id, c.slug, c.name
"""
# llm_usage has no principal kind — a call is attributed to the workspace it was billed to.
_TENANT_AI_SQL = """
SELECT u.client_id, c.slug, c.name, count(*) AS ai_calls,
       coalesce(sum(coalesce(u.input_tokens, 0) + coalesce(u.output_tokens, 0)), 0) AS ai_tokens
FROM llm_usage u LEFT JOIN clients c ON c.id = u.client_id
WHERE u.created_at >= $1 AND u.created_at < $2
GROUP BY u.client_id, c.slug, c.name
"""
_TENANT_AUDIO_SQL = """
SELECT j.client_id, j.principal_type, c.slug, c.name, count(*) AS audio_jobs,
       coalesce(sum(j.processing_ms), 0) AS audio_ms
FROM audio_jobs j LEFT JOIN clients c ON c.id = j.client_id
WHERE j.created_at >= $1 AND j.created_at < $2
GROUP BY j.client_id, j.principal_type, c.slug, c.name
"""


def _blank_row(kind: str, client_key: str, client_id, slug, name) -> dict:
    return {"client_id": str(client_id) if client_id else None, "slug": slug, "name": name,
            "principal_kind": kind, "requests": 0, "errors": 0, "avg_ms": None, "max_ms": 0,
            "bytes_in": 0, "bytes_out": 0, "ai_calls": 0, "ai_tokens": 0, "audio_jobs": 0,
            "audio_ms": 0, "total_ms": 0, "load_share_pct": 0.0}


def _attach(rows: dict, client_key: str, kind_hint: str | None, client_id, slug, name) -> dict:
    """The row a usage figure belongs to. Prefer the exact (kind, client) row; else the busiest
    row for that client (an operator acting as the tenant shares its client_id); else a new
    row, so a tenant that only used the API key path still shows its AI spend."""
    if kind_hint in PRINCIPAL_KINDS and (kind_hint, client_key) in rows:
        return rows[(kind_hint, client_key)]
    same_client = [r for (k, ck), r in rows.items() if ck == client_key]
    if same_client:
        return max(same_client, key=lambda r: r["total_ms"])
    kind = kind_hint if kind_hint in PRINCIPAL_KINDS else ("anonymous" if not client_key else "tenant")
    row = rows[(kind, client_key)] = _blank_row(kind, client_key, client_id, slug, name)
    return row


async def tenant_table(range_: str) -> dict:
    """Per-(principal kind, client) load in the window, with AI calls/tokens (llm_usage) and
    recordings (audio_jobs) alongside. `load_share_pct` is each row's share of the summed
    request wall time — the honest 'server time' attribution, because analyze, transcription
    and the model calls all run inside the request."""
    key, window, _ = resolve_range(range_)
    to = _now()
    frm = to - window
    async with pool().acquire() as conn:
        load = await conn.fetch(_TENANT_LOAD_SQL, frm, to)
        ai = await conn.fetch(_TENANT_AI_SQL, frm, to)
        audio = await conn.fetch(_TENANT_AUDIO_SQL, frm, to)

    rows: dict[tuple[str, str], dict] = {}
    for r in load:
        row = _blank_row(r["principal_kind"], r["client_key"], r["client_id"], r["slug"], r["name"])
        row.update(requests=_int(r["requests"]), errors=_int(r["errors"]),
                   total_ms=_int(r["total_ms"]), max_ms=_int(r["max_ms"]),
                   bytes_in=_int(r["bytes_in"]), bytes_out=_int(r["bytes_out"]))
        rows[(r["principal_kind"], r["client_key"])] = row
    for r in ai:
        ck = str(r["client_id"]) if r["client_id"] else ""
        row = _attach(rows, ck, None, r["client_id"], r["slug"], r["name"])
        row["ai_calls"] += _int(r["ai_calls"])
        row["ai_tokens"] += _int(r["ai_tokens"])
    for r in audio:
        ck = str(r["client_id"]) if r["client_id"] else ""
        row = _attach(rows, ck, r["principal_type"], r["client_id"], r["slug"], r["name"])
        row["audio_jobs"] += _int(r["audio_jobs"])
        row["audio_ms"] += _int(r["audio_ms"])

    out = sorted(rows.values(), key=lambda r: (-r["total_ms"], -r["requests"], r["principal_kind"]))
    total = {"requests": 0, "errors": 0, "total_ms": 0, "bytes_in": 0, "bytes_out": 0,
             "ai_calls": 0, "ai_tokens": 0, "audio_jobs": 0, "audio_ms": 0}
    for r in out:
        for k in total:
            total[k] += r[k]
    for r in out:
        r["avg_ms"] = _round(r["total_ms"] / r["requests"], 1) if r["requests"] else None
        r["load_share_pct"] = (_round(r["total_ms"] * 100.0 / total["total_ms"], 1)
                               if total["total_ms"] else 0.0)
    return {"range": key, "from": frm, "to": to, "total": total, "rows": out}


def _deleted(tag: str) -> int:
    # asyncpg's command tag is "DELETE <n>".
    try:
        return int(tag.rsplit(" ", 1)[-1])
    except (ValueError, AttributeError):
        return 0


async def purge(retention_days: int) -> dict:
    """Drop rows older than `retention_days` from both tables. Two statements on purpose: a
    lock or a long delete on one table must not hold up the other, and the counts are
    reported separately so the worker log says which table grew."""
    days = settings_store._health_setting("retention_days", retention_days)
    cutoff = _now() - timedelta(days=days)
    async with pool().acquire() as conn:
        metrics_tag = await conn.execute("DELETE FROM system_metrics WHERE ts < $1", cutoff)
        load_tag = await conn.execute("DELETE FROM tenant_load WHERE bucket < $1", cutoff)
    return {"retention_days": days, "cutoff": cutoff,
            "metrics_deleted": _deleted(metrics_tag), "load_deleted": _deleted(load_tag)}
