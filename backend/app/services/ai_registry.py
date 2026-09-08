"""The AI provider registry: named connections, per-workspace assignment, bring-your-own keys.

Three tables (db/ai_connections.sql), one resolver (ai_resolve.py), and this module in between:
every write to the registry happens here, so the two invariants live in one place —

  * a provider key is SEALED on the way in (`seal_secret`) and opened only inside the
    resolver, at the moment of use; nothing here returns one. Public shapes carry `has_key`
    and a masked `key_hint`, and that is all any API response ever gets;
  * ONE default connection per capability, kept by a transaction that clears the previous
    default AND by a partial unique index, so a race cannot leave two behind.

Validation raises `RegistryError` with a machine `code` and the offending `field`; main.py turns
that into FastAPI's `{"detail": str}` plus the sibling `code`, so a console can point at the
input the operator has to fix.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..db import pool
from . import ai_resolve, settings_store
from .ai_resolve import Resolved, forget, open_secret
from .providers import catalog

log = logging.getLogger("cq")

CAPABILITIES = catalog.CAPABILITIES

# "Leave this field as it is" — distinct from None, which means "clear it". A PUT that omits
# `model` must not wipe the model, and a PUT that sends `"model": null` must.
KEEP = object()

SEED_ACTOR = "system:seed"
SEED_NAMES = {"llm": "Anthropic (deployment)", "stt": "ElevenLabs STT (deployment)",
              "tts": "ElevenLabs TTS (deployment)"}


class RegistryError(Exception):
    """A refused registry write. `code` is machine-readable; `field` names the input."""

    def __init__(self, message: str, *, code: str = "invalid", field: str | None = None,
                 status: int = 400):
        super().__init__(message)
        self.code, self.field, self.status = code, field, status


# ---------------------------------------------------------------------------
# Secrets: sealed on write here, opened in the resolver. One fallback, in ai_resolve.vault().
# ---------------------------------------------------------------------------
def seal_secret(plaintext: str) -> str:
    return ai_resolve.vault().seal(plaintext)


def secrets_status() -> dict:
    """`{"mode": "encrypted" | "plaintext"}` — never raises (health reads it)."""
    try:
        st = ai_resolve.vault().status() or {}
        return {"mode": "encrypted" if st.get("mode") == "encrypted" else "plaintext"}
    except Exception:  # noqa: BLE001
        return {"mode": "plaintext"}


def _mask(value: str) -> str:
    # Same shape the admin panel already shows for the legacy keys (settings_store._mask).
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 4 else "…"


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_jsonb = ai_resolve._jsonb


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def check_capability(capability: str) -> str:
    if capability not in CAPABILITIES:
        raise RegistryError(f"Unknown capability {capability!r}; expected one of "
                            f"{', '.join(CAPABILITIES)}.", code="invalid_capability",
                            field="capability")
    return capability


def _provider(capability: str, provider) -> str:
    p = (provider or "").strip().lower()
    if not catalog.is_known(capability, p):
        known = ", ".join(catalog.providers_for(capability)) or "none"
        raise RegistryError(f"Unknown {capability} provider {p or '(empty)'!r}; known: {known}.",
                            code="unknown_provider", field="provider")
    return p


def _name(name) -> str:
    n = (name or "").strip()
    if not n or len(n) > 120:
        raise RegistryError("A connection needs a name of 1-120 characters.",
                            code="invalid_name", field="name")
    return n


def _model(model) -> str | None:
    m = (model or "").strip() or None
    if m and len(m) > 200:
        raise RegistryError("model id is too long (max 200 characters).",
                            code="invalid_model", field="model")
    return m


def _base_url(capability: str, provider: str, base_url) -> str | None:
    u = (base_url or "").strip() or None
    if u is None:
        return None
    if not catalog.allows_base_url(capability, provider):
        raise RegistryError(f"{provider} does not take a base URL.",
                            code="base_url_not_allowed", field="base_url")
    if not (u.startswith("http://") or u.startswith("https://")) or len(u) > 500:
        raise RegistryError("base_url must be an http(s) URL.",
                            code="invalid_base_url", field="base_url")
    return u


def _settings(settings) -> dict:
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise RegistryError("settings must be a JSON object.", code="invalid_settings",
                            field="settings")
    # A key must not hide in the settings blob either — it has its own sealed column.
    for banned in ("api_key", "api_key_enc"):
        if banned in settings:
            raise RegistryError(f"settings may not carry {banned!r}; use api_key.",
                                code="invalid_settings", field="settings")
    try:
        json.dumps(settings)
    except (TypeError, ValueError) as exc:
        raise RegistryError("settings must be JSON-serialisable.", code="invalid_settings",
                            field="settings") from exc
    return {k: v for k, v in settings.items() if v not in (None, "")}


def _key(api_key) -> str | None:
    return (api_key or "").strip() or None


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
_CONN_COLS = ("id, name, capability, provider, model, base_url, api_key_enc, settings, "
              "is_active, is_default, last_test, created_at, updated_at, updated_by")


def public_connection(row) -> dict:
    """What an operator may SEE. The key is opened only to mask it, and the plaintext never
    leaves this function."""
    key = open_secret(row["api_key_enc"])
    return {
        "id": str(row["id"]), "name": row["name"], "capability": row["capability"],
        "provider": row["provider"], "model": row["model"], "base_url": row["base_url"],
        "has_key": bool(key), "key_hint": _mask(key),
        "settings": _jsonb(row["settings"]),
        "is_active": bool(row["is_active"]), "is_default": bool(row["is_default"]),
        "last_test": _jsonb(row["last_test"]) or None,
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
        "updated_by": row["updated_by"],
    }


async def _raw(conn, conn_id: str):
    return await conn.fetchrow(f"SELECT {_CONN_COLS} FROM ai_connections WHERE id = $1::uuid",
                               conn_id)


async def _raw_or_404(conn, conn_id: str):
    row = await _raw(conn, conn_id)
    if not row:
        raise RegistryError("Connection not found.", code="not_found", field="id", status=404)
    return row


async def list_connections(capability: str | None = None) -> list[dict]:
    """Every connection (inactive ones included, flagged), defaults first, then by name."""
    if capability:
        check_capability(capability)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_CONN_COLS} FROM ai_connections "
            "WHERE ($1::text IS NULL OR capability = $1) "
            "ORDER BY capability, is_default DESC, is_active DESC, lower(name), created_at",
            capability or None)
    return [public_connection(r) for r in rows]


async def get_connection(conn_id: str) -> dict:
    async with pool().acquire() as conn:
        return public_connection(await _raw_or_404(conn, conn_id))


async def create_connection(*, name, capability, provider, model=None, base_url=None,
                            api_key=None, settings=None, updated_by: str) -> dict:
    cap = check_capability(capability)
    prov = _provider(cap, provider)
    key = _key(api_key)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO ai_connections (name, capability, provider, model, base_url,
                                        api_key_enc, settings, updated_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
            RETURNING {_CONN_COLS}
            """,
            _name(name), cap, prov, _model(model), _base_url(cap, prov, base_url),
            seal_secret(key) if key else None, json.dumps(_settings(settings)), updated_by)
    forget()
    log.info("AI connection created: %s (%s/%s) by %s", row["name"], cap, prov, updated_by)
    return public_connection(row)


