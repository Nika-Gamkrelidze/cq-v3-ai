"""The Asterisk converter, held to what its catalog promises.

A wrong `-ar` in `audio_convert.FORMATS` does not raise, does not log, and does not fail
review. It ships, and then a prompt plays back at half speed on a live call — chipmunked or
dragging, discovered by a customer, hours from anything that points at the cause. Nothing in
the Python can catch that, because the Python is correct: it faithfully passed the wrong
number to ffmpeg. So the centrepiece here is a matrix that converts a real file for EVERY row
of the catalog and measures what came out.

How the measurement works differs by format, and the difference is not incidental:

  * `wav`/`wav16` carry a RIFF header, so ffprobe reads the true rate, channel count and codec
    back out of the file. That is a direct assertion.
  * The other six are HEADERLESS. Their demuxers assume the standard rate (8 kHz, or 16 kHz
    for G.722) no matter what is actually in the file, so asking ffprobe about them just
    replays the assumption — the answer is always "correct", which is worse than no answer.
    They are measured by SIZE instead, against bytes-per-sample restated independently from
    the codec definitions in `_RAW_BYTES_PER_SAMPLE`. A file encoded at 16 kHz when the
    catalog says 8 kHz is twice as long as it should be, and a stereo leak is likewise 2x, so
    the byte count is the rate check AND the mono check for exactly the formats where ffprobe
    cannot be one.

`_RAW_BYTES_PER_SAMPLE` is deliberately a second, hand-written statement of the same physics
rather than anything derived from `FORMATS`. A table that reads its expectations out of the
code under test agrees with every bug that code has.

The source file for the matrix is stereo 44.1 kHz — the shape a user actually uploads — so
"mono and fixed-rate are enforced" is proved on every format at once rather than assumed.

No network and no API keys: every input is synthesised by ffmpeg itself from `lavfi` sources
(there are no binary fixtures in this repo, and a committed .mp4 is a thing nobody can review).
The pure-Python half — filenames, dedupe, the ZIP, the catalog — runs anywhere; everything
that shells out to ffmpeg skips without it, and everything that goes over HTTP inherits
`conftest.py`'s skip-without-a-database rule.
"""
import asyncio
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import pytest

from app.services import audio_convert, settings_store
from app.services.audio_convert import FORMATS, ConvertError
from conftest import chat_route_present, sql  # loop-independent SQL; see its module docstring

# ---------------------------------------------------------------------------
# ffmpeg is present in the api image (that is where this suite runs) but not on a
# developer's laptop. Missing tooling must skip, never fail — same rule conftest.py
# applies to a missing database.
# ---------------------------------------------------------------------------
_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(
    not _HAVE_FFMPEG, reason="ffmpeg/ffprobe are not on PATH (they are in the api image)")

# One second of audio. Short enough that eight conversions cost nothing, long enough that a
# rate error is hundreds of bytes rather than rounding.
DURATION_S = 1.0
SRC_RATE = 44100
SRC_CHANNELS = 2

# Bytes each codec spends per SAMPLE, restated from the codec definitions and NOT read out of
# `FORMATS`. Only the headerless formats appear here — see the module docstring.
_RAW_BYTES_PER_SAMPLE = {
    "alaw": 1.0,          # G.711: one companded byte per sample
    "ulaw": 1.0,          # ditto, the other companding law
    "sln": 2.0,           # signed 16-bit linear
    "sln16": 2.0,
    "g722": 0.5,          # 4 bits per sample — 64 kbit/s at 16 kHz
    "gsm": 33.0 / 160.0,  # one 33-byte frame per 160-sample (20 ms) block
}
# The formats whose container lets ffprobe answer honestly.
_PROBEABLE = {"wav": "pcm_s16le", "wav16": "pcm_s16le"}


# ---------------------------------------------------------------------------
# ffmpeg helpers (synchronous — these build fixtures, they are not the code under test)
# ---------------------------------------------------------------------------
def _generate(suffix: str, *args: str) -> bytes:
    """Synthesise a fixture with ffmpeg. `args` are everything before the output path."""
    if not _HAVE_FFMPEG:
        pytest.skip("ffmpeg is not on PATH")
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        proc = subprocess.run(["ffmpeg", "-nostdin", "-y", *args, path],
                              capture_output=True, timeout=120)
        if proc.returncode != 0 or not os.path.getsize(path):
            pytest.skip("this ffmpeg build cannot synthesise the fixture: "
                        + proc.stderr.decode(errors="replace")[-300:])
        return Path(path).read_bytes()
    finally:
        os.remove(path)


