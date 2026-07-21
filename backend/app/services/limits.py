"""Usage limits — check/reserve quota and report remaining.

Two mechanisms live here, deliberately kept separate:

  * `reserve()` / `snapshot()` — the *anonymous* quota, counted per anon_key per day in
    `anon_usage`. Limits come from the admin-configured 'anonymous' settings.
    `snapshot()`'s shape is read by the frontend — do not change it.
  * `reserve_counter()` — the general metering primitive on `usage_counters`, used for the
    tenant/integration dimensions (end-user/hour, tenant/minute, tenant/day) that the chat
    endpoints enforce.

`reserve()` is also the scope gate for the paid features, not only a counter: an integration
credential (chat scopes only) is rejected outright, because "no branch matched" must never mean
"allowed, unmetered".

`reserve()` no longer no-ops for tenants: a tenant call is counted on `usage_counters` under
`tenant:<client_id>`, and rejected only when that tenant has a cap configured in
`clients.settings`. Counting unconditionally is the point — an uncapped tenant is still a
tenant whose spend has to be visible, and retrofitting the counter later means a blind period.
Superadmin stays exempt: it is the operator, not a customer.

The anonymous path is intentionally NOT migrated onto `usage_counters` in this phase — that is
a data migration plus a frontend contract change, and it is not what the chat work needs.
"""
import datetime as dt
import logging

from fastapi import HTTPException

from ..db import pool
from . import settings_store
from .auth import Principal

log = logging.getLogger("cq")

# kind -> (usage column, per-day-limit key, feature flag key)
_KIND = {
    "analyses": ("analyses", "max_analyses_per_day", "analyze"),
    "tts": ("tts", "max_tts_per_day", "tts"),
}


async def reserve(principal: Principal, kind: str, size_bytes: int = 0) -> None:
    col, max_key, feature = _KIND[kind]
    if principal.kind == "integration":
        # A chat integration credential holds `chat:*` scopes ONLY — never `analyses` or `tts`.
        # It used to fall through the bottom of this function to a bare `return`, which is the
        # worst possible outcome for a quota gate: no cap, no counter, and the caller proceeds
        # to the provider. So a scoped credential reached /analyze and /v1/tts and spent
        # Anthropic and ElevenLabs money outside its scope and outside all metering. Rejecting
        # here (rather than in each route) means a new paid route inherits the refusal instead
        # of having to remember it.
        raise HTTPException(
            status_code=403,
            detail="This integration credential is not permitted to use this feature.")
    if principal.kind == "tenant" and principal.client_id:
        await reserve_counter(f"tenant:{principal.client_id}", kind,
                              await _tenant_limit(principal.client_id, max_key))
        return
    if principal.kind != "anonymous":
        return
    cfg = await settings_store.get_anonymous_config()
    if not cfg.get("enabled", True):
        raise HTTPException(status_code=403, detail="Anonymous access is disabled. Please sign in.")
    if not (cfg.get("features") or {}).get(feature, True):
        raise HTTPException(status_code=403,
                            detail="This feature is disabled for anonymous users. Please sign in.")
    if kind == "analyses":
        mb = int(cfg.get("max_audio_mb") or 0)
        if mb and size_bytes > mb * 1024 * 1024:
            raise HTTPException(status_code=413,
                                detail=f"Anonymous uploads are limited to {mb} MB. Sign in for more.")
    limit = int(cfg.get(max_key) or 0)
    today = dt.date.today()
    async with pool().acquire() as conn:
        used = await conn.fetchval(
            f"SELECT {col} FROM anon_usage WHERE anon_key = $1 AND day = $2",
            principal.anon_key, today) or 0
        if limit and used >= limit:
            raise HTTPException(status_code=429,
                                detail=f"Daily anonymous limit reached ({limit}). Sign in to continue.")
        await conn.execute(
            f"""
            INSERT INTO anon_usage (anon_key, day, {col}) VALUES ($1, $2, 1)
            ON CONFLICT (anon_key, day) DO UPDATE SET {col} = anon_usage.{col} + 1, updated_at = now()
            """, principal.anon_key, today)


# ---- general metering (usage_counters) -------------------------------------
# Bucket granularities, as a pure string discriminator: the resolved label IS the bucket
# column, so a day row ("2026-07-21") and an hour row ("2026-07-21T14") can never collide.
_BUCKET_FMT = {
    "day": "%Y-%m-%d",
    "hour": "%Y-%m-%dT%H",
    "minute": "%Y-%m-%dT%H:%M",
}