async def update_connection(conn_id: str, *, name=KEEP, capability=KEEP, provider=KEEP,
                            model=KEEP, base_url=KEEP, api_key=None, clear_key: bool = False,
                            settings=KEEP, is_active=KEEP, updated_by: str) -> dict:
    """Edit a connection. `api_key` absent keeps the stored key (the console cannot read it
    back, so it cannot echo it); `clear_key=True` removes it. Any change to what the
    connection actually talks to — provider, model, base_url, key — discards `last_test`:
    a green tick earned by the previous key would be a lie."""
    async with pool().acquire() as conn, conn.transaction():
        row = await _raw_or_404(conn, conn_id)
        cap = row["capability"]
        if capability is not KEEP and capability is not None and capability != cap:
            raise RegistryError("A connection's capability cannot change; create a new one.",
                                code="capability_immutable", field="capability")
        prov = row["provider"] if provider is KEEP or provider is None else _provider(cap, provider)
        new_name = row["name"] if name is KEEP or name is None else _name(name)
        new_model = row["model"] if model is KEEP else _model(model)
        stored_key = row["api_key_enc"]
        key = _key(api_key)
        if clear_key:
            new_key_enc = None
        elif key:
            new_key_enc = seal_secret(key)
        elif prov != row["provider"]:
            # A key belongs to a provider. Carrying the old one under a new provider would
            # store an Anthropic key as if it were OpenAI's.
            new_key_enc = None
        else:
            new_key_enc = stored_key
        if base_url is KEEP:
            new_base = row["base_url"] if catalog.allows_base_url(cap, prov) else None
        else:
            new_base = _base_url(cap, prov, base_url)
        new_settings = _jsonb(row["settings"]) if settings is KEEP else _settings(settings)
        active = bool(row["is_active"]) if is_active is KEEP or is_active is None else bool(is_active)
        changed = (prov != row["provider"] or new_model != row["model"]
                   or new_base != row["base_url"] or new_key_enc != stored_key)
        row = await conn.fetchrow(
            f"""
            UPDATE ai_connections
               SET name = $2, provider = $3, model = $4, base_url = $5, api_key_enc = $6,
                   settings = $7::jsonb, is_active = $8,
                   is_default = CASE WHEN $8 THEN is_default ELSE false END,
                   last_test = CASE WHEN $9 THEN NULL ELSE last_test END,
                   updated_at = now(), updated_by = $10
             WHERE id = $1::uuid
            RETURNING {_CONN_COLS}
            """,
            conn_id, new_name, prov, new_model, new_base, new_key_enc,
            json.dumps(new_settings), active, changed, updated_by)
    forget()
    return public_connection(row)


