"""Which provider, model and key a given tenant's call runs on — for one capability.

THIS IS THE CONTRACT the LLM and voice layers are written against. The resolution chain is:

    code defaults
      <- the deployment's DEFAULT connection for the capability      (superadmin, registry)
      <- the connection a superadmin ASSIGNED to this tenant          (dropdown, per tenant)
      <- the tenant's OWN key for the capability (bring-your-own)     (tenant owner, portal)

with each layer overriding only what it sets, and the LEGACY deployment settings (the admin
panel's Integrations key + model) underneath everything — so a deployment with an empty
registry behaves exactly as it did before the registry existed.

`source` says which layer answered, so usage rows can record whose money was spent and a console
can say what a tenant is really running on. It is honest rather than convenient: a tenant row
that sets only a model and no key is `source="byo"` with `byo=False` — they chose the model, but
the spend is still ours.

Two rules a layer follows when it is applied:

  * A field it leaves empty is inherited from below. A connection with no model runs the model
    of the layer under it; a tenant key with no model runs the assigned connection's model.
  * A layer that CHANGES THE PROVIDER drops the inherited model, key, base_url and settings,
    because they belonged to the other provider. An OpenAI connection assigned over an Anthropic
    default must not be sent the Anthropic key or a Claude model id.

The legacy layer is the bottom for its own provider only: when the chain ends on Anthropic (llm)
or ElevenLabs (stt/tts) with a hole — no key, no model, no voice — the admin panel's value fills
it. A chain that ends on another provider with no key gets an empty key, and the adapter reports
that plainly rather than the product quietly spending on the wrong account.

Keys are stored sealed (services/secrets.py) and are opened HERE, at the moment of use, and
nowhere else. Never raises for a lookup failure: a tenant whose configuration cannot be read runs
on the legacy default rather than losing the request, and the failure is logged.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from ..db import pool
from . import settings_store

log = logging.getLogger("cq")

CAPABILITIES = ("llm", "stt", "tts")

# The provider the admin panel's Integrations tab configures for each capability — the one the
# legacy layer can fill holes for.
LEGACY_PROVIDER = {"llm": "anthropic", "stt": "elevenlabs", "tts": "elevenlabs"}


@dataclass(frozen=True)
class Resolved:
    capability: str                 # llm | stt | tts
    provider: str                   # anthropic | openai | gemini | elevenlabs | ...
    model: str | None
    api_key: str                    # decrypted, ready to send
    base_url: str | None
    settings: dict = field(default_factory=dict)   # provider/capability specifics, e.g. tts voice_id
    byo: bool = False               # the spend is on the TENANT'S key, not ours
    connection_id: str | None = None
    source: str = "legacy"          # byo | assigned | default | legacy


# ---------------------------------------------------------------------------
# Sealed secrets. services/secrets.py is built beside this module; until it lands, and on a
# deployment with no SECRETS_KEY, a stored value is its own plaintext. Everything that stores
# or opens a provider key goes through these two names so the fallback lives in one place.
# ---------------------------------------------------------------------------
class _PlainVault:
    """The no-encryption behaviour of services/secrets.py, used only when that module is
    absent. Same five names, so callers cannot tell the two apart."""

    @staticmethod
    def seal(plaintext: str) -> str:
        return plaintext

    @staticmethod
    def open(stored: str | None) -> str:       # noqa: A003 — mirrors the contract's name
        return stored or ""

    @staticmethod
    def is_sealed(value) -> bool:
        return False

    @staticmethod
    def status() -> dict:
        return {"mode": "plaintext"}

    @staticmethod
    async def migrate_plaintext() -> int:
        return 0


def vault():
    """The secrets module, or the plaintext stand-in when it has not landed."""
    try:
        from . import secrets as sealed     # noqa: WPS433 — deliberately late (built in parallel)
    except ImportError:
        return _PlainVault
    return sealed


def open_secret(stored: str | None) -> str:
    """A stored key, ready to send. '' for None, and '' (logged) for a value that cannot be
    opened — a wrong SECRETS_KEY must surface as "no key" at the adapter, not as a 500."""
    if not stored:
        return ""
    try:
        return vault().open(stored) or ""
    except Exception:  # noqa: BLE001 — see docstring
        log.exception("a stored provider key could not be opened (SECRETS_KEY changed?)")
        return ""


# ---------------------------------------------------------------------------
# The three registry layers, cached briefly. This is read on the hot path of every AI call,
# and a change to any of them is an operator or owner action that may take seconds to land.
# ---------------------------------------------------------------------------
_CACHE: dict[tuple[str | None, str], tuple[float, dict]] = {}
_TTL_S = 30.0

_SQL_DEFAULT = """
    SELECT id, name, provider, model, base_url, api_key_enc, settings
    FROM ai_connections
    WHERE capability = $1 AND is_default AND is_active
