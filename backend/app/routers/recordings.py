"""The Call Workbench's HTTP surface: recordings and summaries (design-v2 §8).

A *recording* is one `audio_jobs` row created here — from an upload (`source='audio'`) or a
pasted transcript (`source='text'`) — parked in status `ready` with its transcript and its §2
timeline. The analysers (fact-check, score, semantic) then run ON DEMAND against that row,
each writing its own column, so a reviewer pays for exactly the judgements they ask for and
History can replay any of them on the player. A *summary* is one `call_summaries` row over
one or several related recordings.

Routes (all resolve the principal; the kinds each one admits are stated on the handler):

  * `POST /recordings[?stream=1]`          upload → transcribe → ready
  * `POST /recordings/text`                paste → ready (no audio)
  * `POST /recordings/{id}/factcheck`      §4, tenant only (needs a knowledge base)
  * `POST /recordings/{id}/score`          §5, against `get_active_config_for(principal)`
  * `POST /recordings/{id}/semantic`       §6, `{"modes": ["text", "voice"]}`
  * `GET  /recordings[?limit=]`, `GET /recordings/{id}`, `GET /recordings/{id}/audio`
  * `POST /summaries[?stream=1]`           1..10 uploads → transcribe in order → §7
  * `GET  /summaries[?limit=]`, `GET /summaries/{id}`

An anonymous visitor may CREATE a recording (and gets its transcript and timeline back in the
response) but may not read one back afterwards: the only key an anonymous row has is the client
IP, which everyone behind one NAT shares, and this router serves the stored audio — see `_scope`.

`/recordings` rather than `/calls` because the legacy `routers/calls.py` owns that word. Two
transports on the upload routes, for the reason `scoring.py` gives for its rubric import: a
transcription is minutes for a long call, and a static spinner for that long is a hang to the
person watching it. Everything that can be judged about the REQUEST (kind, size, quota) is
judged before the transport is chosen, so those refusals are HTTP statuses, never an `error`
frame inside a 200 that has already started.
"""
import asyncio
import contextlib
import json
import logging
import mimetypes
import uuid
from pathlib import Path

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Request,
                     UploadFile)
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import pool
from ..services import (analysis, elevenlabs, factcheck, limits, llm, media,
                        scoring, scoring_store, segments, semantic, sentiment_config,
                        settings_store, summarise)
from ..services.auth import Principal, client_ip, resolve_principal
# One definition of "who owns this row", shared with the legacy upload routes rather than
# copied: writing a tenant login's `user_id` into the registered-user column is the kind of
# mistake that only shows up as an invisible recording weeks later.
from .analyze import _user_id
from .chat import _sse, _sse_response

router = APIRouter(tags=["recordings"])

log = logging.getLogger("cq")

MAX_BYTES = 100 * 1024 * 1024          # per upload, matching routers/analyze.py
MAX_SUMMARY_FILES = 10
MAX_SUMMARY_BYTES = 300 * 1024 * 1024  # per /summaries request, held in memory at once
# A pasted transcript feeds Claude the same way an upload does, so it is metered the same way;
# this bound is what stops one paste from being a novel. 200k characters is ~3 hours of dense
# Georgian speech, well past anything the analysers can place on one timeline.
MAX_TEXT_CHARS = 200_000
TEXT_FILENAME = "transcript.txt"        # what a pasted recording is called in lists and prompts
PING_S = 15.0                           # SSE keepalive cadence; matches scoring.py/convert.py
SEMANTIC_MODES = ("text", "voice")
# How many voice runs may hold a whole stored recording in memory at once, and how long a
# caller waits for a slot before being told to come back. Deliberately small: each slot is up
# to MAX_BYTES resident here PLUS a decode of up to 30 minutes of audio in the sidecar, on a
# box that shares its RAM with the embedding encoder.
VOICE_MAX_CONCURRENCY = 2
VOICE_WAIT_S = 20.0

_JSON_COLS = ("segments", "kb_check", "scoring", "semantic", "sentiment", "analysis", "kb_used")

# Process-wide, like `llm._LLM_SEM`: the ceiling being defended is this uvicorn worker's
# memory, not any one caller's quota, so it is not per-principal.
_voice_slots = asyncio.Semaphore(VOICE_MAX_CONCURRENCY)


# --------------------------------------------------------------------------- #
# Principals and scope
# --------------------------------------------------------------------------- #
def _require(principal: Principal, kinds: tuple[str, ...], action: str) -> None:
    """Admit the listed kinds. A signed-out caller gets 401 (sign in and try again); a
    signed-in kind the route is not for gets 403 (a different credential will not help)."""
    if principal.kind in kinds:
        return
    if principal.kind == "anonymous":
        raise HTTPException(status_code=401, detail=f"Sign in to {action}.")
    raise HTTPException(status_code=403, detail=f"This account cannot {action}.")


def _owner_predicate(principal: Principal, first: int) -> tuple[str, list] | None:
    """The signed-in branches shared by both scope helpers, or None for anyone else.

    A user row is matched on `user_id` WITH the `principal_type` discriminator: rows written
    for tenants have `user_id` NULL by construction (`_user_id`), and the discriminator keeps
    that true even if a future writer forgets.
    """
    if principal.is_superadmin:
        return "TRUE", []
    if principal.is_tenant:
        return f"client_id = ${first}", [principal.client_id]
    if principal.kind == "user" and principal.user_id:
        return f"user_id = ${first} AND principal_type = 'user'", [principal.user_id]
    if principal.kind == "integration":
        raise HTTPException(status_code=403,
                            detail="This integration credential cannot read recordings.")
    return None