async def deactivate_connection(conn_id: str, *, updated_by: str) -> dict:
    """Never a hard delete: usage rows reference the id. A deactivated connection stops being
    the default (the resolver would skip it anyway) and stops being offered for assignment;
    an existing assignment to it falls back to the default."""
    async with pool().acquire() as conn:
        await _raw_or_404(conn, conn_id)
        row = await conn.fetchrow(
            f"""
            UPDATE ai_connections
               SET is_active = false, is_default = false, updated_at = now(), updated_by = $2
             WHERE id = $1::uuid
            RETURNING {_CONN_COLS}
            """, conn_id, updated_by)
    forget()
    log.info("AI connection deactivated: %s by %s", row["name"], updated_by)
    return public_connection(row)


async def set_default(conn_id: str, *, updated_by: str) -> dict:
    """Make this THE default for its capability. The previous default is cleared in the same
    transaction; the partial unique index is the belt to this braces."""
    async with pool().acquire() as conn, conn.transaction():
        row = await _raw_or_404(conn, conn_id)
        if not row["is_active"]:
            raise RegistryError("An inactive connection cannot be the default.",
                                code="connection_inactive", field="id")
        await conn.execute(
            "UPDATE ai_connections SET is_default = false, updated_at = now(), updated_by = $3 "
            "WHERE capability = $1 AND is_default AND id <> $2::uuid",
            row["capability"], conn_id, updated_by)
        row = await conn.fetchrow(
            f"""
            UPDATE ai_connections
               SET is_default = true, updated_at = now(), updated_by = $2
             WHERE id = $1::uuid
            RETURNING {_CONN_COLS}
            """, conn_id, updated_by)
    forget()
    log.info("AI default %s connection is now %s (by %s)", row["capability"], row["name"],
             updated_by)
    return public_connection(row)


def _resolved_for_connection(row, cfg: dict) -> Resolved:
    """This one connection as the resolver would hand it to an adapter — its own key, or the
    legacy key when it has none and speaks the legacy provider (the same hole-filling the
    chain does), so the Test button exercises what a tenant on it would actually run."""
    cap, prov = row["capability"], row["provider"]
    key = open_secret(row["api_key_enc"])
    model = row["model"]
    settings = _jsonb(row["settings"])
    if prov == ai_resolve.LEGACY_PROVIDER[cap]:
        legacy = ai_resolve._legacy(cap, cfg, None, None)
        key = key or legacy["api_key"]
        model = model or legacy["model"]
        settings = {**legacy["settings"], **settings}
    return Resolved(cap, prov, model, key, row["base_url"], settings=settings, byo=False,
                    connection_id=str(row["id"]), source="default")