"""
_SQL_ASSIGNED = """
    SELECT c.id, c.name, c.provider, c.model, c.base_url, c.api_key_enc, c.settings, c.is_active
    FROM tenant_ai_assignments a
    JOIN ai_connections c ON c.id = a.connection_id
    WHERE a.client_id = $1::uuid AND a.capability = $2
"""
_SQL_OVERRIDE = """
    SELECT provider, model, base_url, api_key_enc, settings, enabled
    FROM tenant_ai_overrides
    WHERE client_id = $1::uuid AND capability = $2
"""


def _jsonb(value) -> dict:
    # asyncpg hands jsonb back as a str unless a codec is registered.
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _connection_layer(row) -> dict:
    return {"connection_id": str(row["id"]), "name": row["name"], "provider": row["provider"],
            "model": row["model"], "base_url": row["base_url"],
            "api_key_enc": row["api_key_enc"], "settings": _jsonb(row["settings"])}


def _override_layer(row) -> dict:
    return {"connection_id": None, "name": None, "provider": row["provider"],
            "model": row["model"], "base_url": row["base_url"],
            "api_key_enc": row["api_key_enc"], "settings": _jsonb(row["settings"]),
            "enabled": bool(row["enabled"])}


async def load_layers(client_id: str | None, capability: str) -> dict:
    """`{default, assigned, override}` for one tenant and capability, each a layer dict or
    None. Keys stay SEALED in the cache; they are opened only when a chain is resolved.
    An assignment to an inactive connection is reported as None — it falls back to the
    default — and an inactive default is not a default at all."""
    key = (client_id or None, capability)
    hit = _CACHE.get(key)
    if hit and (time.monotonic() - hit[0]) < _TTL_S:
        return hit[1]
    layers: dict = {"default": None, "assigned": None, "override": None}
    async with pool().acquire() as conn:
        row = await conn.fetchrow(_SQL_DEFAULT, capability)
        if row:
            layers["default"] = _connection_layer(row)
        if client_id:
            row = await conn.fetchrow(_SQL_ASSIGNED, client_id, capability)
            if row and row["is_active"]:
                layers["assigned"] = _connection_layer(row)
            row = await conn.fetchrow(_SQL_OVERRIDE, client_id, capability)
            if row:
                layers["override"] = _override_layer(row)
    _CACHE[key] = (time.monotonic(), layers)
    return layers


def forget(client_id: str | None = None, capability: str | None = None) -> None:
    """Drop the layer cache — after any registry write, and for tests.

    With no arguments everything goes (a default connection changed: every tenant is
    affected). With a client_id only that tenant's entries go; the deployment-wide
    `(None, capability)` entries go with them because a tenant write never touches those,
    but a caller that just changed the default will pass nothing and clear all."""
    if client_id is None and capability is None:
        _CACHE.clear()
        return
    for k in list(_CACHE):
        if (client_id is None or k[0] == client_id) and (capability is None or k[1] == capability):
            _CACHE.pop(k, None)


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------
def _legacy(capability: str, cfg: dict, api_key: str | None, model: str | None) -> dict:
    """The bottom layer: what the admin panel says (or what the caller already read from it)."""
    if capability == "llm":
        return {"model": model or cfg.get("llm_model") or None,
                "api_key": api_key or cfg.get("anthropic_api_key") or "", "settings": {}}
    key = api_key or cfg.get("elevenlabs_api_key") or ""
    if capability == "stt":
        return {"model": model or cfg.get("stt_model") or None, "api_key": key, "settings": {}}
    voice = cfg.get("tts_voice_id")
    return {"model": model or cfg.get("tts_model") or None, "api_key": key,
            "settings": {"voice_id": voice} if voice else {}}


def _apply(state: dict, layer: dict, source: str) -> None:
    """Lay one layer over `state`, in place. See the module docstring for the two rules."""
    provider = layer.get("provider")
    if provider and provider != state["provider"]:
        state.update(provider=provider, model=None, api_key="", base_url=None, settings={})
    if layer.get("model"):
        state["model"] = layer["model"]
    key = open_secret(layer.get("api_key_enc"))
    if key:
        state["api_key"] = key
    if layer.get("base_url"):
        state["base_url"] = layer["base_url"]
    extra = {k: v for k, v in (layer.get("settings") or {}).items() if v not in (None, "")}
    state["settings"] = {**state["settings"], **extra}
    state["source"] = source
    if layer.get("connection_id"):
        state["connection_id"] = layer["connection_id"]
        state["byo"] = False
    else:
        # The tenant's own row. With a key the spend is theirs and no connection paid for it;
        # without one it rides on whatever connection (or legacy key) sits underneath.
        state["byo"] = bool(key)
        if key:
            state["connection_id"] = None


async def _resolve_with(client_id: str | None, capability: str, *,
                        api_key: str | None, model: str | None,
                        force_override: bool = False) -> Resolved:
    """`resolve()` with one extra knob: `force_override=True` applies the tenant's own row
    even when it is disabled — the portal's Test button probes the key an owner just typed,
    whether or not they have switched it on yet."""
    if capability not in CAPABILITIES:
        raise ValueError(f"unknown AI capability: {capability!r}")
    try:
        cfg = await settings_store.get_effective()
    except Exception:  # noqa: BLE001 — the legacy layer is a fallback; its absence is a hole, not a crash
        # The registry layers above still resolve. Only a chain that ends on the legacy
        # provider with nothing set above it comes out keyless — and the adapter says so in
        # plain words, which beats the alternative: every voice call in the product raising
        # from inside its resolver because one settings read failed.
        log.exception("legacy AI settings could not be read for %s/%s; resolving without them",
                      client_id, capability)
        cfg = {}
    legacy = _legacy(capability, cfg, api_key, model)
    legacy_provider = LEGACY_PROVIDER[capability]
    state: dict = {"provider": legacy_provider, "model": None, "api_key": "", "base_url": None,
                   "settings": {}, "connection_id": None, "source": "legacy", "byo": False}
    try:
        layers = await load_layers(client_id, capability)
    except Exception:  # noqa: BLE001 — a registry lookup must never break an AI call
        log.exception("AI registry lookup failed for %s/%s; using the legacy settings",
                      client_id, capability)
        layers = {"default": None, "assigned": None, "override": None}

    if layers.get("default"):
        _apply(state, layers["default"], "default")
    if layers.get("assigned"):
        _apply(state, layers["assigned"], "assigned")
    override = layers.get("override")
    if override and (override.get("enabled") or force_override):
        _apply(state, override, "byo")

    if state["provider"] == legacy_provider:
        # Holes at the bottom of the chain are the admin panel's to fill — for its own
        # provider only. A tenant key (byo) is never displaced: it is non-empty by now.
        state["model"] = state["model"] or legacy["model"]
        state["api_key"] = state["api_key"] or legacy["api_key"]
        state["settings"] = {**legacy["settings"], **state["settings"]}

    return Resolved(capability, state["provider"], state["model"], state["api_key"],
                    state["base_url"], settings=state["settings"], byo=state["byo"],
                    connection_id=state["connection_id"], source=state["source"])


async def resolve(client_id: str | None, capability: str, *,
                  api_key: str | None = None, model: str | None = None) -> Resolved:
    """The provider, model and key this call should use.

    `api_key` / `model` are what the caller already read from the legacy settings, if it did;
    they are the bottom of the chain and are only used when nothing above sets a value.
    Never raises for a lookup failure: a tenant whose configuration cannot be read runs on the
    default rather than losing the request (the same rule `ai_config.overlay` already keeps).
    """
    return await _resolve_with(client_id, capability, api_key=api_key, model=model)
