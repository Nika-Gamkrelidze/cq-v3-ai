"""Per-tenant scoring config persistence (scoring_configs table), tenant-scoped by client_id.

A config is a versioned set of rubric dimensions with weights. Exactly one row per
client is active; the pipeline scores against the active one. Saving creates a new
version and flips the active flag atomically.
"""
import json

import asyncpg

from ..db import pool
from .scoring import normalize_dimensions


async def get_active_config(client_id: str) -> dict | None:
    if not client_id:
        return None
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT version, dimensions, weights, rubric, is_active, updated_at, updated_by
            FROM scoring_configs WHERE client_id = $1 AND is_active ORDER BY version DESC LIMIT 1
            """, client_id)
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


async def save_config(client_id: str, dimensions, rubric: str, updated_by: str) -> dict:
    """Validate + normalize, then persist a new active version. Raises ValueError if invalid."""
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
    weights = {d["key"]: d["weight"] for d in dims}
    # Retry on a version collision from a concurrent save for the same tenant
    # (both readers computed the same MAX(version)+1 -> UNIQUE(client_id, version)).
    for _attempt in range(3):
        try:
            async with pool().acquire() as conn:
                async with conn.transaction():
                    next_ver = await conn.fetchval(
                        "SELECT COALESCE(MAX(version), 0) + 1 FROM scoring_configs WHERE client_id = $1",
                        client_id)
                    await conn.execute(
                        "UPDATE scoring_configs SET is_active = false WHERE client_id = $1 AND is_active",
                        client_id)
                    await conn.execute(
                        """
                        INSERT INTO scoring_configs
                            (client_id, version, dimensions, weights, rubric, is_active, updated_at, updated_by)
                        VALUES ($1,$2,$3::jsonb,$4::jsonb,$5,true,now(),$6)
                        """,
                        client_id, next_ver, json.dumps(dims), json.dumps(weights),
                        (rubric or "").strip() or None, updated_by)
            break
        except asyncpg.UniqueViolationError:
            if _attempt == 2:
                raise
    return await get_active_config(client_id)
