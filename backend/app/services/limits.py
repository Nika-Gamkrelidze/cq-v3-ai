"""Anonymous usage limits — check/reserve quota and report remaining.

No-op for tenants and superadmins (unlimited). Limits come from the admin-configured
'anonymous' settings; usage is counted per anon_key per day.
"""
import datetime as dt

from fastapi import HTTPException

from ..db import pool
from . import settings_store
from .auth import Principal

# kind -> (usage column, per-day-limit key, feature flag key)
_KIND = {
    "analyses": ("analyses", "max_analyses_per_day", "analyze"),
    "tts": ("tts", "max_tts_per_day", "tts"),
}


async def reserve(principal: Principal, kind: str, size_bytes: int = 0) -> None:
    if principal.kind != "anonymous":
        return
    col, max_key, feature = _KIND[kind]
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
