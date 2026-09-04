"""Convert uploaded audio or video into Asterisk playback formats, in bulk, as one ZIP.

Three surfaces open to signed-out visitors within the anonymous quota, and one for callers
who are signed in:

  * `GET  /convert/formats`          — the catalog + the batch limits, so the browser's
                                       dropdown and its client-side checks are built from
                                       the same table ffmpeg is driven by.
  * `POST /convert[?stream=1]`       — one or many files; blocking JSON, or the same work
                                       narrated over SSE.
  * `GET  /convert/{token}/download` — the batch as a ZIP.
  * `GET  /convert/history`          — a tenant's or registered user's past batches, with
                                       the download link only while the ZIP still exists.

Every batch that produced a download is also recorded in `convert_batches` (who, what, how
big, when it expires). That row is what History lists; the bytes and their deadline stay on
the volume beside the manifest (`services/audio_convert`), and the row never outranks them.

Two transports over one handler, mirroring `scoring.py`'s rubric import and for the same
reason: a batch of thirty recordings is minutes of ffmpeg, and a static spinner for that long
is indistinguishable from a hang. Everything that can be judged about the REQUEST — the
format, the file count, the sizes, the quota — is judged before the transport is chosen, so
those failures are HTTP statuses rather than an `error` frame inside a 200 that has already
started. Everything that can only be judged about a FILE is reported per file, and never
aborts the batch: losing twenty-nine good conversions to one corrupt upload is the worst
outcome this endpoint has.

The download is always a ZIP, even for a single file. One shape means one code path in the
browser and one content type; it is the only place the per-file names survive; and a bare
`.alaw` or `.sln` handed to a browser is a stream of bytes with an extension nothing
recognises, which is exactly when a download gets renamed or "helpfully" reinterpreted.
"""
import asyncio
import datetime as dt
import logging

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
# Starlette's, deliberately: FastAPI's own HTTPException is a SUBCLASS of it, and the 400 we
# are here to reword is raised by fastapi.routing as the Starlette one. Catching FastAPI's
# would quietly miss it — which is exactly what happened the first time this was written.
from starlette.exceptions import HTTPException as AnyHTTPException

from ..db import pool
from ..services import audio_convert, limits, settings_store
from ..services.audio_convert import ConvertError
from ..services.auth import Principal, resolve_principal
from .chat import _sse, _sse_response

log = logging.getLogger("cq")

class _UploadRoute(APIRoute):
    """Give FastAPI's opaque body-parse 400 a reason a caller can act on.

    A file name carrying a raw CRLF ends the Content-Disposition header in the middle of
    itself, and the parser abandons the whole body at that point — upstream of this router,
    before a single `UploadFile` exists. (A lone LF is tolerated by the parser and reaches
    `safe_output_name`, which collapses it; it is the pair that is unrecoverable, and it is
    also what a header-injection attempt looks like.)

    So the promise that one bad file never costs the other twenty-nine cannot hold for this
    one input: there is nothing left to convert them from. What can be fixed is the answer.
    "There was an error parsing the body" sends someone hunting through their recordings for
    a corrupt file; the fault is in a NAME, and saying so is the difference between a rename
    and an afternoon.

    Browsers strip line breaks from file names, so this is a programmatic caller's failure —
    which is exactly the caller with no UI to explain it to them.
    """

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except AnyHTTPException as exc:
                # FastAPI's `raise http_error from e` around `request.form()` is the only 400
                # in this router that carries a `__cause__` — every 400 we raise ourselves is
                # raised bare, so this cannot swallow one of them and reword it.
                if exc.status_code == 400 and exc.__cause__ is not None:
                    log.info("convert: unparseable multipart body (%s)", exc.__cause__)
                    raise HTTPException(
                        status_code=400,
                        detail=("The upload could not be read. One of the file names contains "
                                "a line break or another character that cannot appear in an "
                                "upload header — rename it and send the batch again."),
                    ) from exc.__cause__
                raise

        return handler


router = APIRouter(tags=["convert"], route_class=_UploadRoute)

# Keepalive cadence and progress throttle, both matching scoring.py — one long-running SSE
# route in this codebase should not feel different from another.
PING_S = 15.0

# A signed-in caller's batch outlives the anonymous two hours — a tenant or registered user
# comes back to History days later — but never a week: a batch is re-creatable from the source
# recording, so it does not deserve the full retention window a recording gets. The Storage
# setting's `retention_days` still wins when it is SHORTER, and its `0` ("keep recordings
# forever") reads as the cap here, because scratch bytes are never kept forever.
SIGNED_IN_TTL_CAP_DAYS = 7


