import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .routers import (admin, ai_admin, ai_tenant, analyze, auth, calls, chat, chat_config,
                     convert, curation, kb, kb_admin, partner, recordings, scoring, sentiment,
                     tenants, transcription as transcription_router, tts)
from .config import settings
from .services import ai_registry, ai_resolve, analysis, settings_store
from .services import auth as auth_service
# `sentiment` is already bound to the ROUTER above; the service needs its own name.
from .services import sentiment as sentiment_service
from .services.ai_registry import RegistryError
from .services.transcription import TranscriptionSettingsError
from .services.migrate import run_startup_migrations

log = logging.getLogger("cq")

# ---- Server health: per-request load metering + the host sampler -----------------------------
# The accumulator the middleware records into. None until the lifespan has started the flusher
# that drains it, and None again once that flusher is gone — so the middleware records ONLY
# while something is emptying the accumulator. Without that coupling a deployment with the
# sampler switched off (the test suite, a developer box) would grow one row per minute-bucket
# per principal, forever, in a dict nothing reads.
_load = None
# How often accumulated load rows are written out. 15 s keeps the write small (one upsert per
# minute-bucket per principal) while the Health tab's freshest minute is never more than a few
# seconds stale.
HEALTH_FLUSH_INTERVAL_S = 15.0
# What the sampler loop falls back to when the settings read itself fails: the same value as
# settings_store.HEALTH_DEFAULTS, so a broken settings row degrades to the default cadence
# rather than to a tight loop.
_HEALTH_SAMPLE_FALLBACK_S = 10.0


def _record_load(scope, started: float, status: int, declared_out, body_out: int) -> None:
    """Hand one finished request to the accumulator. Never raises: metering is not allowed to
    turn a served request into a 500, and it runs in the request's own task, so it must cost
    microseconds — one header scan, one dict update, no await."""
    acc = _load
    if acc is None:
        return
    try:
        principal = auth_service.current_principal()
        bytes_in = 0
        for name, value in scope.get("headers") or ():
            if name == b"content-length":
                bytes_in = int(value)
                break
        acc.record(
            principal_kind=principal.kind if principal is not None else "unknown",
            client_id=principal.client_id if principal is not None else None,
            ms=(time.perf_counter() - started) * 1000.0,
            status=status,
            bytes_in=bytes_in,
            # The declared length when the response has one; the bytes actually streamed
            # otherwise (SSE, chunked downloads), which a header cannot know up front.
            bytes_out=int(declared_out) if declared_out is not None else body_out,
        )
    except Exception:  # noqa: BLE001 — see the docstring
        pass


