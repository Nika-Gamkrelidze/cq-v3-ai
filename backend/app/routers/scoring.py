"""Scoring-rubric config endpoints + the answer-scoring playground.

Surfaces, all owner-isolated (a tenant by client_id, a registered user by user_id):
  • Superadmin, tenant-parameterized:  GET/PUT /admin/scoring/{tenant_id}/config
                                       POST   /admin/scoring/{tenant_id}/import[?stream=1]
                                       POST   /admin/scoring/{tenant_id}/score-text
                                       POST   /admin/analyze/{tenant_id} (audio; see below)
  • Superadmin, global:                 GET/PUT /admin/default-rubric (what owners without a
                                       rubric of their own score against)
  • Self-serve (tenant | user):         GET/PUT /scoring/config   (scoped to the caller; the
                                       GET answers with the default when they have none)
                                        POST    /scoring/reset    (copy the default in — the
                                       caller re-enters their own password)
  • Tenant self-serve (owner):          POST    /scoring/import[?stream=1]

The AI rubric import carries two transports over one body — blocking JSON, or SSE under
`?stream=1` — for the reason spelled out above `_ImportFailed`.
"""
import asyncio
import logging
import time
import uuid

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Request,
                     UploadFile)
from pydantic import BaseModel

from ..db import pool
from ..services import (analysis, factcheck, kb_ingest, scoring, scoring_import,
                        scoring_store, settings_store)
from ..services.auth import (Principal, client_ip, resolve_principal,
                             verify_password)
from .chat import _sse, _sse_response

router = APIRouter(tags=["scoring"])

log = logging.getLogger("cq")


class Dimension(BaseModel):
    key: str | None = None
    name: str
    description: str | None = ""
    guidance: str | None = ""
    weight: float = 0.0


class ConfigBody(BaseModel):
    dimensions: list[Dimension]
    rubric: str | None = ""


def _dump(dims: list[Dimension]) -> list[dict]:
    return [d.model_dump() for d in dims]


# --------------------------------------------------------------------------- #
# Superadmin, tenant-parameterized
# --------------------------------------------------------------------------- #
def _superadmin(principal: Principal = Depends(resolve_principal)) -> Principal:
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")
    return principal


async def _scope(tenant_id: str, principal: Principal = Depends(resolve_principal)) -> str:
    # The check stays inline (not via `_superadmin`): `_scope` is also called directly by
    # tests as one function, and it must reject before it looks at the tenant id.
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")
    try:
        uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Tenant not found")
    async with pool().acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM clients WHERE id = $1", tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant_id


@router.get("/admin/scoring/{tenant_id}/config")
async def admin_get(tid: str = Depends(_scope)):
    """The tenant's active rubric, or the shared default with `is_default` — exactly what
    `GET /scoring/config` answers them. Returning a blank config instead made every tenant
    who has never customised look, to the operator, like a tenant with no rubric at all."""
    return await scoring_store.get_active_config_for(_as_tenant(tid))


@router.put("/admin/scoring/{tenant_id}/config")
async def admin_put(body: ConfigBody, tid: str = Depends(_scope)):
    try:
        return await scoring_store.save_config(tid, _dump(body.dimensions), body.rubric or "", "superadmin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/admin/default-rubric", dependencies=[Depends(_superadmin)])
async def admin_get_default_rubric():
    """The rubric every owner WITHOUT one of their own scores against. `source` tells the
    panel whether it is showing the stored blob, the demo tenant's rubric it is seeded from,
    or the built-in fallback."""
    return await scoring_store.get_default_rubric()


