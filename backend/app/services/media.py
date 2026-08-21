"""Retained media for anonymous (unregistered) submissions.

Everything an unregistered visitor sends us — the uploaded recording, the text they asked us
to speak, the clip we spoke back — is kept for a bounded window so abuse can be investigated
and a bad result can be reproduced. Two rules shape this module:

  * **Bytes on a volume, metadata in Postgres.** Recordings are megabytes; putting them in a
    bytea column would drag every one of them through every pg_dump forever. The row keeps the
    path, size and checksum instead, so one sweep can delete both halves.
  * **Every stored object gets a deadline when it is written**, never "when someone remembers".
    `purge_after` is set at write time from the admin's retention setting, so an object that is
    never read again still expires on its own.

Paths are relative to MEDIA_ROOT and stored that way, so moving the volume does not invalidate
every row in the database.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
import uuid
from pathlib import Path

log = logging.getLogger("cq")

MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "/data/media"))

# Anything not in this map is stored as .bin — the extension is cosmetic (it makes the volume
# browsable), never trusted, and never taken from the client's filename.
_EXT_BY_TYPE = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/flac": ".flac",
    "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".aac", "video/mp4": ".mp4",
    "video/webm": ".webm", "video/quicktime": ".mov",
}

_SAFE_REL = re.compile(r"^[0-9]{4}/[0-9]{2}/[0-9a-f-]{36}[A-Za-z0-9.]*$")


def _ext_for(content_type: str | None, filename: str | None) -> str:
    if content_type:
        base = content_type.split(";")[0].strip().lower()
        if base in _EXT_BY_TYPE:
            return _EXT_BY_TYPE[base]
    if filename and "." in filename:
        cand = "." + filename.rsplit(".", 1)[1].lower()
        # Only echo the client's extension when it is short and alphanumeric — a filename is
        # attacker-controlled and this string becomes part of a path.
        if len(cand) <= 6 and cand[1:].isalnum():
            return cand
    return ".bin"


def deadline(retention_days: int) -> dt.datetime | None:
    """Absolute purge time for something written now, or None to keep indefinitely."""
    days = int(retention_days or 0)
    if days <= 0:
        return None
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)


def save(data: bytes, *, content_type: str | None = None, filename: str | None = None) -> dict:
    """Write bytes under MEDIA_ROOT/YYYY/MM/<uuid><ext>. Returns metadata for the DB row.

    Never raises to the caller's business path: storing a copy is a retention duty, not part
    of answering the request, so a full disk must not fail a user's transcription. On failure
    the returned path is None and the row simply records that there is no stored object.
    """
    now = dt.datetime.now(dt.timezone.utc)
    rel = f"{now:%Y}/{now:%m}/{uuid.uuid4()}{_ext_for(content_type, filename)}"
    try:
        dest = MEDIA_ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return {"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    except Exception:  # noqa: BLE001 — retention must never break the request
        log.exception("media.save failed; continuing without a stored copy")
        return {"path": None, "bytes": len(data), "sha256": None}


def delete(rel_path: str | None) -> bool:
    """Delete one stored object. Ignores anything that does not look like a path we wrote."""
    if not rel_path or not _SAFE_REL.match(rel_path):
        return False
    try:
        target = (MEDIA_ROOT / rel_path).resolve()
        # Defence in depth: the regex already forbids "..", but a symlinked volume could still
        # resolve outside the root, and this function deletes files.
        if not str(target).startswith(str(MEDIA_ROOT.resolve())):
            log.warning("media.delete refused a path outside the root: %s", rel_path)
            return False
        target.unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001
        log.exception("media.delete failed for %s", rel_path)
        return False


def prune_empty_dirs() -> int:
    """Remove the YYYY/MM directories a purge emptied. Cheap, and keeps the volume readable."""
    removed = 0
    try:
        for month in sorted(MEDIA_ROOT.glob("*/*"), reverse=True):
            if month.is_dir() and not any(month.iterdir()):
                month.rmdir()
                removed += 1
        for year in sorted(MEDIA_ROOT.glob("*")):
            if year.is_dir() and not any(year.iterdir()):
                year.rmdir()
                removed += 1
    except Exception:  # noqa: BLE001
        log.exception("media.prune_empty_dirs failed")
    return removed