def _signed_in(principal: Principal) -> bool:
    """A tenant (login or API key) or a registered user — the callers who have a History.
    Written against `kind`/`user_id` directly rather than an `is_user` property so this module
    does not depend on the resolver's newer surface to be importable."""
    return principal.is_tenant or (principal.kind == "user" and bool(principal.user_id))


def _owner(principal: Principal) -> str:
    """The batch owner string, built in ONE place from every principal field it keys on.

    A second call site that forgot `user_id` would key every registered user to the
    `anon:unknown` fallback — and to each other's downloads.
    """
    return audio_convert.owner_key(principal.kind, principal.client_id, principal.anon_key,
                                   principal.integration_id, principal.user_id)


async def _batch_ttl_seconds(principal: Principal) -> int:
    """How long this caller's batch stays downloadable: the anonymous default, or for a
    signed-in caller `min(retention_days, SIGNED_IN_TTL_CAP_DAYS)` days."""
    if not _signed_in(principal):
        return audio_convert.TTL_SECONDS
    days = int((await settings_store.get_storage_config())["retention_days"])
    if days <= 0 or days > SIGNED_IN_TTL_CAP_DAYS:
        days = SIGNED_IN_TTL_CAP_DAYS
    return days * 86400


def _history_scope(principal: Principal, first: int = 1) -> tuple[str, list]:
    """(where_sql, args) restricting `convert_batches` to what this principal may list.

    Mirrors `routers/analyze.py::_scope`: the superadmin sees everything (it is the operator,
    not a customer), a tenant its own rows, a registered user their own — each with the
    `principal_type` discriminator riding along so a row can never match through the wrong
    column. There is NO anonymous branch on purpose: anonymous batches are keyed on an IP, and
    an IP is shared by everyone behind the same NAT, so "your history" would be your office's.
    """
    if principal.is_superadmin:
        return "TRUE", []
    if principal.is_tenant:
        return f"client_id = ${first} AND principal_type = 'tenant'", [principal.client_id]
    if principal.kind == "user" and principal.user_id:
        return f"user_id = ${first} AND principal_type = 'user'", [principal.user_id]
    if principal.kind == "integration":
        raise HTTPException(status_code=403,
                            detail="This integration credential cannot read conversion history.")
    raise HTTPException(status_code=401, detail="Sign in to see your conversion history.")


async def _record_batch(principal: Principal, summary: dict) -> None:
    """One `convert_batches` row per batch that produced a download — the caller's History.

    Written AFTER the manifest, so a row never points at a batch that does not exist, and it
    never raises: the ZIP is already on disk and the caller is owed it whether or not the
    bookkeeping landed. The row copies the manifest's deadline so History can say "expired"
    without reading the volume. Owner columns are written by KIND — a tenant login's
    `user_id` is a tenant_users row and must not land in the column a registered user's
    history is scoped by.
    """
    if not summary.get("token"):
        return
    try:
        files = summary.get("files") or []
        async with pool().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO convert_batches
                    (token, principal_type, client_id, user_id, anon_key, format,
                     file_count, total_bytes, expires_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (token) DO NOTHING
                """,
                summary["token"], principal.kind,
                principal.client_id if principal.is_tenant else None,
                principal.user_id if principal.kind == "user" else None,
                principal.anon_key if principal.kind == "anonymous" else None,
                summary["format"], int(summary["converted"]),
                sum(int(f.get("bytes") or 0) for f in files if f.get("ok")),
                dt.datetime.fromisoformat(summary["expires_at"]))
    except Exception:  # noqa: BLE001 — bookkeeping must never cost the caller their ZIP
        log.exception("convert: could not record batch %s", summary.get("token"))


@router.get("/convert/formats")
async def formats():
    """What we can convert to, and how much may be sent at once.

    The limits ride along with the catalog deliberately: a UI that validates against its own
    hardcoded numbers drifts, and then rejects a batch the server would have accepted (or
    worse, accepts one it will not).
    """
    return {
        "formats": audio_convert.catalog(),
        "default": audio_convert.DEFAULT_FORMAT,
        "limits": {
            "max_files": audio_convert.MAX_BATCH_FILES,
            "max_file_bytes": audio_convert.MAX_FILE_BYTES,
            "max_batch_bytes": audio_convert.MAX_BATCH_BYTES,
            "download_ttl_seconds": audio_convert.TTL_SECONDS,
        },
        "available": audio_convert.audio_tools_available(),
    }


def _check_batch_shape(files: list[UploadFile]) -> None:
    """Everything about the batch that can be judged WITHOUT reading a byte of it.

    Separate from `_read_uploads` so the quota gate can sit between the two: these two
    refusals cost nothing to make and must not cost the caller a quota unit either, while
    the read below is the expensive part that a caller with no allowance left must never
    reach.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(files) > audio_convert.MAX_BATCH_FILES:
        raise HTTPException(
            status_code=413,
            detail=(f"Up to {audio_convert.MAX_BATCH_FILES} files per batch. "
                    f"You sent {len(files)} — split them into smaller batches."))


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    """Read every upload into memory, enforcing the size caps as we go.

    Read here, in full, BEFORE either transport is chosen — the same thing
    `scoring.py::_read_import_upload` does, and not just for symmetry: the SSE generator
    outlives this function, and an `UploadFile`'s spooled temp file does not. A generator
    holding `UploadFile` objects reads from files FastAPI has already closed.

    That is what bounds `MAX_BATCH_BYTES`: it is a memory figure, not a bandwidth one — and
    it is why the caller's allowance is checked before this runs rather than after.
    """
    mb = 1024 * 1024
    out: list[tuple[str, bytes]] = []
    total = 0
    for up in files:
        data = await up.read()
        # Fail on the file that broke the cap rather than after reading the rest: the point
        # of the cap is not to hold the rest in memory.
        if len(data) > audio_convert.MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(f"'{up.filename or 'file'}' is larger than the "
                        f"{audio_convert.MAX_FILE_BYTES // mb} MB per-file limit."))
        total += len(data)
        if total > audio_convert.MAX_BATCH_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(f"The batch is larger than the {audio_convert.MAX_BATCH_BYTES // mb} MB "
                        "total limit. Send fewer files at a time."))
        out.append((up.filename or "audio", data))
    return out