def _probe(data: bytes, suffix: str, stream: str = "a") -> dict:
    """ffprobe one stream of `data`. Returns {} when there is no such stream."""
    if not _HAVE_FFMPEG:
        pytest.skip("ffprobe is not on PATH")
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        Path(path).write_bytes(data)
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", f"{stream}:0", "-of", "json",
             "-show_entries", "stream=codec_name,sample_rate,channels:format=duration", path],
            capture_output=True, timeout=60)
        assert proc.returncode == 0, proc.stderr.decode(errors="replace")[-400:]
        parsed = json.loads(proc.stdout or b"{}")
        streams = parsed.get("streams") or []
        if not streams:
            return {}
        out = dict(streams[0])
        out["duration"] = float((parsed.get("format") or {}).get("duration") or 0.0)
        return out
    finally:
        os.remove(path)


def _within(actual: float, expected: float, *, pct: float = 0.06, floor: float = 96.0) -> bool:
    """Close enough. The slack absorbs a resampler's few-sample delay and a codec's final
    partial frame; it is nowhere near the 2x a wrong sample rate or a stereo leak costs."""
    return abs(actual - expected) <= max(expected * pct, floor)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def convert_root(tmp_path, monkeypatch):
    """Batches land in a per-test temp directory, never on the real `media` volume.

    `audio_convert.batch_dir()` reads the module global on every call, so this redirect is
    seen by the app thread too — monkeypatch is process-global; it is only the event loop
    that is per-thread (see conftest.py).
    """
    root = tmp_path / "convert"
    monkeypatch.setattr(audio_convert, "CONVERT_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def fresh_ffmpeg_slots(monkeypatch):
    """A per-test copy of `audio_convert._SLOTS`.

    That semaphore is a module global built at import, and in production it lives its whole
    life on one event loop. Here every async test gets a fresh loop, and a 3.10+ Semaphore
    binds to the first loop it ever has to WAIT on. Today's tests convert one file at a time
    so it never binds — but the first test that exercises concurrency would fail with "bound
    to a different event loop", which reads as a bug in the code under test and is not one.
    """
    monkeypatch.setattr(audio_convert, "_SLOTS", asyncio.Semaphore(2))


@pytest.fixture(autouse=True)
def anon_config(monkeypatch):
    """A known anonymous policy: conversion on, and a cap high enough not to bite.

    Pinned rather than inherited from `app_settings`, because the developer's database may
    have anonymous access disabled — and a quota test that passes because the feature was off
    is not a quota test. Mutate `["max_conversions_per_day"]` to make the cap bite.
    """
    cfg = {"enabled": True, "max_audio_mb": 0, "max_conversions_per_day": 1000,
           "features": {"convert": True, "analyze": True, "tts": True}}

    async def _get_anonymous_config():
        return dict(cfg)

    monkeypatch.setattr(settings_store, "get_anonymous_config", _get_anonymous_config)
    return cfg


@pytest.fixture(scope="session")
def stereo_wav() -> bytes:
    """What people upload: stereo, 44.1 kHz. Every catalog row must flatten it."""
    return _generate(".wav", "-f", "lavfi",
                     "-i", f"sine=frequency=440:sample_rate={SRC_RATE}:duration={DURATION_S}",
                     "-ac", str(SRC_CHANNELS), "-c:a", "pcm_s16le")


@pytest.fixture(scope="session")
def video_mp4() -> bytes:
    """A video with an audio track — the case the converter exists to handle politely."""
    return _generate(
        ".mp4",
        "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=15:duration={DURATION_S}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={SRC_RATE}:duration={DURATION_S}",
        "-c:v", "mpeg4", "-c:a", "aac", "-shortest")


@pytest.fixture(scope="session")
def silent_video_mp4() -> bytes:
    """Video with NO audio track — the friendly-message case."""
    return _generate(".mp4", "-f", "lavfi",
                     "-i", f"testsrc=size=160x120:rate=15:duration={DURATION_S}",
                     "-c:v", "mpeg4")


GARBAGE = b"# these are notes, not a recording\nline two\n" * 40


@pytest.fixture(scope="session")
def anon_ip(api):
    """Mint a fresh anonymous quota key per test, and clean the rows up afterwards.

    The key is whatever `X-Real-IP` says — that is the trusted source `services/auth` reads,
    so this is the supported way to be a different visitor, not a bypass. Fresh per test
    because `anon_usage` is keyed per day: a re-run an hour later must not inherit the
    counters of the last one.
    """
    minted: list[str] = []

    def _mint() -> str:
        key = f"convtest-{uuid.uuid4().hex[:12]}"
        minted.append(key)
        return key

    yield _mint
    if minted:
        # Both tables a converted batch writes: the quota counter AND the history row
        # `_record_batch` inserts for every kind. Nothing else ever collects the latter
        # (no purge, no sweep), so leaving them accumulates test residue in a shared dev DB —
        # visible to a superadmin in GET /convert/history.
        sql(lambda c: c.execute(
            "DELETE FROM anon_usage WHERE anon_key = ANY($1::text[])", minted))
        sql(lambda c: c.execute(
            "DELETE FROM convert_batches WHERE anon_key = ANY($1::text[])", minted))


@pytest.fixture
def convert_api(api):
    if not chat_route_present(api, "/convert"):
        pytest.skip("/convert is not mounted (routers/convert.py not landed)")
    return api


def _multipart(uploads, fmt="wav"):
    """Hand-built multipart body, for the names an HTTP client library will not send.

    `httpx` sanitises a file name before it writes the Content-Disposition header, which is
    the correct thing for a client to do and useless for testing what happens when a caller
    does not."""
    boundary = "----cqtest"
    out = b""
    for name, data in uploads:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
                f"filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        out += data + b"\r\n"
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"format\"\r\n\r\n"
            f"{fmt}\r\n--{boundary}--\r\n").encode()
    return out, boundary


