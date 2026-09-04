"""Purge expired stored media — the other half of storing it.

Retention that is only ever written and never enforced is not retention, it is an
indefinite archive with a misleading column name. This module is what makes the number of
days in the admin panel's Storage setting true.

What "expired" costs a row depends on who submitted it, and the split is deliberate:

  * **Anonymous** rows are stripped: file, IP, anon key AND the transcript/text go. There is
    no account behind them, so the content is personal data with nobody left to consent to
    keeping it; the row itself survives as the usage record the quota and any abuse report
    are built on. "The text" means EVERY verbatim copy of it: `segments` holds the same words
    again, one entry per speaker turn, so clearing `transcript` alone would leave the whole
    call readable on the row forever (the purge also NULLs `purge_after`, so no later pass
    would ever come back for it).
  * **Signed-in** rows (tenant, registered user — anything not anonymous) lose ONLY the file.
    The transcript, segments and every analyser result stay: they are the customer's own
    history, and the deadline exists to bound disk, not to forget the call. History keeps
    replaying such a recording in text mode with a "no longer stored" note.

Deletion order matters and is deliberate: **file first, then row**. If the process dies
between the two, the next pass sees a row whose file is already gone, calls `unlink` with
`missing_ok=True`, and clears the row — self-healing. The reverse order would drop the row
first and leave the bytes on the volume with nothing left pointing at them, which is exactly
the orphan a retention policy exists to prevent.
"""
from __future__ import annotations

import logging

from ..db import pool
from . import media

log = logging.getLogger("cq")

# Bounded so one pass cannot hold the pool or the volume for minutes; the duty simply runs
# again on its next tick and drains the rest.
_BATCH = 500

# Only rows KNOWN to be anonymous are stripped. A NULL principal_type would mean a row some
# other code wrote without saying who owns it — content that might be a customer's, and
# that is not something to erase on a guess.
_ANON = "anonymous"

_EXPIRED_AUDIO = """
    SELECT id, audio_path, principal_type FROM audio_jobs
    WHERE purge_after IS NOT NULL AND purge_after <= now()
    ORDER BY purge_after LIMIT $1
"""
# `segments` is the transcript a second time (one entry per turn, verbatim), so it goes with
# it; `duration_s` is derived from it and means nothing once both are gone.
_STRIP_AUDIO = """
    UPDATE audio_jobs
       SET audio_path = NULL, audio_sha256 = NULL, client_ip = NULL,
           anon_key = NULL, transcript = NULL, segments = NULL, duration_s = NULL,
           purge_after = NULL
     WHERE id = $1
"""
_UNLINK_AUDIO = """
    UPDATE audio_jobs
       SET audio_path = NULL, audio_sha256 = NULL, purge_after = NULL
     WHERE id = $1
"""

_EXPIRED_TTS = """
    SELECT id, audio_path, principal_type FROM tts_requests
    WHERE purge_after IS NOT NULL AND purge_after <= now()
    ORDER BY purge_after LIMIT $1
"""
_STRIP_TTS = """
    UPDATE tts_requests
       SET audio_path = NULL, client_ip = NULL, anon_key = NULL,
           text = '', purge_after = NULL
     WHERE id = $1
"""
_UNLINK_TTS = """
    UPDATE tts_requests
       SET audio_path = NULL, purge_after = NULL
     WHERE id = $1
"""


async def _purge(conn, *, select_sql: str, strip_sql: str, unlink_sql: str) -> tuple[int, int, int]:
    """One table's pass: (rows cleared, files deleted, rows stripped)."""
    rows = files = stripped = 0
    for r in await conn.fetch(select_sql, _BATCH):
        if media.delete(r["audio_path"]):
            files += 1
        anonymous = r["principal_type"] == _ANON
        await conn.execute(strip_sql if anonymous else unlink_sql, r["id"])
        rows += 1
        stripped += anonymous
    return rows, files, stripped


async def purge_expired() -> dict:
    """Delete stored media (and clear the pointers) for everything past its deadline."""
    async with pool().acquire() as conn:
        audio_rows, audio_files, audio_stripped = await _purge(
            conn, select_sql=_EXPIRED_AUDIO, strip_sql=_STRIP_AUDIO, unlink_sql=_UNLINK_AUDIO)
        tts_rows, tts_files, tts_stripped = await _purge(
            conn, select_sql=_EXPIRED_TTS, strip_sql=_STRIP_TTS, unlink_sql=_UNLINK_TTS)

    if audio_rows or tts_rows:
        media.prune_empty_dirs()
        log.info("retention purge: audio rows=%s files=%s stripped=%s | "
                 "tts rows=%s files=%s stripped=%s",
                 audio_rows, audio_files, audio_stripped, tts_rows, tts_files, tts_stripped)
    return {"audio_rows": audio_rows, "audio_files": audio_files,
            "audio_stripped": audio_stripped,
            "tts_rows": tts_rows, "tts_files": tts_files, "tts_stripped": tts_stripped}
