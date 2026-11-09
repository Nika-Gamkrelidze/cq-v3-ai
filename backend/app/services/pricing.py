"""The operator's AI price list and billing markup (db/pricing.sql).

Two tables, both VERSIONED by `effective_from`: a change of price or markup is a new row from a
date, and every call is costed by the row in force when it ran (`usage_price` in the SQL does
the lookup and the multiplication). Editing or deleting a row is allowed — it is how a typo is
corrected — and it re-prices the calls that row covered, which is what a correction means.

The cost of each call is STORED on it (`llm_usage.cost_usd` etc., see db/pricing.sql): a
trigger costs a call as it is recorded, and every write here re-costs exactly the calls the
change can reach (the provider's calls from the earliest date involved, or the workspace's,
or every workspace's for the default markup) inside the same transaction, so a report never
sees a price list and costs that disagree.

Everything here is superadmin-only (the router's dependency) and validated before it reaches
SQL: a malformed price is a 400 with a sentence, a second row for the same (provider, model,
kind, date) a 409.
"""
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation

import asyncpg

from ..db import pool
from . import usage

# USD per unit as the providers' own price pages state them: tokens per million, characters
# per thousand, audio per hour.
PRICE_FIELDS = ("input_per_mtok", "output_per_mtok", "cache_write_per_mtok",
                "cache_read_per_mtok", "audio_per_hour", "chars_per_k")
MAX_PRICE = Decimal("1000000")
MAX_MARKUP = Decimal("10000")


class PricingError(ValueError):
    """A request the caller got wrong; the router answers 400 with this sentence."""


class Conflict(PricingError):
    """The same version already exists; the router answers 409."""


def _text(value, name: str, limit: int, *, required: bool = True) -> str | None:
    v = (value or "").strip() if isinstance(value, str) or value is None else None
    if v is None:
        raise PricingError(f"`{name}` must be text.")
    if not v:
        if required:
            raise PricingError(f"`{name}` is required.")
        return None
    if len(v) > limit:
        raise PricingError(f"`{name}` is longer than {limit} characters.")
    return v


def _money(value, name: str, ceiling: Decimal) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise PricingError(f"`{name}` must be a number.")
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise PricingError(f"`{name}` must be a number.") from exc
    if not d.is_finite() or d < 0:
        raise PricingError(f"`{name}` cannot be negative.")
    if d > ceiling:
        raise PricingError(f"`{name}` is implausibly large.")
    return d