def _post(client, uploads, fmt="wav", ip=None):
    """POST a batch. `uploads` is [(filename, bytes), ...]."""
    headers = {"X-Real-IP": ip} if ip else {}
    return client.post(
        "/convert",
        files=[("files", (name, data, "application/octet-stream")) for name, data in uploads],
        data={"format": fmt}, headers=headers)


# ---------------------------------------------------------------------------
# 1. The catalog is what ffmpeg actually produces
# ---------------------------------------------------------------------------
def test_this_file_and_the_catalog_cover_the_same_formats():
    """A new row in FORMATS must arrive with a way to verify it.

    Without this, adding a ninth format would slip past the matrix below silently: the
    parametrisation would run it, find no expectation, and have nothing to assert.
    """
    assert set(FORMATS) == set(_PROBEABLE) | set(_RAW_BYTES_PER_SAMPLE)


@needs_ffmpeg
def test_the_source_fixture_really_is_stereo_44100(stereo_wav):
    """Guards the matrix from being vacuous: if the source were already mono 8 kHz, every
    'downmixed to mono at the right rate' assertion below would pass without converting."""
    info = _probe(stereo_wav, ".wav")
    assert int(info["channels"]) == SRC_CHANNELS
    assert int(info["sample_rate"]) == SRC_RATE


@needs_ffmpeg
@pytest.mark.parametrize("fmt", list(FORMATS))
async def test_every_format_matches_what_the_catalog_promises(fmt, stereo_wav):
    """The rate, channel count and codec of the real output, per catalog row.

    This is the test that catches a wrong `-ar` — the bug whose only other symptom is audio
    playing at the wrong speed on a live call, days later.
    """
    spec = FORMATS[fmt]
    out, name = await audio_convert.convert(stereo_wav, "customer call.wav", fmt)

    assert out, "conversion produced no bytes"
    # Asterisk picks the codec off the extension, so the extension is part of the format.
    assert name.endswith(spec["ext"])
    assert name == f"customer call{spec['ext']}"

    if fmt in _PROBEABLE:
        info = _probe(out, ".wav")
        assert info, "the output has no readable audio stream"
        assert info["codec_name"] == _PROBEABLE[fmt]
        assert int(info["sample_rate"]) == spec["rate"], (
            f"{fmt}: catalog says {spec['rate']} Hz, the file is {info['sample_rate']} Hz")
        assert int(info["channels"]) == 1, f"{fmt}: telephony formats are mono"
        assert _within(info["duration"], DURATION_S, pct=0.06, floor=0.05)
    else:
        # Headerless: size is the only honest measurement. See the module docstring.
        expected = spec["rate"] * _RAW_BYTES_PER_SAMPLE[fmt] * DURATION_S
        assert _within(len(out), expected), (
            f"{fmt}: {len(out)} bytes for {DURATION_S}s, expected ~{expected:.0f} at "
            f"{spec['rate']} Hz mono — the sample rate or the channel count is wrong")


@needs_ffmpeg
async def test_stereo_44k_is_downmixed_and_resampled_not_passed_through(stereo_wav):
    """Named separately from the matrix because it is the promise, not a detail: whatever the
    user uploads, an Asterisk format comes out mono at its fixed rate."""
    out, _ = await audio_convert.convert(stereo_wav, "stereo.wav", "wav")
    info = _probe(out, ".wav")
    assert int(info["channels"]) == 1 and int(info["sample_rate"]) == 8000
    assert len(out) < len(stereo_wav), "8 kHz mono cannot be larger than 44.1 kHz stereo"


