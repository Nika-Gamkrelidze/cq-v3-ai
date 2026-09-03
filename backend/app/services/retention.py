"""Purge expired anonymous submissions — the other half of storing them.

Retention that is only ever written and never enforced is not retention, it is an
indefinite archive with a misleading column name. This module is what makes the 30-day
promise in the admin panel true.

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


async def purge_expired() -> dict:
    """Delete stored media (and clear the pointers) for everything past its deadline."""
    audio_files = audio_rows = tts_files = tts_rows = 0

    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, audio_path FROM audio_jobs
            WHERE purge_after IS NOT NULL AND purge_after <= now()
            ORDER BY purge_after LIMIT $1
            """, _BATCH)
        for r in rows:
            if media.delete(r["audio_path"]):
                audio_files += 1
            # The transcript and the IP are personal data too — the row survives (it is the
            # usage record the quota and any abuse report are built on) but is stripped of
            # everything that identifies the person or reproduces their content.
            await conn.execute(
                """
                UPDATE audio_jobs
                   SET audio_path = NULL, audio_sha256 = NULL, client_ip = NULL,
                       anon_key = NULL, transcript = NULL, purge_after = NULL
                 WHERE id = $1
                """, r["id"])
            audio_rows += 1

        trows = await conn.fetch(
            """
            SELECT id, audio_path FROM tts_requests
            WHERE purge_after IS NOT NULL AND purge_after <= now()
            ORDER BY purge_after LIMIT $1
            """, _BATCH)
        for r in trows:
            if media.delete(r["audio_path"]):
                tts_files += 1
            await conn.execute(
                """
                UPDATE tts_requests
                   SET audio_path = NULL, client_ip = NULL, anon_key = NULL,
                       text = '', purge_after = NULL
                 WHERE id = $1
                """, r["id"])
            tts_rows += 1

    if audio_rows or tts_rows:
        media.prune_empty_dirs()
        log.info("retention purge: audio rows=%s files=%s | tts rows=%s files=%s",
                 audio_rows, audio_files, tts_rows, tts_files)
    return {"audio_rows": audio_rows, "audio_files": audio_files,
            "tts_rows": tts_rows, "tts_files": tts_files}
