"""Which AI a given tenant runs on.

`overlay()` answers "whose model and whose key does this call use", and `usage.py` answers
"what did that come to" — the second is only meaningful if the first is honest about who paid,
which is why the overlay reports `byo` and the usage rows carry it.

THE DEFAULT IS THE DEPLOYMENT'S OWN. Almost every tenant has no row here at all: they run on
the model and key in the admin settings, and their spend is ours. A row appears when a tenant
asks for something else — a different model, or their OWN provider key so the bill lands on
their account. Both are superadmin-managed on purpose: a tenant able to set its own base_url
could point the product at an endpoint that keeps every transcript it is handed.
"""
import logging
from typing import NamedTuple

from ..db import pool

log = logging.getLogger("cq")

# Small and short-lived: this is read on the hot path of every AI call, and a tenant's model
# changing is an operator action that can take a few seconds to land.
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_S = 30.0


def _now() -> float:
    import time
    return time.monotonic()


async def get_config(client_id: str) -> dict | None:
    """The raw row for a tenant, or None. `api_key` is included — callers that build an API
    response must use `public_config` instead."""
    if not client_id:
        return None
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT client_id, provider, model, api_key, base_url, enabled, notes,
                   updated_at, updated_by
            FROM tenant_ai_configs WHERE client_id = $1
            """, client_id)
    return dict(row) if row else None


async def public_config(client_id: str) -> dict:
    """What an operator may SEE. The key itself never leaves the server — only whether one is
    set — because a console that can display a credential is a console that can leak it."""
    row = await get_config(client_id)
    if not row:
        return {"enabled": False, "provider": "anthropic", "model": None,
                "base_url": None, "has_key": False, "notes": None,
                "updated_at": None, "updated_by": None}
    return {
        "enabled": bool(row["enabled"]),
        "provider": row["provider"] or "anthropic",
        "model": row["model"],
        "base_url": row["base_url"],
        "has_key": bool(row["api_key"]),
        "notes": row["notes"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": row["updated_by"],
    }


async def save_config(client_id: str, *, enabled: bool, provider: str | None,
                      model: str | None, base_url: str | None,
                      api_key: str | None, clear_key: bool, notes: str | None,
                      updated_by: str) -> dict:
    """Upsert a tenant's overrides.

    `api_key` is only written when a NEW one is supplied: the console cannot read the stored
    key, so it cannot send it back, and treating "absent" as "clear it" would wipe a
    tenant's credential every time an operator edited the model. Clearing is therefore an
    explicit `clear_key`.
    """
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tenant_ai_configs
                (client_id, provider, model, base_url, enabled, notes, api_key,
                 updated_at, updated_by)
            VALUES ($1, COALESCE($2,'anthropic'), $3, $4, $5, $6, $7, now(), $8)
            ON CONFLICT (client_id) DO UPDATE SET
                provider = COALESCE(EXCLUDED.provider, 'anthropic'),
                model = EXCLUDED.model,
                base_url = EXCLUDED.base_url,
                enabled = EXCLUDED.enabled,
                notes = EXCLUDED.notes,
                api_key = CASE
                    WHEN $9 THEN NULL                       -- explicitly cleared
                    WHEN $7 IS NOT NULL THEN $7             -- replaced
                    ELSE tenant_ai_configs.api_key          -- left alone
                END,
                updated_at = now(),
                updated_by = EXCLUDED.updated_by
            """,
            client_id, (provider or "anthropic").strip() or "anthropic",
            (model or "").strip() or None, (base_url or "").strip() or None,
            bool(enabled), (notes or "").strip() or None,
            (api_key or "").strip() or None, updated_by, bool(clear_key))
    _CACHE.pop(client_id, None)
    return await public_config(client_id)


class Overlay(NamedTuple):
    """What a call should actually run on, after the tenant's row is applied."""
    api_key: str
    model: str
    base_url: str | None
    byo: bool          # the spend is on the TENANT'S key, not ours


async def overlay(client_id: str | None, api_key: str, model: str) -> Overlay:
    """Apply a tenant's overrides on top of the deployment default.

    `api_key` and `model` are the default the caller already resolved from settings, so this
    does not re-read them; it only fetches the tenant's row (cached) and substitutes the
    fields that row actually sets. A tenant may override the model alone and still run on our
    key — the common request — or bring only a key, which is equally fine.

    THIS IS THE ONE PLACE THE OVERRIDE IS APPLIED. `llm.py` calls it for every Claude request,
    which is why the ~15 call sites do not each have to remember to. Anything that reads a key
    from settings and talks to Anthropic WITHOUT going through `llm.py` would silently bypass
    a tenant's configuration, so new AI code goes through `llm.py`.

    Never raises: a tenant whose config cannot be read runs on the default rather than losing
    the request, and the failure is logged.
    """
    default = Overlay(api_key=api_key, model=model, base_url=None, byo=False)
    if not client_id:
        return default

    hit = _CACHE.get(client_id)
    if hit and (_now() - hit[0]) < _TTL_S:
        row = hit[1]
    else:
        try:
            row = await get_config(client_id) or {}
        except Exception:  # noqa: BLE001 — a config lookup must never break an AI call
            log.exception("tenant AI config lookup failed for %s", client_id)
            return default
        _CACHE[client_id] = (_now(), row)

    if not row or not row.get("enabled"):
        return default
    return Overlay(
        api_key=row.get("api_key") or api_key,
        model=row.get("model") or model,
        base_url=row.get("base_url") or None,
        byo=bool(row.get("api_key")),
    )


def forget(client_id: str | None = None) -> None:
    """Drop the cache — for tests, and after a save."""
    if client_id:
        _CACHE.pop(client_id, None)
    else:
        _CACHE.clear()
