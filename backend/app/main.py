import logging
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .routers import (admin, analyze, auth, calls, kb, kb_admin, partner, scoring,
                     tenants, tts)
from .services import analysis
from .services.migrate import run_startup_migrations

log = logging.getLogger("cq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    for line in await run_startup_migrations():
        log.info("startup migration: %s", line)
    # Fail any analysis job left mid-flight by a previous crash/restart (audio isn't
    # persisted, so they can't be auto-retried — the partner resubmits).
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


app.include_router(calls.router)
app.include_router(analyze.router)
app.include_router(tts.router)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(kb.router)
app.include_router(kb_admin.router)
app.include_router(scoring.router)
app.include_router(tenants.router)

# ---- B2B partner API (versioned) -------------------------------------------
# New partner-facing endpoints (account, transcriptions, async + bulk analysis, jobs,
# scoring config) live under /v1. KB + TTS are re-exposed under /v1 too so partners get
# one coherent, versioned surface; the same routers keep serving the browser UI at root.
app.include_router(partner.router)                 # /v1/account, /v1/analyses, ...
app.include_router(kb.router, prefix="/v1")        # /v1/kb/*
app.include_router(tts.router, prefix="/v1")       # /v1/tts, /v1/voices, /v1/languages


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
async def health():
    try:
        async with db.pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok", "database": "connected"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "database": "unavailable", "detail": str(exc)}


# Serve the static frontend from the API too, so the whole app is reachable on a
# single port (:8000) without needing nginx or an extra firewall rule. Mounted last
# so the API routes above take precedence; html=True falls back to index.html.
_FRONTEND_DIR = Path("/app/frontend")
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
