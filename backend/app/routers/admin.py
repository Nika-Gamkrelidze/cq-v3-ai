"""Admin panel API: login, view/update integration settings, and test connectivity.

The super-admin logs in with username + password (POST /admin/login), which returns
the admin token. That token then authorizes every other admin endpoint via the
X-Admin-Token header.
"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..services import claude, elevenlabs, settings_store

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(x_admin_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginRequest):
    """Exchange super-admin username+password for the admin token."""
    ok_user = secrets.compare_digest(req.username, settings.superadmin_username)
    ok_pass = secrets.compare_digest(req.password, settings.superadmin_password)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"token": settings.admin_token, "username": settings.superadmin_username}


class SettingsPatch(BaseModel):
    anthropic_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    llm_model: str | None = None
    stt_model: str | None = None
    tts_model: str | None = None
    tts_voice_id: str | None = None
    analysis_instructions: str | None = None


@router.get("/settings", dependencies=[Depends(require_admin)])
async def get_settings():
    return await settings_store.get_public()


@router.put("/settings", dependencies=[Depends(require_admin)])
async def put_settings(patch: SettingsPatch):
    await settings_store.update(patch.model_dump(exclude_none=True))
    return await settings_store.get_public()


@router.post("/test", dependencies=[Depends(require_admin)])
async def test_integrations():
    """Check that the configured keys actually work."""
    cfg = await settings_store.get_effective()
    result = {}

    # ElevenLabs: list voices as a cheap authenticated call.
    try:
        voices = await elevenlabs.list_voices(cfg["elevenlabs_api_key"])
        result["elevenlabs"] = {"ok": True, "detail": f"{len(voices)} voices available"}
    except Exception as exc:  # noqa: BLE001
        result["elevenlabs"] = {"ok": False, "detail": str(exc)}

    # Claude: a tiny analysis round-trip.
    try:
        analysis = await claude.analyze(
            "Speaker 1: Hello, this is a connectivity test. Speaker 2: Acknowledged.",
            cfg["anthropic_api_key"], cfg["llm_model"], cfg["analysis_instructions"],
        )
        result["claude"] = {"ok": True, "detail": f"model {cfg['llm_model']} responded",
                            "sample": analysis.get("summary", "")[:120]}
    except Exception as exc:  # noqa: BLE001
        result["claude"] = {"ok": False, "detail": str(exc)}

    return result


# ---- Embeddings provider config -------------------------------------------
class EmbeddingPatch(BaseModel):
    provider: str | None = None       # tei | openai
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    dim: int | None = None


@router.get("/embeddings", dependencies=[Depends(require_admin)])
async def get_embeddings():
    return await settings_store.get_embedding_public()


@router.put("/embeddings", dependencies=[Depends(require_admin)])
async def put_embeddings(patch: EmbeddingPatch):
    await settings_store.set_embedding_config(patch.model_dump(exclude_none=True))
    return await settings_store.get_embedding_public()


@router.post("/embeddings/test", dependencies=[Depends(require_admin)])
async def test_embeddings():
    from ..services import embeddings as emb
    try:
        provider = await emb.get_provider()
        health = await provider.health()
        cfg_dim = provider.dim
        detail = f"model {health.get('model')} returned dim {health.get('dim')}"
        if health.get("dim") and cfg_dim and health["dim"] != cfg_dim:
            return {"ok": False, "detail": f"{detail} but configured dim is {cfg_dim} — "
                                           f"set EMBEDDING_DIM to {health['dim']} and re-embed."}
        return {"ok": True, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


# ---- Anonymous (no-tenant) usage limits -----------------------------------
class AnonPatch(BaseModel):
    enabled: bool | None = None
    max_analyses_per_day: int | None = None
    max_audio_mb: int | None = None
    max_tts_per_day: int | None = None
    features: dict | None = None


@router.get("/anonymous-limits", dependencies=[Depends(require_admin)])
async def get_anon_limits():
    return await settings_store.get_anonymous_config()


@router.put("/anonymous-limits", dependencies=[Depends(require_admin)])
async def put_anon_limits(patch: AnonPatch):
    await settings_store.set_anonymous_config(patch.model_dump(exclude_none=True))
    return await settings_store.get_anonymous_config()