@router.put("/admin/default-rubric", dependencies=[Depends(_superadmin)])
async def admin_put_default_rubric(body: ConfigBody):
    try:
        return await scoring_store.set_default_rubric(_dump(body.dimensions), body.rubric or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


MAX_IMPORT_BYTES = 25 * 1024 * 1024

# Both import routes come in two transports. Blocking (no `?stream=1`) is byte-for-byte what
# it always was, because the existing editors are live consumers of it. `?stream=1` narrates
# the same work over SSE — mirroring `POST /v1/chat/answer`, which carries the same pair over
# one handler — because a rubric import can take MINUTES: the model must reproduce every
# criterion of the standard verbatim, and a Georgian scorecard costs ~2 tokens per character
# to write back. A static "loading" line for that long is indistinguishable from a hang.
IMPORT_PING_S = 15.0            # keepalive comment; matches chat.py's cadence
PROGRESS_MIN_INTERVAL_S = 0.2   # at most five progress frames a second


class _ImportFailed(Exception):
    """A rubric-import failure plus the HTTP status the blocking route answers with.

    It exists because the two transports can no longer share a raise: once the SSE response
    has started, the status line is already on the wire and the only place left to say what
    went wrong is an `error` event. Carrying the status separately lets both transports
    deliver the SAME uploader-facing text.
    """

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def _read_import_upload(file: UploadFile) -> bytes:
    """Emptiness and size gate, run BEFORE the transport is chosen: a request that fails on
    its size must fail as an HTTP status, not as an `error` frame inside a 200."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 25 MB limit")
    return data


def _progress_emitter(emit):
    """Translate `scoring_import`'s stage dicts into wire events, throttled.

    The first dict arrives before the model call and becomes the `analyzing` stage — it
    carries `expected`, the denominator the bar divides by. Every later one is a `progress`
    frame, sent only when the whole-number percentage has actually moved AND at least
    PROGRESS_MIN_INTERVAL_S has passed since the last frame. Nothing is lost by dropping
    one: the percentage only ever rises, so the next frame carries it.
    """
    state = {"pct": -1, "at": 0.0, "started": False}

    def _on(d: dict) -> None:
        pct = int(d.get("pct") or 0)
        if not state["started"]:
            state.update(started=True, pct=pct, at=time.monotonic())
            emit("stage", {"stage": "analyzing", "expected": int(d.get("expected") or 0)})
            return
        now = time.monotonic()
        if pct <= state["pct"] or now - state["at"] < PROGRESS_MIN_INTERVAL_S:
            return
        state.update(pct=pct, at=now)
        emit("progress", d)

    return _on


async def _rubric_draft(data: bytes, filename: str | None, content_type: str | None,
                        client_id: str, *, emit=None) -> dict:
    """Shared body of both rubric-import routes: extract text, let the AI map it to a
    dimensions draft. Nothing is saved — the caller's editor receives a draft to review.

    `emit(name, payload)` is the streaming transport listening in; the blocking one passes
    nothing and this runs exactly as it did before. Failures raise `_ImportFailed` so each
    transport can deliver them its own way.
    """
    if emit is not None:
        # Deliberately no percentage. Extraction is ONE opaque call into pypdf / openpyxl /
        # python-docx with no progress signal of its own, and a number we invented here
        # would be the exact lie this feature exists to avoid. Name the stage; that is what
        # the user actually wants to know.
        emit("stage", {"stage": "extracting"})
    try:
        text = await asyncio.to_thread(kb_ingest.extract_text, filename, content_type, data)
    except Exception as exc:  # noqa: BLE001
        raise _ImportFailed(422, f"Could not read file: {exc}") from exc
    if not text.strip():
        raise _ImportFailed(422, (
            "No readable text found in the file. If this is a scanned document, run OCR "
            "or export a text-based version, then import again."))
    try:
        return await scoring_import.rubric_from_text(
            text, client_id=client_id,
            on_progress=_progress_emitter(emit) if emit is not None else None)
    except scoring_import.RubricImportError as exc:
        raise _ImportFailed(422, str(exc)) from exc


async def _rubric_stream(data: bytes, filename: str | None, content_type: str | None,
                         client_id: str, request: Request):
    """The SSE transport: `stage` -> `stage` + `progress`... -> `draft`, or `error`.

    The work runs as its own task feeding a queue rather than being awaited here, for the
    reason `chat.py::_pump` spells out: `asyncio.wait_for` CANCELS what it times out on, so
    wrapping the work itself in the keepalive timeout would abort the Anthropic call every
    fifteen seconds. Cancelling a `queue.get()` costs nothing.
    """
    queue: asyncio.Queue = asyncio.Queue()

    def emit(name: str, payload: dict) -> None:
        queue.put_nowait((name, payload))

    async def work() -> None:
        try:
            queue.put_nowait(("draft", await _rubric_draft(
                data, filename, content_type, client_id, emit=emit)))
        except _ImportFailed as exc:
            queue.put_nowait(("error", {"detail": exc.detail}))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the client is owed an answer, not a dead socket
            log.exception("rubric import stream failed (client=%s)", client_id)
            queue.put_nowait(("error", {"detail": "AI rubric import failed. Try again."}))
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(work())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=IMPORT_PING_S)
            except asyncio.TimeoutError:
                # A comment line: keeps the connection (and any intermediary) alive through
                # a long silent stretch without being an event the client must understand.
                yield ": ping\n\n"
                if await request.is_disconnected():
                    break
                continue
            if item is None:
                break
            yield _sse(item[0], item[1])
    finally:
        task.cancel()


async def _import_rubric(request: Request, file: UploadFile, client_id: str, as_stream: int):
    """Both import routes' one body, in both transports.

    Auth and tenant scoping are already done (the routes' dependencies); the size gate runs
    here, before either transport starts, so an oversized upload still fails as a clean HTTP
    status rather than as a frame inside a 200 that has already begun.
    """
    data = await _read_import_upload(file)
    if as_stream:
        return _sse_response(_rubric_stream(data, file.filename, file.content_type,
                                            client_id, request))
    try:
        return await _rubric_draft(data, file.filename, file.content_type, client_id)
    except _ImportFailed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.post("/admin/scoring/{tenant_id}/import")
async def admin_import_rubric(request: Request, file: UploadFile = File(...),
                              as_stream: int = Query(default=0, alias="stream", ge=0, le=1),
                              tid: str = Depends(_scope)):
    # `as_stream`, aliased: mirrors POST /v1/chat/answer's `?stream=1`, and keeps the name
    # clear of this module's own streaming helpers.
    return await _import_rubric(request, file, tid, as_stream)


class ScoreTextBody(BaseModel):
    text: str
    factcheck: bool = True   # also verify the answer's claims against the tenant's KB


async def _score_text(tid: str, text: str, do_factcheck: bool) -> dict:
    """Score a written operator answer against a tenant's active rubric (same engine the
    audio pipeline uses), optionally fact-checked against that tenant's own KB. Strictly
    scoped by tid — only this tenant's rubric and KB are ever used."""
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    cfg = await scoring_store.get_active_config(tid)
    if not cfg or not cfg.get("dimensions"):
        raise HTTPException(status_code=400,
                            detail="No active scoring rubric for this tenant yet. Set one before scoring.")

    s = await settings_store.get_effective()
    try:
        scorecard = await scoring.run_scoring(text, cfg, s["anthropic_api_key"], s["llm_model"],
                                              client_id=tid)
    except scoring.ScoringError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Ground "correctness" in the tenant's KB when there is one. Never blocks the score.
    kb_check = None
    if do_factcheck:
        try:
            async with pool().acquire() as conn:
                has_kb = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM kb_chunks WHERE client_id = $1)", tid)
            if has_kb:
                kb_check = await factcheck.run_factcheck(text, tid, s["anthropic_api_key"], s["llm_model"])
        except Exception:  # noqa: BLE001 — fact-check must never block the scorecard
            kb_check = None

    return {"scoring": scorecard, "kb_check": kb_check, "config_version": cfg.get("version")}


