"""The LLM-only view of a tenant's AI configuration — a compatibility shim.

This module used to OWN the per-tenant override (table `tenant_ai_configs`). The registry
(`ai_registry.py`, `ai_resolve.py`) replaced it with three capabilities, named connections and
sealed keys, and the old rows were copied into `tenant_ai_overrides` as `capability='llm'` on
boot (services/migrate.py). Everything here now reads and writes THAT row, so the two callers
that still speak the old vocabulary keep working unchanged:

  * `/admin/ai-config/{tenant_id}` (routers/admin.py) — `public_config` / `save_config`;
  * `llm.overlay`-era call sites — `overlay()` is the full resolver chain, LLM capability,
    folded back into the old four-field tuple.

New code imports `ai_resolve.resolve` directly. Nothing here returns a key: `public_config`
carries `has_key` and a masked `key_hint`, and `overlay` is internal to the call path.
"""
import logging
from typing import NamedTuple

from . import ai_registry, ai_resolve

log = logging.getLogger("cq")


async def get_config(client_id: str) -> dict | None:
    """The tenant's LLM override in the legacy row shape, or None. `api_key` is the OPENED
    key — callers that build an API response must use `public_config` instead."""
    if not client_id:
        return None
    row = await ai_registry.get_override(client_id, "llm")
    if not row:
        return None
    return {"client_id": str(row["client_id"]), "provider": row["provider"],
            "model": row["model"], "api_key": ai_resolve.open_secret(row["api_key_enc"]),
            "base_url": row["base_url"], "enabled": bool(row["enabled"]),
            "notes": row["notes"], "updated_at": row["updated_at"],
            "updated_by": row["updated_by"]}


async def public_config(client_id: str) -> dict:
    """What an operator may SEE. The key itself never leaves the server — only whether one is
    set and a masked hint — because a console that can display a credential is a console
    that can leak it."""
    row = await get_config(client_id)
    if not row:
        return {"enabled": False, "provider": "anthropic", "model": None,
                "base_url": None, "has_key": False, "key_hint": "", "notes": None,
                "updated_at": None, "updated_by": None}
    return {
        "enabled": bool(row["enabled"]),
        "provider": row["provider"] or "anthropic",
        "model": row["model"],
        "base_url": row["base_url"],
        "has_key": bool(row["api_key"]),
        "key_hint": ai_registry._mask(row["api_key"] or ""),
        "notes": row["notes"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": row["updated_by"],
    }


async def save_config(client_id: str, *, enabled: bool, provider: str | None,
                      model: str | None, base_url: str | None,
                      api_key: str | None, clear_key: bool, notes: str | None,
                      updated_by: str) -> dict:
    """Upsert a tenant's LLM override (the superadmin shape: every field explicit, base_url
    allowed — this is the operator's route, never a tenant's).

    `api_key` is only written when a NEW one is supplied: the console cannot read the stored
    key, so it cannot send it back, and treating "absent" as "clear it" would wipe a
    tenant's credential every time an operator edited the model. Clearing is therefore an
    explicit `clear_key`. Raises `ai_registry.RegistryError` (400 with a `code`) for an
    unknown provider or a base_url the provider does not take.
    """
    await ai_registry.save_override(
        client_id, "llm", provider=(provider or "anthropic"), model=model, api_key=api_key,
        clear_key=clear_key, enabled=bool(enabled), notes=notes, base_url=base_url,
        updated_by=updated_by)
    forget(client_id)
    return await public_config(client_id)


class Overlay(NamedTuple):
    """What a call should actually run on, after the tenant's own row is applied."""
    api_key: str
    model: str
    base_url: str | None
    byo: bool          # the spend is on the TENANT'S key, not ours


# The overlay's own short cache of `get_config` rows, exactly as before the registry: it is
# the pre-registry algorithm kept whole for the callers (and tests) that still pin it.
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_S = 30.0


def _now() -> float:
    import time
    return time.monotonic()


async def overlay(client_id: str | None, api_key: str, model: str) -> Overlay:
    """DEPRECATED — the pre-registry overlay: the tenant's OWN LLM row (bring-your-own key
    and/or model) applied over the deployment default the caller passes in.

    It sees ONE layer of the chain — the tenant's own row — and none of the registry's
    default or assigned connections. Production call sites go through
    `ai_resolve.resolve(client_id, "llm", ...)`, which is the full chain; this stays so a
    caller written against the old shape keeps its old, exact behaviour rather than a
    silently different one. Never raises: a tenant whose row cannot be read runs on the
    default rather than losing the request, and the failure is logged.
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
    """Drop both caches — the overlay's and the resolver's — for tests, and after a save."""
    if client_id:
        _CACHE.pop(client_id, None)
    else:
        _CACHE.clear()
    ai_resolve.forget(client_id)