async def _probe(res: Resolved, *, client_id: str | None = None) -> dict:
    """Call the capability's probe. Imported lazily: llm.probe and voice.probe are being built
    beside this module, and a missing one must read as a failed test, not a crashed route.

    `client_id` is who the probe is metered against. A superadmin testing a deployment
    connection is nobody's spend (None); an owner testing their own key from the portal is
    that workspace's — and a probe that landed unattributed would be the one AI call in the
    product the usage page could not explain."""
    try:
        if res.capability == "llm":
            from . import llm as layer
        else:
            from . import voice as layer
        fn = getattr(layer, "probe", None)
        if fn is None:
            return {"ok": False, "detail": f"The {res.capability} layer has no probe yet."}
        out = (await fn(res, client_id=client_id) if res.capability == "llm" else await fn(res)) or {}
        return {"ok": bool(out.get("ok")), "detail": str(out.get("detail") or "")[:1000]}
    except ImportError:
        return {"ok": False, "detail": f"The {res.capability} layer is not available in this build."}
    except Exception as exc:  # noqa: BLE001 — a probe failure IS the result
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:1000]}


async def test_connection(conn_id: str) -> dict:
    """Probe the provider with this connection's own settings; store and return the result."""
    async with pool().acquire() as conn:
        row = await _raw_or_404(conn, conn_id)
    cfg = await settings_store.get_effective()
    result = await _probe(_resolved_for_connection(row, cfg))
    result = {"ok": result["ok"], "detail": result["detail"], "at": _now_iso()}
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE ai_connections SET last_test = $2::jsonb WHERE id = $1::uuid",
            conn_id, json.dumps(result))
    return result


# ---------------------------------------------------------------------------
# What a tenant is really running on
# ---------------------------------------------------------------------------
async def effective(client_id: str | None, capability: str) -> dict:
    """`{source, provider, model, byo, has_key, connection: {id, name} | None,
    connection_name}` — the resolver's answer, without the key."""
    check_capability(capability)
    res = await ai_resolve.resolve(client_id, capability)
    connection = None
    if res.connection_id:
        async with pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name FROM ai_connections WHERE id = $1::uuid", res.connection_id)
        if row:
            connection = {"id": str(row["id"]), "name": row["name"]}
    return {"source": res.source, "provider": res.provider, "model": res.model,
            "byo": res.byo, "has_key": bool(res.api_key), "connection": connection,
            "connection_name": connection["name"] if connection else None}


async def tenant_exists(client_id: str) -> bool:
    async with pool().acquire() as conn:
        return bool(await conn.fetchval("SELECT 1 FROM clients WHERE id = $1::uuid", client_id))


# ---------------------------------------------------------------------------
# Assignments (superadmin): which connection a workspace runs on, per capability
# ---------------------------------------------------------------------------
async def get_assignments(client_id: str) -> dict:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT capability, connection_id FROM tenant_ai_assignments "
            "WHERE client_id = $1::uuid", client_id)
    assigned = {r["capability"]: str(r["connection_id"]) for r in rows}
    return {cap: {"connection_id": assigned.get(cap),
                  "effective": await effective(client_id, cap)}
            for cap in CAPABILITIES}


async def set_assignments(client_id: str, patch: dict, *, updated_by: str) -> dict:
    """`{llm?: id|null, stt?: id|null, tts?: id|null}` — a key that is absent is untouched,
    null removes the assignment (back to the default), an id assigns it. Refuses an id of the
    wrong capability or an inactive connection: an assignment that would silently fall back
    on the day it is made is a mistake, not a choice."""
    for cap in patch:
        check_capability(cap)
    async with pool().acquire() as conn, conn.transaction():
        for cap, conn_id in patch.items():
            if conn_id is None:
                await conn.execute(
                    "DELETE FROM tenant_ai_assignments WHERE client_id = $1::uuid "
                    "AND capability = $2", client_id, cap)
                continue
            row = await _raw(conn, str(conn_id))
            if not row:
                raise RegistryError(f"Connection not found for {cap}.", code="not_found",
                                    field=cap, status=404)
            if row["capability"] != cap:
                raise RegistryError(
                    f"'{row['name']}' is a {row['capability']} connection; it cannot be "
                    f"assigned as {cap}.", code="connection_mismatch", field=cap)
            if not row["is_active"]:
                raise RegistryError(f"'{row['name']}' is inactive and cannot be assigned.",
                                    code="connection_inactive", field=cap)
            await conn.execute(
                """
                INSERT INTO tenant_ai_assignments (client_id, capability, connection_id,
                                                   updated_at, updated_by)
                VALUES ($1::uuid, $2, $3::uuid, now(), $4)
                ON CONFLICT (client_id, capability) DO UPDATE SET
                    connection_id = EXCLUDED.connection_id, updated_at = now(),
                    updated_by = EXCLUDED.updated_by
                """, client_id, cap, str(conn_id), updated_by)
    forget(client_id)
    return await get_assignments(client_id)


