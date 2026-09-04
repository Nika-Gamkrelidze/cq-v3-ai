"""Runtime integration settings, editable from the admin panel.

Effective config = DB overrides (app_settings 'integrations' row) merged on top of
the .env defaults in `settings`. Secrets never leave the backend except masked.
"""
import json
import logging
import time

from ..config import settings
from ..db import pool

log = logging.getLogger("cq")

SETTINGS_KEY = "integrations"

# Non-secret fields and their env-backed defaults.
DEFAULTS = {
    "llm_model": settings.llm_model,
    "stt_model": settings.stt_model,
    "tts_model": settings.tts_model,
    "tts_voice_id": settings.tts_voice_id,
    "sentiment_url": settings.sentiment_url,
    "analysis_instructions": (
        "You are a call-quality and conversation analyst. Analyse the transcript of "
        "an audio recording (calls may be in Georgian, Russian, or English). Identify "
        "the primary language, summarise what happened, judge overall sentiment, and "
        "extract topics, key points, and any action items or follow-ups. Be concise "
        "and base every point strictly on the transcript."
    ),
}

SECRET_FIELDS = ("anthropic_api_key", "elevenlabs_api_key")


async def _load_key(key: str) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
    if not row:
        return {}
    # asyncpg returns jsonb as a str unless a codec is registered.
    return json.loads(row) if isinstance(row, str) else dict(row)


async def _save_key(key: str, value: dict) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ($1, $2::jsonb, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            key, json.dumps(value),
        )


def _merge_patch(stored: dict, patch: dict) -> dict:
    """Apply `patch` to a stored settings blob in place, one nested level deep for dicts.

    Top-level keys REPLACE (a None means "not sent", so it is skipped), but a nested dict —
    in practice `features` — merges key by key. A blanket replace turns a form that sends only
    the switch it changed into a switch that silently re-enables everything it left out: the
    reader fills every missing feature with its default True, so `PUT {"features":
    {"semantic": false}}` followed by `PUT {"features": {"score": false}}` would leave semantic
    on again. That is not a hypothetical for the registered tier, whose six switches gate real
    analysers through `limits.require_feature`.
    """
    for k, v in patch.items():
        if v is None:
            continue
        if isinstance(v, dict) and isinstance(stored.get(k), dict):
            stored[k] = {**stored[k], **v}
        else:
            stored[k] = v
    return stored


async def get_blob(key: str) -> dict:
    """The raw stored object under `key` ({} when never saved) — for features whose blob
    has no defaults to merge here (e.g. the default rubric), so they need not reach into
    the private helpers."""
    return await _load_key(key)


async def set_blob(key: str, value: dict) -> None:
    """Replace the object under `key`. Objects only: the admin panel and every reader here
    assume a JSON object, and a bare array would silently break `dict(row)` on read."""
    if not isinstance(key, str) or not key:
        raise ValueError("app_settings key must be a non-empty string")
    if not isinstance(value, dict):
        raise TypeError("app_settings values must be JSON objects")
    await _save_key(key, value)


async def _load_overrides() -> dict:
    return await _load_key(SETTINGS_KEY)


async def get_effective() -> dict:
    """Full effective config (INCLUDING secrets) for internal use by the pipeline."""
    overrides = await _load_overrides()
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in overrides.items() if v not in (None, "")})
    # Secrets: DB override wins, else fall back to env.
    cfg["anthropic_api_key"] = overrides.get("anthropic_api_key") or settings.anthropic_api_key
    cfg["elevenlabs_api_key"] = overrides.get("elevenlabs_api_key") or settings.elevenlabs_api_key
    return cfg


def _mask(value: str) -> str:
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 4 else "…"


async def get_public() -> dict:
    """Config safe to return to the admin UI — secrets replaced with set-flag + hint."""
    cfg = await get_effective()
    public = {k: cfg.get(k, "") for k in DEFAULTS}
    for field in SECRET_FIELDS:
        val = cfg.get(field) or ""
        public[f"{field}_set"] = bool(val)
        public[f"{field}_hint"] = _mask(val)
    return public


async def update(patch: dict) -> None:
    """Merge a patch of settings into the stored overrides.

    Empty-string secret values are ignored so the admin can save non-secret changes
    without wiping keys. Send the literal string "__clear__" to unset a secret.
    """
    overrides = await _load_overrides()
    for key, value in patch.items():
        if value is None:
            continue
        if key in SECRET_FIELDS:
            if value == "__clear__":
                overrides.pop(key, None)
            elif value != "":
                overrides[key] = value
        else:
            overrides[key] = value
    await _save_key(SETTINGS_KEY, overrides)


