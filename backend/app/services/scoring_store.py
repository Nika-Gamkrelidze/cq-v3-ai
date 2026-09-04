"""Scoring-rubric persistence (scoring_configs table): one versioned rubric per OWNER.

An owner is a tenant (`client_id`) or, since the workbench, a registered user (`user_id`);
a row carries exactly one of the two, and every query here is scoped by the owner column.
Exactly one row per owner is active; the pipeline scores against the active one. Saving
creates a new version — counters are per owner — and flips the active flag atomically.

Owners with no rubric of their own score against the DEFAULT rubric, resolved in this
order: the blob the superadmin stores under app_settings['default_rubric']; else the demo
tenant's active rubric (so a deployment that has been tuning its demo rubric keeps that
behaviour before the superadmin ever visits the new panel); else BUILTIN_DEFAULT. The default
is returned as a config with `version 0` and `is_default True`, never persisted for the
owner — "reset to default" is the one place that copies it into a real row.
"""
import json
from datetime import datetime, timezone

import asyncpg

from ..db import pool
from . import settings_store
from .scoring import normalize_dimensions

DEFAULT_RUBRIC_KEY = "default_rubric"
DEMO_SLUG = "demo"

BUILTIN_DEFAULT = {
    "dimensions": [
        {"key": "greeting", "name": "Greeting & identification", "weight": 15.0,
         "description": "The agent greets the caller and verifies who they are.",
         "guidance": "Full marks for a warm greeting plus identity verification; low if skipped."},
        {"key": "correctness", "name": "Correctness of information", "weight": 45.0,
         "description": "The information given to the caller is accurate.",
         "guidance": "High when every statement is correct; low for any wrong or misleading claim."},
        {"key": "problem_solving", "name": "Problem solving", "weight": 25.0,
         "description": "The caller's issue is understood and resolved.",
         "guidance": "High for a complete resolution or clear next step; low if the issue is left open."},
        {"key": "time_efficiency", "name": "Time efficiency", "weight": 15.0,
         "description": "The call stays focused and concise.",
         "guidance": "High for a focused call; low for avoidable delays, repetition or dead air."},
    ],
    "rubric": "Score each dimension 0-100 from the transcript. The weighted total is the "
              "auditable call-quality score.",
}

# The only two columns a rubric can be owned by. The column name is interpolated into SQL,
# so it must come from here (chosen by principal kind), never from request input.
_OWNER_COLUMNS = {"tenant": "client_id", "user": "user_id"}


def _owner_of(principal) -> tuple[str, str] | None:
    """(owner column, owner id) for a principal that can own a rubric, else None.

    Reads `kind`/`client_id`/`user_id` directly rather than `is_user`, which is being added
    to Principal concurrently."""
    if principal.kind == "tenant" and principal.client_id:
        return _OWNER_COLUMNS["tenant"], principal.client_id
    if principal.kind == "user" and principal.user_id:
        return _OWNER_COLUMNS["user"], principal.user_id
    return None


async def get_active_config(client_id: str) -> dict | None:
    if not client_id:
        return None
    return await _active(_OWNER_COLUMNS["tenant"], client_id)


async def get_active_config_for(principal) -> dict:
    """The rubric this principal scores against: their own active row when they have one,
    else the default (version 0, `is_default` True). Never None — every owner can score."""
    owner = _owner_of(principal)
    row = await _active(*owner) if owner else None
    if row:
        return {**row, "is_default": False}
    return await get_default_rubric()


