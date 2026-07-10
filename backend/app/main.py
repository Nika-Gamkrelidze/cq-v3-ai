import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import db
from .routers import admin, analyze, auth, calls, kb, kb_admin, scoring, tenants, tts
from .services.migrate import run_startup_migrations

log = logging.getLogger("cq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    for line in await run_startup_migrations():
        log.info("startup migration: %s", line)
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(title="CQ v3 AI API", version="0.2.0", lifespan=lifespan)

# Allow the browser UI to call the API directly (e.g. over the LAN on :8000).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(calls.router)
app.include_router(analyze.router)
app.include_router(tts.router)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(kb.router)
app.include_router(kb_admin.router)
app.include_router(scoring.router)
app.include_router(tenants.router)


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