# ---------------------------------------------------------------------------
# Embeddings config (app_settings 'embeddings'), provider-swappable.
# ---------------------------------------------------------------------------
EMBEDDINGS_KEY = "embeddings"
EMBEDDING_SECRETS = ("api_key",)


async def get_embedding_config() -> dict:
    """Effective embeddings config (incl. secret) merged over env defaults."""
    ov = await _load_key(EMBEDDINGS_KEY)
    cfg = {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "base_url": settings.embedding_base_url,
        "dim": settings.embedding_dim,
        "api_key": settings.embedding_api_key,
    }
    cfg.update({k: v for k, v in ov.items() if v not in (None, "")})
    cfg["dim"] = int(cfg.get("dim") or settings.embedding_dim)
    return cfg


async def get_embedding_public() -> dict:
    cfg = await get_embedding_config()
    pub = {k: cfg.get(k) for k in ("provider", "model", "base_url", "dim")}
    pub["api_key_set"] = bool(cfg.get("api_key"))
    pub["api_key_hint"] = _mask(cfg.get("api_key") or "")
    return pub


async def set_embedding_config(patch: dict) -> None:
    ov = await _load_key(EMBEDDINGS_KEY)
    for key, value in patch.items():
        if value is None:
            continue
        if key in EMBEDDING_SECRETS:
            if value == "__clear__":
                ov.pop(key, None)
            elif value != "":
                ov[key] = value
        else:
            ov[key] = value
    await _save_key(EMBEDDINGS_KEY, ov)
    # The embeddings package caches the built provider for a minute; without this
    # an admin's provider/model/dim change would look like it did nothing for up
    # to 60s. Imported here, not at module scope: embeddings imports settings_store.
    from .embeddings import invalidate_provider_cache

    invalidate_provider_cache()


# ---------------------------------------------------------------------------
# Anonymous (no-tenant) usage limits (app_settings 'anonymous').
# ---------------------------------------------------------------------------
ANON_KEY = "anonymous"
ANON_DEFAULTS = {
    "enabled": True,
    "max_analyses_per_day": 3,
    "max_audio_mb": 10,
    "max_tts_per_day": 10,
    "features": {"analyze": True, "tts": True, "kb": False},
    # Days to keep an unregistered visitor's IP, audio and text before the worker purges it.
    # 0 disables the deadline (keep indefinitely) — a deliberate choice an operator has to
    # make, never the default, because this is personal data with no consent attached.
    "retention_days": 30,
}


async def get_anonymous_config() -> dict:
    ov = await _load_key(ANON_KEY)
    cfg = dict(ANON_DEFAULTS)
    cfg.update(ov or {})
    # ensure features dict is complete
    feats = dict(ANON_DEFAULTS["features"])
    feats.update(cfg.get("features") or {})
    cfg["features"] = feats
    return cfg


async def set_anonymous_config(patch: dict) -> None:
    ov = await _load_key(ANON_KEY)
    _merge_patch(ov, patch)
    await _save_key(ANON_KEY, ov)


# ---------------------------------------------------------------------------
# Storage retention (app_settings 'storage') — ONE number of days for every stored
# recording and TTS clip, whoever submitted it.
#
# Retention used to be a field of the anonymous blob because anonymous audio was the only
# audio kept. Tenant and registered-user audio is stored now too (History replays a call with
# its highlights), and a deadline that differs by who uploaded would be two purge rules to
# explain and audit. So the number moved to its own blob — and until a superadmin saves that
# blob once, it READS the anonymous blob's value, so an existing deployment keeps the number
# its operator already chose rather than snapping back to the default.
# ---------------------------------------------------------------------------
STORAGE_KEY = "storage"
STORAGE_DEFAULTS = {"retention_days": 30}   # 0 = keep forever (see ANON_DEFAULTS)


def _retention_days(value) -> int:
    """A non-negative whole number of days. Raises ValueError for anything else, so a bad
    admin input is refused at the door instead of becoming a deadline the purge misreads."""
    if isinstance(value, bool):
        raise ValueError("retention_days must be a number of days")
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise ValueError("retention_days must be a number of days") from None
    if days < 0:
        raise ValueError("retention_days cannot be negative")
    return days


async def get_storage_config() -> dict:
    """`{retention_days: int}` — the stored blob, else the anonymous blob's number, else 30.
    Never raises: a corrupt stored value falls back the same way."""
    ov = await _load_key(STORAGE_KEY)
    if "retention_days" not in ov:
        ov = {"retention_days": (await get_anonymous_config()).get("retention_days")}
    cfg = dict(STORAGE_DEFAULTS)
    try:
        cfg["retention_days"] = _retention_days(ov["retention_days"])
    except ValueError:
        log.warning("storage.retention_days is not a valid number (%r); using default",
                    ov.get("retention_days"))
    return cfg