def _bucket_label(bucket: str) -> str:
    try:
        fmt = _BUCKET_FMT[bucket]
    except KeyError:
        raise ValueError(f"unknown bucket granularity: {bucket!r}") from None
    # UTC, not local time: the server's TZ is not a thing a quota window should depend on.
    return dt.datetime.now(dt.timezone.utc).strftime(fmt)


async def _tenant_limit(client_id: str, max_key: str) -> int:
    """A tenant's per-day cap for one kind, from its own `clients.settings` blob.

    Absent/unparseable means 0 = uncapped (usage is still counted). Per-tenant rather than a
    global admin setting because caps are a commercial term of one customer's contract.
    """
    async with pool().acquire() as conn:
        raw = await conn.fetchval(
            "SELECT settings ->> $2 FROM clients WHERE id = $1", client_id, max_key)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        log.warning("client %s has a non-numeric %s in settings: %r", client_id, max_key, raw)
        return 0


async def reserve_counter(scope_key: str, kind: str, limit: int, bucket: str = "day") -> None:
    """Atomically consume one unit of `kind` for `scope_key` in the current `bucket` window.

    Raises HTTPException(429) when the cap is already reached. `limit <= 0` means UNCAPPED,
    not unmetered: the unit is still counted, it just can never be rejected. Cost visibility
    must not depend on somebody having configured a limit first.

    `scope_key` is a free-text dimension carrier (e.g. "tenant:<client_id>",
    "enduser:<integration_id>:<hash>") — that is why `usage_counters` has no client_id column:
    one table and one statement cover every dimension.

    The whole check-and-increment is ONE statement, which is the point. The WHERE clause sits on
    the DO UPDATE, so when the row is already at the cap the update is skipped — and a skipped
    ON CONFLICT DO UPDATE produces NO row from RETURNING. So "fetchval returned None" is exactly
    "cap reached", with no read-then-write window for a concurrent request to slip through. (The
    INSERT branch always returns, so a first-ever request is never falsely rejected.)
    """
    label = _bucket_label(bucket)
    # Uncapped still counts: the guard degrades to an always-true predicate rather than to a
    # skipped write, so both paths are the same single statement.
    guard = "usage_counters.n < $4" if limit > 0 else "true"
    args = (scope_key, label, kind) + ((limit,) if limit > 0 else ())
    async with pool().acquire() as conn:
        n = await conn.fetchval(
            f"""
            INSERT INTO usage_counters (scope_key, bucket, kind, n) VALUES ($1, $2, $3, 1)
            ON CONFLICT (scope_key, bucket, kind) DO UPDATE
                SET n = usage_counters.n + 1, updated_at = now()
              WHERE {guard}
            RETURNING n
            """, *args)
    if n is None:
        log.info("quota exhausted scope=%s kind=%s bucket=%s limit=%s", scope_key, kind, label, limit)
        raise HTTPException(status_code=429,
                            detail=f"Rate limit reached for {kind} ({limit} per {bucket}).")


async def snapshot(principal: Principal) -> dict:
    if principal.kind != "anonymous":
        return {"anonymous": False, "unlimited": True, "kind": principal.kind,
                "client_id": principal.client_id}
    cfg = await settings_store.get_anonymous_config()
    today = dt.date.today()
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT analyses, tts FROM anon_usage WHERE anon_key = $1 AND day = $2",
            principal.anon_key, today)
    ua = (row["analyses"] if row else 0) or 0
    ut = (row["tts"] if row else 0) or 0
    ma = int(cfg.get("max_analyses_per_day") or 0)
    mt = int(cfg.get("max_tts_per_day") or 0)
    return {
        "anonymous": True,
        "enabled": cfg.get("enabled", True),
        "features": cfg.get("features") or {},
        "max_analyses_per_day": ma,
        "max_tts_per_day": mt,
        "max_audio_mb": int(cfg.get("max_audio_mb") or 0),
        "used": {"analyses": ua, "tts": ut},
        "remaining": {
            "analyses": max(ma - ua, 0) if ma else None,
            "tts": max(mt - ut, 0) if mt else None,
        },
    }