def _scope(principal: Principal, first: int = 1) -> tuple[str, list]:
    """(where_sql, args) restricting `audio_jobs` rows to this principal — `analyze.py::_scope`
    plus the registered-user branch, MINUS its anonymous one.

    An anonymous caller is refused rather than matched on `anon_key`, because that key is the
    client IP: everyone behind one NAT, office egress or CGNAT pool is the same "visitor". The
    legacy /jobs listing lives with that for metadata; this router serves the STORED BYTES of
    every row it lists, so the same predicate would hand a stranger on the same public IP the
    original call recording. `tts.py::_history_scope` and `convert.py::_history_scope` refuse
    an anonymous history for exactly this reason. Nothing an anonymous caller is promised is
    lost: the POST response already carries the id, transcript and timeline, and the page plays
    the local file it just uploaded.
    """
    owned = _owner_predicate(principal, first)
    if owned is None:
        raise HTTPException(status_code=401, detail="Sign in to see your recordings.")
    return owned


def _summary_scope(principal: Principal, first: int = 1) -> tuple[str, list]:
    """Same policy for `call_summaries`, which has no anonymous rows and no `anon_key` column."""
    owned = _owner_predicate(principal, first)
    if owned is None:
        raise HTTPException(status_code=401, detail="Sign in to see your summaries.")
    return owned


# --------------------------------------------------------------------------- #
# Shared shapes and small helpers
# --------------------------------------------------------------------------- #
class _Failed(Exception):
    """A failure plus the HTTP status the blocking transport answers with — the SSE transport
    can only deliver it as an `error` event (the status line is already on the wire)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _json(value):
    """asyncpg hands jsonb back as a str unless a codec is registered."""
    return json.loads(value) if isinstance(value, str) else value


def _audio_url(job_id: str) -> str:
    # API-relative, like convert's download_path: the browser owns the `/api` base.
    return f"/recordings/{job_id}/audio"


def _recording(job_id: str, *, filename: str | None, language: str | None,
               duration_s: float | None, transcript: str, segs: list[dict],
               has_audio: bool) -> dict:
    """The ONE response shape of the ingest routes and of a summary's `calls[]` entries."""
    return {"id": job_id, "filename": filename, "language": language, "duration_s": duration_s,
            "transcript": transcript, "segments": segs,
            "audio_url": _audio_url(job_id) if has_audio else None, "status": "ready"}


def _stored_file(rel_path: str | None) -> Path | None:
    """The on-disk file behind an `audio_path`, or None when there is nothing to serve
    (never stored, purged, gone from the volume). Resolved under MEDIA_ROOT and required to
    still be under it — the row's path is data — same rule as `routers/tts.py::_stored_file`."""
    if not rel_path:
        return None
    root = media.MEDIA_ROOT.resolve()
    target = (media.MEDIA_ROOT / rel_path).resolve()
    if root not in target.parents:
        log.warning("recording audio refused a path outside the media root: %s", rel_path)
        return None
    return target if target.is_file() else None


def _media_type(content_type: str | None, path: Path) -> str:
    """What to label the bytes: the uploader's type when it named a real media type, else a
    guess from the stored extension — a `curl` upload arrives as octet-stream, and a player
    handed that will not always sniff."""
    base = (content_type or "").split(";")[0].strip().lower()
    if base.startswith(("audio/", "video/")):
        return base
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


_KEYS = {"stt": ("elevenlabs_api_key", "ElevenLabs"), "llm": ("anthropic_api_key", "Anthropic")}


async def _settings(*needs: str) -> dict:
    """The integration settings, with the keys a route is about to spend checked FIRST: a
    missing key is a clean 502 before a quota unit is taken or a row created — not a None
    result, an SDK exception dressed as a 500, or an error row plus the same 502 later."""
    cfg = await settings_store.get_effective()
    for need in needs:
        key, vendor = _KEYS[need]
        if not cfg.get(key):
            raise HTTPException(status_code=502,
                                detail=f"{vendor} API key is not configured (set it in the admin panel).")
    return cfg