async def _reserve_batch(principal: Principal, count: int) -> tuple[int, str | None]:
    """Consume one quota unit per FILE, up front. Returns (files granted, refusal text).

    Up front, before any ffmpeg runs, so the common refusals ("anonymous conversion is off",
    "daily limit reached") are a clean 4xx instead of a frame inside a 200. A refusal on the
    first file re-raises — nothing has been consumed and nothing has been promised — while a
    refusal partway through truncates the batch: the files already paid for still get
    converted, and the rest are reported with the reason they were not.

    A file that then fails in ffmpeg has still spent its unit. That is deliberate: the unit
    meters the CPU we agreed to spend on the caller's behalf, and a transcode that ran and
    failed spent it just as surely as one that worked.
    """
    for i in range(count):
        try:
            await limits.reserve(principal, "conversions")
        except HTTPException as exc:
            if i == 0:
                raise
            log.info("convert: batch truncated at %s/%s files (%s)", i, count, exc.detail)
            return i, str(exc.detail)
    return count, None


def _entry(index: int, name: str, *, output: str | None = None, size: int = 0,
           ok: bool = True, error: str | None = None) -> dict:
    """One row of the per-file result, in the ONE shape both transports emit."""
    return {"index": index, "name": name, "output": output, "bytes": size,
            "ok": ok, "error": error}


async def _run_batch(uploads: list[tuple[str, bytes]], fmt: str, granted: int,
                     refusal: str | None, batch: audio_convert.Batch, *, emit=None) -> dict:
    """Convert the batch file by file, appending each result to the ZIP as it lands.

    Sequential on purpose. `audio_convert` already caps concurrent ffmpeg processes across
    the whole api process, and converting one batch in parallel would only spend that budget
    on one caller. A per-file failure is recorded and the loop continues — see the module
    docstring.
    """
    total = len(uploads)
    taken: set[str] = set()
    entries: list[dict] = []
    converted = 0

    if emit is not None:
        emit("stage", {"stage": "converting", "total": total, "format": fmt})

    for i, (filename, data) in enumerate(uploads):
        if i >= granted:
            entry = _entry(i, filename, ok=False,
                           error=refusal or "Daily conversion limit reached.")
        else:
            try:
                payload, name = await audio_convert.convert(data, filename, fmt)
                name = audio_convert.dedupe(name, taken)
                await batch.add(name, payload)
                entry = _entry(i, filename, output=name, size=len(payload))
                converted += 1
            except ConvertError as exc:
                entry = _entry(i, filename, ok=False, error=str(exc))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad file must not cost the other twenty-nine
                log.exception("convert: unexpected failure on %r", filename)
                entry = _entry(i, filename, ok=False,
                               error="The server failed to convert this file.")
        entries.append(entry)
        if emit is not None:
            emit("progress", {**entry, "total": total})

    return {"entries": entries, "converted": converted, "failed": total - converted,
            "total": total}


