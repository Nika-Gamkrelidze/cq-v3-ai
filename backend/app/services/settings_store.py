"""Runtime integration settings, editable from the admin panel.

Effective config = DB overrides (app_settings 'integrations' row) merged on top of
the .env defaults in `settings`. Secrets never leave the backend except masked.
"""
import json

from ..config import settings
from ..db import pool

SETTINGS_KEY = "integrations"

# Non-secret fields and their env-backed defaults.
DEFAULTS = {
    "llm_model": settings.llm_model,
    "stt_model": settings.stt_model,
    "tts_model": settings.tts_model,
    "tts_voice_id": settings.tts_voice_id,
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
    ov.update({k: v for k, v in patch.items() if v is not None})
    await _save_key(ANON_KEY, ov)