def _upstream(exc: Exception) -> HTTPException:
    """An analyser's failure as an HTTP answer: 429 when the model was refused by our own
    admission gate (the caller should simply retry), 502 for everything upstream."""
    if isinstance(exc.__cause__, llm.LLMBusyError):
        return HTTPException(status_code=429, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


async def _store(job_id: str, column: str, result: dict) -> None:
    """Write one analyser's result into its jsonb column. `_update` is the pipeline's own
    writer (it knows which columns take the ::jsonb cast); there is no second one."""
    await analysis._update(job_id, **{column: json.dumps(result)})


async def _load(job_id: str, principal: Principal, cols: str) -> dict:
    """One recording the caller may see, or 404. Scope placeholders start at $2 (id is $1),
    the same trick `analyze.py::get_job` uses so the policy is not hand-copied per query."""
    where, args = _scope(principal, first=2)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {cols} FROM audio_jobs WHERE id = $1 AND {where}", job_id, *args)
    if row is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    data = dict(row)
    for k in _JSON_COLS:
        if k in data:
            data[k] = _json(data[k])
    return data


async def _pay_for_run(principal: Principal) -> None:
    """One `analyses` unit for one analyser run, taken AFTER every refusal that costs nothing.

    An upload is metered once, but each analyser is its own Claude call (and voice mode its own
    sidecar decode), so an unmetered re-run is an unbounded paid loop for anybody holding one
    recording: sign-ups are open by default, and a free account could replay /score and
    /semantic all day. It counts on the SAME `analyses` bucket the upload uses rather than a
    fourth kind, because `limits._KIND` is what the admin panel has dials for — a private kind
    here would be a cap no operator can see or set. For tenants (uncapped by default) this is
    what puts the spend on `usage_counters` instead of nowhere.
    """
    await limits.reserve(principal, "analyses")


@contextlib.asynccontextmanager
async def _voice_slot():
    """Bound the voice runs in flight, and refuse rather than queue when they are all busy.

    A voice run re-reads the STORED file (up to MAX_BYTES) into this process and posts it whole
    to the prosody sidecar, which decodes up to half an hour of audio — all triggered by a
    ~20-byte JSON body, so one caller can replay one big recording as often and as concurrently
    as they like. The per-call size is bounded by the upload limits; the aggregate was not, and
    a single-worker API that OOMs takes every tenant's in-flight work with it. 503 (with a
    retry hint) is a better answer than a request that sits behind two 120-second decodes.
    """
    try:
        await asyncio.wait_for(_voice_slots.acquire(), timeout=VOICE_WAIT_S)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Voice analysis is busy right now. Try again in a moment.") from None
    try:
        yield
    finally:
        _voice_slots.release()


def _transcript_of(row: dict) -> tuple[str, list[dict]]:
    """(transcript, segments) an analyser can work on, or 409 — a row still transcribing, one
    that failed, or a silent recording has nothing to judge."""
    transcript = (row.get("transcript") or "").strip()
    if not transcript:
        raise HTTPException(status_code=409, detail="This recording has no transcript to analyse.")
    return transcript, list(row.get("segments") or [])


# --------------------------------------------------------------------------- #
# Ingest: upload → transcribe → ready, in both transports
# --------------------------------------------------------------------------- #
async def _read_upload(file: UploadFile) -> bytes:
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(audio) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 100 MB limit")
    return audio


async def _ingest(job_id: str, audio: bytes, filename: str | None, content_type: str | None,
                  cfg: dict, *, emit=None) -> dict:
    """Transcribe an already-created row and park it `ready` with its timeline.

    Shared by the single-upload route (both transports) and the summaries batch. A failure is
    recorded on the row first — a row left in `transcribing` is what the startup sweep fails
    as abandoned, and this one was not abandoned, it was refused upstream.
    """
    if emit is not None:
        emit("stage", {"stage": "transcribing"})
    try:
        stt = await elevenlabs.transcribe(
            audio, filename, content_type, cfg["elevenlabs_api_key"], cfg["stt_model"])
    except Exception as exc:  # noqa: BLE001 — every STT failure is the same answer to the caller
        await analysis.mark_error(job_id, f"Transcription failed: {exc}")
        raise _Failed(502, f"Transcription failed: {exc}") from exc
    transcript = stt.get("text") or ""
    language = stt.get("language_code")
    segs, duration_s = analysis._timeline(stt, transcript)
    await analysis.mark_ready(job_id, transcript=transcript, language=language,
                              segments=segs, duration_s=duration_s)
    return _recording(job_id, filename=filename, language=language, duration_s=duration_s,
                      transcript=transcript, segs=segs, has_audio=True)


async def _abandon(job_ids: list[str]) -> None:
    """Close out rows whose request was cancelled mid-flight (the client went away during an
    SSE transcription), so they read as what happened rather than being swept as a crash."""
    for job_id in job_ids:
        try:
            await analysis.mark_error(job_id, "Cancelled: the request ended before transcription finished.")
        except Exception:  # noqa: BLE001 — best effort while being cancelled
            log.exception("could not mark recording %s as cancelled", job_id)


async def _stream(request: Request, run):
    """The SSE transport shared by both upload routes: `run(emit)` does the work and returns
    the `done` payload, raising `_Failed` for anything the caller should read as a refusal.

    The work runs as its own task feeding a queue rather than being awaited here, for the
    reason `chat.py::_pump` spells out: `asyncio.wait_for` CANCELS what it times out on, so
    wrapping the work itself in the keepalive timeout would abort the ElevenLabs call every
    fifteen seconds. Cancelling a `queue.get()` costs nothing.
    """
    queue: asyncio.Queue = asyncio.Queue()

    def emit(name: str, payload: dict) -> None:
        queue.put_nowait((name, payload))

    async def work() -> None:
        try:
            queue.put_nowait(("done", await run(emit)))
        except _Failed as exc:
            queue.put_nowait(("error", {"detail": exc.detail}))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the client is owed an answer, not a dead socket
            log.exception("recording stream failed")
            queue.put_nowait(("error", {"detail": "The request failed. Try again."}))
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(work())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=PING_S)
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