async def set_storage_config(patch: dict) -> dict:
    """Merge `patch` into the storage blob and return the effective config.
    `retention_days` is validated (ValueError on garbage); None values are ignored."""
    ov = await _load_key(STORAGE_KEY)
    for key, value in patch.items():
        if value is None:
            continue
        ov[key] = _retention_days(value) if key == "retention_days" else value
    await _save_key(STORAGE_KEY, ov)
    return await get_storage_config()


# ---------------------------------------------------------------------------
# Registered-user tier (app_settings 'registered') — the daily limits and feature switches
# for self-service email+password accounts, counted PER USER (services/limits.py).
#
# Same shape and merge rules as the anonymous pair above so the admin panel can reuse its
# form. `enabled` means "sign-ups are open" — NOT "existing users may log in": an existing
# account is switched off through app_users.is_active, one user at a time.
# ---------------------------------------------------------------------------
REGISTERED_KEY = "registered"
REGISTERED_DEFAULTS = {
    "enabled": True,
    "max_analyses_per_day": 20,
    "max_audio_mb": 50,
    "max_tts_per_day": 50,
    "max_conversions_per_day": 100,
    "features": {"analyze": True, "tts": True, "convert": True,
                 "summarise": True, "score": True, "semantic": True},
}


async def get_registered_config() -> dict:
    ov = await _load_key(REGISTERED_KEY)
    cfg = dict(REGISTERED_DEFAULTS)
    cfg.update(ov or {})
    # ensure features dict is complete
    feats = dict(REGISTERED_DEFAULTS["features"])
    feats.update(cfg.get("features") or {})
    cfg["features"] = feats
    return cfg


async def set_registered_config(patch: dict) -> dict:
    """Merge `patch` into the registered blob and return the effective config."""
    ov = await _load_key(REGISTERED_KEY)
    _merge_patch(ov, patch)
    await _save_key(REGISTERED_KEY, ov)
    return await get_registered_config()


# ---------------------------------------------------------------------------
# Public-app sentiment configuration (app_settings 'public_sentiment').
#
# The public site's standalone Sentiment tab has no tenant to own its config, so — like the
# anonymous limits above — it is a single global row, superadmin-only. The per-tenant
# equivalent (services/sentiment_config.py) is a real table keyed by client_id; there is
# nothing to key this one by.
# ---------------------------------------------------------------------------
PUBLIC_SENTIMENT_KEY = "public_sentiment"
PUBLIC_SENTIMENT_DEFAULTS = {"enabled": True, "guidance": ""}


async def get_public_sentiment_config() -> dict:
    ov = await _load_key(PUBLIC_SENTIMENT_KEY)
    cfg = dict(PUBLIC_SENTIMENT_DEFAULTS)
    cfg.update(ov or {})
    cfg["enabled"] = bool(cfg.get("enabled", True))
    cfg["guidance"] = str(cfg.get("guidance") or "")
    return cfg


async def set_public_sentiment_config(patch: dict) -> None:
    ov = await _load_key(PUBLIC_SENTIMENT_KEY)
    ov.update({k: v for k, v in patch.items() if v is not None})
    await _save_key(PUBLIC_SENTIMENT_KEY, ov)


# ---------------------------------------------------------------------------
# Customer-visible TTS voices (app_settings 'voices').
# Kept in its OWN key, not the 'integrations' blob: get_public() coerces missing
# DEFAULTS to '' and the admin panel's settings save rewrites every integration
# field, which would clobber a list. Value is always an object, never a bare array.
# ---------------------------------------------------------------------------
VOICES_KEY = "voices"
VOICE_DEFAULTS = {
    "mode": "all",        # all | allowlist  — 'all' means show every voice (fail open)
    "voice_ids": [],      # ordered: the admin's tick order is the dropdown order
}


async def get_voice_config() -> dict:
    """Never raises and never returns a state that hides every voice by accident:
    an absent/!invalid config falls back to mode='all' (show everything)."""
    ov = await _load_key(VOICES_KEY)
    cfg = dict(VOICE_DEFAULTS)
    cfg.update(ov or {})
    cfg["mode"] = cfg.get("mode") if cfg.get("mode") in ("all", "allowlist") else "all"
    cfg["voice_ids"] = [str(i) for i in (cfg.get("voice_ids") or []) if i]
    return cfg