async def _active(col: str, owner_id: str) -> dict | None:
    assert col in _OWNER_COLUMNS.values()
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT version, dimensions, weights, rubric, is_active, updated_at, updated_by
            FROM scoring_configs WHERE {col} = $1 AND is_active ORDER BY version DESC LIMIT 1
            """, owner_id)
    return _row_to_config(row) if row else None


def _row_to_config(row) -> dict:
    dims = row["dimensions"]
    if isinstance(dims, str):
        dims = json.loads(dims)
    weights = row["weights"]
    if isinstance(weights, str):
        weights = json.loads(weights)
    return {
        "version": row["version"],
        "dimensions": dims or [],
        "weights": weights or {},
        "rubric": row["rubric"] or "",
        "is_active": row["is_active"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": row["updated_by"],
    }


def _validated(dimensions) -> tuple[list[dict], dict]:
    """Normalize a dimension list and enforce the 100 % rule. Raises ValueError if invalid.

    Shared by every write path (tenant, user, default) so the default rubric can never be
    stored in a shape a tenant rubric would be refused in."""
    dims = normalize_dimensions(dimensions)
    if not dims:
        raise ValueError("At least one scoring dimension with a name is required.")
    if not any(d["weight"] for d in dims):
        # No weights given → distribute evenly, giving the rounding remainder to the last
        # dimension so the total is exactly 100.
        base = round(100 / len(dims), 2)
        for d in dims:
            d["weight"] = base
        dims[-1]["weight"] = round(dims[-1]["weight"] + (100 - base * len(dims)), 2)
    # Weights are percentages and must total 100 (small rounding tolerance).
    total = round(sum(d["weight"] for d in dims), 2)
    if abs(total - 100) > 0.5:
        raise ValueError(f"Dimension weights must total 100% (they currently total {total:g}%).")
    return dims, {d["key"]: d["weight"] for d in dims}


async def save_config(client_id: str, dimensions, rubric: str, updated_by: str) -> dict:
    """Validate + normalize, then persist a new active version. Raises ValueError if invalid."""
    await _save(_OWNER_COLUMNS["tenant"], client_id, dimensions, rubric, updated_by)
    return await get_active_config(client_id)


async def save_config_for(principal, dimensions, rubric: str, updated_by: str) -> dict:
    """save_config for either owner kind: writes `client_id` OR `user_id` from the principal.
    Raises ValueError for an invalid rubric or a principal that cannot own one."""
    owner = _owner_of(principal)
    if owner is None:
        raise ValueError("Only a tenant or a registered user can own a scoring rubric.")
    await _save(*owner, dimensions, rubric, updated_by)
    return await get_active_config_for(principal)


async def _save(col: str, owner_id: str, dimensions, rubric: str, updated_by: str) -> None:
    assert col in _OWNER_COLUMNS.values()
    dims, weights = _validated(dimensions)
    # Retry on a version collision from a concurrent save for the same owner (both readers
    # computed the same MAX(version)+1 -> UNIQUE(client_id, version) / (user_id, version)).
    for _attempt in range(3):
        try:
            async with pool().acquire() as conn:
                async with conn.transaction():
                    next_ver = await conn.fetchval(
                        f"SELECT COALESCE(MAX(version), 0) + 1 FROM scoring_configs WHERE {col} = $1",
                        owner_id)
                    await conn.execute(
                        f"UPDATE scoring_configs SET is_active = false WHERE {col} = $1 AND is_active",
                        owner_id)
                    await conn.execute(
                        f"""
                        INSERT INTO scoring_configs
                            ({col}, version, dimensions, weights, rubric, is_active, updated_at, updated_by)
                        VALUES ($1,$2,$3::jsonb,$4::jsonb,$5,true,now(),$6)
                        """,
                        owner_id, next_ver, json.dumps(dims), json.dumps(weights),
                        (rubric or "").strip() or None, updated_by)
            break
        except asyncpg.UniqueViolationError:
            if _attempt == 2:
                raise


# --------------------------------------------------------------------------- #
# Default rubric
# --------------------------------------------------------------------------- #
async def get_default_rubric() -> dict:
    """The rubric owners without one score against — see the module docstring for the
    resolution order. `source` says which step answered (stored | demo | builtin) so the
    admin panel can tell the superadmin what they are looking at before they edit it."""
    stored = await settings_store.get_blob(DEFAULT_RUBRIC_KEY)
    dims = normalize_dimensions(stored.get("dimensions"))
    if dims:
        return _as_default(dims, stored.get("rubric"), "stored",
                           updated_at=stored.get("updated_at"), updated_by=stored.get("updated_by"))
    demo = await _demo_config()
    if demo and demo["dimensions"]:
        return _as_default(demo["dimensions"], demo["rubric"], "demo",
                           updated_at=demo["updated_at"], updated_by=demo["updated_by"])
    return _as_default(BUILTIN_DEFAULT["dimensions"], BUILTIN_DEFAULT["rubric"], "builtin")


async def set_default_rubric(dimensions, rubric: str, updated_by: str = "superadmin") -> dict:
    """Validate like any rubric save, then store the blob. Returns the effective default."""
    dims, _weights = _validated(dimensions)
    await settings_store.set_blob(DEFAULT_RUBRIC_KEY, {
        "dimensions": dims, "rubric": (rubric or "").strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": updated_by,
    })
    return await get_default_rubric()


async def _demo_config() -> dict | None:
    """The demo tenant's active rubric, looked up by slug in one query (there may be no
    demo tenant at all on a fresh deployment)."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.version, s.dimensions, s.weights, s.rubric, s.is_active, s.updated_at, s.updated_by
            FROM scoring_configs s JOIN clients c ON c.id = s.client_id
            WHERE c.slug = $1 AND s.is_active ORDER BY s.version DESC LIMIT 1
            """, DEMO_SLUG)
    return _row_to_config(row) if row else None


def _as_default(dims: list[dict], rubric, source: str, *, updated_at=None, updated_by=None) -> dict:
    """Shape the default exactly like a stored config (same keys as `_row_to_config`) so every
    renderer treats it as one, plus the two markers: `version 0` and `is_default`."""
    return {
        "version": 0,
        "dimensions": dims,
        "weights": {d["key"]: d["weight"] for d in dims},
        "rubric": str(rubric or "").strip(),
        "is_active": True,
        "is_default": True,
        "source": source,
        "updated_at": updated_at,
        "updated_by": updated_by,
    }


# --------------------------------------------------------------------------- #
# Manual score edits — the audit trail behind audio_jobs.scoring
# --------------------------------------------------------------------------- #
async def revisions(job_id: str) -> list[dict]:
    """Every revision of one recording's scorecard, oldest first. Revision 1 is the model's."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT revision, scoring, edited_by, note, created_at
            FROM scoring_revisions WHERE job_id = $1 ORDER BY revision
            """, job_id)
    out = []
    for r in rows:
        scoring = r["scoring"]
        if isinstance(scoring, str):
            scoring = json.loads(scoring)
        out.append({"revision": r["revision"], "scoring": scoring, "edited_by": r["edited_by"],
                    "note": r["note"], "created_at": r["created_at"].isoformat()})
    return out


async def save_revision(job_id: str, scoring: dict, *, edited_by: str | None,
                        note: str | None = None, original: dict | None = None) -> int:
    """Append a revision and return its number.

    `original` is the model's own scorecard, written as revision 1 if this recording has no
    history yet. That backfill is what lets a scorecard produced BEFORE this table existed
    still show "what the machine said" the first time somebody edits it — without it, the
    first edit would silently become the earliest thing on record.

    The whole thing is one transaction taking the row's lock, so two reviewers editing the
    same scorecard at once get two revisions in a defined order rather than one overwriting
    the other's number.
    """
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT id FROM audio_jobs WHERE id = $1 FOR UPDATE", job_id)
            last = await conn.fetchval(
                "SELECT COALESCE(MAX(revision), 0) FROM scoring_revisions WHERE job_id = $1",
                job_id)
            if last == 0 and original is not None:
                await conn.execute(
                    """
                    INSERT INTO scoring_revisions (job_id, revision, scoring, edited_by, note)
                    VALUES ($1, 1, $2::jsonb, NULL, NULL)
                    """, job_id, json.dumps(original))
                last = 1
            nxt = last + 1
            await conn.execute(
                """
                INSERT INTO scoring_revisions (job_id, revision, scoring, edited_by, note)
                VALUES ($1, $2, $3::jsonb, $4, $5)
                """, job_id, nxt, json.dumps(scoring), edited_by, (note or None))
    return nxt


# --------------------------------------------------------------------------- #
# Score colour bands — a display preference, owned separately from the rubric
# --------------------------------------------------------------------------- #
# Three colours, two boundaries: below AMBER_FROM is red, below GREEN_FROM is amber, the rest
# is green. Kept out of scoring_configs on purpose — "reset the rubric" must not silently
# repaint everyone's scorecards, and resetting the colours must not throw away the rubric.
DEFAULT_BANDS = {"amber_from": 50, "green_from": 80}


def owner_key(principal) -> str:
    """The row these thresholds belong to. Tenants share one setting across their users: the
    colours are how a WORKSPACE reads its scores, not a personal preference."""
    if getattr(principal, "kind", None) == "user" and principal.user_id:
        return f"user:{principal.user_id}"
    if principal.client_id:
        return f"tenant:{principal.client_id}"
    return "default"


def normalize_bands(amber_from, green_from) -> dict:
    """Clamp to a usable, ordered pair, or raise ValueError. Rejects the states the CHECK
    constraint would reject anyway, but with a message a person can act on."""
    try:
        amber, green = int(amber_from), int(green_from)
    except (TypeError, ValueError):
        raise ValueError("Both thresholds must be whole numbers.") from None
    if not 1 <= amber <= 99:
        raise ValueError("The amber threshold must be between 1 and 99.")
    if not amber < green <= 100:
        raise ValueError("The green threshold must be above the amber one, and at most 100.")
    return {"amber_from": amber, "green_from": green}


async def get_bands(principal) -> dict:
    key = owner_key(principal)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT amber_from, green_from, updated_at, updated_by FROM score_bands WHERE owner_key = $1",
            key)
    if not row:
        return {**DEFAULT_BANDS, "is_default": True}
    return {"amber_from": row["amber_from"], "green_from": row["green_from"],
            "is_default": False,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            "updated_by": row["updated_by"]}


async def set_bands(principal, amber_from, green_from, updated_by: str) -> dict:
    bands = normalize_bands(amber_from, green_from)
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO score_bands (owner_key, amber_from, green_from, updated_by)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (owner_key) DO UPDATE
                SET amber_from = EXCLUDED.amber_from, green_from = EXCLUDED.green_from,
                    updated_by = EXCLUDED.updated_by, updated_at = now()
            """, owner_key(principal), bands["amber_from"], bands["green_from"], updated_by)
    return await get_bands(principal)


async def reset_bands(principal) -> dict:
    """Back to the built-in thresholds by DELETING the row, not by writing the defaults into
    it: an owner who has never chosen and one who reset to the same numbers then read the same,
    and a later change to DEFAULT_BANDS reaches both."""
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM score_bands WHERE owner_key = $1", owner_key(principal))
    return await get_bands(principal)
