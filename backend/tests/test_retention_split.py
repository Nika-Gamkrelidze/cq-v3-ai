"""`services/retention.py::purge_expired` after the storage split (design-v2 §9): every
stored recording and TTS clip has ONE deadline, but what the deadline costs a row depends on
who submitted it.

  * anonymous  → stripped: file, IP, anon key AND the transcript/text (nobody consented to
                 the content being kept; the row survives as the usage record);
  * everyone else (tenant, registered user, and anything not KNOWN to be anonymous) → the
                 file only: `audio_path`, `audio_sha256`, `purge_after` go, the transcript,
                 timeline and every analyser result stay for History to replay in text mode.

The rows are inserted straight into the two tables with a fake stored file each — the purge
reads columns, not the code that wrote them — and the purge runs on a pool bound to the test's
own loop (`test_autopilot._with_db` explains why). `MEDIA_ROOT` is a temp dir, so the files
deleted are the test's own. Every row is deleted by id afterwards; the throwaway tenant goes
with them.

Skips without a database, like every integration test here.
"""
import asyncio
import datetime as dt
import json
import uuid

import pytest

from app.services import media, retention
from conftest import sql  # loop-independent SQL; see its module docstring

PAST = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
FAR_FUTURE = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
SEGMENTS = [{"i": 0, "speaker": "speaker_0", "start": 0.0, "end": 1.5, "text": "hello"}]
RESULT = {"kept": True}


def _with_db(coro_factory):
    """Run `coro_factory()` with an asyncpg pool bound to THIS loop, restoring the app's
    pool afterwards (it lives in the TestClient portal thread and cannot be awaited here)."""
    from app import db

    async def _run():
        prev = db._pool
        await db.connect()
        try:
            return await coro_factory()
        finally:
            await db.disconnect()
            db._pool = prev
    return asyncio.run(_run())


def _purge() -> dict:
    return _with_db(retention.purge_expired)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def media_root(tmp_path, monkeypatch):
    """`media.delete` / `prune_empty_dirs` read the module global on every call, so the
    redirect is what the purge sees."""
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(media, "MEDIA_ROOT", root)
    return root


@pytest.fixture
def owners(api):
    """A throwaway tenant and a registered-user id (the purge never joins `app_users`, so a
    bare uuid is the honest fixture), plus the id lists the inserters fill for teardown."""
    cid = sql(lambda c: c.fetchval(
        "INSERT INTO clients (slug, name) VALUES ($1, $2) RETURNING id",
        f"rettest-{uuid.uuid4().hex[:8]}", "retention split test"))
    data = {"client_id": cid, "user_id": uuid.uuid4(), "audio": [], "tts": []}
    yield data

    async def _cleanup(conn):
        await conn.execute("DELETE FROM audio_jobs WHERE id = ANY($1::uuid[])", data["audio"])
        await conn.execute("DELETE FROM tts_requests WHERE id = ANY($1::uuid[])", data["tts"])
        await conn.execute("DELETE FROM clients WHERE id = $1", cid)
    sql(_cleanup)


def _stored(root, *, with_file: bool = True) -> str:
    """A relative path shaped the way `media.save` writes them (the purge's `_SAFE_REL`
    refuses anything else), with the bytes on disk unless the test wants an orphan row."""
    now = dt.datetime.now(dt.timezone.utc)
    rel = f"{now:%Y}/{now:%m}/{uuid.uuid4()}.wav"
    if with_file:
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"RIFF-not-really-audio")
    return rel


def _owner_cols(owners, kind: str | None) -> dict:
    """What each principal kind writes into the owner columns."""
    return {
        "principal_type": kind,
        "client_id": owners["client_id"] if kind == "tenant" else None,
        "user_id": owners["user_id"] if kind == "user" else None,
        "anon_key": "203.0.113.9" if kind == "anonymous" else None,
    }