@needs_ffmpeg
async def test_the_catalog_rate_is_not_the_input_rate(stereo_wav):
    """The two wideband formats must resample UP from nothing and DOWN from 44.1 kHz — i.e.
    the rate comes from the catalog, never from the source."""
    narrow, _ = await audio_convert.convert(stereo_wav, "a.wav", "wav")
    wide, _ = await audio_convert.convert(stereo_wav, "a.wav", "wav16")
    assert int(_probe(narrow, ".wav")["sample_rate"]) == 8000
    assert int(_probe(wide, ".wav")["sample_rate"]) == 16000


# ---------------------------------------------------------------------------
# 2. Video in, audio out
# ---------------------------------------------------------------------------
@needs_ffmpeg
async def test_video_input_yields_audio_only(video_mp4):
    """A screen recording of a call is a normal upload. The video track is dropped (`-vn`),
    and what lands on the PBX is the audio at the format's rate."""
    out, name = await audio_convert.convert(video_mp4, "zoom recording.mp4", "wav")
    assert name == "zoom recording.wav"
    audio = _probe(out, ".wav")
    assert int(audio["channels"]) == 1 and int(audio["sample_rate"]) == 8000
    assert _probe(out, ".wav", stream="v") == {}, "the video stream survived into the output"


@needs_ffmpeg
async def test_video_with_no_audio_track_says_so(silent_video_mp4):
    """The most common self-inflicted failure gets a sentence the uploader can act on, not a
    codec dump."""
    with pytest.raises(ConvertError) as err:
        await audio_convert.convert(silent_video_mp4, "slides.mp4", "alaw")
    assert "no audio track" in str(err.value).lower()


# ---------------------------------------------------------------------------
# 3. Failure policy — the deliberate difference from audio.to_stt_format
# ---------------------------------------------------------------------------
@needs_ffmpeg
async def test_a_failed_conversion_raises_and_never_returns_the_original_bytes():
    """DO NOT "simplify" this into a fallback that returns the input, the way
    `audio.to_stt_format` does. That is right there and wrong here.

    `to_stt_format` normalises INPUT for a speech model, so handing back the original is a
    second chance. This produces a DELIVERABLE: a file that is quietly passed through
    unconverted is an .mp3 with a .alaw name, which loads into a dialplan and fails at CALL
    time — hours later, on a customer's line, with nothing pointing back to here.
    """
    try:
        out, name = await audio_convert.convert(GARBAGE, "meeting notes.txt", "alaw")
    except ConvertError as exc:
        message = str(exc)
    else:
        pytest.fail(
            "convert() returned instead of raising on an unconvertible file "
            f"({len(out)} bytes named {name!r}; identical to the input: {out == GARBAGE}). "
            "A silent fallback to the original bytes ships an unconverted file to a PBX — "
            "see this test's docstring before changing the policy.")

    # Actionable means a sentence, not ffmpeg's build banner.
    assert message and len(message) < 400
    assert "ffmpeg version" not in message
    assert "--enable-" not in message


async def test_an_empty_upload_and_an_unknown_format_are_named_errors():
    """Both are refused before ffmpeg is even looked for, so this runs anywhere."""
    with pytest.raises(ConvertError, match="empty"):
        await audio_convert.convert(b"", "silence.wav", "wav")
    with pytest.raises(ConvertError, match="Unknown target format"):
        await audio_convert.convert(b"abc", "a.wav", "mp3")


# ---------------------------------------------------------------------------
# 4. Filenames: recognisable, but never a path
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("given,fmt,expected", [
    ("../../etc/passwd", "alaw", "passwd.alaw"),          # traversal is just a stem
    ("/etc/shadow", "wav", "shadow.wav"),                 # absolute path
    ("..", "gsm", "audio.gsm"),                           # nothing left -> a default
    (".", "wav", "audio.wav"),
    (".hidden", "wav", "hidden.wav"),                     # no dotfile in someone's unzip
    ("C:\\Users\\ana\\call.mp3", "wav", "call.wav"),      # Windows path separators
    ("call\x00.mp3", "wav", "call.wav"),                  # NUL
    ("a\r\nb\tc.mp3", "ulaw", "abc.ulaw"),                # control characters
    ('bad<>:"|?*name.mp3', "wav", "badname.wav"),         # reserved on Windows/macOS
    ("-f lavfi", "alaw", "f lavfi.alaw"),                 # not an option to the user's own shell
    ("--help.mp3", "wav", "help.wav"),
    ("-", "gsm", "audio.gsm"),
    ("- . -x.mp3", "wav", "x.wav"),                       # trimmed to a fixed point
    ("ზარი 2026-09-01.mp3", "alaw", "ზარი 2026-09-01.alaw"),  # Georgian names are the norm
    ("call.wav", "alaw", "call.alaw"),                    # the extension is the format's
    (None, "wav", "audio.wav"),
    ("", "sln", "audio.sln"),
    ("   ", "sln16", "audio.sln16"),
])
def test_filenames_are_made_local_and_harmless(given, fmt, expected):
    assert audio_convert.safe_output_name(given, fmt) == expected