# ---------------------------------------------------------------------------
# Overrides: the workspace's OWN provider settings (bring-your-own)
# ---------------------------------------------------------------------------
_OVR_COLS = ("client_id, capability, provider, model, api_key_enc, settings, enabled, notes, "
             "base_url, updated_at, updated_by")


async def get_override(client_id: str, capability: str):
    """The raw row (sealed key included) or None. API responses use `public_override`."""
    check_capability(capability)
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            f"SELECT {_OVR_COLS} FROM tenant_ai_overrides "
            "WHERE client_id = $1::uuid AND capability = $2", client_id, capability)


def public_override(row) -> dict | None:
    """The tenant-facing view: no key, no base_url (a tenant cannot set one and need not see
    the gateway an operator chose)."""
    if not row:
        return None
    key = open_secret(row["api_key_enc"])
    return {"provider": row["provider"], "model": row["model"],
            "has_key": bool(key), "key_hint": _mask(key),
            "enabled": bool(row["enabled"]), "settings": _jsonb(row["settings"]),
            "notes": row["notes"], "updated_at": _iso(row["updated_at"]),
            "updated_by": row["updated_by"]}


async def save_override(client_id: str, capability: str, *, provider, model=KEEP,
                        api_key=None, clear_key: bool = False, settings=KEEP, enabled=KEEP,
                        notes=KEEP, base_url=KEEP, updated_by: str):
    """Upsert one capability's override and return the raw row.

    `api_key` absent keeps the stored key; `clear_key` removes it. A provider change without
    a new key drops the old one (and any base_url), for the reason `update_connection`
    gives. `base_url` is accepted here because the SUPERADMIN shim passes it; the tenant
    router refuses the field before it ever reaches this function.
    """
    check_capability(capability)
    prov = _provider(capability, provider)
    key = _key(api_key)
    async with pool().acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"SELECT {_OVR_COLS} FROM tenant_ai_overrides "
            "WHERE client_id = $1::uuid AND capability = $2 FOR UPDATE", client_id, capability)
        old_prov = row["provider"] if row else None
        if clear_key:
            key_enc = None
        elif key:
            key_enc = seal_secret(key)
        elif row and prov == old_prov:
            key_enc = row["api_key_enc"]
        else:
            key_enc = None
        new_model = (row["model"] if row else None) if model is KEEP else _model(model)
        if base_url is KEEP:
            new_base = row["base_url"] if row and prov == old_prov else None
            if new_base and not catalog.allows_base_url(capability, prov):
                new_base = None
        else:
            new_base = _base_url(capability, prov, base_url)
        new_settings = (_jsonb(row["settings"]) if row else {}) if settings is KEEP \
            else _settings(settings)
        new_enabled = (bool(row["enabled"]) if row else True) if enabled is KEEP or enabled is None \
            else bool(enabled)
        new_notes = (row["notes"] if row else None) if notes is KEEP \
            else ((notes or "").strip() or None)
        row = await conn.fetchrow(
            f"""
            INSERT INTO tenant_ai_overrides
                (client_id, capability, provider, model, api_key_enc, settings, enabled,
                 notes, base_url, updated_at, updated_by)
            VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, now(), $10)
            ON CONFLICT (client_id, capability) DO UPDATE SET
                provider = EXCLUDED.provider, model = EXCLUDED.model,
                api_key_enc = EXCLUDED.api_key_enc, settings = EXCLUDED.settings,
                enabled = EXCLUDED.enabled, notes = EXCLUDED.notes,
                base_url = EXCLUDED.base_url, updated_at = now(),
                updated_by = EXCLUDED.updated_by
            RETURNING {_OVR_COLS}
            """,
            client_id, capability, prov, new_model, key_enc, json.dumps(new_settings),
            new_enabled, new_notes, new_base, updated_by)
    forget(client_id, capability)
    return row