class _HealthLoadMiddleware:
    """Wall time, status and byte counts for every request, attributed to its principal.

    A PURE ASGI middleware rather than `@app.middleware("http")`, and that is load-bearing:
    `BaseHTTPMiddleware` runs `call_next` in a child task, so a ContextVar that the auth
    dependency sets inside the route is invisible to the caller of `call_next` — the principal
    would always read as None. Awaiting the downstream app directly keeps the whole request in
    ONE task, and `auth_service.current_principal()` answers after the await (the argument is
    written out above `auth._principal`). It also sees the real response start, which is where
    the status and the declared content-length live, and every body chunk, which is the only
    honest size for a stream.

    Everything is recorded except `/health` — the container healthcheck hits it every few
    seconds and would drown the table in a principal-less hum. The Health tab's OWN polling is
    deliberately kept: it shows up under `superadmin`, which is what it is.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _load is None or scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        # A handler that never starts a response died before answering; the error middleware
        # outside us turns that into a 500, so that is what it counts as here too.
        status = 500
        declared_out = None
        body_out = 0

        async def send_wrapped(message):
            nonlocal status, declared_out, body_out
            kind = message["type"]
            if kind == "http.response.start":
                status = message["status"]
                for name, value in message.get("headers") or ():
                    if name == b"content-length":
                        declared_out = value
                        break
            elif kind == "http.response.body":
                body_out += len(message.get("body") or b"")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            _record_load(scope, started, status, declared_out, body_out)


async def _health_sampler_loop(health_metrics) -> None:
    """One host sample per interval, the interval re-read from settings every loop so a change
    made in the console takes effect without a restart. A failing iteration is logged and the
    loop continues — a sampler that dies on one transient database blip is a Health tab that
    quietly goes flat."""
    sampler = health_metrics.HostSampler()
    while True:
        try:
            interval = float((await settings_store.get_health_config())["sample_interval_s"])
        except Exception:  # noqa: BLE001
            log.exception("health sampler: could not read settings; using %ss",
                          _HEALTH_SAMPLE_FALLBACK_S)
            interval = _HEALTH_SAMPLE_FALLBACK_S
        try:
            await health_metrics.insert_sample(await sampler.sample())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("health sampler: iteration failed; continuing")
        await asyncio.sleep(interval)


async def _health_flush_loop(health_metrics, acc) -> None:
    """Drain the accumulator into `tenant_load` every HEALTH_FLUSH_INTERVAL_S. Drained BEFORE
    the write, so rows that fail to flush are lost rather than double-counted on the retry —
    a gap in a load chart is honest, an inflated minute is not."""
    while True:
        await asyncio.sleep(HEALTH_FLUSH_INTERVAL_S)
        rows = acc.drain()
        if not rows:
            continue
        try:
            await health_metrics.flush_load(rows)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("health load flush: %s row(s) lost; continuing", len(rows))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _load
    await db.connect()
    for line in await run_startup_migrations():
        log.info("startup migration: %s", line)
    # Provider keys at rest: re-seal any legacy plaintext secret now that the DDL exists, then
    # turn the admin panel's keys into the first registry connections (no-op once any active
    # connection exists). Both AFTER migrations, both non-fatal: a deployment must still boot
    # on a vault problem — it just runs with the legacy settings and says so in the log.
    try:
        resealed = await ai_resolve.vault().migrate_plaintext()
        log.info("startup secrets: %s plaintext secret(s) re-sealed (mode=%s)",
                 resealed, ai_registry.secrets_status()["mode"])
    except Exception:  # noqa: BLE001
        log.exception("startup secrets: migrate_plaintext failed")
    for line in await ai_registry.seed_from_legacy():
        log.info("startup: %s", line)
    # Fail any analysis job left mid-flight by a previous crash/restart. The audio IS stored
    # now (every principal, under the Storage retention), but nothing re-drives a half-run
    # pipeline from a stored file — the caller resubmits. Workbench rows parked in `ready`
    # are a resting state, not mid-flight, and the sweep leaves them alone.
    swept = await analysis.sweep_stuck_jobs()
    if swept:
        log.info("startup: failed %s stuck analysis job(s)", swept)
    # Server health: the host sampler + the load flusher, api-side because the api is the
    # process being measured. Imported here rather than at the top so a problem in that module
    # (psutil missing on some image, say) costs the Health tab and not the whole API; the same
    # reasoning makes it non-fatal. The worker owns the retention purge.
    health_tasks: list[asyncio.Task] = []
    health_metrics = None
    if settings.health_sampler_enabled:
        try:
            from .services import health_metrics
            cfg = await settings_store.get_health_config()
            _load = health_metrics.LoadAccumulator()
            health_tasks = [
                asyncio.create_task(_health_sampler_loop(health_metrics), name="health_sampler"),
                asyncio.create_task(_health_flush_loop(health_metrics, _load),
                                    name="health_flush"),
            ]
            log.info("startup health: sampler every %ss, load flush every %.0fs, "
                     "retention %s day(s)", cfg["sample_interval_s"], HEALTH_FLUSH_INTERVAL_S,
                     cfg["retention_days"])
        except Exception:  # noqa: BLE001
            log.exception("startup health: sampler not started")
            _load = None
    try:
        yield
    finally:
        for task in health_tasks:
            task.cancel()
        if health_tasks:
            await asyncio.gather(*health_tasks, return_exceptions=True)
        # Last drain while the pool is still open, so the final seconds before a deploy are
        # not the seconds that go missing from every chart. Best effort, like the loop.
        acc, _load = _load, None
        if acc is not None and health_metrics is not None:
            try:
                rows = acc.drain()
                if rows:
                    await health_metrics.flush_load(rows)
            except Exception:  # noqa: BLE001
                log.exception("shutdown health: final load flush failed")
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

# Registered BEFORE `_no_store` below, which makes it the INNER of the two (Starlette stacks the
# last-added middleware outermost). That placement is the point: the load meter must share the
# route's task to read the principal (see the class docstring), and `_no_store` — a
# BaseHTTPMiddleware that spawns a child task — has to stay outside it, untouched.
app.add_middleware(_HealthLoadMiddleware)

@app.middleware("http")
async def _no_store(request: Request, call_next):
    """API responses are per-principal, so a cache must not keep them.

    Every route here answers differently for a superadmin, a tenant, a registered user and an
    anonymous visitor — and the difference is carried in request HEADERS (`Authorization`,
    `X-API-Key`, `X-Admin-Token`), not in the URL. A cache keyed on the URL alone, which is
    what a browser and any shared proxy are, can therefore hand one principal the body built
    for another.

    That was not theoretical. `/limits` shipped with no cache directive at all, so a phone
    answered the quota question from its own store instead of from the server: it reported an
    allowance that had already been spent, and kept reporting it after the numbers moved.
    Measuring the quota bug became impossible because the measurement was cached. nginx marks
    the app shell `no-cache` but says nothing about `/api/`, and the fix belongs here anyway —
    in the application, where it also covers the port the dev server talks to directly and
    cannot be lost by editing only one of the two nginx files.

    `no-store` rather than `no-cache`: the latter still permits a copy to be written to disk
    and merely requires revalidation, and a stored copy of one tenant's knowledge base is the
    thing being prevented, not a stale one.

    Set only where the route has NOT already decided. The audio downloads, the converted-file
    download and the SSE stream each choose their own directive on purpose, and overwriting
    those would be this middleware breaking three deliberate decisions to enforce a default.
    """
    response = await call_next(request)
    if "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "private, no-store"
    return response


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


@app.exception_handler(RegistryError)
async def _registry_error_handler(request: Request, exc: RegistryError):
    """A refused AI-registry write: FastAPI's own `detail` plus the machine `code` and the
    offending `field`, so the console and the portal can point at the input to fix — the
    same shape the transcription settings use."""
    return JSONResponse(status_code=exc.status, content={
        "detail": str(exc), "code": exc.code, "field": exc.field})


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
# AI provider registry: /admin/ai/* (connections, assignments, catalog — superadmin) and
# /ai/config (the workspace's own keys — owner). Root only, like the other settings routers.
app.include_router(ai_admin.router)
app.include_router(ai_tenant.router)

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
    # `secrets` and `ai` answer, from one unauthenticated curl, whether provider keys are
    # encrypted at rest and what the deployment defaults to per capability — names only,
    # never a key or a hint.
    secrets_mode = ai_registry.secrets_status()["mode"]
    try:
        async with db.pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        try:
            ai = await ai_registry.summary()
        except Exception as exc:  # noqa: BLE001 — the registry must not take /health down
            ai = {"connections": 0, "defaults": {c: None for c in ai_registry.CAPABILITIES},
                  "error": str(exc)}
        # `sentiment` is the STATE WORD only, never the sidecar's exception text: this route is
        # unauthenticated, and a load traceback carries paths. The operator gets the reason from
        # the console's connection test. It is here at all because the voice half fails silently
        # by design — text-only sentiment still returns 200 — so without one curl saying
        # `model_error`, a tone model that never loads is invisible from outside the box.
        try:
            voice_tone = (await sentiment_service.status())["state"]
        except Exception:  # noqa: BLE001 — /health must not depend on an optional sidecar
            voice_tone = "unknown"
        return {"status": "ok", "database": "connected", "client_addressing": addressing,
                "secrets": secrets_mode, "ai": ai, "voice_tone": voice_tone}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "database": "unavailable", "detail": str(exc),
                "client_addressing": addressing, "secrets": secrets_mode,
                "ai": {"connections": 0,
                       "defaults": {c: None for c in ai_registry.CAPABILITIES}}}


# Serve the static frontend from the API too, so the whole app is reachable on a
# single port (:8000) without needing nginx or an extra firewall rule. Mounted last
# so the API routes above take precedence; html=True falls back to index.html.
_FRONTEND_DIR = Path("/app/frontend")
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