@router.post("/admin/scoring/{tenant_id}/score-text")
async def admin_score_text(body: ScoreTextBody, tid: str = Depends(_scope)):
    """Answer-scoring playground (superadmin, per tenant)."""
    return await _score_text(tid, body.text, body.factcheck)


MAX_ANALYZE_BYTES = 100 * 1024 * 1024  # matches routers/analyze.py's own limit


@router.post("/admin/analyze/{tenant_id}")
async def admin_analyze_audio(request: Request, file: UploadFile = File(...),
                              tid: str = Depends(_scope)):
    """KB-admin Playground's audio mode: the full pipeline (transcribe -> analysis -> KB
    fact-check -> rubric scoring), run against a tenant the superadmin chose, exactly as if
    that tenant had uploaded the file themselves via POST /analyze.

    A separate route rather than reusing /analyze: that endpoint scopes to the CALLER's own
    principal, and a superadmin has no client_id — there is nothing for it to run against.
    This one takes the tenant explicitly, gated the same way every other /admin/*/{tenant_id}
    route in this file is."""
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_ANALYZE_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")

    job_id = await analysis.create_job(
        filename=file.filename, content_type=file.content_type, size_bytes=len(audio),
        client_id=tid, principal_kind="tenant", anon_key=None,
        status="transcribing", client_ip=client_ip(request), audio=audio)
    result = await analysis.run_pipeline(
        job_id, audio, file.filename, file.content_type, tid, True)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("error") or "Analysis failed")
    return result