def test_a_very_long_name_is_capped_but_still_recognisable():
    out = audio_convert.safe_output_name("ბ" * 400 + ".mp3", "g722")
    assert out.endswith(".g722")
    assert len(out) - len(".g722") == 80
    assert out.startswith("ბბბ")   # still tells the user which file this was


@pytest.mark.parametrize("given", [
    "../../etc/passwd", "/etc/shadow", "C:\\Users\\ana\\call.mp3", "..", "....//....//x",
    "a/b/c.mp3", "\x00\x01\x02", "  ../  ", "ზარი/../../etc/hosts",
    "-f lavfi", "--help.mp3", "-rf /", "  --version  ",
])
def test_no_sanitised_name_can_escape_a_directory_or_a_command_line(given, tmp_path):
    """The property, stated once over every nasty input: the result is a single path
    component that lands inside the directory it is joined to — and never opens with a dash.

    The dash is not about this server (a user's filename is never an ffmpeg argument here; it
    is a label). It is about the box this ZIP is extracted onto, where `ffmpeg -i *` and
    `sox * out.wav` are how this audience actually uses the files, and a member called
    `--help.alaw` is an OPTION to every one of those commands."""
    name = audio_convert.safe_output_name(given, "wav")
    assert name == os.path.basename(name)
    assert not name.startswith(".") and "/" not in name and "\\" not in name
    assert not name.startswith("-")
    assert (tmp_path / name).resolve().parent == tmp_path.resolve()


def test_duplicate_names_in_one_batch_are_made_unique():
    """Two uploads called `call.mp3` produce one output name, and a ZIP with two identical
    entries loses one on extraction — the exact failure a bulk converter exists to avoid."""
    taken: set[str] = set()
    names = [audio_convert.dedupe(audio_convert.safe_output_name(n, "wav"), taken)
             for n in ("call.mp3", "call.m4a", "CALL.wav", "other.mp3")]
    assert names == ["call.wav", "call-2.wav", "CALL-3.wav", "other.wav"]
    assert len({n.casefold() for n in names}) == len(names)


# ---------------------------------------------------------------------------
# 5. The ZIP
# ---------------------------------------------------------------------------
def _entry(i: int, name: str, output: str, size: int) -> dict:
    return {"index": i, "name": name, "output": output, "bytes": size, "ok": True, "error": None}


def test_a_batch_writes_a_readable_zip_with_utf8_entry_names(convert_root):
    """Georgian entry names survive the round trip — most of this product's users name their
    files in Georgian, and a ZIP of `audio-1.alaw` … `audio-30.alaw` is useless."""
    batch = audio_convert.Batch(audio_convert.new_token(), "alaw", "anon:1.2.3.4")
    batch.open()
    try:
        asyncio.run(batch.add("ზარი.alaw", b"\x55" * 800))
        asyncio.run(batch.add("second call.alaw", b"\xd5" * 400))
        manifest = batch.close([_entry(0, "ზარი.mp3", "ზარი.alaw", 800),
                                _entry(1, "second call.m4a", "second call.alaw", 400)])
    except Exception:
        batch.discard()
        raise

    zip_path = convert_root / batch.token / "bundle.zip"
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.testzip() is None
        assert zf.namelist() == ["ზარი.alaw", "second call.alaw"]
        assert zf.read("ზარი.alaw") == b"\x55" * 800

    assert manifest["owner"] == "anon:1.2.3.4" and manifest["format"] == "alaw"
    assert manifest["expires_at"] > manifest["created_at"]