@router.post("/recordings")
async def upload_recording(request: Request, file: UploadFile = File(...),
                           as_stream: int = Query(default=0, alias="stream", ge=0, le=1),
                           principal: Principal = Depends(resolve_principal)):
    """tenant | user | anonymous. Store the bytes, transcribe, build the timeline, park the
    row `ready`. `?stream=1` narrates the same work over SSE (`stage`, `done` | `error`)."""
    _require(principal, ("tenant", "user", "anonymous"), "upload a recording")
    # The door before the till (`limits.check`): buffering 100 MB for somebody whose allowance
    # ran out hours ago is exactly the cost this meter exists to refuse, and `reserve()` can
    # only run once the size is known — i.e. after the read. Same order convert.py uses.
    await limits.check(principal, "analyses")
    audio = await _read_upload(file)
    cfg = await _settings("stt")
    await limits.reserve(principal, "analyses", len(audio))
    job_id = await analysis.create_job(
        filename=file.filename, content_type=file.content_type, size_bytes=len(audio),
        client_id=principal.client_id, principal_kind=principal.kind, anon_key=principal.anon_key,
        status="transcribing", client_ip=client_ip(request), audio=audio,
        user_id=_user_id(principal), source="audio", created_by=await _actor_name(principal))

    if as_stream:
        async def run(emit):
            try:
                return await _ingest(job_id, audio, file.filename, file.content_type, cfg,
                                     emit=emit)
            except asyncio.CancelledError:
                await _abandon([job_id])
                raise
        return _sse_response(_stream(request, run))
    try:
        return await _ingest(job_id, audio, file.filename, file.content_type, cfg)
    except _Failed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


class TextBody(BaseModel):
    text: str


@router.post("/recordings/text")
async def paste_recording(request: Request, body: TextBody,
                          principal: Principal = Depends(resolve_principal)):
    """tenant | user. A pasted transcript becomes a recording with no audio: one segment per
    line (speaker labels kept), no times, `ready` at once. Metered on `analyses` because it
    feeds the same analysers an upload does."""
    _require(principal, ("tenant", "user"), "paste a transcript")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413,
                            detail=f"Transcripts are limited to {MAX_TEXT_CHARS:,} characters.")
    segs = segments.segments_from_text(text)
    if not segs:
        raise HTTPException(status_code=400, detail="No transcript lines found in the text.")
    await limits.reserve(principal, "analyses")
    job_id = await analysis.create_job(
        filename=TEXT_FILENAME, content_type="text/plain", size_bytes=len(text.encode("utf-8")),
        client_id=principal.client_id, principal_kind=principal.kind, anon_key=None,
        status="ready", client_ip=client_ip(request), user_id=_user_id(principal), source="text",
        created_by=await _actor_name(principal))
    await analysis.mark_ready(job_id, transcript=text, language=None, segments=segs,
                              duration_s=None)
    return _recording(job_id, filename=TEXT_FILENAME, language=None, duration_s=None,
                      transcript=text, segs=segs, has_audio=False)


# --------------------------------------------------------------------------- #
# Analysers, on demand
# --------------------------------------------------------------------------- #
_ANALYSER_COLS = "id, filename, content_type, source, language, transcript, segments, audio_path"


@router.post("/recordings/{job_id}/factcheck")
async def factcheck_recording(job_id: str, principal: Principal = Depends(resolve_principal)):
    """Tenant only: the claims in the call, checked against THIS tenant's knowledge base.
    A registered user has no KB, so the refusal says what the feature needs rather than
    pretending the recording is missing."""
    if principal.kind == "user":
        raise HTTPException(status_code=403,
                            detail="Fact-checking needs a knowledge base, which only a workspace account has.")
    _require(principal, ("tenant",), "fact-check a recording")
    row = await _load(job_id, principal, _ANALYSER_COLS)
    transcript, segs = _transcript_of(row)
    async with pool().acquire() as conn:
        has_kb = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM kb_chunks WHERE client_id = $1)", principal.client_id)
    if not has_kb:
        raise HTTPException(status_code=409, detail="No knowledge base yet")
    cfg = await _settings("llm")
    await _pay_for_run(principal)
    try:
        result = await factcheck.run_factcheck(
            transcript, principal.client_id, cfg["anthropic_api_key"], cfg["llm_model"],
            segments=segs)
    except factcheck.FactCheckError as exc:
        raise _upstream(exc) from exc
    if result is None:
        raise HTTPException(status_code=409, detail="Nothing to check in this recording.")
    await _store(job_id, "kb_check", result)
    return result


class ScoreEdit(BaseModel):
    key: str
    score: int | None = None


class ScoreEditBody(BaseModel):
    scores: list[ScoreEdit] = []
    note: str | None = None


async def _actor_name(principal: Principal) -> str | None:
    """The person's NAME, for the History list's author column.

    Looked up once at creation and stored on the row rather than joined at read time: a shared
    workspace History is only useful if it still says who ran a call after that account is
    renamed or deleted, which is precisely when someone goes looking. Anonymous callers get
    None — there is no person to name, and their IP is not one.
    """
    if principal.is_superadmin:
        return "superadmin"
    try:
        async with pool().acquire() as conn:
            if principal.kind == "tenant" and principal.user_id:
                return await conn.fetchval(
                    "SELECT username FROM tenant_users WHERE id = $1", principal.user_id)
            if principal.is_operator:                # CQ staff acting on this workspace
                return "CommuniQ support"
            if principal.kind == "tenant":           # an API key has no person behind it
                return "API key"
            if principal.kind == "user" and principal.user_id:
                row = await conn.fetchrow(
                    "SELECT display_name, email FROM app_users WHERE id = $1", principal.user_id)
                if row:
                    return row["display_name"] or row["email"]
    except Exception:  # noqa: BLE001 — a missing label must never fail an upload
        log.warning("could not resolve the actor name for %s", principal.kind)
    return None