def _finish(batch: audio_convert.Batch, result: dict, refusal: str | None,
            ttl_seconds: int = audio_convert.TTL_SECONDS) -> dict:
    """Close the batch and shape the answer both transports send.

    A batch where nothing converted is DISCARDED rather than left as an empty ZIP: there is
    no download, so there must be no token — a token that resolves to nothing is a worse
    answer than no token. `ttl_seconds` is the caller's deadline (`_batch_ttl_seconds`).
    """
    if not result["converted"]:
        batch.discard()
        return {"token": None, "download_path": None, "expires_at": None,
                "format": batch.fmt, "total": result["total"], "converted": 0,
                "failed": result["failed"], "files": result["entries"],
                "quota_refusal": refusal}
    manifest = batch.close(result["entries"], ttl_seconds=ttl_seconds)
    return {
        "token": batch.token,
        # API-relative on purpose: the browser is served through nginx's `/api/` prefix and
        # the frontend already owns that base. An absolute URL built here would have to guess
        # the scheme and host, which is exactly what gets it wrong behind a proxy.
        "download_path": f"/convert/{batch.token}/download",
        "expires_at": manifest["expires_at"],
        "format": batch.fmt,
        "total": result["total"],
        "converted": result["converted"],
        "failed": result["failed"],
        "files": result["entries"],
        "quota_refusal": refusal,
    }


async def _convert_stream(uploads: list[tuple[str, bytes]], fmt: str, granted: int,
                          refusal: str | None, batch: audio_convert.Batch, request: Request,
                          principal: Principal, ttl_seconds: int):
    """The SSE transport: `stage` -> `progress` per file -> `done`, or `error`.

    The work runs as its own task feeding a queue rather than being awaited here, for the
    reason `chat.py::_pump` spells out: `asyncio.wait_for` CANCELS what it times out on, so
    wrapping the conversion in the keepalive timeout would kill an ffmpeg process every
    fifteen seconds. Cancelling a `queue.get()` costs nothing.
    """
    queue: asyncio.Queue = asyncio.Queue()

    def emit(name: str, payload: dict) -> None:
        queue.put_nowait((name, payload))

    async def work() -> None:
        try:
            result = await _run_batch(uploads, fmt, granted, refusal, batch, emit=emit)
            summary = _finish(batch, result, refusal, ttl_seconds=ttl_seconds)
            if summary["converted"]:
                # Recorded before `done` is sent, so a client that lists History on that
                # event sees the batch it was just handed.
                await _record_batch(principal, summary)
                queue.put_nowait(("done", summary))
            else:
                # Nothing came out, so there is nothing to download. Still send the per-file
                # detail — "none of them worked" is not an answer, "this one has no audio
                # track and that one is a text file" is.
                queue.put_nowait(("error", {
                    "detail": refusal or "None of these files could be converted.",
                    **summary}))
        except ConvertError as exc:
            batch.discard()
            queue.put_nowait(("error", {"detail": str(exc)}))
        except asyncio.CancelledError:
            batch.discard()
            raise
        except Exception:  # noqa: BLE001 — the client is owed an answer, not a dead socket
            log.exception("convert stream failed (format=%s, files=%s)", fmt, len(uploads))
            batch.discard()
            queue.put_nowait(("error", {"detail": "Conversion failed. Try again."}))
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(work())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=PING_S)
            except asyncio.TimeoutError:
                # A comment line: keeps the connection and any intermediary alive through a
                # long single file without being an event the client must understand.
                yield ": ping\n\n"
                if await request.is_disconnected():
                    break
                continue
            if item is None:
                break
            yield _sse(item[0], item[1])
    finally:
        task.cancel()