def _when(value, name: str = "effective_from") -> datetime:
    """A date (midnight UTC that day) or an ISO timestamp; empty = now."""
    if value is None or value == "":
        return datetime.now(timezone.utc)
    if not isinstance(value, str):
        raise PricingError(f"`{name}` must be a date like 2026-11-01.")
    v = value.strip()
    try:
        if len(v) == 10:
            dt = datetime.combine(date.fromisoformat(v), time.min, tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PricingError(f"`{name}` must be a date like 2026-11-01.") from exc
    if not 2000 <= dt.year <= 2100:
        raise PricingError(f"`{name}` is out of range.")
    return dt


def _uuid(value, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise PricingError(f"`{name}` must be an id.") from exc


def clean_price(body: dict) -> dict:
    """A price row from a request body, validated. Raises PricingError."""
    if not isinstance(body, dict):
        raise PricingError("Send a JSON object.")
    provider = _text(body.get("provider"), "provider", 64).lower()
    model = _text(body.get("model"), "model", 200)
    capability = _text(body.get("capability"), "capability", 20, required=False)
    if capability and capability not in usage.CAPABILITIES:
        raise PricingError(f"Unknown kind {capability!r}.")
    prices = {f: _money(body.get(f), f, MAX_PRICE) for f in PRICE_FIELDS}
    if all(v is None for v in prices.values()):
        raise PricingError("Set at least one price.")
    return {"provider": provider, "model": model, "capability": capability,
            "effective_from": _when(body.get("effective_from")),
            "note": _text(body.get("note"), "note", 500, required=False), **prices}


def clean_markup(body: dict) -> dict:
    """A markup row from a request body, validated. Raises PricingError."""
    if not isinstance(body, dict):
        raise PricingError("Send a JSON object.")
    raw = body.get("client_id")
    client_id = _uuid(raw, "client_id") if raw not in (None, "") else None
    pct = _money(body.get("pct"), "pct", MAX_MARKUP)
    if pct is None and client_id is None:
        raise PricingError("The default markup needs a percentage.")
    return {"client_id": client_id, "pct": pct, "effective_from": _when(body.get("effective_from")),
            "note": _text(body.get("note"), "note", 500, required=False)}


# ---------------------------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------------------------
async def overview() -> dict:
    """Everything the Prices tab shows: every price version, every markup version, and the
    (provider, model, kind) combinations that have calls with no price in force — the to-do
    list that makes the totals complete."""
    prov, cap = usage.provider_sql(), usage.capability_sql()
    async with pool().acquire() as conn:
        prices = await conn.fetch("""
            SELECT * FROM ai_prices
             ORDER BY provider, model, COALESCE(capability, ''), effective_from DESC""")
        markups = await conn.fetch("""
            SELECT m.*, c.name AS tenant_name
              FROM ai_markups m LEFT JOIN clients c ON c.id = m.client_id
             ORDER BY (m.client_id IS NOT NULL), c.name, m.effective_from DESC""")
        unpriced = await conn.fetch(f"""
            SELECT {prov} AS provider, u.model, {cap} AS capability,
                   COUNT(*)::bigint AS calls,
                   COALESCE(SUM(u.input_tokens), 0)::bigint  AS input_tokens,
                   COALESCE(SUM(u.output_tokens), 0)::bigint AS output_tokens,
                   COALESCE(SUM(u.audio_seconds), 0)::float8 AS audio_seconds,
                   COALESCE(SUM(u.characters), 0)::bigint    AS characters,
                   MIN(u.created_at) AS first_used, MAX(u.created_at) AS last_used
              FROM {usage.PRICED} u
             WHERE u.cost_usd IS NULL
             GROUP BY 1, 2, 3 ORDER BY calls DESC""")
        models = await conn.fetch(f"""
            SELECT DISTINCT {prov} AS provider, u.model, {cap} AS capability
              FROM llm_usage u ORDER BY 1, 2, 3""")
        tenants = await conn.fetch("SELECT id AS client_id, name FROM clients ORDER BY name")
    return {
        "prices": [usage.record(r) for r in prices],
        "markups": [usage.record(r) for r in markups],
        "unpriced": [usage.record(r) for r in unpriced],
        "models": [usage.record(r) for r in models],
        "tenants": [usage.record(r) for r in tenants],
        "fields": list(PRICE_FIELDS),
    }


# ---------------------------------------------------------------------------------------------
# Re-costing
# ---------------------------------------------------------------------------------------------
# Set-based, with the costing function inlined, and writing only the calls whose answer
# CHANGED: one model's new price leaves the provider's other calls untouched on disk, which is
# most of the time a re-price takes (55 s -> the cost of reading, for 200k calls).
_REPRICE = """
    UPDATE llm_usage u
       SET price_id = q.price_id, cost_usd = q.cost_usd, markup_pct = q.markup_pct,
           billed_usd = q.billed_usd, priced_at = now()
      FROM (SELECT x.id, p.price_id, p.cost_usd, p.markup_pct, p.billed_usd
              FROM llm_usage x CROSS JOIN LATERAL usage_price(x) p
             WHERE x.created_at >= $1 AND {scope}) q
     WHERE u.id = q.id
       AND (u.price_id, u.cost_usd, u.markup_pct)
           IS DISTINCT FROM (q.price_id, q.cost_usd, q.markup_pct)
"""


async def _reprice_provider(conn, providers: set[str], since: datetime) -> int:
    """Re-cost every call of these providers made since `since` — the most a price row can
    reach: a '*' row covers every model of its provider, and a version covers calls from its
    own date until the next one."""
    scope = f"{usage.provider_sql('x')} = ANY($2::text[])"
    done = await conn.execute(_REPRICE.format(scope=scope), since, sorted(providers))
    return int(done.split()[-1])


async def _reprice_markup(conn, clients: set[str | None], since: datetime) -> int:
    """Re-cost the calls a markup row can reach: one workspace's, or — for the default (None) —
    every workspace's, since the default applies wherever there is no override."""
    if None in clients:
        done = await conn.execute(_REPRICE.format(scope="true"), since)
    else:
        done = await conn.execute(_REPRICE.format(scope="x.client_id = ANY($2::uuid[])"),
                                  since, sorted(clients))
    return int(done.split()[-1])


# ---------------------------------------------------------------------------------------------
# Writes — each one, and the re-costing it causes, in one transaction.
# ---------------------------------------------------------------------------------------------
_PRICE_COLS = ["provider", "model", "capability", "effective_from", "note", *PRICE_FIELDS]


async def add_price(body: dict, actor: str) -> dict:
    p = clean_price(body)
    try:
        async with pool().acquire() as conn, conn.transaction():
            row = await conn.fetchrow(f"""
                INSERT INTO ai_prices ({", ".join(_PRICE_COLS)}, created_by)
                VALUES ({", ".join(f"${i}" for i in range(1, len(_PRICE_COLS) + 2))})
                RETURNING *""", *[p[c] for c in _PRICE_COLS], actor)
            n = await _reprice_provider(conn, {p["provider"]}, p["effective_from"])
    except asyncpg.UniqueViolationError as exc:
        raise Conflict("A price for that model, kind and date already exists — edit it instead.") from exc
    return {**usage.record(row), "repriced_calls": n}


async def update_price(price_id: str, body: dict, actor: str) -> dict | None:
    pid = _uuid(price_id, "price id")
    p = clean_price(body)
    sets = ", ".join(f"{c} = ${i}" for i, c in enumerate(_PRICE_COLS, start=2))
    try:
        async with pool().acquire() as conn, conn.transaction():
            old = await conn.fetchrow(
                "SELECT provider, effective_from FROM ai_prices WHERE id = $1::uuid FOR UPDATE", pid)
            if old is None:
                return None
            row = await conn.fetchrow(f"""
                UPDATE ai_prices SET {sets}, updated_at = now(), updated_by = ${len(_PRICE_COLS) + 2}
                 WHERE id = $1::uuid RETURNING *""", pid, *[p[c] for c in _PRICE_COLS], actor)
            n = await _reprice_provider(conn, {old["provider"], p["provider"]},
                                        min(old["effective_from"], p["effective_from"]))
    except asyncpg.UniqueViolationError as exc:
        raise Conflict("Another price for that model, kind and date already exists.") from exc
    return {**usage.record(row), "repriced_calls": n}


async def delete_price(price_id: str) -> bool:
    pid = _uuid(price_id, "price id")
    async with pool().acquire() as conn, conn.transaction():
        old = await conn.fetchrow(
            "DELETE FROM ai_prices WHERE id = $1::uuid RETURNING provider, effective_from", pid)
        if old is None:
            return False
        await _reprice_provider(conn, {old["provider"]}, old["effective_from"])
    return True


async def add_markup(body: dict, actor: str) -> dict:
    m = clean_markup(body)
    try:
        async with pool().acquire() as conn, conn.transaction():
            row = await conn.fetchrow("""
                INSERT INTO ai_markups (client_id, pct, effective_from, note, created_by)
                VALUES ($1::uuid, $2, $3, $4, $5) RETURNING *""",
                                      m["client_id"], m["pct"], m["effective_from"], m["note"], actor)
            n = await _reprice_markup(conn, {m["client_id"]}, m["effective_from"])
    except asyncpg.UniqueViolationError as exc:
        raise Conflict("A markup for that workspace and date already exists — edit it instead.") from exc
    except asyncpg.ForeignKeyViolationError as exc:
        raise PricingError("No such workspace.") from exc
    return {**usage.record(row), "repriced_calls": n}


async def update_markup(markup_id: str, body: dict, actor: str) -> dict | None:
    mid = _uuid(markup_id, "markup id")
    m = clean_markup(body)
    try:
        async with pool().acquire() as conn, conn.transaction():
            old = await conn.fetchrow(
                "SELECT client_id, effective_from FROM ai_markups WHERE id = $1::uuid FOR UPDATE", mid)
            if old is None:
                return None
            row = await conn.fetchrow("""
                UPDATE ai_markups SET client_id = $2::uuid, pct = $3, effective_from = $4, note = $5,
                       updated_at = now(), updated_by = $6
                 WHERE id = $1::uuid RETURNING *""",
                                      mid, m["client_id"], m["pct"], m["effective_from"], m["note"], actor)
            old_client = str(old["client_id"]) if old["client_id"] else None
            n = await _reprice_markup(conn, {old_client, m["client_id"]},
                                      min(old["effective_from"], m["effective_from"]))
    except asyncpg.UniqueViolationError as exc:
        raise Conflict("Another markup for that workspace and date already exists.") from exc
    except asyncpg.ForeignKeyViolationError as exc:
        raise PricingError("No such workspace.") from exc
    return {**usage.record(row), "repriced_calls": n}


async def delete_markup(markup_id: str) -> bool:
    mid = _uuid(markup_id, "markup id")
    async with pool().acquire() as conn, conn.transaction():
        old = await conn.fetchrow(
            "DELETE FROM ai_markups WHERE id = $1::uuid RETURNING client_id, effective_from", mid)
        if old is None:
            return False
        client = str(old["client_id"]) if old["client_id"] else None
        await _reprice_markup(conn, {client}, old["effective_from"])
    return True