def _editor_name(principal: Principal) -> str:
    """Who to record as the author of an edit. A name, not an id: this is read by a person
    reviewing why a score changed, and it must still mean something after the account is gone."""
    if principal.is_superadmin:
        return "superadmin"
    if principal.kind == "user":
        return f"user:{principal.user_id}"
    return f"tenant:{principal.user_id or principal.role or 'member'}"


def _require_score_editor(principal: Principal) -> None:
    """Who may overrule the model. Overriding a score changes how an agent's work is judged,
    so a plain workspace MEMBER cannot: it is the owner's call (or the operator's). A
    registered user editing their own recording is their own owner."""
    _require(principal, ("tenant", "user", "superadmin"), "edit scores")
    if principal.kind == "tenant" and not principal.may_configure_workspace:
        raise HTTPException(status_code=403,
                            detail="Only a workspace owner can change a score.")


@router.get("/recordings/{job_id}/score/revisions")
async def score_history(job_id: str, principal: Principal = Depends(resolve_principal)):
    """Every version of this scorecard: the model's own first, then each manual edit with who
    made it. Readable by anyone who may read the recording — seeing that a score was changed
    is not a privilege, changing it is."""
    _require(principal, ("tenant", "user", "superadmin"), "see a recording")
    await _load(job_id, principal, "id")          # 404s unless the caller may see it
    return {"revisions": await scoring_store.revisions(job_id)}


@router.patch("/recordings/{job_id}/score")
async def edit_score(job_id: str, body: ScoreEditBody,
                     principal: Principal = Depends(resolve_principal)):
    """Replace one or more dimension scores by hand, keeping the model's own scorecard.

    Not metered and not an LLM call: this is a person typing a number. The weighted total is
    recomputed server-side from the stored weights — a client that posts a total is ignored.
    """
    _require_score_editor(principal)
    row = await _load(job_id, principal, "id, scoring")
    current = row.get("scoring")
    if not isinstance(current, dict) or not current.get("dimensions"):
        raise HTTPException(status_code=409, detail="This recording has not been scored yet.")

    known = {str(d.get("key")) for d in current["dimensions"] if isinstance(d, dict)}
    edits: dict[str, int | None] = {}
    for e in body.scores:
        key = str(e.key)
        if key not in known:
            raise HTTPException(status_code=400, detail=f"Unknown dimension: {key}")
        edits[key] = None if e.score is None else max(0, min(100, int(e.score)))
    if not edits:
        raise HTTPException(status_code=400, detail="No scores to change.")

    updated = scoring.apply_manual_scores(current, edits, edited_by=_editor_name(principal))
    # `original` backfills revision 1 for a scorecard produced before this history existed,
    # so the model's own numbers are on record even for older recordings.
    revision = await scoring_store.save_revision(
        job_id, updated, edited_by=_editor_name(principal), note=body.note, original=current)
    await _store(job_id, "scoring", updated)
    log.info("scoring edited job=%s by=%s revision=%s keys=%s",
             job_id, _editor_name(principal), revision, ",".join(sorted(edits)))
    return {**updated, "revision": revision}


@router.post("/recordings/{job_id}/score")
async def score_recording(job_id: str, principal: Principal = Depends(resolve_principal)):
    """tenant | user: score against the caller's active rubric — their own, or the default
    every owner without one inherits (`get_active_config_for`)."""
    _require(principal, ("tenant", "user"), "score a recording")
    await limits.require_feature(principal, "score")
    row = await _load(job_id, principal, _ANALYSER_COLS)
    transcript, segs = _transcript_of(row)
    config = await scoring_store.get_active_config_for(principal)
    if not config.get("dimensions"):
        raise HTTPException(status_code=409, detail="The scoring rubric has no dimensions yet.")
    cfg = await _settings("llm")
    await _pay_for_run(principal)
    try:
        result = await scoring.run_scoring(
            transcript, config, cfg["anthropic_api_key"], cfg["llm_model"],
            client_id=principal.client_id, segments=segs, user_id=_user_id(principal))
    except scoring.ScoringError as exc:
        raise _upstream(exc) from exc
    if result is None:
        # Transcript, key and dimensions were all checked above; the only way left to get
        # nothing is a rubric whose dimensions normalise to none.
        raise HTTPException(status_code=409, detail="The scoring rubric has no usable dimensions.")
    await _store(job_id, "scoring", result)
    return result


class SemanticBody(BaseModel):
    modes: list[str] = ["text"]


def _modes(body: SemanticBody) -> set[str]:
    modes = {str(m).strip().lower() for m in (body.modes or [])}
    unknown = sorted(modes - set(SEMANTIC_MODES))
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"Unknown mode(s): {', '.join(unknown)}. Choose from text, voice.")
    if not modes:
        raise HTTPException(status_code=400, detail="Choose at least one mode: text, voice.")
    return modes


