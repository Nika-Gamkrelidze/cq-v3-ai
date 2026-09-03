"""Scoring-rubric config endpoints + the answer-scoring playground.

Surfaces, all tenant-isolated:
  • Superadmin, tenant-parameterized:  GET/PUT /admin/scoring/{tenant_id}/config
                                       POST   /admin/scoring/{tenant_id}/import[?stream=1]
                                       POST   /admin/scoring/{tenant_id}/score-text
                                       POST   /admin/analyze/{tenant_id} (audio; see below)
  • Tenant self-serve (owner):          GET/PUT /scoring/config   (scoped to the caller)
                                        POST    /scoring/import[?stream=1]

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
from ..services.auth import Principal, client_ip, resolve_principal
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
async def _scope(tenant_id: str, principal: Principal = Depends(resolve_principal)) -> str:
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
    return await scoring_store.get_active_config(tid) or {"version": None, "dimensions": [],
                                                          "weights": {}, "rubric": "", "is_active": False}


@router.put("/admin/scoring/{tenant_id}/config")
async def admin_put(body: ConfigBody, tid: str = Depends(_scope)):
    try:
        return await scoring_store.save_config(tid, _dump(body.dimensions), body.rubric or "", "superadmin")
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
# Tenant self-serve — scoped to the authenticated tenant
# --------------------------------------------------------------------------- #
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


@router.get("/scoring/config")
async def tenant_get(client_id: str = Depends(_tenant)):
    return await scoring_store.get_active_config(client_id) or {"version": None, "dimensions": [],
                                                                "weights": {}, "rubric": "", "is_active": False}


@router.put("/scoring/config")
async def tenant_put(body: ConfigBody, client_id: str = Depends(_tenant_owner)):
    try:
        return await scoring_store.save_config(client_id, _dump(body.dimensions), body.rubric or "", "tenant")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