def _audio_row(owners, root, *, kind, purge_after=PAST, with_file=True) -> uuid.UUID:
    cols = _owner_cols(owners, kind)
    rel = _stored(root, with_file=with_file)

    async def _insert(conn):
        return await conn.fetchval(
            """
            INSERT INTO audio_jobs
                (filename, status, source, principal_type, client_id, user_id, anon_key,
                 client_ip, transcript, segments, kb_check, scoring, semantic, analysis,
                 audio_path, audio_bytes, audio_sha256, purge_after)
            VALUES ($1, 'done', 'audio', $2, $3, $4, $5, '203.0.113.9', 'the transcript',
                    $6::jsonb, $7::jsonb, $7::jsonb, $7::jsonb, $7::jsonb, $8, 21, 'sha', $9)
            RETURNING id
            """, f"{kind or 'unknown'}.wav", cols["principal_type"], cols["client_id"],
            cols["user_id"], cols["anon_key"], json.dumps(SEGMENTS), json.dumps(RESULT),
            rel, purge_after)
    job_id = sql(_insert)
    owners["audio"].append(job_id)
    return job_id


def _tts_row(owners, root, *, kind, purge_after=PAST) -> uuid.UUID:
    cols = _owner_cols(owners, kind)
    rel = _stored(root)

    async def _insert(conn):
        return await conn.fetchval(
            """
            INSERT INTO tts_requests
                (principal_type, client_id, user_id, anon_key, client_ip, text, text_chars,
                 language_code, voice_id, audio_path, audio_bytes, purge_after)
            VALUES ($1, $2, $3, $4, '203.0.113.9', 'spoken text', 11, 'en', 'v1', $5, 21, $6)
            RETURNING id
            """, cols["principal_type"], cols["client_id"], cols["user_id"], cols["anon_key"],
            rel, purge_after)
    tts_id = sql(_insert)
    owners["tts"].append(tts_id)
    return tts_id


def _audio(job_id: uuid.UUID) -> dict:
    row = sql(lambda c: c.fetchrow(
        "SELECT audio_path, audio_sha256, purge_after, client_ip, anon_key, transcript, "
        "segments, kb_check, scoring, semantic, analysis, status FROM audio_jobs WHERE id = $1",
        job_id))
    return dict(row)


def _tts(tts_id: uuid.UUID) -> dict:
    row = sql(lambda c: c.fetchrow(
        "SELECT audio_path, purge_after, client_ip, anon_key, text, language_code, voice_id "
        "FROM tts_requests WHERE id = $1", tts_id))
    return dict(row)


def _files(root) -> list:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _assert_file_only(row: dict) -> None:
    """The signed-in outcome: pointers gone, everything the customer owns still there."""
    assert row["audio_path"] is None and row["audio_sha256"] is None and row["purge_after"] is None
    assert row["transcript"] == "the transcript"
    assert json.loads(row["segments"]) == SEGMENTS
    for col in ("kb_check", "scoring", "semantic", "analysis"):
        assert json.loads(row[col]) == RESULT, col
    assert row["client_ip"] == "203.0.113.9"
    assert row["status"] == "done"