# --------------------------------------------------------------------------- #
# Self-serve — scoped to the authenticated owner (tenant by client_id, user by user_id)
# --------------------------------------------------------------------------- #
def _is_user(principal: Principal) -> bool:
    # `kind`/`user_id` read directly: `Principal.is_user` is being added concurrently.
    return principal.kind == "user" and bool(principal.user_id)


def _tenant(principal: Principal = Depends(resolve_principal)) -> str:
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login or API key required")
    return principal.client_id


def _tenant_owner(principal: Principal = Depends(resolve_principal)) -> str:
    """Editing the rubric needs full tenant authority: an owner login or the tenant API key."""
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login or API key required")
    if principal.role not in ("owner", "apikey"):
        raise HTTPException(status_code=403, detail="Owner role required to edit the scoring rubric")
    return principal.client_id


def _rubric_reader(principal: Principal = Depends(resolve_principal)) -> Principal:
    """Anyone who owns (or inherits) a rubric may read it: any tenant credential, or a user."""
    if principal.is_tenant or _is_user(principal):
        return principal
    raise HTTPException(status_code=401, detail="Tenant login, API key or user login required")


def _rubric_editor(principal: Principal = Depends(resolve_principal)) -> Principal:
    """A user edits their own rubric; a tenant keeps the owner|apikey rule of `_tenant_owner`."""
    if _is_user(principal):
        return principal
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login, API key or user login required")
    if principal.role not in ("owner", "apikey"):
        raise HTTPException(status_code=403, detail="Owner role required to edit the scoring rubric")
    return principal


def _rubric_resetter(principal: Principal = Depends(resolve_principal)) -> Principal:
    """Reset is guarded by re-entering one's OWN password, so it needs a principal that has
    one: a tenant owner login or a user. An API key has no password to verify (403, not 401 —
    the credential is valid, this action is just not available to it)."""
    if _is_user(principal):
        return principal
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login or user login required")
    if principal.role == "apikey" or not principal.user_id:
        raise HTTPException(status_code=403,
                            detail="An API key has no password to verify; reset from an owner login")
    if principal.role != "owner":
        raise HTTPException(status_code=403, detail="Owner role required to reset the scoring rubric")
    return principal


def _updated_by(principal: Principal) -> str:
    return "user" if _is_user(principal) else "tenant"


async def _password_matches(principal: Principal, password: str) -> bool:
    """Check `password` against the CALLER'S OWN stored hash — never anyone else's.

    A tenant login is looked up by its user id AND client_id, so a token can only ever verify
    against a user of the tenant it was issued for; a registered user by app_users.id. An
    inactive account fails the check like a wrong password."""
    async with pool().acquire() as conn:
        if principal.is_tenant:
            row = await conn.fetchrow(
                "SELECT password_hash, is_active FROM tenant_users WHERE id = $1 AND client_id = $2",
                principal.user_id, principal.client_id)
        else:
            row = await conn.fetchrow(
                "SELECT password_hash, is_active FROM app_users WHERE id = $1", principal.user_id)
    if not row or not row["is_active"]:
        return False
    # PBKDF2 (200k rounds) in a thread: this process is one uvicorn worker, and a blocking
    # ~40 ms hash inside an async handler stalls every other coroutine in it — including the
    # SSE keepalives a long transcription depends on. Same rule as routers/auth.py.
    return await asyncio.to_thread(verify_password, password, row["password_hash"])


@router.get("/scoring/config")
async def tenant_get(principal: Principal = Depends(_rubric_reader)):
    """The caller's active rubric, or the default (`is_default` true) when they have none."""
    return await scoring_store.get_active_config_for(principal)