async def _guidance(principal: Principal) -> str:
    """The operator's tone guidance: a tenant's own sentiment config, or the public one for a
    registered user (who has no config of their own — same source the public /sentiment uses)."""
    if principal.is_tenant:
        return (await sentiment_config.get_tenant_config(principal.client_id)).get("guidance") or ""
    return (await settings_store.get_public_sentiment_config()).get("guidance") or ""


@router.post("/recordings/{job_id}/semantic")
async def semantic_recording(job_id: str, body: SemanticBody,
                             principal: Principal = Depends(resolve_principal)):
    """tenant | user: tone of the words (`text`) and/or of the voice (`voice`, needs the
    stored audio — a text source drops it silently, a purged recording is a 409)."""
    _require(principal, ("tenant", "user"), "analyse a recording")
    await limits.require_feature(principal, "semantic")
    modes = _modes(body)
    row = await _load(job_id, principal, _ANALYSER_COLS)
    transcript, segs = _transcript_of(row)
    # Whether the file is still there is a stat, not a read: settle the 409 before a unit is
    # spent, and read the bytes only inside the slot that bounds how many of them exist at once.
    path = None
    if "voice" in modes and row["source"] == "audio":
        path = _stored_file(row["audio_path"])
        if path is None:
            raise HTTPException(status_code=409, detail="Audio no longer stored")
    cfg = await _settings("llm")
    await _pay_for_run(principal)
    guidance = await _guidance(principal)

    async def run(audio: bytes | None) -> dict:
        return await semantic.analyse(
            segments=segs, transcript=transcript, audio=audio, filename=row["filename"],
            content_type=row["content_type"], modes=modes,
            api_key=cfg["anthropic_api_key"], model=cfg["llm_model"],
            guidance=guidance, client_id=principal.client_id,
            user_id=_user_id(principal), language=row["language"])

    try:
        if path is None:
            result = await run(None)
        else:
            async with _voice_slot():
                result = await run(await asyncio.to_thread(path.read_bytes))
    except semantic.SemanticError as exc:
        raise _upstream(exc) from exc
    await _store(job_id, "semantic", result)
    return result