# ---------------------------------------------------------------------------
# The split, across both tables in one pass
# ---------------------------------------------------------------------------
def test_anonymous_rows_are_stripped_and_signed_in_rows_keep_their_content(owners, media_root):
    anon = _audio_row(owners, media_root, kind="anonymous")
    tenant = _audio_row(owners, media_root, kind="tenant")
    user = _audio_row(owners, media_root, kind="user")
    unknown = _audio_row(owners, media_root, kind=None)          # nobody said who owns it
    admin = _audio_row(owners, media_root, kind="superadmin")
    not_yet = _audio_row(owners, media_root, kind="tenant", purge_after=FAR_FUTURE)
    forever = _audio_row(owners, media_root, kind="tenant", purge_after=None)
    anon_tts = _tts_row(owners, media_root, kind="anonymous")
    tenant_tts = _tts_row(owners, media_root, kind="tenant")
    user_tts = _tts_row(owners, media_root, kind="user")
    assert len(_files(media_root)) == 10

    out = _purge()

    assert out == {"audio_rows": 5, "audio_files": 5, "audio_stripped": 1,
                   "tts_rows": 3, "tts_files": 3, "tts_stripped": 1}

    # Anonymous: file, sha, IP, key, transcript and deadline all gone; the row remains.
    row = _audio(anon)
    for col in ("audio_path", "audio_sha256", "purge_after", "client_ip", "anon_key", "transcript"):
        assert row[col] is None, col
    assert row["status"] == "done"

    # Tenant, user, and anything not KNOWN to be anonymous: the file only.
    for job_id in (tenant, user, unknown, admin):
        _assert_file_only(_audio(job_id))

    # A future deadline and no deadline at all: untouched, files still there.
    for job_id in (not_yet, forever):
        row = _audio(job_id)
        assert row["audio_path"] and (media_root / row["audio_path"]).is_file()
        assert row["audio_sha256"] == "sha" and row["transcript"] == "the transcript"
    assert _audio(not_yet)["purge_after"] == FAR_FUTURE
    assert _audio(forever)["purge_after"] is None

    # tts_requests: the same split. Anonymous loses the text too; signed-in keeps it.
    row = _tts(anon_tts)
    assert row["audio_path"] is None and row["purge_after"] is None
    assert row["client_ip"] is None and row["anon_key"] is None
    assert row["text"] == ""
    assert row["language_code"] == "en" and row["voice_id"] == "v1"    # the request itself stays
    for tts_id in (tenant_tts, user_tts):
        row = _tts(tts_id)
        assert row["audio_path"] is None and row["purge_after"] is None
        assert row["text"] == "spoken text" and row["client_ip"] == "203.0.113.9"

    # Exactly the two files with a live deadline survive.
    survivors = {str(p.relative_to(media_root)) for p in _files(media_root)}
    assert survivors == {_audio(not_yet)["audio_path"], _audio(forever)["audio_path"]}

    # A second pass finds nothing: the deadlines were cleared, so nothing is expired twice.
    assert _purge() == {"audio_rows": 0, "audio_files": 0, "audio_stripped": 0,
                        "tts_rows": 0, "tts_files": 0, "tts_stripped": 0}


def test_the_file_goes_before_the_row_and_a_missing_file_self_heals(owners, media_root):
    """Deletion order is file first, then row: a row whose file is already gone (a crash
    between the two on an earlier pass) is cleared on the next one rather than erroring."""
    orphan = _audio_row(owners, media_root, kind="tenant", with_file=False)
    assert not (media_root / _audio(orphan)["audio_path"]).exists()
    out = _purge()
    assert out["audio_rows"] == 1
    _assert_file_only(_audio(orphan))


def test_the_purge_leaves_no_empty_month_directories_behind(owners, media_root):
    anon = _audio_row(owners, media_root, kind="anonymous")
    month_dir = (media_root / _audio(anon)["audio_path"]).parent
    assert month_dir.is_dir()
    _purge()
    assert not month_dir.exists()
    assert not any(media_root.iterdir())


def test_a_row_is_only_stripped_when_it_is_known_to_be_anonymous(owners, media_root):
    """Pinned separately because it is a decision, not an accident: a NULL owner could be a
    customer's row written by code that forgot to say so, and erasing a transcript on a guess
    is the one mistake a retention purge must never make."""
    unknown = _audio_row(owners, media_root, kind=None)
    assert _purge()["audio_stripped"] == 0
    row = _audio(unknown)
    assert row["transcript"] == "the transcript" and row["audio_path"] is None


def test_anonymous_strip_also_clears_the_timeline_copy_of_the_transcript(owners, media_root):
    """`segments` is the transcript a second time (one entry per speaker turn, verbatim), so
    the anonymous strip has to take it too — clearing `transcript` alone would leave the
    content nobody consented to keeping sitting in the next column. `duration_s` goes with it
    for the same reason the audio does: it describes a recording that no longer exists."""
    anon = _audio_row(owners, media_root, kind="anonymous")
    _purge()
    row = _audio(anon)
    assert row["segments"] is None and row["transcript"] is None