def test_locate_refuses_someone_elses_batch_and_an_expired_one(convert_root):
    """`locate()` is the whole authorisation story for bytes on disk. One `None` for
    not-yours, not-there and expired, so the router's 404 cannot tell them apart."""
    batch = audio_convert.Batch(audio_convert.new_token(), "wav", "anon:10.0.0.1")
    batch.open()
    asyncio.run(batch.add("a.wav", b"RIFFxxxx"))
    batch.close([_entry(0, "a.mp3", "a.wav", 8)])

    assert audio_convert.locate(batch.token, "anon:10.0.0.1") is not None
    assert audio_convert.locate(batch.token, "anon:10.0.0.2") is None
    assert audio_convert.locate(batch.token, "tenant:" + str(uuid.uuid4())) is None
    assert audio_convert.locate("../../etc", "anon:10.0.0.1") is None
    assert audio_convert.locate("short", "anon:10.0.0.1") is None
    assert audio_convert.locate(audio_convert.new_token(), "anon:10.0.0.1") is None

    # Age the directory past the TTL: the manifest's own deadline refuses it, and the sweep
    # then collects it on mtime (which is what catches a batch that died before its manifest).
    old = time.time() - audio_convert.TTL_SECONDS - 60
    d = convert_root / batch.token
    manifest_path = d / "manifest.json"
    stale = json.loads(manifest_path.read_text(encoding="utf-8"))
    stale["expires_at"] = "2020-01-01T00:00:00+00:00"
    manifest_path.write_text(json.dumps(stale), encoding="utf-8")
    assert audio_convert.locate(batch.token, "anon:10.0.0.1") is None

    os.utime(d, (old, old))
    assert asyncio.run(audio_convert.sweep_expired()) == 1
    assert not d.exists()


# ---------------------------------------------------------------------------
# 6. Over HTTP: bulk, quota, download scoping
#    (these need the database — conftest's `api` fixture skips without one)
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_one_bad_file_does_not_cost_the_good_ones(convert_api, anon_ip, stereo_wav):
    """The worst outcome this endpoint has is losing twenty-nine good conversions to one
    corrupt upload. A per-file failure is reported per file and the batch continues."""
    r = _post(convert_api,
              [("first.wav", stereo_wav), ("notes.txt", GARBAGE), ("first.wav", stereo_wav)],
              fmt="alaw", ip=anon_ip())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total"] == 3 and body["converted"] == 2 and body["failed"] == 1
    assert [f["ok"] for f in body["files"]] == [True, False, True]
    assert body["files"][1]["error"] and body["files"][1]["output"] is None
    assert body["files"][0]["bytes"] > 0
    # Both good uploads were called first.wav; the second must not overwrite the first.
    assert [f["output"] for f in body["files"] if f["ok"]] == ["first.alaw", "first-2.alaw"]
    assert body["token"] and body["download_path"] == f"/convert/{body['token']}/download"
    assert body["quota_refusal"] is None


@needs_ffmpeg
def test_the_download_is_a_valid_zip_of_exactly_the_converted_files(convert_api, anon_ip,
                                                                   stereo_wav):
    ip = anon_ip()
    r = _post(convert_api, [("call one.wav", stereo_wav), ("bad.txt", GARBAGE),
                            ("call two.wav", stereo_wav)], fmt="gsm", ip=ip)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["converted"] == 2

    got = convert_api.get(body["download_path"], headers={"X-Real-IP": ip})
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith("application/zip")
    assert "no-store" in got.headers.get("cache-control", "")
    assert f"cq-gsm-2-{body['token'][:8]}.zip" in got.headers.get("content-disposition", "")

    with zipfile.ZipFile(io.BytesIO(got.content)) as zf:
        assert zf.testzip() is None
        assert zf.namelist() == ["call one.gsm", "call two.gsm"]     # only the ones that worked
        member = zf.read("call one.gsm")
    assert len(member) == body["files"][0]["bytes"]
    assert _within(len(member), 8000 * _RAW_BYTES_PER_SAMPLE["gsm"] * DURATION_S)


@needs_ffmpeg
def test_a_download_belongs_to_the_visitor_who_made_it(convert_api, anon_ip, stereo_wav):
    """The token is unguessable, but the owner check is the part that survives a token being
    shared, logged or shoulder-surfed. Someone else's token is a 404, identical to a token
    that never existed."""
    mine, theirs = anon_ip(), anon_ip()
    r = _post(convert_api, [("private call.wav", stereo_wav)], fmt="ulaw", ip=mine)
    assert r.status_code == 200, r.text
    path = r.json()["download_path"]

    assert convert_api.get(path, headers={"X-Real-IP": theirs}).status_code == 404
    assert convert_api.get(path, headers={"X-Real-IP": mine}).status_code == 200
    # A token that was never issued, and one that is not even shaped like a token.
    assert convert_api.get(f"/convert/{audio_convert.new_token()}/download",
                           headers={"X-Real-IP": mine}).status_code == 404
    assert convert_api.get("/convert/nope/download",
                           headers={"X-Real-IP": mine}).status_code == 404


@needs_ffmpeg
def test_a_batch_where_nothing_converted_is_a_200_with_reasons_and_no_token(convert_api,
                                                                           anon_ip):
    """`converted: 0` is still an answer to the question that was asked — the caller asked
    about a batch, and 'this one is a text file' is the answer. But there is no download, so
    there must be no token: one that resolves to nothing is worse than none."""
    r = _post(convert_api, [("a.txt", GARBAGE), ("b.txt", GARBAGE)], fmt="wav", ip=anon_ip())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["converted"] == 0 and body["failed"] == 2
    assert body["token"] is None and body["download_path"] is None
    assert all(f["error"] for f in body["files"])