# --------------------------------------------------------------------------- #
# Reading recordings
# --------------------------------------------------------------------------- #
@router.get("/recordings")
async def list_recordings(limit: int = 20, principal: Principal = Depends(resolve_principal)):
    """The caller's recordings, newest first, with which analysers have run on each."""
    limit = max(1, min(limit, 100))
    where, args = _scope(principal)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, filename, source, status, language, duration_s, created_at, created_by,
                   audio_path IS NOT NULL AS has_audio,
                   kb_check IS NOT NULL AS ran_factcheck,
                   scoring  IS NOT NULL AS ran_score,
                   semantic IS NOT NULL AS ran_semantic
            FROM audio_jobs WHERE {where} ORDER BY created_at DESC LIMIT ${len(args)+1}
            """, *args, limit)
    return [{
        "id": str(r["id"]), "filename": r["filename"], "source": r["source"],
        "status": r["status"], "language": r["language"], "duration_s": r["duration_s"],
        "created_at": r["created_at"].isoformat(), "has_audio": r["has_audio"],
        # Who ran it. A workspace History is shared by every user in the tenant, so a row
        # without an author is just a thing that happened.
        "created_by": r["created_by"],
        "ran": {"factcheck": r["ran_factcheck"], "score": r["ran_score"],
                "semantic": r["ran_semantic"]},
    } for r in rows]


@router.get("/recordings/{job_id}")
async def get_recording(job_id: str, principal: Principal = Depends(resolve_principal)):
    """The full row: transcript, timeline and every result that has been produced for it."""
    row = await _load(job_id, principal, (
        "id, filename, content_type, size_bytes, source, status, language, duration_s, "
        "transcript, segments, kb_check, scoring, semantic, sentiment, analysis, error, "
        "created_at, created_by, audio_path"))
    has_audio = row.pop("audio_path") is not None
    return {**row, "id": str(row["id"]), "created_at": row["created_at"].isoformat(),
            "has_audio": has_audio, "audio_url": _audio_url(job_id) if has_audio else None}


@router.get("/recordings/{job_id}/audio")
async def recording_audio(job_id: str, principal: Principal = Depends(resolve_principal)):
    """The stored bytes, for the player. 404 for a recording the caller does not own, one that
    was never stored, or one the retention purge has already taken — the same answer for all
    three, so this URL cannot be used to probe other people's recordings. `private, no-store`:
    the retention purge must be the only thing deciding how long a copy exists. `FileResponse`
    honours Range on the installed Starlette, which is what lets the player seek."""
    row = await _load(job_id, principal, "id, filename, content_type, audio_path")
    path = _stored_file(row["audio_path"])
    if path is None:
        raise HTTPException(status_code=404, detail="Audio no longer stored")
    return FileResponse(path, media_type=_media_type(row["content_type"], path),
                        filename=row["filename"] or f"recording-{job_id[:8]}{path.suffix}",
                        content_disposition_type="inline",
                        headers={"Cache-Control": "private, no-store"})


# --------------------------------------------------------------------------- #
# Summaries
# --------------------------------------------------------------------------- #
def _check_batch_shape(files: list[UploadFile]) -> None:
    """The refusals that cost nothing to make, before a byte is read or a unit is spent."""
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(files) > MAX_SUMMARY_FILES:
        raise HTTPException(status_code=413,
                            detail=f"Up to {MAX_SUMMARY_FILES} recordings per summary. You sent {len(files)}.")


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, str | None, bytes]]:
    """Every upload, in full, BEFORE the transport is chosen: the SSE generator outlives the
    `UploadFile` objects (convert.py::_read_uploads explains). Sizes are enforced as we go so
    the cap bounds memory, not just the final count."""
    mb = 1024 * 1024
    out, total = [], 0
    for up in files:
        data = await up.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"'{up.filename or 'file'}' is empty.")
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"'{up.filename or 'file'}' is larger than the {MAX_BYTES // mb} MB per-file limit.")
        total += len(data)
        if total > MAX_SUMMARY_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"The batch is larger than the {MAX_SUMMARY_BYTES // mb} MB total limit.")
        out.append((up.filename or "audio", up.content_type, data))
    return out


async def _refuse_oversize(principal: Principal, uploads: list[tuple[str, str | None, bytes]]) -> None:
    """413 for a file over the caller's own size cap, BEFORE any unit is spent.

    `reserve()` enforces that cap per file, inside the same call that consumes the unit, so a
    batch whose third file is over it would pay for the first two and then be refused —
    deterministically, on a fact every file's size already told us before the loop started.
    The cap comes from `snapshot()` (the public read of the same tier config `reserve()` uses);
    a tenant's snapshot carries no `max_audio_mb`, which reads as uncapped, exactly as
    `reserve()` treats it.
    """
    cap_mb = int((await limits.snapshot(principal)).get("max_audio_mb") or 0)
    if not cap_mb:
        return
    over = [name for name, _ctype, data in uploads if len(data) > cap_mb * 1024 * 1024]
    if over:
        raise HTTPException(
            status_code=413,
            detail=(f"Uploads are limited to {cap_mb} MB for your account: "
                    f"{', '.join(repr(n) for n in over)} {'is' if len(over) == 1 else 'are'} larger."))


async def _reserve_batch(principal: Principal, uploads: list[tuple[str, str | None, bytes]]) -> None:
    """One `analyses` unit per file, up front, all or nothing.

    Convert truncates a batch at the first refusal because its files are independent; the
    calls of a summary are not — a digest of "the first three of five connected calls" is the
    misleading answer this route exists to avoid — so a refusal partway through fails the
    request. A refusal that was knowable up front (a file over the caller's size cap) is made
    first, for nothing; what remains is the quota running out mid-batch, where the units
    already spent are logged rather than refunded (`reserve()` has no refund path) because the
    refusal on file k tells the caller they had k-1 left, which is what they need before
    retrying with fewer files.
    """
    await _refuse_oversize(principal, uploads)
    for i, (filename, _ctype, data) in enumerate(uploads):
        try:
            await limits.reserve(principal, "analyses", len(data))
        except HTTPException as exc:
            if i:
                log.info("summaries: refused at file %s/%s after %s unit(s) spent (%s)",
                         i + 1, len(uploads), i, exc.detail)
                raise HTTPException(status_code=exc.status_code, detail=(
                    f"{exc.detail} Only {i} of {len(uploads)} recordings could be admitted "
                    f"('{filename}' was the first refused); send fewer files.")) from exc
            raise


def _summary_calls(recs: list[dict]) -> list[dict]:
    """The `calls[]` a summary response carries: each recording's ingest shape, keyed
    `job_id` as the summary's own `calls[]` entries are."""
    return [{"job_id": r["id"], **{k: v for k, v in r.items() if k not in ("id", "status")}}
            for r in recs]


async def _run_summary(request: Request, principal: Principal, cfg: dict,
                       uploads: list[tuple[str, str | None, bytes]], *, emit=None) -> dict:
    """Create + transcribe one recording per file IN UPLOAD ORDER (= chronological, which is
    what the digest's "call 1 … call n" means), then summarise the thread and record it.

    Sequential on purpose: the stages are narrated per file, and a thread's calls arriving out
    of order would be a worse failure than a slower one. A transcription failure stops the
    batch — the recordings already transcribed stay in History as ordinary recordings, the
    failed one is marked, and nothing after it is created.
    """
    n = len(uploads)
    recs: list[dict] = []
    ip = client_ip(request)
    actor = await _actor_name(principal)   # one lookup for the whole batch
    for i, (filename, content_type, audio) in enumerate(uploads):
        if emit is not None:
            emit("stage", {"stage": "transcribing", "index": i, "count": n, "filename": filename})
        job_id = await analysis.create_job(
            filename=filename, content_type=content_type, size_bytes=len(audio),
            client_id=principal.client_id, principal_kind=principal.kind, anon_key=None,
            status="transcribing", client_ip=ip, audio=audio,
            user_id=_user_id(principal), source="audio", created_by=actor)
        try:
            recs.append(await _ingest(job_id, audio, filename, content_type, cfg))
        except _Failed as exc:
            raise _Failed(exc.status, f"'{filename}': {exc.detail}") from exc
        except asyncio.CancelledError:
            await _abandon([job_id])
            raise

    if emit is not None:
        emit("stage", {"stage": "summarising"})
    try:
        summary = await summarise.summarise(
            [{"job_id": r["id"], "filename": r["filename"], "language": r["language"],
              "transcript": r["transcript"], "segments": r["segments"]} for r in recs],
            api_key=cfg["anthropic_api_key"], model=cfg["llm_model"],
            client_id=principal.client_id, user_id=_user_id(principal))
    except summarise.SummariseError as exc:
        status = 429 if isinstance(exc.__cause__, llm.LLMBusyError) else 502
        raise _Failed(status, str(exc)) from exc

    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO call_summaries (principal_type, client_id, user_id, job_ids, language,
                                        summary, created_by)
            VALUES ($1, $2, $3, $4::uuid[], $5, $6::jsonb, $7)
            RETURNING id, created_at
            """,
            principal.kind, principal.client_id if principal.is_tenant else None,
            _user_id(principal), [uuid.UUID(r["id"]) for r in recs],
            summary.get("language"), json.dumps(summary), actor)
    return {"id": str(row["id"]), "summary": summary, "calls": _summary_calls(recs),
            "created_at": row["created_at"].isoformat()}


@router.post("/summaries")
async def create_summary(request: Request, files: list[UploadFile] = File(...),
                         as_stream: int = Query(default=0, alias="stream", ge=0, le=1),
                         principal: Principal = Depends(resolve_principal)):
    """tenant | user. One or several related recordings (same people, separate calls) →
    one digest. `?stream=1` narrates it: `stage` per file, `stage` summarising, `done` |
    `error`."""
    _require(principal, ("tenant", "user"), "summarise recordings")
    await limits.require_feature(principal, "summarise")
    _check_batch_shape(files)
    # Before up to MAX_SUMMARY_BYTES are held in this process: `_read_uploads` cannot be
    # streamed (the SSE generator outlives the UploadFile objects) and `reserve()` needs the
    # sizes, so a caller with nothing left would otherwise buffer the whole batch for free,
    # as often as they liked. convert.py guards its identical read the same way.
    await limits.check(principal, "analyses")
    uploads = await _read_uploads(files)
    # Both keys are checked before N quota units are taken and a minute of transcription
    # is paid for, not after.
    cfg = await _settings("stt", "llm")
    await _reserve_batch(principal, uploads)

    if as_stream:
        return _sse_response(_stream(
            request, lambda emit: _run_summary(request, principal, cfg, uploads, emit=emit)))
    try:
        return await _run_summary(request, principal, cfg, uploads)
    except _Failed as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/summaries")
async def list_summaries(limit: int = 20, principal: Principal = Depends(resolve_principal)):
    """The caller's summaries, newest first, with enough of each digest to label a row."""
    limit = max(1, min(limit, 100))
    where, args = _summary_scope(principal)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, language, job_ids, summary, created_at, created_by
            FROM call_summaries WHERE {where} ORDER BY created_at DESC LIMIT ${len(args)+1}
            """, *args, limit)
    out = []
    for r in rows:
        summary = _json(r["summary"]) or {}
        out.append({
            "id": str(r["id"]), "language": r["language"],
            "created_at": r["created_at"].isoformat(), "created_by": r["created_by"],
            "call_count": len(r["job_ids"] or []),
            "short_summary": summary.get("short_summary") or "",
            "calls": [{"job_id": str(c.get("job_id") or ""), "filename": c.get("filename"),
                       "title": c.get("title")} for c in (summary.get("calls") or [])
                      if isinstance(c, dict)],
        })
    return out


@router.get("/summaries/{summary_id}")
async def get_summary(summary_id: str, principal: Principal = Depends(resolve_principal)):
    """One summary with its calls in thread order — each call's transcript and timeline, and
    its audio link while the file is still stored."""
    where, args = _summary_scope(principal, first=2)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT id, language, job_ids, summary, created_at, created_by FROM call_summaries "
            f"WHERE id = $1 AND {where}", summary_id, *args)
        if row is None:
            raise HTTPException(status_code=404, detail="Summary not found")
        # The calls are re-scoped too: a summary's job_ids can only ever be the owner's, but
        # the rule is "every tenant/user-scoped query filters", not "unless a join implies it".
        jwhere, jargs = _scope(principal, first=2)
        jobs = await conn.fetch(
            f"""
            SELECT id, filename, language, duration_s, transcript, segments, audio_path
            FROM audio_jobs WHERE id = ANY($1::uuid[]) AND {jwhere}
            """, list(row["job_ids"] or []), *jargs)
    by_id = {str(j["id"]): j for j in jobs}
    calls = []
    for jid in (row["job_ids"] or []):
        j = by_id.get(str(jid))
        if j is None:
            continue   # the recording row is gone; the digest still stands on its own
        calls.append({"job_id": str(j["id"]), "filename": j["filename"], "language": j["language"],
                      "duration_s": j["duration_s"], "transcript": j["transcript"],
                      "segments": _json(j["segments"]) or [],
                      "audio_url": _audio_url(str(j["id"])) if j["audio_path"] else None})
    return {"id": str(row["id"]), "language": row["language"], "summary": _json(row["summary"]),
            "calls": calls, "created_at": row["created_at"].isoformat()}