@router.put("/scoring/config")
async def tenant_put(body: ConfigBody, principal: Principal = Depends(_rubric_editor)):
    try:
        return await scoring_store.save_config_for(
            principal, _dump(body.dimensions), body.rubric or "", _updated_by(principal))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class ResetBody(BaseModel):
    password: str


@router.post("/scoring/reset")
async def tenant_reset(body: ResetBody, principal: Principal = Depends(_rubric_resetter)):
    """Replace the caller's rubric with a COPY of the default, as a new version — so the
    reset is undoable by the same history every other save leaves, and a later change to
    the default does not silently follow them. The caller re-enters their own password
    because this throws away a rubric someone may have spent an afternoon tuning."""
    if not body.password:
        raise HTTPException(status_code=400, detail="password is required")
    if not await _password_matches(principal, body.password):
        raise HTTPException(status_code=403, detail="Password does not match")
    default = await scoring_store.get_default_rubric()
    try:
        return await scoring_store.save_config_for(
            principal, default["dimensions"], default["rubric"], "reset")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class BandsBody(BaseModel):
    amber_from: int
    green_from: int


@router.get("/scoring/bands")
async def get_bands(principal: Principal = Depends(resolve_principal)):
    """Where the colours change on this workspace's scorecards. Readable by anyone who can see
    a score — the thresholds are how you read the number, not a setting worth hiding."""
    if principal.kind not in ("tenant", "user"):
        raise HTTPException(status_code=401, detail="Tenant or account login required")
    return await scoring_store.get_bands(principal)


@router.put("/scoring/bands")
async def put_bands(body: BandsBody, principal: Principal = Depends(_rubric_editor)):
    """Move the red/amber/green boundaries. Same permission as editing the rubric: it changes
    how every past scorecard reads, not just future ones."""
    try:
        return await scoring_store.set_bands(principal, body.amber_from, body.green_from,
                                             "tenant" if principal.kind == "tenant" else "user")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/scoring/bands/reset")
async def reset_bands(principal: Principal = Depends(_rubric_editor)):
    """Back to red below 50, amber to 80, green above.

    Deliberately NOT password-guarded and deliberately a separate route from /scoring/reset:
    this throws away two numbers anyone can retype in seconds, while resetting the rubric
    throws away work. Sharing one button (or one confirmation) between them would make the
    cheap action feel dangerous and the dangerous one routine."""
    return await scoring_store.reset_bands(principal)


# ---- operator twins of the band routes --------------------------------------
# The console and the portal render the SAME page, so every control on it needs a route
# that works for a superadmin acting on a chosen tenant.
def _as_tenant(tid: str) -> Principal:
    """A tenant-shaped principal naming the scope a superadmin is acting on.

    `scoring_store` keys bands by `owner_key(principal)` — by client_id — so this reads and
    writes the very same row the tenant's own page does. It names a scope; it never grants
    one: the superadmin gate already ran in `_scope`.
    """
    return Principal(kind="tenant", client_id=tid, via="admin")


@router.get("/admin/scoring/{tenant_id}/bands")
async def admin_get_bands(tid: str = Depends(_scope)):
    return await scoring_store.get_bands(_as_tenant(tid))


@router.put("/admin/scoring/{tenant_id}/bands")
async def admin_put_bands(body: BandsBody, tid: str = Depends(_scope)):
    try:
        return await scoring_store.set_bands(_as_tenant(tid), body.amber_from,
                                             body.green_from, "superadmin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/admin/scoring/{tenant_id}/bands/reset")
async def admin_reset_bands(tid: str = Depends(_scope)):
    return await scoring_store.reset_bands(_as_tenant(tid))


@router.post("/scoring/import")
async def tenant_import_rubric(request: Request, file: UploadFile = File(...),
                               as_stream: int = Query(default=0, alias="stream", ge=0, le=1),
                               client_id: str = Depends(_tenant_owner)):
    """Owner-gated like every rubric edit: the draft is harmless, but gating the expensive
    AI call the same way as the save keeps members from spending the tenant's quota."""
    return await _import_rubric(request, file, client_id, as_stream)


@router.post("/scoring/score-text")
async def tenant_score_text(body: ScoreTextBody, client_id: str = Depends(_tenant)):
    """Tenant playground: score a written answer against the caller's own rubric + KB."""
    return await _score_text(client_id, body.text, body.factcheck)
