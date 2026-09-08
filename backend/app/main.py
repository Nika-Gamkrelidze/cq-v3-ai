import logging
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .routers import (admin, analyze, auth, calls, chat, chat_config, convert, curation, kb,
                     kb_admin, partner, recordings, scoring, sentiment, tenants,
                     transcription as transcription_router, tts)
from .services import analysis
from .services import auth as auth_service
from .services.transcription import TranscriptionSettingsError
from .services.migrate import run_startup_migrations

log = logging.getLogger("cq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    for line in await run_startup_migrations():
        log.info("startup migration: %s", line)
    # Fail any analysis job left mid-flight by a previous crash/restart. The audio IS stored
    # now (every principal, under the Storage retention), but nothing re-drives a half-run
    # pipeline from a stored file — the caller resubmits. Workbench rows parked in `ready`
    # are a resting state, not mid-flight, and the sweep leaves them alone.
    swept = await analysis.sweep_stuck_jobs()
    if swept:
        log.info("startup: failed %s stuck analysis job(s)", swept)
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(
    title="CQ v3 AI — Partner API", version="1.0.0", lifespan=lifespan,
    description=("Multi-tenant audio analysis. Partners authenticate with the "
                "**`X-API-Key`** header (per-tenant key). See the `/v1` endpoints."),
)

# Allow the browser UI to call the API directly (e.g. over the LAN on :8000).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(asyncpg.DataError)
async def _data_error_handler(request: Request, exc: asyncpg.DataError):
    # A DataError always means the client sent a malformed query value (e.g. a
    # non-UUID id in the path/body). Return 400 instead of a 500.
    return JSONResponse(status_code=400, content={"detail": "Invalid identifier or value"})


@app.exception_handler(TranscriptionSettingsError)
async def _transcription_settings_handler(request: Request, exc: TranscriptionSettingsError):
    """Transcription settings are validated in exactly ONE place (services/transcription.py),
    and they are validated on four different surfaces: the admin default, the workspace
    override, and the per-file `transcription` field on every upload route. One handler is what
    keeps those four answering identically — FastAPI's own shape plus the machine `code` and
    the offending `field`, so a UI can point at the input the operator has to fix."""
    return JSONResponse(status_code=400, content={
        "detail": str(exc), "code": "invalid_transcription_setting",
        "field": getattr(exc, "field", "") or None})


app.include_router(calls.router)
app.include_router(analyze.router)
app.include_router(tts.router)
# Asterisk audio converter: open to signed-out visitors within the anonymous quota, so it
# sits on the root surface beside /analyze and /tts rather than behind /v1.
app.include_router(convert.router)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(kb.router)
app.include_router(kb_admin.router)
app.include_router(scoring.router)
app.include_router(sentiment.router)
app.include_router(tenants.router)
# The portal's own bot settings (GET/PUT /chat/config), the tenant twin of
# /admin/chat/{tenant_id}/config. Root only: the integration surface has its own read-only
# /v1/chat/config in chat.router and must not gain a write path through a prefix.
app.include_router(chat_config.router)
# Transcription settings: /admin/transcription/defaults (superadmin) and /transcription/config
# (the workspace). Root only — the per-file layer rides on the upload routes themselves, so the
# partner surface needs no prefixed twin of these.
app.include_router(transcription_router.router)
# Call Workbench: /recordings + /summaries. Root only, never under /v1 — it admits registered
# users and anonymous visitors, neither of which belongs on the partner surface.
app.include_router(recordings.router)

# ---- B2B partner API (versioned) -------------------------------------------
# New partner-facing endpoints (account, transcriptions, async + bulk analysis, jobs,
# scoring config) live under /v1. KB + TTS are re-exposed under /v1 too so partners get
# one coherent, versioned surface; the same routers keep serving the browser UI at root.
app.include_router(partner.router)                 # /v1/account, /v1/analyses, ...
app.include_router(kb.router, prefix="/v1")        # /v1/kb/*
app.include_router(tts.router, prefix="/v1")       # /v1/tts, /v1/voices, /v1/languages

# ---- Conversational AI (the chat site's server-to-server surface) -----------
# Everything behind nginx's `/api/v1/chat/` location, including the P0 transport probe that used
# to be declared inline here — it moved into the router unchanged and still answers at
# `/v1/chat/health`, which the post-deploy smoke and the proxy test both depend on.
app.include_router(chat.router)                    # /v1/chat/turns, /v1/chat/stream, ...

# ---- KB curation review queue (ADR endpoint 11) ----------------------------
# Declares BOTH its surfaces internally (tenant `/v1/curation/*` + the superadmin mirror
# `/admin/curation/{tenant_id}/*`), like scoring.router, so it is included exactly once and
# never behind the `/v1` prefix — an admin-gated path must not appear on the partner surface.
app.include_router(curation.router)


def _custom_openapi():
    from fastapi.openapi.utils import get_openapi
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version,
                         description=app.description, routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["ApiKeyAuth"] = {
        "type": "apiKey", "in": "header", "name": "X-API-Key",
        "description": "Per-tenant API key (server-to-server). A tenant Bearer token also works.",
    }
    # Mark the partner (/v1) surface as requiring the key, so Swagger shows the lock.
    for path, methods in schema.get("paths", {}).items():
        for op in methods.values():
            if isinstance(op, dict) and path.startswith("/v1"):
                op.setdefault("security", [{"ApiKeyAuth": []}])
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


@app.get("/health")
async def health(request: Request):
    # `client_addressing` is additive and answers a question that otherwise needs a shell on the
    # box: does this container see real peer addresses, or is every visitor arriving as the
    # deployment's own NAT gateway? The latter silently pooled every anonymous visitor into one
    # daily allowance for months. It reports what THIS request looked like, so one
    # unauthenticated `curl /api/health` from anywhere confirms the network path — before and
    # after the host-side fix — with no SSH and no VPN.
    addressing = "ok" if auth_service.can_identify_visitor(auth_service.client_ip(request)) \
        else "nat-masked"
    try:
        async with db.pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok", "database": "connected", "client_addressing": addressing}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "database": "unavailable", "detail": str(exc),
                "client_addressing": addressing}


# Serve the static frontend from the API too, so the whole app is reachable on a
# single port (:8000) without needing nginx or an extra firewall rule. Mounted last
# so the API routes above take precedence; html=True falls back to index.html.
_FRONTEND_DIR = Path("/app/frontend")
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