def test_an_unknown_format_is_refused_before_anything_is_read(convert_api, anon_ip):
    r = _post(convert_api, [("a.wav", b"RIFF0000WAVE")], fmt="mp3", ip=anon_ip())
    assert r.status_code == 400
    assert "mp3" in r.json()["detail"]


@needs_ffmpeg
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_format_field_means_the_default_one(convert_api, anon_ip, stereo_wav, blank):
    """An empty field and a whitespace one are the same field.

    FastAPI substitutes the `Form` default for an empty string but not for "   ", so without
    a `strip()` that lands on the default too, the same "I did not choose" answers 200-wav in
    one caller's client library and 400 in another's."""
    r = _post(convert_api, [("a.wav", stereo_wav)], fmt=blank, ip=anon_ip())
    assert r.status_code == 200, r.text
    assert r.json()["format"] == audio_convert.DEFAULT_FORMAT


@needs_ffmpeg
def test_an_exhausted_visitor_is_refused_before_the_upload_is_buffered(convert_api, anon_ip,
                                                                      anon_config, monkeypatch):
    """The order of the gate, asserted as an order rather than as an outcome.

    A 429 proves nothing on its own: it is the same 429 whether it was decided before or
    after this process copied the batch into memory. So `_read_uploads` is replaced with a
    detonator — if the refusal happens after the read, this fails. That is the whole point of
    the meter: it bounds what an unauthenticated visitor can cost the box, and it cannot do
    that from behind the expensive part. (The bytes have already reached the server by then —
    nginx buffers the body — so what this saves is the RAM copy, which is the part that
    multiplies by concurrency.)"""
    from app.routers import convert as convert_router

    anon_config["max_conversions_per_day"] = 1
    ip = anon_ip()
    sql(lambda c: c.execute(
        "INSERT INTO anon_usage (anon_key, day, conversions) VALUES ($1, CURRENT_DATE, 5) "
        "ON CONFLICT (anon_key, day) DO UPDATE SET conversions = 5", ip))

    async def _detonate(files):
        raise AssertionError("the batch was buffered before the quota was checked")

    monkeypatch.setattr(convert_router, "_read_uploads", _detonate)
    r = _post(convert_api, [("a.wav", b"RIFF0000WAVE" * 1000)], fmt="wav", ip=ip)
    assert r.status_code == 429
    assert "limit" in r.json()["detail"].lower()
    # And the refusal itself spent nothing — it is a read, not a reservation.
    assert sql(lambda c: c.fetchval(
        "SELECT conversions FROM anon_usage WHERE anon_key = $1 AND day = CURRENT_DATE",
        ip)) == 5