@router.post("/convert")
async def convert_audio(request: Request,
                        files: list[UploadFile] = File(...),
                        fmt: str = Form(default=audio_convert.DEFAULT_FORMAT, alias="format"),
                        as_stream: int = Query(default=0, alias="stream", ge=0, le=1),
                        principal: Principal = Depends(resolve_principal)):
    """Convert one or many uploads to `format`; `?stream=1` narrates the same work over SSE.

    `as_stream`, aliased: mirrors `POST /scoring/import` and `POST /v1/chat/answer`, and keeps
    the name clear of this module's own streaming helpers.
    """
    # A blank field is an ABSENT field. FastAPI already substitutes the default for an empty
    # string, so anything else here would answer "" with a 400 and "   " with a 200, or the
    # other way round, depending on which layer got to it first.
    fmt = (fmt or "").strip().lower() or audio_convert.DEFAULT_FORMAT
    if fmt not in audio_convert.FORMATS:
        raise HTTPException(
            status_code=400,
            detail=(f"Unknown target format '{fmt}'. Supported: "
                    f"{', '.join(audio_convert.FORMATS)}."))
    if not audio_convert.audio_tools_available():
        raise HTTPException(status_code=503,
                            detail="Audio conversion is unavailable on this server.")

    _check_batch_shape(files)
    # Before `_read_uploads` copies the batch into THIS PROCESS'S MEMORY, which is the cost
    # that scales with concurrency: 150 MB per request, held for as long as the request lives,
    # on a box that shares its RAM with the TEI encoder serving live retrieval. (The bytes
    # themselves have already arrived — nginx buffers the whole request body before it opens
    # the upstream connection, and Starlette has spooled the parts to disk — so this is the
    # earliest point at which refusing actually saves anything.) `check` spends no quota, so
    # the two refusals above stay free.
    await limits.check(principal, "conversions")

    uploads = await _read_uploads(files)
    granted, refusal = await _reserve_batch(principal, len(uploads))
    # Decided before the batch opens, not at close: the deadline counts from the upload, and
    # a settings read must not sit between the last ffmpeg run and the manifest write.
    ttl_seconds = await _batch_ttl_seconds(principal)

    batch = audio_convert.Batch(audio_convert.new_token(), fmt, _owner(principal))
    try:
        batch.open()
    except ConvertError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if as_stream:
        return _sse_response(_convert_stream(uploads, fmt, granted, refusal, batch, request,
                                             principal, ttl_seconds))

    try:
        result = await _run_batch(uploads, fmt, granted, refusal, batch)
        summary = _finish(batch, result, refusal, ttl_seconds=ttl_seconds)
    except ConvertError as exc:
        batch.discard()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception:
        batch.discard()
        raise
    await _record_batch(principal, summary)
    # A batch that converted nothing still answers 200 with the per-file reasons: the caller
    # asked about a batch, and "which files failed and why" IS the answer. Only refusals
    # about the REQUEST (bad format, too many files, quota) are HTTP statuses, and those were
    # all raised above.
    return summary


@router.get("/convert/history")
async def convert_history(limit: int = 20, principal: Principal = Depends(resolve_principal)):
    """A signed-in caller's past batches, newest first, with `download_path` only while the
    ZIP is still there.

    `download_path` goes null at the deadline rather than being left for the download route
    to 404 on: a History row that offers a link to nothing is worse than one that says it
    expired. The judge is the database's clock against the row's copy of the manifest's
    deadline — the same instant `locate()` reads — so this list and the download route agree
    without this route touching the volume.
    """
    limit = max(1, min(limit, 100))
    where, args = _history_scope(principal)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT token, format, file_count, total_bytes, created_at, expires_at,
                   (expires_at IS NULL OR expires_at > now()) AS live
              FROM convert_batches
             WHERE {where}
             ORDER BY created_at DESC
             LIMIT ${len(args) + 1}
            """, *args, limit)
    return [{
        "token": r["token"],
        "format": r["format"],
        "file_count": r["file_count"],
        "total_bytes": r["total_bytes"],
        "created_at": r["created_at"].isoformat(),
        "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
        "download_path": f"/convert/{r['token']}/download" if r["live"] else None,
    } for r in rows]


@router.get("/convert/{token}/download")
async def download_batch(token: str, principal: Principal = Depends(resolve_principal)):
    """The batch as a ZIP, for the principal that created it.

    Not yours, not there, and expired are one answer — a 404 — on purpose: a token belonging
    to someone else must be indistinguishable from one that never existed, or the endpoint
    tells an enumerator when they have guessed right.
    """
    found = audio_convert.locate(token, _owner(principal))
    if found is None:
        raise HTTPException(status_code=404,
                            detail="This download has expired or is no longer available.")
    manifest = found["manifest"]
    n = sum(1 for f in manifest.get("files") or [] if isinstance(f, dict) and f.get("ok"))
    # Token-suffixed so two batches of the same format land as two files rather than
    # "cq-alaw (1).zip", and so a support question can be traced back to a batch.
    name = f"cq-{manifest.get('format', 'audio')}-{n}-{token[:8]}.zip"
    return FileResponse(
        found["path"], media_type="application/zip", filename=name,
        # Someone's recordings: never let a shared cache or an intermediary keep a copy.
        headers={"Cache-Control": "no-store, private"})