async def delete_override(client_id: str, capability: str) -> bool:
    check_capability(capability)
    async with pool().acquire() as conn:
        tag = await conn.execute(
            "DELETE FROM tenant_ai_overrides WHERE client_id = $1::uuid AND capability = $2",
            client_id, capability)
    forget(client_id, capability)
    return tag.endswith(" 1")


async def test_override(client_id: str, capability: str) -> dict:
    """Probe what the tenant's OWN row would run on — applied over the chain beneath it, and
    applied even if it is still switched off, so an owner can test before enabling."""
    if not await get_override(client_id, capability):
        raise RegistryError(f"No {capability} override is configured for this workspace.",
                            code="no_override", field="capability", status=404)
    forget(client_id, capability)
    res = await ai_resolve._resolve_with(client_id, capability, api_key=None, model=None,
                                         force_override=True)
    result = await _probe(res, client_id=client_id)
    return {"ok": result["ok"], "detail": result["detail"], "at": _now_iso()}


# ---------------------------------------------------------------------------
# Boot: the legacy deployment keys become the first connections
# ---------------------------------------------------------------------------
async def seed_from_legacy() -> list[str]:
    """When the registry has NO active connections, turn the admin panel's keys into named
    default connections, one per capability that has a key. Idempotent: a second boot finds
    active connections and does nothing, and a seed row an operator deliberately deactivated
    is not re-created. Returns log lines; never raises (a seed failure must not stop boot)."""
    lines: list[str] = []
    try:
        async with pool().acquire() as conn:
            active = await conn.fetchval("SELECT count(*) FROM ai_connections WHERE is_active")
            if active:
                return [f"AI registry: {active} active connection(s); legacy seed skipped"]
            existing = {r["name"] for r in await conn.fetch("SELECT name FROM ai_connections")}
        cfg = await settings_store.get_effective()
        voice = cfg.get("tts_voice_id")
        plan = [
            ("llm", "anthropic", cfg.get("anthropic_api_key"), cfg.get("llm_model"), {}),
            ("stt", "elevenlabs", cfg.get("elevenlabs_api_key"), cfg.get("stt_model"), {}),
            ("tts", "elevenlabs", cfg.get("elevenlabs_api_key"), cfg.get("tts_model"),
             {"voice_id": voice} if voice else {}),
        ]
        async with pool().acquire() as conn, conn.transaction():
            for cap, prov, key, model, st in plan:
                name = SEED_NAMES[cap]
                if not key:
                    lines.append(f"AI registry: no legacy {cap} key configured; nothing to seed")
                    continue
                if name in existing:
                    lines.append(f"AI registry: legacy seed '{name}' exists (inactive); "
                                 "not re-created")
                    continue
                await conn.execute(
                    """
                    INSERT INTO ai_connections (name, capability, provider, model, api_key_enc,
                                                settings, is_active, is_default, updated_by)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, true, true, $7)
                    """, name, cap, prov, (model or "").strip() or None, seal_secret(key),
                    json.dumps(st), SEED_ACTOR)
                lines.append(f"AI registry: seeded default {cap} connection '{name}'"
                             + (f" (model {model})" if model else ""))
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.exception("AI registry legacy seed failed")
        lines.append(f"AI registry: legacy seed FAILED: {exc}")
    forget()
    return lines


async def summary() -> dict:
    """For /health: how many active connections, and the default's name per capability."""
    async with pool().acquire() as conn:
        n = await conn.fetchval("SELECT count(*) FROM ai_connections WHERE is_active")
        rows = await conn.fetch(
            "SELECT capability, name FROM ai_connections WHERE is_default AND is_active")
    names = {r["capability"]: r["name"] for r in rows}
    return {"connections": int(n or 0),
            "defaults": {cap: names.get(cap) for cap in CAPABILITIES}}
