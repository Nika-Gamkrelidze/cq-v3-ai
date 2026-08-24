"""Per-tenant sentiment configuration: on/off + free-text guidance.

One row per client, plain UPSERT — deliberately not the versioned scoring_configs pattern.
See db/sentiment_config.sql for why. The public app's equivalent lives in settings_store
(PUBLIC_SENTIMENT_KEY): superadmin-only and global, no client_id to key a row by.
"""
from __future__ import annotations

from ..db import pool

DEFAULTS = {"enabled": True, "guidance": ""}


async def get_tenant_config(client_id: str) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled, guidance, updated_at FROM sentiment_configs WHERE client_id = $1",
            client_id)
    if not row:
        return dict(DEFAULTS)
    return {"enabled": row["enabled"], "guidance": row["guidance"] or "",
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None}


async def save_tenant_config(client_id: str, *, enabled: bool, guidance: str,
                             actor: str) -> dict:
    guidance = (guidance or "").strip()
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sentiment_configs (client_id, enabled, guidance, updated_by)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (client_id) DO UPDATE
                SET enabled = EXCLUDED.enabled, guidance = EXCLUDED.guidance,
                    updated_by = EXCLUDED.updated_by, updated_at = now()
            """, client_id, bool(enabled), guidance, actor)
    return await get_tenant_config(client_id)