async def set_voice_config(patch: dict) -> None:
    ov = await _load_key(VOICES_KEY)
    ov.update({k: v for k, v in patch.items() if v is not None})
    await _save_key(VOICES_KEY, ov)


# ---------------------------------------------------------------------------
# Autopilot kill switch (app_settings 'autopilot_kill').
#
# The public bot is the one surface that talks to end customers with no human in the loop,
# so "stop it NOW" has to be a superadmin action that takes seconds and touches nothing else.
# Two levers: `global_disabled` (everything, everywhere) and `disabled_clients` (one tenant
# that is misbehaving, while the rest keep working).
#
# Why this is not just `chat_configs.autopilot_enabled`: that column is the TENANT's setting,
# it is versioned, and turning it off writes a new config version into the tenant's own audit
# trail as if they had changed their mind. This is the OPERATOR's brake — orthogonal owner,
# orthogonal storage.
#
# Why it is not an env var or a deploy: a redeploy is the slowest possible way to stop a bot
# (image build + restart), and it is not side-effect free — every deploy blanket-errors
# in-flight audio jobs via `sweep_stuck_jobs()`. Flipping a row must not cost anyone their
# analysis.
#
# The 5-second TTL is the whole design. Reading `app_settings` on every single turn would put
# a DB round-trip in front of a latency-critical path for a value that changes maybe twice a
# year; caching it for minutes would mean a superadmin hits "stop" and then watches the bot
# keep answering. 5s is short enough that "within seconds" is true and long enough that a
# busy tenant is not hammering the row.
# ---------------------------------------------------------------------------
AUTOPILOT_KILL_KEY = "autopilot_kill"
AUTOPILOT_KILL_DEFAULTS = {"global_disabled": False, "disabled_clients": []}
AUTOPILOT_KILL_TTL_S = 5.0

# (fetched_at, value). Module-level, therefore per-process — correct here because the API
# runs a single uvicorn worker (see services/llm.py for the same assumption).
_kill_cache: tuple[float, dict] | None = None


def _normalize_kill(raw: dict) -> dict:
    cfg = dict(AUTOPILOT_KILL_DEFAULTS)
    cfg.update(raw or {})
    cfg["global_disabled"] = bool(cfg.get("global_disabled"))
    cfg["disabled_clients"] = [str(c) for c in (cfg.get("disabled_clients") or []) if c]
    return cfg


async def get_autopilot_kill_switch(*, force: bool = False) -> dict:
    """`{global_disabled: bool, disabled_clients: [uuid]}`, cached for 5 seconds.

    Never raises. On a DB failure it returns the last value it saw, or the permissive default
    — deliberately fail-OPEN, because the DB being unreachable already means retrieval is
    down, which means `chat.gate()` refuses every turn anyway. Failing closed here would add
    nothing but a second reason for the same outcome; failing open keeps this switch honest
    about being an operator brake rather than a dependency.
    """
    global _kill_cache
    now = time.monotonic()
    if not force and _kill_cache and (now - _kill_cache[0]) < AUTOPILOT_KILL_TTL_S:
        return _kill_cache[1]
    try:
        cfg = _normalize_kill(await _load_key(AUTOPILOT_KILL_KEY))
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning("autopilot kill switch read failed: %s", exc)
        return _kill_cache[1] if _kill_cache else dict(AUTOPILOT_KILL_DEFAULTS)
    _kill_cache = (now, cfg)
    return cfg


async def set_autopilot_kill_switch(patch: dict) -> dict:
    """Flip the brake and drop the cache, so the superadmin's own next read is the truth."""
    global _kill_cache
    ov = _normalize_kill(await _load_key(AUTOPILOT_KILL_KEY))
    if patch.get("global_disabled") is not None:
        ov["global_disabled"] = bool(patch["global_disabled"])
    if patch.get("disabled_clients") is not None:
        ov["disabled_clients"] = [str(c) for c in (patch["disabled_clients"] or []) if c]
    await _save_key(AUTOPILOT_KILL_KEY, ov)
    _kill_cache = None
    log.warning("autopilot kill switch set: global_disabled=%s disabled_clients=%d",
                ov["global_disabled"], len(ov["disabled_clients"]))
    return ov


def autopilot_killed(kill: dict, client_id: str | None) -> bool:
    """Pure predicate over an already-fetched switch, so the engine can log WHY without a
    second read and a test can exercise it with a dict literal."""
    kill = kill or {}
    if kill.get("global_disabled"):
        return True
    return str(client_id or "") in (kill.get("disabled_clients") or [])