def test_a_file_name_with_a_line_break_says_so(convert_api, anon_ip):
    """A raw CR/LF in a file name makes the multipart headers ambiguous, so the parser
    abandons the body upstream of this router — nothing survives to convert, and no care in
    `safe_output_name` can reach back through a body that did not parse. What must survive is
    the EXPLANATION: the fault is in a name, not in a recording."""
    body, boundary = _multipart([("first\nsecond\rthird.mp3", b"RIFF0000WAVE"),
                                 ("good.mp3", b"RIFF0000WAVE")], fmt="alaw")
    r = convert_api.post("/convert", content=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Real-IP": anon_ip()})
    assert r.status_code == 400
    detail = r.json()["detail"].lower()
    assert "name" in detail and "rename" in detail
    assert "parsing the body" not in detail       # the answer FastAPI gives on its own


def test_the_catalog_endpoint_publishes_the_dropdown_and_the_limits(convert_api):
    r = convert_api.get("/convert/formats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["id"] for f in body["formats"]] == list(FORMATS)     # order IS the dropdown
    assert body["default"] == audio_convert.DEFAULT_FORMAT
    assert all(f["channels"] == 1 and f["ext"] and f["label"] for f in body["formats"])
    # ffmpeg arguments are ours to change; publishing them invites a caller to send them.
    assert all("args" not in f for f in body["formats"])
    assert body["limits"]["max_files"] == audio_convert.MAX_BATCH_FILES
    assert body["limits"]["max_file_bytes"] == audio_convert.MAX_FILE_BYTES
    assert body["limits"]["max_batch_bytes"] == audio_convert.MAX_BATCH_BYTES


# ---------------------------------------------------------------------------
# 7. Quota and the batch caps — clean refusals, never a 500
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_every_file_in_a_batch_costs_one_quota_unit(convert_api, anon_ip, stereo_wav):
    """Per FILE, not per request. Conversion is the one CPU-expensive thing an unregistered
    visitor can ask this box for, and a thirty-file batch is thirty transcodes."""
    ip = anon_ip()
    r = _post(convert_api, [("a.wav", stereo_wav), ("b.wav", stereo_wav),
                            ("c.txt", GARBAGE)], fmt="sln", ip=ip)
    assert r.status_code == 200, r.text

    used = sql(lambda c: c.fetchval(
        "SELECT conversions FROM anon_usage WHERE anon_key = $1 AND day = CURRENT_DATE", ip))
    # Three, including the file that failed in ffmpeg: the unit meters the CPU we agreed to
    # spend, and a transcode that ran and failed spent it just as surely as one that worked.
    assert used == 3


@needs_ffmpeg
def test_the_daily_limit_truncates_a_batch_then_refuses_the_next_one(convert_api, anon_ip,
                                                                    anon_config, stereo_wav):
    anon_config["max_conversions_per_day"] = 2
    ip = anon_ip()

    r = _post(convert_api, [("a.wav", stereo_wav), ("b.wav", stereo_wav),
                            ("c.wav", stereo_wav)], fmt="wav", ip=ip)
    # The files already paid for still convert; the rest are reported with the reason.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["converted"] == 2 and body["failed"] == 1
    assert body["files"][2]["ok"] is False and body["files"][2]["error"]
    assert body["quota_refusal"]

    # Nothing left: a refusal on the FIRST file is an HTTP status, not a frame inside a 200.
    again = _post(convert_api, [("d.wav", stereo_wav)], fmt="wav", ip=ip)
    assert again.status_code == 429
    assert "limit" in again.json()["detail"].lower()


@needs_ffmpeg
def test_anonymous_conversion_can_be_switched_off(convert_api, anon_ip, anon_config):
    anon_config["features"] = {"convert": False}
    r = _post(convert_api, [("a.wav", b"RIFF0000WAVE")], fmt="wav", ip=anon_ip())
    assert r.status_code == 403
    assert "sign in" in r.json()["detail"].lower()


@needs_ffmpeg
def test_too_many_files_is_a_413_and_costs_no_quota(convert_api, anon_ip, monkeypatch):
    """The caps are judged before any ffmpeg runs and before any unit is spent, so an
    oversized batch is a clean status rather than a partly-converted 200."""
    monkeypatch.setattr(audio_convert, "MAX_BATCH_FILES", 2)
    ip = anon_ip()
    r = _post(convert_api, [(f"{i}.wav", b"RIFF0000WAVE") for i in range(3)], fmt="wav", ip=ip)
    assert r.status_code == 413
    assert "2 files" in r.json()["detail"]
    assert sql(lambda c: c.fetchval(
        "SELECT conversions FROM anon_usage WHERE anon_key = $1 AND day = CURRENT_DATE",
        ip)) is None


@needs_ffmpeg
def test_an_oversized_file_and_an_oversized_batch_are_413s(convert_api, anon_ip, monkeypatch):
    monkeypatch.setattr(audio_convert, "MAX_FILE_BYTES", 1024)
    big = b"\x00" * 4096
    ip = anon_ip()
    r = _post(convert_api, [("huge.wav", big)], fmt="wav", ip=ip)
    assert r.status_code == 413
    assert "huge.wav" in r.json()["detail"]
    # The quota gate runs BEFORE the read that raises this — and it only reads the meter, so
    # a size refusal still costs the caller nothing.
    assert sql(lambda c: c.fetchval(
        "SELECT conversions FROM anon_usage WHERE anon_key = $1 AND day = CURRENT_DATE",
        ip)) is None

    monkeypatch.setattr(audio_convert, "MAX_FILE_BYTES", 4096)
    monkeypatch.setattr(audio_convert, "MAX_BATCH_BYTES", 5000)
    r = _post(convert_api, [("a.wav", big), ("b.wav", big)], fmt="wav", ip=anon_ip())
    assert r.status_code == 413
    assert "total limit" in r.json()["detail"]


def test_conversion_is_503_when_ffmpeg_is_missing(convert_api, anon_ip, monkeypatch):
    """Unavailable is not the same as broken: a box without ffmpeg says so, rather than
    answering 200 with every file failed."""
    monkeypatch.setattr(audio_convert, "audio_tools_available", lambda: False)
    r = _post(convert_api, [("a.wav", b"RIFF0000WAVE")], fmt="wav", ip=anon_ip())
    assert r.status_code == 503
    assert convert_api.get("/convert/formats").json()["available"] is False
