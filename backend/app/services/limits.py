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

`check()` is `reserve()` with the counting taken out, for the one caller that cannot reserve
first: `/convert` has to read a whole multipart batch into memory before it knows how many
files it is being asked to pay for, and buffering 150 MB for someone whose allowance ran out
hours ago is exactly the cost this meter exists to refuse. It is read-only and therefore racy
by construction — the atomic statement in `reserve()` is still what decides.

`reserve()` no longer no-ops for tenants: a tenant call is counted on `usage_counters` under
`tenant:<client_id>`, and rejected only when that tenant has a cap configured in
`clients.settings`. Counting unconditionally is the point — an uncapped tenant is still a
tenant whose spend has to be visible, and retrofitting the counter later means a blind period.
Superadmin stays exempt: it is the operator, not a customer.

The anonymous path is intentionally NOT migrated onto `usage_counters` in this phase — that is
a data migration plus a frontend contract change, and it is not what the chat work needs.

Registered users (`principal.kind == "user"`) are the third tier: counted on `usage_counters`
under `user:<user_id>` (per-day buckets, like tenants), capped by the admin-configured
'registered' blob with a per-user override in `app_users.limits` winning when it names the key.
The tier's `enabled` switch closes SIGN-UPS only; an existing account is refused solely through
`app_users.is_active`, which is re-read on every `reserve()` because session tokens are
stateless and would otherwise outlive a deactivation for up to token_ttl_hours.
"""
import datetime as dt
import json
import logging

from fastapi import HTTPException

from ..db import pool
from . import settings_store
from .auth import Principal

log = logging.getLogger("cq")

# kind -> (usage column, per-day-limit key, feature flag key, built-in anonymous cap)
#
# The fourth element is the cap that applies when `settings_store.ANON_DEFAULTS` has no dial
# for this kind yet. It exists because the alternative is worse than a wrong number: a kind
# added here before the admin panel grows a field for it would read `cfg.get(...) -> None`
# and come out UNCAPPED, which is the one state a public, unauthenticated meter must never
# default to. `None` means "the settings blob always has this key", which is true of the two
# original kinds.
#
# `conversions` counts FILES, not requests. It is the one CPU-expensive thing an unregistered
# visitor can ask this box for — ffmpeg demuxing a video shares the machine with the TEI
# encoder that serves live retrieval — so a thirty-file batch is thirty units, not one.
_KIND = {
    "analyses": ("analyses", "max_analyses_per_day", "analyze", None),
    "tts": ("tts", "max_tts_per_day", "tts", None),
    "conversions": ("conversions", "max_conversions_per_day", "convert", 60),
}


def _anon_limit(cfg: dict, max_key: str, default_max: int | None) -> int:
    """The anonymous per-day cap for one kind: 0 means uncapped (still counted).

    A key the operator has actually set wins, INCLUDING an explicit 0 — that is them saying
    uncapped, and this must not second-guess it. Only an absent (or unparseable) key falls
    back to the kind's built-in cap.
    """
    raw = cfg.get(max_key)
    if raw in (None, ""):
        return int(default_max or 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        log.warning("anonymous %s is not a number: %r; using the built-in %s",
                    max_key, raw, default_max)
        return int(default_max or 0)


# The refusals `reserve()` and `check()` must phrase IDENTICALLY — a caller has to get the same
# answer from the door as from the till, or the precheck becomes its own little policy that
# drifts from the real one.
def _integration_refused() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail="This integration credential is not permitted to use this feature.")


def _anon_gate(cfg: dict, feature: str) -> None:
    """The anonymous refusals that are policy rather than counting."""
    if not cfg.get("enabled", True):
        raise HTTPException(status_code=403, detail="Anonymous access is disabled. Please sign in.")
    if not (cfg.get("features") or {}).get(feature, True):
        raise HTTPException(status_code=403,
                            detail="This feature is disabled for anonymous users. Please sign in.")


def _anon_exhausted(limit: int) -> HTTPException:
    return HTTPException(status_code=429,
                         detail=f"Daily anonymous limit reached ({limit}). Sign in to continue.")


def _counter_exhausted(kind: str, limit: int, bucket: str) -> HTTPException:
    return HTTPException(status_code=429,
                         detail=f"Rate limit reached for {kind} ({limit} per {bucket}).")


# ---- registered users -------------------------------------------------------
def _registered_gate(cfg: dict, feature: str) -> None:
    """The registered refusals that are policy rather than counting. Note what is NOT here:
    `cfg["enabled"]` — that switch closes sign-ups, it does not lock existing accounts out."""
    if not (cfg.get("features") or {}).get(feature, True):
        raise HTTPException(status_code=403,
                            detail="This feature is disabled for registered users.")


def _user_cap(overrides: dict, cfg: dict, key: str) -> int:
    """One registered cap: the per-user override when present and numeric, else the tier's.
    0 = uncapped (still counted).

    An override is a commercial exception for one account, so it wins outright — including an
    explicit 0 meaning "no cap for this person". Anything that is not a number (a bool, a word,
    an empty string) is ignored rather than treated as 0, because a typo in an override must not
    silently hand out unlimited usage.
    """
    for source, raw in (("override", overrides.get(key)), ("registered tier", cfg.get(key))):
        if raw in (None, "") or isinstance(raw, bool):
            continue
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            log.warning("%s %s is not a number: %r; ignoring it", source, key, raw)
    return 0


def _overrides_of(row) -> dict:
    """`app_users.limits` as a dict (asyncpg hands jsonb back as a str unless a codec is set)."""
    raw = row["limits"] if row else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return raw if isinstance(raw, dict) else {}


async def _user_row(user_id: str):
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            "SELECT is_active, limits FROM app_users WHERE id = $1", user_id)


async def _user_account(user_id: str) -> dict:
    """The per-user overrides of an ACTIVE account; 403 for one that is disabled or deleted.
    One primary-key SELECT per reserve — the price of a stateless token honouring a
    deactivation immediately instead of at expiry."""
    row = await _user_row(user_id)
    if not row or not row["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")
    return _overrides_of(row)


async def usage_today(scope_keys: list[str]) -> dict[str, dict[str, int]]:
    """`{scope_key: {kind: n}}` for today's day bucket — one query for any number of scopes,
    so a console listing N users does not issue N counter reads. Scopes with no row today are
    simply absent."""
    if not scope_keys:
        return {}
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT scope_key, kind, n FROM usage_counters "
            "WHERE bucket = $1 AND scope_key = ANY($2::text[])",
            _bucket_label("day"), list(scope_keys))
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(r["scope_key"], {})[r["kind"]] = int(r["n"] or 0)
    return out


async def require_feature(principal: Principal, feature: str) -> None:
    """403 when a feature switch is off for the caller's tier; a no-op for everyone else.

    The switches that are not paid-unit kinds (`score`, `semantic`, `summarise` on the
    registered tier, `kb` on the anonymous one) have no `reserve()` call to hang off, so a
    route that gates on one calls this instead. Same refusal wording as the metered path, and
    the same `is_active` check for users, so the two doors can never disagree.
    """
    if principal.kind == "user" and principal.user_id:
        await _user_account(principal.user_id)
        _registered_gate(await settings_store.get_registered_config(), feature)
    elif principal.kind == "anonymous":
        _anon_gate(await settings_store.get_anonymous_config(), feature)


async def reserve(principal: Principal, kind: str, size_bytes: int = 0) -> None:
    col, max_key, feature, default_max = _KIND[kind]
    if principal.kind == "integration":
        # A chat integration credential holds `chat:*` scopes ONLY — never `analyses` or `tts`.
        # It used to fall through the bottom of this function to a bare `return`, which is the
        # worst possible outcome for a quota gate: no cap, no counter, and the caller proceeds
        # to the provider. So a scoped credential reached /analyze and /v1/tts and spent
        # Anthropic and ElevenLabs money outside its scope and outside all metering. Rejecting
        # here (rather than in each route) means a new paid route inherits the refusal instead
        # of having to remember it.
        raise _integration_refused()
    if principal.kind == "tenant" and principal.client_id:
        # An operator is tenant-SHAPED but is not the customer: metering their support work
        # against the customer's paid allowance would bill the customer for CQ's own
        # troubleshooting, and — worse — would let a customer who has spent their day's
        # quota lock support out of the very account that needs looking at. Their spend is
        # still recorded per-workspace in `llm_usage`; it just does not consume the plan.
        if getattr(principal, "is_operator", False):
            return
        await reserve_counter(f"tenant:{principal.client_id}", kind,
                              await _tenant_limit(principal.client_id, max_key))
        return
    if principal.kind == "user" and principal.user_id:
        overrides = await _user_account(principal.user_id)
        cfg = await settings_store.get_registered_config()
        _registered_gate(cfg, feature)
        if kind == "analyses":
            mb = _user_cap(overrides, cfg, "max_audio_mb")
            if mb and size_bytes > mb * 1024 * 1024:
                raise HTTPException(status_code=413,
                                    detail=f"Uploads are limited to {mb} MB for your account.")
        await reserve_counter(f"user:{principal.user_id}", kind,
                              _user_cap(overrides, cfg, max_key))
        return
    if principal.kind != "anonymous":
        return
    cfg = await settings_store.get_anonymous_config()
    _anon_gate(cfg, feature)
    if kind == "analyses":
        mb = int(cfg.get("max_audio_mb") or 0)
        if mb and size_bytes > mb * 1024 * 1024:
            raise HTTPException(status_code=413,
                                detail=f"Anonymous uploads are limited to {mb} MB. Sign in for more.")
    limit = _anon_limit(cfg, max_key, default_max)
    today = dt.date.today()
    async with pool().acquire() as conn:
        used = await conn.fetchval(
            f"SELECT {col} FROM anon_usage WHERE anon_key = $1 AND day = $2",
            principal.anon_key, today) or 0
        if limit and used >= limit:
            raise _anon_exhausted(limit)
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
        raise _counter_exhausted(kind, limit, bucket)


async def check(principal: Principal, kind: str) -> None:
    """Refuse a caller who has nothing left — WITHOUT spending a unit.

    The door in front of `reserve()`, for a route that cannot reserve first. `/convert` has to
    read the whole multipart batch into memory before it knows how many files it is being asked
    to pay for (the SSE generator outlives the `UploadFile` objects, so it cannot stream them),
    and reserving after that read means an anonymous visitor whose allowance ran out hours ago
    can still make this process hold 150 MB of their upload in RAM, once per request, as often
    as they like. The meter exists to bound what an unauthenticated visitor can cost us; a meter
    that only engages after the expensive part does not do that.

    Read-only, so two concurrent callers can both pass it. That is fine and is not what it is
    for: the single atomic statement in `reserve()`/`reserve_counter()` is still what decides,
    and the worst a race here can do is let through a batch that reserve then truncates.

    Refuses only on the states that are ALREADY true — disabled, out of scope, at the cap. It
    never rejects a caller who has some allowance left but less than the batch needs: that is a
    truncation, and truncation is `reserve()`'s job, per file, with the files already paid for
    still converted.
    """
    col, max_key, feature, default_max = _KIND[kind]
    if principal.kind == "integration":
        raise _integration_refused()
    if principal.kind == "tenant" and principal.client_id:
        limit = await _tenant_limit(principal.client_id, max_key)
        if limit <= 0:                      # uncapped: counted, never refused
            return
        async with pool().acquire() as conn:
            used = await conn.fetchval(
                "SELECT n FROM usage_counters WHERE scope_key = $1 AND bucket = $2 AND kind = $3",
                f"tenant:{principal.client_id}", _bucket_label("day"), kind) or 0
        if used >= limit:
            raise _counter_exhausted(kind, limit, "day")
        return
    if principal.kind == "user" and principal.user_id:
        overrides = await _user_account(principal.user_id)
        cfg = await settings_store.get_registered_config()
        _registered_gate(cfg, feature)
        limit = _user_cap(overrides, cfg, max_key)
        if limit <= 0:                      # uncapped: counted, never refused
            return
        scope = f"user:{principal.user_id}"
        used = (await usage_today([scope])).get(scope, {}).get(kind, 0)
        if used >= limit:
            raise _counter_exhausted(kind, limit, "day")
        return
    if principal.kind != "anonymous":
        return
    cfg = await settings_store.get_anonymous_config()
    _anon_gate(cfg, feature)
    limit = _anon_limit(cfg, max_key, default_max)
    if not limit:
        return
    async with pool().acquire() as conn:
        used = await conn.fetchval(
            f"SELECT {col} FROM anon_usage WHERE anon_key = $1 AND day = $2",
            principal.anon_key, dt.date.today()) or 0
    if used >= limit:
        raise _anon_exhausted(limit)


async def _user_snapshot(user_id: str) -> dict:
    """Today's usage vs caps for one registered account, in the anonymous snapshot's shape.

    Deliberately does not 403 a disabled or deleted account the way `reserve()` does: this is
    what the account page renders on load, and a banner is a better place to learn you are
    switched off than a broken page. `active` carries that state instead.
    """
    row = await _user_row(user_id)
    overrides = _overrides_of(row)
    cfg = await settings_store.get_registered_config()
    scope = f"user:{user_id}"
    used = (await usage_today([scope])).get(scope, {})
    ua, ut, uc = used.get("analyses", 0), used.get("tts", 0), used.get("conversions", 0)
    ma = _user_cap(overrides, cfg, "max_analyses_per_day")
    mt = _user_cap(overrides, cfg, "max_tts_per_day")
    mc = _user_cap(overrides, cfg, "max_conversions_per_day")
    return {
        "anonymous": False,
        "registered": True,
        "kind": "user",
        "user_id": user_id,
        "active": bool(row and row["is_active"]),
        "features": cfg.get("features") or {},
        "max_analyses_per_day": ma,
        "max_tts_per_day": mt,
        "max_conversions_per_day": mc,
        "max_audio_mb": _user_cap(overrides, cfg, "max_audio_mb"),
        "used": {"analyses": ua, "tts": ut, "conversions": uc},
        "remaining": {
            "analyses": max(ma - ua, 0) if ma else None,
            "tts": max(mt - ut, 0) if mt else None,
            "conversions": max(mc - uc, 0) if mc else None,
        },
    }


async def snapshot(principal: Principal) -> dict:
    if principal.kind == "user" and principal.user_id:
        return await _user_snapshot(principal.user_id)
    if principal.kind != "anonymous":
        return {"anonymous": False, "unlimited": True, "kind": principal.kind,
                "client_id": principal.client_id}
    cfg = await settings_store.get_anonymous_config()
    today = dt.date.today()
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT analyses, tts, conversions FROM anon_usage "
            "WHERE anon_key = $1 AND day = $2",
            principal.anon_key, today)
    ua = (row["analyses"] if row else 0) or 0
    ut = (row["tts"] if row else 0) or 0
    uc = (row["conversions"] if row else 0) or 0
    ma = _anon_limit(cfg, "max_analyses_per_day", _KIND["analyses"][3])
    mt = _anon_limit(cfg, "max_tts_per_day", _KIND["tts"][3])
    mc = _anon_limit(cfg, "max_conversions_per_day", _KIND["conversions"][3])
    # Additive only: `analyses` and `tts` keep the exact names, types and nesting the
    # frontend already reads. `conversions` joins them in the same shape rather than in a
    # parallel structure, so one renderer keeps covering all three.
    return {
        "anonymous": True,
        "enabled": cfg.get("enabled", True),
        "features": cfg.get("features") or {},
        "max_analyses_per_day": ma,
        "max_tts_per_day": mt,
        "max_conversions_per_day": mc,
        "max_audio_mb": int(cfg.get("max_audio_mb") or 0),
        "used": {"analyses": ua, "tts": ut, "conversions": uc},
        "remaining": {
            "analyses": max(ma - ua, 0) if ma else None,
            "tts": max(mt - ut, 0) if mt else None,
            "conversions": max(mc - uc, 0) if mc else None,
        },
    }
